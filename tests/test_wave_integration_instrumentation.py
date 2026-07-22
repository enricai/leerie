"""phase_execute must make wave integration observable.

Fix 3 of the empty-run-branch finalize investigation. The wave loop used
to keep current_phase == "phase 4-5: implementing" across both the settle
(implementers/conformers) and the integrate_wave step, so a silently
skipped integration — a wave marked complete whose branches never merged
into the run branch, the exact failure that let an empty run branch reach
finalize — left no trace in state.json. The memory sampler could not
distinguish "settling" from "integrating," so post-mortem forensics had
nothing to key on.

These source-coupling pins (mirroring
tests/test_phase_finalize_cleanup_invocation.py's inspect.getsource
style) assert phase_execute:
  1. stamps a distinct "phase 5: integrating wave N" current_phase before
     integrate_wave, so the sampler records the transition; and
  2. logs the integrated-count against the eligible-completed count after
     integrate_wave returns, so a divergence (integrated < expected with
     no die()) is a visible signature of a silent skip.
"""
from __future__ import annotations

import inspect


def test_phase_execute_stamps_integrating_phase_before_integrate_wave(leerie):
    """A distinct current_phase must be set before integrate_wave so the
    memory sampler can tell "integrating" from "implementing"."""
    src = inspect.getsource(leerie.phase_execute)
    stamp = src.index('"phase 5: integrating wave')
    integrate_call = src.index("integrate_wave(")
    assert stamp < integrate_call, (
        'phase_execute must set current_phase="phase 5: integrating wave N" '
        "BEFORE calling integrate_wave, so a silently-skipped integration "
        "leaves an observable phase transition in state.json / memory.ndjson."
    )


def test_phase_execute_logs_integrated_count_vs_expected(leerie):
    """After integrate_wave, phase_execute must log how many subtasks were
    integrated against how many were eligible (status == complete). A
    divergence with no die() is the visible signature of a silent skip."""
    src = inspect.getsource(leerie.phase_execute)
    # The eligible count is computed from results with status == "complete".
    assert 'r.get("status") == "complete"' in src, (
        "phase_execute must compute the eligible-completed count from "
        "results before integrate_wave so it can be logged against the "
        "integrated count."
    )
    # And a log line surfaces integrated-of-expected.
    assert "integrated" in src and "completed subtask" in src, (
        'phase_execute must log "integrated N of M completed subtask(s)" '
        "after integrate_wave so a silent skip (N < M) is visible."
    )
