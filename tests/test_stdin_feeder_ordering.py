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

    Uses a REAL child process, which is the whole point. An earlier draft
    modelled the deadline with an in-process `asyncio.Event` + `wait_for`
    and was wrong in the one way that mattered: a blocked event loop also
    freezes the model's own timer, so whether the deadline or the write won
    came down to ready-queue ordering — which differs across CPython
    versions. It passed on 3.12+ and reported `GOT` for the two blocking
    variants on 3.10/3.11. The real CLI is a separate process whose 3 s
    clock runs regardless of what leerie's loop is doing; only a real
    subprocess reproduces that, and it does so identically on every version.

    Scaled down 10x from the real 3 s deadline to keep the test quick.
    """
    import asyncio
    import sys
    import time

    DEADLINE = 0.30          # stands in for the CLI's hard-coded 3 s
    BLOCK = 0.90             # stands in for a slow broker round-trip

    # Mirrors the CLI: wait for the first stdin byte until a deadline, then
    # stop listening. `select` gives the child its own OS-level clock.
    #
    # It announces READY before arming that clock, and the parent waits for
    # it, so interpreter startup cannot eat into the margin. Without that
    # handshake a slow runner shifts the child's deadline later than the
    # parent's block and the two TIMEOUT variants flip to GOT — the same
    # class of environment-dependence that made the previous draft of this
    # test fail on 3.10/3.11 while passing locally.
    CHILD = (
        "import sys, select\n"
        "print('READY', flush=True)\n"
        f"r, _, _ = select.select([sys.stdin], [], [], {DEADLINE})\n"
        "print('GOT' if r else 'TIMEOUT', flush=True)\n"
    )

    async def scenario():
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-c", CHILD,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE)
        ready = await asyncio.wait_for(proc.stdout.readline(), 30)
        assert ready.strip() == b"READY", ready

        async def feed():
            try:
                proc.stdin.write(b"x" * 1024)
                await proc.stdin.drain()
            except (BrokenPipeError, ConnectionResetError):
                pass                      # child already gone — the D case
            finally:
                try:
                    proc.stdin.close()
                except Exception:
                    pass

        feeder = asyncio.create_task(feed()) if hoisted else None
        if threaded:
            await asyncio.to_thread(time.sleep, BLOCK)
        else:
            time.sleep(BLOCK)             # blocks the whole event loop
        if feeder is None:
            feeder = asyncio.create_task(feed())

        out, _ = await asyncio.gather(proc.stdout.read(), feeder)
        await proc.wait()
        return out.decode().strip()

    got = asyncio.run(scenario())
    assert got == ("GOT" if expect_delivered else "TIMEOUT"), (
        f"hoisted={hoisted} threaded={threaded} -> {got}")
