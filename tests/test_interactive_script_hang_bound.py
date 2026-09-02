"""Adversarial wall-clock-bound regression test for the interactive `-it`,
`script`(1)-teed launcher exit path (bugfix-005, leerie:8877-8886, the
non-Darwin `script -qe -c "$_nerdctl_run_quoted" -a "$_log_tee_target"`
branch).

DESIGN.md asserts the interactive `-it` path "has a real pty and thus no
hang" -- but that argument rests entirely on `script`(1)'s own pty
allocation, and nothing in this suite has ever put it under the same
lingering-background-holder condition test_log_file_wiring.py's stubs
never exercise: existing coverage there
(`test_interactive_tty_path_is_teed_via_script` et al.) uses a `nerdctl`
stub that always writes-and-returns synchronously, so it only proves the
teed output is byte-correct, never that the branch *returns promptly*.

This mirrors test-002's harness shape (reusing
`tests.log_file_extract_helpers`) but drives the adversarial nerdctl stub
through the `script`(1)-wrapped invocation instead of the piped/`_run_log`
one: the stub backgrounds a detached grandchild that inherits the
`script`-allocated pty as its stdout and sleeps well past the stub's own
exit, mirroring the lingering-background-holder failure mode DESIGN.md
cites for the SSH-mux / broker case. If `script`(1) (or the shell it
launches the wrapped command through) waits on the pty itself going EOF
rather than on the direct child's exit, this reproduces the hang the
pty was supposed to make impossible.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from tests.log_file_extract_helpers import (
    extract_invocation as _extract_invocation,
    extract_reap_tail as _extract_reap_tail,
    extract_setup_block as _extract_setup_block,
)
from tests.test_log_file_wiring import _extract_mkdir_line, _HARNESS

CEILING_SECONDS = 5.0

pytestmark = pytest.mark.skipif(
    shutil.which("script") is None, reason="requires script(1) on PATH"
)


def _write_lingering_nerdctl_stub(bin_dir: Path) -> None:
    """A real executable `nerdctl` (required -- `script -c` runs the
    wrapped command in a fresh `$SHELL -c` subprocess that cannot see a
    harness bash function). Writes the expected output, then backgrounds
    a detached grandchild that inherits its stdout (the `script`-allocated
    pty slave under this branch) and sleeps well past its own exit --
    mirroring a lingering background holder of the pty/output stream, the
    same class of failure DESIGN.md's hang writeup describes for the SSH
    mux / broker case. `disown` (not a `( ... & ) >/dev/null` subshell --
    bash silently redirects an asynchronous list's own stdout to
    /dev/null unless it already inherits a real fd, which defeats the
    adversarial condition entirely; confirmed empirically: wrapping in
    `()` leaves the grandchild's fd 1 pointing at /dev/null, not the
    inherited pty/pipe) keeps the grandchild's fd 1 genuinely inherited
    while detaching it from the job table so nerdctl itself (and the
    `$SHELL -c` script(1) runs it through) does not wait on it."""
    bin_dir.mkdir(exist_ok=True)
    stub = bin_dir / "nerdctl"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        "printf 'hello from container\\nsecond line\\n'\n"
        "sleep 60 &\n"
        "disown\n"
        "exit 0\n"
    )
    stub.chmod(0o755)


def test_script_teed_path_returns_promptly_even_with_a_lingering_stdout_holder(
    tmp_path,
):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    log_file = tmp_path / "logs" / "leerie-run.log"

    bin_dir = state_dir / "bin"
    _write_lingering_nerdctl_stub(bin_dir)

    script_path = state_dir / "harness.sh"
    script_path.write_text(
        _HARNESS
        .replace("__STATE__", str(state_dir))
        .replace("__LOGFILE__", str(log_file))
        .replace("__TTYFLAGS__", "-it")
        .replace("__MKDIR__", _extract_mkdir_line())
        .replace("__SETUP__", _extract_setup_block())
        .replace("__INVOCATION__", _extract_invocation())
        .replace("__REAP__", _extract_reap_tail())
    )

    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env['PATH']}"

    started = time.monotonic()
    result = subprocess.run(
        ["bash", str(script_path)],
        capture_output=True, text=True, timeout=30,
        env=env,
    )
    elapsed = time.monotonic() - started

    assert result.returncode == 0, result.stderr
    assert elapsed < CEILING_SECONDS, (
        f"script(1)-teed -it branch took {elapsed:.2f}s (ceiling "
        f"{CEILING_SECONDS}s) with a lingering background stdout holder -- "
        "the pty is not the hang-proofing DESIGN.md claims it is"
    )
    assert "hello from container" in result.stdout
