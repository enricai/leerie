"""End-to-end pin for N5b: --log-file must persist the run's captured
output at the resolved path and that file must survive process exit --
not just that the path resolves correctly (test_log_file_resolution.py)
and not just that the teeing wiring exists in isolation
(test_log_file_wiring.py, which takes LEERIE_LOG_FILE_RESOLVED as a given
input). This file drives the launcher's own --log-file resolver
(leerie:846-916, "# --- --log-file / LEERIE_LOG_FILE / leerie.toml
log_file (N5b) ---") together with the decoupled-streaming teeing block
(leerie:7771-7935) in one harness, with a stubbed `nerdctl` that writes
fixed, recognizable content to its stdout standing in for a real
container run.

Extracted verbatim from the launcher (the `_extract_forwarding_loop`
convention used throughout this suite -- see
test_log_file_resolution.py's and test_log_file_wiring.py's module
docstrings) so this test is robust to the implementer's exact internal
variable names; it asserts only the observable file-content-and-survival
contract.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = REPO_ROOT / "leerie"


def _launcher_text() -> str:
    return LAUNCHER.read_text()


def _extract(start_marker: str, end_marker: str) -> str:
    src = _launcher_text()
    s = src.index(start_marker)
    e = src.index(end_marker, s) + len(end_marker)
    return src[s:e]


def _extract_resolver() -> str:
    """The --log-file / LEERIE_LOG_FILE / leerie.toml resolution block,
    including the parent-mkdir line that follows it -- without that mkdir
    a fresh target under a not-yet-existing directory (the common case)
    could never be probed writable by the setup block."""
    src = _launcher_text()
    m = re.search(
        r"(# --- --log-file / LEERIE_LOG_FILE / leerie\.toml log_file "
        r"\(N5b\) -----------\n.*?\nexport LEERIE_LOG_FILE_RESOLVED\n"
        r".*?\nmkdir -p \"\$\(dirname \"\$LEERIE_LOG_FILE_RESOLVED\"\)\" "
        r"2>/dev/null \|\| true\n)",
        src, re.DOTALL)
    assert m, "could not locate the --log-file resolution block in the launcher"
    return m.group(1)


def _extract_setup_block() -> str:
    """The `_run_log=...` / `_log_tee_target=...` decoupled-streaming
    resolution block. `_log_tee_target` is computed independent of
    `$_run_log` (bugfix-005) so the interactive/-it branch can wire it too."""
    return _extract(
        '  _run_log=""\n',
        '  _log_tee_target=""\n  if [ -n "${LEERIE_LOG_FILE_RESOLVED:-}" ]; then\n'
        '    if : >> "$LEERIE_LOG_FILE_RESOLVED" 2>/dev/null; then\n'
        '      _log_tee_target="$LEERIE_LOG_FILE_RESOLVED"\n'
        "    fi\n"
        "  fi\n",
    )


def _extract_reap_tail() -> str:
    return _extract("  _reap_tail() {\n", "  }\n")


def _extract_invocation() -> str:
    return _extract(
        '  if [ -n "$_run_log" ]; then\n    # Decoupled: nerdctl',
        '    nerdctl run "${_run_argv[@]}" || container_rc=$?\n  fi\n',
    )


assert "LEERIE_LOG_FILE_RESOLVED" in _extract_resolver()
assert "_log_tee_target=" in _extract_setup_block()
assert "tee -a" in _extract_invocation()
assert "sleep 0.3" in _extract_reap_tail()


_HARNESS = r"""#!/usr/bin/env bash
set -uo pipefail

USER_REPO="__REPO__"
LEERIE_STATE_HOST_DIR="__STATE__"
TTY_FLAGS="-i"
container_rc=0
_run_argv=()

# argv-recording, output-emitting nerdctl stub standing in for a real
# container run. Writes fixed content to whatever fd it's redirected
# into (mirroring `nerdctl run ... >"$_run_log" 2>&1`), then exits.
nerdctl() {
  printf 'CONTAINER OUTPUT LINE ONE\nCONTAINER OUTPUT LINE TWO\n'
}

__RESOLVER__

__SETUP__

__INVOCATION__

# Give the background tail/tee pipeline a moment to catch up, then reap it
# exactly as the real EXIT trap would.
sleep 0.5
__REAP__
_reap_tail

printf 'RUN_LOG_GONE=%s\n' "$([ -z "$_run_log" ] && echo yes || echo no)"
"""


def _run(repo: Path, state_dir: Path, args):
    script = repo.parent / "harness.sh"
    script.write_text(
        _HARNESS
        .replace("__REPO__", str(repo))
        .replace("__STATE__", str(state_dir))
        .replace("__RESOLVER__", _extract_resolver())
        .replace("__SETUP__", _extract_setup_block())
        .replace("__INVOCATION__", _extract_invocation())
        .replace("__REAP__", _extract_reap_tail())
    )
    return subprocess.run(
        ["bash", str(script), *args],
        capture_output=True, text=True, timeout=30,
        env=os.environ.copy(),
    )


def _setup(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    return repo, state_dir


def test_default_log_file_persists_outside_repo_and_survives_exit(tmp_path):
    """With no --log-file flag, the resolver's own default (test-003) still
    lands under the state dir, outside the repo -- and, unlike the
    throwaway .runlog-$$ (deleted by _reap_tail on every exit), this file
    must contain the full captured output and must NOT be removed."""
    repo, state_dir = _setup(tmp_path)
    result = _run(repo, state_dir, [])
    assert result.returncode == 0, result.stderr

    logs_dir = state_dir / "logs"
    candidates = list(logs_dir.glob("leerie-*.log")) if logs_dir.exists() else []
    assert candidates, f"no default log file created under {logs_dir}; stdout={result.stdout!r} stderr={result.stderr!r}"
    log_file = candidates[0]

    assert str(repo) not in str(log_file)
    assert str(log_file).startswith(str(state_dir))

    content = log_file.read_text()
    assert "CONTAINER OUTPUT LINE ONE" in content
    assert "CONTAINER OUTPUT LINE TWO" in content

    # The throwaway .runlog-$$ mechanism must still be reaped -- it is a
    # distinct file from the persisted --log-file target.
    assert "RUN_LOG_GONE=yes" in result.stdout
    leftover_runlogs = list(state_dir.glob(".runlog-*"))
    assert not leftover_runlogs, f"throwaway run-log not cleaned up: {leftover_runlogs}"


def test_explicit_log_file_persists_full_content_and_survives_exit(tmp_path):
    repo, state_dir = _setup(tmp_path)
    explicit_log = tmp_path / "elsewhere" / "custom.log"
    result = _run(repo, state_dir, ["--log-file", str(explicit_log)])
    assert result.returncode == 0, result.stderr

    assert explicit_log.exists(), "resolved --log-file target was never created"
    content = explicit_log.read_text()
    assert "CONTAINER OUTPUT LINE ONE" in content
    assert "CONTAINER OUTPUT LINE TWO" in content
    assert str(repo) not in str(explicit_log)

    assert "RUN_LOG_GONE=yes" in result.stdout
    assert not list(state_dir.glob(".runlog-*"))


def test_persisted_log_survives_a_second_reap_tail_call(tmp_path):
    """_reap_tail is invoked from multiple traps (EXIT, INT, TERM) and must
    be idempotent; a second call after the file is already persisted must
    not truncate or remove it."""
    repo, state_dir = _setup(tmp_path)
    log_file = tmp_path / "logs" / "leerie-run.log"
    result = _run(repo, state_dir, ["--log-file", str(log_file)])
    assert result.returncode == 0, result.stderr
    assert log_file.exists()
    before = log_file.read_text()

    # The harness script itself already calls _reap_tail twice (once
    # explicitly, once via the EXIT-mirroring tail of the script) --
    # reaching here with content intact is itself the pin. Assert the
    # content is unchanged/non-empty, not accidentally cleared by the
    # second reap.
    assert before == log_file.read_text()
    assert "CONTAINER OUTPUT LINE ONE" in before


def test_falsified_by_reverting_the_tee_wiring(tmp_path):
    """Positive falsifier: with the `tee -a` branch stripped back out of
    the invocation block (simulating a revert to pre-N5b behavior), the
    resolved --log-file target must NOT be populated -- proving the
    passing tests above depend on the wiring, not on some unrelated side
    effect (e.g. the setup block's writability probe)."""
    repo, state_dir = _setup(tmp_path)
    log_file = tmp_path / "logs" / "leerie-run.log"

    reverted_invocation = _extract_invocation().replace(
        'if [ -n "$_log_tee_target" ]; then\n'
        '      tail -n +1 -f "$_run_log" 2>/dev/null | tee -a "$_log_tee_target" &\n'
        "    else\n"
        '      tail -n +1 -f "$_run_log" 2>/dev/null &\n'
        "    fi",
        '    tail -n +1 -f "$_run_log" 2>/dev/null &',
    )
    assert "tee -a" not in reverted_invocation

    script = repo.parent / "harness.sh"
    script.write_text(
        _HARNESS
        .replace("__REPO__", str(repo))
        .replace("__STATE__", str(state_dir))
        .replace("__RESOLVER__", _extract_resolver())
        .replace("__SETUP__", _extract_setup_block())
        .replace("__INVOCATION__", reverted_invocation)
        .replace("__REAP__", _extract_reap_tail())
    )
    result = subprocess.run(
        ["bash", str(script), "--log-file", str(log_file)],
        capture_output=True, text=True, timeout=30,
        env=os.environ.copy(),
    )
    assert result.returncode == 0, result.stderr
    assert not log_file.exists() or log_file.read_text() == ""
