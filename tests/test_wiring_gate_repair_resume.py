"""A kill between the wiring gate's verdict and the re-scheduled
`plan_snapshot` must not let `resume` skip the gate onto the unrepaired plan
(DESIGN §5 *A wiring re-check on the fully-merged plan*).

The gate's verdict (`wiring_gate`) is the resume skip; the edges it added
live only in the plan `_run_phases` re-schedules into `plan_snapshot`. Saved
in two writes, a kill between them resumed with the verdict present, the
gate skipped, and the snapshot written before the gate — so the run executed
without the edges. Driven through the real `_run_phases` and the real
`phase_wiring_gate`; the judge's verdict is scripted, every other phase is
the shared stub harness. The process is killed after each save from the
verdict's first appearance in state until the repaired snapshot is on disk,
then resumed; the snapshot `phase_execute` receives must order the repaired
edge every time."""
from __future__ import annotations

import asyncio
import json

import pytest

from tests.test_resume_planning_reentry import (
    EFFORTS, MODELS, _StopAtExecute, _args, _caps, _make_state, _plan,
    _stub_common, _subtask,
    run_dirs,  # noqa: F401  — pytest fixture, imported for use
)

PLANS = [_plan("refactoring",
               _subtask("refactor-040", provides=["thing"]),
               _subtask("refactor-041"))]

DEFECT = {"kind": "missing_requires", "sid": "refactor-041",
          "tag_or_dep": "thing", "severity": "live_defect",
          "concrete_reason": "refactor-041 uses the thing refactor-040 adds"}


class _Crash(BaseException):
    """A process death: no handler on the way can swallow it."""


def _seeded() -> dict:
    data = {"task": "t", "categories": ["refactoring"],
            "answers": {"source_of_truth": "codebase"}}
    for phase in ("classify", "plan", "reconcile", "overlap_judge",
                  "adherence_gate", "coverage_gate", "filters"):
        data[f"plans_after_{phase}"] = PLANS
    return data


def _ordered(snap: dict) -> bool:
    pos = {sid: i for i, w in enumerate(snap["waves"]) for sid in w}
    return pos["refactor-040"] < pos["refactor-041"]


def _harness(leerie, monkeypatch, real_gate, judged: list[int]):
    calls: dict = {}
    _stub_common(leerie, monkeypatch, calls)
    monkeypatch.setattr(leerie, "phase_wiring_gate", real_gate)

    async def judge(**kw):
        assert kw["schema_key"] == "wiring_judge"
        judged.append(1)
        return {"plan_reviewed": True, "rationale": "r",
                "wiring_defects": [DEFECT]}
    monkeypatch.setattr(leerie, "claude_p", judge)
    return calls


def _drive(leerie, st, run_dirs, crash_at: int | None,  # noqa: F811
           marks: list[int]) -> None:
    """One `_run_phases`, stopped at `phase_execute`. Saves are numbered
    from the first one carrying `wiring_gate` until the one carrying an
    ordered `plan_snapshot`."""
    real_save = type(st).save
    seen = {"n": 0, "done": False}

    def save(self):
        real_save(self)
        if seen["done"] or "wiring_gate" not in self.data:
            return
        seen["n"] += 1
        marks.append(seen["n"])
        if _ordered(self.data.get("plan_snapshot") or {"waves": [
                ["refactor-040", "refactor-041"]]}):
            seen["done"] = True
        if crash_at is not None and seen["n"] == crash_at:
            raise _Crash(f"killed after save {crash_at}")

    st.save = save.__get__(st)
    _root, _rid, run_dir = run_dirs
    try:
        asyncio.run(leerie._run_phases(
            _args(), _caps(leerie), run_dir, st, "codebase", "normal",
            MODELS, EFFORTS))
    finally:
        st.save = real_save.__get__(st)


def _on_disk(run_dirs) -> dict:  # noqa: F811
    return json.loads((run_dirs[2] / "state.json").read_text())


@pytest.mark.parametrize("crash_at", [None, 1, 2])
def test_killed_after_each_save_then_resume_keeps_the_repair(
        leerie, monkeypatch, run_dirs, crash_at):  # noqa: F811
    real_gate = leerie.phase_wiring_gate
    judged: list[int] = []
    _harness(leerie, monkeypatch, real_gate, judged)
    st = _make_state(leerie, run_dirs, _seeded())
    marks: list[int] = []
    if crash_at is None:
        # The uninterrupted reference, and the window's size.
        with pytest.raises(_StopAtExecute):
            _drive(leerie, st, run_dirs, None, marks)
        assert marks == [1], marks
        assert _ordered(_on_disk(run_dirs)["plan_snapshot"])
        assert _on_disk(run_dirs)["wiring_gate"]["repairs"]
        return
    if crash_at > 1:
        pytest.skip("the verdict and the repaired snapshot are one save")
    with pytest.raises(_Crash):
        _drive(leerie, st, run_dirs, crash_at, marks)
    disk = _on_disk(run_dirs)
    # Either both reached disk or neither did.
    assert ("wiring_gate" in disk) == _ordered(
        disk.get("plan_snapshot") or {"waves": [["refactor-040",
                                                 "refactor-041"]]})
    st.release_lock()
    st2 = leerie.State(run_dirs[0], run_dirs[1])
    assert st2.load()
    _harness(leerie, monkeypatch, real_gate, judged)
    with pytest.raises(_StopAtExecute):
        _drive(leerie, st2, run_dirs, None, [])
    assert _ordered(st2.data["plan_snapshot"])
    assert _ordered(_on_disk(run_dirs)["plan_snapshot"])
    st2.release_lock()


def test_kill_inside_the_gate_after_its_verdict_reruns_it(
        leerie, monkeypatch, run_dirs):  # noqa: F811
    """The verdict set in memory, the process killed before `_run_phases`
    reaches its save: nothing of it is on disk, and the resume judges
    again and keeps the repair."""
    real_gate = leerie.phase_wiring_gate
    judged: list[int] = []
    _harness(leerie, monkeypatch, real_gate, judged)

    def die_in_schedule(*a, **k):
        raise _Crash("killed while re-scheduling")
    real_schedule = leerie._schedule
    calls = {"n": 0}

    def schedule(*a, **k):
        calls["n"] += 1
        if calls["n"] == 2:  # the re-schedule after the repair
            die_in_schedule()
        return real_schedule(*a, **k)
    monkeypatch.setattr(leerie, "_schedule", schedule)
    st = _make_state(leerie, run_dirs, _seeded())
    with pytest.raises(_Crash):
        _drive(leerie, st, run_dirs, None, [])
    assert "wiring_gate" not in _on_disk(run_dirs)
    st.release_lock()
    monkeypatch.setattr(leerie, "_schedule", real_schedule)
    st2 = leerie.State(run_dirs[0], run_dirs[1])
    assert st2.load()
    _harness(leerie, monkeypatch, real_gate, judged)
    with pytest.raises(_StopAtExecute):
        _drive(leerie, st2, run_dirs, None, [])
    assert len(judged) == 2
    assert _ordered(st2.data["plan_snapshot"])
    st2.release_lock()
