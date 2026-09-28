"""Prompt-content pin for the classifier's standing-deliverable-constraint
rule (IMPLEMENTATION §8 `required_items` row).

Motivation (measured, 2026-09-26): the same task text — carrying an
explicit "the deliverable must never reference X" instruction — had that
instruction extracted into required_items on one run and silently dropped
on the re-run; the re-run's deliverables violated it and the NEXT run's
no_work_judge vetoed its no-work claim over exactly that violation.
prompts/classifier.md gained a never-omitted carve-out for standing
deliverable constraints. This was the one feature of its branch shipped
without a pin (review round 8): the regression class it closes is
literally "the rule present on one run, absent on the next", which is
exactly what a prompt-content pin catches when someone trims the
classifier prompt.

Sibling pattern: test_planner_prompt_documents_the_key in
tests/test_defect_scope_audit.py and tests/test_no_work_dispute_ctx.py.
Substance discipline: assert the rule's LOAD-BEARING clauses (the
never-omitted force, the constraint definition, the verbatim-extraction
instruction), not one incidental phrase a rewrite could keep while
gutting the rule.
"""
from __future__ import annotations


def test_classifier_prompt_carries_the_standing_constraint_rule(leerie):
    text = leerie._load_prompt("classifier")
    # The carve-out's force: this class is never omitted, overriding the
    # section's default when-in-doubt-omit posture.
    assert "NEVER omitted" in text
    # The class definition, by its name and by its shape.
    assert "standing deliverable" in text
    assert "must never contain" in text
    # The extraction instruction the downstream consumers depend on.
    assert "verbatim or near-verbatim" in text
    assert "`source_ref`" in text


def test_rule_lives_inside_the_required_items_section(leerie):
    """The rule must be part of the Required items instructions the
    classifier reads when deciding what to extract — not a stray
    paragraph elsewhere in the prompt."""
    text = leerie._load_prompt("classifier")
    i_section = text.index("## Required items")
    i_rule = text.index("NEVER omitted")
    i_next_section = text.index("## Already satisfied on HEAD")
    assert i_section < i_rule < i_next_section
