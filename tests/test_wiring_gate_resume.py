"""`--resume` must not bypass the phase-3 semantic wiring gate.

Regression pins for run 3a4abba3 (2026-08-01). `phase_wiring_gate` is a
detect-and-die gate: on a concrete `wiring_defects` entry it `die()`s with
the defect named. Its skip-on-resume used to be keyed on the presence of
`plan_snapshot` — but `plan_snapshot` is written a few lines *earlier*, by
design, so that a die() at either terminal gate does not discard the whole
planning spend. That made the snapshot present even when the gate had
FAILED, so the next `--resume` skipped the entire `plan_snapshot` branch,
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

# `schedule()` returns subtasks as a sid→subtask DICT, and that is what
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
    now says so explicitly for `--resume`."""
    import inspect
    src = inspect.getsource(leerie.phase_wiring_gate)
    assert "--resume` does not bypass it" in src or (
        "`--resume` does not bypass" in src), (
        "the die() message must state that --resume re-runs the gate, "
        "since the pre-fix behaviour silently bypassed it")
