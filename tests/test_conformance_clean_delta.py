"""`_conformance_clean` judges against the base tree, not in absolute terms.

DESIGN §9 *The signal that continues the loop is a delta, not a verdict*.

An absolute verdict makes the conformer round loop unsatisfiable on any repo
whose base tree is not already green, and then every subtask spends its whole
`conformance_rounds` budget re-running an expensive suite to rediscover debt it
did not create. Measured on a 91-subtask run with a RED base: only 6 of 79
subtasks were clean at round 1, and 57 ran exactly the 3-round cap.

The pre-existing failure reaches the predicate through two channels — the axis
channel and the residual channel — and this file pins both, each paired with the
control that keeps the exclusion from swallowing a real regression. Those
partners are the point: an implementation that simply never returns False passes
every "does not block" assertion here and is caught only by them.
"""
from __future__ import annotations

import pytest


def _axis(ran=True, passed=True):
    return {"ran": ran, "passed": passed, "measured": True,
            "command": "pnpm run test", "summary": ""}


def _res(rule="build/lint/tests must pass", axis=None, why="pre-existing"):
    row = {"rule": rule, "why_not_fixed": why}
    if axis is not None:
        row["axis"] = axis
    return row


def _clean_result(**over):
    """A conformer result with everything green and no residuals."""
    out = {"rule_violations_residual": [],
           "build": _axis(), "lint": _axis(), "tests": _axis()}
    out.update(over)
    return out


BASE_RED_TESTS = {"axes": {}, "red_axes": ["tests"]}
BASE_GREEN = {"axes": {}, "red_axes": []}


# --------------------------------------------------------------------------
# The axis channel
# --------------------------------------------------------------------------

def test_axis_red_at_baseline_does_not_block(leerie):
    """The reported failure is the base tree's, not this subtask's."""
    res = _clean_result(tests=_axis(ran=True, passed=False))
    assert leerie._conformance_clean(res, BASE_RED_TESTS) is True


def test_axis_green_at_baseline_still_blocks(leerie):
    """ANTI-VACUITY PARTNER for the test above.

    `build` was green on the base tree, so a red `build` now is a regression
    this run introduced and must still drive another round. Without this,
    `test_axis_red_at_baseline_does_not_block` passes against an
    implementation that returns True unconditionally.
    """
    res = _clean_result(build=_axis(ran=True, passed=False))
    assert leerie._conformance_clean(res, BASE_RED_TESTS) is False


def test_axis_that_did_not_run_never_blocks(leerie):
    """`ran: False` is 'not applicable', unchanged from before."""
    res = _clean_result(lint=_axis(ran=False, passed=False))
    assert leerie._conformance_clean(res, BASE_GREEN) is True


# --------------------------------------------------------------------------
# The residual channel
# --------------------------------------------------------------------------

def test_blt_residual_on_a_baseline_red_axis_does_not_block(leerie):
    """Measured, this is the single largest contributor: 125 of 139 residuals
    on the motivating run restated the same baseline-red suite, citing the
    orchestrator's own BASELINE block as the reason they were not fixed."""
    res = _clean_result(rule_violations_residual=[_res(axis="tests")])
    assert leerie._conformance_clean(res, BASE_RED_TESTS) is True


def test_blt_residual_on_a_baseline_green_axis_still_blocks(leerie):
    """ANTI-VACUITY PARTNER. `build` was green at baseline, so a build
    residual is a real finding regardless of its rule text."""
    res = _clean_result(rule_violations_residual=[_res(axis="build")])
    assert leerie._conformance_clean(res, BASE_RED_TESTS) is False


def test_non_blt_residual_still_blocks(leerie):
    """A residual about a naming convention carries no `axis` and is
    untouched by this change — the exclusion is scoped to BLT, not to
    residuals in general."""
    res = _clean_result(rule_violations_residual=[
        _res(rule="MUST use date-fns for date manipulation", axis=None)])
    assert leerie._conformance_clean(res, BASE_RED_TESTS) is False


def test_unlabelled_residual_blocks(leerie):
    """`axis` is optional in the schema and gating on absence, and that is ONE
    decision (see the schema comment). A worker that omits the label costs a
    round rather than silently disabling the loop."""
    res = _clean_result(rule_violations_residual=[_res(axis=None)])
    assert leerie._conformance_clean(res, BASE_RED_TESTS) is False


def test_a_new_residual_alongside_an_excused_one_still_blocks(leerie):
    """Exclusion is per-entry, not per-set: one excusable residual must not
    launder a second, genuine one sharing the list."""
    res = _clean_result(rule_violations_residual=[
        _res(axis="tests"),
        _res(rule="README must document new flags", axis=None)])
    assert leerie._conformance_clean(res, BASE_RED_TESTS) is False


def test_malformed_residual_entry_blocks(leerie):
    """A non-dict entry is not evidence of cleanliness."""
    res = _clean_result(rule_violations_residual=["oops"])
    assert leerie._conformance_clean(res, BASE_RED_TESTS) is False


# --------------------------------------------------------------------------
# Degradation: no baseline must reproduce the old behaviour exactly
# --------------------------------------------------------------------------

@pytest.mark.parametrize("baseline", [None, {}, {"red_axes": None},
                                      {"red_axes": []},
                                      {"red_axes": ["nonsense-axis"]}])
def test_absent_or_empty_baseline_reproduces_absolute_verdict(leerie, baseline):
    """`--skip-base-baseline`, a not-yet-captured baseline, and a malformed
    one all degrade to the pre-change behaviour rather than to a permissive
    one. A red axis blocks; a residual blocks."""
    assert leerie._conformance_clean(
        _clean_result(tests=_axis(ran=True, passed=False)), baseline) is False
    assert leerie._conformance_clean(
        _clean_result(rule_violations_residual=[_res(axis="tests")]),
        baseline) is False


def test_default_argument_is_the_absolute_verdict(leerie):
    """Callers that do not pass a baseline get the old semantics. Guards the
    three pre-existing direct-call sites in the suite from a silent behaviour
    change, the same compatibility concern `_DescendantTracker`'s new-parameter
    guard documents."""
    assert leerie._conformance_clean(
        _clean_result(tests=_axis(ran=True, passed=False))) is False


def test_fully_clean_result_is_clean_under_every_baseline(leerie):
    """Control: the exclusions did not break the ordinary happy path."""
    for b in (None, BASE_GREEN, BASE_RED_TESTS):
        assert leerie._conformance_clean(_clean_result(), b) is True


# --------------------------------------------------------------------------
# `_baseline_red_axes`
# --------------------------------------------------------------------------

def test_red_axes_only_admits_known_axis_names(leerie):
    """A junk entry cannot widen the exclusion to an axis leerie does not
    model."""
    assert leerie._baseline_red_axes(
        {"red_axes": ["tests", "wat", 7, None]}) == {"tests"}


@pytest.mark.parametrize("bad", [None, {}, [], "tests", {"red_axes": "tests"}])
def test_red_axes_degrades_to_empty(leerie, bad):
    assert leerie._baseline_red_axes(bad) == set()


# --------------------------------------------------------------------------
# The `axis` field must survive the wire-shape expansion
# --------------------------------------------------------------------------

def test_expand_carries_axis_through(leerie):
    """`_expand_conformer_output` rebuilds each residual by key, so a field it
    does not copy is dropped no matter what the schema declares — which is how
    a declared-but-dead field ships. Without this the whole predicate above is
    inert on real worker output."""
    out = leerie._expand_conformer_output({"rule_violations": [
        {"status": "residual", "rule": "build/lint/tests must pass",
         "axis": "tests", "why_not_fixed": "pre-existing"}]})
    assert out["rule_violations_residual"][0].get("axis") == "tests"


def test_expand_drops_an_invalid_axis(leerie):
    """Normalising at the expansion keeps `_conformance_clean` a plain set
    test. An unrecognised value becomes absent, which blocks."""
    out = leerie._expand_conformer_output({"rule_violations": [
        {"status": "residual", "rule": "r", "axis": "typecheck"}]})
    assert "axis" not in out["rule_violations_residual"][0]
    assert leerie._conformance_clean(
        {"rule_violations_residual": out["rule_violations_residual"]},
        BASE_RED_TESTS) is False


def test_expand_normalises_case_and_whitespace(leerie):
    out = leerie._expand_conformer_output({"rule_violations": [
        {"status": "residual", "rule": "r", "axis": "  Tests "}]})
    assert out["rule_violations_residual"][0].get("axis") == "tests"


def test_axis_is_optional_on_the_schema(leerie):
    """Optional in the schema, gating in the check. Requiring it would cost
    the whole submission rather than the one field."""
    item = (leerie.SCHEMAS["conformer"]["properties"]
            ["rule_violations"]["items"])
    assert "axis" not in item["required"]
    assert item["properties"]["axis"]["enum"] == ["build", "lint", "tests"]


def test_prompt_asks_the_worker_to_fill_axis(leerie):
    """The field is inert unless the prompt asks for it — the §12 advisory
    half of the split. Structural only: whether the worker complies is not
    something a unit test can assert."""
    from pathlib import Path
    txt = (Path(leerie.__file__).resolve().parent.parent
           / "prompts" / "conformer.md").read_text()
    assert '`axis`' in txt
    assert '"axis": "tests"' in txt
