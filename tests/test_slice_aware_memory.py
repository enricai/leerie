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


# ---- _await_worker_memory_admission: admission gate ------------------------

def test_admission_returns_immediately_when_no_slice_info(leerie, monkeypatch):
    """Containment off / no broker: nothing to gate against, admit
    immediately (no sleep)."""
    monkeypatch.setattr(leerie, "_cgroup_slice_info", lambda: None)
    slept = []
    monkeypatch.setattr(asyncio, "sleep",
                        lambda s: slept.append(s) or _immediate())
    asyncio.run(leerie._await_worker_memory_admission(max_parallel=5))
    assert slept == []


def test_admission_returns_immediately_when_cap_above_build_peak(
        leerie, monkeypatch):
    monkeypatch.setattr(leerie, "_cgroup_slice_info",
                         lambda: (200 * 1024**3, 2))  # plenty of room
    slept = []
    monkeypatch.setattr(asyncio, "sleep",
                        lambda s: slept.append(s) or _immediate())
    asyncio.run(leerie._await_worker_memory_admission(
        max_parallel=5, build_peak_bytes=leerie._WORKER_BUILD_PEAK_BYTES))
    assert slept == []


def test_admission_blocks_then_admits_once_siblings_free_up(leerie, monkeypatch):
    """The core admission-queue contract: a cap below the build-peak floor
    makes the gate wait (sleep), re-polling until conditions improve."""
    calls = {"n": 0}

    def fake_slice_info():
        calls["n"] += 1
        # First two polls: saturated (12 live siblings -> well under peak).
        # Third poll: a sibling finished -> plenty of room.
        if calls["n"] < 3:
            return (60 * 1024**3, 12)
        return (60 * 1024**3, 0)

    monkeypatch.setattr(leerie, "_cgroup_slice_info", fake_slice_info)
    slept = []

    async def fake_sleep(s):
        slept.append(s)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    asyncio.run(leerie._await_worker_memory_admission(
        max_parallel=5, poll_interval_sec=1.0, max_wait_sec=100.0))
    assert len(slept) == 2  # blocked for exactly the two saturated polls
    assert calls["n"] == 3


def test_admission_blocks_rather_than_admitting_a_doomed_worker(
        leerie, monkeypatch):
    """Falsification: with a permanently saturated slice, the gate must
    keep waiting (not admit) until max_wait_sec is exhausted — proving
    admission is genuinely gated, not a no-op that always returns."""
    monkeypatch.setattr(leerie, "_cgroup_slice_info",
                         lambda: (60 * 1024**3, 12))  # never improves
    slept = []

    async def fake_sleep(s):
        slept.append(s)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    asyncio.run(leerie._await_worker_memory_admission(
        max_parallel=5, poll_interval_sec=10.0, max_wait_sec=25.0))
    # Waited until the budget was exhausted (3 polls: 10, 20, 30 >= 25) then
    # admitted anyway rather than hanging forever.
    assert len(slept) >= 2
    assert sum(slept) >= 20.0


def test_admission_gives_up_after_max_wait_and_admits_anyway(
        leerie, monkeypatch):
    """Bounded wait: never hang indefinitely even if the slice never frees
    up (a long-running sibling that never releases its slot)."""
    monkeypatch.setattr(leerie, "_cgroup_slice_info",
                         lambda: (60 * 1024**3, 12))
    monkeypatch.setattr(asyncio, "sleep", _async_noop)
    # Must return (not hang) within a bounded number of event-loop turns.
    asyncio.run(asyncio.wait_for(
        leerie._await_worker_memory_admission(
            max_parallel=5, poll_interval_sec=1.0, max_wait_sec=3.0),
        timeout=5.0))


async def _async_noop(_s):
    return None


async def _immediate():
    return None
