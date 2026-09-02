"""phase_finalize must invoke cleanup.sh with --run-id <st.run_id>.

Regression pin for the silent-cleanup-no-op bug:

cleanup.sh's no-arg mode is the operator-facing interactive path
(IMPLEMENTATION.md §"cleanup.sh") — it scans for the most-recent
unfinished run, prompts y/N on stdin, and aborts on anything else. When
the orchestrator (which runs cleanup non-interactively) invokes it with
no args, `read -r answer` reads EOF, falls to the `*)` case, prints
"cleanup: aborted", and exits 0. The orchestrator sees a clean exit and
continues, while every worktree under .leerie/runs/<id>/worktrees/
survives on disk.

The fix: pass --run-id <st.run_id> from phase_finalize so cleanup.sh
takes the explicit single-run path that does not consult stdin.
"""
from __future__ import annotations

import asyncio
import inspect
import subprocess

import pytest


def test_run_script_passes_a_bounded_timeout_to_run_proc(leerie, monkeypatch):
    """`_run_script` previously called `run_proc` with no `timeout`
    (defaults to None / unbounded at leerie.py's run_proc), so a hung
    cleanup.sh/finalize.sh could block the orchestrator forever. It must
    now pass an explicit bounded timeout."""
    captured = {}

    async def fake_run_proc(cmd, **kwargs):
        captured.update(kwargs)
        return subprocess.CompletedProcess(cmd, 0, "", "")
    monkeypatch.setattr(leerie, "run_proc", fake_run_proc)

    asyncio.run(leerie._run_script("cleanup.sh", "--run-id", "r1"))

    assert captured.get("timeout") is not None, (
        "_run_script must pass an explicit, non-None timeout to run_proc"
    )
    assert captured["timeout"] > 0


def test_phase_finalize_invokes_cleanup_with_run_id(leerie):
    """phase_finalize must invoke cleanup.sh with --run-id and the run_id.

    Without the explicit --run-id, cleanup.sh falls into its interactive
    no-arg mode, reads EOF from the orchestrator's non-tty stdin, and
    silently aborts — leaving every subtask worktree on disk."""
    src = inspect.getsource(leerie.phase_finalize)
    assert '_run_script("cleanup.sh", "--run-id", st.run_id, "--subtask-branches")' in src, (
        "phase_finalize must invoke cleanup.sh with --run-id st.run_id and "
        "--subtask-branches. The --run-id avoids the interactive no-arg "
        "path (which silently aborts non-interactively); the "
        "--subtask-branches deletes per-subtask branches that are pure "
        "clutter post-finalize while keeping leerie/runs/<id> as the "
        "PR head."
    )


def test_phase_finalize_does_not_use_bare_cleanup(leerie):
    """Defensive pin: the bare invocation must not reappear via refactor."""
    src = inspect.getsource(leerie.phase_finalize)
    assert '_run_script("cleanup.sh")' not in src, (
        "phase_finalize must not invoke cleanup.sh with no args. "
        "The no-arg mode is the operator-facing y/N path and aborts "
        "non-interactively."
    )


def test_phase_finalize_has_completion_gate(leerie):
    """Fix I / DESIGN §6: phase_finalize must refuse to finalize a run whose
    waves are not all integrated (completed_waves < len(waves)), so a stray
    finalize-only invocation never pushes a partial run branch or opens a
    premature PR. The guard must precede the finalize.sh call."""
    src = inspect.getsource(leerie.phase_finalize)
    assert "completed_waves" in src and "refusing to finalize" in src, (
        "phase_finalize must gate on completed_waves == len(waves) and "
        "die() with a 'refusing to finalize' message on a partial run."
    )
    # The gate must come BEFORE the finalize.sh invocation (else a partial
    # run would already have run finalize/cleanup before the check fires).
    gate_pos = src.index("refusing to finalize")
    finalize_pos = src.index('_run_script("finalize.sh"')
    assert gate_pos < finalize_pos, (
        "the completion gate must fire before _run_script('finalize.sh')"
    )


def test_current_phase_stamped_after_finalize_sh_succeeds(leerie):
    """Fix (empty-run-branch finalize): phase_finalize must stamp
    current_phase="phase 6: finalize" ONLY AFTER finalize.sh returns 0,
    never before it.

    Root cause it guards: `die()` on a finalize.sh failure sets
    `finished_at` via main()'s `except SystemExit` handler. If
    current_phase were stamped on phase entry (before finalize.sh), a
    *died* finalize would leave state byte-identical to a *succeeded* one
    (finished_at set AND current_phase == "phase 6: finalize"), and the
    resume completion guard in _run_phases would declare the run
    "already completed" and hand the host launcher an empty run branch to
    push — which then fails at `gh pr create` with "No commits between …".

    Stamping AFTER the finalize.sh success check keeps a died finalize
    resumable: current_phase stays at its pre-finalize value, the resume
    guard falls through, and resume re-runs finalize.sh's non-empty
    check. This test pins the ordering by source position."""
    src = inspect.getsource(leerie.phase_finalize)
    finalize_call = src.index('_run_script("finalize.sh"')
    stamp = src.index('st.data["current_phase"] = "phase 6: finalize"')
    assert finalize_call < stamp, (
        'phase_finalize must set current_phase="phase 6: finalize" AFTER '
        'the _run_script("finalize.sh") call, not before it. Stamping before '
        "makes a died finalize indistinguishable from a successful one to "
        "the resume completion guard, which then pushes an empty branch."
    )
    # And specifically after the die-on-nonzero check, so a nonzero
    # finalize.sh never reaches the stamp.
    die_check = src.index('finalize failed (run branch is intact)')
    assert die_check < stamp, (
        "the current_phase stamp must come after the "
        "'finalize failed (run branch is intact)' die() guard, so a "
        "nonzero finalize.sh exits before current_phase is stamped."
    )


def test_phase_finalize_catches_timeout_around_both_cleanup_calls(leerie):
    """Both cleanup.sh call sites in phase_finalize (the no-work-run path
    and the main finalize path) must swallow subprocess.TimeoutExpired —
    a hung cleanup.sh is a best-effort failure, not something that should
    crash a finalize that already confirmed the run branch is real."""
    src = inspect.getsource(leerie.phase_finalize)
    assert src.count("except subprocess.TimeoutExpired") >= 2, (
        "phase_finalize must catch subprocess.TimeoutExpired at both "
        "cleanup.sh call sites (no-work-run path and main finalize path)"
    )


def test_phase_finalize_survives_a_cleanup_sh_timeout(
        leerie, monkeypatch, tmp_path):
    """Execution-level pin: if cleanup.sh times out, phase_finalize must
    still reach its terminal state instead of letting TimeoutExpired
    propagate uncaught and crash the run."""
    async def _fake_run_script(name, *args):
        if name == "cleanup.sh":
            raise subprocess.TimeoutExpired(["bash", name, *args], 600)
        return subprocess.CompletedProcess(["bash", name, *args], 0, "", "")

    async def _fake_run_proc(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, "", "")

    async def _noop_capture(*a, **k):
        return None

    monkeypatch.setattr(leerie, "_run_script", _fake_run_script)
    monkeypatch.setattr(leerie, "run_proc", _fake_run_proc)
    monkeypatch.setattr(leerie, "capture_repo_deps", _noop_capture)

    leerie_root = tmp_path / ".leerie"
    (leerie_root / "runs" / "r1").mkdir(parents=True)
    st = leerie.State(leerie_root, "r1", repo_root=tmp_path)
    st.data["waves"] = [["feat-001"]]
    st.data["completed_waves"] = 1
    st.data["worker_count"] = 2
    st.data["subtask_status"] = {"feat-001": "complete"}
    st.data["working_branch"] = "main"
    caps = dict(leerie.DEFAULT_CAPS)
    caps["max_total_workers"] = 100
    try:
        asyncio.run(leerie.phase_finalize(
            st.leerie_root, st, no_push=True, no_verify=False,
            caps=caps, models={}, efforts={}))
    finally:
        st.release_lock()

    assert st.data["current_phase"] == "phase 6: finalize", (
        "phase_finalize must reach its terminal state even when "
        "cleanup.sh times out"
    )
    assert st.data.get("finished_at"), (
        "a timed-out cleanup.sh must not prevent finished_at from being "
        "recorded"
    )


def test_resume_completion_guard_keys_on_current_phase(leerie):
    """The resume completion guard (in _run_phases) treats a run as
    terminal when finished_at is set AND current_phase == "phase 6:
    finalize". This test pins that the guard reads current_phase — the
    discriminator the Fix-1 ordering change relies on. If the guard stopped
    consulting current_phase, the ordering fix would be silently defeated."""
    src = inspect.getsource(leerie._run_phases)
    assert '"phase 6: finalize"' in src and "finished_at" in src, (
        "the resume completion guard must key on finished_at AND "
        'current_phase == "phase 6: finalize"; the Fix-1 ordering change '
        "depends on current_phase distinguishing a died finalize from a "
        "successful one."
    )
