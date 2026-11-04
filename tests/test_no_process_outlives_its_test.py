"""No process a test starts outlives the test, however the test ended
(`tests/conftest.py::_no_process_outlives_its_test`).

A run killed mid-test left the launcher's relay pipeline (`bash -c` over
`_log.sh`, its `bash` subshells, `terminal-text.py`) reparented to init for
hours. A per-test timeout makes that the normal way a hung test ends:
pytest-timeout's signal method raises inside the test, and
`subprocess.run` kills only its direct child. These tests time a test out
for real and look for what it started — a grandchild in the test's own
session and one in a session of its own (`setsid`, which a process-group
kill would miss).
"""
from __future__ import annotations

import contextlib
import importlib.util
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

from tests.conftest import TEST_PROCESS_ENV, kill_marked, processes_marked

REPO_ROOT = Path(__file__).resolve().parent.parent

# The guarantee is CI's (Linux): the fixture finds a process through
# /proc/<pid>/environ, and `setsid` -- the session the proof turns on -- is
# not in base macOS, so SPAWN's absolute paths do not exist there. Skipping
# beats substituting the host's paths, which would drop the setsid arm and
# leave a weaker test wearing this one's name.
pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="needs /proc and setsid: the Linux guarantee this fixture backs",
)

# Absolute paths only: nothing here resolves a program on PATH.
SPAWN = ("/usr/bin/sleep 300 & echo $! >> {pids}; "
         "/usr/bin/setsid /usr/bin/sleep 300 & echo $! >> {pids}; wait")


def _alive(pid: int) -> bool:
    """Running and not a zombie (an orphan's zombie waits on its reaper)."""
    try:
        with open(f"/proc/{pid}/stat", "rb") as f:
            st = f.read()
    except OSError:
        return False
    return st[st.rindex(b")") + 2:st.rindex(b")") + 3] != b"Z"


def _gone(pids: list[int], within: float = 5.0) -> bool:
    end = time.monotonic() + within
    while time.monotonic() < end:
        if not any(_alive(p) for p in pids):
            return True
        time.sleep(0.05)
    return False


def _pids(path: Path) -> list[int]:
    end = time.monotonic() + 10
    while time.monotonic() < end:
        if path.exists() and len(path.read_text().split()) == 2:
            return [int(x) for x in path.read_text().split()]
        time.sleep(0.05)
    raise AssertionError(f"the spawned processes never wrote {path}")


def test_kill_marked_finds_a_process_by_its_environment(tmp_path):
    """Neither parent, group nor session is the handle: the setsid child
    is in none of the test's, and both outlive the shell that started
    them."""
    marker = uuid.uuid4().hex
    pidfile = tmp_path / "pids"
    env = {**os.environ, TEST_PROCESS_ENV: marker}
    subprocess.run(["/bin/bash", "-c",
                    SPAWN.format(pids=pidfile).replace("; wait", "")],
                   env=env, check=True)
    pids = _pids(pidfile)
    assert all(_alive(p) for p in pids)
    assert set(pids) <= set(processes_marked(marker))
    killed = kill_marked(marker)
    assert set(pids) <= set(killed)
    assert _gone(pids)
    assert processes_marked(marker) == []


INNER_CONFTEST = """\
{import_line}
"""

INNER_TEST = """\
import subprocess


def test_hangs():
    subprocess.run(["/bin/bash", "-c", {spawn!r}], timeout=120)
"""


def _run_inner(tmp_path: Path, *, with_fixture: bool) -> list[int]:
    d = tmp_path / ("with" if with_fixture else "without")
    d.mkdir()
    pidfile = d / "pids"
    (d / "conftest.py").write_text(INNER_CONFTEST.format(
        import_line=("from tests.conftest import "
                     "_no_process_outlives_its_test  # noqa: F401"
                     if with_fixture else "")))
    (d / "test_inner.py").write_text(
        INNER_TEST.format(spawn=SPAWN.format(pids=pidfile)))
    env = {**os.environ,
           "PYTHONPATH": os.pathsep.join(
               [str(REPO_ROOT), *filter(None, [os.environ.get("PYTHONPATH")])])}
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
         "-p", "no:xdist", "--timeout=3", "-o", "addopts=",
         "--rootdir", str(d), str(d / "test_inner.py")],
        cwd=d, env=env, capture_output=True, text=True, timeout=120)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "Timeout" in r.stdout, r.stdout
    return _pids(pidfile)


@pytest.mark.skipif(importlib.util.find_spec("pytest_timeout") is None,
                    reason="pytest-timeout is not installed here")
def test_a_test_that_times_out_leaves_no_process(tmp_path):
    pids = _run_inner(tmp_path, with_fixture=True)
    assert _gone(pids), [p for p in pids if _alive(p)]


@pytest.mark.skipif(importlib.util.find_spec("pytest_timeout") is None,
                    reason="pytest-timeout is not installed here")
def test_without_the_fixture_the_timed_out_test_leaves_both(tmp_path):
    """Anti-vacuity: the same timed-out test, without the fixture, leaves
    both grandchildren running (killed here, by the outer test's own
    fixture at the latest — they carry its marker)."""
    pids = _run_inner(tmp_path, with_fixture=False)
    try:
        assert all(_alive(p) for p in pids)
    finally:
        for p in pids:
            with contextlib.suppress(OSError):
                os.kill(p, 9)
