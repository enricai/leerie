"""A worktree-creation failure must fail ONE subtask, not the whole run.

`_run_implementer` raises `WorktreeSetupError` (a `WorkerError` subclass)
when `new-worktree.sh` returns
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
import inspect

from tests.test_oom_naming import env  # noqa: F401  (pytest fixture)


def _stub_raising_run_implementer(leerie_mod, monkeypatch, calls):
    """Patch _run_implementer to raise the exact exception that
    `_run_script("new-worktree.sh", ...)` produces on a nonzero rc.

    MUST be `WorktreeSetupError`, not the base `WorkerError`. Production
    stopped raising the base type on 2026-08-05; a stub that still raises it
    lands on the generic `except WorkerError` arm — the terminal "broken"
    path production no longer takes for this failure — so every assertion
    below would pass while exercising a path that cannot occur."""
    async def _stub(sid_, leerie_dir, caps, st, models, efforts,
                    continuation=False, note=""):
        calls.append((sid_, continuation, note))
        raise leerie_mod.WorktreeSetupError(
            f"worktree creation failed for {sid_}: "
            f"fatal: '/leerie-state/runs/r/worktrees/{sid_}' already exists")
    monkeypatch.setattr(leerie_mod, "_run_implementer", _stub)


def _settle(leerie_mod, env, *, failed_retries=None):
    """Run `_settle_subtask`. `failed_retries` overrides the cap.

    The shared `env` fixture pins `failed_retries = 0`, which is right for the
    tests it was written for and wrong for any test asserting a RETRY: with a
    zero budget the retry can never happen and the assertion passes or fails
    for a reason that has nothing to do with the code under test. A test must
    set the variable it asserts on."""
    caps = dict(env["caps"])
    if failed_retries is not None:
        caps["failed_retries"] = failed_retries
    return asyncio.run(leerie_mod._settle_subtask(
        env["sid"], env["run_dir"], caps, env["st"],
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
    """The failure is persisted so resume and --report can see it."""
    leerie_mod = env["leerie"]
    calls: list[str] = []
    _stub_raising_run_implementer(leerie_mod, monkeypatch, calls)

    _settle(leerie_mod, env)

    assert env["st"].data["subtask_status"][env["sid"]] == "failed"


def test_worktree_failure_retries_but_is_bounded(env, monkeypatch):  # noqa: F811
    """It MUST retry, and the retry must be bounded.

    This test previously asserted the opposite — exactly one attempt — on the
    premise that "a stale worktree is not fixed by re-running the same
    worker". Run 488c42e5 (2026-08-05) falsified that premise: re-running is
    precisely what fixes it, because `new-worktree.sh` clears the orphaned
    directory and re-attaches to the existing branch. Treating it as terminal
    killed a wave with 25 of 26 subtasks complete and left `resume` hitting
    the same wall forever.

    Bounded, not unbounded: a stub that fails every time must still stop at
    `failed_retries + 1` attempts rather than spinning."""
    leerie_mod = env["leerie"]
    calls: list = []
    _stub_raising_run_implementer(leerie_mod, monkeypatch, calls)

    _settle(leerie_mod, env, failed_retries=2)

    assert len(calls) == 3, (
        f"expected 3 attempts (1 + failed_retries=2), got {len(calls)} — a "
        "worktree-setup failure is retryable infrastructure, not a broken "
        "worker")


def test_worktree_failure_respects_a_zero_retry_budget(env, monkeypatch):  # noqa: F811
    """ANTI-VACUITY for the test above: retryable does not mean unconditional.
    With no budget the subtask still terminates after one attempt, so the
    3-attempt assertion is measuring the cap, not an unbounded loop."""
    leerie_mod = env["leerie"]
    calls: list = []
    _stub_raising_run_implementer(leerie_mod, monkeypatch, calls)

    res = _settle(leerie_mod, env, failed_retries=0)

    assert len(calls) == 1
    assert res["status"] == "failed"


def test_infra_retry_preserves_the_pending_corrective_note(env, monkeypatch):  # noqa: F811
    """THE DESTRUCTIVE-FIX GUARD for the retry's payload.

    In run 488c42e5 the worktree failed while the mechanical-check path had
    already set `continuation=True` and a `_format_check_feedback` note naming
    the unmet criterion — the pending feedback the retry existed to deliver.
    `fail()` used to overwrite both unconditionally, so the retried worker
    restarted blind to the very thing it was sent back to fix, missed the same
    check again, and burned `implementer_confidence_retries`.

    An infrastructure failure carries no information about what the worker
    should do differently, so it must not overwrite the state that says what
    the worker should do differently."""
    leerie_mod = env["leerie"]
    calls: list = []

    pending_note = "UNMET_CRITERION: 'pnpm test roles/page.test.tsx passes'"
    first = {"done": False}

    async def _stub(sid_, leerie_dir, caps, st, models, efforts,
                    continuation=False, note=""):
        calls.append((sid_, continuation, note))
        raise leerie_mod.WorktreeSetupError(
            f"worktree creation failed for {sid_}: fatal: already exists")

    monkeypatch.setattr(leerie_mod, "_run_implementer", _stub)
    _settle(leerie_mod, env, failed_retries=2)
    del first, pending_note

    assert len(calls) > 1, "precondition: a retry must have happened"
    _, cont_retry, note_retry = calls[1]
    assert "worktree creation failed" not in note_retry, (
        "the infrastructure error leaked into the worker's corrective note; "
        "an infra failure must not overwrite what the worker is told to fix")
    assert note_retry == calls[0][2], (
        f"the note changed across an infrastructure retry: "
        f"{calls[0][2]!r} -> {note_retry!r}")
    assert cont_retry == calls[0][1], (
        "the continuation flag changed across an infrastructure retry")


def test_worker_failure_still_gets_a_corrective_note(env, monkeypatch):  # noqa: F811
    """ANTI-VACUITY: the exemption must not leak to WORKER failures, where
    the note is the entire mechanism by which the retry does better."""
    leerie_mod = env["leerie"]
    assert "no_commits" not in leerie_mod._INFRASTRUCTURE_FAILURE_KINDS
    src = inspect.getsource(leerie_mod._settle_subtask)
    i = src.index("_INFRASTRUCTURE_FAILURE_KINDS")
    j = src.index('note = f"Previous attempt failed:')
    assert i < j, "the note assignment must sit inside the worker-failure arm"
