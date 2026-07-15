"""Tests pinning decide_ec2_teardown()'s rc -> stop/terminate classification
table in scripts/remote/ec2-provision.sh (DESIGN §6 Remote pause-on-failure;
"the exit-code classification table is runtime-agnostic by construction" —
this is the EC2 counterpart of provision.sh's decide_teardown, exercised
independently in tests/test_decide_teardown_auto_finalize.py for Fly).

This is the highest-consequence EC2 behavior: a misclassification can
either destroy a user's in-progress work (terminating too eagerly) or
leak a billed instance (never tearing down). Three invariants carried
over from provision.sh's decide_teardown, each pinned by a dedicated
test below:

  1. LEERIE_TEARDOWN_DONE idempotency guard — a SIGINT fires the trap
     via INT, then again via EXIT; the second fire must not reclassify,
     even if $LEERIE_REMOTE_EXIT_RC was mutated between the two calls.
  2. The clean-exit branch (rc=0|10|11|75) fetches state to the host
     BEFORE terminating — a sync failure is a one-way-ratchet: leave
     the instance running rather than risk unrecoverable work.
  3. Every other non-zero rc pauses (stops, does not terminate) and
     records a pause reason.

Stubs _try_fetch_state_for_ec2_teardown directly (mirroring how
test_decide_teardown_auto_finalize.py stubs _try_fetch_branch_for_teardown)
so each test isolates decide_ec2_teardown's dispatch logic from the
real ec2-fetch-branch.sh transport (see tests/test_ec2_fetch_branch.py
for that file's own coverage). All AWS calls go through
the stateful stub in tests/ec2_stub.py — no real AWS API call is made.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from tests.ec2_stub import _stub_aws, read_log, read_state

REPO_ROOT = Path(__file__).resolve().parent.parent
EC2_PROVISION_SH = REPO_ROOT / "scripts" / "remote" / "ec2-provision.sh"

REQUIRED_ENV = {
    "LEERIE_EC2_AMI": "ami-0123456789abcdef0",
    "LEERIE_EC2_INSTANCE_TYPE": "m5.xlarge",
    "LEERIE_EC2_KEY_NAME": "leerie-key",
    "LEERIE_EC2_SECURITY_GROUP": "sg-0123456789abcdef0",
    "LEERIE_EC2_SUBNET_ID": "subnet-0123456789abcdef0",
}


def _run_bash(script: str, env: dict) -> subprocess.CompletedProcess:
    base_env = {k: v for k, v in os.environ.items()}
    base_env.pop("LEERIE_STATE_DIR", None)
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


def _provision_and_classify(
    aws_dir: Path, rc: int, *, fetch_ok: bool = True, extra_env: dict | None = None,
) -> subprocess.CompletedProcess:
    """Provision an instance (registering the teardown trap), then export
    LEERIE_REMOTE_EXIT_RC=<rc> and let the EXIT trap fire decide_ec2_teardown
    as the subshell exits. _try_fetch_state_for_ec2_teardown is stubbed to
    return 0/1 per `fetch_ok` so the sync outcome is deterministic."""
    env = _stub_env(aws_dir, extra_env)
    fetch_stub = "return 0" if fetch_ok else "return 1"
    script = (
        f"( source {EC2_PROVISION_SH}; "
        f"_try_fetch_state_for_ec2_teardown() {{ {fetch_stub}; }}; "
        "provision_instance; "
        f"export LEERIE_REMOTE_EXIT_RC={rc} )"
    )
    return _run_bash(script, env)


# --- clean-exit rc arms: sync then terminate ---------------------------------


def test_rc_0_syncs_then_terminates(tmp_path):
    aws_dir = tmp_path / "bin"
    _stub_aws(aws_dir)

    result = _provision_and_classify(aws_dir, 0, fetch_ok=True)

    assert result.returncode == 0, result.stderr
    assert "run branch + state synced to host" in result.stderr
    assert "terminating instance" in result.stderr
    state = read_state(aws_dir)
    (iid, rec), = state["instances"].items()
    assert rec["state"] == "terminated"


def test_rc_10_syncs_then_terminates(tmp_path):
    """rc=10 (EXIT_NEEDS_ANSWERS) is a genuine clean-exit disposition."""
    aws_dir = tmp_path / "bin"
    _stub_aws(aws_dir)

    result = _provision_and_classify(aws_dir, 10, fetch_ok=True)

    assert result.returncode == 0, result.stderr
    assert "terminating instance" in result.stderr
    state = read_state(aws_dir)
    (iid, rec), = state["instances"].items()
    assert rec["state"] == "terminated"


def test_rc_11_syncs_then_terminates(tmp_path):
    """rc=11 (EXIT_BUDGET_INFEASIBLE) is a genuine clean-exit disposition
    per ec2-provision.sh's own docstring table (mirrors provision.sh's
    budget-preflight-die case)."""
    aws_dir = tmp_path / "bin"
    _stub_aws(aws_dir)

    result = _provision_and_classify(aws_dir, 11, fetch_ok=True)

    assert result.returncode == 0, result.stderr
    assert "terminating instance" in result.stderr
    state = read_state(aws_dir)
    (iid, rec), = state["instances"].items()
    assert rec["state"] == "terminated"


def test_rc_75_syncs_then_terminates(tmp_path):
    """rc=75 (EX_TEMPFAIL: rate-limit / parse-fail) is a genuine
    clean-exit disposition."""
    aws_dir = tmp_path / "bin"
    _stub_aws(aws_dir)

    result = _provision_and_classify(aws_dir, 75, fetch_ok=True)

    assert result.returncode == 0, result.stderr
    assert "terminating instance" in result.stderr
    state = read_state(aws_dir)
    (iid, rec), = state["instances"].items()
    assert rec["state"] == "terminated"


# --- sync failure on the clean-exit path: one-way ratchet --------------------


def test_sync_failure_on_clean_exit_leaves_instance_running_no_terminate_call(tmp_path):
    """SAFETY-CRITICAL: a sync failure on ANY clean-exit rc must leave the
    instance running — verified both by final state AND by asserting that
    no `ec2 terminate-instances` call ever reached the stub's log. This is
    the one-way-ratchet invariant: destroy-then-fetch would make paid-for
    LLM work unrecoverable (mirrors provision.sh:262-272)."""
    aws_dir = tmp_path / "bin"
    _stub_aws(aws_dir)

    result = _provision_and_classify(aws_dir, 0, fetch_ok=False)

    assert result.returncode == 0, result.stderr
    assert "LEFT RUNNING" in result.stderr
    assert "sync from instance to host FAILED" in result.stderr

    state = read_state(aws_dir)
    (iid, rec), = state["instances"].items()
    assert rec["state"] == "running"

    log = read_log(aws_dir)
    terminate_calls = [l for l in log if l.startswith("ec2 terminate-instances")]
    assert terminate_calls == [], (
        "no terminate-instances call may reach AWS when the state sync "
        "failed — the instance (and any unrecovered work on it) must "
        "survive"
    )
    stop_calls = [l for l in log if l.startswith("ec2 stop-instances")]
    assert stop_calls == [], (
        "sync failure on the clean-exit path must not stop the instance "
        "either — it is left fully running for the user to investigate"
    )


def test_sync_failure_on_clean_exit_records_sync_fail_reason(tmp_path):
    """The sidecar gets sync_failed_at + sync_fail_reason so --list/--status
    can surface the failure."""
    aws_dir = tmp_path / "bin"
    _stub_aws(aws_dir)
    state_host_dir = tmp_path / "state"
    run_id = "rid-sync-fail"
    run_dir = state_host_dir / "runs" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text("{}")

    result = _provision_and_classify(
        aws_dir, 0, fetch_ok=False,
        extra_env={
            "LEERIE_STATE_HOST_DIR": str(state_host_dir),
            "LEERIE_RUN_ID": run_id,
        },
    )

    assert result.returncode == 0, result.stderr
    import json
    run_json = json.loads((run_dir / "run.json").read_text())
    assert run_json.get("sync_fail_reason") == "sync-failed-on-clean-exit"
    assert "sync_failed_at" in run_json
    assert run_json.get("ec2_instance_id", "").startswith("i-")


# --- detach arms: 130/143 do not pause -----------------------------------


def test_rc_130_detaches_without_pausing(tmp_path):
    aws_dir = tmp_path / "bin"
    _stub_aws(aws_dir)

    result = _provision_and_classify(aws_dir, 130, fetch_ok=True)

    assert result.returncode == 0, result.stderr
    assert "detached from run" in result.stderr
    assert "PAUSED" not in result.stderr
    assert "terminating instance" not in result.stderr

    state = read_state(aws_dir)
    (iid, rec), = state["instances"].items()
    assert rec["state"] == "running"

    log = read_log(aws_dir)
    assert not any(l.startswith("ec2 stop-instances") for l in log)
    assert not any(l.startswith("ec2 terminate-instances") for l in log)


def test_rc_143_detaches_without_pausing(tmp_path):
    """rc=143 (host-side SIGTERM) takes the same detach arm as rc=130."""
    aws_dir = tmp_path / "bin"
    _stub_aws(aws_dir)

    result = _provision_and_classify(aws_dir, 143, fetch_ok=True)

    assert result.returncode == 0, result.stderr
    assert "detached from run" in result.stderr
    assert "PAUSED" not in result.stderr

    state = read_state(aws_dir)
    (iid, rec), = state["instances"].items()
    assert rec["state"] == "running"

    log = read_log(aws_dir)
    assert not any(l.startswith("ec2 stop-instances") for l in log)
    assert not any(l.startswith("ec2 terminate-instances") for l in log)


# --- pause arm: any other non-zero rc stops, never terminates ----------------


def test_other_nonzero_rc_stops_not_terminates(tmp_path):
    aws_dir = tmp_path / "bin"
    _stub_aws(aws_dir)

    result = _provision_and_classify(aws_dir, 1, fetch_ok=True)

    assert result.returncode == 0, result.stderr
    assert "PAUSED" in result.stderr
    assert "terminating instance" not in result.stderr

    state = read_state(aws_dir)
    (iid, rec), = state["instances"].items()
    assert rec["state"] == "stopped"

    log = read_log(aws_dir)
    assert not any(l.startswith("ec2 terminate-instances") for l in log)


def test_other_nonzero_rc_records_pause_reason(tmp_path):
    aws_dir = tmp_path / "bin"
    _stub_aws(aws_dir)
    state_host_dir = tmp_path / "state"
    run_id = "rid-pause"
    run_dir = state_host_dir / "runs" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text("{}")

    result = _provision_and_classify(
        aws_dir, 2, fetch_ok=True,
        extra_env={
            "LEERIE_STATE_HOST_DIR": str(state_host_dir),
            "LEERIE_RUN_ID": run_id,
            "LEERIE_PAUSE_REASON": "worker-crash",
        },
    )

    assert result.returncode == 0, result.stderr
    import json
    run_json = json.loads((run_dir / "run.json").read_text())
    assert run_json.get("pause_reason") == "worker-crash"
    assert "paused_at" in run_json
    assert run_json.get("ec2_instance_id", "").startswith("i-")

    state = read_state(aws_dir)
    (iid, rec), = state["instances"].items()
    assert rec["state"] == "stopped"


def test_different_nonzero_rcs_all_pause_not_terminate(tmp_path):
    """A sweep across several unclassified rc values, confirming none of
    them slip into the terminate arm."""
    for rc in (1, 2, 3, 42, 99, 255):
        aws_dir = tmp_path / f"bin-{rc}"
        _stub_aws(aws_dir)

        result = _provision_and_classify(aws_dir, rc, fetch_ok=True)

        assert result.returncode == 0, f"rc={rc}: {result.stderr}"
        state = read_state(aws_dir)
        (iid, rec), = state["instances"].items()
        assert rec["state"] == "stopped", f"rc={rc} did not stop the instance"


# --- idempotency: double-fire must not reclassify with a clobbered rc -------


def test_double_fire_does_not_reclassify_with_clobbered_rc(tmp_path):
    """Simulates INT-then-EXIT: decide_ec2_teardown is called once with
    rc=0 (clean exit, terminates), then LEERIE_REMOTE_EXIT_RC is mutated
    to 1 (would route to pause/stop) and decide_ec2_teardown is called
    again. The second call MUST be a no-op — the instance must already be
    terminated and must NOT be stopped afterward, proving the trap did
    not reclassify using the clobbered rc."""
    aws_dir = tmp_path / "bin"
    _stub_aws(aws_dir)
    env = _stub_env(aws_dir)

    script = (
        f"source {EC2_PROVISION_SH}; "
        "_try_fetch_state_for_ec2_teardown() { return 0; }; "
        "provision_instance; "
        "export LEERIE_REMOTE_EXIT_RC=0; "
        "decide_ec2_teardown; "
        "export LEERIE_REMOTE_EXIT_RC=1; "
        "decide_ec2_teardown; "
        "echo done"
    )
    result = _run_bash(script, env)

    assert result.returncode == 0, result.stderr
    assert "done" in result.stdout

    state = read_state(aws_dir)
    (iid, rec), = state["instances"].items()
    assert rec["state"] == "terminated", (
        "the first (rc=0) classification must stick — the instance must "
        "end up terminated, not left running or re-stopped by the second "
        "(clobbered rc=1) call"
    )

    log = read_log(aws_dir)
    terminate_calls = [l for l in log if l.startswith("ec2 terminate-instances")]
    stop_calls = [l for l in log if l.startswith("ec2 stop-instances")]
    assert len(terminate_calls) == 1, "exactly one terminate call — the second fire is a no-op"
    assert stop_calls == [], "the clobbered second rc=1 must never reach stop_instance"


def test_double_fire_after_pause_does_not_re_pause_or_terminate(tmp_path):
    """The reverse ordering: first call pauses (stops) the instance, a
    second call with a clean rc must not then terminate it — the
    LEERIE_TEARDOWN_DONE guard blocks every pass after the first,
    regardless of which rc came first."""
    aws_dir = tmp_path / "bin"
    _stub_aws(aws_dir)
    env = _stub_env(aws_dir)

    script = (
        f"source {EC2_PROVISION_SH}; "
        "_try_fetch_state_for_ec2_teardown() { return 0; }; "
        "provision_instance; "
        "export LEERIE_REMOTE_EXIT_RC=1; "
        "decide_ec2_teardown; "
        "export LEERIE_REMOTE_EXIT_RC=0; "
        "decide_ec2_teardown; "
        "echo done"
    )
    result = _run_bash(script, env)

    assert result.returncode == 0, result.stderr
    assert "done" in result.stdout

    state = read_state(aws_dir)
    (iid, rec), = state["instances"].items()
    assert rec["state"] == "stopped", (
        "the first (rc=1) classification must stick — the instance stays "
        "stopped, not terminated by the second (clean-exit rc=0) call"
    )

    log = read_log(aws_dir)
    terminate_calls = [l for l in log if l.startswith("ec2 terminate-instances")]
    assert terminate_calls == [], "the clobbered second rc=0 must never reach terminate_instance"


# --- fetch-before-terminate ordering (one-way ratchet), independently pinned -


def test_fetch_happens_before_terminate_ordering(tmp_path):
    """The load-bearing ordering, pinned independently of feat-004's own
    coverage: at the moment _try_fetch_state_for_ec2_teardown runs, the
    instance must still be in `running` state (not yet terminated)."""
    aws_dir = tmp_path / "bin"
    _stub_aws(aws_dir)
    env = _stub_env(aws_dir)
    marker = tmp_path / "fetch_saw_running"

    script = f"""
source {EC2_PROVISION_SH}
_try_fetch_state_for_ec2_teardown() {{
  out="$(aws ec2 describe-instances --instance-ids "$LEERIE_EC2_INSTANCE_ID" 2>/dev/null)"
  echo "$out" | grep -q '"Name": "running"' && touch {marker}
  return 0
}}
provision_instance
export LEERIE_REMOTE_EXIT_RC=0
"""
    result = _run_bash(f"( {script} )", env)

    assert result.returncode == 0, result.stderr
    assert marker.exists(), (
        "the fetch hook did not observe the instance as still running — "
        "fetch must run strictly before terminate"
    )
    state = read_state(aws_dir)
    (iid, rec), = state["instances"].items()
    assert rec["state"] == "terminated"
