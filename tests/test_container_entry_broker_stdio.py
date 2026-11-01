"""bugfix-004: the cgroup broker must not inherit PID 1's stdio.

A background child that inherits PID 1's stdout/stderr can hold the
container's output pipe open past PID 1's own exit, delaying EOF on the
launcher's read side and contributing to the reported hang-on-exit. The
broker is forked by `cgroup-broker.py --spawn` (container-entry.sh's final
exec), which points its stdin at /dev/null and its stdout/stderr at its log.
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


def _broker_stdio(tmp_path: Path) -> dict:
    """Run the real `cgroup-broker.py --spawn` with its stdout a pipe (PID
    1's output, standing in for the container's), and report what the
    forked broker's fds 0-2 are."""
    import json
    import subprocess
    import sys
    broker = REPO_ROOT / "scripts" / "cgroup-broker.py"
    log = tmp_path / "broker.log"
    probe = (
        "import json, os\n"
        "kid = open(f'/proc/self/task/{os.getpid()}/children').read().split()[0]\n"
        "print(json.dumps({n: os.readlink(f'/proc/{kid}/fd/{n}') "
        "for n in ('0', '1', '2')}))\n")
    root = tmp_path / "cg"
    root.mkdir()
    r = subprocess.run(
        [sys.executable, str(broker), "--spawn", "--log", str(log), "--",
         sys.executable, "-c", probe],
        capture_output=True, text=True, timeout=30,
        env={**os.environ, "LEERIE_CGROUP_V2_ROOT": str(root)})
    assert r.returncode == 0, r.stderr
    return {"fds": json.loads(r.stdout), "log": str(log)}


def test_broker_stdin_is_not_pid1s(tmp_path):
    got = _broker_stdio(tmp_path)
    assert got["fds"]["0"] == "/dev/null"


def test_broker_stdout_and_stderr_are_its_log_not_pid1s(tmp_path):
    """PID 1's stdout here is the `subprocess.run` pipe; the broker, which
    outlives the exec, must not hold it open."""
    got = _broker_stdio(tmp_path)
    assert got["fds"]["1"] == got["log"]
    assert got["fds"]["2"] == got["log"]
    assert not got["fds"]["1"].startswith("pipe:")
