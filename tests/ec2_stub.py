"""A stateful, resource-tracking stub `aws` binary for EC2 lifecycle tests.

Unlike the argv-only stub in tests/test_ec2_lib_sh.py::_stub_aws (which
records invocations but tracks no state), this stub models EC2 as a
persistent state machine: `run-instances` creates a resource that
`stop-instances` / `start-instances` / `terminate-instances` transition
through, and `create-volume` / `delete-volume` do the same for volumes.
Downstream lifecycle tests (provisioning, teardown, --kill) can then
assert on resource *leaks* — "is anything still running after teardown?"
— rather than merely inspecting which commands were invoked.

State file format (JSON, written to `<dir>/state.json`):

    {
      "instances": {
        "<instance-id>": {
          "state": "pending|running|stopped|terminated",
          "public_ip": "203.0.113.11",   # present once the instance has run;
                                          # reassigned on every start-instances
                                          # call, mirroring EC2's real behavior
                                          # of handing out a new public IP on
                                          # each stop/start cycle
          "status_ok": true              # optional; describe-instance-status
                                          # reports "initializing" instead of
                                          # "ok" when explicitly set to false
        }
      },
      "volumes": {
        "<volume-id>": {"state": "creating|available|in-use|deleting|deleted"}
      }
    }

Every invocation additionally appends one line to `<dir>/aws.log`
(space-joined argv, mirroring test_ec2_lib_sh.py's aws.log convention)
so callers can assert on the exact command shape when state assertions
aren't enough.

The stub is a self-contained Python script (shebang `#!/usr/bin/env
python3`) rather than bash, since bash has no practical JSON support and
three sibling test files are expected to read this state back. It never
makes a network call — every subcommand it doesn't recognize exits 0
with an empty JSON object, so it can never reach a real AWS endpoint.

Usage:

    from tests.ec2_stub import _stub_aws, read_state, leaked_resources

    def test_something(tmp_path):
        aws_dir = tmp_path / "bin"
        aws_dir.mkdir()
        _stub_aws(aws_dir)
        env = {"PATH": f"{aws_dir}:{os.environ['PATH']}"}
        subprocess.run(["aws", "ec2", "run-instances", ...], env=env, check=True)
        state = read_state(aws_dir)
        assert leaked_resources(state) == {...}
"""
from __future__ import annotations

import json
from pathlib import Path

STATE_FILENAME = "state.json"
LOG_FILENAME = "aws.log"

_EMPTY_STATE = {"instances": {}, "volumes": {}}

# The stub script itself. Kept as a single string (rather than importing
# a shared module at runtime) so the stub has zero dependency on this
# repo's PYTHONPATH once it's written to disk — it must run standalone
# under whatever `python3` is first on the subprocess's PATH.
_STUB_SOURCE = r'''#!/usr/bin/env python3
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
STATE_PATH = HERE / "state.json"
LOG_PATH = HERE / "aws.log"

EMPTY_STATE = {"instances": {}, "volumes": {}}


def load_state():
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text())
    return {"instances": {}, "volumes": {}}


def save_state(state):
    STATE_PATH.write_text(json.dumps(state, indent=2))


def get_flag(argv, name, default=None):
    if name in argv:
        i = argv.index(name)
        if i + 1 < len(argv):
            return argv[i + 1]
    return default


def get_flag_all(argv, name):
    # Real `aws` CLI syntax is `--instance-ids i-1 i-2 i-3` — one flag
    # followed by every space-separated value up to the next `--flag` or
    # end of argv (argparse nargs="+" semantics), not one value per flag.
    if name not in argv:
        return []
    start = argv.index(name) + 1
    values = []
    for a in argv[start:]:
        if a.startswith("--"):
            break
        values.append(a)
    return values


def next_id(existing, prefix):
    n = 0
    while f"{prefix}{n:017x}" in existing:
        n += 1
    return f"{prefix}{n:017x}"


def instance_doc(instance_id, rec):
    doc = {
        "InstanceId": instance_id,
        "State": {"Name": rec["state"]},
    }
    if rec["state"] == "running":
        doc["PublicIpAddress"] = rec.get("public_ip", "203.0.113.10")
    return doc


def volume_doc(volume_id, rec):
    return {
        "VolumeId": volume_id,
        "State": rec["state"],
        "Size": rec.get("size", 8),
    }


def main(argv):
    with open(LOG_PATH, "a") as f:
        f.write(" ".join(argv) + "\n")

    if not argv:
        print("{}")
        return 0

    service = argv[0]

    if service == "sts" and len(argv) > 1 and argv[1] == "get-caller-identity":
        print(json.dumps({
            "UserId": "AIDASTUBUSERSTUB",
            "Account": "123456789012",
            "Arn": "arn:aws:iam::123456789012:user/leerie-stub",
        }))
        return 0

    if service != "ec2":
        print("{}")
        return 0

    action = argv[1] if len(argv) > 1 else ""
    state = load_state()

    if action == "run-instances":
        count = int(get_flag(argv, "--count", "1"))
        created = []
        for _ in range(count):
            iid = next_id(state["instances"], "i-")
            state["instances"][iid] = {"state": "running", "_ip_gen": 1, "public_ip": "203.0.113.11"}
            created.append(iid)
        save_state(state)
        print(json.dumps({
            "Instances": [instance_doc(iid, state["instances"][iid]) for iid in created],
        }))
        return 0

    if action == "describe-instances":
        ids = get_flag_all(argv, "--instance-ids") or list(state["instances"].keys())
        reservations = []
        for iid in ids:
            rec = state["instances"].get(iid)
            if rec is None:
                continue
            reservations.append({"Instances": [instance_doc(iid, rec)]})
        print(json.dumps({"Reservations": reservations}))
        return 0

    if action == "describe-instance-status":
        ids = get_flag_all(argv, "--instance-ids") or list(state["instances"].keys())
        statuses = []
        for iid in ids:
            rec = state["instances"].get(iid)
            if rec is None or rec["state"] != "running":
                continue
            ok = "ok" if rec.get("status_ok", True) else "initializing"
            statuses.append({
                "InstanceId": iid,
                "InstanceStatus": {"Status": ok},
                "SystemStatus": {"Status": ok},
            })
        print(json.dumps({"InstanceStatuses": statuses}))
        return 0

    if action == "stop-instances":
        ids = get_flag_all(argv, "--instance-ids")
        changed = []
        for iid in ids:
            rec = state["instances"].get(iid)
            if rec is None:
                continue
            rec["state"] = "stopped"
            changed.append(iid)
        save_state(state)
        print(json.dumps({
            "StoppingInstances": [
                {"InstanceId": iid, "CurrentState": {"Name": "stopped"}}
                for iid in changed
            ],
        }))
        return 0

    if action == "start-instances":
        ids = get_flag_all(argv, "--instance-ids")
        changed = []
        for iid in ids:
            rec = state["instances"].get(iid)
            if rec is None:
                continue
            rec["state"] = "running"
            # EC2 assigns a new public IP on every stop/start cycle
            # (unless an EIP is attached) — bump a counter so tests can
            # assert that the resume path re-resolves it rather than
            # reusing a stale cached address.
            rec["_ip_gen"] = rec.get("_ip_gen", 0) + 1
            rec["public_ip"] = f"203.0.113.{10 + rec['_ip_gen']}"
            changed.append(iid)
        save_state(state)
        print(json.dumps({
            "StartingInstances": [
                {"InstanceId": iid, "CurrentState": {"Name": "running"}}
                for iid in changed
            ],
        }))
        return 0

    if action == "terminate-instances":
        ids = get_flag_all(argv, "--instance-ids")
        changed = []
        for iid in ids:
            rec = state["instances"].get(iid)
            if rec is None:
                continue
            rec["state"] = "terminated"
            changed.append(iid)
        save_state(state)
        print(json.dumps({
            "TerminatingInstances": [
                {"InstanceId": iid, "CurrentState": {"Name": "terminated"}}
                for iid in changed
            ],
        }))
        return 0

    if action == "create-volume":
        size = int(get_flag(argv, "--size", "8"))
        vid = next_id(state["volumes"], "vol-")
        state["volumes"][vid] = {"state": "available", "size": size}
        save_state(state)
        print(json.dumps(volume_doc(vid, state["volumes"][vid])))
        return 0

    if action == "describe-volumes":
        ids = get_flag_all(argv, "--volume-ids") or list(state["volumes"].keys())
        volumes = [volume_doc(vid, state["volumes"][vid]) for vid in ids if vid in state["volumes"]]
        print(json.dumps({"Volumes": volumes}))
        return 0

    if action == "delete-volume":
        vid = get_flag(argv, "--volume-id")
        rec = state["volumes"].get(vid) if vid else None
        if rec is not None:
            rec["state"] = "deleted"
            save_state(state)
        print("{}")
        return 0

    print("{}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
'''


def _stub_aws(dir: Path) -> Path:
    """Write the stateful `aws` stub binary into `dir`.

    `dir` becomes both the stub's install location (put it on PATH) and
    its state directory (`dir/state.json`, `dir/aws.log`). Returns the
    path to the stub executable.
    """
    dir = Path(dir)
    dir.mkdir(parents=True, exist_ok=True)
    stub = dir / "aws"
    stub.write_text(_STUB_SOURCE)
    stub.chmod(0o755)
    (dir / LOG_FILENAME).write_text("")
    (dir / STATE_FILENAME).write_text(json.dumps(_EMPTY_STATE))
    return stub


def read_state(dir: Path) -> dict:
    """Read the current resource state written by the stub in `dir`."""
    state_path = Path(dir) / STATE_FILENAME
    if not state_path.exists():
        return {"instances": {}, "volumes": {}}
    return json.loads(state_path.read_text())


def read_log(dir: Path) -> list[str]:
    """Read every argv line the stub in `dir` has recorded, in order."""
    log_path = Path(dir) / LOG_FILENAME
    if not log_path.exists():
        return []
    return [line for line in log_path.read_text().splitlines() if line]


def leaked_resources(state: dict) -> dict:
    """Return the subset of `state` that is not fully torn down.

    An instance leaks unless it is `terminated`; a volume leaks unless
    it is `deleted`. Returns the same two-key shape as `state`, but with
    only leaking entries — an empty result on both keys means clean
    teardown.
    """
    return {
        "instances": {
            iid: rec for iid, rec in state.get("instances", {}).items()
            if rec.get("state") != "terminated"
        },
        "volumes": {
            vid: rec for vid, rec in state.get("volumes", {}).items()
            if rec.get("state") != "deleted"
        },
    }
