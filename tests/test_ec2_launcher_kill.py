"""Tests for the `leerie` launcher's `--kill` verb routing an EC2 run to
`terminate_instance()` with fetch-before-terminate ordering (DESIGN §6
*EC2 runtime lifecycle*, one-way-ratchet invariant).

Before this subtask, an EC2 run-id passed to `--kill` fell through to the
Fly kill path (since no ec2 branch existed there yet — feat-004 wired only
detection + a fail-closed message) where it was handed to `flyctl` — the
destroy silently no-ops against a nonexistent Fly machine, `--kill`
reports "destroyed", and the EC2 instance survives as an unbounded silent
cost leak. This module pins the real fix: `--kill` resolves credentials,
re-resolves the instance's SSH target, syncs run state to the host via
`_try_fetch_state_for_ec2_teardown` (mirroring `decide_ec2_teardown`'s own
hook — same ordering rule `ec2-provision.sh:262-272` documents:
destroy-then-fetch makes paid-for LLM work unrecoverable), and only then
calls `terminate_instance()`.

Harness: invokes the real `leerie` launcher binary end to end (mirroring
`tests/test_auto_detect_run_runtime.py`'s `_run_launcher` e2e pattern).
The stub `aws` combines two behaviors behind one binary, since `--kill`'s
EC2 path exercises both surfaces in one run:

  - `sts` / `ec2 <action>` subcommands route to the resource-tracking
    state machine (`tests/ec2_stub.py`), so assertions can read tracked
    instance/volume state and leaks, not just argv.
  - `ssm start-session` (the transport `ec2_remote_exec` — and therefore
    `fetch_state_ec2`'s run-discovery step — uses) routes to
    `tests/test_ec2_fetch_branch.py`'s decode-and-exec-locally stub
    (imported directly rather than reimplemented), against a real git
    repo standing in for the instance's `/work`, so `fetch_state_ec2`
    runs for real rather than being hand-waved.

A hard-failing `flyctl` stub (records invocation, exits nonzero) is also
on PATH so any accidental hand-off to the Fly path is caught immediately
rather than silently "succeeding".
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from tests.ec2_stub import leaked_resources, read_log, read_state
from tests.test_ec2_fetch_branch import (
    _init_instance_repo_with_run,
    _make_git_repo,
    _make_stub_ssh,
    _make_stub_timeout,
    _setup_instance,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = REPO_ROOT / "leerie"

INSTANCE_ID = "i-0123456789abcdef0"

# Combined `aws` stub: ssm start-session decodes+execs locally against the
# instance fixture (mirroring test_ec2_fetch_branch.py's _make_stub_aws);
# everything else (sts, ec2 <action>) delegates to a Python resource-
# tracking backend (mirroring tests/ec2_stub.py's state machine) so
# --kill's credential/instance-lifecycle calls are tracked too. Both
# halves append to the same aws.log / state.json so
# tests/ec2_stub.py's read_log/read_state/leaked_resources work
# unmodified.
_COMBINED_AWS_STUB = r'''#!/usr/bin/env bash
echo "$@" >> "{aws_dir}/aws.log"

if [ "$1" = "ssm" ] && [ "$2" = "start-session" ]; then
  DEST={dest!r}
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
  inner="${{param#command=[\"}}"
  inner="${{inner%\"]}}"
  b64="${{inner#echo }}"
  b64="${{b64%% | base64*}}"
  decoded="$(printf '%s' "$b64" | base64 -d)"
  decoded="${{decoded//\/work/$DEST/work}}"
  bash -c "$decoded"
  exit 0
fi

exec python3 "{aws_dir}/_ec2_lifecycle_stub.py" "$@"
'''


def _write_combined_aws_stub(aws_dir: Path, instance_work: Path) -> None:
    aws_dir.mkdir(parents=True, exist_ok=True)
    dest = str(instance_work.parent.resolve())
    stub = aws_dir / "aws"
    stub.write_text(_COMBINED_AWS_STUB.format(aws_dir=str(aws_dir), dest=dest))
    stub.chmod(0o755)
    # Reuse tests/ec2_stub.py's Python stub source as the lifecycle
    # backend, installed as a separate script the bash wrapper execs into
    # for anything that isn't `ssm start-session`.
    from tests.ec2_stub import _STUB_SOURCE
    (aws_dir / "_ec2_lifecycle_stub.py").write_text(_STUB_SOURCE)
    (aws_dir / "aws.log").write_text("")
    (aws_dir / "state.json").write_text(json.dumps({"instances": {}, "volumes": {}}))


def _write_failing_flyctl(bin_dir: Path) -> None:
    """A `flyctl` stub that records its invocation to flyctl.log and
    always exits 1 — so a test asserting "flyctl was never called" fails
    loudly (empty log) rather than passing vacuously if some code path
    silently swallows a real flyctl failure."""
    flyctl = bin_dir / "flyctl"
    flyctl.write_text(
        "#!/usr/bin/env bash\n"
        f'echo "$@" >> "{bin_dir}/flyctl.log"\n'
        "exit 1\n"
    )
    flyctl.chmod(0o755)


def _read_flyctl_log(bin_dir: Path) -> list[str]:
    log = bin_dir / "flyctl.log"
    if not log.exists():
        return []
    return [l for l in log.read_text().splitlines() if l]


def _seed_running_instance(aws_dir: Path, *, public_ip: str = "203.0.113.11") -> str:
    """Seed the stub's state with one already-`running` instance (as if
    a prior `provision_instance()` call had created it) without paying
    for a real run-instances round trip."""
    state = json.loads((aws_dir / "state.json").read_text())
    state["instances"][INSTANCE_ID] = {
        "state": "running",
        "_ip_gen": 1,
        "public_ip": public_ip,
        "status_ok": True,
    }
    (aws_dir / "state.json").write_text(json.dumps(state))
    return INSTANCE_ID


def _make_run_dir(state_dir: Path, run_id: str, *, instance_id: str = INSTANCE_ID,
                   on_run_json: bool = True) -> Path:
    run_dir = state_dir / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "ec2-instance.json").write_text(json.dumps({
        "ec2_instance_id": instance_id,
        "region": "us-east-1",
        "run_id": run_id,
    }))
    if on_run_json:
        (run_dir / "run.json").write_text(json.dumps({
            "ec2_instance_id": instance_id,
            "ec2_ami": "ami-0123456789abcdef0",
        }))
    return run_dir


def _setup_fixture(tmp_path: Path, *, with_completed_run: bool = True):
    """Builds the full fixture: a host git repo, an "instance" git repo
    (standing in for /work on the EC2 instance) with a completed leerie
    run committed on it (so fetch_state_ec2's run-discovery step has
    something real to find), the combined aws/ssh/timeout/flyctl stubs,
    and the leerie run-dir sidecars. Returns (env, aws_dir, state_dir,
    run_dir, instance_id)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    repo = _make_git_repo(tmp_path, subdir="hostrepo")
    instance_root, instance_work = _setup_instance(tmp_path)

    fetch_run_id = "feat-ec2-kill-test-001"
    run_branch = f"leerie/runs/{fetch_run_id}"
    if with_completed_run:
        _init_instance_repo_with_run(instance_work, fetch_run_id, run_branch)

    _write_combined_aws_stub(bin_dir, instance_work)
    _make_stub_ssh(bin_dir / "ssh", tmp_path / "exec_log.txt", instance_work)
    _make_stub_timeout(bin_dir)
    _write_failing_flyctl(bin_dir)

    state_dir = tmp_path / "state"
    run_dir = _make_run_dir(state_dir, "r1")
    instance_id = _seed_running_instance(bin_dir)

    home = tmp_path / "home"
    home.mkdir()

    env = {k: v for k, v in os.environ.items()
           if not k.startswith("AWS_") and k not in ("LEERIE_AWS_PROFILE", "LEERIE_AWS_REGION")}
    env["LEERIE_STATE_DIR"] = str(state_dir)
    env.pop("LEERIE_FLY_APP", None)
    env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"
    env["USER_REPO"] = str(repo)
    env["HOME"] = str(home)
    env["AWS_ACCESS_KEY_ID"] = "AKIASTUBFIXTURE"
    env["AWS_SECRET_ACCESS_KEY"] = "stubfixturesecret"
    env["AWS_REGION"] = "us-east-1"

    return env, bin_dir, state_dir, run_dir, instance_id


def _run_launcher(args: list[str], env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(LAUNCHER)] + args,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


# ---------------------------------------------------------------------------
# Fetch-before-terminate ordering (the load-bearing contract)
# ---------------------------------------------------------------------------


def test_fetch_precedes_terminate_by_call_index(tmp_path):
    env, aws_dir, state_dir, run_dir, iid = _setup_fixture(tmp_path)

    result = _run_launcher(["--kill", "r1", "--force"], env)

    assert result.returncode == 0, f"stdout={result.stdout} stderr={result.stderr}"

    log = read_log(aws_dir)
    ssm_calls = [i for i, l in enumerate(log) if l.startswith("ssm start-session")]
    terminate_calls = [i for i, l in enumerate(log) if l.startswith("ec2 terminate-instances")]
    assert ssm_calls, f"expected the fetch step's ssm start-session call; log={log}"
    assert terminate_calls, f"expected a terminate-instances call; log={log}"
    assert max(ssm_calls) < min(terminate_calls), (
        f"fetch-before-terminate ordering violated: ssm_calls={ssm_calls} "
        f"terminate_calls={terminate_calls}; log={log}"
    )

    state = read_state(aws_dir)
    assert state["instances"][iid]["state"] == "terminated"


def test_successful_kill_leaves_zero_non_terminated_instances_and_no_leaked_volumes(tmp_path):
    env, aws_dir, state_dir, run_dir, iid = _setup_fixture(tmp_path)

    result = _run_launcher(["--kill", "r1", "--force"], env)
    assert result.returncode == 0, f"stdout={result.stdout} stderr={result.stderr}"

    state = read_state(aws_dir)
    leaked = leaked_resources(state)
    assert leaked["instances"] == {}, leaked
    assert leaked["volumes"] == {}, leaked


def test_fetch_failure_leaves_instance_running_not_terminated(tmp_path):
    """The one-way-ratchet invariant: a failed sync must never escalate
    to termination. No completed run is seeded on the instance, so
    fetch_state_ec2's run-discovery step fails closed."""
    env, aws_dir, state_dir, run_dir, iid = _setup_fixture(
        tmp_path, with_completed_run=False
    )

    result = _run_launcher(["--kill", "r1", "--force"], env)

    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "LEFT RUNNING" in combined or "still running" in combined.lower()

    state = read_state(aws_dir)
    assert state["instances"][iid]["state"] == "running", (
        "a failed state sync must not escalate to termination"
    )
    log = read_log(aws_dir)
    assert not any(l.startswith("ec2 terminate-instances") for l in log)


# ---------------------------------------------------------------------------
# flyctl must never be invoked for an EC2 run
# ---------------------------------------------------------------------------


def test_kill_never_hands_the_run_id_to_flyctl(tmp_path):
    """The highest-consequence regression this subtask fixes: before the
    ec2 kill branch existed, an EC2 run-id fell through to the Fly path
    and was hand-delivered to flyctl, which silently no-ops against a
    nonexistent Fly machine. Pin that flyctl is never invoked for an EC2
    run, on both the success and failure paths."""
    env, aws_dir, state_dir, run_dir, iid = _setup_fixture(tmp_path)

    result = _run_launcher(["--kill", "r1", "--force"], env)
    assert result.returncode == 0, f"stdout={result.stdout} stderr={result.stderr}"
    assert _read_flyctl_log(aws_dir) == [], "flyctl must never be invoked for an EC2 run"


def test_kill_never_hands_the_run_id_to_flyctl_on_fetch_failure(tmp_path):
    env, aws_dir, state_dir, run_dir, iid = _setup_fixture(
        tmp_path, with_completed_run=False
    )

    result = _run_launcher(["--kill", "r1", "--force"], env)
    assert result.returncode != 0
    assert _read_flyctl_log(aws_dir) == [], "flyctl must never be invoked for an EC2 run"


# ---------------------------------------------------------------------------
# Run-sidecar bookkeeping
# ---------------------------------------------------------------------------


def test_successful_kill_marks_run_json_killed(tmp_path):
    env, aws_dir, state_dir, run_dir, iid = _setup_fixture(tmp_path)

    result = _run_launcher(["--kill", "r1", "--force"], env)
    assert result.returncode == 0, f"stdout={result.stdout} stderr={result.stderr}"

    run_json = json.loads((run_dir / "run.json").read_text())
    assert run_json.get("killed_at")
    assert run_json.get("ec2_instance_id") == iid


def test_kill_bootstraps_run_json_from_ec2_instance_sidecar_when_absent(tmp_path):
    """Mirrors the local/Fly kill paths' _ensure_run_json fallback: a run
    dir with only ec2-instance.json (no run.json yet) must still get a
    killed_at write rather than silently no-op."""
    env, aws_dir, state_dir, run_dir, iid = _setup_fixture(tmp_path)
    (run_dir / "run.json").unlink()

    result = _run_launcher(["--kill", "r1", "--force"], env)
    assert result.returncode == 0, f"stdout={result.stdout} stderr={result.stderr}"

    run_json = json.loads((run_dir / "run.json").read_text())
    assert run_json.get("killed_at")
    assert run_json.get("ec2_instance_id") == iid


# ---------------------------------------------------------------------------
# No ec2_instance_id resolvable
# ---------------------------------------------------------------------------


def test_kill_fails_closed_when_no_instance_id_in_sidecar(tmp_path):
    env, aws_dir, state_dir, run_dir, iid = _setup_fixture(tmp_path)
    (run_dir / "ec2-instance.json").unlink()
    (run_dir / "run.json").write_text(json.dumps({}))

    result = _run_launcher(["--kill", "r1", "--runtime", "ec2", "--force"], env)

    assert result.returncode != 0
    assert "no ec2_instance_id found" in result.stderr
    log = read_log(aws_dir)
    assert not any(l.startswith("ec2 terminate-instances") for l in log)
    assert _read_flyctl_log(aws_dir) == []


# ---------------------------------------------------------------------------
# Confirmation prompt
# ---------------------------------------------------------------------------


def test_kill_without_force_prompts_for_confirmation(tmp_path):
    env, aws_dir, state_dir, run_dir, iid = _setup_fixture(tmp_path)

    result = subprocess.run(
        [str(LAUNCHER), "--kill", "r1"],
        env=env,
        input="wrong-confirmation\n",
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode != 0
    assert "confirmation mismatch" in result.stderr.lower()
    log = read_log(aws_dir)
    assert not any(l.startswith("ec2 terminate-instances") for l in log)


def test_kill_with_correct_confirmation_proceeds(tmp_path):
    env, aws_dir, state_dir, run_dir, iid = _setup_fixture(tmp_path)

    result = subprocess.run(
        [str(LAUNCHER), "--kill", "r1"],
        env=env,
        input="r1\n",
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, f"stdout={result.stdout} stderr={result.stderr}"
    state = read_state(aws_dir)
    assert state["instances"][iid]["state"] == "terminated"
