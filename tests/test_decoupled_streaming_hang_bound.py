"""Wall-clock-bound regression test for the piped/decoupled-streaming
(-i, non-tty) launcher exit path (leerie:8811-8826).

test_log_file_persistence.py and test_log_file_wiring.py prove the tee/
tail wiring exists and produces correct file content, but both use a
stub `nerdctl` that writes-and-returns synchronously
(test_log_file_persistence.py:82-84) -- neither ever exercises a
background process that keeps the container's stdout stream open past
the stub's own exit, which is exactly the SSH-mux / broker failure mode
the file-based `_run_log` + launcher-owned `tail -f` design exists to
route around (leerie:8649-8656). This file's stub forks a detached
grandchild that inherits stdout and sleeps well past the harness's own
completion, then exits itself -- proving the harness returns promptly
because `_run_log` is a plain FILE (not a pipe): redirecting nerdctl's
stdout to a file means a lingering fd-holder never keeps a reader
blocked on EOF the way a held pipe would.
"""
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

from tests.log_file_extract_helpers import (
    extract_invocation as _extract_invocation,
    extract_reap_tail as _extract_reap_tail,
    extract_setup_block as _extract_setup_block,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = REPO_ROOT / "leerie"

HANG_CEILING_SECONDS = 5.0

# The grandchild sleeps far past both the ceiling and the harness's own
# `sleep 0.5` settle window, so a false pass (the process actually exiting
# before the hold matters) is not possible.
_HOLDER_SLEEP_SECONDS = 20

_HARNESS = r"""#!/usr/bin/env bash
set -uo pipefail

USER_REPO="__REPO__"
LEERIE_STATE_HOST_DIR="__STATE__"
TTY_FLAGS="-i"
container_rc=0
_run_argv=()

# Adversarial nerdctl stub: writes fixed content, then backgrounds a
# detached grandchild that inherits nerdctl's stdout (the redirected
# run-log file, per `nerdctl run ... >"$_run_log" 2>&1`) and sleeps well
# past this stub's own exit, mirroring a broker/mux process that keeps a
# stream open after the container it belongs to has finished. `disown`
# detaches it from the job table so nothing in this stub's own shell
# waits on it.
nerdctl() {
  printf 'CONTAINER OUTPUT LINE ONE\nCONTAINER OUTPUT LINE TWO\n'
  ( sleep __HOLDER_SLEEP__ ) &
  disown
}

__SETUP__

__INVOCATION__

sleep 0.5
__REAP__
_reap_tail

printf 'RUN_LOG_GONE=%s\n' "$([ -z "$_run_log" ] && echo yes || echo no)"
"""


def test_piped_path_returns_promptly_even_with_a_lingering_stdout_holder(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    script = _write_harness(repo, state_dir)

    start = time.monotonic()
    result = subprocess.run(
        ["bash", str(script)],
        capture_output=True, text=True, timeout=_HOLDER_SLEEP_SECONDS,
        env=os.environ.copy(),
    )
    elapsed = time.monotonic() - start

    assert result.returncode == 0, result.stderr
    assert elapsed < HANG_CEILING_SECONDS, (
        f"harness took {elapsed:.2f}s (ceiling {HANG_CEILING_SECONDS}s) -- "
        "it blocked on the lingering stdout holder instead of returning "
        f"once nerdctl itself exited; stdout={result.stdout!r} "
        f"stderr={result.stderr!r}"
    )

    assert "CONTAINER OUTPUT LINE ONE" in result.stdout
    assert "CONTAINER OUTPUT LINE TWO" in result.stdout
    assert "RUN_LOG_GONE=yes" in result.stdout


def _write_harness(repo: Path, state_dir: Path) -> Path:
    script = repo.parent / "harness.sh"
    script.write_text(
        _HARNESS
        .replace("__REPO__", str(repo))
        .replace("__STATE__", str(state_dir))
        .replace("__HOLDER_SLEEP__", str(_HOLDER_SLEEP_SECONDS))
        .replace("__SETUP__", _extract_setup_block())
        .replace("__INVOCATION__", _extract_invocation())
        .replace("__REAP__", _extract_reap_tail())
    )
    return script
