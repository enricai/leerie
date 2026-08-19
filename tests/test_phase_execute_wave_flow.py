"""`phase_execute`'s wave loop (phases 4-5) — the happy-path flow through
settling subtasks, integrating a wave, and advancing `completed_waves`.

`TestPhaseExecuteDiskLowSpace` in `test_disk_preflight.py` already covers the
mid-run disk-headroom raise and the "wave already fully complete" shortcut.
This file covers the surrounding control flow that neither of those two
narrow cases exercises: settling a subtask via `_settle_subtask`, integrating
it via `integrate_wave`, pruning its worktree, the conflict-marker safety
net, the `blocked` die() path, and the integration-integrity shortfall gate
(DESIGN §6 "the completion signal is completed_waves == len(waves)")."""
from __future__ import annotations

import asyncio
from unittest import mock

import pytest


def _mock_st(waves, **extra_data):
    st = mock.Mock()
    st.data = {"waves": waves, "completed_waves": 0,
               "subtask_status": {}, "integration_gate": {},
               "skip_base_baseline": True, **extra_data}
    st.save = mock.Mock()
    st.repo_root = "/tmp/repo"
    st.path = "/tmp/state.json"
    return st


def _patch_common(leerie, monkeypatch, *, disk_ratio=0.90):
    monkeypatch.setattr(leerie, "_run_script",
                        mock.AsyncMock(return_value=mock.Mock(returncode=0, stderr="")))
    monkeypatch.setattr(leerie, "_prune_leerie_worktrees", mock.Mock())
    monkeypatch.setattr(leerie, "_disk_free_ratio", lambda p: disk_ratio)
    monkeypatch.setattr(leerie, "_degrade_max_parallel_for_wave",
                        lambda max_parallel, demand: max_parallel)
    monkeypatch.setattr(leerie, "_scan_conflict_markers",
                        mock.AsyncMock(return_value=None))
    monkeypatch.setattr(leerie, "_prune_subtask_worktree", mock.AsyncMock())


def test_full_wave_settles_integrates_and_advances(leerie, tmp_path, monkeypatch):
    """A single-wave, single-subtask run: settle -> integrate -> prune ->
    completed_waves advances to 1, with no die()."""
    st = _mock_st([["feat-001"]])
    caps = dict(leerie.DEFAULT_CAPS)
    caps["max_parallel"] = 5

    _patch_common(leerie, monkeypatch)
    monkeypatch.setattr(
        leerie, "_settle_subtask",
        mock.AsyncMock(return_value={"status": "complete", "intent": "x",
                                      "criteria_results": []}))
    monkeypatch.setattr(
        leerie, "integrate_wave",
        mock.AsyncMock(return_value=["feat-001"]))

    asyncio.run(leerie.phase_execute(tmp_path, st, caps, {}, {}))

    assert st.data["completed_waves"] == 1


def test_blocked_subtask_dies_after_integrating_the_rest(leerie, tmp_path, monkeypatch):
    """A blocked/failed subtask in the wave dies (DESIGN §3 *Partial-wave
    integration*) — but only AFTER integrate_wave has run, so a sibling
    success in the same wave is still merged first."""
    st = _mock_st([["feat-001", "feat-002"]])
    caps = dict(leerie.DEFAULT_CAPS)
    caps["max_parallel"] = 5

    _patch_common(leerie, monkeypatch)

    async def fake_settle(sid, *a, **k):
        if sid == "feat-001":
            return {"status": "complete", "intent": "x", "criteria_results": []}
        return {"status": "blocked", "blocker": "missing API key"}

    monkeypatch.setattr(leerie, "_settle_subtask", fake_settle)
    monkeypatch.setattr(leerie, "integrate_wave",
                        mock.AsyncMock(return_value=["feat-001"]))

    with pytest.raises(SystemExit):
        asyncio.run(leerie.phase_execute(tmp_path, st, caps, {}, {}))

    assert st.data["blocked"]["feat-002"] == "missing API key"
    # completed_waves must NOT advance — the wave never fully settled.
    assert st.data["completed_waves"] == 0


def test_conflict_marker_left_behind_dies(leerie, tmp_path, monkeypatch):
    """A conflict marker surviving integration is a deterministic
    post-integration safety net independent of any per-subtask confidence
    gate — it must halt the run."""
    st = _mock_st([["feat-001"]])
    caps = dict(leerie.DEFAULT_CAPS)
    caps["max_parallel"] = 5

    _patch_common(leerie, monkeypatch)
    monkeypatch.setattr(
        leerie, "_settle_subtask",
        mock.AsyncMock(return_value={"status": "complete", "intent": "x",
                                      "criteria_results": []}))
    monkeypatch.setattr(leerie, "integrate_wave",
                        mock.AsyncMock(return_value=["feat-001"]))
    monkeypatch.setattr(
        leerie, "_scan_conflict_markers",
        mock.AsyncMock(return_value="unresolved conflict markers in src/x.py"))

    with pytest.raises(SystemExit):
        asyncio.run(leerie.phase_execute(tmp_path, st, caps, {}, {}))

    assert st.data["completed_waves"] == 0


def test_integration_integrity_shortfall_dies_without_advancing(leerie, tmp_path, monkeypatch):
    """A wave that settles with zero failures but whose `integrate_wave`
    returns fewer sids than expected (a silent integration skip — the
    incident class DESIGN §6 documents) must halt rather than advance
    `completed_waves`, and it must record no `blocked` entry (there is no
    per-sid failure to attribute)."""
    st = _mock_st([["feat-001"]])
    caps = dict(leerie.DEFAULT_CAPS)
    caps["max_parallel"] = 5

    _patch_common(leerie, monkeypatch)
    monkeypatch.setattr(
        leerie, "_settle_subtask",
        mock.AsyncMock(return_value={"status": "complete", "intent": "x",
                                      "criteria_results": []}))
    # Silent shortfall: expected 1 completed subtask, integrate_wave
    # reports 0 integrated, with no blocked/failed status anywhere.
    monkeypatch.setattr(leerie, "integrate_wave",
                        mock.AsyncMock(return_value=[]))

    with pytest.raises(SystemExit):
        asyncio.run(leerie.phase_execute(tmp_path, st, caps, {}, {}))

    assert st.data["completed_waves"] == 0
    assert "feat-001" not in st.data.get("blocked", {})


def test_setup_run_failure_dies_before_any_wave_work(leerie, tmp_path, monkeypatch):
    """A non-zero setup-run.sh exit dies immediately, naming its stderr,
    before any worktree pruning or wave work happens."""
    st = _mock_st([["feat-001"]])
    caps = dict(leerie.DEFAULT_CAPS)
    caps["max_parallel"] = 5

    monkeypatch.setattr(
        leerie, "_run_script",
        mock.AsyncMock(return_value=mock.Mock(
            returncode=1, stderr="fatal: run-branch already exists")))
    monkeypatch.setattr(
        leerie, "_prune_leerie_worktrees",
        mock.Mock(side_effect=AssertionError("must not prune after a setup failure")))

    with pytest.raises(SystemExit):
        asyncio.run(leerie.phase_execute(tmp_path, st, caps, {}, {}))


def test_baseline_exception_is_defense_in_depth_and_does_not_abort(
        leerie, tmp_path, monkeypatch):
    """`_capture_conformance_baseline` is documented to never raise, but a
    bug in its own glue must not block the run — caught, logged, and the
    wave proceeds with no baseline."""
    st = _mock_st([["feat-001"]], skip_base_baseline=False)
    caps = dict(leerie.DEFAULT_CAPS)
    caps["max_parallel"] = 5

    _patch_common(leerie, monkeypatch)
    monkeypatch.setattr(
        leerie, "_capture_conformance_baseline",
        mock.AsyncMock(side_effect=RuntimeError("boom")))
    monkeypatch.setattr(
        leerie, "_settle_subtask",
        mock.AsyncMock(return_value={"status": "complete", "intent": "x",
                                      "criteria_results": []}))
    monkeypatch.setattr(leerie, "integrate_wave",
                        mock.AsyncMock(return_value=["feat-001"]))

    asyncio.run(leerie.phase_execute(tmp_path, st, caps, {}, {}))

    assert st.data["completed_waves"] == 1
