"""`resume` must not bypass the phase-3 semantic wiring gate.

Regression pins for run 3a4abba3 (2026-08-01). `phase_wiring_gate` is a
detect-and-die gate: on a concrete `wiring_defects` entry it `die()`s with
the defect named. Its skip-on-resume used to be keyed on the presence of
`plan_snapshot` — but `plan_snapshot` is written a few lines *earlier*, by
design, so that a die() at either terminal gate does not discard the whole
planning spend. That made the snapshot present even when the gate had
FAILED, so the next `resume` skipped the entire `plan_snapshot` branch,
never re-invoked the gate, and executed the very plan the gate rejected —
while the die() message claimed the gate had "no bypass flag".

The skip is now keyed on `st.data["wiring_gate"]`, which `phase_wiring_gate`
writes only on a clean pass. Three shapes matter, and all three are pinned
here: gate-died (must re-run), fresh run (must run), gate-passed (must skip,
so the budget-check resume this branch exists for stays cheap).

Reuses the stub harness from `tests/test_resume_planning_reentry.py` rather
than reimplementing it — that module already stubs every phase function
`_run_phases` touches and records call counts, which is exactly the
instrument needed here.
"""
from __future__ import annotations

import pytest

from tests.test_resume_planning_reentry import (
    _args, _caps, _drive, _make_state, _plan, _stub_common, _subtask,
    run_dirs,  # noqa: F401  — pytest fixture, imported for use
)


PLANS = [_plan("refactoring",
               _subtask("refactor-004", provides=["provision-readme-fold-in"]),
               _subtask("test-005"),
               _subtask("test-006"))]

# `_schedule()` returns subtasks as a sid→subtask DICT, and that is what
# plan_snapshot persists (verified against a real run's state.json) — the
# rehydrate path feeds it straight to `check_plan_wiring`, which calls
# `.keys()` on it.
SNAPSHOT = {
    "subtasks": {s["id"]: s for p in PLANS for s in p["subtasks"]},
    "waves": [["refactor-004"], ["test-005", "test-006"]],
}


def _seeded(**extra) -> dict:
    """state.json as 3a4abba3 left it: every planning checkpoint present,
    `plan_snapshot` written, `wiring_gate` absent because the gate died."""
    data = {
        "task": "t",
        "categories": ["refactoring"],
        "answers": {"source_of_truth": "codebase"},
        "plan_snapshot": SNAPSHOT,
    }
    for phase in ("classify", "plan", "reconcile", "overlap_judge",
                  "adherence_gate", "coverage_gate", "filters"):
        data[f"plans_after_{phase}"] = PLANS
    data.update(extra)
    return data


def test_resume_after_gate_died_reruns_the_gate(
        leerie, monkeypatch, run_dirs):  # noqa: F811
    """THE REPORTED FAILURE. `plan_snapshot` present, `wiring_gate` absent
    (the gate die()d) — the gate must be re-invoked, not skipped."""
    calls: dict = {}
    _stub_common(leerie, monkeypatch, calls)
    st = _make_state(leerie, run_dirs, _seeded())
    _drive(leerie, _args(), _caps(leerie), run_dirs, st)
    assert calls.get("phase_wiring_gate", 0) == 1, (
        "a resume after a wiring-gate die() must re-run the gate; keying "
        "the skip on plan_snapshot let the run execute a rejected plan")


def test_fresh_run_invokes_the_gate(leerie, monkeypatch, run_dirs):  # noqa: F811
    """Anti-vacuity control: with no `plan_snapshot` the gate runs, so the
    test above is measuring the skip condition rather than a dead stub."""
    calls: dict = {}
    _stub_common(leerie, monkeypatch, calls)
    data = _seeded()
    del data["plan_snapshot"]
    st = _make_state(leerie, run_dirs, data)
    _drive(leerie, _args(), _caps(leerie), run_dirs, st)
    assert calls.get("phase_wiring_gate", 0) == 1


def test_resume_after_gate_passed_skips_the_gate(
        leerie, monkeypatch, run_dirs):  # noqa: F811
    """The case the branch exists for (DESIGN §6 "Budget-check resume"):
    the gate already cleared this plan and persisted its audit, so the
    expensive LLM call must not be repeated."""
    calls: dict = {}
    _stub_common(leerie, monkeypatch, calls)
    st = _make_state(leerie, run_dirs,
                     _seeded(wiring_gate={"wiring_defects": []}))
    _drive(leerie, _args(), _caps(leerie), run_dirs, st)
    assert calls.get("phase_wiring_gate", 0) == 0, (
        "a clean-pass resume must still skip the gate")


def test_gate_call_is_not_nested_in_the_plan_snapshot_branch(leerie):
    """Source-coupling guard. The behavioural tests above stub
    `phase_wiring_gate`, so they cannot see a future refactor that moves
    the call back inside `if "plan_snapshot" not in st.data:` while
    happening to preserve the observed counts. Pin the structure: the
    gate's guard must name `wiring_gate`, and must come after the
    snapshot branch closes."""
    import inspect
    src = inspect.getsource(leerie._run_phases)
    guard = 'if "wiring_gate" not in st.data:'
    assert guard in src, (
        "the wiring gate's resume skip must be keyed on the wiring_gate "
        "audit key, which is written only when the gate passes")
    snap_branch = src.index('if "plan_snapshot" not in st.data:')
    gate_guard = src.index(guard)
    gate_call = src.index("await phase_wiring_gate(")
    assert snap_branch < gate_guard < gate_call, (
        "phase_wiring_gate must be invoked from its own wiring_gate-keyed "
        "guard after the plan_snapshot branch, not from inside it")


def test_die_message_does_not_claim_resume_cannot_bypass_falsely(leerie):
    """The die() text is the operator's only instruction at that moment.
    It claims the gate has no bypass; that must stay true, and the text
    now says so explicitly for `resume`."""
    import inspect
    src = inspect.getsource(leerie.phase_wiring_gate)
    assert "resume` does not bypass it" in src or (
        "`resume` does not bypass" in src), (
        "the die() message must state that resume re-runs the gate, "
        "since the pre-fix behaviour silently bypassed it")


# ===========================================================================
# The deterministic `check_plan_wiring` re-check that runs right after
# `phase_wiring_gate` in `_run_phases` (DESIGN §5 *A wiring re-check on the
# fully-merged plan*). `phase_wiring_gate` is stubbed to a no-op pass here
# (as it is everywhere else in this file), so this exercises the REAL
# `check_plan_wiring` against the real `_schedule()` output — a dangle the
# semantic judge stub cannot have caught. Previously only unit-tested in
# isolation (tests/test_check_plan_wiring.py); this is the first test to
# drive it from inside `_run_phases` itself.
# ===========================================================================

BAD_PLANS = [_plan(
    "refactoring",
    _subtask("refactor-030"),
    _subtask(
        "refactor-031",
        requires=[{"tag": "nothing-provides-this", "extent": "in_plan"}]),
)]


def _seeded_bad(**extra) -> dict:
    data = {
        "task": "t",
        "categories": ["refactoring"],
        "answers": {"source_of_truth": "codebase"},
    }
    for phase in ("classify", "plan", "reconcile", "overlap_judge",
                  "adherence_gate", "coverage_gate", "filters"):
        data[f"plans_after_{phase}"] = BAD_PLANS
    data.update(extra)
    return data


def test_check_plan_wiring_die_branch_fires_in_run_phases(
        leerie, monkeypatch, run_dirs):  # noqa: F811
    """A merged plan that still carries an unresolved `requires` tag after
    scheduling must die() from `_run_phases`' own `check_plan_wiring` call
    — distinct from (and a backstop for) the semantic `wiring_judge` gate,
    which is stubbed here to a clean no-op pass and therefore cannot be
    what is catching this."""
    import asyncio

    from tests.test_resume_planning_reentry import EFFORTS, MODELS

    calls: dict = {}
    _stub_common(leerie, monkeypatch, calls)
    st = _make_state(leerie, run_dirs, _seeded_bad())
    leerie_root, run_id, run_dir = run_dirs

    with pytest.raises(SystemExit):
        asyncio.run(leerie._run_phases(
            _args(), _caps(leerie), run_dir, st, "codebase", "normal",
            MODELS, EFFORTS))

    # Anti-vacuity: the semantic gate stub really did pass cleanly (it was
    # invoked and returned plans unchanged), so the die() above can only be
    # the deterministic check_plan_wiring re-check, not the judge.
    assert calls.get("phase_wiring_gate", 0) == 1
    assert calls.get("phase_execute", 0) == 0, (
        "the die() must fire before phase_execute is ever reached")


# ===========================================================================
# The repairs branch: a `phase_wiring_gate` repair adds a `requires` edge,
# which changes the wave partition, so `_run_phases` must re-derive
# `_schedule()` and rewrite `plan_snapshot` — previously only pinned by
# source-coupling (test_wiring_gate_repair.py::
# test_caller_reschedules_when_repairs_land). This drives the branch for
# real by stubbing `phase_wiring_gate` to perform an actual repair.
# ===========================================================================

GOOD_PLANS = [_plan(
    "refactoring",
    _subtask("refactor-040", provides=["thing"]),
    _subtask("refactor-041"),
)]


def _seeded_repair(**extra) -> dict:
    data = {
        "task": "t",
        "categories": ["refactoring"],
        "answers": {"source_of_truth": "codebase"},
    }
    for phase in ("classify", "plan", "reconcile", "overlap_judge",
                  "adherence_gate", "coverage_gate", "filters"):
        data[f"plans_after_{phase}"] = GOOD_PLANS
    data.update(extra)
    return data


def test_run_phases_reschedules_and_repersists_snapshot_after_a_repair(
        leerie, monkeypatch, run_dirs):  # noqa: F811
    """Behavioural counterpart to
    test_wiring_gate_repair.py::test_caller_reschedules_when_repairs_land,
    which only source-couples the branch. Here `phase_wiring_gate` is
    stubbed to simulate a real repair — adding a `requires` edge — and this
    asserts `_run_phases` actually re-derives `_schedule()` and rewrites
    `plan_snapshot` to the repaired wave partition rather than persisting
    the pre-repair one."""
    calls: dict = {}
    _stub_common(leerie, monkeypatch, calls)

    async def _wiring_gate_with_repair(plans, task, st, caps, models,
                                       efforts):
        calls["phase_wiring_gate"] = calls.get("phase_wiring_gate", 0) + 1
        for p in plans:
            for s in p["subtasks"]:
                if s["id"] == "refactor-041":
                    s["requires"] = [
                        {"tag": "thing", "extent": "in_plan"}]
        st.data["wiring_gate"] = {
            "wiring_defects": [],
            "repairs": [{"sid": "refactor-041", "tag": "thing",
                        "channel": "tag"}],
        }
        return plans
    monkeypatch.setattr(leerie, "phase_wiring_gate", _wiring_gate_with_repair)

    st = _make_state(leerie, run_dirs, _seeded_repair())
    _drive(leerie, _args(), _caps(leerie), run_dirs, st)

    assert calls.get("phase_wiring_gate") == 1
    snap = st.data["plan_snapshot"]
    # The repaired schedule must reflect the added edge: refactor-041 now
    # requires "thing" (provided by refactor-040), so they land in separate
    # waves rather than the single wave a schedule of the pre-repair plan
    # (no edges at all) would produce.
    pos = {sid: i for i, w in enumerate(snap["waves"]) for sid in w}
    assert pos["refactor-040"] < pos["refactor-041"], (
        "plan_snapshot must be rewritten from the REPAIRED schedule, not "
        "the pre-repair one")
