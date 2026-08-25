"""A stateful, resource-tracking stub `aws` binary for EC2 lifecycle tests.

Unlike the argv-only stub in tests/test_ec2_lib_sh.py::_stub_aws (which
records invocations but tracks no state), this stub models EC2 as a
persistent state machine: `run-instances` creates a resource that
`stop-instances` / `start-instances` / `terminate-instances` transition
through, and `create-volume` / `delete-volume` do the same for volumes.
Downstream lifecycle tests (provisioning, teardown, kill) can then
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
import subprocess
from pathlib import Path

STATE_FILENAME = "state.json"
LOG_FILENAME = "aws.log"

_EMPTY_STATE = {"instances": {}, "volumes": {}}

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LAUNCHER = REPO_ROOT / "leerie"


def run_launcher(
    args: list[str],
    env: dict,
    *,
    launcher: Path = DEFAULT_LAUNCHER,
    timeout: int = 30,
    use_bash: bool = False,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess:
    """Invoke the `leerie` launcher as a subprocess and return the result.

    Single-owner replacement for the near-identical `_run_launcher` helper
    previously redefined in tests/test_auto_detect_run_runtime.py,
    tests/test_ec2_launcher_kill.py, and tests/test_ec2_launcher_resume.py
    (all `subprocess.run([LAUNCHER] + args, env=env, capture_output=True,
    text=True, timeout=N)`, differing only in the timeout value and
    whether the launcher is invoked via an explicit `bash` prefix). Not to
    be confused with tests/test_group_state_dir_guard.py's own
    `_run_launcher`, which has a materially different signature and
    purpose (stub-binary injection) and is left untouched.

    `cwd` is load-bearing for any test that needs the launcher to act on a
    scratch repo: `leerie:39` assigns `USER_REPO="$(pwd -P)"` unconditionally,
    so setting `USER_REPO` in `env` has NO effect — the launcher overwrites it
    before any verb runs. The working directory is the only lever. This is the
    same idiom `tests/test_launcher_finalize_no_work.py` already uses.
    """
    argv = (["bash", str(launcher)] if use_bash else [str(launcher)]) + args
    return subprocess.run(
        argv,
        env=env,
        cwd=None if cwd is None else str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
    )

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


def make_sts_only_stub_aws(
    aws_dir: Path,
    *,
    identity_succeeds: bool = True,
    record_region: bool = False,
) -> Path:
    """Write a minimal, argv-only `aws` stub that only special-cases
    `sts get-caller-identity`.

    Unlike `_stub_aws` below (a stateful resource-tracking stub for
    provisioning/lifecycle tests), this one tracks no state — it exists
    for `require_aws`-focused tests that only need to observe whether
    `sts get-caller-identity` was called, with what argv, and (optionally)
    with what effective `AWS_REGION`. Every invocation's argv is appended
    to `aws_dir/aws.log`, one line per call; with `record_region=True`
    each line also carries `|region=<value-or-<unset>>` so a caller can
    recover the `AWS_REGION` env value `require_aws`'s `sts
    get-caller-identity` call inherited (it never passes `--region` on
    argv itself).

    Returns the path to the stub executable.
    """
    aws_dir = Path(aws_dir)
    aws_dir.mkdir(parents=True, exist_ok=True)
    stub = aws_dir / "aws"
    log_line = (
        f'echo "$@|region=${{AWS_REGION:-<unset>}}" >> {aws_dir}/aws.log\n'
        if record_region
        else f'echo "$@" >> {aws_dir}/aws.log\n'
    )
    stub.write_text(
        "#!/usr/bin/env bash\n"
        f"{log_line}"
        'if [ "$1" = "sts" ] && [ "$2" = "get-caller-identity" ]; then\n'
        f"  exit {0 if identity_succeeds else 1}\n"
        "fi\n"
        "exit 0\n"
    )
    stub.chmod(0o755)
    return stub


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


def seed_running_instance(
    aws_dir: Path,
    *,
    instance_id: str | None = None,
    public_ip: str = "203.0.113.20",
    status_ok: bool = True,
    ip_gen: int | None = None,
) -> str:
    """Directly seed a `running` instance into the stub's state.json.

    Bypasses a real `run-instances` round trip, for tests that only need
    a pre-existing instance to act on (stop/kill/resume dispatch, not
    provisioning itself — covered by test_ec2_provision.py). `instance_id`
    defaults to an auto-generated id (mirroring the stub's own `next_id`
    shape); pass it explicitly to pin a known id. `ip_gen` seeds the
    stub's `_ip_gen` counter field only when given, since not every
    caller's readiness/IP-reassignment assertions need it.
    """
    state = read_state(aws_dir)
    iid = instance_id or ("i-" + format(len(state["instances"]), "017x"))
    entry: dict = {"state": "running", "public_ip": public_ip, "status_ok": status_ok}
    if ip_gen is not None:
        entry["_ip_gen"] = ip_gen
    state["instances"][iid] = entry
    (aws_dir / STATE_FILENAME).write_text(json.dumps(state))
    return iid


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


def write_ec2_sidecar(
    run_dir: Path,
    run_id: str,
    instance_id: str,
    *,
    with_host_state: dict | None = None,
    paused_at: str | None = None,
    pause_reason: str | None = None,
) -> Path:
    """Write an `ec2-instance.json` + `run.json` sidecar pair into `run_dir`.

    Consolidates the four near-identical `_write_ec2_sidecar` copies
    previously redefined in test_ec2_bash32_portability.py,
    test_ec2_launcher_readonly_verbs.py, test_ec2_launcher_resume.py, and
    test_ec2_launcher_stop.py — single-owner discipline, same precedent as
    the state-machine stub above. `paused_at`/`pause_reason` are omitted
    from `run.json` unless given (most callers want a live, unpaused run);
    `with_host_state`, when given, additionally writes it as `state.json`
    (the readonly-verbs file's mirror-step fixture). Returns `run_dir`.
    """
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "ec2-instance.json").write_text(json.dumps({
        "ec2_instance_id": instance_id,
        "region": "us-east-1",
        "started_at": "2026-07-01T00:00:00+00:00",
        "run_id": run_id,
        "launcher_pid": 12345,
    }))
    run_json: dict = {
        "run_id": run_id,
        "branch": f"leerie/runs/{run_id}",
        "ec2_instance_id": instance_id,
    }
    if paused_at is not None:
        run_json["paused_at"] = paused_at
    if pause_reason is not None:
        run_json["pause_reason"] = pause_reason
    (run_dir / "run.json").write_text(json.dumps(run_json))
    if with_host_state is not None:
        (run_dir / "state.json").write_text(json.dumps(with_host_state))
    return run_dir


# --- Shared launcher-env builder -------------------------------------------
#
# tests/test_ec2_launcher_readonly_verbs.py and
# tests/test_ec2_launcher_stop.py each defined a near-identical `_env(tmp_path,
# aws_dir)` building the minimal PATH/USER_REPO/LEERIE_REPO/HOME/
# LEERIE_STATE_HOST_DIR/LEERIE_STATE_DIR/AWS_* env dict a `leerie` launcher
# subprocess invocation needs against the stubbed `aws` binary. They diverged
# only in extras: readonly_verbs.py additionally creates a `remote-work` dir
# and returns it as a 3-tuple `(env, state_dir, work_dest)`; stop.py accepts
# an optional pre-built `home` Path and returns a 2-tuple `(env, state_dir)`.
# Single-owner discipline (same precedent as the state-machine stub above):
# one parameterized version here, reconciled via optional params the way
# `write_ec2_sidecar` reconciles its callers' divergent optional params.


def build_ec2_launcher_env(
    tmp_path: Path,
    aws_dir: Path,
    *,
    home: Path | None = None,
    with_work_dest: bool = False,
) -> tuple[dict, Path] | tuple[dict, Path, Path]:
    """Build the minimal env dict for a `leerie` launcher subprocess run
    against a stubbed `aws` binary.

    Returns `(env, state_dir)` by default, or `(env, state_dir, work_dest)`
    when `with_work_dest=True` — mirroring the two callers' original
    2-tuple/3-tuple return shapes so each keeps its own unpacking. `home`,
    when given, is used as-is instead of creating a fresh `tmp_path/"home"`
    dir (test_ec2_launcher_stop.py's isolation-guard test needs a home dir
    it controls independently).
    """
    state_dir = tmp_path / ".leerie" / "myrepo"
    if home is None:
        home = tmp_path / "home"
        home.mkdir(parents=True, exist_ok=True)
    env = {
        "PATH": f"{aws_dir}:/usr/bin:/bin",
        "USER_REPO": str(tmp_path),
        "LEERIE_REPO": str(REPO_ROOT),
        "HOME": str(home),
        "LEERIE_STATE_HOST_DIR": str(state_dir),
        "LEERIE_STATE_DIR": str(state_dir),
        "AWS_ACCESS_KEY_ID": "AKIASTUBFIXTURE",
        "AWS_SECRET_ACCESS_KEY": "stubfixturesecret",
        "AWS_REGION": "us-east-1",
    }
    if not with_work_dest:
        return env, state_dir
    work_dest = tmp_path / "remote-work"
    work_dest.mkdir(parents=True, exist_ok=True)
    env["LEERIE_TEST_WORK_DEST"] = str(work_dest)
    return env, state_dir, work_dest


# --- Shared SSM-exec-decode `aws`/`ssh` stub builders ---------------------
#
# tests/test_ec2_seed_repo.py, tests/test_ec2_fetch_branch.py, and
# tests/test_ec2_seed_auth.py each defined their own near-identical
# `_make_stub_aws`/`_make_stub_ssh`: a bash stub that decodes
# ec2_remote_exec's base64-wrapped SSM command (or, for ssh, drains
# ec2_tar_pipe's payload / execs a real rsync --server), rewrites one or
# more absolute instance-side path prefixes to land inside a local test
# dest dir, and re-executes the (rewritten) command locally. The three
# copies differed only in which paths got rewritten and, for
# test_ec2_seed_auth.py, an optional chown-log hook. Single-owner
# discipline (same precedent as the state-machine stub above and
# tests/launcher_blocks.py): one parameterized pair here, callers pass
# the rewrite mapping.


def _bash_rewrite_lines(var: str, rewrites: dict[str, str]) -> str:
    """`{var}="${{{var}//pat/repl}}"` lines, one per `rewrites` entry, in
    order. Only the pattern (`old`) side is slash-escaped — required
    because `/` is the `${var//pattern/string}` delimiter; the
    replacement side is inserted verbatim (callers pass bash text like
    `"$DEST/work"`, whose slashes are not delimiters)."""
    lines = []
    for old, new in rewrites.items():
        old_esc = old.replace("/", "\\/")
        lines.append(f'{var}="${{{var}//{old_esc}/{new}}}"')
    return "\n".join(lines)


def make_ssm_stub_aws(
    stub_path: Path,
    exec_log: Path,
    dest_dir: Path,
    rewrites: dict[str, str],
    *,
    mkdir_subdir: str | None = None,
    chown_log: Path | None = None,
    swallow_runuser: bool = False,
) -> None:
    """Write a stub `aws` that decodes+executes ec2_remote_exec's
    base64-wrapped SSM command locally, rewriting absolute instance-side
    path prefixes (per `rewrites`, applied in order to the decoded
    command text) so it operates against `dest_dir` instead of the real
    instance filesystem — then re-encodes the (possibly nonzero) real
    exit code as the rc sentinel ec2_remote_exec expects, while itself
    always exiting 0 (mirroring the real SSM session-manager-plugin's
    documented always-exit-0 behavior, which is exactly what
    ec2_remote_exec's sentinel-recovery logic is designed to work
    around).

    `rewrites` maps an unescaped instance-side path prefix (e.g.
    `"/work"`) to its bash replacement expression (e.g. `"$DEST/work"` —
    `DEST` is bound to `dest_dir.resolve()` inside the stub).
    `mkdir_subdir`, when given, pre-creates `$DEST/<mkdir_subdir>` before
    the command runs, matching a directory the real AMI/image bakes in
    ahead of any seed. `chown -R leerie: ` / `chown leerie: ` calls are
    always replaced with a logging no-op (no leerie user exists in the
    test sandbox); pass `chown_log` to make that log assertable,
    otherwise it is discarded to /dev/null. `swallow_runuser`
    additionally drops a `runuser -u leerie -- ` prefix.
    """
    dest = str(dest_dir.resolve())
    chown_sink = str(chown_log.resolve()) if chown_log else "/dev/null"
    rewrite_lines = _bash_rewrite_lines("decoded", rewrites)
    mkdir_line = f'mkdir -p "$DEST/{mkdir_subdir}"' if mkdir_subdir else ""
    runuser_line = (
        'decoded="${decoded//runuser -u leerie -- /}"' if swallow_runuser else ""
    )
    stub_path.write_text(
        f"""#!/usr/bin/env bash
echo "aws $*" >> {exec_log}

DEST={dest!r}
CHOWN_LOG={chown_sink!r}
{mkdir_line}

# Find the --parameters "command=[...]" argument.
param=""
prev=""
for arg in "$@"; do
  if [ "$prev" = "--parameters" ]; then
    param="$arg"
  fi
  prev="$arg"
done

if [ -z "$param" ]; then
  exit 0
fi

# param looks like: command=["echo <b64> | base64 -d | bash"]
inner="${{param#command=[\\"}}"
inner="${{inner%\\"]}}"
# inner is: echo <b64> | base64 -d | bash
b64="${{inner#echo }}"
b64="${{b64%% | base64*}}"
decoded="$(printf '%s' "$b64" | base64 -d)"

# Rewrite absolute instance-side paths to land inside DEST.
{rewrite_lines}
{runuser_line}
# No leerie user in the test sandbox — swallow chown, logging it when
# a caller wants to assert the ownership fix actually happened.
decoded="${{decoded//chown -R leerie: /echo chown-R >> \\"\\$CHOWN_LOG\\"; true }}"
decoded="${{decoded//chown leerie: /echo chown >> \\"\\$CHOWN_LOG\\"; true }}"

CHOWN_LOG="$CHOWN_LOG" bash -c "$decoded"
# SSM's own process always exits 0 regardless of the wrapped command's
# real exit status; the sentinel inside `decoded` already carried the
# real rc back over stdout, which ec2_remote_exec parses.
exit 0
"""
    )
    stub_path.chmod(0o755)


def make_ssm_stub_ssh(
    stub_path: Path,
    exec_log: Path,
    dest_dir: Path,
    rewrites: dict[str, str],
    *,
    rsync_rewrite: tuple[str, str] | None = None,
    raw_command_rewrite: bool = False,
) -> None:
    """Write a stub `ssh` used as ec2_tar_pipe's transport (extracts a
    one-entry gzipped tar / evals a single `sh -c '...'` receiver
    argv element from stdin, rewriting absolute instance-side paths
    per `rewrites` first) and, optionally, as rsync's `-e` transport
    or a raw single-command-string transport.

    ec2_tar_pipe passes its whole `sh -c '...'` receiver as a SINGLE
    argv element (not three separate sh/-c/<script> args) — see
    ec2-lib.sh's ec2_tar_pipe body. This stub matches that shape.

    `rsync_rewrite`, when given as `(old, new)`, adds an `rsync*)` case
    arm that execs a real local `rsync --server` after rewriting the
    trailing target argument the same way `rewrites` does (used by
    ec2-seed-repo.sh's upload path, which never appears in the other
    two callers). `raw_command_rewrite`, when true, makes the fallback
    `*)` arm treat the remaining argv as a single raw command string
    (`ssh <target> "<cmd>"`, used by ec2-fetch-branch.sh's download
    path) — rewritten and eval'd, streaming this stub's own stdout
    straight back — instead of draining and discarding stdin.
    """
    dest = str(dest_dir.resolve())
    sh_rewrite_lines = _bash_rewrite_lines("remote_cmd", rewrites)

    if rsync_rewrite is not None:
        old, new = rsync_rewrite
        old_esc = old.replace("/", "\\/")
        subdir = old.lstrip("/")
        rsync_arm = f"""  rsync*)
    # rsync --server invocation. Rewrite the trailing /{subdir}/ target arg.
    # The replacement half of ${{var/pat/repl}} is NOT a regex and needs
    # no escaping: a `\\/` there is a *literal backslash*, which sent the
    # transfer to a directory named `<dest>\\` and left `<dest>/{subdir}`
    # empty — rsync exits 0, so the test failed with "untracked.txt
    # missing" and no error anywhere. Only the pattern half escapes.
    new_rest=()
    for a in "${{rest[@]}}"; do
      case "$a" in
        */{subdir}/*|*/{subdir}) a="${{a/{old_esc}/{new}}}" ;;
      esac
      new_rest+=("$a")
    done
    exec "${{new_rest[@]}}"
    ;;
"""
    else:
        rsync_arm = ""

    if raw_command_rewrite:
        raw_rewrite_lines = _bash_rewrite_lines("remote_cmd", rewrites)
        default_arm = f"""  *)
    # Raw command form: ssh <target> "<cmd>" — a single argv element
    # containing the whole remote command. Rewrite path references and
    # eval, streaming this stub's own stdout straight back (binary-safe:
    # no command substitution in this path).
    remote_cmd="${{rest[*]}}"
    {raw_rewrite_lines}
    eval "$remote_cmd"
    exit $?
    ;;
"""
    else:
        default_arm = """  *)
    cat > /dev/null
    exit 0
    ;;
"""

    stub_path.write_text(
        f"""#!/usr/bin/env bash
echo "ssh $*" >> {exec_log}
DEST={dest!r}

# Drop leading ssh flags to find the target and the remainder of argv.
# `-o <opt>` (our own ec2_tar_pipe/rsync -e invocation) and `-l <user>`
# (rsync's own -e-transport invocation always passes login name via
# `-l`, e.g. `ssh -o ... -o ... -l ec2-user 1.2.3.4 rsync --server
# ...`) both take a separate value argument — treat them the same as
# any other single-token flag, or `target`/`rest` end up off-by-one and
# the rsync* case below is never reached (the real bug this comment
# guards against: silently falling through to the drain-and-exit
# default, which leaves the sending rsync hung waiting on a protocol
# handshake that never arrives).
args=("$@")
i=0
while [ $i -lt ${{#args[@]}} ]; do
  case "${{args[$i]}}" in
    -o|-l) i=$((i+2)); continue ;;
    -*) i=$((i+1)); continue ;;
    *) break ;;
  esac
done
target="${{args[$i]}}"
rest=("${{args[@]:$((i+1))}}")

if [ "${{#rest[@]}}" -eq 0 ]; then
  # No remote command — this is ec2_tar_pipe's bare-target invocation
  # form. Shouldn't happen in production (ec2_tar_pipe always appends a
  # remote command), but guard defensively.
  cat > /dev/null
  exit 0
fi

case "${{rest[0]}}" in
  sh*)
    # ec2_tar_pipe passes its whole receiver as ONE argv element:
    # "sh -c 'mkdir -p '\\''<dir>'\\'' && tar -xzC '\\''<dir>'\\'''"
    # (not three separate sh/-c/<script> args) — rewrite paths in that
    # single string and eval it as-is.
    remote_cmd="${{rest[0]}}"
    {sh_rewrite_lines}
    eval "$remote_cmd"
    exit $?
    ;;
{rsync_arm}{default_arm}esac
"""
    )
    stub_path.chmod(0o755)
