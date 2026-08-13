"""Concurrent orchestrator-run BLT measurements are bounded (DESIGN §6).

A build/lint/test command the orchestrator starts is NOT a worker: it does not
pass through `_await_worker_memory_admission` and it is not enrolled in a
cgroup, so it has no `memory.max` and no `pids.max` of its own. Under
`--subtask-tests full` that puts a whole suite behind every subtask, and
`max_parallel` of those at once is the shape that saturated a worker cgroup
badly enough to raise `worker_pids_max` to 2048 — except uncontained.

`blt_parallel` is what keeps that multiplier off. The load-bearing test here is
not "peak <= cap" on its own — a fully serialised implementation satisfies that
trivially — but the pair with `test_raising_the_cap_raises_the_peak`.
"""
from __future__ import annotations

import asyncio
import subprocess
import types

import pytest


@pytest.fixture(autouse=True)
def _reset_sem(leerie):
    """`_BLT_SEM` is module-level and conftest's `leerie` fixture is
    session-scoped, so a cap from one test would otherwise size the gate for
    every later one — the same leak `_active_admissions` documents."""
    leerie._BLT_SEM = None
    leerie._BLT_SEM_LIMIT = None
    yield
    leerie._BLT_SEM = None
    leerie._BLT_SEM_LIMIT = None


def _repo(tmp_path, name):
    d = tmp_path / name
    d.mkdir()
    for a in (["init", "-q"], ["config", "user.email", "t@e.com"],
              ["config", "user.name", "t"]):
        subprocess.run(["git", *a], cwd=d, check=True, capture_output=True)
    (d / "a.txt").write_text(name)
    subprocess.run(["git", "add", "-A"], cwd=d, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "i"], cwd=d, check=True,
                   capture_output=True)
    return d


def _peak_under(leerie, monkeypatch, tmp_path, cap, n=6, tag=""):
    """Run `n` concurrent measurements on distinct worktrees; return the peak
    number of BLT commands in flight at once."""
    state = {"cur": 0, "peak": 0}

    async def _fake_run_streaming(cmd, **kw):
        state["cur"] += 1
        state["peak"] = max(state["peak"], state["cur"])
        await asyncio.sleep(0.02)          # hold the slot long enough to overlap
        state["cur"] -= 1
        return (0, "ok")

    monkeypatch.setattr(leerie, "_run_streaming", _fake_run_streaming)
    leerie._DEPS_INSTALLED.clear()

    async def _go():
        trees = [_repo(tmp_path, f"wt{tag}{i}") for i in range(n)]
        sts = [types.SimpleNamespace(data={"provision": {"recipe": []}},
                                     save=lambda: None) for _ in trees]
        await asyncio.gather(*[
            leerie._measure_axes(str(t), {"tests": "pytest"}, st,
                                 {"blt_parallel": cap},
                                 log_path=None, verbosity="quiet")
            for t, st in zip(trees, sts)])

    asyncio.run(_go())
    return state["peak"]


def test_peak_concurrency_is_bounded_by_the_cap(leerie, monkeypatch, tmp_path):
    assert _peak_under(leerie, monkeypatch, tmp_path, cap=2) <= 2


def test_raising_the_cap_raises_the_peak(leerie, monkeypatch, tmp_path):
    """ANTI-VACUITY PARTNER. Without it, the bound test passes against an
    implementation that serialises everything — which is not the fix, it is a
    different (and slower) bug."""
    low = _peak_under(leerie, monkeypatch, tmp_path, cap=1, tag="a")
    high = _peak_under(leerie, monkeypatch, tmp_path, cap=4, tag="b")
    assert low == 1
    assert high > low, (
        f"cap=4 peaked at {high}, same as cap=1 — the gate is serialising "
        "rather than bounding")


def test_a_memo_hit_does_not_take_the_gate(leerie, monkeypatch, tmp_path):
    """A recorded verdict costs nothing, so it must not queue behind a
    running suite. Held only around the command, never the lookup."""
    calls = []

    async def _fake(cmd, **kw):
        calls.append(cmd)
        return (0, "ok")

    monkeypatch.setattr(leerie, "_run_streaming", _fake)
    leerie._DEPS_INSTALLED.clear()
    d = _repo(tmp_path, "wt")
    st = types.SimpleNamespace(data={"provision": {"recipe": []}},
                               save=lambda: None)

    async def _go():
        await leerie._measure_axes(str(d), {"tests": "pytest"}, st,
                                   {"blt_parallel": 1},
                                   log_path=None, verbosity="quiet")
        # Second call is a hit; with the gate wrongly wrapping the lookup this
        # would still acquire (and, at cap=1, still complete — so the real
        # assertion is the call count, not a timing one).
        await leerie._measure_axes(str(d), {"tests": "pytest"}, st,
                                   {"blt_parallel": 1},
                                   log_path=None, verbosity="quiet")

    asyncio.run(_go())
    assert len(calls) == 1


def test_the_cap_has_a_default_and_a_floor(leerie):
    assert leerie.DEFAULT_CAPS["blt_parallel"] == 2
    # A caps dict missing the key, or carrying 0, must not deadlock.
    assert leerie._blt_semaphore({})._value >= 1
    leerie._BLT_SEM = None
    assert leerie._blt_semaphore({"blt_parallel": 0})._value >= 1


def test_the_gate_is_resized_when_the_cap_changes(leerie):
    """A stale semaphore from an earlier cap would silently ignore the new
    one — the same class as a memo keyed on the wrong thing."""
    a = leerie._blt_semaphore({"blt_parallel": 2})
    b = leerie._blt_semaphore({"blt_parallel": 5})
    assert a is not b
    assert b._value == 5
