"""A worktree-creation failure must fail ONE subtask, not the whole run.

`_run_implementer` raises `WorkerError` when `new-worktree.sh` returns
nonzero (e.g. an orphaned worktree dir makes `git worktree add` refuse).
Its own `except WorkerError` guard wraps only `claude_p`, so a `_run_script`
failure escapes it. Left uncaught in `_settle_subtask` it reaches
`_gather_or_cancel`, which cancels the wave and takes down the entire run —
discarding every sibling's committed work and skipping finalize.

That contradicts DESIGN §3 *Partial-wave integration*, where a wave collects
`failed`/`blocked` results and integrates the successes first. These tests
pin that the raise is caught and routed through the same per-subtask `fail()`
channel every other failure uses.

Observed in the wild: one subtask tripped a mechanical check, re-entered the
continuation path, hit `fatal: '<path>' already exists`, and killed a run
that had two subtasks' worth of correct committed work.

The `env` fixture is reused from test_oom_naming — it builds a real git repo
plus a `.leerie` run dir and State, which is exactly the ground _settle_subtask
needs.
"""
from __future__ import annotations

import asyncio

from tests.test_oom_naming import env  # noqa: F401  (pytest fixture)


def _stub_raising_run_implementer(leerie_mod, monkeypatch, calls):
    """Patch _run_implementer to raise the exact WorkerError that
    `_run_script("new-worktree.sh", ...)` produces on a nonzero rc."""
    async def _stub(sid_, leerie_dir, caps, st, models, efforts,
                    continuation=False, note=""):
        calls.append(sid_)
        raise leerie_mod.WorkerError(
            f"worktree creation failed for {sid_}: "
            f"fatal: '/leerie-state/runs/r/worktrees/{sid_}' already exists")
    monkeypatch.setattr(leerie_mod, "_run_implementer", _stub)


def _settle(leerie_mod, env):
    return asyncio.run(leerie_mod._settle_subtask(
        env["sid"], env["run_dir"], env["caps"], env["st"],
        env["models"], env["efforts"]))


def test_worktree_failure_returns_failed_instead_of_raising(env, monkeypatch):  # noqa: F811
    """_settle_subtask returns a `failed` result; the WorkerError must not
    escape to the wave runner (which would kill the run)."""
    leerie_mod = env["leerie"]
    calls: list[str] = []
    _stub_raising_run_implementer(leerie_mod, monkeypatch, calls)

    res = _settle(leerie_mod, env)

    assert res is not None, "_settle_subtask must return a result, not raise"
    assert res["status"] == "failed", f"expected a failed result, got {res!r}"
    assert "worktree creation failed" in res["summary"], (
        "the diagnosis must survive into the result so the operator can see "
        f"why the subtask died: {res!r}")


def test_worktree_failure_is_recorded_in_state(env, monkeypatch):  # noqa: F811
    """The failure is persisted so --resume and --report can see it."""
    leerie_mod = env["leerie"]
    calls: list[str] = []
    _stub_raising_run_implementer(leerie_mod, monkeypatch, calls)

    _settle(leerie_mod, env)

    assert env["st"].data["subtask_status"][env["sid"]] == "failed"


def test_worktree_failure_does_not_retry_forever(env, monkeypatch):  # noqa: F811
    """`broken` is non-retryable: a stale worktree is not fixed by re-running
    the same worker, so the subtask terminates on the first attempt rather
    than burning the retry budget on a deterministic failure."""
    leerie_mod = env["leerie"]
    calls: list[str] = []
    _stub_raising_run_implementer(leerie_mod, monkeypatch, calls)

    _settle(leerie_mod, env)

    assert len(calls) == 1, (
        f"expected exactly one attempt for a non-retryable failure, "
        f"got {len(calls)}")
