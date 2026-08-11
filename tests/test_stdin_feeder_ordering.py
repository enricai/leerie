"""The worker's prompt must reach its stdin before anything can block the
event loop (DESIGN §6 *User prompt transport*).

`claude -p` waits a hard-coded **3 s** for its first stdin byte
(`KJr(process.stdin, 3000)` in the CLI bundle), then removes its own `data`
listener and proceeds without it — so a late write is DISCARDED, not buffered,
and the worker dies with `Input must be provided either through stdin or as a
prompt argument`. leerie used to make two synchronous broker round-trips
between the spawn and the first write, each bounded by `_cgroup_request`'s
**5 s** timeout. An accepted 5 s stall in front of a 3 s deadline permits the
failure by construction, and the comment at that site called it "negligible".

Measured across every run on one host before the fix: **218 workers lost this
way — 12.4% of all invocations in the affected runs** — retried up to 4x each,
every attempt charged to `max_total_workers`. It spans v0.9.95 through v0.16.0.
`_cgroup_enroll`'s own docstring records the same pair in a different run
(`870cf82c…`) as "two apparently-unconnected events"; they are one event.

**Both halves of the fix are load-bearing**, which is the point of this file.
A reproduction harness scored all four combinations against a child that
mimics the CLI's 3 s wait:

| variant | child saw |
|---|---|
| blocking call before the feeder (the old code) | TIMEOUT |
| feeder task created first, call still synchronous | **TIMEOUT** — the blocked loop never schedules it |
| call in a thread, feeder created afterwards | **TIMEOUT** — write lands after the child is gone |
| feeder task first AND call in a thread | GOT |

So neither half alone is a fix, and a future edit that keeps one while
dropping the other silently reopens a 12% budget leak.
"""
from __future__ import annotations

import ast
import inspect

import pytest


def _invoke_src(leerie) -> str:
    """`_invoke`'s source with COMMENTS REMOVED.

    Every scan in this file is about where statements sit relative to one
    another, and the region under test is heavily commented — with comments
    that necessarily name `_feed_stdin`, `await`, and `_cgroup_enroll` while
    explaining the ordering. A raw substring scan matches that prose and
    reports the ordering broken on correct code (it did, on the first draft
    of this file). Stripped via `tokenize` rather than a `#` heuristic so a
    `#` inside a string literal cannot corrupt the result.
    """
    import io
    import tokenize
    src = inspect.getsource(leerie._invoke)
    out, last_line, last_col = [], 1, 0
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.start[0] > last_line:
            out.append("\n" * (tok.start[0] - last_line))
            last_col = 0
        if tok.type == tokenize.COMMENT:
            last_line, last_col = tok.end
            continue
        if tok.start[1] > last_col:
            out.append(" " * (tok.start[1] - last_col))
        out.append(tok.string)
        last_line, last_col = tok.end
    return "".join(out)


# ---- ordering: the write is queued before the broker round-trips ----------

def test_feeder_task_is_created_before_the_cgroup_calls(leerie):
    """Source-ordered, because the real path needs a spawned child, a live
    broker socket and a 3-second race to observe behaviourally."""
    src = _invoke_src(leerie)
    i_feeder = src.index("asyncio.create_task(_feed_stdin())")
    i_create = src.index("_cgroup_create")
    i_enroll = src.index("_cgroup_enroll")
    assert i_feeder < i_create, "the feeder must be queued before `create`"
    assert i_feeder < i_enroll, "the feeder must be queued before `enroll`"


def test_feeder_is_created_immediately_after_the_spawn(leerie):
    """Nothing awaitable may sit between the spawn and the feeder.

    An `await` there yields the loop to whatever else is runnable — which in
    a wave of `max_parallel` workers is a sibling's broker round-trip — and
    the 3 s clock is already running on a child that exists.
    """
    src = _invoke_src(leerie)
    spawn = src.index("proc = await asyncio.create_subprocess_exec(")
    feeder = src.index("asyncio.create_task(_feed_stdin())")
    between = src[spawn:feeder]
    # The spawn's own `await` is the one on the first line of the slice.
    assert between.count("await ") == 1, (
        "an extra await sits between the spawn and the feeder:\n"
        + "\n".join(ln for ln in between.splitlines() if "await " in ln))


def test_cgroup_calls_do_not_block_the_event_loop(leerie):
    """`_cgroup_create` / `_cgroup_enroll` are synchronous socket
    round-trips; run inline they stall the WHOLE loop — every sibling
    worker's feeder included — for up to 5 s apiece."""
    src = _invoke_src(leerie)
    for fn in ("_cgroup_create", "_cgroup_enroll"):
        # Every CALL of it, not the first mention: `_cgroup_enroll` is also
        # named in a comment (stripped above) and assigned to a variable
        # (`cgroup_enroll_failure`), so anchor on the call syntax.
        calls = [i for i in range(len(src))
                 if src.startswith(fn + ",", i) or src.startswith(fn + "(", i)]
        assert calls, f"no call site found for {fn}"
        for i in calls:
            window = src[max(0, i - 80):i]
            assert "to_thread" in window, (
                f"{fn} is called inline and will block the event loop: "
                f"{src[max(0, i - 80):i + len(fn) + 40]!r}")


def test_gather_awaits_the_existing_task_not_a_second_call(leerie):
    """Calling `_feed_stdin()` again in the gather would spawn a SECOND
    writer against a stdin the first one already closed, and would leave the
    real feeder unawaited."""
    src = _invoke_src(leerie)
    assert "feeder, proc.wait()" in src
    assert "_feed_stdin(), proc.wait()" not in src
    # Count CALLS, not the definition: `async def _feed_stdin():` contains
    # `_feed_stdin()` as a substring, so a bare count reports 2 for correct
    # code (it did, on the first draft).
    calls = [i for i in range(len(src))
             if src.startswith("_feed_stdin()", i)
             and not src[:i].rstrip().endswith("def")]
    assert len(calls) == 1, \
        f"_feed_stdin() should be invoked exactly once, found {len(calls)}"
    assert src[:calls[0]].rstrip().endswith("asyncio.create_task("), \
        "the single invocation must be the create_task at the spawn"


def test_feed_stdin_is_defined_before_the_spawn(leerie):
    """It has to be, to be startable at the spawn — and it may close over
    nothing that is bound later. `proc` and `stdin_data` are the only two,
    and both are bound before the task first runs."""
    src = _invoke_src(leerie)
    assert src.index("async def _feed_stdin") < \
        src.index("proc = await asyncio.create_subprocess_exec(")

    # `_invoke` is module-level, so its source parses as-is — no dedent.
    tree = ast.parse(inspect.getsource(leerie._invoke))
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, ast.AsyncFunctionDef)
               and n.name == "_feed_stdin"), None)
    assert fn is not None
    free = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name)}
    assert free <= {"proc", "stdin_data", "BrokenPipeError",
                    "ConnectionResetError"}, \
        f"_feed_stdin closes over something new: {free}"


# ---- the property the ordering exists to protect --------------------------

@pytest.mark.parametrize("hoisted,threaded,expect_delivered", [
    (False, False, False),   # the old code
    (True,  False, False),   # hoist alone — blocked loop never runs the task
    (False, True,  False),   # to_thread alone — write lands too late
    (True,  True,  True),    # both
])
def test_only_both_halves_deliver_the_prompt_in_time(hoisted, threaded,
                                                     expect_delivered):
    """Behavioural proof that neither half alone suffices.

    Models the CLI faithfully in the one respect that matters: a consumer
    that gives up on stdin after a deadline and never looks again. Uses a
    plain `asyncio.Event` deadline rather than a real subprocess so the test
    is fast and platform-independent — the failure being reproduced is one
    of *scheduling*, not of process semantics.
    """
    import asyncio
    import time

    DEADLINE = 0.30          # stands in for the CLI's 3 s
    BLOCK = 0.45             # stands in for a slow broker round-trip

    async def scenario():
        delivered = asyncio.Event()
        started = time.monotonic()

        async def child():
            try:
                await asyncio.wait_for(delivered.wait(), DEADLINE)
                return "GOT"
            except asyncio.TimeoutError:
                return "TIMEOUT"          # gives up, stops listening

        async def feed():
            delivered.set()

        child_task = asyncio.create_task(child())
        await asyncio.sleep(0)            # let the child start waiting

        feeder = asyncio.create_task(feed()) if hoisted else None
        if threaded:
            await asyncio.to_thread(time.sleep, BLOCK)
        else:
            time.sleep(BLOCK)             # blocks the whole loop
        if feeder is None:
            feeder = asyncio.create_task(feed())

        result = await child_task
        await feeder
        assert time.monotonic() - started >= BLOCK
        return result

    got = asyncio.run(scenario())
    assert got == ("GOT" if expect_delivered else "TIMEOUT"), (
        f"hoisted={hoisted} threaded={threaded} -> {got}")
