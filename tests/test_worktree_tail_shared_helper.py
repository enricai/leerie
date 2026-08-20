"""Source-coupling guard: _reset_subtask_worktree and _prune_subtask_worktree
must both delegate their rmtree-fallback+to_thread-prune tail to one shared
helper rather than each inlining it."""

import inspect

from orchestrator import leerie


def test_shared_helper_exists():
    assert hasattr(leerie, "_rmtree_fallback_and_prune")


def test_both_functions_call_the_shared_helper():
    reset_src = inspect.getsource(leerie._reset_subtask_worktree)
    prune_src = inspect.getsource(leerie._prune_subtask_worktree)
    assert "_rmtree_fallback_and_prune(" in reset_src
    assert "_rmtree_fallback_and_prune(" in prune_src


def test_tail_not_inlined_separately():
    reset_src = inspect.getsource(leerie._reset_subtask_worktree)
    prune_src = inspect.getsource(leerie._prune_subtask_worktree)
    for src in (reset_src, prune_src):
        assert "shutil.rmtree(" not in src
        assert "_prune_leerie_worktrees" not in src
