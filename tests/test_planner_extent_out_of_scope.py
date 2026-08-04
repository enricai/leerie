"""`prompts/planner.md`'s `extent` rule must admit out-of-this-run work.

DESIGN §5 `requires.extent` defines two values. Before this change the prompt
defined `external` as work **no code subtask could produce** and offered this
decision test:

    could a small connector subtask in SOME DOMAIN'S PLAN produce this?
    If yes, it is `in_plan`.

A fix described in a sibling phase document *is* producible by a code subtask,
so that rule pushed the planner toward `in_plan` — which by definition has no
provider and routes straight to `unresolvable`. Measured across four
funeralworks runs, planners split both ways on the identical capability within
a single planning pass, and six entries died there.

The prompt is advisory (DESIGN §12) — the enforcing check is
`_demote_unresolvable_with_external_twin`, covered in
`tests/test_demote_unresolvable_twin.py`. This file pins only that the
guidance is present and self-consistent across the three layers, following
`tests/test_migration_surface.py::test_planner_prompt_asks_for_the_field`.
"""
from __future__ import annotations

import pathlib

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def planner_md() -> str:
    return (REPO / "prompts" / "planner.md").read_text()


class TestOutOfScopeIsExpressible:
    def test_external_admits_work_owned_by_another_run(self, planner_md):
        low = planner_md.lower()
        assert "outside this run's scope" in low or "another run" in low, (
            "the planner needs a legal way to say a capability is produced "
            "by code that belongs to a different run")

    def test_decision_test_is_scoped_to_this_plan(self, planner_md):
        """The one word that caused the ambiguity."""
        assert "in **this plan** produce this" in planner_md

    def test_old_unscoped_phrasing_is_gone(self, planner_md):
        """ANTI-VACUITY CONTROL. The rule above can pass while the original
        unscoped sentence still sits in the prompt telling the planner the
        opposite. Reverting that one word must fail this test."""
        assert "in some domain's plan produce this" not in planner_md.lower()

    def test_already_landed_work_is_not_a_requires(self, planner_md):
        """The dominant real cause: a capability the task describes as
        already fixed is existing state, not a dependency."""
        low = planner_md.lower()
        assert "already landed" in low or "already done" in low


class TestLayersAgree:
    """The three-layer rule is top-down canonical, so a prompt that says
    something DESIGN does not is a defect regardless of which reads better."""

    def test_design_describes_the_second_kind(self):
        text = (REPO / "docs" / "DESIGN.md").read_text().lower()
        assert "owned by another run" in text or "another run" in text

    def test_design_states_the_discriminating_test(self):
        text = (REPO / "docs" / "DESIGN.md").read_text().lower()
        assert "this run's" in text and "build graph" in text

    def test_implementation_restatement_is_not_narrower(self):
        """IMPLEMENTATION.md previously described `external` as strictly
        (other repo, ops runbook, manual step) — a list that excludes the
        second kind and would contradict DESIGN."""
        text = (REPO / "docs" / "IMPLEMENTATION.md").read_text().lower()
        assert "owned by another run" in text or "another run" in text
