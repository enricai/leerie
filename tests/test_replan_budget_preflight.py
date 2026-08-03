"""`check_replan_affordable` — budget preflight before a re-plan (DESIGN §13).

`check_budget_feasibility` runs once, after `schedule()`. But a re-plan is the
single largest budget event in a run and was authorised with no budget check
at all — and it is far more expensive than "re-running the planners", because
`phase_plan` also re-runs the entire P1 decomposition behind it. Measured on
run `d8a764f3…`: `fit_judge` was 118 of 201 spawns (59%) against the planners'
62 (31%), and the adherence-gate re-plan cost ~125 of 201 — almost twice the
entire first planning pass. The run then died of budget exhaustion
mid-decomposition, having written no code.

Dying at the gate instead costs nothing and names the real cause.
"""
from __future__ import annotations

import pytest


class _FakeState:
    def __init__(self, **data):
        self.data = dict(data)


def _caps(max_workers=200, samples=3):
    return {"max_total_workers": max_workers, "planner_samples": samples}


def _snapshot(n):
    return {"subtasks": {f"feat-{i:03d}": {} for i in range(n)}}


def test_affordable_replan_is_silent(leerie):
    st = _FakeState(worker_count=20, categories=["feature-implementation"],
                    plan_snapshot=_snapshot(10))
    leerie.check_replan_affordable(st, _caps(), "adherence gate")


def test_the_measured_incident_shape_dies(leerie):
    """`d8a764f3…` at the adherence gate: ~76 spent of 200, 3 domains, 35
    subtasks already decomposed. The re-plan that followed cost ~125 and
    exhausted the budget."""
    st = _FakeState(
        worker_count=160,
        categories=["bug-fixing", "feature-implementation", "testing"],
        plan_snapshot=_snapshot(35))
    with pytest.raises(SystemExit) as exc:
        leerie.check_replan_affordable(st, _caps(), "adherence gate")
    assert exc.value.code == leerie.EXIT_BUDGET_INFEASIBLE


def test_die_message_is_actionable(leerie):
    """An operator reading this must learn WHICH gate, what it would cost,
    and the exact flag value that would let it proceed."""
    st = _FakeState(worker_count=190,
                    categories=["bug-fixing", "testing"],
                    plan_snapshot=_snapshot(40))
    with pytest.raises(SystemExit):
        leerie.check_replan_affordable(st, _caps(), "coverage gate")


def test_names_the_gate_that_triggered_it(leerie, capsys):
    st = _FakeState(worker_count=195, categories=["testing"],
                    plan_snapshot=_snapshot(50))
    with pytest.raises(SystemExit):
        leerie.check_replan_affordable(st, _caps(), "coverage gate")
    err = capsys.readouterr().err
    assert "coverage gate" in err
    assert "--max-workers" in err
    assert "--skip-budget-check" in err
    assert "fit_judge" in err, (
        "the message must name decomposition as the dominant cost, or the "
        "operator will assume re-planning is just re-running the planners")


def test_skip_budget_check_opts_out(leerie):
    """Same opt-out as `check_budget_feasibility`, so one flag governs both."""
    st = _FakeState(worker_count=199, categories=["a", "b", "c"],
                    plan_snapshot=_snapshot(100), skip_budget_check=True)
    leerie.check_replan_affordable(st, _caps(), "adherence gate")


def test_decomposition_cost_dominates_the_estimate(leerie):
    """The whole point: subtask count, not domain count, drives the estimate.
    A small-domain/large-plan run must still be refused."""
    small_plan = _FakeState(worker_count=150, categories=["a"],
                            plan_snapshot=_snapshot(5))
    leerie.check_replan_affordable(small_plan, _caps(), "gate")  # affordable
    big_plan = _FakeState(worker_count=150, categories=["a"],
                          plan_snapshot=_snapshot(60))
    with pytest.raises(SystemExit):
        leerie.check_replan_affordable(big_plan, _caps(), "gate")


def test_missing_snapshot_degrades_to_planner_cost_only(leerie):
    """An early gate can fire before `plan_snapshot` exists. The check must
    still work rather than raising, and must not fabricate subtasks."""
    st = _FakeState(worker_count=10, categories=["a", "b"])
    leerie.check_replan_affordable(st, _caps(), "gate")


def test_missing_categories_does_not_divide_by_zero(leerie):
    st = _FakeState(worker_count=10, plan_snapshot=_snapshot(1))
    leerie.check_replan_affordable(st, _caps(), "gate")


def test_exhausted_budget_always_dies(leerie):
    """At or past the cap there is nothing left for any re-plan."""
    st = _FakeState(worker_count=200, categories=["a"],
                    plan_snapshot=_snapshot(1))
    with pytest.raises(SystemExit):
        leerie.check_replan_affordable(st, _caps(), "gate")


def test_raising_max_workers_makes_it_affordable(leerie):
    """The remedy the die message recommends must actually work."""
    kw = dict(worker_count=160,
              categories=["bug-fixing", "feature-implementation", "testing"],
              plan_snapshot=_snapshot(35))
    with pytest.raises(SystemExit):
        leerie.check_replan_affordable(_FakeState(**kw), _caps(200), "gate")
    leerie.check_replan_affordable(_FakeState(**kw), _caps(400), "gate")


# ----- wiring ---------------------------------------------------------------

@pytest.mark.parametrize("phase,gate", [
    ("phase_adherence_gate", "adherence gate"),
    ("phase_planning_coverage_gate", "coverage gate"),
])
def test_both_replan_paths_preflight_before_spending(leerie, phase, gate):
    """Source-coupling guard. The check is inert unless it runs BEFORE
    `phase_plan`, and both gates re-plan."""
    import inspect
    src = inspect.getsource(getattr(leerie, phase))
    assert "check_replan_affordable(" in src, f"{phase} does not preflight"
    assert f'"{gate}"' in src
    assert src.index("check_replan_affordable(") < src.index(
        "await phase_plan("), (
        f"{phase} preflights AFTER re-planning, which spends the budget it "
        "was supposed to protect")


def test_estimate_constant_matches_the_measurement(leerie):
    """Pinned so a future edit cannot quietly make the preflight toothless.
    Measured: the re-plan issued 80 fit_judge + 5 splitter calls against a
    subtask set of 35→65, i.e. ~1.3/subtask, rounded up to err toward
    refusing a marginal re-plan."""
    assert leerie._REPLAN_DECOMPOSE_CALLS_PER_SUBTASK >= 1.3
