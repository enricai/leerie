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
    """The retryable enum must be exactly the three documented kinds.
    Adding a kind requires updating IMPLEMENTATION.md's "The two-tier
    retry policy" section (under §5 "Deterministic enforcement points")
    in the same change."""
    assert leerie._RETRYABLE_FAILURE_KINDS == frozenset(
        {"no_commits", "dirty_worktree", "empty_handoff"}
    )


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
