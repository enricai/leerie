"""The worker's prompt must reach its stdin before anything can block the
event loop (DESIGN §6 *User prompt transport*).

`claude -p` waits a hard-coded **3 s** for its first stdin byte
(`r6r(process.stdin,3000)` in the CLI bundle, read out of the shipped
binary), then removes its own `data`
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

**The feeder was never a complete fix, and this file now guards its
replacement.** Hoisting the write into a task and moving the broker calls off
the loop narrowed the window but could not close it: delivery still depended on
the event loop *scheduling* that task within 3 s. Under bursts of synchronous
work on the loop — the shape `_read_stream` produces when parsing several
workers' JSON at once — it lost every time. Measured A/B, identical parent,
only the transport varying:

| parent loop burst | pipe + feeder | staged file |
|---|---|---|
| 400 ms | **20/20 prompts LOST** | 0/20 lost |
| 900 ms | **20/20 prompts LOST** | 0/20 lost |
| 900 ms + 320 CPU hogs (load 97) | — | 0/20 lost |

Raw CPU contention alone never reproduced it (96 hogs on 32 cores: 0/40 lost on
*both* transports), so the mechanism is loop-blocking, not a busy host.

The fix is to remove the deadline as a dependency rather than race it: the
prompt is staged to a temp file before the spawn, so its first byte is readable
at `exec` and there is no writer left to schedule. What this file pins is that
property — a revert to any pipe-and-writer shape must fail here, because such a
shape is a race whether or not its ordering happens to be correct today.
"""
from __future__ import annotations

import ast
import asyncio
import inspect
import sys
import time

import pytest

# The real-child rationale, retained: an earlier draft modelled the deadline
# with an in-process `asyncio.Event` + `wait_for` and was wrong in the one way
# that mattered — a blocked event loop also freezes the model's own timer, so
# whether the deadline or the write won came down to ready-queue ordering,
# which differs across CPython versions. It passed on 3.12+ and reported GOT
# for the blocking variants on 3.10/3.11. The real CLI is a separate process
# whose 3 s clock runs regardless of what leerie's loop is doing; only a real
# subprocess reproduces that, and it does so identically on every version.
#
# The child announces READY before arming its clock and the parent waits for
# it, so interpreter startup cannot eat into the margin. Without that
# handshake a slow runner shifts the child's deadline past the parent's block
# and the TIMEOUT variant flips to GOT.
CHILD = (
    "import sys, select\n"
    "print('READY', flush=True)\n"
    "r, _, _ = select.select([sys.stdin], [], [], 0.30)\n"
    "print('GOT' if r else 'TIMEOUT', flush=True)\n"
)


def _invoke_src(leerie) -> str:
    """`_invoke`'s source with COMMENTS REMOVED.

    Every scan in this file is about where statements sit relative to one
    another, and the region under test is heavily commented — with comments
    that necessarily name the old feeder, `await`, and `_cgroup_enroll`
    while explaining the history. A raw substring scan matches that prose —
    and `test_no_writer_task_exists` below asserts a name is ABSENT, so an
    unstripped comment mentioning it fails on correct code (it did, on the
    first draft of this file). Stripped via `tokenize` rather than a `#` heuristic so a
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


# ---- the payload is on disk before the child exists -----------------------

def test_prompt_is_staged_before_the_spawn(leerie):
    """The write must complete BEFORE `create_subprocess_exec`.

    Staging it afterwards would recreate the race in a new shape: the child
    would exist, its 3 s clock running, while the parent still had work to do
    before the first byte was readable.
    """
    src = _invoke_src(leerie)
    i_stage = src.index("tempfile.mkstemp(")
    i_spawn = src.index("proc = await asyncio.create_subprocess_exec(")
    assert i_stage < i_spawn, (
        "the prompt file must be staged before the spawn, not after")


def test_no_writer_task_exists(leerie):
    """No coroutine may be responsible for delivering the prompt.

    This is the invariant, stated negatively because that is the form a
    regression takes: any reintroduced writer — however well ordered — makes
    delivery depend on the loop scheduling it inside the CLI's 3 s window,
    which is exactly what the measured A/B in this module's docstring shows
    losing 20/20.
    """
    src = _invoke_src(leerie)
    assert "_feed_stdin" not in src, (
        "a stdin feeder has been reintroduced; the prompt must be staged to "
        "a file before the spawn instead")
    assert "proc.stdin.write" not in src, (
        "nothing may write to the child's stdin after the spawn")


def test_stdin_is_never_a_pipe_when_a_prompt_is_given(leerie):
    """A PIPE is the mechanism the race needs; a file is what removes it."""
    src = _invoke_src(leerie)
    spawn = src.index("proc = await asyncio.create_subprocess_exec(")
    args = src[spawn:spawn + 1400]
    assert "stdin=(stdin_file" in args, (
        "the staged file must be handed to the child as its stdin")
    assert "subprocess.PIPE if stdin_data" not in args, (
        "stdin must not be a PIPE when a prompt is given")


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


def test_gather_awaits_no_stdin_task(leerie):
    """Nothing stdin-related may remain in the gather.

    A leftover name there would be either a dangling reference (crash) or a
    revived writer (the race). The gather's members are now exactly the three
    that outlive the spawn.
    """
    src = _invoke_src(leerie)
    assert "feeder, proc.wait()" not in src
    assert "_read_stream(), _drain_stderr()," in src
    assert "proc.wait()" in src


def test_staged_file_is_cleaned_up(leerie):
    """One 100KB+ file per worker invocation, so a leak is not academic — a
    long run makes thousands. Pinned structurally because the real path needs
    a spawned child to observe."""
    src = _invoke_src(leerie)
    assert "os.unlink(stdin_path)" in src, (
        "the staged prompt file must be unlinked")
    # It must sit in the teardown, not on the success path only: a worker that
    # times out or crashes leaks the file otherwise.
    tree = ast.parse(inspect.getsource(leerie._invoke))
    in_finally = any(
        "os.unlink(stdin_path)" in ast.unparse(h)
        for n in ast.walk(tree) if isinstance(n, ast.Try) for h in [n.finalbody]
        if h)
    assert in_finally, (
        "the unlink must run from a `finally`, so a timed-out or crashed "
        "worker cannot leak its prompt file")


# ---- the property the ordering exists to protect --------------------------

@pytest.mark.parametrize("transport,expect_delivered", [
    ("pipe", False),   # any writer loses when the loop is blocked
    ("file", True),    # a staged file has nothing left to schedule
])
def test_only_a_staged_file_survives_a_blocked_event_loop(transport,
                                                          expect_delivered):
    """The property the whole transport exists for.

    A blocked parent loop is not hypothetical: `_read_stream` parses JSON
    from up to `max_parallel` workers, and each burst of that work is time
    the loop cannot schedule anything else. With a pipe, the prompt is still
    undelivered during those bursts and the child's independent clock runs
    out. With a file it was delivered before the child existed.

    Scaled down 10x from the real 3 s deadline to keep the test quick; the
    full-scale A/B is in this module's docstring.
    """
    import os
    import tempfile

    DEADLINE = 0.30          # stands in for the CLI's hard-coded 3 s
    BLOCK = 0.90             # stands in for a burst of loop-blocking work

    async def scenario():
        if transport == "file":
            fd, path = tempfile.mkstemp()
            os.write(fd, b"x" * 1024)
            os.close(fd)
            f = open(path, "rb")
            try:
                proc = await asyncio.create_subprocess_exec(
                    sys.executable, "-c", CHILD,
                    stdin=f, stdout=asyncio.subprocess.PIPE)
                ready = await asyncio.wait_for(proc.stdout.readline(), 30)
                assert ready.strip() == b"READY", ready
                time.sleep(BLOCK)          # blocks the whole event loop
                out = await proc.stdout.read()
                await proc.wait()
            finally:
                f.close()
                os.unlink(path)
            return out.decode().strip()

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
                pass                      # child already gone
            finally:
                try:
                    proc.stdin.close()
                except Exception:
                    pass

        # Hoisted feeder AND nothing else awaited first — i.e. the most
        # favourable arrangement the pipe transport ever had. It still loses.
        feeder = asyncio.create_task(feed())
        time.sleep(BLOCK)
        out, _ = await asyncio.gather(proc.stdout.read(), feeder)
        await proc.wait()
        return out.decode().strip()

    got = asyncio.run(scenario())
    assert got == ("GOT" if expect_delivered else "TIMEOUT"), (
        f"transport={transport} -> {got}")
