"""Tests for the M9+M3 memory-admission-gate fix (artifact tag
`memory-admission-degrade-fix`): raise the build-peak admission floor to
8 GiB (matching `_auto_worker_memory_max_legacy`'s own floor for the
identical 6.3 GiB measurement) and replace the blocking
poll-then-admit-anyway `_await_worker_memory_admission` with a wave-scoped
`_degrade_max_parallel_for_wave` computed once at wave entry.

Mirrors tests/test_slice_aware_memory.py's stubbed-`_cgroup_slice_info`
pattern.
"""
from __future__ import annotations

import asyncio

import pytest


# ---- (1) the admission floor is 8 GiB, not 6.3 GiB -------------------------

def test_build_peak_floor_is_8_gib(leerie):
    assert leerie._WORKER_BUILD_PEAK_BYTES == int(8 * 1024**3)


def test_build_peak_floor_matches_legacy_floor(leerie):
    """The floor this gate uses must match `_auto_worker_memory_max_legacy`'s
    own documented floor for the SAME 6.3 GiB measurement — the
    self-inconsistency M9's finding names (6.3 GiB was 46% below the
    codebase's own 8 GiB legacy floor for the identical peak). A huge
    `max_parallel` forces the legacy per-worker split well below its
    8 GiB floor, so what's returned is the floor itself, not an unfloored
    split."""
    legacy_floor = leerie._auto_worker_memory_max_legacy(max_parallel=100000)
    assert leerie._WORKER_BUILD_PEAK_BYTES == legacy_floor


# ---- (2) a permanently-below-floor share degrades, without a 600s wait ----

def test_degrade_fires_on_a_permanently_saturated_share(leerie, monkeypatch, capsys):
    """A live sibling run holding its slot means the per-worker share can
    never rise on its own — the fix must degrade max_parallel for the wave
    and log a warning, not block waiting for a condition that provably
    cannot change."""
    monkeypatch.setattr(leerie, "_cgroup_slice_info",
                         lambda: (60 * 1024**3, 12))  # never improves
    result = leerie._degrade_max_parallel_for_wave(5)
    assert result < 5
    out = capsys.readouterr().out
    assert "degrading this wave" in out
    assert f"max_parallel={result}" in out


def test_degrade_proceeds_without_waiting_the_600s_poll_budget(leerie, monkeypatch):
    """The retired _await_worker_memory_admission polled up to 600s before
    admitting anyway. The replacement must return near-immediately —
    proven here by a hard wall-clock bound well under that budget, with no
    sleep/await involved at all (this is a plain sync function)."""
    monkeypatch.setattr(leerie, "_cgroup_slice_info",
                         lambda: (60 * 1024**3, 12))
    import time
    t0 = time.monotonic()
    leerie._degrade_max_parallel_for_wave(5)
    elapsed = time.monotonic() - t0
    assert elapsed < 1.0  # nowhere near the retired 600s poll budget


def test_await_worker_memory_admission_no_longer_exists(leerie):
    """The blocking poll-then-admit-anyway gate is retired, not merely
    dormant — its replacement is a synchronous, wave-scoped function."""
    assert not hasattr(leerie, "_await_worker_memory_admission")


# ---- (3) computed once at wave entry, not re-derived per spawn / oscillating ---

def test_degrade_is_a_plain_sync_function_not_a_per_spawn_gate(leerie):
    """`_invoke` (the per-spawn call site) must no longer carry a
    `max_parallel` admission-gating parameter — the gate moved to
    `phase_execute`'s wave loop, computed once per wave."""
    import inspect
    sig = inspect.signature(leerie._invoke)
    assert "max_parallel" not in sig.parameters
    assert not asyncio.iscoroutinefunction(leerie._degrade_max_parallel_for_wave)


def test_phase_execute_computes_degrade_once_per_wave_not_per_spawn(leerie):
    """Source-coupling guard (driving the real phase_execute end-to-end
    spawns real workers): `_degrade_max_parallel_for_wave` must be called
    inside phase_execute's `for wi in range(...)` wave loop, and the
    resulting Semaphore must be constructed from it — never inside
    `settle_one` (which would re-derive it per spawn) and never inside
    `_invoke`/`claude_p` (the retired per-spawn gate)."""
    import inspect
    src = inspect.getsource(leerie.phase_execute)
    assert "_degrade_max_parallel_for_wave(" in src
    # The call sits inside the wave loop, before the per-wave semaphore.
    wave_loop_idx = src.index("for wi in range(")
    degrade_idx = src.index("_degrade_max_parallel_for_wave(")
    sem_idx = src.index("asyncio.Semaphore(")
    assert wave_loop_idx < degrade_idx < sem_idx

    invoke_src = inspect.getsource(leerie._invoke)
    assert "_degrade_max_parallel_for_wave(" not in invoke_src
    claude_p_src = inspect.getsource(leerie.claude_p)
    assert "_degrade_max_parallel_for_wave(" not in claude_p_src


def test_degrade_result_does_not_oscillate_when_reapplied(leerie, monkeypatch):
    """A degraded max_parallel must not, by construction, feed back into
    `_slice_worker_memory_max`'s own divisor in a way that keeps shrinking
    on repeated evaluation against the same (unchanged) slice state —
    idempotent re-application is the falsifier for "oscillates"."""
    monkeypatch.setattr(leerie, "_cgroup_slice_info",
                         lambda: (60 * 1024**3, 12))
    a = leerie._degrade_max_parallel_for_wave(5)
    b = leerie._degrade_max_parallel_for_wave(a)
    c = leerie._degrade_max_parallel_for_wave(b)
    assert a == b == c
