"""`check_replan_affordable` — budget preflight before a re-plan (DESIGN §13).

`check_budget_feasibility` runs once, after `_schedule()`. But a re-plan is the
single largest budget event in a run and was authorised with no budget check
at all — and it is far more expensive than "re-running the planners", because
`phase_plan` also re-runs the entire P1 decomposition behind it. Measured on
run `d8a764f3…`: `fit_judge` was 118 of 201 spawns (59%) against the planners'
62 (31%), and the adherence-gate re-plan cost ~125 of 201. The run then died
of budget exhaustion mid-decomposition, having written no code.

**Why these tests pass the plans in explicitly.** The first version of this
function sized the re-plan from `st.data["plan_snapshot"]`, which `_run_phases`
does not write until *after* `_schedule()` — while both re-planning gates run
*before* it. The check was therefore inert at both call sites: replaying
`d8a764f3`'s real state (160 of 200 spent, 3 domains, no snapshot) produced no
die at all. Its original tests passed only because they seeded a
`plan_snapshot` that cannot exist there.

So every test below constructs the state the gate really has — **no
`plan_snapshot`** — and supplies the subtask count the way production does, via
the caller's live plans. `test_inert_when_sized_from_state_instead_of_plans` is
the regression lock on that specific mistake.
"""
from __future__ import annotations

import pytest


class _FakeState:
    def __init__(self, **data):
        self.data = dict(data)


def _caps(max_workers=200, samples=3, decompose=1.5):
    return {"max_total_workers": max_workers, "planner_samples": samples,
            "replan_decompose_estimate": decompose}


def _plans(n_subtasks, n_plans=1):
    """Plans shaped the way a gate's `cur_plans[0]` holds them."""
    per, rem = divmod(n_subtasks, n_plans)
    out = []
    for i in range(n_plans):
        k = per + (1 if i < rem else 0)
        out.append({"domain": f"d{i}",
                    "subtasks": [{"id": f"d{i}-{j:03d}"} for j in range(k)]})
    return out


# ----- the reported defect --------------------------------------------------

def test_the_measured_incident_dies_at_its_real_call_site(leerie):
    """`d8a764f3` at the adherence gate: 160 of 200 spent, 3 domains, 35
    subtasks already decomposed, and — as in production — NO `plan_snapshot`.

    This is the case the shipped version silently allowed through.
    """
    st = _FakeState(
        worker_count=160,
        categories=["bug-fixing", "feature-implementation", "testing"])
    assert "plan_snapshot" not in st.data
    with pytest.raises(SystemExit) as exc:
        leerie.check_replan_affordable(
            st, _caps(), "adherence gate", _plans(35, 3))
    assert exc.value.code == leerie.EXIT_BUDGET_INFEASIBLE


def test_inert_when_sized_from_state_instead_of_plans(leerie):
    """Anti-vacuity lock on the exact defect. Reproduces the old sizing —
    reading `plan_snapshot` from state — against the real call-site state, and
    asserts it yields NO die. If someone reintroduces state-sourced sizing,
    the test above starts failing and this one explains why."""
    st = _FakeState(
        worker_count=160,
        categories=["bug-fixing", "feature-implementation", "testing"])
    caps = _caps()
    snapshot = st.data.get("plan_snapshot") or {}
    n_subtasks = len(snapshot.get("subtasks") or {})
    assert n_subtasks == 0, "no snapshot exists at this call site"
    old_estimate = (len(st.data["categories"]) * caps["planner_samples"]
                    + n_subtasks * caps["replan_decompose_estimate"])
    remaining = caps["max_total_workers"] - st.data["worker_count"]
    assert old_estimate <= remaining, (
        "the old state-sourced sizing must be shown to pass here — that is "
        "precisely why the shipped check never fired")


def test_recommendation_is_a_valid_max_workers_value(leerie, capsys):
    """The estimate is fractional by construction (n_subtasks × 1.5) and
    `--max-workers` is `type=_positive_int`, so an uncast recommendation
    emitted e.g. `--max-workers 241.5` — advice the CLI rejects."""
    st = _FakeState(worker_count=160, categories=["a", "b", "c"])
    with pytest.raises(SystemExit):
        leerie.check_replan_affordable(st, _caps(), "adherence gate",
                                       _plans(35, 3))
    err = capsys.readouterr().err
    import re
    # Capture the bare token, not trailing prose punctuation.
    mo = re.search(r"--max-workers ([^\s,.]+)", err)
    assert mo, "no --max-workers recommendation in the die message"
    leerie._positive_int(mo.group(1))  # raises if not a positive int
    assert "." not in mo.group(1), (
        f"recommendation {mo.group(1)!r} is fractional; --max-workers is "
        "type=_positive_int and would reject it")


# ----- estimate behaviour ---------------------------------------------------

def test_affordable_replan_is_silent(leerie):
    st = _FakeState(worker_count=20, categories=["feature-implementation"])
    leerie.check_replan_affordable(st, _caps(), "adherence gate", _plans(10))


def test_decomposition_cost_dominates_the_estimate(leerie):
    """Subtask count, not domain count, drives the estimate — a
    small-domain/large-plan run must still be refused."""
    st = _FakeState(worker_count=150, categories=["a"])
    leerie.check_replan_affordable(st, _caps(), "gate", _plans(5))
    with pytest.raises(SystemExit):
        leerie.check_replan_affordable(st, _caps(), "gate", _plans(60))


def test_subtasks_counted_across_every_plan(leerie):
    """`cur_plans[0]` is a list of per-domain plans; the count must span all
    of them, not just the first."""
    st = _FakeState(worker_count=150, categories=["a", "b", "c"])
    with pytest.raises(SystemExit):
        leerie.check_replan_affordable(st, _caps(), "gate", _plans(60, 3))


def test_empty_plans_do_not_raise(leerie):
    st = _FakeState(worker_count=10, categories=["a"])
    leerie.check_replan_affordable(st, _caps(), "gate", [])
    leerie.check_replan_affordable(st, _caps(), "gate", [{"subtasks": []}])


def test_missing_categories_does_not_divide_by_zero(leerie):
    st = _FakeState(worker_count=10)
    leerie.check_replan_affordable(st, _caps(), "gate", _plans(1))


def test_exhausted_budget_always_dies(leerie):
    st = _FakeState(worker_count=200, categories=["a"])
    with pytest.raises(SystemExit):
        leerie.check_replan_affordable(st, _caps(), "gate", _plans(1))


def test_raising_max_workers_makes_it_affordable(leerie):
    """The remedy the die message recommends must actually work."""
    st = _FakeState(worker_count=160, categories=["a", "b", "c"])
    with pytest.raises(SystemExit):
        leerie.check_replan_affordable(st, _caps(200), "gate", _plans(35, 3))
    leerie.check_replan_affordable(st, _caps(400), "gate", _plans(35, 3))


def test_skip_budget_check_opts_out(leerie):
    """Same opt-out as `check_budget_feasibility`, so one flag governs both."""
    st = _FakeState(worker_count=199, categories=["a", "b", "c"],
                    skip_budget_check=True)
    leerie.check_replan_affordable(st, _caps(), "adherence gate",
                                   _plans(100, 3))


def test_die_message_names_the_gate_and_the_real_cost_driver(leerie, capsys):
    st = _FakeState(worker_count=195, categories=["testing"])
    with pytest.raises(SystemExit):
        leerie.check_replan_affordable(st, _caps(), "coverage gate",
                                       _plans(50))
    err = capsys.readouterr().err
    assert "coverage gate" in err
    assert "--skip-budget-check" in err
    assert "fit_judge" in err, (
        "the message must name decomposition as the dominant cost, or the "
        "operator will assume re-planning is just re-running the planners")


# ----- wiring ---------------------------------------------------------------

# `phase_planning_coverage_gate` was the second entry here until it became
# advisory (PR #166): it no longer re-plans, so it has no re-plan to preflight.
# The parametrize is kept rather than inlined because a THIRD re-planning gate
# would belong here, and the shape should make that obvious.
@pytest.mark.parametrize("phase,gate", [
    ("phase_adherence_gate", "adherence gate"),
])
def test_both_replan_paths_preflight_before_spending(leerie, phase, gate):
    """The check is inert unless it runs BEFORE `phase_plan`, and it must be
    handed the live plans — not left to source them from state."""
    import inspect
    src = inspect.getsource(getattr(leerie, phase))
    assert "check_replan_affordable(" in src, f"{phase} does not preflight"
    assert f'"{gate}"' in src
    call = src[src.index("check_replan_affordable("):]
    assert "cur_plans[0]" in call[:call.index(")") + 1], (
        f"{phase} must pass the live plans; sizing from state is the defect "
        "that made this check inert")
    assert src.index("check_replan_affordable(") < src.index(
        "await phase_plan("), (
        f"{phase} preflights AFTER re-planning, which spends the budget it "
        "was supposed to protect")


def test_function_takes_plans_and_does_not_read_plan_snapshot(leerie):
    """Structural guard: the fix is re-sourcing the input, so the signature
    and the body both have to reflect it."""
    import inspect
    assert "plans" in inspect.signature(leerie.check_replan_affordable).parameters
    src = inspect.getsource(leerie.check_replan_affordable)
    body = src[src.index('"""', src.index('"""') + 3):]
    assert "plan_snapshot" not in body, (
        "sizing from plan_snapshot is what made this check inert")


def test_estimate_cap_matches_the_measurement(leerie):
    """Pinned so a future edit cannot quietly make the preflight toothless.
    Measured: the re-plan issued 80 fit_judge + 5 splitter calls against a
    subtask set of 35→65, i.e. ~1.3/subtask, rounded up to err toward
    refusing a marginal re-plan."""
    assert leerie.DEFAULT_CAPS["replan_decompose_estimate"] >= 1.3
