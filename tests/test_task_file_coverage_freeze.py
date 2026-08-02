"""The 2026-07-19 planner-feedback freeze (root cause A) is structurally gone.

`check_task_file_coverage` harvested repo-convention headings out of files
like CLAUDE.md — imperatives such as ``Run `pnpm run lint:fix` - MUST pass
with no errors`` — as coverage items, then gated on whether each appeared
verbatim in the plan text. Those items combine a backtick-quoted command
with "MUST" and cannot appear verbatim in a subtask title or intent by
construction, so the gate fired identically every round: 33/33 feedback
rounds froze at an unchanged 15/15 ratio, costing ~$39 in feedback calls
that could not converge.

That was first patched by excluding backtick+MUST items from the ratio
(`_is_uncoverable_convention_item`) and de-duplicating repeated ratios
across rounds. Both were guards on a mechanism that should not have
existed: harvesting headings with regex, classifying the harvested prose
with another regex, and substring-matching the result against plan prose
are three layers of exactly what CLAUDE.md *Language-to-JSON* forbids.

The mechanism is deleted. Coverage of what a task's referenced files
require is `task_coverage_judge`'s job, which reads those files and judges
substance rather than string overlap. This file no longer tests that the
guard works — it tests that there is nothing left to guard.
"""
from __future__ import annotations

import inspect

import pytest


# The exact uncoverable-by-construction heading shapes recorded in the
# incident note: CLAUDE.md H3 headings combining a backtick-quoted command
# with "MUST". Retained because they are the concrete shape any future
# reimplementation would break on again.
INCIDENT_HEADINGS = [
    "CLAUDE.md: Run `pnpm run lint:fix` - MUST pass with no errors",
    "CLAUDE.md: Run `pnpm run build` - MUST succeed",
    "CLAUDE.md: Run `pnpm test <touched>` - MUST pass with no errors",
]


@pytest.mark.parametrize("sym", [
    "check_task_file_coverage",
    "extract_task_file_structure",
    "_is_uncoverable_convention_item",
    "_dedup_frozen_coverage_issues",
])
def test_the_frozen_mechanism_is_gone(leerie, sym):
    assert not hasattr(leerie, sym), (
        f"{sym} is back. The 2026-07-19 freeze was a property of this "
        "mechanism, not of its inputs — reintroducing it reintroduces the "
        "incident class")


def test_no_coverage_gate_in_the_planner_loop(leerie):
    """The freeze happened inside `phase_plan`'s CRITIC retry loop: a gate
    that fires every round on a signal no planner can move burns the round
    budget without converging. No such gate remains."""
    src = inspect.getsource(leerie.phase_plan)
    assert "LOW_COVERAGE" not in src
    assert "coverage_ratios" not in src


def test_incident_headings_reach_no_gate(leerie):
    """The load-bearing property, stated as behaviour rather than absence:
    there is no longer any call that turns these headings into a planner
    issue. If a future change adds one, it must not be substring-based —
    none of these can ever appear verbatim in a subtask."""
    for heading in INCIDENT_HEADINGS:
        key = heading.split(": ", 1)[1]
        assert "`" in key and "MUST" in key, (
            "fixture drift: these are meant to be the uncoverable shape")
    assert not hasattr(leerie, "check_task_file_coverage")


def test_coverage_judgment_moved_rather_than_vanished():
    """Deleting a check is only correct if something better owns the job.
    `task_coverage_judge` does, and is told to read the referenced files."""
    from pathlib import Path
    text = (Path(__file__).resolve().parent.parent
            / "prompts" / "task_coverage_judge.md").read_text()
    assert "Read the files the task names" in text
    assert "substring-matching" in text, (
        "the judge prompt should carry the reason it judges substance "
        "rather than string overlap — that is what froze the run")
