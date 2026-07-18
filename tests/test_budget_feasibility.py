"""Tests for check_budget_feasibility() — the planner-output budget
feasibility preflight (DESIGN §13 *Budget feasibility — fail fast at
the cheapest moment*; IMPLEMENTATION.md §"Budget feasibility
preflight" and §"Plan validation" budget row).

The function dies via die() (sys.exit) when the estimate exceeds the
cap; otherwise it returns None.
"""
from __future__ import annotations

import pytest


class _FakeState:
    """Minimal stand-in for State that exposes only what
    check_budget_feasibility() reads: `data.get("worker_count", 0)` and
    `data.get("skip_budget_check")`. Carries a dict, nothing else."""

    def __init__(self, worker_count: int = 0, skip: bool = False):
        self.data: dict = {"worker_count": worker_count}
        if skip:
            self.data["skip_budget_check"] = True


def _make_subtasks(n: int) -> dict:
    """N opaque subtasks. check_budget_feasibility() only reads len()."""
    return {f"feat-{i:03d}": {"id": f"feat-{i:03d}"} for i in range(n)}


def _make_waves(n: int) -> list[list[str]]:
    """N opaque waves. check_budget_feasibility() only reads len()."""
    return [["sid"] for _ in range(n)]


def _default_caps(leerie) -> dict:
    """A caps dict with the production defaults for the keys
    check_budget_feasibility() touches."""
    d = leerie.DEFAULT_CAPS
    return {
        "max_total_workers": d["max_total_workers"],
        "subtask_call_estimate": d["subtask_call_estimate"],
        "budget_safety_margin": d["budget_safety_margin"],
        "conformance_rounds": d["conformance_rounds"],
    }


# ---------------------------------------------------------------------------
# Happy path: small / medium / large plans that fit
# ---------------------------------------------------------------------------

def test_empty_plan_does_not_die(leerie):
    """A no-work plan (zero subtasks, zero waves) is the §8 cleared-but-
    empty terminal state and must not trip the preflight even with a
    tiny cap. The estimate is the 2 fixed (finalize + pr_writer) calls,
    well below any sane cap."""
    st = _FakeState(worker_count=2)
    caps = _default_caps(leerie)
    # No SystemExit raised → pass.
    leerie.check_budget_feasibility(st, caps, _make_subtasks(0), _make_waves(0))


def test_small_plan_fits_default_cap(leerie):
    """Single subtask, single wave. Models the refactor-remove
    1-subtask successful run (worker_count ended at 8, default cap 200)."""
    st = _FakeState(worker_count=3)  # classifier + planner ×2
    caps = _default_caps(leerie)
    leerie.check_budget_feasibility(st, caps, _make_subtasks(1), _make_waves(1))


def test_medium_plan_fits_default_cap(leerie):
    """13 subtasks, 3 waves. Models the feat-please-read-remote
    successful run (worker_count=37, default cap 200). The preflight
    estimate should be conservative but still under the cap."""
    st = _FakeState(worker_count=4)  # classifier + provision + 2 planners
    caps = _default_caps(leerie)
    # Predicted: 4 + (13*2.5 + 3 + 2 + 1) = 4 + 38.5 = 42.5. ×1.15 = 48.875.
    # Under 200.
    leerie.check_budget_feasibility(st, caps, _make_subtasks(13), _make_waves(3))


# ---------------------------------------------------------------------------
# Failure path: the summarizer scenario (63 subtasks, default cap)
# ---------------------------------------------------------------------------

def test_summarizer_scenario_dies(leerie, capsys):
    """63 subtasks, 10 waves, worker_count=8 (the upstream phases on a
    multi-domain run). With default cap 200 and the default 2.5
    multiplier, the estimate exceeds the cap.
    Estimate: 8 + (63*2.5 + 10 + 2 + 1) = 8 + 170.5 = 178.5. ×1.15 = 205.3 > 200.
    Inspired by run `feat-migrate-the-application-code-c65cbe`, 2026-06-03."""
    st = _FakeState(worker_count=8)  # classifier + provision + 4 planners + reconciler + overlap_judge
    caps = _default_caps(leerie)
    with pytest.raises(SystemExit) as exc:
        leerie.check_budget_feasibility(st, caps,
                                        _make_subtasks(63), _make_waves(10))
    # die() exit code is the third positional arg / kw `code=`
    assert exc.value.code == leerie.EXIT_BUDGET_INFEASIBLE
    err = capsys.readouterr().err
    # The message must name the subtask count, wave count, already-spent,
    # and a recommended --max-workers. Coupling-test the wording so a
    # silent rephrasing in leerie.py doesn't break docs/tests.
    assert "63 subtask(s)" in err
    assert "10 wave(s)" in err
    assert "8 `claude -p` call(s) already spent" in err
    assert "--max-workers" in err
    assert "--skip-budget-check" in err


def test_die_message_names_the_per_subtask_upstream_phases(leerie, capsys):
    """The `already spent` attribution must name `fit_judge`/`splitter` and
    `satisfied_probe`, not just the once-per-run phases.

    Both are per-subtask and routinely dominate upstream spend. Measured on a
    real failed run (navegando, 2026-07-18): of 25 upstream calls, fit_judge
    was 10 and satisfied_probe was 10 — the message's original list
    (classifier / planner / reconciler / overlap-judge / provision)
    accounted for only the remaining 5, sending the operator to look in
    entirely the wrong place for their budget.
    """
    st = _FakeState(worker_count=8)
    caps = _default_caps(leerie)
    with pytest.raises(SystemExit):
        leerie.check_budget_feasibility(st, caps,
                                        _make_subtasks(63), _make_waves(10))
    err = capsys.readouterr().err
    assert "satisfied_probe" in err, (
        "die message must name satisfied_probe — it is per-subtask and often "
        "a large share of already-spent calls"
    )
    assert "fit_judge" in err, (
        "die message must name fit_judge/splitter (P1 decomposition) — also "
        "per-subtask upstream spend"
    )


# ---------------------------------------------------------------------------
# Edge: at the boundary, just below the boundary
# ---------------------------------------------------------------------------

def test_just_under_boundary_passes(leerie):
    """An estimate that lands just under the cap (within the safety
    margin) must pass without dying."""
    st = _FakeState(worker_count=0)
    # Construct caps so the estimate is exactly at the boundary.
    # estimate = N*2.5 + waves + conformance_rounds + 1.
    # With conformance_rounds=2, waves=1: N=19 → 19*2.5+1+2+1 = 51.5;
    # 51.5 * 1.15 = 59.225 — just under 60. N=20 would be 54 * 1.15 = 62.1.
    caps = {
        "max_total_workers": 60,
        "subtask_call_estimate": 2.5,
        "budget_safety_margin": 1.15,
        "conformance_rounds": 2,
    }
    leerie.check_budget_feasibility(st, caps, _make_subtasks(19), _make_waves(1))


def test_just_over_boundary_dies(leerie, capsys):
    """An estimate that crosses the cap (after safety multiplier) must
    die. Adjacent to test_just_under_boundary_passes by one subtask."""
    st = _FakeState(worker_count=0)
    caps = {
        "max_total_workers": 60,
        "subtask_call_estimate": 2.5,
        "budget_safety_margin": 1.15,
        "conformance_rounds": 2,
    }
    # 20*2.5+1+2+1 = 54. 54 * 1.15 = 62.1 → > 60 → dies.
    with pytest.raises(SystemExit) as exc:
        leerie.check_budget_feasibility(st, caps,
                                        _make_subtasks(20), _make_waves(1))
    assert exc.value.code == leerie.EXIT_BUDGET_INFEASIBLE


# ---------------------------------------------------------------------------
# Opt-out: --skip-budget-check makes the check a no-op
# ---------------------------------------------------------------------------

def test_skip_budget_check_bypasses(leerie):
    """When `st.data["skip_budget_check"]` is True, the check is a
    no-op even for plans that would otherwise die. The runtime
    backstop (`State.bump_workers()`) remains the load-bearing
    enforcement in that case."""
    st = _FakeState(worker_count=8, skip=True)
    caps = _default_caps(leerie)
    # No SystemExit despite the 63-subtask summarizer scenario.
    leerie.check_budget_feasibility(st, caps,
                                    _make_subtasks(63), _make_waves(10))


# ---------------------------------------------------------------------------
# Recommended-cap calculation: the suggestion should actually work
# ---------------------------------------------------------------------------

def test_recommended_cap_passes_when_applied(leerie, capsys):
    """The `--max-workers <N>` value recommended in the die() message
    should produce a passing preflight when re-applied. This is the
    coupling-test that the user's recovery path actually works:
    `re-run with --max-workers <recommended>` must yield a run that
    clears its own preflight."""
    st = _FakeState(worker_count=8)
    caps = _default_caps(leerie)
    with pytest.raises(SystemExit):
        leerie.check_budget_feasibility(st, caps,
                                        _make_subtasks(63), _make_waves(10))
    err = capsys.readouterr().err
    # Extract the recommended value from the message.
    import re
    m = re.search(r"--max-workers (\d+)", err)
    assert m is not None, f"no --max-workers <N> in error: {err!r}"
    recommended = int(m.group(1))
    # The message includes both the original "vs --max-workers 200" mention
    # and the recommendation. The recommendation should be after "Re-run
    # with". Capture that one specifically.
    m2 = re.search(r"Re-run with --max-workers (\d+)", err)
    assert m2 is not None, f"no 'Re-run with --max-workers <N>' in: {err!r}"
    recommended = int(m2.group(1))
    # Re-apply: the SAME subtasks/waves with the recommended cap should pass.
    new_caps = dict(caps)
    new_caps["max_total_workers"] = recommended
    # Reset worker_count back to 8 to model a fresh run that reaches the
    # same scheduling point.
    st2 = _FakeState(worker_count=8)
    leerie.check_budget_feasibility(st2, new_caps,
                                    _make_subtasks(63), _make_waves(10))


# ---------------------------------------------------------------------------
# Coupling: defaults are real DEFAULT_CAPS entries (not free-floating constants)
# ---------------------------------------------------------------------------

def test_defaults_live_in_default_caps(leerie):
    """Per CLAUDE.md's 'caps are real Python counters in DEFAULT_CAPS'
    rule, the preflight's tunable knobs must live in the canonical dict
    — not as free constants in the function body. Catches a future
    refactor that moves them out."""
    assert "subtask_call_estimate" in leerie.DEFAULT_CAPS
    assert "budget_safety_margin" in leerie.DEFAULT_CAPS
    assert isinstance(leerie.DEFAULT_CAPS["subtask_call_estimate"], (int, float))
    assert isinstance(leerie.DEFAULT_CAPS["budget_safety_margin"], (int, float))
    # Sanity: the margin should be >= 1.0 (anything else would be lowering
    # the cap, not adding safety).
    assert leerie.DEFAULT_CAPS["budget_safety_margin"] >= 1.0


def test_exit_code_constant_exported(leerie):
    """EXIT_BUDGET_INFEASIBLE must be a distinct exit code, not aliased
    to EXIT_NEEDS_ANSWERS or the generic die() code 1. The Fly runtime's
    decide_teardown trap and any external automation key off the
    specific value."""
    assert hasattr(leerie, "EXIT_BUDGET_INFEASIBLE")
    assert leerie.EXIT_BUDGET_INFEASIBLE == 11
    assert leerie.EXIT_BUDGET_INFEASIBLE != leerie.EXIT_NEEDS_ANSWERS
    assert leerie.EXIT_BUDGET_INFEASIBLE != 1


def test_state_field_registered(leerie):
    """`skip_budget_check` must appear in STATE_FIELDS — otherwise
    test_state_fields.py will catch the drift between the runtime
    state-write site and the canonical schema in IMPLEMENTATION.md §8."""
    assert "skip_budget_check" in leerie.STATE_FIELDS
