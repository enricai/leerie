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

The schema gained a third state, `not_applicable`, and the check honours it.

**One channel, deliberately.** A second briefly existed: substring-matching the
repo's resolved build/lint/test commands inside the criterion text, as a
backstop for a worker that forgot the flag. It was deleted. `criterion` is
planner-authored prose, and CLAUDE.md's *Language-to-JSON* rule forbids Python
reading meaning out of prose — prescribing instead that "the owning worker must
surface it as a JSON field", which is what `not_applicable` is. The residual
risk is one re-drive when a worker omits the flag: the behaviour before any of
this existed, so not a regression.

See docs/POSTMORTEM-2026-08-14.md, F3.
"""
from __future__ import annotations

import ast
import inspect
import io
import tokenize
from pathlib import Path

import pytest
from tests.source_strip import code_only as _code_only   # single owner; see that module

REPO_ROOT = Path(__file__).resolve().parent.parent



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


def _unmet(leerie, result):
    issues = leerie.check_implementer_output(result, _subtask(), set())
    return [i for i in issues if i.startswith("UNMET_CRITERION")]


def test_the_declared_flag_exempts(leerie):
    """The incident shape, reported the way the prompt now asks for it."""
    assert not _unmet(leerie, _result(criteria_results=[{
        "criterion": "`pnpm run build` passes",
        "met": False,
        "not_applicable": True,
        "evidence": "not run — conformance phase owns this",
    }]))


def test_a_genuinely_unmet_criterion_still_fires(leerie):
    """Anti-vacuity: the exemption must not swallow real failures."""
    assert _unmet(leerie, _result(criteria_results=[{
        "criterion": "the endpoint returns 201 on success",
        "met": False,
        "evidence": "not implemented",
    }]))


def test_a_build_criterion_without_the_flag_still_fires(leerie):
    """The deliberate cost of deleting the prose fallback, pinned rather than
    left as a surprise.

    A worker that names the build but omits `not_applicable` IS re-driven. That
    was the behaviour before this work, the flag is what fixes it, and the
    alternative — inferring the exemption from the criterion's wording — is a
    *Language-to-JSON* violation that no amount of convenience justifies.
    """
    assert _unmet(leerie, _result(criteria_results=[{
        "criterion": "`pnpm run build` passes",
        "met": False,
        "evidence": "not run — conformance phase owns this",
    }]))


def test_met_true_is_never_flagged(leerie):
    assert not _unmet(leerie, _result(criteria_results=[{
        "criterion": "`pnpm run build` passes", "met": True, "evidence": "ran"}]))


@pytest.mark.parametrize("flag", [False, None, "yes", 1])
def test_only_a_real_true_exempts(leerie, flag):
    """`is True`, not truthiness: a worker returning the string "yes" or a
    stray `1` has not made the declaration the schema asks for."""
    cr = {"criterion": "x", "met": False, "evidence": "e"}
    if flag is not None:
        cr["not_applicable"] = flag
    assert _unmet(leerie, _result(criteria_results=[cr]))


class TestTheProseFallbackIsGone:
    """Absence pins. A deleted inference that quietly returns is worse than one
    that was never written, because the docs now say it does not exist."""

    def test_the_predicate_no_longer_exists(self, leerie):
        assert not hasattr(leerie, "_criterion_is_conformance_owned")

    def test_the_check_takes_no_command_list(self, leerie):
        params = inspect.signature(leerie.check_implementer_output).parameters
        assert "blt_commands" not in params, (
            "the parameter fed the deleted prose match; leaving it accepts an "
            "argument nothing reads")

    def test_no_caller_still_passes_one(self, leerie):
        # Comments AND docstrings stripped via `tokenize`/`ast`, not a
        # `#`-prefix line heuristic: the natural place to record why the
        # parameter went is a comment or a docstring, and the heuristic made
        # this pass or fail depending on whether that comment sat on its own
        # line or at the end of one.
        src = (REPO_ROOT / "orchestrator" / "leerie.py").read_text()
        assert "blt_commands" not in _code_only(src)


def test_schema_carries_the_third_state(leerie):
    props = (leerie.SCHEMAS["implementer"]["properties"]["criteria_results"]
             ["items"]["properties"])
    assert props["not_applicable"]["type"] == "boolean"


def test_the_prompt_asks_for_the_flag(leerie):
    """The advisory half of the §12 split: code honours the field, the prompt
    is what gets it filled in. With the fallback gone this is the ONLY way a
    build criterion is exempted, so a prompt that stops asking silently
    reinstates the re-drive loop."""
    src = (REPO_ROOT / "prompts" / "implementer.md").read_text()
    assert "not_applicable" in src
