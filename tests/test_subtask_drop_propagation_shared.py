"""Source-coupling guard: `_filter_offtree_subtasks` and
`_filter_satisfied_subtasks` must both call a single shared helper for the
pruned/dropped_provides/_remap_vanished_deps/_prune_orphaned_requires/
dropped_subtasks-persist sequence (DESIGN §5 *Id-vanishing operations*)
rather than each inlining it. Without this, the two enforcement sites can
drift out of lockstep by hand.
"""
from __future__ import annotations

import inspect


def test_both_filters_call_the_shared_drop_propagation_helper(leerie):
    assert hasattr(leerie, "_apply_subtask_drop_propagation"), (
        "expected a shared '_apply_subtask_drop_propagation' helper")

    offtree_src = inspect.getsource(leerie._filter_offtree_subtasks)
    satisfied_src = inspect.getsource(leerie._filter_satisfied_subtasks)

    assert "_apply_subtask_drop_propagation(" in offtree_src
    assert "_apply_subtask_drop_propagation(" in satisfied_src

    # Neither caller should still inline the sequence itself.
    for src in (offtree_src, satisfied_src):
        assert "_remap_vanished_deps(" not in src
        assert "_prune_orphaned_requires(" not in src
        assert 'st.data.setdefault("dropped_subtasks"' not in src


def test_shared_helper_does_the_prune_and_persist_work(leerie):
    src = inspect.getsource(leerie._apply_subtask_drop_propagation)
    assert "_remap_vanished_deps(" in src
    assert "_prune_orphaned_requires(" in src
    assert 'dropped_subtasks' in src
    assert "st.save()" in src
    # Logging stays caller-specific — the shared helper must not log.
    assert "log(" not in src
