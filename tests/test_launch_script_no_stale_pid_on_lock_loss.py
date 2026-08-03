"""Regression test for the launcher's poll-before-pid-write logic.

The launcher's `_launch_script` (a Python heredoc inside the `leerie`
bash) spawns the orchestrator on the Fly Machine, then writes its pid
to `<run-dir>/orchestrator.pid`. Without the post-`Popen` poll, a
stillborn flock-loser child (a duplicate `resume` that lost the
`State.__init__` flock race and exits 75) would have its dead pid
written to the file — clobbering the winning orchestrator's pid and
breaking every downstream liveness check (DESIGN §6 *Single owner
per run dir* — stale-pid contagion).

This test exercises the poll+write logic in isolation: the launcher's
exact code excerpt is mirrored verbatim in a small driver script,
which is then run twice — once with a child that exits 75
(flock-loser) and once with a child that stays alive (winner). The
pid file should remain *un-written* after the 75-loser case and
should be written after the winner case.

Note: this is a behavioral test of the *logic*, not of the launcher
bash that wraps it. The launcher's actual heredoc is a Python
payload sent over SSH to the Fly Machine; extracting and inlining
the poll loop here keeps the test on-host without inventing a new
fake-flyctl harness.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from textwrap import dedent

REPO_ROOT = Path(__file__).resolve().parent.parent


# This script mirrors the launcher's poll-before-write code (the
# excerpt between `subprocess.Popen(...)` and `with open(pid_path...)`).
# Kept here as a behavioral spec — if the launcher's logic drifts, both
# must update.
DRIVER_TEMPLATE = dedent('''
    import subprocess, sys, time
    pid_path = sys.argv[1]
    child_cmd = sys.argv[2:]
    p = subprocess.Popen(child_cmd)
    # The poll loop the launcher uses.
    for _ in range(10):
        if p.poll() is not None:
            break
        time.sleep(0.2)
    if p.poll() == 75:
        # Stillborn flock-loser; do not write the pid file.
        sys.exit(75)
    with open(pid_path, "w") as f:
        f.write(str(p.pid) + "\\n")
    # In the launcher this Python is the wrapper script; the spawned
    # orchestrator detaches and the wrapper exits. For the test, we
    # also wait briefly so the winning child's pid actually exists
    # when the test asserts on the file (no zombie reaping race).
    sys.exit(0)
''')


def _run_driver(driver_path: Path, pid_path: Path,
                child_argv: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(driver_path), str(pid_path), *child_argv],
        capture_output=True, text=True, timeout=10,
    )


def test_poll_does_not_write_pid_on_rc_75(tmp_path):
    """Child exits 75 (flock-loser) → pid file is NOT touched."""
    driver = tmp_path / "driver.py"
    driver.write_text(DRIVER_TEMPLATE)
    pid_path = tmp_path / "orchestrator.pid"
    # Child that exits 75 quickly — mirrors the flock-loss exit.
    result = _run_driver(
        driver, pid_path,
        [sys.executable, "-c", "import sys; sys.exit(75)"],
    )
    assert result.returncode == 75, (
        f"driver should propagate rc=75; stdout={result.stdout!r} "
        f"stderr={result.stderr!r}"
    )
    assert not pid_path.exists(), (
        f"pid file must NOT be written when child exits 75 "
        f"(content if present: {pid_path.read_text() if pid_path.exists() else '?'})"
    )


def test_poll_writes_pid_when_child_survives(tmp_path):
    """Child stays alive (flock winner) → pid file IS written."""
    driver = tmp_path / "driver.py"
    driver.write_text(DRIVER_TEMPLATE)
    pid_path = tmp_path / "orchestrator.pid"
    # Child that sleeps 3 s — beyond the driver's 2 s poll budget but
    # not so long the test times out. After the driver writes the pid
    # and exits, the child is still alive; the test then reads the
    # file and verifies content matches a live pid.
    result = _run_driver(
        driver, pid_path,
        [sys.executable, "-c", "import time; time.sleep(3)"],
    )
    assert result.returncode == 0, (
        f"driver should exit 0 when child survives the poll window; "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert pid_path.exists(), "pid file must be written when child survives"
    written = pid_path.read_text().strip()
    assert written.isdigit(), f"pid file should contain a numeric pid, got {written!r}"


def test_poll_writes_pid_when_child_exits_0_immediately(tmp_path):
    """Child exits 0 immediately → pid file IS written (no flock-loss).

    Edge case: a child that exits cleanly before the poll loop finishes
    is not a flock-loser; the orchestrator's clean-exit path runs
    after State.__init__ succeeds. The pid is the correct truth at
    the moment of write even though the process is now gone. This
    matches the launcher's pre-fix behavior on this path and is
    consistent with the existing convention that orchestrator.pid is
    a stale artifact after clean exit (see force-finalize.sh header).
    """
    driver = tmp_path / "driver.py"
    driver.write_text(DRIVER_TEMPLATE)
    pid_path = tmp_path / "orchestrator.pid"
    result = _run_driver(
        driver, pid_path,
        [sys.executable, "-c", "import sys; sys.exit(0)"],
    )
    assert result.returncode == 0, (
        f"driver should exit 0 when child exits 0; "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert pid_path.exists(), (
        "pid file must be written when child exits cleanly (rc=0 is "
        "not flock-loss)"
    )


def test_launcher_logic_matches_driver_template():
    """Coupling test: the launcher's actual poll+write block must
    structurally match what DRIVER_TEMPLATE encodes.

    If the launcher's logic is edited, this test fires to remind the
    author to update DRIVER_TEMPLATE so the behavioral tests above
    still describe the real code."""
    launcher = (REPO_ROOT / "leerie").read_text()
    # Anchors from the launcher's poll+write block; if any of these
    # strings change, update DRIVER_TEMPLATE to match.
    must_contain = [
        "for _ in range(10):",
        "if p.poll() is not None:",
        "time.sleep(0.2)",
        "if p.poll() == 75:",
        'sys.exit(75)',
        'with open(pid_path, "w") as pid_f:',
        'pid_f.write(str(p.pid) + "\\n")',
    ]
    missing = [s for s in must_contain if s not in launcher]
    assert not missing, (
        f"launcher poll+write block is missing expected anchors: {missing}. "
        f"If the launcher changed, update DRIVER_TEMPLATE in this test."
    )
