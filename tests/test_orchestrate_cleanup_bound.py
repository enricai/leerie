"""Bounded wall-clock regression test for `_orchestrate()`'s `finally` block
(leerie:32599-32660).

`_orchestrate()` starts `sampler_task` (`_memory_sampler`) and `reaper_task`
(`_zombie_reaper`) before `_run_phases`, and — when
`--dangerously-force-strict-output` is active — a `_StrictOutputProxy`. All
three are torn down in the `finally`: the two tasks are cancelled and
awaited, and the proxy's `stop()` is called (`_pool.shutdown(wait=False,
cancel_futures=True)`). Nothing previously exercised this under an
adversarial slow-coroutine condition with a wall-clock assertion — a `while
True: await asyncio.sleep(interval)` background task that swallowed
`asyncio.CancelledError` (e.g. via a bare `except:` or `except
BaseException:`) would hang `_orchestrate()` forever on every run, and no
test would catch it.

Both real background coroutines are replaced with test doubles that sleep
far longer than the wall-clock ceiling below, so a hang shows up as an
actual timeout rather than being masked by a short default `interval_sec`.
"""
from __future__ import annotations

import asyncio
import contextlib
import signal
import time
from types import SimpleNamespace

import pytest

# `_orchestrate()`'s finally must always return well inside this many seconds
# whether `_run_phases` completes normally or raises -- the sampler/reaper
# tasks sleep for far longer than this, so only prompt cancellation makes
# the ceiling achievable.
_CEILING_SEC = 5.0

# A hard multiple of the ceiling used as a SIGALRM backstop (see
# `_hard_timeout` below) -- deliberately NOT `asyncio.wait_for`. Measured:
# the real `finally` awaits each background task inside
# `contextlib.suppress(asyncio.CancelledError)` (leerie:32605-32608); if that
# task's own cancellation handling is broken (swallows CancelledError and
# keeps sleeping -- exactly the regression class this file guards against),
# `asyncio.wait_for`'s own cancellation of the outer `_orchestrate()` task
# lands inside that same `suppress` block and gets silently swallowed too,
# so `asyncio.run(asyncio.wait_for(...))` hangs right along with the bug it
# was meant to catch. A `SIGALRM` fires at the interpreter level, independent
# of asyncio task cancellation, so it cannot be defeated by the same defect.
_HARD_TIMEOUT_SEC = int(_CEILING_SEC * 4)


@contextlib.contextmanager
def _hard_timeout(seconds: int):
    def _on_alarm(_signum, _frame):
        raise TimeoutError(f"hard SIGALRM timeout after {seconds}s")

    previous = signal.signal(signal.SIGALRM, _on_alarm)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


def _make_slow_forever(entered: list):
    """Builds a stand-in for `_memory_sampler` / `_zombie_reaper`: sleeps far
    past the ceiling, so only a real `.cancel()` + await lets `_orchestrate()`
    return in time. Appends to `entered` right before the first sleep so the
    caller can assert the double actually started running (and was caught
    mid-sleep) rather than being cancelled before the event loop ever gave it
    a turn -- a task cancelled pre-start "succeeds" trivially regardless of
    its cancellation handling, which would make the assertion below vacuous."""
    async def _slow_forever(*_a, **_k) -> None:
        while True:
            entered.append(1)
            await asyncio.sleep(9999)
    return _slow_forever


def _args(**overrides) -> SimpleNamespace:
    base = dict(dangerously_force_strict_output=False)
    base.update(overrides)
    return SimpleNamespace(**base)


def _caps(leerie, **overrides) -> dict:
    caps = dict(leerie.DEFAULT_CAPS)
    caps["force_strict_output"] = False
    caps.update(overrides)
    return caps


@pytest.fixture(autouse=True)
def _reset_strict_proxy(leerie):
    """`_STRICT_PROXY` is a module global `_orchestrate()` sets and clears --
    reset it around each test so a failure in one test can't leak a live
    proxy/port into the next."""
    yield
    leerie._STRICT_PROXY = None


def _patched(leerie, monkeypatch) -> list:
    """Installs the sampler/reaper doubles and returns the shared `entered`
    list so callers can assert both doubles actually ran before being
    cancelled."""
    entered: list = []
    monkeypatch.setattr(leerie, "_memory_sampler", _make_slow_forever(entered))
    monkeypatch.setattr(leerie, "_zombie_reaper", _make_slow_forever(entered))
    return entered


async def _yield_to_background_tasks() -> None:
    """Stand-in body for a stubbed `_run_phases`: gives the event loop a few
    turns so `sampler_task`/`reaper_task` (scheduled but not yet run at the
    point `_run_phases` is awaited) actually enter their loop and reach
    `asyncio.sleep(9999)` before `_orchestrate()`'s `finally` cancels them.
    Real `_run_phases` awaits many things internally, so this mirrors that
    rather than special-casing the test."""
    for _ in range(5):
        await asyncio.sleep(0)


def _run_bounded(coro) -> float:
    """Runs `coro` under the SIGALRM backstop and returns the wall-clock
    elapsed time. Fails cleanly on a genuine hang instead of blocking the
    suite forever."""
    start = time.monotonic()
    try:
        with _hard_timeout(_HARD_TIMEOUT_SEC):
            asyncio.run(coro)
    except TimeoutError:
        pytest.fail(
            f"_orchestrate() did not return within {_HARD_TIMEOUT_SEC}s -- "
            f"sampler/reaper/proxy cancellation is hanging")
    return time.monotonic() - start


def _run_bounded_expect_raise(coro, exc_type) -> float:
    """Like `_run_bounded`, but expects `coro` to raise `exc_type` rather
    than return."""
    start = time.monotonic()
    try:
        with _hard_timeout(_HARD_TIMEOUT_SEC):
            asyncio.run(coro)
    except TimeoutError:
        pytest.fail(
            f"_orchestrate() did not propagate {exc_type.__name__} within "
            f"{_HARD_TIMEOUT_SEC}s -- sampler/reaper/proxy cancellation is "
            f"hanging")
    except exc_type:
        return time.monotonic() - start
    else:
        pytest.fail(f"expected {exc_type.__name__} to propagate")


def test_orchestrate_returns_promptly_when_run_phases_succeeds(
        leerie, monkeypatch, tmp_path):
    entered = _patched(leerie, monkeypatch)

    async def _ok(*_a, **_k) -> None:
        await _yield_to_background_tasks()

    monkeypatch.setattr(leerie, "_run_phases", _ok)

    elapsed = _run_bounded(leerie._orchestrate(
        _args(), _caps(leerie), tmp_path, SimpleNamespace(),
        "both", "quiet", {}, {}))

    assert entered, ("sampler/reaper doubles never entered their sleep -- "
                     "cancelled before the event loop scheduled them, so "
                     "this run proves nothing about their cancellation "
                     "handling")
    assert elapsed < _CEILING_SEC, (
        f"_orchestrate() took {elapsed:.2f}s to return after a normal "
        f"_run_phases completion -- sampler/reaper cancellation is hanging")


def test_orchestrate_returns_promptly_when_run_phases_raises(
        leerie, monkeypatch, tmp_path):
    entered = _patched(leerie, monkeypatch)

    async def _boom(*_a, **_k) -> None:
        await _yield_to_background_tasks()
        raise leerie.WorkerError("simulated worker failure")

    monkeypatch.setattr(leerie, "_run_phases", _boom)

    elapsed = _run_bounded_expect_raise(leerie._orchestrate(
        _args(), _caps(leerie), tmp_path, SimpleNamespace(),
        "both", "quiet", {}, {}), leerie.WorkerError)

    assert entered, ("sampler/reaper doubles never entered their sleep -- "
                     "cancelled before the event loop scheduled them, so "
                     "this run proves nothing about their cancellation "
                     "handling")
    assert elapsed < _CEILING_SEC, (
        f"_orchestrate() took {elapsed:.2f}s to propagate a _run_phases "
        f"exception -- sampler/reaper cancellation is hanging")


def test_orchestrate_stops_strict_proxy_promptly_on_success(
        leerie, monkeypatch, tmp_path):
    """Covers the `force_strict_output=True` variant: the finally must also
    start and stop a real `_StrictOutputProxy` (leerie:15181-15196) within
    the same ceiling."""
    entered = _patched(leerie, monkeypatch)

    async def _ok(*_a, **_k) -> None:
        await _yield_to_background_tasks()

    monkeypatch.setattr(leerie, "_run_phases", _ok)

    elapsed = _run_bounded(leerie._orchestrate(
        _args(dangerously_force_strict_output=True),
        _caps(leerie, force_strict_output=True, max_parallel=2), tmp_path,
        SimpleNamespace(), "both", "quiet", {}, {}))

    assert entered, "sampler/reaper doubles never entered their sleep"
    assert elapsed < _CEILING_SEC, (
        f"_orchestrate() took {elapsed:.2f}s to return with "
        f"force_strict_output=True -- _StrictOutputProxy.stop() is hanging")
    assert leerie._STRICT_PROXY is None, (
        "_orchestrate() must clear the module-global proxy in its finally")


def test_orchestrate_stops_strict_proxy_promptly_on_raise(
        leerie, monkeypatch, tmp_path):
    entered = _patched(leerie, monkeypatch)

    async def _boom(*_a, **_k) -> None:
        await _yield_to_background_tasks()
        raise leerie.WorkerError("simulated worker failure")

    monkeypatch.setattr(leerie, "_run_phases", _boom)

    elapsed = _run_bounded_expect_raise(leerie._orchestrate(
        _args(dangerously_force_strict_output=True),
        _caps(leerie, force_strict_output=True, max_parallel=2),
        tmp_path, SimpleNamespace(), "both", "quiet", {}, {}),
        leerie.WorkerError)

    assert entered, "sampler/reaper doubles never entered their sleep"
    assert elapsed < _CEILING_SEC, (
        f"_orchestrate() took {elapsed:.2f}s to propagate a _run_phases "
        f"exception with force_strict_output=True -- proxy teardown is "
        f"hanging")
    assert leerie._STRICT_PROXY is None
