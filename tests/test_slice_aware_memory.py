"""Tests for slice-aware worker memory: the per-worker `memory.max` ceiling
(`_worker_memory_ceiling`) and the admission gate that blocks spawning a
worker while the shared `leerie.slice` lacks measured headroom for another
build.

The superseded design divided the slice budget across a projected worker
count (`slice_max // (live_siblings + max_parallel + 1)`). That treated a
ceiling as a reservation and issued caps *below* the measured build peak —
guaranteeing the in-cgroup OOM the cap exists to prevent (measured live:
4.58 GiB/worker) — and double-counted this run, since `live_siblings` is
slice-wide and already includes the run's own workers. The two properties
that kill that whole class are pinned here: the ceiling is **never below
the build peak** and is **independent of load**.

Mirrors tests/test_cgroup_helpers.py's stubbed-socket-broker pattern: stub
`_cgroup_request` (the broker round-trip) rather than a real cgroup tree.
"""
from __future__ import annotations

import asyncio
import inspect

import pytest


# The measured live host values these tests are calibrated against.
_SLICE_54_9_GIB = 58986594304
_UNRECLAIMABLE_ROOMY = 8 * 1024**3


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


async def _async_noop(_s):
    return None


# ---- _cgroup_slice_info: broker client -------------------------------------

def test_slice_info_parses_ok_triple(leerie, monkeypatch):
    sent = _stub_broker(leerie, monkeypatch, "OK 58956849152 12 8589934592")
    assert leerie._cgroup_slice_info() == (58956849152, 12, 8589934592)
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
    _stub_broker(leerie, monkeypatch, "OK 1 2")  # the OLD 3-field shape
    assert leerie._cgroup_slice_info() is None
    _stub_broker(leerie, monkeypatch, "OK a b c")  # non-integer
    assert leerie._cgroup_slice_info() is None


def test_slice_info_none_when_no_configured_ceiling(leerie, monkeypatch):
    """The broker reports -1 for memory.max when the slice has no
    configured ceiling — treat as "no shared budget known", not
    unlimited."""
    _stub_broker(leerie, monkeypatch, "OK -1 3 100")
    assert leerie._cgroup_slice_info() is None


def test_slice_info_passes_through_unknown_unreclaimable(leerie, monkeypatch):
    """-1 in the third field means "could not read"; the client must
    surface it rather than coercing it to 0 (which would read as "the
    slice is entirely free" and defeat the gate)."""
    _stub_broker(leerie, monkeypatch, "OK 58956849152 3 -1")
    assert leerie._cgroup_slice_info() == (58956849152, 3, -1)


# ---- _worker_memory_ceiling: a ceiling, not a share ------------------------

def test_ceiling_never_below_build_peak(leerie):
    """THE regression pin. The superseded divisor issued 4.58 GiB/worker
    against a 6.3 GiB build peak, guaranteeing an in-cgroup OOM. No slice
    size — however small or crowded — may produce a sub-peak ceiling."""
    peak = leerie._WORKER_BUILD_PEAK_BYTES
    for slice_max in (0, 1024, 1 * 1024**3, 8 * 1024**3, 16 * 1024**3,
                      _SLICE_54_9_GIB, 512 * 1024**3):
        assert leerie._worker_memory_ceiling(slice_max) >= peak, slice_max


def test_ceiling_independent_of_load(leerie):
    """Kills the double-count AND the frozen-sample class at once: the
    ceiling is a function of the slice budget alone. Structural, because a
    load-dependent ceiling could not even be expressed through this
    signature."""
    params = list(inspect.signature(leerie._worker_memory_ceiling)
                  .parameters)
    assert params == ["slice_max_bytes"], (
        "ceiling must not accept live_siblings/max_parallel — taking them "
        "at all is how the reservation semantics crept back in")
    # And it is genuinely deterministic in that one input.
    assert (leerie._worker_memory_ceiling(_SLICE_54_9_GIB)
            == leerie._worker_memory_ceiling(_SLICE_54_9_GIB))


def test_ceiling_bounded_by_half_the_slice_when_that_beats_the_peak(leerie):
    """One worker is never licensed to eat the whole fleet's budget — on a
    slice big enough for that bound to still clear the build peak."""
    big = 512 * 1024**3
    assert leerie._worker_memory_ceiling(big) <= big // 2


def test_ceiling_build_peak_outranks_half_slice_bound(leerie):
    """On a slice too small to honour both, the build peak wins: a
    memory.max above the slice is harmless (the aggregate cap binds first),
    one below the peak guarantees the OOM."""
    small = 8 * 1024**3          # half = 4 GiB, under the 6.3 GiB peak
    assert leerie._worker_memory_ceiling(small) == \
        leerie._WORKER_BUILD_PEAK_BYTES


def test_ceiling_on_the_measured_host(leerie):
    """Sanity against the real host: ~9.4 GiB, comfortably over the peak
    and in the neighbourhood of the 10.5 GiB legacy value that worked."""
    got = leerie._worker_memory_ceiling(_SLICE_54_9_GIB)
    assert 9.0 * 1024**3 < got < 10.0 * 1024**3


# ---- _auto_worker_memory_max: ceiling basis, NOT /proc/meminfo -----------

def test_auto_memory_max_uses_ceiling_when_slice_available(leerie, monkeypatch):
    """Falsify by reverting to the /proc/meminfo basis (e.g. deleting the
    _cgroup_slice_info() check) and confirm this fails."""
    monkeypatch.setattr(leerie, "_cgroup_slice_info",
                        lambda: (60 * 1024**3, 12, _UNRECLAIMABLE_ROOMY))
    # Sabotage the /proc/meminfo fallback so the test fails loudly if
    # _auto_worker_memory_max ever falls through to it despite slice info
    # being available.
    monkeypatch.setattr(leerie, "_auto_worker_memory_max_legacy",
                        lambda max_parallel: (_ for _ in ()).throw(
                            AssertionError("legacy basis must not be used "
                                           "when slice info is available")))
    assert leerie._auto_worker_memory_max(max_parallel=5) == \
        leerie._worker_memory_ceiling(60 * 1024**3)


def test_auto_memory_max_ignores_live_sibling_count(leerie, monkeypatch):
    """The resolved cap must not move with slice-wide concurrency. Under
    the divisor, 0 vs 20 live siblings changed the answer by ~4x and pinned
    a run that started during a busy moment to a starvation cap for its
    entire life (the cap resolves once, in main())."""
    seen = []

    def at(live):
        monkeypatch.setattr(
            leerie, "_cgroup_slice_info",
            lambda: (_SLICE_54_9_GIB, live, _UNRECLAIMABLE_ROOMY))
        return leerie._auto_worker_memory_max(max_parallel=5)

    for live in (0, 3, 6, 20):
        seen.append(at(live))
    assert len(set(seen)) == 1, f"cap varied with live siblings: {seen}"


def test_auto_memory_max_ignores_max_parallel(leerie, monkeypatch):
    """Same class, other half of the double-count."""
    monkeypatch.setattr(leerie, "_cgroup_slice_info",
                        lambda: (_SLICE_54_9_GIB, 3, _UNRECLAIMABLE_ROOMY))
    caps = {leerie._auto_worker_memory_max(max_parallel=mp)
            for mp in (1, 5, 12)}
    assert len(caps) == 1


def test_auto_memory_max_falls_back_to_legacy_when_no_slice_info(
        leerie, monkeypatch):
    """No broker / containment off: falls back to the /proc/meminfo-derived
    basis rather than failing."""
    monkeypatch.setattr(leerie, "_cgroup_slice_info", lambda: None)
    sentinel = 7 * 1024**3
    monkeypatch.setattr(leerie, "_auto_worker_memory_max_legacy",
                        lambda max_parallel: sentinel)
    assert leerie._auto_worker_memory_max(max_parallel=5) == sentinel


# ---- _await_worker_memory_admission: gates on headroom, not headcount ------

def test_admission_returns_immediately_when_no_slice_info(leerie, monkeypatch):
    """Containment off / no broker: nothing to gate against."""
    consulted = []
    monkeypatch.setattr(leerie, "_cgroup_slice_info",
                        lambda: consulted.append(1))
    slept = []
    monkeypatch.setattr(asyncio, "sleep", lambda s: slept.append(s))
    asyncio.run(leerie._await_worker_memory_admission())
    assert slept == []
    assert consulted, "gate must actually consult the broker, not no-op"


def test_admission_returns_immediately_when_headroom_above_peak(
        leerie, monkeypatch):
    monkeypatch.setattr(leerie, "_cgroup_slice_info",
                        lambda: (_SLICE_54_9_GIB, 2, _UNRECLAIMABLE_ROOMY))
    slept = []
    monkeypatch.setattr(asyncio, "sleep", lambda s: slept.append(s))
    asyncio.run(leerie._await_worker_memory_admission())
    assert slept == []


def test_admission_gates_on_headroom_not_worker_count(leerie, monkeypatch):
    """The behavioural heart of the fix. Twenty live workers with the slice
    barely touched must admit instantly; one live worker with the slice
    genuinely full must block. Under the divisor the first case stalled for
    the full 600s — that is the ~620s floor this fixes."""
    slept = []
    monkeypatch.setattr(asyncio, "sleep", _async_noop)

    # Crowded but roomy -> admit with no wait.
    monkeypatch.setattr(leerie, "_cgroup_slice_info",
                        lambda: (_SLICE_54_9_GIB, 20, 2 * 1024**3))
    monkeypatch.setattr(asyncio, "sleep", lambda s: slept.append(s))
    asyncio.run(leerie._await_worker_memory_admission())
    assert slept == [], "many workers + free memory must not block"

    # Uncrowded but full -> must block until the budget is spent. The sleep
    # stub must RECORD: a non-recording stub makes this half pass whether
    # or not the gate blocked, which is precisely the half that proves the
    # fix.
    slept.clear()
    monkeypatch.setattr(
        leerie, "_cgroup_slice_info",
        lambda: (_SLICE_54_9_GIB, 1, _SLICE_54_9_GIB - 1024**3))

    async def recording_sleep(s):
        slept.append(s)

    monkeypatch.setattr(asyncio, "sleep", recording_sleep)
    asyncio.run(leerie._await_worker_memory_admission(
        poll_interval_sec=10.0, max_wait_sec=25.0))
    assert slept, "one worker + a full slice must block, not admit"
    assert sum(slept) >= 20.0, (
        f"must have waited out the budget before admitting: {slept}")


def test_admission_admits_when_unreclaimable_unknown(leerie, monkeypatch):
    """-1 means the broker could not read memory.stat. Unknown fails OPEN,
    matching the whole-tuple None contract — a read error must not wedge
    every spawn for 10 minutes apiece."""
    monkeypatch.setattr(leerie, "_cgroup_slice_info",
                        lambda: (_SLICE_54_9_GIB, 3, -1))
    slept = []
    monkeypatch.setattr(asyncio, "sleep", lambda s: slept.append(s))
    asyncio.run(leerie._await_worker_memory_admission())
    assert slept == []


def test_admission_blocks_then_admits_once_memory_frees_up(leerie, monkeypatch):
    """The core admission-queue contract: re-polls while the slice is full
    and proceeds the moment it is not."""
    calls = {"n": 0}

    def fake_slice_info():
        calls["n"] += 1
        if calls["n"] < 3:
            return (_SLICE_54_9_GIB, 12, _SLICE_54_9_GIB - 1024**3)
        return (_SLICE_54_9_GIB, 12, 2 * 1024**3)

    monkeypatch.setattr(leerie, "_cgroup_slice_info", fake_slice_info)
    slept = []

    async def fake_sleep(s):
        slept.append(s)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    asyncio.run(leerie._await_worker_memory_admission(
        poll_interval_sec=1.0, max_wait_sec=100.0))
    assert len(slept) == 2  # blocked for exactly the two saturated polls
    assert calls["n"] == 3


def test_admission_blocks_rather_than_admitting_into_a_full_slice(
        leerie, monkeypatch):
    """Anti-vacuity for the gate itself: a permanently full slice must
    actually wait, proving admission is gated rather than a no-op that
    always returns."""
    monkeypatch.setattr(
        leerie, "_cgroup_slice_info",
        lambda: (_SLICE_54_9_GIB, 12, _SLICE_54_9_GIB - 1024**3))
    slept = []

    async def fake_sleep(s):
        slept.append(s)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    asyncio.run(leerie._await_worker_memory_admission(
        poll_interval_sec=10.0, max_wait_sec=25.0))
    assert len(slept) >= 2
    assert sum(slept) >= 20.0


def test_admission_gives_up_after_max_wait_and_admits_anyway(
        leerie, monkeypatch):
    """Bounded wait: never hang indefinitely even if the slice never frees
    (a long-running sibling that never releases its memory)."""
    monkeypatch.setattr(
        leerie, "_cgroup_slice_info",
        lambda: (_SLICE_54_9_GIB, 12, _SLICE_54_9_GIB - 1024**3))
    monkeypatch.setattr(asyncio, "sleep", _async_noop)
    asyncio.run(asyncio.wait_for(
        leerie._await_worker_memory_admission(
            poll_interval_sec=1.0, max_wait_sec=3.0),
        timeout=5.0))


def test_admission_signature_takes_no_max_parallel(leerie):
    """max_parallel was an input to the divisor's arithmetic. It is now an
    arming signal at the call site only; accepting it here is how the
    projection-based gate would creep back."""
    assert "max_parallel" not in inspect.signature(
        leerie._await_worker_memory_admission).parameters
