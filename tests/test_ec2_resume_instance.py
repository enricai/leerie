"""Tests for scripts/remote/ec2-resume-instance.sh — the EC2 stop/resume
lifecycle counterpart to scripts/remote/resume-machine.sh (DESIGN §6 *EC2
runtime lifecycle*).

Fly's pause/resume path ships resume-machine.sh; before this file, EC2 had
no counterpart, so a paused EC2 run (stopped via ec2-provision.sh's
stop_instance()) had no way back to `running`. ec2-resume-instance.sh
sources ec2-lib.sh + ec2-provision.sh and adds resume_instance():
`aws ec2 start-instances` -> wait_for_instance_ready() -> re-resolve the
(possibly changed) public IP into LEERIE_EC2_SSH_TARGET -> clear the
run.json sidecar's pause fields.

Uses the stateful `aws` stub (tests/ec2_stub.py) so assertions can check
resource state and leaks, not just which commands ran — mirroring
tests/test_ec2_provision.py's harness pattern.

Footgun reminder (CLAUDE.md): the state-dir override consumed by the
sidecar writes is LEERIE_STATE_HOST_DIR, NOT LEERIE_STATE_DIR — the
latter silently resolves to the real ~/.leerie/... and assertions
against it would pass vacuously.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from tests.ec2_stub import _stub_aws, leaked_resources, read_log, read_state

REPO_ROOT = Path(__file__).resolve().parent.parent
EC2_PROVISION_SH = REPO_ROOT / "scripts" / "remote" / "ec2-provision.sh"
EC2_RESUME_SH = REPO_ROOT / "scripts" / "remote" / "ec2-resume-instance.sh"

REQUIRED_ENV = {
    "LEERIE_EC2_AMI": "ami-0123456789abcdef0",
    "LEERIE_EC2_INSTANCE_TYPE": "m5.xlarge",
    "LEERIE_EC2_KEY_NAME": "leerie-key",
    "LEERIE_EC2_SECURITY_GROUP": "sg-0123456789abcdef0",
    "LEERIE_EC2_SUBNET_ID": "subnet-0123456789abcdef0",
}


def _run_bash(script: str, env: dict | None = None) -> subprocess.CompletedProcess:
    base_env = {k: v for k, v in os.environ.items()}
    base_env.pop("LEERIE_STATE_DIR", None)
    if env:
        base_env.update(env)
    return subprocess.run(
        ["bash", "-c", script],
        env=base_env,
        capture_output=True,
        text=True,
    )


def _stub_env(aws_dir: Path, extra: dict | None = None) -> dict:
    env = {
        **REQUIRED_ENV,
        "PATH": f"{aws_dir}:/usr/bin:/bin",
        "USER_REPO": str(aws_dir),
    }
    if extra:
        env.update(extra)
    return env


def _seed_stopped_instance(aws_dir: Path, *, status_ok: bool = True) -> str:
    """Provision an instance via the stub, then mark it `stopped` directly
    in state.json (bypassing decide_ec2_teardown/stop_instance, since we
    only need a stopped instance to exist — not to exercise the pause
    classification table, which test_ec2_decide_teardown.py already
    covers)."""
    state = read_state(aws_dir)
    iid = "i-" + format(len(state["instances"]), "017x")
    state["instances"][iid] = {
        "state": "stopped",
        "_ip_gen": 1,
        "public_ip": "203.0.113.11",
        "status_ok": status_ok,
    }
    (aws_dir / "state.json").write_text(json.dumps(state))
    return iid


def test_ec2_resume_instance_sh_exists_and_is_executable():
    assert EC2_RESUME_SH.is_file()
    assert os.access(EC2_RESUME_SH, os.X_OK)


# --- resume_instance: basic wake path -----------------------------------


def test_resume_instance_starts_a_stopped_instance(tmp_path):
    aws_dir = tmp_path / "bin"
    _stub_aws(aws_dir)
    iid = _seed_stopped_instance(aws_dir)
    env = _stub_env(aws_dir)

    result = _run_bash(
        f"source {EC2_PROVISION_SH}; source {EC2_RESUME_SH}; resume_instance {iid}",
        env=env,
    )

    assert result.returncode == 0, f"stderr: {result.stderr}"
    state = read_state(aws_dir)
    assert state["instances"][iid]["state"] == "running"

    log = read_log(aws_dir)
    start_calls = [l for l in log if l.startswith("ec2 start-instances")]
    assert len(start_calls) == 1
    assert iid in start_calls[0]


def test_resume_instance_readiness_poll_does_not_return_early(tmp_path):
    """Seed status_ok:False so describe-instance-status reports
    'initializing' rather than 'ok' — resume_instance's
    wait_for_instance_ready() must not return success while the instance
    is still initializing. status_ok never flips true in this test, so
    resume_instance must time out (non-zero) rather than fabricate
    success while InstanceStatus/SystemStatus are "initializing"; a
    short LEERIE_INSTANCE_START_TIMEOUT keeps the test fast."""
    aws_dir = tmp_path / "bin"
    _stub_aws(aws_dir)
    iid = _seed_stopped_instance(aws_dir, status_ok=False)
    env = _stub_env(aws_dir, {"LEERIE_INSTANCE_START_TIMEOUT": "3"})

    result = _run_bash(
        f"source {EC2_PROVISION_SH}; source {EC2_RESUME_SH}; resume_instance {iid}",
        env=env,
    )

    assert result.returncode != 0
    assert "timed out" in result.stderr.lower() or "timeout" in result.stderr.lower()


def test_resume_instance_readiness_poll_succeeds_once_status_ok(tmp_path):
    """The positive counterpart: once status_ok is (already) true, the
    poll returns success promptly."""
    aws_dir = tmp_path / "bin"
    _stub_aws(aws_dir)
    iid = _seed_stopped_instance(aws_dir, status_ok=True)
    env = _stub_env(aws_dir)

    result = _run_bash(
        f"source {EC2_PROVISION_SH}; source {EC2_RESUME_SH}; resume_instance {iid}",
        env=env,
    )

    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert "instance" in result.stderr.lower()
    assert "ready" in result.stderr.lower() or "resumed" in result.stderr.lower()


# --- resume_instance: ssh target re-resolution --------------------------


def test_resume_instance_reresolves_ssh_target(tmp_path):
    """EC2 assigns a new public IP on every start — resume_instance must
    export a fresh LEERIE_EC2_SSH_TARGET reflecting the post-restart
    address, not any address cached from provision time."""
    aws_dir = tmp_path / "bin"
    _stub_aws(aws_dir)
    iid = _seed_stopped_instance(aws_dir)
    env = _stub_env(aws_dir)

    result = _run_bash(
        f"source {EC2_PROVISION_SH}; source {EC2_RESUME_SH}; "
        f"resume_instance {iid} && echo \"target=$LEERIE_EC2_SSH_TARGET\"",
        env=env,
    )

    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert "target=ec2-user@203.0.113." in result.stdout

    state = read_state(aws_dir)
    new_ip = state["instances"][iid]["public_ip"]
    assert f"target=ec2-user@{new_ip}" in result.stdout


# --- resume_instance: full provision -> stop -> resume round trip -------


def test_provision_stop_resume_round_trip_leaves_one_running_instance(tmp_path):
    aws_dir = tmp_path / "bin"
    _stub_aws(aws_dir)
    env = _stub_env(aws_dir)

    result = _run_bash(
        f"( source {EC2_PROVISION_SH}; "
        "_try_fetch_state_for_ec2_teardown() { return 0; }; "
        "provision_instance; "
        "iid=\"$LEERIE_EC2_INSTANCE_ID\"; "
        "stop_instance; "
        # Disarm the teardown trap provision_instance registered — this
        # test simulates an explicit pause (stop_instance called
        # directly), not the rc-classified decide_ec2_teardown path
        # already covered by tests/test_ec2_decide_teardown.py, so the
        # subshell exiting here must not re-fire teardown and terminate
        # the instance we just stopped.
        "trap - EXIT INT TERM; "
        "echo \"iid=$iid\" )",
        env=env,
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    iid = result.stdout.strip().split("iid=")[-1]

    state = read_state(aws_dir)
    assert state["instances"][iid]["state"] == "stopped"

    result2 = _run_bash(
        f"source {EC2_PROVISION_SH}; source {EC2_RESUME_SH}; resume_instance {iid}",
        env=env,
    )
    assert result2.returncode == 0, f"stderr: {result2.stderr}"

    state = read_state(aws_dir)
    assert len(state["instances"]) == 1
    (only_iid, rec), = state["instances"].items()
    assert only_iid == iid
    assert rec["state"] == "running"
    # leaked_resources() treats any non-terminated instance as "leaked",
    # which is the right question for teardown paths but not for resume
    # (whose whole point is a running instance) — so here only the
    # volume side of leaked_resources applies: no volume was ever
    # created on this round trip, let alone orphaned.
    assert leaked_resources(state)["volumes"] == {}


def test_provision_stop_resume_terminate_round_trip_leaves_no_leaks(tmp_path):
    """The full cycle: provision -> stop -> resume -> terminate. Resume
    re-arms decide_ec2_teardown's EXIT trap (mirroring resume_machine.sh
    for the Fly path), so a subsequent clean exit must still tear the
    instance down cleanly — leaked_resources(state) empty on both
    instances and volumes, not just the volumes side the round-trip-
    without-terminate test above covers."""
    aws_dir = tmp_path / "bin"
    _stub_aws(aws_dir)
    env = _stub_env(aws_dir)

    result = _run_bash(
        f"( source {EC2_PROVISION_SH}; "
        "_try_fetch_state_for_ec2_teardown() { return 0; }; "
        "provision_instance; "
        "iid=\"$LEERIE_EC2_INSTANCE_ID\"; "
        "stop_instance; "
        "trap - EXIT INT TERM; "
        "echo \"iid=$iid\" )",
        env=env,
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    iid = result.stdout.strip().split("iid=")[-1]

    state = read_state(aws_dir)
    assert state["instances"][iid]["state"] == "stopped"

    # resume_instance re-arms decide_ec2_teardown on EXIT/INT/TERM; a
    # clean exit (rc=0, the subshell's own default) fires it here.
    # ec2-resume-instance.sh re-sources ec2-provision.sh internally, which
    # would clobber a fetch stub defined before that source line — define
    # it after both sources instead.
    result2 = _run_bash(
        f"( source {EC2_PROVISION_SH}; "
        f"source {EC2_RESUME_SH}; "
        "_try_fetch_state_for_ec2_teardown() { return 0; }; "
        f"resume_instance {iid}; "
        "export LEERIE_REMOTE_EXIT_RC=0 )",
        env=env,
    )
    assert result2.returncode == 0, f"stderr: {result2.stderr}"

    state = read_state(aws_dir)
    leaked = leaked_resources(state)
    assert leaked["instances"] == {}, leaked
    assert leaked["volumes"] == {}, leaked
    (only_iid, rec), = state["instances"].items()
    assert only_iid == iid
    assert rec["state"] == "terminated"


# --- resume_instance: re-armed teardown honors the one-way ratchet -------


def test_resume_then_clean_exit_syncs_state_before_terminating(tmp_path):
    """The load-bearing ordering this file owns (test_ec2_decide_teardown.py
    pins decide_ec2_teardown's fetch-before-terminate ordering in
    isolation, never through resume_instance): once resume_instance has
    re-armed the EXIT trap, a subsequent clean exit must still sync state
    to the host BEFORE terminating the instance — the one-way-ratchet
    invariant must survive the resume path, not just the original
    provision path.

    Stubs _try_fetch_state_for_ec2_teardown to make its own `aws sts
    get-caller-identity` call (mirroring
    test_ec2_decide_teardown.py::test_fetch_happens_before_terminate_ordering's
    marker technique, but using an `sts` call rather than `describe-
    instances` as the marker — resume_instance's own ssh-target
    re-resolution already makes several describe-instances calls of its
    own, which would make a describe-instances-shaped marker ambiguous;
    `sts get-caller-identity` is called exactly once elsewhere, by
    require_aws at the very start, so a second occurrence is
    unambiguously the fetch stub) so the fetch is independently visible
    in the stub's call log, then asserts by call index — not just log
    presence — that the fetch-marker call precedes terminate-instances.
    """
    aws_dir = tmp_path / "bin"
    _stub_aws(aws_dir)
    iid = _seed_stopped_instance(aws_dir)
    env = _stub_env(aws_dir)

    # ec2-resume-instance.sh re-sources ec2-provision.sh internally, which
    # would clobber a fetch stub defined before that source line — define
    # it after both sources instead.
    script = f"""
( source {EC2_PROVISION_SH}
source {EC2_RESUME_SH}
_try_fetch_state_for_ec2_teardown() {{
  aws sts get-caller-identity >/dev/null 2>&1
  return 0
}}
resume_instance {iid}
export LEERIE_REMOTE_EXIT_RC=0 )
"""
    result = _run_bash(script, env=env)
    assert result.returncode == 0, f"stderr: {result.stderr}"

    state = read_state(aws_dir)
    (only_iid, rec), = state["instances"].items()
    assert only_iid == iid
    assert rec["state"] == "terminated"

    log = read_log(aws_dir)
    terminate_calls = [i for i, l in enumerate(log) if l.startswith("ec2 terminate-instances")]
    sts_calls = [i for i, l in enumerate(log) if l.startswith("sts get-caller-identity")]
    assert terminate_calls, log
    # require_aws's own preflight call plus the fetch stub's marker call.
    assert len(sts_calls) == 2, log
    fetch_marker_idx = sts_calls[-1]
    assert fetch_marker_idx < terminate_calls[0], (
        f"state sync must precede the destructive terminate-instances call; "
        f"fetch_marker_idx={fetch_marker_idx} terminate_idx={terminate_calls[0]}"
    )


def test_resume_then_sync_failure_leaves_instance_running_not_terminated(tmp_path):
    """The failure-path counterpart: if the re-armed teardown's state
    sync fails after resume, the one-way ratchet must leave the instance
    running rather than destroy possibly-unrecovered work — mirroring
    test_ec2_decide_teardown.py's sync-failure coverage, but through the
    resume path specifically."""
    aws_dir = tmp_path / "bin"
    _stub_aws(aws_dir)
    iid = _seed_stopped_instance(aws_dir)
    env = _stub_env(aws_dir)

    # Fetch stub defined after both sources (ec2-resume-instance.sh
    # re-sources ec2-provision.sh internally, which would otherwise
    # clobber a stub defined earlier — see the ordering test above).
    script = f"""
( source {EC2_PROVISION_SH}
source {EC2_RESUME_SH}
_try_fetch_state_for_ec2_teardown() {{ return 1; }}
resume_instance {iid}
export LEERIE_REMOTE_EXIT_RC=0 )
"""
    result = _run_bash(script, env=env)
    assert result.returncode == 0, f"stderr: {result.stderr}"

    state = read_state(aws_dir)
    (only_iid, rec), = state["instances"].items()
    assert only_iid == iid
    assert rec["state"] == "running", (
        "a failed state sync after resume must not escalate to termination"
    )

    log = read_log(aws_dir)
    assert not any(l.startswith("ec2 terminate-instances") for l in log)


# --- resume_instance: idempotent no-op on an already-running instance ----


def test_resume_instance_on_already_running_is_noop(tmp_path):
    aws_dir = tmp_path / "bin"
    _stub_aws(aws_dir)
    env = _stub_env(aws_dir)

    result = _run_bash(
        f"( source {EC2_PROVISION_SH}; "
        "provision_instance; "
        "echo \"iid=$LEERIE_EC2_INSTANCE_ID\" )",
        env=env,
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    iid = result.stdout.strip().split("iid=")[-1]

    state = read_state(aws_dir)
    assert state["instances"][iid]["state"] == "running"

    result2 = _run_bash(
        f"source {EC2_PROVISION_SH}; source {EC2_RESUME_SH}; resume_instance {iid}",
        env=env,
    )
    assert result2.returncode == 0, f"stderr: {result2.stderr}"
    assert "already running" in result2.stderr.lower()

    log = read_log(aws_dir)
    start_calls = [l for l in log if l.startswith("ec2 start-instances")]
    assert start_calls == [], "resuming an already-running instance must not call start-instances"


# --- resume_instance: never terminates or deletes volumes ----------------


def test_resume_instance_never_terminates_or_deletes_volume(tmp_path):
    aws_dir = tmp_path / "bin"
    _stub_aws(aws_dir)
    iid = _seed_stopped_instance(aws_dir)
    env = _stub_env(aws_dir)

    result = _run_bash(
        f"source {EC2_PROVISION_SH}; source {EC2_RESUME_SH}; resume_instance {iid}",
        env=env,
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"

    log = read_log(aws_dir)
    assert not any(l.startswith("ec2 terminate-instances") for l in log)
    assert not any(l.startswith("ec2 delete-volume") for l in log)


def test_resume_instance_failure_path_never_terminates_or_deletes_volume(tmp_path):
    """Even on the failure path (instance never becomes ready), resume
    must not escalate to termination — the one-way-ratchet invariant
    holds on failure just as much as on success."""
    aws_dir = tmp_path / "bin"
    _stub_aws(aws_dir)
    iid = _seed_stopped_instance(aws_dir, status_ok=False)
    env = _stub_env(aws_dir, {"LEERIE_INSTANCE_START_TIMEOUT": "2"})

    result = _run_bash(
        f"source {EC2_PROVISION_SH}; source {EC2_RESUME_SH}; resume_instance {iid}",
        env=env,
    )
    assert result.returncode != 0

    log = read_log(aws_dir)
    assert not any(l.startswith("ec2 terminate-instances") for l in log)
    assert not any(l.startswith("ec2 delete-volume") for l in log)


def test_resume_instance_source_contains_no_terminate_or_delete_volume_calls():
    """Source-level grep guard: no non-comment line in
    ec2-resume-instance.sh may invoke `aws ec2 terminate-instances` or
    `aws ec2 delete-volume` — a structural regression guard independent
    of the log-based assertions above, mirroring
    test_ec2_volume_reaping.py's regex-guard pattern. Comment lines are
    stripped first since the docstring legitimately names these actions
    when documenting the one-way-ratchet invariant this file must not
    violate."""
    import re

    code_lines = [
        line for line in EC2_RESUME_SH.read_text().splitlines()
        if not line.strip().startswith("#")
    ]
    code = "\n".join(code_lines)
    assert not re.search(r"\bterminate-instances\b", code)
    assert not re.search(r"\bdelete-volume\b", code)


# --- resume_instance: missing/terminated instance -------------------------


def test_resume_instance_requires_instance_id():
    result = _run_bash(
        f"source {EC2_PROVISION_SH}; source {EC2_RESUME_SH}; resume_instance ''",
        env={**REQUIRED_ENV, "PATH": "/usr/bin:/bin"},
    )
    assert result.returncode != 0
    assert "instance id required" in result.stderr.lower()


def test_resume_instance_fails_on_unknown_instance(tmp_path):
    aws_dir = tmp_path / "bin"
    _stub_aws(aws_dir)
    env = _stub_env(aws_dir)

    result = _run_bash(
        f"source {EC2_PROVISION_SH}; source {EC2_RESUME_SH}; resume_instance i-doesnotexist",
        env=env,
    )
    assert result.returncode != 0
    assert "no longer recoverable" in result.stderr.lower() or "does not exist" in result.stderr.lower()

    log = read_log(aws_dir)
    assert not any(l.startswith("ec2 start-instances") for l in log)


# --- resume_instance: sidecar clears pause fields -------------------------


def test_resume_instance_clears_pause_sidecar_fields(tmp_path):
    aws_dir = tmp_path / "bin"
    _stub_aws(aws_dir)
    iid = _seed_stopped_instance(aws_dir)
    state_host_dir = tmp_path / "state"
    run_id = "run-resume-abc"
    run_dir = state_host_dir / "runs" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text(json.dumps({
        "paused_at": "2026-07-15T00:00:00+00:00",
        "pause_reason": "worker-error",
        "ec2_instance_id": iid,
    }))

    env = _stub_env(aws_dir, {
        "LEERIE_STATE_HOST_DIR": str(state_host_dir),
        "LEERIE_RUN_ID": run_id,
    })

    result = _run_bash(
        f"source {EC2_PROVISION_SH}; source {EC2_RESUME_SH}; resume_instance {iid}",
        env=env,
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"

    run_json = json.loads((run_dir / "run.json").read_text())
    # update_run_json clears a key by setting it to JSON null (empty
    # string in -> None out), matching resume_machine.sh's clear
    # semantics for the Fly path.
    assert run_json.get("paused_at") is None
    assert run_json.get("pause_reason") is None
    assert run_json.get("ec2_instance_id") == iid
