"""Prompt-content pin for the contradiction judge's cross-invocation-state
carve-out (DESIGN §8 *Delivery re-drive* rule 4).

Motivation (measured, 2026-11-23, run-2 case p04): the judge was asked
whether a required item's probe and an existing test can both pass. The
existing test pinned "delete the active key labelled L"; the probe, driving
the same stateless per-request function twice, asserted a key it had just
created (same `isActive` and same label L — identical on every field the
delete branch reads) must NOT be deleted. The judge returned `both_can_hold`
by constructing a joint implementation that keeps "module-level memory of
the key ids it created and skips deleting those" — state a stateless
per-request function does not persist across invocations (a bookmark must
survive a restart; module memory does not). That spurious `both_can_hold`
routed around the `task_required` path and blocked a fix the task required.

The carve-out added to `mutually_exclusive` requires the joint
implementation the judge builds to be one the production code could run as
it stands — distinguishing the two cases by call-time data, not by
non-persisted cross-invocation state or by provenance the code does not
read. Re-benched (claude -p, opus high, the original lane-B3 pair inputs were
not reconstructable, so representative cases, 6 samples/arm): the
cross-invocation-state (p04) shape judges `exclusive` 6/6, while four
both-can-hold shapes — a route test mocking the lib vs the real lib, an input
selecting a redirect code, two observation times in one render, and a
persisted-state service — give 0 false `exclusive` of 24.

Substance discipline (sibling: test_classifier_standing_constraint_prompt):
assert the rule's LOAD-BEARING clauses — the call-time-data restriction, the
no-cross-invocation-state force, and the provenance-is-not-a-distinction
clause — INSIDE fact 4, not one incidental phrase a rewrite could keep while
gutting the rule.
"""
from __future__ import annotations


def _fact_four(text: str) -> str:
    """The `mutually_exclusive` fact's own text, isolated from the rest of
    the prompt so a clause present elsewhere (the JSON field list, the
    other facts) cannot satisfy the check."""
    start = text.index("4. `mutually_exclusive`")
    end = text.index("Evidence.", start)
    return text[start:end]


def test_prompt_forbids_non_persisted_cross_invocation_reconciliations(leerie):
    fact4 = _fact_four(leerie._load_prompt("contradiction_judge"))
    # The joint implementation must use data the function has at decision
    # time — its arguments and what it reads in that same call.
    assert "data the function has at the moment it decides" in fact4
    # Its force: it may NOT rely on state remembered across separate
    # invocations unless the code already persists it.
    assert "may not rely on state remembered across separate invocations" in fact4
    assert "already persists that state in a store both cases reach" in fact4
    # Provenance the code does not read is not a distinction — two inputs
    # equal on every deciding-branch field are the same input.
    assert "provenance the production code does not read is not a legitimate distinction" in fact4
    assert "two inputs equal on every field the deciding branch reads are the same input" in fact4


def test_carve_out_is_scoped_to_the_mutually_exclusive_fact(leerie):
    text = leerie._load_prompt("contradiction_judge")
    # The carve-out sharpens `mutually_exclusive` specifically; it must not
    # have drifted up into `same_input_by_meaning` / `same_observable`
    # (which judge different facts) nor down into the task-blind closing
    # instruction. A whole-prompt substring check would miss that drift.
    before_fact4 = text[: text.index("4. `mutually_exclusive`")]
    assert "may not rely on state remembered across separate invocations" not in before_fact4
    # It stays blind to the task: the carve-out adds no task text.
    fact4 = _fact_four(text)
    assert "the task" not in fact4.lower()
