"""Orchestrator stdout persistence (Fix 5 of the empty-run-branch finalize
investigation).

On the local runtime the orchestrator runs directly (container-entry.sh
`exec python3 leerie.py`), so its stdout goes only to nerdctl -> the
launcher's decoupled tail -> the user's stdout/`tee`. There is no state-dir
copy of the orchestrator's own phase logs. When the piped stream is lost
(abnormal exit, or a resume overwriting the tee'd file) those logs vanish
-- run 26fd0fa5's `leerie.log` was 0 bytes, which is why its integration
skip could not be diagnosed. The remote (Fly/EC2) path already writes
`<run_dir>/orchestrator.log` via Popen(stdout=log_f).

`_install_run_log_tee` closes the local gap: it mirrors stdout+stderr to
`<run_dir>/orchestrator.log`, but only when stdout is not ALREADY that file
(inode check), so the remote path is never double-written.

The module is loaded via the session-scoped `leerie` fixture (tests/conftest.py);
the subprocess-based tests re-load it inside a child so the test's own
sys.stdout is never left wrapped.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LEERIE_PY = REPO_ROOT / "orchestrator" / "leerie.py"


def test_tee_mirrors_writes_to_both_streams(leerie, tmp_path, capsys):
    """_TeeStream writes to the original stream AND the log file."""
    logf = (tmp_path / "orchestrator.log").open("a", buffering=1)
    orig = sys.stdout
    tee = leerie._TeeStream(orig, logf)
    try:
        tee.write("phase 5: wave 1 integrated 0 of 10 completed subtask(s)\n")
        tee.flush()
    finally:
        logf.close()
    on_disk = (tmp_path / "orchestrator.log").read_text()
    assert "integrated 0 of 10" in on_disk
    # And the original stream still received it (capsys captures the real
    # sys.stdout that `orig` points at).
    assert "integrated 0 of 10" in capsys.readouterr().out


def test_tee_getattr_falls_through(leerie, tmp_path):
    """Attribute access (fileno, isatty, encoding) must reach the wrapped
    stream so callers that introspect stdout keep working."""
    logf = (tmp_path / "orchestrator.log").open("a", buffering=1)
    tee = leerie._TeeStream(sys.stdout, logf)
    try:
        # fileno / isatty exist on the wrapped real stream; the tee must
        # delegate rather than AttributeError.
        assert isinstance(tee.fileno(), int)
        _ = tee.isatty()  # must not raise
    finally:
        logf.close()


def test_tee_write_is_nonfatal_when_logfile_breaks(leerie, tmp_path, capsys):
    """If the log file becomes unwritable mid-run, the real stream still
    gets the write and no exception escapes."""
    logf = (tmp_path / "orchestrator.log").open("a", buffering=1)
    tee = leerie._TeeStream(sys.stdout, logf)
    logf.close()  # now writes to logf raise ValueError (I/O on closed file)
    # Must not raise, and the original stream must still receive the text.
    tee.write("still-visible-on-terminal\n")
    assert "still-visible-on-terminal" in capsys.readouterr().out


def test_stdout_already_targets_true_when_fd_is_the_file(leerie, tmp_path):
    """When sys.stdout's fd points at the target file (the remote runtime's
    Popen(stdout=log_f) shape), the guard returns True so the tee is skipped
    (no double-write)."""
    log_path = tmp_path / "orchestrator.log"
    with log_path.open("w") as f:
        saved = os.dup(sys.stdout.fileno())
        os.dup2(f.fileno(), sys.stdout.fileno())
        try:
            result = leerie._stdout_already_targets(log_path)
        finally:
            os.dup2(saved, sys.stdout.fileno())
            os.close(saved)
    assert result is True


def test_stdout_already_targets_false_when_stdout_is_a_pipe(leerie, tmp_path):
    """When stdout is a pipe (the local runtime: nerdctl captures it), the
    guard returns False so the tee IS installed and the log persists."""
    log_path = tmp_path / "orchestrator.log"
    log_path.write_text("")  # target exists but is a different inode
    r, w = os.pipe()
    saved = os.dup(sys.stdout.fileno())
    os.dup2(w, sys.stdout.fileno())
    try:
        result = leerie._stdout_already_targets(log_path)
    finally:
        os.dup2(saved, sys.stdout.fileno())
        os.close(saved)
        os.close(r)
        os.close(w)
    assert result is False


def test_install_creates_orchestrator_log_and_captures_output(tmp_path):
    """End-to-end: after _install_run_log_tee, a print() lands in
    <run_dir>/orchestrator.log. Runs in a subprocess so the test's own
    sys.stdout is never left wrapped."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    prog = (
        "import importlib.util, sys\n"
        f"spec = importlib.util.spec_from_file_location('l', {str(LEERIE_PY)!r})\n"
        "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
        "from pathlib import Path\n"
        f"m._install_run_log_tee(Path({str(run_dir)!r}))\n"
        "print('phase 5: wave 1 integrated 0 of 10 completed subtask(s)')\n"
        "sys.stderr.write('a stderr line\\n')\n"
    )
    subprocess.run([sys.executable, "-c", prog], check=True,
                   capture_output=True, text=True)
    on_disk = (run_dir / "orchestrator.log").read_text()
    assert "integrated 0 of 10" in on_disk
    assert "a stderr line" in on_disk


def test_install_is_nonfatal_when_run_dir_unwritable(leerie, tmp_path):
    """A run dir whose orchestrator.log cannot be opened must not raise;
    the run proceeds with terminal-only output."""
    # Point at a path whose parent is a file, so open() fails.
    bogus = tmp_path / "afile"
    bogus.write_text("x")
    orig_out, orig_err = sys.stdout, sys.stderr
    try:
        leerie._install_run_log_tee(bogus)  # bogus/orchestrator.log -> NotADirectory
        # stdout must NOT have been wrapped (open failed before assignment).
        assert sys.stdout is orig_out
    finally:
        sys.stdout, sys.stderr = orig_out, orig_err


def test_install_skipped_when_stdout_is_the_log(tmp_path):
    """Remote-shape guard: when sys.stdout already targets orchestrator.log,
    _install_run_log_tee must NOT wrap stdout (no double-write). Subprocess
    so the redirect is isolated."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    prog = (
        "import importlib.util, sys, os\n"
        f"spec = importlib.util.spec_from_file_location('l', {str(LEERIE_PY)!r})\n"
        "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
        "from pathlib import Path\n"
        f"lp = Path({str(run_dir)!r}) / 'orchestrator.log'\n"
        "f = open(lp, 'w'); os.dup2(f.fileno(), sys.stdout.fileno())\n"
        f"m._install_run_log_tee(Path({str(run_dir)!r}))\n"
        "print('LINE')\n"
        "sys.stdout.flush()\n"
        # If the tee had been installed, _TeeStream would double-write LINE.
        "was_wrapped = type(sys.stdout).__name__ == '_TeeStream'\n"
        "sys.stderr.write('WRAPPED=' + str(was_wrapped) + '\\n')\n"
    )
    r = subprocess.run([sys.executable, "-c", prog], check=True,
                       capture_output=True, text=True)
    assert "WRAPPED=False" in r.stderr
    # LINE appears exactly once (no double-write).
    assert (run_dir / "orchestrator.log").read_text().count("LINE") == 1
