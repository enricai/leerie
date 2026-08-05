"""Tests for _retryable_failure() — the retry policy classifier.

Per DESIGN §12, the classifier dispatches on a structured
`failure_kind` enum tagged at the producer rather than substring-
matching prose. The coupling test below asserts that every producer's
retryable-path return uses a kind in `_RETRYABLE_FAILURE_KINDS`.
"""
from __future__ import annotations

import ast
import inspect
import re
from pathlib import Path

import pytest

LEERIE_PY = (Path(__file__).resolve().parent.parent
               / "orchestrator" / "leerie.py")


# --- behavior of _retryable_failure ---------------------------------------

@pytest.mark.parametrize("kind", [
    "no_commits",
    "dirty_worktree",
    "empty_handoff",
])
def test_retryable_kinds_return_true(leerie, kind):
    assert leerie._retryable_failure(kind) is True


@pytest.mark.parametrize("kind", [
    "broken",
    "",
    "unknown_kind",
    # legacy substrings from the old prose-classifying implementation
    # — must NOT accidentally pass through the new enum check:
    "no commits ahead of the run",
    "checkpoint_path 'foo' does not exist on disk",
])
def test_terminal_kinds_return_false(leerie, kind):
    assert leerie._retryable_failure(kind) is False


def test_retryable_kinds_constant_matches_documented_set(leerie):
    """The retryable enum must be exactly the six documented kinds.
    Adding a kind requires updating IMPLEMENTATION.md's "The two-tier
    retry policy" section (under §5 "Deterministic enforcement points")
    in the same change."""
    assert leerie._RETRYABLE_FAILURE_KINDS == frozenset(
        {"no_commits", "dirty_worktree", "empty_handoff", "worktree_setup",
         "pid_exhausted", "oom_killed"}
    )


def test_worktree_setup_is_retryable(leerie):
    """A worktree-setup failure is INFRASTRUCTURE, not a broken worker.

    It is raised before any worker runs, so `_retryable_failure`'s stated
    rationale for terminal ("the worker is broken or dishonest, and
    re-running it burns a worker invocation for no expected gain") does not
    hold: re-running is exactly what clears it.

    Regression pin for run 488c42e5 (2026-08-05), which lost `bugfix-009-2`
    to this after its implementer had already committed — killing the wave
    with 25 of 26 subtasks complete and leaving `resume` to hit the same
    wall on every attempt."""
    assert leerie._retryable_failure("worktree_setup") is True


def test_worktree_setup_retries_in_place_and_keeps_the_branch(leerie):
    """THE DESTRUCTIVE-FIX GUARD.

    Marking the kind retryable is only half correct. The retry path calls
    `_reset_subtask_worktree`, which runs `git branch -D` on the subtask
    branch — right for `no_commits` (nothing worth keeping), catastrophic
    here: the branch can already carry an earlier attempt's commits (488c42e5
    failed with `de0d3bf` committed). Resetting would turn an infrastructure
    hiccup into silent loss of paid-for work.

    So the kind must ALSO be in `_INFRASTRUCTURE_FAILURE_KINDS`, and `_settle_subtask`
    must gate the reset on it. `new-worktree.sh` reuses an existing branch by
    design, so retrying in place re-attaches to the work."""
    assert "worktree_setup" in leerie._INFRASTRUCTURE_FAILURE_KINDS
    assert "git branch -D" in inspect.getsource(
        leerie._reset_subtask_worktree) or "branch" in inspect.getsource(
        leerie._reset_subtask_worktree), "reset no longer touches the branch"
    src = inspect.getsource(leerie._settle_subtask)
    assert "_INFRASTRUCTURE_FAILURE_KINDS" in src, (
        "the reset is no longer gated — a worktree_setup retry would "
        "delete a branch that may hold committed work")
    i = src.index("_INFRASTRUCTURE_FAILURE_KINDS")
    j = src.index("await _reset_subtask_worktree")
    assert i < j, "the guard must precede the reset call"


def test_no_commits_still_resets(leerie):
    """ANTI-VACUITY: the in-place exemption must not disable the reset for
    the kind that genuinely needs it. `no_commits` means the branch holds
    nothing, and without the reset the retry hits
    `fatal: a branch ... already exists`."""
    assert "no_commits" not in leerie._INFRASTRUCTURE_FAILURE_KINDS


# --- coupling test: producer returns must match consumer's accepted set ---

# Every retryable-path producer return literal. Drift here without a
# matching change to `_RETRYABLE_FAILURE_KINDS` is caught by the test
# below. Keep this list aligned with the producer table in
# IMPLEMENTATION.md's "The two-tier retry policy" section (§5).
_PRODUCER_RETRYABLE_KINDS = {
    # check_branch_has_commits → "no_commits"
    "no_commits",
    # the inline dirty-worktree check in _settle_subtask → "dirty_worktree"
    "dirty_worktree",
    # _validate_result's incomplete-handoff missing-checkpoint arm → "empty_handoff"
    "empty_handoff",
    # _settle_subtask's `except WorktreeSetupError` arm → "worktree_setup"
    "worktree_setup",
    # _settle_subtask's `except PidExhaustedError` arm → "pid_exhausted"
    "pid_exhausted",
    # _settle_subtask's `except OomKilledError` arm → "oom_killed"
    "oom_killed",
}


def test_producer_kinds_all_classified_retryable(leerie):
    """Every kind a producer can tag on a retryable path must be in
    `_RETRYABLE_FAILURE_KINDS`. If a producer tags a new kind and
    forgets to add it to the enum, this test catches the drift."""
    missing = _PRODUCER_RETRYABLE_KINDS - leerie._RETRYABLE_FAILURE_KINDS
    assert not missing, (
        f"producers emit {missing!r} on retryable paths but "
        f"_RETRYABLE_FAILURE_KINDS does not accept them — the retry "
        f"classifier would silently downgrade these to terminal. "
        f"Add to _RETRYABLE_FAILURE_KINDS or fix the producer."
    )


def test_check_branch_has_commits_tags_no_commits(leerie):
    """`check_branch_has_commits` must return `("no_commits", ...)` on
    its retryable arm. If the kind is renamed or the producer is
    rewritten to return a different shape, this test fails."""
    src = inspect.getsource(leerie.check_branch_has_commits)
    assert '"no_commits"' in src, (
        "check_branch_has_commits no longer tags `no_commits` — the "
        "retry classifier would not treat its failure as retryable."
    )


def test_validate_result_tags_empty_handoff_for_missing_checkpoint(leerie):
    """`_validate_result`'s incomplete-handoff + missing-checkpoint arm
    must tag `("empty_handoff", ...)` — this is the Claude Code
    session-limit / rate-limit safety net path."""
    src = inspect.getsource(leerie._validate_result)
    assert '"empty_handoff"' in src, (
        "_validate_result no longer tags `empty_handoff` for the "
        "incomplete-handoff missing-checkpoint case — the session-"
        "limit no-op recovery path would be silently downgraded "
        "to terminal."
    )


def test_settle_subtask_tags_dirty_worktree(leerie):
    """The inline dirty-worktree check in `_settle_subtask` is the only
    producer of the `dirty_worktree` kind. Find it in the leerie.py
    source text and confirm the literal appears."""
    source = LEERIE_PY.read_text()
    settle_match = re.search(
        r"^(?:async )?def _settle_subtask\b.*?"
        r"(?=^(?:async )?(?:def |class ))",
        source, re.DOTALL | re.MULTILINE,
    )
    assert settle_match, "could not locate _settle_subtask in source"
    assert '"dirty_worktree"' in settle_match.group(0), (
        "_settle_subtask's dirty-worktree check no longer tags "
        "`dirty_worktree` — that retryable case would be silently "
        "downgraded to terminal."
    )


def test_settle_subtask_fail_calls_use_two_arg_signature():
    """Every `await fail(...)` invocation inside `_settle_subtask` must
    pass exactly two positional args: (kind, reason). The legacy
    single-arg shape `fail(reason)` would raise `TypeError` at runtime
    because `fail` was changed to take a structured `failure_kind`
    discriminator. The original refactor missed leerie.py:10293's
    worker-self-reported-failed arm because no test exercised that
    branch (the path is rare in production — see the "Per-subtask
    checks" table in IMPLEMENTATION.md §5 "Deterministic enforcement
    points"). This test parses _settle_subtask's AST and asserts the
    signature is consistent across ALL call sites.

    Concretely guards: the worker-self-reported `status: "failed"`
    arm in _settle_subtask must pass a structured kind alongside the
    worker's freeform summary."""
    source = LEERIE_PY.read_text()
    tree = ast.parse(source)

    # Locate _settle_subtask in the module's top-level functions.
    settle = None
    for node in tree.body:
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_settle_subtask":
            settle = node
            break
    assert settle is not None, "could not locate _settle_subtask in leerie.py AST"

    # Find every Call node inside _settle_subtask whose callee is the
    # bare name `fail` — the local closure. Exclude the def itself.
    fail_calls = []
    for node in ast.walk(settle):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id == "fail":
            fail_calls.append(node)

    assert fail_calls, (
        "no `fail(...)` calls found inside _settle_subtask — the test's "
        "AST walk is broken or the function was renamed."
    )

    bad = [
        (call.lineno, len(call.args), [ast.unparse(a) for a in call.args])
        for call in fail_calls
        if len(call.args) != 2
    ]
    assert not bad, (
        f"fail() calls inside _settle_subtask must pass exactly 2 "
        f"positional args (kind, reason); found wrong arity at: {bad!r}. "
        f"Every fail() invocation must pair a structured "
        f"`failure_kind` with a human-readable `reason`."
    )


def test_settle_subtask_tags_worktree_setup(leerie):
    """`_settle_subtask` must catch `WorktreeSetupError` SEPARATELY from the
    generic `except WorkerError`, which tags everything "broken" (terminal).

    Ordering is load-bearing: `WorktreeSetupError` subclasses `WorkerError`,
    so a generic handler placed first would swallow it and the kind would
    never be produced."""
    src = inspect.getsource(leerie._settle_subtask)
    assert "except WorktreeSetupError" in src
    assert '"worktree_setup"' in src
    assert (src.index("except WorktreeSetupError")
            < src.index("except WorkerError as e:")), (
        "WorktreeSetupError must be caught BEFORE the generic WorkerError "
        "arm — it is a subclass, so a generic-first order swallows it")


def test_producer_raises_the_tagged_exception_type(leerie):
    """The kind is tagged at the producer, never inferred from prose
    (the discipline `_RETRYABLE_FAILURE_KINDS`' own comment states)."""
    src = inspect.getsource(leerie._run_implementer)
    assert "raise WorktreeSetupError(" in src
    assert issubclass(leerie.WorktreeSetupError, leerie.WorkerError)


def test_pid_and_oom_producers_raise_the_tagged_exception_types(leerie):
    """PID-exhaustion and OOM-kill are both tagged at their producer,
    `_invoke`, never inferred from prose. Both raise sites live inside
    the same function."""
    src = inspect.getsource(leerie._invoke)
    assert "raise PidExhaustedError(" in src
    assert "raise OomKilledError(" in src
    assert issubclass(leerie.PidExhaustedError, leerie.WorkerError)
    assert issubclass(leerie.OomKilledError, leerie.WorkerError)


def test_settle_subtask_tags_pid_exhausted(leerie):
    """`_settle_subtask` must catch `PidExhaustedError` SEPARATELY from the
    generic `except WorkerError`, which tags everything "broken" (terminal).

    Ordering is load-bearing: `PidExhaustedError` subclasses `WorkerError`,
    so a generic handler placed first would swallow it and the kind would
    never be produced."""
    src = inspect.getsource(leerie._settle_subtask)
    assert "except PidExhaustedError" in src
    assert '"pid_exhausted"' in src
    assert (src.index("except PidExhaustedError")
            < src.index("except WorkerError as e:")), (
        "PidExhaustedError must be caught BEFORE the generic WorkerError "
        "arm — it is a subclass, so a generic-first order swallows it")


def test_settle_subtask_tags_oom_killed(leerie):
    """`_settle_subtask` must catch `OomKilledError` SEPARATELY from the
    generic `except WorkerError`, which tags everything "broken" (terminal).

    Ordering is load-bearing: `OomKilledError` subclasses `WorkerError`,
    so a generic handler placed first would swallow it and the kind would
    never be produced."""
    src = inspect.getsource(leerie._settle_subtask)
    assert "except OomKilledError" in src
    assert '"oom_killed"' in src
    assert (src.index("except OomKilledError")
            < src.index("except WorkerError as e:")), (
        "OomKilledError must be caught BEFORE the generic WorkerError "
        "arm — it is a subclass, so a generic-first order swallows it")


def test_pid_exhausted_and_oom_killed_are_retryable(leerie):
    assert leerie._retryable_failure("pid_exhausted") is True
    assert leerie._retryable_failure("oom_killed") is True
    assert "pid_exhausted" in leerie._INFRASTRUCTURE_FAILURE_KINDS
    assert "oom_killed" in leerie._INFRASTRUCTURE_FAILURE_KINDS


class TestCategoryIsTheSingleSourceOfTruth:
    """One membership must drive every consequence.

    The previous shape needed a kind listed in TWO sets (`_RETRYABLE_*` and
    `_RETRY_IN_PLACE_*`); a kind in only one is silently half-wrong — retried
    but branch-deleted, or preserved but terminal — and nothing catches it.
    That hazard is not hypothetical: auditing `raise WorkerError` found 5-6
    more infrastructure failures (PID exhaustion, OOM-kill, dropped API
    connection, 529, auth/quota) on the same generic path, so this set will
    gain members."""

    def test_infrastructure_kinds_are_retryable_by_composition(self, leerie):
        assert leerie._INFRASTRUCTURE_FAILURE_KINDS <= \
            leerie._RETRYABLE_FAILURE_KINDS
        assert leerie._RETRYABLE_FAILURE_KINDS == (
            leerie._WORKER_RETRYABLE_KINDS
            | leerie._INFRASTRUCTURE_FAILURE_KINDS), (
            "the retryable set must be COMPOSED from the two categories, not "
            "maintained separately — a hand-maintained copy is how a kind "
            "ends up in one list and not the other")

    def test_the_two_categories_are_disjoint(self, leerie):
        """A kind is either the worker's doing or it is not."""
        assert not (leerie._WORKER_RETRYABLE_KINDS
                    & leerie._INFRASTRUCTURE_FAILURE_KINDS)

    def test_composition_is_not_hand_maintained(self, leerie):
        """Source guard: `_RETRYABLE_FAILURE_KINDS` must be derived. A literal
        frozenset here would pass the equality test above today and silently
        drift the moment a category gains a member."""
        src = inspect.getsource(leerie)
        m = re.search(r"^_RETRYABLE_FAILURE_KINDS\s*=\s*(.+)$", src,
                      re.MULTILINE)
        assert m, "constant not found"
        assert "frozenset({" not in m.group(1), (
            "must be composed from the category sets, not a literal")
        assert "_INFRASTRUCTURE_FAILURE_KINDS" in m.group(1)

    def test_infrastructure_retry_is_logged(self, leerie):
        """It used to be silent: `fail()` logs only on terminal or
        cap-reached, so an operator saw a worker re-run with no explanation.
        Part of why 488c42e5 was hard to diagnose."""
        src = inspect.getsource(leerie._settle_subtask)
        # Strip comments first. The explanatory comment above the call also
        # contains "infrastructure failure", so a naive substring check is
        # satisfied by prose even when the log() call is gone — the same trap
        # CLAUDE.md records for the zombie reaper's docstring. Verified by
        # mutation: replacing the call with `pass` passed the naive check.
        code = "\n".join(l for l in src.splitlines()
                         if not l.strip().startswith("#"))
        assert 'log(f"  {sid}: infrastructure failure' in code, (
            "the infrastructure retry is silent again — an operator sees a "
            "worker re-run with no line explaining why")
        assert (code.index("_INFRASTRUCTURE_FAILURE_KINDS")
                < code.index("infrastructure failure")), (
            "the log must sit inside the category branch")

