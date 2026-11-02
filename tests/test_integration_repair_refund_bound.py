"""An interrupted integration repair round gets its count back once, not on
every resume (DESIGN §6 *When integration cannot succeed*; round-3 review
L2).

Every resume that found `in_round` set `repair_rounds = round - 1`, so a
crash the round itself causes every time — an OOM kill or SIGKILL the
integrator session reliably triggers, which also skips the re-drive time
charge — was redone on every resume with neither budget ever drawn down.
Driven against a real git staging tree with a real two-parent merge; the
integrator crashes the process on every call, and each resume re-enters
the gate the way `integrate_wave` does.
"""
from __future__ import annotations

import subprocess

import pytest

from tests.test_integration_repair_loop import (DEFECT, SID, _Crash, _run,
                                                _setup, _Workers)


class _AlwaysCrash(_Workers):
    """The judge always finds the defect; the integrator dies every time,
    after leaving an uncommitted edit behind."""

    async def __call__(self, **kw):
        self.calls.append(kw)
        if kw["schema_key"] == "integration_judge":
            return {"merge_reviewed": True, "defects": [DEFECT],
                    "rationale": "r"}
        (self.staging / "app.py").write_text("half-written\n")
        raise _Crash()


def _resume(leerie, st):
    st.release_lock()
    st2 = leerie.State(st.run_dir.parent.parent, st.run_id)
    assert st2.load()
    return st2


def test_a_deterministic_crash_is_bounded(leerie, tmp_path, monkeypatch):
    st, staging, p1, merge = _setup(leerie, tmp_path)
    cap = int(leerie.DEFAULT_CAPS["integration_repair_rounds"])
    sessions = 0
    seen: list[dict] = []
    outcome = None
    for _attempt in range(4 * cap + 2):
        w = _AlwaysCrash(staging, [])
        try:
            outcome = _run(leerie, st, staging, monkeypatch, w)
        except _Crash:
            outcome = None
        monkeypatch.undo()
        sessions += w.kinds().count("integrator")
        st = _resume(leerie, st)
        rec = st.data["integration_gate"][SID]
        if "in_round" in rec:
            seen.append(dict(rec["in_round"]))
        if outcome is not None:
            break
    # Each round runs twice (the crash, then its one refunded redo), then
    # the cap backs the subtask out — it never loops forever.
    assert outcome is False
    assert sessions == 2 * cap
    rec = st.data["integration_gate"][SID]
    assert rec["outcome"] == "backed_out"
    assert rec["fallback_reason"] == f"not repaired within {cap} round(s)"
    assert rec["repair_rounds"] == cap
    assert st.data["subtask_status"][SID] == "unconverged"
    assert subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(staging),
                          capture_output=True, text=True
                          ).stdout.strip() == p1
    # The write-ahead record says which interruption was the refunded redo.
    assert [(f["round"], bool(f.get("refunded"))) for f in seen] == [
        (r, refunded) for r in range(1, cap + 1)
        for refunded in (False, True)]


def test_one_interruption_is_still_handed_back(leerie, tmp_path,
                                               monkeypatch):
    """The review round 2 M1 guarantee stands: a single crash in the last
    round does not back the subtask out."""
    st, staging, _p1, merge = _setup(leerie, tmp_path)
    with pytest.raises(_Crash):
        _run(leerie, st, staging, monkeypatch, _AlwaysCrash(staging, []))
    monkeypatch.undo()
    st = _resume(leerie, st)
    assert st.data["integration_gate"][SID]["in_round"] == {
        "round": 1, "pre": merge}
    ok = _run(leerie, st, staging, monkeypatch,
              _Workers(staging, [[DEFECT], []]))
    assert ok is True
    rec = st.data["integration_gate"][SID]
    assert rec["outcome"] == "repaired"
    assert rec["repair_rounds"] == 1
    assert rec["refunded_round"] == 1
    assert rec["rounds"][0]["verdict"] == "interrupted"
