"""bugfix-004: the cgroup broker launch line must not inherit PID 1's stdio.

container-entry.sh launches the cgroup broker with `python3 ... &`. A bare
background launch inherits PID 1's stdout/stderr, which can hold the
container's output pipe open past PID 1's own exit, delaying EOF on the
launcher's read side and contributing to the reported hang-on-exit. The
launch line must explicitly redirect stdin/stdout/stderr away from those
inherited fds.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ENTRY = REPO_ROOT / "scripts" / "container-entry.sh"


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
