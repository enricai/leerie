"""Tests for _get_progress() and _format_progress_prefix() — the helpers
that compute the per-wave activity counts and the rendered prefix shown
on each Phase 5 streaming log line.

_get_progress returns None before planning (no waves key) and once
waves exist it splits the current wave's subtasks into running /
in_conformer / done buckets and surfaces the in-flight wave index
(`completed_waves + 1`).
"""
from __future__ import annotations

from types import SimpleNamespace


def test_no_waves_returns_none(leerie):
    """Before planning, st.data has no 'waves' key. Returns None so
    classifier/planner workers emit no progress prefix."""
    st = SimpleNamespace(data={})
    assert leerie._get_progress(st) is None


def test_empty_waves_returns_none(leerie):
    """waves=[] means planning produced no subtasks. Returns None
    rather than zeros to avoid showing a misleading prefix."""
    st = SimpleNamespace(data={"waves": [], "subtask_status": {}})
    assert leerie._get_progress(st) is None


def test_all_pending_counts_as_running(leerie):
    """At wave start, every subtask is running and none are done.
    Restricted to the current wave's membership — sibling waves'
    subtasks don't pollute the count."""
    st = SimpleNamespace(data={
        "waves": [["a", "b"], ["c"]],
        "subtask_status": {},
    })
    # (running, in_conformer, done, wave_idx, wave_total)
    assert leerie._get_progress(st) == (2, 0, 0, 1, 2)


def test_mixed_running_and_done(leerie):
    """Common mid-wave state: some implementers settled, others still
    running. 'complete' with conformance present counts as done;
    'in_progress' counts as running."""
    st = SimpleNamespace(data={
        "waves": [["a", "b", "c"]],
        "subtask_status": {"a": "complete", "b": "failed", "c": "in_progress"},
        "conformance": {"a": {"result": None, "warnings": []}},
    })
    # a: done (complete + conformance present)
    # b: done (failed is terminal regardless of conformer)
    # c: running (not terminal)
    assert leerie._get_progress(st) == (1, 0, 2, 1, 1)


def test_complete_without_conformance_is_in_conformer(leerie):
    """The case that prompted this redesign: implementer reached
    'complete' but the advisory conformer phase hasn't finished yet.
    Reported as in_conformer, not done."""
    st = SimpleNamespace(data={
        "waves": [["a", "b"]],
        "subtask_status": {"a": "complete", "b": "complete"},
        "conformance": {"a": {"result": None, "warnings": []}},
    })
    # a: done (conformance settled)
    # b: in_conformer (status complete, conformance absent)
    assert leerie._get_progress(st) == (0, 1, 1, 1, 1)


def test_failed_skips_conformer_check(leerie):
    """'failed' / 'blocked' are terminal regardless of the conformance
    dict — the conformer only runs on the success path. They count as
    done even with no conformance entry."""
    st = SimpleNamespace(data={
        "waves": [["a"]],
        "subtask_status": {"a": "blocked"},
        "conformance": {},
    })
    assert leerie._get_progress(st) == (0, 0, 1, 1, 1)


def test_cleared_failed_status_counts_as_running(leerie):
    """On --resume, phase_execute pops stale failed/blocked entries from
    subtask_status before dispatching retries. The absent key should
    make _get_progress count the subtask as running, not done."""
    st = SimpleNamespace(data={
        "waves": [["a", "b", "c"]],
        "subtask_status": {"a": "complete", "b": "complete"},
        "conformance": {
            "a": {"result": None, "warnings": []},
            "b": {"result": None, "warnings": []},
        },
    })
    # c was "failed" but popped before retry — absent = running
    assert leerie._get_progress(st) == (1, 0, 2, 1, 1)


def test_wave_index_advances_with_completed_waves(leerie):
    """`completed_waves` is incremented after each wave settles. The
    in-flight wave is `completed_waves + 1`, and counts are restricted
    to that wave only — `a` from wave 1 doesn't leak into wave 2."""
    st = SimpleNamespace(data={
        "waves": [["a"], ["b", "c"], ["d"]],
        "subtask_status": {"a": "complete", "b": "complete"},
        "conformance": {"a": {"result": None, "warnings": []}},
        "completed_waves": 1,
    })
    # current wave = waves[1] = ["b", "c"]
    # b: in_conformer (complete + no conformance)
    # c: running (no status)
    assert leerie._get_progress(st) == (1, 1, 0, 2, 3)


def test_post_wave_loop_returns_none(leerie):
    """When `completed_waves >= len(waves)`, the wave loop has finished
    and post-wave workers (summarizer, pr_writer, _run_final_conformance)
    run. There is no in-flight wave; return None so those workers emit
    no prefix (rather than the absurd `wave 3/2` overflow the old
    `+ 1`-without-bound code produced)."""
    st = SimpleNamespace(data={
        "waves": [["a"], ["b"]],
        "subtask_status": {"a": "complete", "b": "complete"},
        "conformance": {
            "a": {"result": None, "warnings": []},
            "b": {"result": None, "warnings": []},
        },
        "completed_waves": 2,
    })
    assert leerie._get_progress(st) is None


def test_all_done_after_conformers_settle(leerie):
    """Once every subtask in the wave has both implementer and
    conformer settled, the prefix should read fully done."""
    st = SimpleNamespace(data={
        "waves": [["a", "b"]],
        "subtask_status": {"a": "complete", "b": "complete"},
        "conformance": {
            "a": {"result": None, "warnings": []},
            "b": {"result": None, "warnings": []},
        },
    })
    assert leerie._get_progress(st) == (0, 0, 2, 1, 1)


# --- _format_progress_prefix ----------------------------------------


def test_format_prefix_none_returns_empty(leerie):
    """None (no waves scheduled yet) renders to empty string so
    classifier/planner workers emit no prefix at all."""
    assert leerie._format_progress_prefix(None) == ""


def test_format_prefix_wave_start(leerie):
    """At wave start: only the running segment appears; in_conformer
    and done are omitted because they're zero."""
    # 5 running, 0 in_conformer, 0 done, wave 1 of 1
    out = leerie._format_progress_prefix((5, 0, 0, 1, 1))
    assert out == "[wave 1 of 1 · running 5 subtasks] "


def test_format_prefix_mid_wave(leerie):
    """Mid-wave: running and done both appear; done last so progress
    reads on the right."""
    out = leerie._format_progress_prefix((2, 0, 3, 1, 1))
    assert out == "[wave 1 of 1 · running 2 subtasks · 3 subtasks done] "


def test_format_prefix_conformer_phase(leerie):
    """Implementers settled, advisory conformer still running on the
    last subtask."""
    out = leerie._format_progress_prefix((0, 1, 4, 1, 1))
    assert out == "[wave 1 of 1 · 1 subtask in conformer · 4 subtasks done] "


def test_format_prefix_all_done(leerie):
    """Wave fully settled — only the done segment appears."""
    out = leerie._format_progress_prefix((0, 0, 5, 1, 1))
    assert out == "[wave 1 of 1 · 5 subtasks done] "


def test_format_prefix_singular_plural(leerie):
    """`1 subtask` vs `2 subtasks` — pluralize on the count per
    segment independently."""
    # Singular running, plural done
    out = leerie._format_progress_prefix((1, 0, 2, 1, 1))
    assert out == "[wave 1 of 1 · running 1 subtask · 2 subtasks done] "
    # Singular in conformer, singular done
    out = leerie._format_progress_prefix((0, 1, 1, 1, 1))
    assert out == "[wave 1 of 1 · 1 subtask in conformer · 1 subtask done] "


def test_format_prefix_multi_wave_header(leerie):
    """The wave header reads `wave W of V` so the reader doesn't have
    to interpret a bare `1/3`."""
    out = leerie._format_progress_prefix((3, 0, 0, 2, 3))
    assert out == "[wave 2 of 3 · running 3 subtasks] "
