"""Regression test for the 2026-07-19 planner-feedback freeze (root cause A).

``check_task_file_coverage`` (``orchestrator/leerie.py:5660``) harvested
repo-convention headings from files like CLAUDE.md — coding-standard
imperatives such as ``Run `pnpm run lint:fix` - MUST pass with no errors`` —
as coverage items. Those items combine a backtick-quoted command with
"MUST" and can never appear verbatim in a subtask title/intent, so the
literal-substring gate (``key.lower() not in plan_text``) fired identically
every round: 33/33 feedback rounds froze at an unchanged 15/15 ratio in the
incident run, costing ~$39 across zero-convergence feedback calls.

This test reproduces the exact incident heading shapes against a
plan_text that cannot substring-match them, and asserts the gate no longer
emits a blocking LOW_COVERAGE issue. A control case keeps a genuinely
uncovered *coverable* item in the mix to confirm the fix narrows the gate
rather than disabling it outright.
"""
from __future__ import annotations


# The exact uncoverable-by-construction heading shapes recorded in the
# incident note (docs/incident evidence, not a canonical spec): CLAUDE.md
# H3 headings that combine a backtick-quoted command with "MUST" — these
# can never appear verbatim in a subtask title/intent, which is what made
# them uncoverable by construction rather than merely hard to cover.
INCIDENT_HEADINGS = [
    "CLAUDE.md: Run `pnpm run lint:fix` - MUST pass with no errors",
    "CLAUDE.md: Run `pnpm run build` - MUST succeed",
    "CLAUDE.md: Run `pnpm test <touched>` - MUST pass with no errors",
]

# Non-backtick/MUST headings from the same incident's harvested list.
# The incident note records these as merely hard-to-cover (one, `IMPORTANT:
# Core TypeScript Rules`, was even coincidentally covered in the real run),
# not uncoverable by construction — the fix narrows the gate to exclude
# only the backtick+MUST shape, so these must keep gating normally.
OTHER_INCIDENT_HEADINGS = [
    "CLAUDE.md: IMPORTANT: Core TypeScript Rules",
    "CLAUDE.md: IMPORTANT: Logging Rules",
    "CLAUDE.md: Confirm TypeScript strict mode compliance "
    "(explicit return types)",
]

# A plan_text that intentionally cannot substring-match any of the
# backtick+MUST imperatives above (they are prose commands, not titles a
# planner would ever restate verbatim) — mirrors the 80,339-char
# concatenated plan text from the incident run, where these items stayed
# uncovered across all 112 recovered subtasks.
NON_MATCHING_PLAN_TEXT = (
    "add pagination to the orders list and wire up the export button"
)


def _subtasks_with_intent(intent: str) -> list[dict]:
    return [{"title": "unrelated subtask", "intent": intent,
             "investigation_notes": ""}]


class TestCoverageFreezeRegression:
    def test_uncoverable_incident_headings_do_not_gate(self, leerie):
        # Reproduces the incident shape 1:1: 3 CLAUDE.md-style backtick+MUST
        # headings, none matched by the plan text. Pre-fix this produced a
        # blocking LOW_COVERAGE issue (3/3, the same shape that froze at
        # 15/15 every round in the real incident); post-fix it must not
        # gate.
        subtasks = _subtasks_with_intent(NON_MATCHING_PLAN_TEXT)
        issues = leerie.check_task_file_coverage(
            INCIDENT_HEADINGS, subtasks)
        assert issues == [], (
            "backtick+MUST convention headings must not produce a "
            f"blocking LOW_COVERAGE issue, got: {issues}")

    def test_repeated_calls_never_reproduce_the_frozen_ratio(self, leerie):
        # The incident's defining symptom was that the *same* ratio
        # (15/15) fired on every one of 33 feedback rounds. Simulate
        # several rounds against an unchanged plan_text (the planner
        # cannot move a signal that is uncoverable by construction) and
        # assert no round ever emits a blocking issue — i.e. the freeze
        # cannot recur even across repeated invocations.
        subtasks = _subtasks_with_intent(NON_MATCHING_PLAN_TEXT)
        for _ in range(5):
            issues = leerie.check_task_file_coverage(
                INCIDENT_HEADINGS, subtasks)
            assert issues == []

    def test_genuinely_uncovered_coverable_item_still_gates(self, leerie):
        # Control: mix the uncoverable convention headings with real,
        # coverable spec items that are genuinely missing from the plan.
        # The fix must narrow the gate to coverable items only, not
        # silence it outright — legitimate LOW_COVERAGE signal must
        # still surface.
        extracted = INCIDENT_HEADINGS + [
            "CLAUDE.md: Real spec item that should be covered",
            "CLAUDE.md: Another real spec item, also uncovered",
        ]
        subtasks = _subtasks_with_intent(NON_MATCHING_PLAN_TEXT)
        issues = leerie.check_task_file_coverage(extracted, subtasks)
        assert any("LOW_COVERAGE" in i for i in issues), (
            "a genuinely uncovered coverable item must still surface "
            f"LOW_COVERAGE signal, got: {issues}")

    def test_covered_coverable_item_clears_the_gate(self, leerie):
        # When the real spec items ARE covered by the plan, no issue
        # fires at all — confirms the convention headings aren't quietly
        # forcing a false positive via denominator inflation either.
        extracted = INCIDENT_HEADINGS + [
            "CLAUDE.md: Add pagination to the orders list",
        ]
        subtasks = _subtasks_with_intent(
            NON_MATCHING_PLAN_TEXT + " add pagination to the orders list")
        issues = leerie.check_task_file_coverage(extracted, subtasks)
        assert issues == []

    def test_other_incident_headings_still_gate(self, leerie):
        # Non-backtick/MUST headings harvested in the same incident are
        # merely hard to cover, not uncoverable by construction — the fix
        # must not over-reach and exclude these too, or it would erode
        # the gate's remaining legitimate signal.
        subtasks = _subtasks_with_intent(NON_MATCHING_PLAN_TEXT)
        issues = leerie.check_task_file_coverage(
            OTHER_INCIDENT_HEADINGS, subtasks)
        assert any("LOW_COVERAGE" in i for i in issues), (
            "non-backtick/MUST headings must still be able to gate, "
            f"got: {issues}")
