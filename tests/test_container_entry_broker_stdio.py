"""bugfix-004: the cgroup broker launch line must not inherit PID 1's stdio.

container-entry.sh launches the cgroup broker with `python3 ... &`. A bare
background launch inherits PID 1's stdout/stderr, which can hold the
container's output pipe open past PID 1's own exit, delaying EOF on the
launcher's read side and contributing to the reported hang-on-exit. The
launch line must explicitly redirect stdin/stdout/stderr away from those
inherited fds.
"""
from __future__ import annotations

import os
import re
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ENTRY = REPO_ROOT / "scripts" / "container-entry.sh"


def _time_to_eof(redirect_child_stdio: bool) -> float:
    """Fork a 'PID 1' that backgrounds a long-lived child, then exits.

    Returns the wall-clock time for the pipe reader (standing in for the
    container runtime's log/output reader) to observe EOF after the parent
    ('PID 1') has exited. If the child does not redirect its own stdio away
    from the inherited pipe fd, it keeps the write end open and EOF is
    delayed until the child itself exits (~0.3s here); if it does redirect,
    EOF arrives promptly once the parent exits (~0s).
    """
    read_fd, write_fd = os.pipe()
    parent_pid = os.fork()
    if parent_pid == 0:
        # "PID 1": owns the write end, backgrounds a long-lived child, exits.
        os.close(read_fd)
        child_pid = os.fork()
        if child_pid == 0:
            # The long-lived background child (stands in for cgroup-broker.py).
            if redirect_child_stdio:
                os.close(write_fd)
                devnull = os.open(os.devnull, os.O_WRONLY)
                os.dup2(devnull, 1)
                os.close(devnull)
            time.sleep(0.3)
            os._exit(0)
        # "PID 1" exits immediately, orphaning the background child.
        os.close(write_fd)
        os._exit(0)

    os.close(write_fd)
    os.waitpid(parent_pid, 0)
    start = time.monotonic()
    os.read(read_fd, 1)
    elapsed = time.monotonic() - start
    os.close(read_fd)
    return elapsed


def test_unredirected_background_child_delays_pipe_eof_past_parent_exit():
    elapsed = _time_to_eof(redirect_child_stdio=False)
    assert elapsed > 0.1, (
        "expected an unredirected background child to hold the pipe's write "
        f"end open past the parent's exit, delaying EOF; observed {elapsed}s"
    )


def test_redirected_background_child_lets_reader_see_eof_promptly():
    elapsed = _time_to_eof(redirect_child_stdio=True)
    assert elapsed < 0.1, (
        "expected a background child with its own stdio redirected away "
        f"from the inherited pipe fd to let the reader see EOF promptly "
        f"once the parent exits; observed {elapsed}s"
    )


def _extract_broker_launch_line() -> str:
    src = ENTRY.read_text()
    m = re.search(
        r"^\s*LEERIE_CGROUP_V2_ROOT=.*?cgroup-broker\.py.*?&\s*$",
        src, re.MULTILINE | re.DOTALL,
    )
    assert m, ("could not find the cgroup broker launch line in "
               "container-entry.sh — did it get restructured?")
    return m.group(0)


def test_broker_launch_redirects_stdin_away_from_inherited_fd():
    line = _extract_broker_launch_line()
    assert re.search(r"<\s*/dev/null", line), (
        "broker launch must redirect stdin away from PID 1's inherited fd")


def test_broker_launch_redirects_stdout_and_stderr_away_from_inherited_fds():
    line = _extract_broker_launch_line()
    assert re.search(r">>?\s*\S+", line), (
        "broker launch must redirect stdout away from PID 1's inherited fd")
    assert "2>&1" in line, (
        "broker launch must redirect stderr away from PID 1's inherited fd")
