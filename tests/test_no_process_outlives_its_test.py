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

from tests.conftest import (_POPEN_ENV_POS, TEST_PROCESS_ENV, _popen_init,
                            kill_marked, processes_marked)

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


def _marked(pids: list[int], marker: str, within: float = 10.0) -> list[int]:
    """`processes_marked(marker)` once it can see every pid in `pids`.

    A spawn is asynchronous in a way `_pids` and `_alive` do not cover, and
    asserting on one scan made this test flaky on CI rather than wrong:
    `echo $!` reports the pid when `fork` returns, and `/proc/<pid>/stat`
    exists from that moment, but `/proc/<pid>/environ` carries the marker
    only once `exec` has installed the new environment. Scanning in between
    sees a live, markerless process and the set comparison fails -- `assert
    {218511, 218512} <= {218511}`, test (3.12) of run 42238184378, while
    (3.10) and (3.11) of the same commit passed. Measured in an ubuntu:24.04
    CI-parity container: 94 of 400 spawns (~24%) had at least one pid whose
    environ was not yet readable at the instant the pidfile held both, and
    the failure reproduces on a pristine main (1 in 12 runs of this test),
    so it predates the change that surfaced it.

    Polling is the fix rather than a sleep: it keeps the assertion exactly
    as strong -- both pids must be found through their environment, which is
    the property this test exists for -- and returns as soon as that is
    true. The window is microseconds in practice; the generous bound only
    has to beat a loaded CI host.
    """
    end = time.monotonic() + within
    wanted = set(pids)
    while True:
        found = processes_marked(marker)
        if wanted <= set(found):
            return found
        if time.monotonic() >= end:
            return found
        time.sleep(0.02)


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
    assert set(pids) <= set(_marked(pids, marker))
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


# --- a child given an environment of its own (an explicit `env`) ------------

def test_an_explicit_env_still_carries_the_marker():
    """A `Popen` handed its own `env` -- by keyword, by position, or as
    bytes -- starts a child that carries this test's marker, and the
    caller's mapping is left as it was. Falsified: the `Popen.__init__`
    wrapper dropped from the fixture (the child prints an empty line)."""
    marker = os.environ[TEST_PROCESS_ENV]
    argv = ["/bin/bash", "-c", 'printf %s "$' + TEST_PROCESS_ENV + '"']
    env = {"PATH": "/usr/bin:/bin"}
    assert subprocess.run(argv, env=env, capture_output=True,
                          text=True).stdout == marker
    assert subprocess.check_output(
        argv, env={b"PATH": b"/usr/bin:/bin"}) == marker.encode()
    p = subprocess.Popen(argv, -1, None, None, subprocess.PIPE, None, None,
                         True, False, None, env)
    assert p.communicate()[0] == marker.encode()
    assert env == {"PATH": "/usr/bin:/bin"}


def test_env_is_where_the_wrapper_reads_it():
    """The wrapper finds a positional `env` by index; pin that index to
    Popen's own signature on this Python."""
    import inspect
    names = list(inspect.signature(_popen_init).parameters)
    assert names[_POPEN_ENV_POS + 1] == "env", names


# The shape `test_buildkit_isolation.py` leaked (issue 20): a harness started
# with a fresh environment and a session of its own, whose build runs in a
# group of its own blocked on a FIFO nobody writes once the test has failed.
FIFO_SPAWN = ("/usr/bin/setsid /bin/bash -c 'read -r _ < \"$FIFO\"' & "
              "echo $! >> {pids}; /usr/bin/sleep 300 & echo $! >> {pids}; "
              "wait")

INNER_FAILING_TEST = """\
import os
import subprocess
import time


def test_fails_mid_build(tmp_path):
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)
    p = subprocess.Popen(["/bin/bash", "-c", {spawn!r}],
                         start_new_session=True,
                         env={{"PATH": "/usr/bin:/bin", "FIFO": str(fifo)}})
    try:
        end = time.monotonic() + 10
        while time.monotonic() < end and not (
                os.path.exists({pids!r})
                and len(open({pids!r}).read().split()) == 2):
            time.sleep(0.05)
        assert False, "a failing assertion mid-build"
    finally:
        os.killpg(p.pid, 9)
        p.wait(timeout=10)
"""


def _run_inner_failing(tmp_path: Path, *, with_fixture: bool) -> list[int]:
    d = tmp_path / ("with" if with_fixture else "without")
    d.mkdir()
    pidfile = d / "pids"
    (d / "conftest.py").write_text(INNER_CONFTEST.format(
        import_line=("from tests.conftest import "
                     "_no_process_outlives_its_test  # noqa: F401"
                     if with_fixture else "")))
    (d / "test_inner.py").write_text(INNER_FAILING_TEST.format(
        spawn=FIFO_SPAWN.format(pids=pidfile), pids=str(pidfile)))
    env = {**os.environ,
           "PYTHONPATH": os.pathsep.join(
               [str(REPO_ROOT), *filter(None, [os.environ.get("PYTHONPATH")])])}
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
         "-p", "no:xdist", "-o", "addopts=",
         "--rootdir", str(d), str(d / "test_inner.py")],
        cwd=d, env=env, capture_output=True, text=True, timeout=120)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "a failing assertion mid-build" in r.stdout, r.stdout
    return _pids(pidfile)


def test_a_failed_test_with_a_fresh_env_leaves_no_process(tmp_path):
    """The FIFO reader in a session of its own, started with an environment
    the test wrote itself, is gone once the failed test is torn down.
    Falsified: the `Popen.__init__` wrapper dropped from the fixture (the
    reader stays, blocked on the FIFO, reparented to init)."""
    pids = _run_inner_failing(tmp_path, with_fixture=True)
    try:
        assert _gone(pids), [p for p in pids if _alive(p)]
    finally:
        for p in pids:
            with contextlib.suppress(OSError):
                os.kill(p, 9)


def test_without_the_fixture_the_fifo_reader_stays(tmp_path):
    """Anti-vacuity: without the fixture the same failed test leaves the
    FIFO reader running (killed here: it carries no marker of ours)."""
    pids = _run_inner_failing(tmp_path, with_fixture=False)
    try:
        assert _alive(pids[0])
    finally:
        for p in pids:
            with contextlib.suppress(OSError):
                os.kill(p, 9)
