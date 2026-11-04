"""A command leerie runs starts with the signal dispositions it would have
outside leerie (DESIGN §6 *A command leerie runs starts with the signal
dispositions it would have outside leerie*).

Review r20's L1: CPython ignores SIGPIPE and SIGXFSZ at startup and `exec`
keeps an ignored signal ignored, so every command the Landlock shim (or the
spawn gate) `exec`ed started with both ignored — `yes | head` gave
`PIPESTATUS` `1 0` and "Broken pipe" instead of `141 0`. Each test runs a
real `yes | head` and reads the command's own `SigIgn`, against the same
probe run directly."""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

from tests.conftest import CAN_RUN_IN_CONTAINER, needs_landlock

from tests.test_measurement_tmp import _st, _stream
from tests.test_worker_confinement import run  # noqa: F401  (fixture)


PROBE = ("grep SigIgn /proc/self/status; yes | head -c1 >/dev/null; "
         "echo ps=${PIPESTATUS[*]}")

# Three of the four tests here read the probe's `SigIgn` mask out of
# `/proc/self/status` (through the `outside` fixture), so they need a
# `/proc`: leerie runs in a Linux container, and on a host without one the
# probe printed nothing and `_report`'s `next()` raised StopIteration as a
# collection error rather than saying why. `needs_proc` carries both halves:
# conftest re-runs the test in the container when one is configured, and
# skips only where there is no `/proc` to read and no container to read one
# in.
#
# Deliberately NOT a module `pytestmark`:
# `test_the_list_is_every_signal_python_ignores_at_startup` derives the list
# from a fresh interpreter and reads no `/proc` at all, so it passes on a
# macOS host. A module mark skipped it there -- coverage lost for nothing,
# and with an image configured it would be shipped to a container to prove
# something the host already proves.
_NEEDS_PROC = [
    needs_landlock,
    pytest.mark.skipif(
        not os.path.isdir("/proc") and not CAN_RUN_IN_CONTAINER,
        reason="the signal mask is read from /proc/self/status, which this "
               "host has not got; leerie runs in a Linux container, so set "
               "LEERIE_TEST_CONTAINER_IMAGE to a leerie image and start the "
               "container runtime and it runs there instead"),
]


def _needs_proc(fn):
    """Apply both marks together: a test that can reach the container must
    also be able to skip (see `tests/test_landlock_gate_wiring.py`)."""
    for mark in reversed(_NEEDS_PROC):
        fn = mark(fn)
    return fn



def _report(out: str) -> tuple[str, str]:
    """(`SigIgn` mask, pipe status) from the probe's output."""
    ign = next(ln.split()[1] for ln in out.splitlines()
               if ln.startswith("SigIgn:"))
    ps = next(ln for ln in out.splitlines() if ln.startswith("ps="))
    return ign, ps


@pytest.fixture(scope="module")
def outside() -> tuple[str, str]:
    """The probe as any program outside leerie starts it (`subprocess`,
    whose `restore_signals` resets what Python ignored)."""
    r = subprocess.run(["bash", "-c", PROBE], capture_output=True, text=True,
                       check=True)
    got = _report(r.stdout)
    assert got[1] == "ps=141 0", got  # the reference itself is sound
    return got


def test_the_list_is_every_signal_python_ignores_at_startup(leerie):
    """Derived, not copied: a fresh interpreter's ignored signals are all in
    `PYTHON_IGNORED_SIGNALS`, so a CPython that ignores one more fails here
    rather than leaking it into every command."""
    src = ("import signal\n"
           "print(' '.join(s.name for s in signal.Signals\n"
           "      if signal.getsignal(s) is signal.SIG_IGN))")
    r = subprocess.run([sys.executable, "-I", "-S", "-c", src],
                       capture_output=True, text=True, check=True)
    ignored = set(r.stdout.split())
    assert "SIGPIPE" in ignored  # the probe can see what it looks for
    assert ignored <= set(leerie._landlock().PYTHON_IGNORED_SIGNALS)


@_needs_proc
def test_the_shim_starts_its_command_with_default_dispositions(
        leerie, tmp_path, outside):
    r = subprocess.run(
        [sys.executable, str(leerie.LANDLOCK_SCRIPT), "--rw", str(tmp_path),
         "--rw", "/dev/null", "--ro", "/", "--", "bash", "-c", PROBE],
        capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert _report(r.stdout) == outside
    assert "Broken pipe" not in r.stderr


@_needs_proc
def test_a_measurement_runs_with_default_dispositions(leerie, run, outside):
    """The real path: `_run_streaming` under `_measurement_grants`."""
    rc, out = _stream(leerie, _st(run), run["wts"]["staging"], PROBE)
    assert rc == 0, out
    assert _report(out) == outside
    assert "Broken pipe" not in out


@_needs_proc
def test_the_spawn_gate_starts_its_command_with_default_dispositions(
        leerie, outside):
    """The gate `exec`s whatever it wraps — the shim, or an unconfined
    command where there are no grants — so it resets them too."""
    argv, gate_r, gate_w = leerie._spawn_held_argv(["bash", "-c", PROBE])
    proc = subprocess.Popen(argv, pass_fds=(gate_r,), stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True)
    os.close(gate_r)
    leerie._release_spawn_gate(gate_w)
    out, err = proc.communicate(timeout=60)
    assert proc.returncode == 0, err
    assert _report(out) == outside
    assert "Broken pipe" not in err
