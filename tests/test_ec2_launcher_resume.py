"""Pins `resume` routing a paused EC2 run through `resume_instance()`
in the `leerie` launcher's `RUNTIME=ec2` dispatch block.

scripts/remote/ec2-resume-instance.sh ships `resume_instance()` and is
covered standalone by tests/test_ec2_resume_instance.py, and the launcher
does call it (leerie:6270 `resume_instance "$_ec2_paused_iid"`) when a
run-id's sidecar names an instance — but that seam (sidecar lookup ->
resume_instance vs. provision_instance, and the IP-reassignment
re-resolution surfacing all the way out to LEERIE_EC2_SSH_TARGET) had no
launcher-level test. tests/test_ec2_launcher_stop.py pins the analogous
`stop` seam but through the early fast-path verb dispatch (before
container preflight); `resume` for EC2 instead lives inside the deep
`RUNTIME=ec2` elif dispatch block (leerie:6151+), so this file reuses
tests/test_ec2_e2e_provision.py's `extract_ec2_dispatch_block` /
`run_ec2_dispatch` / `stub_aws_env` harness and tests/ec2_stub.py's
resource-tracking `aws` stub, mirroring
tests/test_ec2_launcher_dispatch_e2e.py's import convention.

The load-bearing risk here — distinct from stop, which has no analogue
for it — is IP reassignment: EC2 hands out a new public IP on every
stop/start cycle absent an attached Elastic IP (tests/ec2_stub.py's
`_ip_gen` counter models this), so a launcher that cached the
provision-time address would export an SSH target reaching nothing.
"""
from __future__ import annotations

import json
from pathlib import Path

from tests.ec2_stub import (
    _stub_aws,
    read_log,
    read_state,
    run_launcher as _run_launcher_shared,
    seed_running_instance,
    write_ec2_sidecar as _write_ec2_sidecar_shared,
)
from tests.test_ec2_e2e_provision import (
    REQUIRED_PROVISION_ENV,
    extract_ec2_dispatch_block,
    run_ec2_dispatch,
    stub_aws_env,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = REPO_ROOT / "leerie"

RUN_ID = "ec2-run-resume-0001"


# ---------------------------------------------------------------------------
# Harness sanity
# ---------------------------------------------------------------------------


def test_dispatch_block_calls_resume_instance():
    """Sanity check on the harness + the wiring itself: the extracted
    RUNTIME=ec2 block must call resume_instance, not just
    provision_instance, or every test below would be exercising dead
    code."""
    block = extract_ec2_dispatch_block()
    assert "resume_instance" in block
    assert "provision_instance" in block


def _seed_stopped_instance(aws_dir: Path, *, status_ok: bool = True,
                            public_ip: str = "203.0.113.11") -> str:
    """Directly seed a `stopped` instance in the stub's state.json,
    bypassing `run-instances`/`stop-instances` — this file only exercises
    the launcher's resume dispatch, not the stop/provision paths (covered
    by test_ec2_provision.py / test_ec2_launcher_stop.py)."""
    state = read_state(aws_dir)
    iid = "i-" + format(len(state["instances"]), "017x")
    state["instances"][iid] = {
        "state": "stopped",
        "_ip_gen": 1,
        "public_ip": public_ip,
        "status_ok": status_ok,
    }
    (aws_dir / "state.json").write_text(json.dumps(state))
    return iid


# Defaults every call site below relies on (every other consumer of the
# shared helper wants paused_at/pause_reason omitted, so the shared
# helper's own defaults are None/None; this file supplies its own).
_DEFAULT_PAUSED_AT = "2026-07-15T00:00:00+00:00"
_DEFAULT_PAUSE_REASON = "user-requested"


def _sidecar(state_dir: Path, run_id: str, instance_id: str,
             *, paused_at: str = _DEFAULT_PAUSED_AT,
             pause_reason: str = _DEFAULT_PAUSE_REASON) -> Path:
    """State-dir-relative convenience shim over the shared
    `ec2_stub.write_ec2_sidecar` (which takes `run_dir` directly) — this
    file's callers pass the state dir, not the run dir, and always want
    paused_at/pause_reason populated."""
    return _write_ec2_sidecar_shared(
        state_dir / "runs" / run_id, run_id, instance_id,
        paused_at=paused_at, pause_reason=pause_reason,
    )


def _resume_env(aws_dir: Path, run_id: str) -> tuple[dict, Path]:
    """Build the env for a resume dispatch: stub_aws_env's usual
    provisioning env, plus IS_RESUME=true / LEERIE_RUN_ID set so the
    dispatch block's sidecar lookup (leerie:6251) actually fires, and
    stub_transport=True (stub_aws_env's default) so a successful resume
    falls through to the stubbed ec2_seed_auth/ec2_seed_repo rather than a
    real ssh/ssm transport to a fake instance.

    LEERIE_TEST_LAUNCH_RC=75 forces the stub ec2_launch_detached (see
    _build_stub_transport_repo) to return the smart-resume-pivot rc,
    which routes container_rc=130 (the SIGINT/detach disposition —
    decide_ec2_teardown's `130|143)` arm leaves the instance alone rather
    than terminating or stopping it). Tests in this file only care
    whether resume_instance was reached and behaved correctly, not about
    the full seed->launch->tail->teardown lifecycle (already covered by
    tests/test_ec2_launcher_dispatch_e2e.py) — this keeps every test's
    assertions about the instance's post-dispatch state meaningful
    without a full clean-exit teardown terminating it out from under the
    assertion."""
    env = stub_aws_env(aws_dir, identity_succeeds=True,
                        extra=REQUIRED_PROVISION_ENV)
    state_dir = Path(env["USER_REPO"]) / "state"
    env["LEERIE_STATE_HOST_DIR"] = str(state_dir)
    env["IS_RESUME"] = "true"
    env["LEERIE_RUN_ID"] = run_id
    env["LEERIE_TEST_LAUNCH_RC"] = "75"
    return env, state_dir


# ---------------------------------------------------------------------------
# Happy path: stopped instance -> running, new IP re-resolved
# ---------------------------------------------------------------------------


def test_resume_stopped_instance_reaches_running_with_one_start_call(tmp_path):
    aws_dir = tmp_path / "bin"
    env, state_dir = _resume_env(aws_dir, RUN_ID)
    _stub_aws(aws_dir)
    iid = _seed_stopped_instance(aws_dir)
    _sidecar(state_dir, RUN_ID, iid)

    result = run_ec2_dispatch(env)
    assert result.returncode == 130, (
        f"the rc=75 launch stub routes container_rc=130 (detach); "
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )

    state = read_state(aws_dir)
    assert state["instances"][iid]["state"] == "running", state

    log = read_log(aws_dir)
    start_calls = [l for l in log if l.startswith("ec2 start-instances")]
    assert len(start_calls) == 1, f"expected exactly one start-instances call; log={log}"
    assert iid in start_calls[0]

    # No duplicate instance provisioned via run-instances.
    run_instances_calls = [l for l in log if l.startswith("ec2 run-instances")]
    assert not run_instances_calls, (
        f"resuming a sidecar-named instance must not also provision a new "
        f"one; log={log}"
    )
    assert len(state["instances"]) == 1, state


def test_resume_reresolves_ssh_target_to_new_public_ip(tmp_path):
    """The load-bearing IP-reassignment assertion: LEERIE_EC2_SSH_TARGET
    must reflect the instance's NEW PublicIpAddress after start-instances
    (the stub's _ip_gen counter reassigns it), not the provision-time
    address recorded in the sidecar."""
    aws_dir = tmp_path / "bin"
    env, state_dir = _resume_env(aws_dir, RUN_ID)
    _stub_aws(aws_dir)
    old_ip = "203.0.113.11"
    iid = _seed_stopped_instance(aws_dir, public_ip=old_ip)
    _sidecar(state_dir, RUN_ID, iid)

    result = run_ec2_dispatch(
        env,
        extra_trailer='echo "SSH_TARGET=$LEERIE_EC2_SSH_TARGET" >&2',
    )
    assert result.returncode == 130, (
        f"the rc=75 launch stub routes container_rc=130 (detach); "
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )

    state = read_state(aws_dir)
    new_ip = state["instances"][iid]["public_ip"]
    assert new_ip != old_ip, (
        f"the stub's start-instances handler must bump the IP; state={state}"
    )

    combined = result.stdout + result.stderr
    assert f"SSH_TARGET=ec2-user@{new_ip}" in combined, (
        f"expected LEERIE_EC2_SSH_TARGET to carry the NEW public IP "
        f"({new_ip}), not the stale provision-time address ({old_ip}); "
        f"output={combined}"
    )
    assert f"ec2-user@{old_ip}" not in combined, combined


def test_resume_clears_paused_at_and_pause_reason_on_run_json(tmp_path):
    aws_dir = tmp_path / "bin"
    env, state_dir = _resume_env(aws_dir, RUN_ID)
    _stub_aws(aws_dir)
    iid = _seed_stopped_instance(aws_dir)
    run_dir = _sidecar(state_dir, RUN_ID, iid)

    result = run_ec2_dispatch(env)
    assert result.returncode == 130, (
        f"the rc=75 launch stub routes container_rc=130 (detach); "
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )

    run_json = json.loads((run_dir / "run.json").read_text())
    assert run_json.get("paused_at") is None, run_json
    assert run_json.get("pause_reason") is None, run_json
    assert run_json.get("ec2_instance_id") == iid, run_json


# ---------------------------------------------------------------------------
# Idempotent no-op on an already-running instance
# ---------------------------------------------------------------------------


def test_resume_on_already_running_instance_is_noop(tmp_path):
    aws_dir = tmp_path / "bin"
    env, state_dir = _resume_env(aws_dir, RUN_ID)
    _stub_aws(aws_dir)
    iid = seed_running_instance(aws_dir, ip_gen=1)
    _sidecar(state_dir, RUN_ID, iid)

    result = run_ec2_dispatch(env)
    assert result.returncode == 130, (
        f"the rc=75 launch stub routes container_rc=130 (detach); "
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )

    log = read_log(aws_dir)
    start_calls = [l for l in log if l.startswith("ec2 start-instances")]
    assert start_calls == [], (
        f"resuming an already-running instance must not call "
        f"start-instances; log={log}"
    )

    state = read_state(aws_dir)
    assert state["instances"][iid]["state"] == "running"
    assert len(state["instances"]) == 1, state


# ---------------------------------------------------------------------------
# Never terminates or deletes a volume — success and failure paths
# ---------------------------------------------------------------------------


def test_resume_success_path_never_terminates_or_deletes_volume(tmp_path):
    aws_dir = tmp_path / "bin"
    env, state_dir = _resume_env(aws_dir, RUN_ID)
    _stub_aws(aws_dir)
    iid = _seed_stopped_instance(aws_dir)
    _sidecar(state_dir, RUN_ID, iid)

    result = run_ec2_dispatch(env)
    assert result.returncode == 130, (
        f"the rc=75 launch stub routes container_rc=130 (detach); "
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )

    log = read_log(aws_dir)
    assert not any(l.startswith("ec2 terminate-instances") for l in log), log
    assert not any(l.startswith("ec2 delete-volume") for l in log), log


def test_resume_never_ready_failure_path_never_terminates_or_deletes_volume(tmp_path):
    """status_ok=False keeps describe-instance-status reporting
    'initializing' forever, so wait_for_instance_ready() inside
    resume_instance times out and the dispatch block must abort non-zero
    — but even on that failure path, neither terminate-instances nor
    delete-volume may ever be called (the one-way-ratchet invariant
    ec2-resume-instance.sh's own docstring commits to)."""
    aws_dir = tmp_path / "bin"
    env, state_dir = _resume_env(aws_dir, RUN_ID)
    env["LEERIE_INSTANCE_START_TIMEOUT"] = "2"
    _stub_aws(aws_dir)
    iid = _seed_stopped_instance(aws_dir, status_ok=False)
    _sidecar(state_dir, RUN_ID, iid)

    result = run_ec2_dispatch(env)

    assert result.returncode != 0, (
        f"a resume that never becomes ready must abort non-zero; "
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )

    log = read_log(aws_dir)
    assert not any(l.startswith("ec2 terminate-instances") for l in log), log
    assert not any(l.startswith("ec2 delete-volume") for l in log), log

    state = read_state(aws_dir)
    assert state["instances"][iid]["state"] != "terminated", state


# ---------------------------------------------------------------------------
# The seam: the real launcher must actually REACH the dispatch block above
# ---------------------------------------------------------------------------
#
# Everything above this line exercises `extract_ec2_dispatch_block()` — the
# dispatch code lifted out of the launcher and run standalone. That coverage
# was correct and stayed green while `leerie resume <ec2-run>` was, in fact,
# broken for every user: a guard in the resume verb's runtime auto-detect
# exited 1 roughly 3,000 lines EARLIER, so the block below it was unreachable
# from any real invocation.
#
# Measured against the unmodified launcher before the fix: rc=1, ZERO `aws`
# calls, and the message "resume: run <id> is an EC2 run (ec2-instance.json
# present); resume does not support EC2 runs yet".
#
# The guard's own stated reason was that promoting `RUNTIME=ec2` would land in
# the fresh-provision branch and die with an unrelated "not yet wired" message.
# That was true when written and went stale when the branch was completed and
# the die removed (`grep -c "not yet wired" leerie` is 0). The guard outlived
# its reason.
#
# So these tests drive the REAL `leerie` binary. They deliberately assert on
# the seam only — the promotion firing — not on reaching `start-instances`:
# unlike `stop`/`kill`/`finalize`, which exit inside the early verb-dispatch
# region, `resume` falls through to an unconditional local container-image
# build with no skip flag, which a test host without containerd cannot pass.
# Splitting it this way is not a compromise; the hole was never in the
# dispatch behaviour, it was in the seam.


def _seam_env(tmp_path: Path, aws_dir: Path) -> tuple[dict, Path]:
    state_dir = tmp_path / ".leerie" / "myrepo"
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    return {
        "PATH": f"{aws_dir}:/usr/bin:/bin",
        "USER_REPO": str(tmp_path),
        "LEERIE_REPO": str(REPO_ROOT),
        "HOME": str(home),
        "LEERIE_STATE_HOST_DIR": str(state_dir),
        "LEERIE_STATE_DIR": str(state_dir),
        "AWS_ACCESS_KEY_ID": "AKIASTUBFIXTURE",
        "AWS_SECRET_ACCESS_KEY": "stubfixturesecret",
        "AWS_REGION": "us-east-1",
    }, state_dir


def _paused_ec2_run(state_dir: Path, run_id: str, iid: str) -> None:
    run_dir = state_dir / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "ec2-instance.json").write_text(json.dumps({
        "ec2_instance_id": iid, "region": "us-east-1", "run_id": run_id}))
    (run_dir / "run.json").write_text(json.dumps({
        "run_id": run_id, "ec2_instance_id": iid,
        "paused_at": "2026-08-01T00:00:00+00:00",
        "pause_reason": "user-requested"}))


def _launcher(args: list[str], env: dict):
    return _run_launcher_shared(args, env, launcher=LAUNCHER, timeout=120, use_bash=True)


def test_real_launcher_promotes_runtime_for_a_paused_ec2_run(tmp_path: Path):
    """THE REGRESSION PIN. Reinstating the guard makes this fail; no
    extracted-block test can, which is exactly why the bug shipped."""
    aws_dir = tmp_path / "bin"
    aws_dir.mkdir()
    _stub_aws(aws_dir)
    env, state_dir = _seam_env(tmp_path, aws_dir)
    _paused_ec2_run(state_dir, RUN_ID, "i-00000000000000042")

    err = (_launcher(["resume", RUN_ID], env).stderr or "")
    assert "promoting --runtime to ec2" in err, (
        "the resume verb must promote RUNTIME=ec2 for a run whose sidecar "
        f"names an instance, so the dispatch block is reachable. stderr:\n{err}")


def test_real_launcher_no_longer_fails_closed_on_ec2_resume(tmp_path: Path):
    aws_dir = tmp_path / "bin"
    aws_dir.mkdir()
    _stub_aws(aws_dir)
    env, state_dir = _seam_env(tmp_path, aws_dir)
    _paused_ec2_run(state_dir, RUN_ID, "i-00000000000000043")

    err = (_launcher(["resume", RUN_ID], env).stderr or "")
    assert "does not support EC2 runs yet" not in err, err


def test_fly_resume_promotion_is_unchanged(tmp_path: Path):
    """ANTI-REGRESSION: the EC2 arm sits in the same if/elif as the Fly arm.
    A malformed edit there would silently break Fly resume, which has far more
    users than EC2."""
    aws_dir = tmp_path / "bin"
    aws_dir.mkdir()
    _stub_aws(aws_dir)
    env, state_dir = _seam_env(tmp_path, aws_dir)
    run_dir = state_dir / "runs" / "fly-run-0001"
    run_dir.mkdir(parents=True)
    (run_dir / "fly-machine.json").write_text(json.dumps(
        {"fly_machine_id": "5683dcd0", "run_id": "fly-run-0001"}))

    err = (_launcher(["resume", "fly-run-0001"], env).stderr or "")
    assert "promoting --runtime to fly" in err, err
    assert "promoting --runtime to ec2" not in err, err


def test_the_retired_failclosed_string_is_gone_from_the_launcher():
    """Prior art: tests/test_ec2_launcher_dispatch_e2e.py's grep guard on
    'not yet wired'. A live die() carrying this text would mean the guard came
    back; `finalize`'s own message is a separate string checked elsewhere."""
    src = LAUNCHER.read_text()
    assert "resume does not support EC2 runs yet" not in src, (
        "the resume fail-closed guard is back; it makes a fully-implemented, "
        "separately-tested dispatch path unreachable")
