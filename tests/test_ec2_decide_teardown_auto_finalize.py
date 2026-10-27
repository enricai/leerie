"""decide_ec2_teardown's clean-exit branch auto-finalizes (host_finalize)
after a successful fetch and before terminate, mirroring provision.sh."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from tests.conftest import HAS_JQ

pytestmark = pytest.mark.skipif(
    not HAS_JQ,
    reason="host-only script: needs real `jq`, which the launcher guarantees "
           "on the host and the leerie image deliberately omits",
)

REPO_ROOT = Path(__file__).resolve().parent.parent
EC2_PROVISION_SH = REPO_ROOT / "scripts" / "remote" / "ec2-provision.sh"


def _run(tmp_path: Path, *, rc: int = 0, fetch_rc: int = 0,
         finalize_rc: int = 0, finished_at: str | None = "2026-06-02T10:30:00Z",
         sidecar_no_push: bool | None = None, no_push_env: str = "",
         launcher_hook: bool = False,
         ) -> str:
    user_repo = tmp_path / "user_repo"
    run_dir = user_repo / ".leerie" / "runs" / "rid-001"
    run_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text(
        f'{{"finished_at": "{finished_at}"}}' if finished_at else "{}")
    if sidecar_no_push is not None:
        (run_dir / "ec2-instance.json").write_text(
            f'{{"ec2_instance_id": "i-123", "host_no_push": {str(sidecar_no_push).lower()}}}')
    leerie_repo = tmp_path / "leerie_repo"
    (leerie_repo / "scripts").mkdir(parents=True)
    (leerie_repo / "scripts" / "host-finalize.sh").write_text(
        'host_finalize() { echo "CALL host_finalize"; '
        f'return {finalize_rc}; }}\n')
    script = f"""
source {EC2_PROVISION_SH}
_try_fetch_state_for_ec2_teardown() {{ echo "CALL fetch"; return {fetch_rc}; }}
terminate_instance() {{ echo "CALL terminate"; LEERIE_EC2_INSTANCE_ID=''; }}
stop_instance() {{ echo "CALL stop"; LEERIE_EC2_INSTANCE_ID=''; }}
update_run_json() {{ :; }}
export LEERIE_EC2_INSTANCE_ID=i-123 LEERIE_REMOTE_EXIT_RC={rc}
export LEERIE_RUN_ID=rid-001 USER_REPO={user_repo} LEERIE_REPO={leerie_repo}
NO_PUSH={no_push_env}
{'_ec2_launcher_exit_cleanup() { echo "CALL cleanup"; }' if launcher_hook else ''}
decide_ec2_teardown
decide_ec2_teardown
"""
    r = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                       env={**os.environ})
    assert r.returncode == 0, r.stderr
    return r.stdout + r.stderr


def _calls(out: str) -> list[str]:
    return [ln.split("CALL ", 1)[1] for ln in out.splitlines()
            if ln.startswith("CALL ")]


def test_clean_exit_order_fetch_finalize_terminate(tmp_path):
    assert _calls(_run(tmp_path)) == ["fetch", "host_finalize", "terminate"]


def test_finalize_failure_leaves_instance_running(tmp_path):
    out = _run(tmp_path, finalize_rc=1)
    assert _calls(out) == ["fetch", "host_finalize"]
    assert "auto-finalize push FAILED" in out


def test_unfinished_run_skips_finalize_but_terminates(tmp_path):
    assert _calls(_run(tmp_path, rc=10, finished_at=None)) == [
        "fetch", "terminate"]


def test_sync_failure_never_finalizes(tmp_path):
    assert _calls(_run(tmp_path, fetch_rc=1)) == ["fetch"]


def test_non_clean_rc_does_not_finalize(tmp_path):
    assert "host_finalize" not in _calls(_run(tmp_path, rc=1))


def test_detach_rc_130_does_not_finalize_or_terminate(tmp_path):
    calls = _calls(_run(tmp_path, rc=130))
    assert "host_finalize" not in calls
    assert "terminate" not in calls


def test_launch_time_no_push_skips_finalize_and_terminates(tmp_path):
    # A resumed run's launcher may not carry --no-push; the sidecar does.
    out = _run(tmp_path, sidecar_no_push=True)
    assert _calls(out) == ["fetch", "terminate"]
    assert "--no-push was set at launch" in out


def test_no_push_on_this_launcher_also_skips_finalize(tmp_path):
    assert _calls(_run(tmp_path, no_push_env="true")) == ["fetch", "terminate"]


def test_recorded_push_intent_still_finalizes(tmp_path):
    # Control: the same sidecar with host_no_push false keeps auto-finalize.
    assert _calls(_run(tmp_path, sidecar_no_push=False)) == [
        "fetch", "host_finalize", "terminate"]


def test_launcher_cleanup_runs_after_teardown_on_every_call(tmp_path):
    # The replacement EXIT trap must still run the launcher's STAGE cleanup,
    # last, and the idempotent second call (INT then EXIT) runs it too.
    calls = _calls(_run(tmp_path, launcher_hook=True))
    assert calls == ["fetch", "host_finalize", "terminate", "cleanup", "cleanup"]


def test_launcher_cleanup_runs_when_the_instance_is_left_running(tmp_path):
    calls = _calls(_run(tmp_path, fetch_rc=1, launcher_hook=True))
    assert calls[0] == "fetch" and calls[-1] == "cleanup"
    assert "terminate" not in calls
