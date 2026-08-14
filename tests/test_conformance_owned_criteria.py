"""`UNMET_CRITERION` must not fire on a criterion that was never the
implementer's to evaluate.

`prompts/implementer.md` tells the implementer that a criterion naming the build
is a *conformance-phase* signal — the conformer runs the build, and a build
inside a worker's turn budget can OOM the container and get the worker reaped
mid-turn — and to record it rather than re-attempting it.
`check_implementer_output` then turned any `met: false` into `UNMET_CRITERION`
and re-drove the worker. With only `{criterion, met, evidence}` on the schema,
**an obedient implementer could not pass**: measured, `bugfix-008` took 3 drives
($1.96) and `feat-003` took 3 ($3.00), one worker narrating the contradiction
verbatim before being re-driven for it.

Two channels close it, and the second is the load-bearing one: the worker can
say so (`not_applicable`), and the check independently exempts a criterion that
quotes one of the repo's resolved build/lint/test commands. Prompts drift and
workers forget, so the guarantee cannot rest on the worker's cooperation
(DESIGN §12).

See docs/POSTMORTEM-2026-08-14.md, F3.
"""
from __future__ import annotations

import pytest

_BLT = ("pnpm run build", "pnpm run lint", "pnpm test")


def _result(**over):
    base = {
        "status": "complete",
        "criteria_results": [],
        "confidence": {"root_cause": {"score": 9.5, "evidence": "x"},
                       "solution": {"score": 9.5, "evidence": "x"}},
    }
    return {**base, **over}


def _subtask():
    return {"id": "feat-001", "files_likely_touched": []}


def _unmet(leerie, result, blt=_BLT):
    issues = leerie.check_implementer_output(
        result, _subtask(), set(), blt_commands=blt)
    return [i for i in issues if i.startswith("UNMET_CRITERION")]


def test_the_incident_shape_no_longer_re_drives(leerie):
    """The exact report the prompt asks for, with no flag set.

    This is the shape that cost 6 implementer drives across two subtasks.
    """
    res = _result(criteria_results=[{
        "criterion": "`pnpm run build` passes",
        "met": False,
        "evidence": "not run — conformance phase owns this",
    }])
    assert _unmet(leerie, res) == []


def test_the_explicit_flag_also_exempts(leerie):
    """The channel the schema previously lacked, for a paraphrased criterion.

    The command-matching half cannot see a criterion that describes the command
    instead of quoting it, which is why the worker-set flag still matters.
    """
    res = _result(criteria_results=[{
        "criterion": "the production build completes without type errors",
        "met": False,
        "not_applicable": True,
        "evidence": "conformance phase owns this",
    }])
    assert _unmet(leerie, res) == []


def test_a_genuinely_unmet_criterion_still_fires(leerie):
    """Anti-vacuity: the check must not be disabled wholesale."""
    res = _result(criteria_results=[{
        "criterion": "the /volumes endpoint returns 201 on success",
        "met": False,
        "evidence": "not implemented yet",
    }])
    assert len(_unmet(leerie, res)) == 1


def test_a_paraphrase_without_the_flag_still_fires(leerie):
    """The command match is deliberately narrow, and that is a trade-off.

    Exempting anything that merely *sounds* build-related would swallow real
    unmet criteria. A worker that neither quotes the command nor sets the flag
    is still re-driven — which is why the prompt asks for the flag.
    """
    res = _result(criteria_results=[{
        "criterion": "the project builds cleanly",
        "met": False,
        "evidence": "not run — conformance owns it",
    }])
    assert len(_unmet(leerie, res)) == 1


@pytest.mark.parametrize("criterion", [
    "`pnpm run build` passes",
    "pnpm run lint reports no errors",
    "pnpm test is green for the touched files",
])
def test_each_resolved_command_is_exempt(leerie, criterion):
    res = _result(criteria_results=[
        {"criterion": criterion, "met": False, "evidence": "conformance owns"}])
    assert _unmet(leerie, res) == []


def test_no_blt_surface_exempts_nothing(leerie):
    """Degrade to the previous behaviour, not to a permissive one."""
    res = _result(criteria_results=[{
        "criterion": "`pnpm run build` passes", "met": False, "evidence": "x"}])
    assert len(_unmet(leerie, res, blt=())) == 1


def test_met_true_is_never_flagged(leerie):
    res = _result(criteria_results=[{
        "criterion": "`pnpm run build` passes", "met": True, "evidence": "ran"}])
    assert _unmet(leerie, res) == []


class TestCriterionPredicate:
    def test_matches_case_insensitively(self, leerie):
        assert leerie._criterion_is_conformance_owned(
            "`PNPM RUN BUILD` passes", _BLT) is True

    def test_unrelated_criterion_is_not_exempt(self, leerie):
        assert leerie._criterion_is_conformance_owned(
            "the endpoint builds a valid payload", _BLT) is False

    @pytest.mark.parametrize("bad", [None, ""])
    def test_missing_criterion_is_not_exempt(self, leerie, bad):
        assert leerie._criterion_is_conformance_owned(bad, _BLT) is False

    def test_empty_command_set_is_not_exempt(self, leerie):
        assert leerie._criterion_is_conformance_owned(
            "`pnpm run build` passes", ()) is False


def test_schema_carries_the_third_state(leerie):
    props = (leerie.SCHEMAS["implementer"]["properties"]["criteria_results"]
             ["items"]["properties"])
    assert "not_applicable" in props
    assert props["not_applicable"]["type"] == "boolean"


def test_the_prompt_asks_for_the_flag(leerie):
    """The two halves of one contract must not drift apart again."""
    from pathlib import Path
    prompt = (Path(__file__).resolve().parent.parent
              / "prompts" / "implementer.md").read_text()
    assert "not_applicable" in prompt, (
        "the prompt tells the implementer to record a conformance-owned "
        "criterion; it must name the field that makes that reportable")
