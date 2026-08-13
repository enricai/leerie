"""`_round_axis_regressions` — the only BLT signal that continues the loop.

DESIGN §9. An axis green before a conformer round and red after it is
attributable to that round with no output parsing and no framework knowledge:
same command, same worktree, different verdict.

Each refusal below has its own anti-vacuity partner, because a function that
returns `[]` unconditionally satisfies every "does not fire" assertion.
"""
from __future__ import annotations

import pytest


def _ax(passed, *, measured=True, command="vitest run", summary=""):
    return {"ran": True, "measured": measured, "passed": passed,
            "command": command, "summary": summary}


def test_green_then_red_fires(leerie):
    out = leerie._round_axis_regressions({"tests": _ax(True)},
                                         {"tests": _ax(False)})
    assert len(out) == 1 and "tests" in out[0]


def test_red_then_red_does_not_fire(leerie):
    """The whole point. Inherited debt is not this round's doing, and
    re-driving the conformer over it is the waste this work removes."""
    assert leerie._round_axis_regressions({"tests": _ax(False)},
                                          {"tests": _ax(False)}) == []


def test_green_then_green_does_not_fire(leerie):
    assert leerie._round_axis_regressions({"tests": _ax(True)},
                                          {"tests": _ax(True)}) == []


def test_red_then_green_does_not_fire(leerie):
    """A fix is not a regression."""
    assert leerie._round_axis_regressions({"tests": _ax(False)},
                                          {"tests": _ax(True)}) == []


@pytest.mark.parametrize("pre,post", [
    ({"tests": _ax(True, measured=False)}, {"tests": _ax(False)}),
    ({"tests": _ax(True)}, {"tests": _ax(False, measured=False)}),
    ({}, {"tests": _ax(False)}),
    ({"tests": _ax(True)}, {}),
])
def test_missing_evidence_never_fires(leerie, pre, post):
    """No evidence is not evidence of green."""
    assert leerie._round_axis_regressions(pre, post) == []


def test_differing_commands_are_never_compared(leerie):
    """This is what stops a scoped `pre` being weighed against a canonical
    `post` — two different questions, and the comparison would manufacture a
    regression out of a scope change."""
    assert leerie._round_axis_regressions(
        {"tests": _ax(True, command="npx vitest related --run a.ts")},
        {"tests": _ax(False, command="vitest run")}) == []


def test_same_command_across_a_scope_label_still_compares(leerie):
    """ANTI-VACUITY PARTNER for the test above: the guard keys on the command
    string, not on any notion of scope, so an unchanged command still
    compares."""
    assert len(leerie._round_axis_regressions(
        {"tests": _ax(True, command="npx vitest related --run a.ts")},
        {"tests": _ax(False, command="npx vitest related --run a.ts")})) == 1


def test_each_axis_is_evaluated_independently(leerie):
    out = leerie._round_axis_regressions(
        {"build": _ax(True, command="tsc"), "tests": _ax(False)},
        {"build": _ax(False, command="tsc"), "tests": _ax(False)})
    assert len(out) == 1 and out[0].startswith("build")


def test_the_message_carries_the_command_and_output(leerie):
    out = leerie._round_axis_regressions(
        {"tests": _ax(True)},
        {"tests": _ax(False, summary="FAIL src/a.test.ts")})
    assert "vitest run" in out[0] and "FAIL src/a.test.ts" in out[0]


def test_a_regression_continues_the_loop(leerie):
    """Source-coupling: the loop must not break on a clean
    `_conformance_clean` while a regression this round introduced stands."""
    import inspect
    for fn in (leerie._run_conformance_phase, leerie._run_final_conformance):
        src = inspect.getsource(fn)
        assert "and not regressions" in src, (
            f"{fn.__name__} can exit its round loop with an unaddressed "
            "regression it just introduced")
