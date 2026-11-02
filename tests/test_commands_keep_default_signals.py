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

from tests.test_measurement_tmp import _st, _stream
from tests.test_worker_confinement import _abi, run  # noqa: F401  (fixture)

needs_landlock = pytest.mark.skipif(
    _abi() < 3, reason="the host kernel has no Landlock ABI 3")

PROBE = ("grep SigIgn /proc/self/status; yes | head -c1 >/dev/null; "
         "echo ps=${PIPESTATUS[*]}")


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


@needs_landlock
def test_the_shim_starts_its_command_with_default_dispositions(
        leerie, tmp_path, outside):
    r = subprocess.run(
        [sys.executable, str(leerie.LANDLOCK_SCRIPT), "--rw", str(tmp_path),
         "--rw", "/dev/null", "--ro", "/", "--", "bash", "-c", PROBE],
        capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert _report(r.stdout) == outside
    assert "Broken pipe" not in r.stderr


@needs_landlock
def test_a_measurement_runs_with_default_dispositions(leerie, run, outside):
    """The real path: `_run_streaming` under `_measurement_grants`."""
    rc, out = _stream(leerie, _st(run), run["wts"]["staging"], PROBE)
    assert rc == 0, out
    assert _report(out) == outside
    assert "Broken pipe" not in out


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
