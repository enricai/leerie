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


# --- Fix 4: integration-integrity gate ------------------------------------
# The instrumentation (above) makes a silent integration skip *observable*.
# The gate below makes it *fatal-and-resumable*: a wave that settles with no
# failures but whose integrate_wave merged fewer subtasks than settled
# complete must die() rather than advance completed_waves onto an
# un-integrated wave (run 26fd0fa5: 10 complete, 0 integrated, no failure,
# reached finalize with an empty run branch).


def test_phase_execute_has_integration_integrity_gate(leerie):
    """phase_execute must die() when len(integrated) != expected."""
    src = inspect.getsource(leerie.phase_execute)
    assert "len(integrated) != expected" in src, (
        "phase_execute must gate on len(integrated) != expected after "
        "integrate_wave, so a silent integration shortfall halts the run "
        "instead of advancing completed_waves onto an un-integrated wave."
    )
    assert "integration integrity check failed" in src, (
        "the integrity gate must die() with an 'integration integrity check "
        "failed' message naming the integrated/expected counts."
    )


def test_integrity_gate_precedes_completed_waves_increment(leerie):
    """The gate must fire BEFORE completed_waves is advanced — otherwise the
    wave is already counted complete and the DESIGN §6 completion signal
    (completed_waves == len(waves)) would certify an un-integrated wave."""
    src = inspect.getsource(leerie.phase_execute)
    gate = src.index("len(integrated) != expected")
    # phase_execute has TWO `completed_waves = wi + 1` assignments: the
    # `remaining`-empty skip branch (which never integrates and is earlier in
    # the source) and the end-of-loop one the gate must precede. Anchor on
    # the LAST occurrence — the post-integration increment.
    bump = src.rindex('st.data["completed_waves"] = wi + 1')
    assert gate < bump, (
        "the integration-integrity gate must precede the post-integration "
        "completed_waves increment, so a shortfall halts the run before the "
        "wave is counted."
    )


def test_integrity_gate_die_points_at_resume(leerie):
    """The gate's die() must be resumable — name --resume and the subtask
    branches, since the per-subtask work survives on
    leerie/subtasks/<run-id>/* and --resume retries integration."""
    src = inspect.getsource(leerie.phase_execute)
    gate_region = src[src.index("len(integrated) != expected"):]
    assert "--resume" in gate_region, (
        "the integrity gate die() must point the operator at --resume."
    )
    assert "leerie/subtasks/" in gate_region, (
        "the integrity gate die() must name the subtask branches where the "
        "un-integrated work survives."
    )
