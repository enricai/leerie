"""`prompts/planner.md`'s `extent` rule must admit out-of-this-run work.

DESIGN §5 `requires.extent` defines two *values* (`in_plan` / `external`) and
three *kinds* of `external`. Before the first of these changes the prompt
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

**What these tests cannot tell you.** Prose guards prove the words are
present, never that a planner obeys them. The behavioural evidence is the
sandbox experiment described in `TestTaskDeclaredFenceIsExpressible`; re-run
it against an edited `prompts/planner.md` before trusting a change to this
section, because a rewrite that keeps every phrase below can still stop
working. Note also that the enforcing backstop named above has **never fired
in 258 recorded runs** — every measured improvement in this area came from
the prose, which is why the prose is guarded this closely.
"""
from __future__ import annotations

import inspect
import pathlib

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def planner_md() -> str:
    return (REPO / "prompts" / "planner.md").read_text()


def _norm(text: str) -> str:
    """Collapse runs of whitespace so a phrase assertion survives re-wrapping.

    These are prose guards over a hand-wrapped markdown file, so an edit that
    changes nothing but where a line breaks would otherwise fail them — which
    is a false alarm, and worse, tempts the next person to weaken the
    assertion instead of the matching. Normalizing the haystack keeps the
    phrase exact while making the line break irrelevant.
    """
    return " ".join(text.split())


class TestOutOfScopeIsExpressible:
    def test_external_admits_work_owned_by_another_run(self, planner_md):
        """The phrase must be the SECOND-KIND heading, not any mention.

        `"another run"` alone occurs three times in this file, so matching
        it would still pass with the second-kind block deleted.
        """
        assert "Produced by code, but outside THIS run's scope" in _norm(
            planner_md), (
            "the planner needs a legal way to say a capability is produced "
            "by code that belongs to a different run")

    def test_decision_test_is_scoped_to_this_plan(self, planner_md):
        """The one word that caused the ambiguity."""
        assert "in **this plan** produce this" in _norm(planner_md)

    def test_old_unscoped_phrasing_is_gone(self, planner_md):
        """ANTI-VACUITY CONTROL. The rule above can pass while the original
        unscoped sentence still sits in the prompt telling the planner the
        opposite. Reverting that one word must fail this test."""
        assert "in some domain's plan produce this" not in _norm(planner_md).lower()

    def test_already_landed_work_is_not_a_requires(self, planner_md):
        """The dominant real cause: a capability the task describes as
        already fixed is existing state, not a dependency."""
        low = _norm(planner_md).lower()
        assert "already landed" in low or "already done" in low


class TestTaskDeclaredFenceIsExpressible:
    """The THIRD kind of `external` (DESIGN §5): a surface the task itself
    fences off.

    Run `2d7527f1` (2026-08-17, 55 workers, $12.46, no code) carried a task
    that fenced off an application-source directory and then listed an
    acceptance criterion whose only implementation site was inside it.
    Planners split: the domain owning the surface obeyed the fence, the
    `testing` domain obeyed the criterion and declared `extent: in_plan` on
    a capability nobody could provide. The reconciler had no legal
    resolution and aborted.

    Measured in a controlled experiment — real prompt rules extracted from
    this file, the real as-run task text recovered from the failed run's
    `run.json`, and a sandbox copy of the target repo with its planning
    docs removed (the task file had been corrected on disk after the run,
    which silently contaminated two earlier attempts):

        pre-fix prompt      1/6  safe   (5 of 6 reproduced `in_plan`)
        as-shipped prompt  17/18 safe   (94%, Wilson 95% CI 0.74–0.99)
        Fisher one-sided                 p = 0.00081

    The shipped wording was re-validated against these exact edits, not
    against the draft patch the design experiment used — an earlier draft
    scored 6/6 at n=6 and the first re-validation of the real text came
    back 5/6, which is why the sample was extended rather than trusted.
    """

    def test_external_admits_a_task_declared_fence(self, planner_md):
        assert "Fenced off by the task itself" in _norm(planner_md), (
            "the planner has no legal way to say a capability's only "
            "implementation site is on a surface the task forbids")

    def test_fence_question_precedes_the_connector_question(self, planner_md):
        """THE load-bearing assertion, and why presence alone is not enough.

        The connector question ("could a small connector subtask in this
        plan produce this?") answers *yes* for a fenced code change, so
        asking it first routes exactly the forbidden capability to
        `in_plan`. A test that merely asserts both sentences exist passes
        against the original file, which already contains the connector
        one. Only the ORDER discriminates.
        """
        norm = _norm(planner_md)
        fence = norm.find("Does the task fence off the surface")
        connector = norm.find("could a small connector subtask")
        assert fence != -1, "the fence question is absent from the ordering"
        assert connector != -1, "the connector question is absent"
        assert fence < connector, (
            "the connector question is asked before the fence question; that "
            "ordering is what produced the incident")

    def test_the_ordering_is_stated_as_load_bearing(self, planner_md):
        """A bare reordering is one edit away from being undone by someone
        tidying the list. The prompt must say why the order matters."""
        norm = _norm(planner_md)
        assert "in this order" in norm
        assert "order is load-bearing" in norm.lower()

    def test_test_wiring_mandate_has_a_fence_escape(self, planner_md):
        """"Test subtasks must wire to their producers" is threat-framed
        ("wasting the whole planning spend") and had NO escape — it is the
        direct generator of the orphan `requires` this class exists for."""
        norm = _norm(planner_md)
        i = norm.find("Test subtasks must wire to their producers")
        assert i != -1, "the wiring mandate moved; re-anchor this test"
        after = norm[i:i + 3000]
        assert "The one escape" in after, (
            "the wiring mandate still admits no way out for a producer the "
            "task fences off")
        assert "external" in after

    def test_escape_still_forbids_the_silent_middle(self, planner_md):
        """ANTI-VACUITY. An escape that merely said "or omit the edge"
        would satisfy the test above while reintroducing the failure the
        mandate exists to prevent."""
        norm = _norm(planner_md)
        i = norm.find("The one escape")
        assert i != -1, "the escape block is absent"
        after = norm[i:i + 1200]
        assert "silent middle" in after
        assert "omitted edge" in after


class TestLayersAgree:
    """The three-layer rule is top-down canonical, so a prompt that says
    something DESIGN does not is a defect regardless of which reads better."""

    def test_design_describes_the_second_kind(self):
        """Anchor on the phrase that occurs once.

        `"another run"` appears 4x in DESIGN.md and 2x in IMPLEMENTATION.md
        (one of those an unrelated cgroup-broker row), so the disjunction
        this replaced would still pass with the second-kind block deleted —
        the same vacuity the sibling planner assertion was fixed for.
        """
        text = _norm((REPO / "docs" / "DESIGN.md").read_text())
        assert "Producible by code, but owned by another run" in text

    def test_design_states_the_discriminating_test(self):
        """Anchor on the sentence, not on two common words.

        `"this run's"` and `"build graph"` each occur several times in
        DESIGN.md, including an unrelated paragraph about worktree bounds,
        so testing them separately proves nothing.
        """
        text = _norm((REPO / "docs" / "DESIGN.md").read_text())
        assert 'is it in *this run\'s* graph?' in text.lower()

    def test_implementation_restatement_is_not_narrower(self):
        """IMPLEMENTATION.md previously described `external` as strictly
        (other repo, ops runbook, manual step) — a list that excludes the
        second kind and would contradict DESIGN.

        Anchored on the phrase that occurs once, for the reason given on
        the DESIGN sibling above.
        """
        text = _norm((REPO / "docs" / "IMPLEMENTATION.md").read_text())
        assert "producible by code but owned by another run" in text.lower()

    def test_design_describes_the_third_kind(self):
        text = (REPO / "docs" / "DESIGN.md").read_text()
        assert "Fenced off by the task itself" in _norm(text)

    def test_implementation_restatement_includes_the_fence(self):
        """Same shape as the second-kind guard above: IMPLEMENTATION must
        not enumerate a narrower list than DESIGN, or the two disagree
        about what a planner is allowed to declare."""
        text = (REPO / "docs" / "IMPLEMENTATION.md").read_text().lower()
        assert "fenced off by the task itself" in _norm(text)

    def test_validator_does_not_demand_the_forbidden_criterion(self, leerie):
        """`_validate_plan` is the one code site that mechanically gates
        `extent: external`, and its error text is read by a planner at
        plan-death.

        It used to demand a `reason` saying **"why no in-repo subtask could
        produce it"** — which is exactly the discriminating test DESIGN §5
        forbids. Both of the non-ops kinds are producible by an in-repo
        subtask (one is owned by another run, the other sits on a fenced
        surface); they are external because they are not in *this run's
        graph*. A planner that classified either correctly could not answer
        the question, so the validator argued for the misclassification that
        causes the abort.

        Anti-vacuity: the error string must be FOUND before its content is
        asserted — a scan that matches nothing passes every claim it makes.
        """
        src = _norm(inspect.getsource(leerie._validate_plan))
        marker = "with extent=external"
        assert marker in src, (
            "could not locate the external-reason validation error; "
            "re-anchor this test rather than deleting it")
        i = src.find(marker)
        err = src[i:i + 500]
        assert "why no in-repo subtask could produce it" not in err, (
            "the validator demands the criterion DESIGN §5 forbids")
        assert "fences off" in err, (
            "the validator's owner list omits the task-declared fence, so it "
            "is narrower than DESIGN's taxonomy")

    def test_all_three_layers_agree_on_the_count(self):
        """A carve-out added to one layer and not the others is the drift
        this class exists to catch.

        The earlier version checked DESIGN and `prompts/planner.md` — but
        `planner.md` is **not** one of CLAUDE.md's three layers, and
        IMPLEMENTATION, which is, went unchecked. Both are worth pinning;
        they are just different things (layer agreement vs. product-surface
        agreement), so they are asserted separately below.
        """
        design = _norm((REPO / "docs" / "DESIGN.md").read_text())
        impl = _norm((REPO / "docs" / "IMPLEMENTATION.md").read_text())
        assert "Three kinds qualify" in design
        assert "or fenced off by the task itself" in impl, (
            "IMPLEMENTATION does not enumerate the third kind, so the "
            "code-surface spec is narrower than DESIGN")

    def test_every_prompt_that_enumerates_external_kinds_lists_three(self):
        """Product surface, not a layer — but these ship to workers.

        `reconciler.md` and `pr_writer.md` each carried a closed two-kind
        list that this change widened; nothing pinned either. A planner
        told the taxonomy excludes the fence is the mental model the
        incident came from.
        """
        for name, phrase in (
            ("planner.md", "Three kinds"),
            ("reconciler.md", "a surface the task fences off"),
            ("pr_writer.md", "sits on a surface the task fences off"),
        ):
            text = _norm((REPO / "prompts" / name).read_text())
            assert phrase in text, f"{name} omits the third external kind"
