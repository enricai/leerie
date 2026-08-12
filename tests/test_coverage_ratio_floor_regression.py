"""Frozen, no-live-LLM replay of the proposed N34 coverage-*ratio* floor
(task.md N34 -- "the coverage gate detected an 18%-coverage plan by name
and could not act").

**This file implements no gate.** task.md is explicit, three times over,
that N34's own root-fix confidence is pinned at 70% and cannot be raised:
the denominator would require prose-parsing (forbidden by CLAUDE.md's
*Language-to-JSON* rule) unless surfaced as a structured field, and no
threshold can be chosen from a single incident. "The deliverable for N34
is the corpus replay and its false-positive count, in the shape of
tests/test_adherence_gate_regression.py. If the replay shows false
positives on healthy plans, the correct outcome is to record that and
implement nothing." That is exactly what this file does: it proves the
number, not a passing gate. No production code in orchestrator/leerie.py
is touched or required by this file.

Mirrors tests/test_adherence_gate_regression.py's shape (a frozen replay
against committed fixtures, no claude_p involved) and
tests/test_check_functions.py's TestAdherenceGateAdvisoryVsGating pattern
(assert the OUTCOME of a predicate, then assert it is not wired into any
gating check function) -- generalized here to a predicate that, per the
work order, must NEVER be wired at all while its false-positive rate is
nonzero.

Denominator provenance: `required_items` (SCHEMAS["classifier"]) is
already the repo's structured, language-to-JSON-compliant field for "the
task's explicit, enumerable requirements" (orchestrator/leerie.py, the
`required_items` schema comment). This file's `_coverage_ratio` treats
`len(required_items)` as the denominator and a fixture-supplied
`covered_source_refs` set as the numerator's provenance -- itself a stand-
in for whatever structured field a real implementation would need a
planner/reconciler to surface (task.md N34's own open question: "the
honest answer may be that the classifier or planner has to surface it").
No prose from any fixture's `task` field is read by the predicate; it
operates purely on `len(required_items)` and set membership against
`covered_source_refs`.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "coverage_ratio_floor"
LEERIE_SRC = Path(__file__).parent.parent / "orchestrator" / "leerie.py"

# The candidate ratio threshold under evaluation. task.md N34 states the
# threshold itself is "Unknown. 4/22 is obvious; 15/22 is not. Needs the
# corpus." -- 0.5 is chosen here only as a representative, moderately
# conservative candidate to replay against the corpus fixtures below; the
# false-positive count measured against it is the deliverable, not the
# choice of 0.5 itself.
CANDIDATE_RATIO_THRESHOLD = 0.5


def _load_json(path: Path) -> dict:
    with open(path) as f:
        return json.load(f)


def _coverage_ratio(required_items: list[dict], covered_source_refs: list[str]):
    """Proposed N34 predicate helper -- NOT part of orchestrator/leerie.py.

    Returns None when there is no enumerable denominator (the common,
    correct case per task.md: required_items empty/absent must never
    drive a ratio floor to fire), otherwise the covered/total ratio.
    Pure structured-field arithmetic; reads no prose.
    """
    total = len(required_items)
    if total == 0:
        return None
    covered_refs = set(covered_source_refs)
    covered = sum(
        1 for item in required_items if item.get("source_ref") in covered_refs
    )
    return covered / total


def _coverage_ratio_floor_would_fire(
    required_items: list[dict],
    covered_source_refs: list[str],
    threshold: float,
) -> bool:
    """Proposed N34 gate predicate -- fires iff there IS an enumerable
    denominator and the plan covers less than `threshold` of it. Mirrors
    the shape of check_prescribed_command_coverage: a pure function over
    structured JSON, returning a boolean rather than an issue list only
    because N34 has no issue-string vocabulary of its own (never wired,
    never surfaced to an operator)."""
    ratio = _coverage_ratio(required_items, covered_source_refs)
    if ratio is None:
        return False
    return ratio < threshold


# ===========================================================================
# 1. The incident fixture: the predicate WOULD have fired on the real
#    ef49d433... 4-of-22 (18%) shape.
# ===========================================================================

def test_incident_fixture_matches_the_reported_shape():
    fixture = _load_json(FIXTURES_DIR / "incident_plan.json")
    assert len(fixture["required_items"]) == fixture["expected_required_count"] == 22
    covered = sum(
        1 for item in fixture["required_items"]
        if item["source_ref"] in set(fixture["covered_source_refs"])
    )
    assert covered == fixture["expected_covered_count"] == 4
    ratio = _coverage_ratio(fixture["required_items"], fixture["covered_source_refs"])
    assert ratio == pytest.approx(fixture["expected_ratio"])
    assert ratio == pytest.approx(4 / 22)
    # task.md's own framing: "an 18%-coverage plan"
    assert round(ratio * 100) == 18


def test_incident_fixture_drives_the_predicate_to_fire():
    fixture = _load_json(FIXTURES_DIR / "incident_plan.json")
    fired = _coverage_ratio_floor_would_fire(
        fixture["required_items"],
        fixture["covered_source_refs"],
        CANDIDATE_RATIO_THRESHOLD,
    )
    assert fired is True, (
        "the proposed coverage-ratio predicate must fire on the real "
        "18%-coverage incident shape at the candidate threshold -- "
        "otherwise the predicate is not even a candidate fix for N34"
    )


# ===========================================================================
# 2. The healthy-plan corpus: the false-positive count IS the deliverable.
#    This is an explicit assertion on the count, not a comment -- and per
#    the work order, a nonzero count means the predicate must never be
#    wired into any gating check function.
# ===========================================================================

def _healthy_plans() -> dict:
    fixture = _load_json(FIXTURES_DIR / "legit_plan.json")
    return fixture["plans"]


def test_healthy_corpus_ratios_match_frozen_expectations():
    """Pins the raw ratio numbers themselves (not just fire/silent), so a
    future edit to the fixture corpus is visible as a diff here -- same
    discipline as test_adherence_gate_regression.py's judge-score freeze."""
    fixture = _load_json(FIXTURES_DIR / "legit_plan.json")
    plans = fixture["plans"]
    expected = fixture["expected_ratios"]
    for name, plan in plans.items():
        ratio = _coverage_ratio(plan["required_items"], plan["covered_source_refs"])
        want = expected[name]
        if want is None:
            assert ratio is None, f"{name}: expected no denominator, got {ratio}"
        else:
            assert ratio == pytest.approx(want), (
                f"{name}: ratio regressed from {want} to {ratio}"
            )


def test_healthy_corpus_false_positive_count_at_candidate_threshold():
    """THE DELIVERABLE. Replays the candidate predicate against every
    healthy-plan fixture at CANDIDATE_RATIO_THRESHOLD and asserts the
    exact false-positive set -- not merely a count, so a fixture edit that
    changes WHICH plan false-positives is caught, not just that the
    number stayed the same."""
    plans = _healthy_plans()
    false_positives = sorted(
        name for name, plan in plans.items()
        if _coverage_ratio_floor_would_fire(
            plan["required_items"], plan["covered_source_refs"],
            CANDIDATE_RATIO_THRESHOLD,
        )
    )
    # THE NUMBER: a naive ratio floor at this threshold false-positives on
    # a legitimate, intentionally-phased partial-rollout plan -- exactly
    # the shape task.md N34 warns cannot be distinguished from
    # under-planning without prose-parsing the task's own scoping
    # language, which the predicate is forbidden from doing.
    assert false_positives == ["phased_rollout"]
    assert len(false_positives) == 1


def test_healthy_corpus_no_false_positives_when_denominator_is_absent():
    """The dominant real-world case (required_items empty/absent) must
    never fire, independent of threshold -- this is what keeps the
    predicate silent on the ~90%+ of ordinary goal-only tasks that carry
    no enumerable checklist at all."""
    plans = _healthy_plans()
    no_items_plan = plans["no_required_items"]
    assert no_items_plan["required_items"] == []
    fired = _coverage_ratio_floor_would_fire(
        no_items_plan["required_items"], no_items_plan["covered_source_refs"],
        CANDIDATE_RATIO_THRESHOLD,
    )
    assert fired is False


def test_healthy_corpus_full_and_high_partial_coverage_do_not_fire():
    plans = _healthy_plans()
    for name in ("full_coverage", "high_partial_coverage"):
        plan = plans[name]
        fired = _coverage_ratio_floor_would_fire(
            plan["required_items"], plan["covered_source_refs"],
            CANDIDATE_RATIO_THRESHOLD,
        )
        assert fired is False, f"{name}: unexpected false positive"


# ===========================================================================
# 3. Because the healthy-corpus false-positive count above is nonzero,
#    the predicate must not be wired into any gating check function --
#    this file lands the measurement and, per the work order, explicitly
#    no code that acts on it.
# ===========================================================================

def test_false_positive_count_is_nonzero_therefore_no_gate_may_be_wired():
    """Structural gate on this file's own conclusion: if the measured
    false-positive count above were ever fixed at zero, this assertion
    would need re-examination before anything could be wired -- as long
    as it is nonzero, wiring the predicate anywhere is explicitly
    forbidden by the work order."""
    plans = _healthy_plans()
    false_positive_count = sum(
        1 for plan in plans.values()
        if _coverage_ratio_floor_would_fire(
            plan["required_items"], plan["covered_source_refs"],
            CANDIDATE_RATIO_THRESHOLD,
        )
    )
    assert false_positive_count > 0
    assert false_positive_count == 1


def test_no_coverage_ratio_gate_landed_in_leerie_py():
    """Source-coupling guard mirroring the wiring-absence half of
    TestAdherenceGateAdvisoryVsGating: no coverage-*ratio*-shaped
    predicate or gating hook exists anywhere in orchestrator/leerie.py."""
    src = LEERIE_SRC.read_text()
    forbidden_markers = (
        "coverage_ratio",
        "COVERAGE_RATIO",
    )
    for marker in forbidden_markers:
        assert marker not in src, (
            f"{marker!r} found in orchestrator/leerie.py -- N34 is "
            f"explicitly 'do not implement' per task.md; this subtask's "
            f"success criteria forbid landing any gating code for it"
        )


def test_coverage_ratio_predicate_is_not_a_check_function():
    """The proposed predicate in THIS file is not, and must not become,
    one of orchestrator/leerie.py's `check_*` gating functions (the
    convention every wired gate in this repo follows -- see
    check_prescribed_command_coverage, check_duplicate_providers, etc.).
    Enumerates the real check_* functions and asserts none of their names
    or docstrings claim a coverage-ratio role."""
    import ast

    tree = ast.parse(LEERIE_SRC.read_text())
    check_fns = [
        node.name for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name.startswith("check_")
    ]
    assert check_fns, "sanity: expected to find check_* functions at all"
    assert not any("ratio" in name for name in check_fns), (
        f"a coverage-ratio-shaped check_* function was found: "
        f"{[n for n in check_fns if 'ratio' in n]} -- N34 forbids landing "
        f"this"
    )
