"""`plans_after_*` checkpoints must be snapshots, not live references.

Regression pin for run 3a4abba3 (2026-08-01). `_run_phases` assigned
`st.data["plans_after_X"] = plans` and then handed the SAME list to the
next phase — and several phases (`phase_reconcile`'s renames,
`phase_overlap_judge`'s merges/drops, both phase-3 soft-drop filters)
mutate `plans` **in place**. Every later `st.save()` therefore rewrote all
earlier checkpoints with the post-mutation plan.

Measured on that run before the fix: all six of `plans_after_plan` …
`plans_after_filters` were byte-identical, and `plans_after_reconcile`
held 15 subtasks while the overlap judge's independently-recorded input
(`calls.ndjson`) had 16 — the checkpoint did not hold what the resume
cursor assumes it holds. DESIGN §6 "Resumable planning" describes these
keys as the phase's output "as it stood immediately after" that phase, so
this was a silent contradiction of the documented contract.

Reuses the stub harness from `tests/test_resume_planning_reentry.py`.
"""
from __future__ import annotations

import json

import pytest

from tests.test_resume_planning_reentry import (
    _args, _caps, _drive, _make_state, _plan, _stub_common, _subtask,
    run_dirs,  # noqa: F401  — pytest fixture, imported for use
)


def _install_mutating_reconcile(leerie, monkeypatch, calls):
    """Stand in for the real `phase_reconcile`, which mutates in place."""
    async def _reconcile(plans, task, st, caps, models, efforts):
        calls["phase_reconcile"] = calls.get("phase_reconcile", 0) + 1
        for plan in plans:
            subtasks = plan.get("subtasks", [])
            if len(subtasks) > 1:
                del subtasks[-1]          # in-place drop, as a real phase does
            for s in subtasks:
                s["title"] = "renamed by reconcile"   # in-place field rewrite
        return plans
    monkeypatch.setattr(leerie, "phase_reconcile", _reconcile)


def _seed(leerie, run_dirs, calls, monkeypatch):  # noqa: F811
    _stub_common(leerie, monkeypatch, calls)

    async def _plan_phase(task, st, caps, models, efforts):
        calls["phase_plan"] = calls.get("phase_plan", 0) + 1
        return [_plan("refactoring",
                      _subtask("refactor-001"), _subtask("refactor-002"))]
    monkeypatch.setattr(leerie, "phase_plan", _plan_phase)
    _install_mutating_reconcile(leerie, monkeypatch, calls)

    st = _make_state(leerie, run_dirs, {
        "task": "t",
        "categories": ["refactoring"],
        "answers": {"source_of_truth": "codebase"},
        "plans_after_classify": [],
    })
    _drive(leerie, _args(), _caps(leerie), run_dirs, st)
    return st


def test_earlier_checkpoint_survives_a_later_in_place_mutation(
        leerie, monkeypatch, run_dirs):  # noqa: F811
    """`plans_after_plan` must still hold the PRE-reconcile plan."""
    calls: dict = {}
    st = _seed(leerie, run_dirs, calls, monkeypatch)
    before = st.data["plans_after_plan"]
    after = st.data["plans_after_reconcile"]
    n_before = sum(len(p.get("subtasks", [])) for p in before)
    n_after = sum(len(p.get("subtasks", [])) for p in after)
    assert n_before == 2, (
        "plans_after_plan must hold the 2 subtasks phase_plan produced, "
        f"not the {n_before} left after phase_reconcile mutated in place")
    assert n_after == 1
    titles = {s.get("title") for p in before for s in p.get("subtasks", [])}
    assert "renamed by reconcile" not in titles, (
        "a later phase's in-place field rewrite leaked backwards into an "
        "earlier checkpoint")


def test_adjacent_checkpoints_are_not_the_same_object(
        leerie, monkeypatch, run_dirs):  # noqa: F811
    """The direct shape of the defect: two checkpoints aliasing one list."""
    calls: dict = {}
    st = _seed(leerie, run_dirs, calls, monkeypatch)
    assert st.data["plans_after_plan"] is not st.data["plans_after_reconcile"]
    assert (json.dumps(st.data["plans_after_plan"], sort_keys=True)
            != json.dumps(st.data["plans_after_reconcile"], sort_keys=True)), (
        "a mutating phase ran between these two checkpoints, so they "
        "cannot be byte-identical; identical content means the earlier "
        "key is a live reference to the mutated list")


def test_checkpoints_round_trip_through_a_real_save(
        leerie, monkeypatch, run_dirs):  # noqa: F811
    """Anti-vacuity: the distinction must survive serialization, since
    `--resume` reads these back off disk, not out of memory."""
    calls: dict = {}
    st = _seed(leerie, run_dirs, calls, monkeypatch)
    st.save()
    # Read the on-disk artifact directly rather than constructing a second
    # State: State.__init__ takes an exclusive flock on the run dir (DESIGN
    # §6 *Single owner per run dir*), which `st` still holds.
    _leerie_root, _run_id, run_dir = run_dirs
    on_disk = json.loads((run_dir / "state.json").read_text())
    n_before = sum(len(p.get("subtasks", []))
                   for p in on_disk["plans_after_plan"])
    n_after = sum(len(p.get("subtasks", []))
                  for p in on_disk["plans_after_reconcile"])
    assert (n_before, n_after) == (2, 1), (
        "the distinction must survive serialization — --resume reads these "
        "back off disk, not out of memory")


def test_every_checkpoint_assignment_deepcopies(leerie):
    """Source-coupling guard. The behavioural tests above only exercise
    the plan→reconcile boundary; this pins all six so a new checkpoint
    added later cannot silently reintroduce the alias."""
    import inspect
    import re
    src = inspect.getsource(leerie._run_phases)
    keys = ("plans_after_plan", "plans_after_reconcile",
            "plans_after_overlap_judge", "plans_after_adherence_gate",
            "plans_after_coverage_gate", "plans_after_filters")
    for key in keys:
        assign = re.search(
            rf'st\.data\["{key}"\]\s*=\s*(.+)', src)
        assert assign, f"no assignment found for {key}"
        assert "copy.deepcopy(" in assign.group(1), (
            f'st.data["{key}"] must store a copy.deepcopy(plans); storing '
            "the live list lets a later in-place mutation rewrite this "
            "checkpoint on the next st.save()")
