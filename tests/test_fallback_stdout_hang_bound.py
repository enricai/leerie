"""Adversarial wall-clock-bound regression test for the bare fallback exit
path (leerie:8887-8890): `nerdctl run "${_run_argv[@]}" || container_rc=$?`
with stdout inherited directly, no decoupling of any kind.

Neither tests/test_log_file_wiring.py nor tests/test_log_file_persistence.py
extract this specific block -- both only ever drive the `$_run_log`
(piped/decoupled) and `script`(1)-teed branches, so this fallback has zero
existing coverage (not content, not structure, not timing). It is also the
one branch with no decoupling mechanism at all: the piped branch redirects
nerdctl's own stdout into a file the launcher tails itself
(tests/test_decoupled_streaming_hang_bound.py), and the `script`(1) branch
routes through a pty that closes on its own once the wrapped command exits
(tests/test_interactive_script_hang_bound.py) -- but this branch hands
nerdctl (and anything it backgrounds) the launcher's own inherited stdout
fd, unchanged. A grandchild that outlives nerdctl and keeps that fd open
(mirroring the SSH-mux / broker held-pipe failure mode the other two
branches are built to route around) keeps a reader of that fd blocked on
EOF for exactly as long as the grandchild lives -- this test pins that the
invocation itself adds no *extra* unbounded delay on top of that, using
`tests.log_file_extract_helpers.extract_invocation` so it tracks the
launcher's real source rather than a hand-copied snippet.
"""
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

from tests.log_file_extract_helpers import extract_invocation as _extract_invocation

REPO_ROOT = Path(__file__).resolve().parent.parent

_INVOCATION = _extract_invocation()
assert "nerdctl run" in _INVOCATION
assert 'nerdctl run "${_run_argv[@]}" || container_rc=$?\n  fi' in _INVOCATION

# A tiny fixed lifetime for the adversarial stdout holder -- long enough to
# prove the launcher isn't adding its own extra delay on top, short enough
# to keep the test fast and deterministic.
_HOLD_SECONDS = 1
_CEILING_SECONDS = 5

_HARNESS = r"""#!/usr/bin/env bash
set -uo pipefail

container_rc=0
_run_argv=(run --rm alpine echo hi)
_run_log=""
_log_tee_target=""

# Adversarial nerdctl stub: returns immediately itself, but backgrounds a
# detached grandchild that inherits (and keeps open) the exact stdout fd it
# was handed -- this branch never redirects that fd anywhere else, so it is
# the one actually exposed to a lingering holder.
nerdctl() {
  ( setsid sleep __HOLD__ </dev/null >&1 2>&1 & ) 2>/dev/null
  printf 'FALLBACK OUTPUT LINE\n'
  return 0
}

__INVOCATION__

printf 'CONTAINER_RC=%s\n' "$container_rc"
"""


def _run(tmp_path):
    script = tmp_path / "harness.sh"
    script.write_text(
        _HARNESS
        .replace("__HOLD__", str(_HOLD_SECONDS))
        .replace("__INVOCATION__", _INVOCATION)
    )
    start = time.monotonic()
    result = subprocess.run(
        ["bash", str(script)],
        capture_output=True, text=True,
        timeout=_CEILING_SECONDS + 20,
        env=os.environ.copy(),
    )
    elapsed = time.monotonic() - start
    return result, elapsed


def test_fallback_path_returns_promptly_even_with_a_lingering_stdout_holder(tmp_path):
    result, elapsed = _run(tmp_path)
    assert result.returncode == 0, result.stderr
    assert "FALLBACK OUTPUT LINE" in result.stdout
    assert "CONTAINER_RC=0" in result.stdout
    assert elapsed < _CEILING_SECONDS, (
        f"fallback invocation (leerie:8887-8890) took {elapsed:.2f}s to "
        f"return under an adversarial stdout holder -- expected under "
        f"{_CEILING_SECONDS}s (holder lifetime {_HOLD_SECONDS}s)"
    )
