"""Tests for the auto-finalize behavior wired into decide_teardown's
clean-exit branch (scripts/remote/provision.sh).

DESIGN §6 *Finalization*: on a clean exit (rc=0|10|75), decide_teardown
syncs the run dir to the host via _try_fetch_branch_for_teardown, then
sources scripts/host-finalize.sh and calls host_finalize <run-dir>.
Push success → destroy_machine. Push failure → leave machine running
with a recovery banner (mirrors the sync-failure pattern).

These tests stub _try_fetch_branch_for_teardown (always succeed),
host_finalize (configurable rc), and destroy_machine (just record).
That isolates decide_teardown's decision logic.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from tests.conftest import HAS_JQ

# decide_teardown runs in the launcher's own host shell (an EXIT/INT/TERM
# trap), and reads run.json's finished_at with real `jq`. Host-only by
# construction; see `tests/conftest.py`'s HAS_JQ.
pytestmark = pytest.mark.skipif(
    not HAS_JQ,
    reason="host-only script: needs real `jq`, which the launcher guarantees "
           "on the host and the leerie image deliberately omits",
)

REPO_ROOT = Path(__file__).resolve().parent.parent
PROVISION_SH = REPO_ROOT / "scripts" / "remote" / "provision.sh"


def _run_decide_teardown(
    *, host_finalize_rc: int, run_dir_exists: bool,
    host_finalize_sh_exists: bool = True,
    run_finished_at: str | None = "2026-06-02T10:30:00Z",
    tmp_path: Path,
) -> subprocess.CompletedProcess:
    """Invoke decide_teardown with stubs for fetch_branch + host_finalize
    + destroy_machine; return the completed process so callers can
    assert on stdout/stderr/rc.

    `run_finished_at`: ISO timestamp to write to run.json's `finished_at`
    field. Default is a real value (run reached phase_finalize). Pass
    `None` to simulate a clean exit before finalize (e.g.
    EXIT_NEEDS_ANSWERS=10), which is the not-finalized branch
    decide_teardown must skip auto-finalize on."""
    user_repo = tmp_path / "user_repo"
    run_dir = user_repo / ".leerie" / "runs" / "rid-001"
    if run_dir_exists:
        run_dir.mkdir(parents=True)
        run_json_data = (
            f'{{"finished_at": "{run_finished_at}"}}'
            if run_finished_at is not None
            else "{}"
        )
        (run_dir / "run.json").write_text(run_json_data)

    # Synthesize a fake LEERIE_REPO that holds a stub host-finalize.sh.
    leerie_repo = tmp_path / "leerie_repo"
    scripts_dir = leerie_repo / "scripts"
    scripts_dir.mkdir(parents=True)
    if host_finalize_sh_exists:
        (scripts_dir / "host-finalize.sh").write_text(
            "#!/usr/bin/env bash\n"
            f"host_finalize() {{ echo \"[stub-host-finalize] $*\"; return {host_finalize_rc}; }}\n"
        )

    script = f"""
source {PROVISION_SH}
# Override the things that would make real system calls.
_try_fetch_branch_for_teardown() {{ return 0; }}
destroy_machine() {{ echo "[stub] destroy_machine called"; LEERIE_MACHINE_ID=''; }}
stop_machine() {{ echo "[stub] stop_machine called"; LEERIE_MACHINE_ID=''; }}
update_run_json() {{ :; }}
export LEERIE_MACHINE_ID=test-mid-001
export LEERIE_REMOTE_EXIT_RC=0
export LEERIE_REMOTE_RUN_ID=rid-001
export LEERIE_RUN_ID=rid-001
export USER_REPO={user_repo}
export LEERIE_REPO={leerie_repo}
decide_teardown
"""
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True, text=True,
        env={**os.environ, "PATH": os.environ.get("PATH", "")},
    )


def test_clean_exit_push_success_destroys_machine(tmp_path):
    """Happy path: sync succeeds, host_finalize returns 0 (push OK),
    machine is destroyed."""
    result = _run_decide_teardown(
        host_finalize_rc=0, run_dir_exists=True, tmp_path=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    combined = result.stdout + result.stderr
    assert "auto-finalize: pushing + opening PR" in combined
    assert "[stub] destroy_machine called" in combined
    assert "WARNING — auto-finalize push FAILED" not in combined


def test_clean_exit_push_failure_keeps_machine_running(tmp_path):
    """Push failure: host_finalize returns 1, the trap MUST leave the
    machine running and print the recovery banner. This mirrors the
    sync-failure recovery path."""
    result = _run_decide_teardown(
        host_finalize_rc=1, run_dir_exists=True, tmp_path=tmp_path,
    )
    assert result.returncode == 0, result.stderr  # trap itself returns 0
    combined = result.stdout + result.stderr
    assert "auto-finalize: pushing + opening PR" in combined
    assert "WARNING — auto-finalize push FAILED" in combined
    assert "[stub] destroy_machine called" not in combined, (
        "On push failure the machine must NOT be destroyed — the work is "
        "on the host but not on origin, and the user needs the machine "
        "available to investigate or retry."
    )
    # Banner mentions the recovery commands.
    assert "leerie --finalize rid-001" in combined
    assert "leerie --kill rid-001" in combined


def test_clean_exit_missing_run_dir_falls_back_to_manual_hint(tmp_path):
    """Defensive: if sync said success but the expected run dir is not
    where decide_teardown looks for it, the trap falls back to the
    manual-finalize hint (no auto-finalize attempt) and destroys."""
    result = _run_decide_teardown(
        host_finalize_rc=0, run_dir_exists=False, tmp_path=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    combined = result.stdout + result.stderr
    assert "auto-finalize: pushing + opening PR" not in combined
    assert "[stub] destroy_machine called" in combined
    assert "leerie --finalize rid-001" in combined


def test_clean_exit_run_not_finalized_skips_auto_finalize(tmp_path):
    """A clean rc (0|10|75) when run.json has no `finished_at` means
    the orchestrator exited cleanly but didn't reach phase_finalize.
    Most commonly EXIT_NEEDS_ANSWERS=10 — the orchestrator wrote
    pending-questions.json and exited 10 so the user can answer
    clarification questions and re-run with --answers. The run is
    NOT failed; it's waiting.

    Calling host_finalize on a not-yet-finalized run would print a
    misleading 'auto-finalize push FAILED' banner because
    host_finalize requires run.json.finished_at + branch fields. The
    decide_teardown guard must skip host_finalize, destroy the
    machine (work is on the host either way), and tell the user how
    to recover via `leerie --finalize <run-id>` after answering."""
    result = _run_decide_teardown(
        host_finalize_rc=0, run_dir_exists=True,
        run_finished_at=None, tmp_path=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    combined = result.stdout + result.stderr
    assert "auto-finalize: pushing + opening PR" not in combined, (
        "host_finalize must NOT be invoked on a run that didn't "
        "reach phase_finalize — it would fail with a misleading "
        "'push FAILED' banner because run.json lacks branch info."
    )
    assert "WARNING — auto-finalize push FAILED" not in combined, (
        "the misleading push-failure banner must not appear; the "
        "run isn't failed, it's waiting for the user."
    )
    assert "did not reach finalize" in combined, (
        "the trap should explicitly say why it's skipping auto-"
        "finalize, so the user understands the run is waiting (not "
        "failed)."
    )
    assert "[stub] destroy_machine called" in combined, (
        "the machine must be destroyed — there's no work left on it "
        "(state was synced to host) and leaving it running would "
        "burn Fly $."
    )


def test_clean_exit_missing_host_finalize_sh_falls_back(tmp_path):
    """Defensive: if host-finalize.sh can't be found, fall back to the
    manual hint + destroy (work IS on host; the user can finalize
    manually). This protects against partial deployments where the
    image has the new provision.sh but not the script it sources."""
    result = _run_decide_teardown(
        host_finalize_rc=0, run_dir_exists=True,
        host_finalize_sh_exists=False, tmp_path=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    combined = result.stdout + result.stderr
    assert "auto-finalize: pushing + opening PR" not in combined
    assert "[stub] destroy_machine called" in combined
    assert "leerie --finalize rid-001" in combined


# ---------------------------------------------------------------------------
# Chain-aware detach banner
# ---------------------------------------------------------------------------
#
# Per-job runs that are part of a chain still pause / detach individually
# under the v5 Shape A model — there is no longer a special chain-mode
# short-circuit in decide_teardown. What survives is the rc=130 detach
# banner's chain-scoped command hints, which fire purely on
# LEERIE_CHAIN_ID being present in env.


def test_chain_id_detach_banner_includes_chain_scoped_commands(tmp_path):
    """When LEERIE_CHAIN_ID is set, the rc=130 detach banner lists
    chain-scoped recovery commands in addition to the run-scoped ones.
    The per-job decide_teardown otherwise behaves identically to a
    single-run detach (no destroy, no stop).
    """
    chain_id = "test-chain-uuid-001"
    run_id = "rid-001"
    user_repo = tmp_path / "user_repo"
    run_dir = user_repo / ".leerie" / "runs" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text(
        '{"finished_at": "2026-06-14T00:00:00Z", "branch": "leerie/runs/test"}'
    )
    script = f"""
source {PROVISION_SH}
_try_fetch_branch_for_teardown() {{ return 0; }}
destroy_machine() {{ echo "[stub] destroy_machine called"; LEERIE_MACHINE_ID=''; }}
stop_machine() {{ echo "[stub] stop_machine called"; LEERIE_MACHINE_ID=''; }}
update_run_json() {{ :; }}
export LEERIE_MACHINE_ID=test-mid-chain-001
export LEERIE_REMOTE_EXIT_RC=130
export LEERIE_REMOTE_RUN_ID={run_id}
export LEERIE_RUN_ID={run_id}
export LEERIE_CHAIN_ID={chain_id}
export USER_REPO={user_repo}
decide_teardown
"""
    result = subprocess.run(
        ["bash", "-c", script],
        capture_output=True, text=True,
        env={**os.environ, "PATH": os.environ.get("PATH", "")},
    )
    combined = result.stdout + result.stderr
    assert result.returncode == 0, combined
    # rc=130 specifically does NOT destroy or stop.
    assert "[stub] destroy_machine called" not in combined
    assert "[stub] stop_machine called" not in combined
    # Run-scoped (existing) commands still shown.
    assert f"leerie --resume {run_id}" in combined
    # Chain-scoped commands surfaced.
    assert f"leerie --status {chain_id}" in combined
    assert f"leerie --attach {chain_id}" in combined
    assert f"leerie --stop   {chain_id}" in combined
    assert f"leerie --kill   {chain_id}" in combined
