"""Tests for the N9 fix: per-worker memory sizing derived from the shared
`leerie.slice` budget and live sibling-worker count, plus the admission gate
that blocks spawning a worker when doing so would drive the shared
per-worker allocation below the measured build-peak floor.

Mirrors tests/test_cgroup_helpers.py's stubbed-socket-broker pattern: stub
`_cgroup_request` (the broker round-trip) rather than a real cgroup tree.
"""
from __future__ import annotations

import asyncio

import pytest


def _stub_broker(leerie, monkeypatch, responses):
    """Same helper as test_cgroup_helpers.py's _stub_broker."""
    sent = []

    def fake(payload, timeout=5.0):
        sent.append(payload)
        resp = responses(payload) if callable(responses) else responses
        if resp.startswith("RAISE"):
            raise OSError(resp[len("RAISE"):].strip() or "connection refused")
        return resp

    monkeypatch.setattr(leerie, "_cgroup_request", fake)
    return sent


# ---- _cgroup_slice_info: broker client -------------------------------------

def test_slice_info_parses_ok_pair(leerie, monkeypatch):
    sent = _stub_broker(leerie, monkeypatch, "OK 58956849152 12")
    assert leerie._cgroup_slice_info() == (58956849152, 12)
    assert sent == ["slice"]


def test_slice_info_none_on_unreachable(leerie, monkeypatch):
    _stub_broker(leerie, monkeypatch, "RAISE no broker")
    assert leerie._cgroup_slice_info() is None


def test_slice_info_none_on_broker_error(leerie, monkeypatch):
    _stub_broker(leerie, monkeypatch, "ERR no usable cgroup hierarchy")
    assert leerie._cgroup_slice_info() is None


def test_slice_info_none_on_malformed_response(leerie, monkeypatch):
    _stub_broker(leerie, monkeypatch, "OK 123")  # wrong arity
    assert leerie._cgroup_slice_info() is None
    _stub_broker(leerie, monkeypatch, "OK a b")  # non-integer
    assert leerie._cgroup_slice_info() is None


def test_slice_info_none_when_no_configured_ceiling(leerie, monkeypatch):
    """The broker reports -1 for memory.max when the slice has no
    configured ceiling — treat as "no shared budget known", not
    unlimited."""
    _stub_broker(leerie, monkeypatch, "OK -1 3")
    assert leerie._cgroup_slice_info() is None


# ---- _slice_worker_memory_max: pure divisor --------------------------------

def test_slice_divisor_matches_n9_formula(leerie):
    """slice_max // (live_siblings + max_parallel + 1) — the formula fixed
    in PENDING_ISSUES.md N9's DECISION."""
    slice_max = 58956849152  # ~54.9 GiB, the measured live host value
    assert leerie._slice_worker_memory_max(slice_max, 12, 5) == \
        slice_max // (12 + 5 + 1)


def test_slice_divisor_shrinks_with_more_siblings(leerie):
    slice_max = 100 * 1024**3
    fewer = leerie._slice_worker_memory_max(slice_max, 0, 5)
    more = leerie._slice_worker_memory_max(slice_max, 20, 5)
    assert more < fewer


def test_slice_divisor_floors_at_256mib(leerie):
    """A saturated/tiny slice must not yield a zero or negative cap."""
    assert leerie._slice_worker_memory_max(1024, 1000, 5) == 256 * 1024**2


# ---- _auto_worker_memory_max: slice-aware basis, NOT /proc/meminfo --------

def test_auto_memory_max_uses_slice_basis_when_available(leerie, monkeypatch):
    """The load-bearing regression pin: falsify by reverting to the
    /proc/meminfo basis (e.g. deleting the _cgroup_slice_info() check) and
    confirm this fails."""
    monkeypatch.setattr(leerie, "_cgroup_slice_info",
                         lambda: (60 * 1024**3, 12))
    # Sabotage the /proc/meminfo fallback so the test would fail loudly if
    # _auto_worker_memory_max ever fell through to it despite slice info
    # being available.
    monkeypatch.setattr(leerie, "_auto_worker_memory_max_legacy",
                         lambda max_parallel: (_ for _ in ()).throw(
                             AssertionError("legacy basis must not be used "
                                            "when slice info is available")))
    result = leerie._auto_worker_memory_max(max_parallel=5)
    assert result == leerie._slice_worker_memory_max(60 * 1024**3, 12, 5)


def test_auto_memory_max_falls_back_to_legacy_when_no_slice_info(
        leerie, monkeypatch):
    """No broker / containment off: falls back to the pre-N9
    /proc/meminfo-derived basis rather than failing."""
    monkeypatch.setattr(leerie, "_cgroup_slice_info", lambda: None)
    sentinel = 7 * 1024**3
    monkeypatch.setattr(leerie, "_auto_worker_memory_max_legacy",
                         lambda max_parallel: sentinel)
    assert leerie._auto_worker_memory_max(max_parallel=5) == sentinel


def test_auto_memory_max_reverting_to_proc_meminfo_basis_fails(
        leerie, monkeypatch):
    """Falsification per the success-criteria seed: with a live slice
    budget available, the /proc/meminfo-derived legacy value must NOT be
    what's returned."""
    monkeypatch.setattr(leerie, "_cgroup_slice_info",
                         lambda: (60 * 1024**3, 12))
    legacy_value = leerie._auto_worker_memory_max_legacy(max_parallel=5)
    result = leerie._auto_worker_memory_max(max_parallel=5)
    assert result != legacy_value


# ---- _degrade_max_parallel_for_wave: wave-scoped admission gate -----------
# (M9+M3 DECISION 2026-08-09: replaces the retired blocking
# _await_worker_memory_admission poll-then-admit-anyway loop with a single,
# non-blocking check computed once at wave entry.)

def test_degrade_returns_max_parallel_unchanged_when_no_slice_info(
        leerie, monkeypatch):
    """Containment off / no broker: nothing to gate against."""
    monkeypatch.setattr(leerie, "_cgroup_slice_info", lambda: None)
    assert leerie._degrade_max_parallel_for_wave(5) == 5


def test_degrade_returns_max_parallel_unchanged_when_cap_above_build_peak(
        leerie, monkeypatch):
    monkeypatch.setattr(leerie, "_cgroup_slice_info",
                         lambda: (200 * 1024**3, 2))  # plenty of room
    assert leerie._degrade_max_parallel_for_wave(
        5, build_peak_bytes=leerie._WORKER_BUILD_PEAK_BYTES) == 5


def test_degrade_shrinks_max_parallel_when_share_is_below_floor(
        leerie, monkeypatch):
    """The core M3 contract: a live sibling holding its slot means the
    per-worker share at the configured max_parallel can never rise on its
    own — the fix must shrink this wave's own concurrency, not poll and
    hope."""
    # 60 GiB slice, 12 live siblings, max_parallel=5: 60/(12+5+1) ~= 3.3 GiB
    # per worker, below an 8 GiB floor at max_parallel=5 but not at 1.
    monkeypatch.setattr(leerie, "_cgroup_slice_info",
                         lambda: (60 * 1024**3, 12))
    result = leerie._degrade_max_parallel_for_wave(5)
    assert 1 <= result < 5
    # Sanity: the returned concurrency actually fits the floor (or is the
    # unavoidable floor of 1 when even solo can't fit).
    cap = leerie._slice_worker_memory_max(60 * 1024**3, 12, result)
    assert cap >= leerie._WORKER_BUILD_PEAK_BYTES or result == 1


def test_degrade_never_returns_less_than_one(leerie, monkeypatch):
    """A permanently saturated slice must not degrade below a usable
    minimum concurrency — better to run one worker at real OOM risk than
    to admit zero and make no progress."""
    monkeypatch.setattr(leerie, "_cgroup_slice_info",
                         lambda: (1024, 1000))  # tiny, saturated
    assert leerie._degrade_max_parallel_for_wave(5) == 1


def test_degrade_does_not_sleep_or_block(leerie, monkeypatch):
    """Bounded/near-immediate: no poll loop, no await — a plain sync call
    that returns on its first pass regardless of how saturated the slice
    is (replacing the old up-to-600s block-then-admit-anyway wait)."""
    monkeypatch.setattr(leerie, "_cgroup_slice_info",
                         lambda: (60 * 1024**3, 12))  # never improves
    calls = {"n": 0}

    def fail_if_slept(*_a, **_k):
        calls["n"] += 1
        raise AssertionError("_degrade_max_parallel_for_wave must not sleep")

    monkeypatch.setattr("time.sleep", fail_if_slept)
    monkeypatch.setattr(asyncio, "sleep", fail_if_slept)
    result = leerie._degrade_max_parallel_for_wave(5)
    assert calls["n"] == 0
    assert result == 1


def test_degrade_logs_warning_naming_the_degraded_value(leerie, monkeypatch, capsys):
    monkeypatch.setattr(leerie, "_cgroup_slice_info",
                         lambda: (60 * 1024**3, 12))
    result = leerie._degrade_max_parallel_for_wave(5)
    out = capsys.readouterr().out
    assert "degrading this wave to max_parallel=" in out
    assert f"max_parallel={result}" in out


def test_degrade_computed_once_does_not_feed_back_into_slice_divisor(
        leerie, monkeypatch):
    """M3's residual-risk note: the degraded value is a plain int handed to
    asyncio.Semaphore(), never re-fed into _slice_worker_memory_max's own
    divisor by _degrade_max_parallel_for_wave itself — calling it twice in
    a row with the same (unchanged) slice info is idempotent rather than
    ratcheting the concurrency down further each call."""
    monkeypatch.setattr(leerie, "_cgroup_slice_info",
                         lambda: (60 * 1024**3, 12))
    first = leerie._degrade_max_parallel_for_wave(5)
    second = leerie._degrade_max_parallel_for_wave(first)
    assert second == first
