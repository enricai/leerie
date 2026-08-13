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
    """Clear the per-loop gate cache around every test.

    It is keyed by running loop in a WeakKeyDictionary, so entries for finished
    loops drop on their own — but conftest's `leerie` fixture is session-scoped
    and a cap from one test would otherwise size the gate for any test that
    happens to share a loop. Same leak `_active_admissions` documents."""
    leerie._BLT_SEMS.clear()
    yield
    leerie._BLT_SEMS.clear()


def _tree(tmp_path, name):
    """A plain directory, deliberately NOT a git repo.

    `_worktree_tree_sha` then returns None, the memo is off, and every call
    actually measures — which is exactly what a concurrency test wants. Using
    real repos here cost ~18 `git init` + `commit` pairs for nothing the
    assertions depend on, against a CI job capped at 10 minutes.
    """
    d = tmp_path / name
    d.mkdir()
    return d


def _git_repo(tmp_path, name):
    """A real repo — needed ONLY where the memo must actually hit, since the
    key is `git rev-parse HEAD^{tree}`."""
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
        trees = [_tree(tmp_path, f"wt{tag}{i}") for i in range(n)]
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
    d = _git_repo(tmp_path, "wt")
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

    async def _go():
        # A caps dict missing the key, or carrying 0, must not deadlock.
        assert leerie._blt_semaphore({})._value >= 1
        leerie._BLT_SEMS.clear()
        assert leerie._blt_semaphore({"blt_parallel": 0})._value >= 1

    asyncio.run(_go())


def test_the_gate_is_resized_when_the_cap_changes(leerie):
    """A stale semaphore from an earlier cap would silently ignore the new
    one — the same class as a memo keyed on the wrong thing."""
    async def _go():
        a = leerie._blt_semaphore({"blt_parallel": 2})
        b = leerie._blt_semaphore({"blt_parallel": 5})
        assert a is not b
        assert b._value == 5

    asyncio.run(_go())


def test_the_gate_is_never_shared_across_event_loops(leerie):
    """REGRESSION PIN for the CI failure this file's own tests caused.

    A module-level cached `asyncio.Semaphore` outlives the loop it was created
    on. On Python <= 3.11 `_LoopBoundMixin._get_loop()` is consulted only on
    the contended path, so cross-loop reuse is silent while uncontended and
    blocks the moment two callers queue — green on 3.12, hung on 3.10/3.11.
    Keying on the running loop makes that unrepresentable.
    """
    seen = []

    async def _go():
        seen.append(leerie._blt_semaphore({"blt_parallel": 2}))

    asyncio.run(_go())          # loop A
    asyncio.run(_go())          # loop B
    assert seen[0] is not seen[1], (
        "the same Semaphore was handed to two different event loops")


def test_calling_outside_a_loop_is_refused(leerie):
    """Fails loudly rather than minting a loop-less semaphore that would
    later be reused across loops."""
    with pytest.raises(RuntimeError):
        leerie._blt_semaphore({"blt_parallel": 2})
