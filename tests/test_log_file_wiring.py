"""bugfix-002 (N5b, part 2): wiring the resolved --log-file path into the
launcher's own output teeing.

`test_log_file_resolution.py` covers LEERIE_LOG_FILE_RESOLVED's value only.
This file covers the actual I/O: for the piped/non-TTY local-runtime path
(the decoupled-streaming mechanism at leerie:7771-7935 that already writes
container output to a launcher-owned run-log file and tails it to our own
stdout, DESIGN §6 "Launcher hang on abnormal container exit"), the same
stream is now also teed into LEERIE_LOG_FILE_RESOLVED -- replacing the
operator's own `leerie task | tee <path>` habit, which N5 (docs/DESIGN.md)
identifies as the leak vector: a tee target left inside $USER_REPO is
bind-mounted whole into every worker container.

Extracts the exact setup block (`_run_log=... _log_tee_target=...`), the
`_reap_tail` teardown function, and the tail/tee invocation verbatim from
the launcher (the `_extract_forwarding_loop` convention used throughout
this suite -- see test_log_file_resolution.py's module docstring) and
drives them in a harness with a stubbed `nerdctl` that writes known
content to the run-log file it's redirected into, standing in for a real
container run. No network, no real container.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from tests.log_file_extract_helpers import (
    _extract,
    extract_invocation as _extract_invocation,
    extract_reap_tail as _extract_reap_tail,
    extract_setup_block as _extract_setup_block,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = REPO_ROOT / "leerie"


def _extract_mkdir_line() -> str:
    """The parent-directory-creation line added alongside LEERIE_LOG_FILE_RESOLVED
    (leerie:916): without it, a fresh --log-file target under a not-yet-existing
    directory (the common case -- state_dir/logs/ on a first run) can never be
    probed writable by the setup block below."""
    return _extract(
        'mkdir -p "$(dirname "$LEERIE_LOG_FILE_RESOLVED")" 2>/dev/null || true\n',
        'mkdir -p "$(dirname "$LEERIE_LOG_FILE_RESOLVED")" 2>/dev/null || true\n',
    )


assert "_log_tee_target=" in _extract_setup_block()
assert "tee -a" in _extract_invocation()
assert "script -qe -c" in _extract_invocation()
assert "sleep 0.3" in _extract_reap_tail()


_HARNESS = r"""#!/usr/bin/env bash
set -uo pipefail

LEERIE_STATE_HOST_DIR="__STATE__"
LEERIE_LOG_FILE_RESOLVED="__LOGFILE__"
TTY_FLAGS="__TTYFLAGS__"
container_rc=0
_run_argv=()

# argv-recording, output-emitting nerdctl stub standing in for a real
# container run. Writes fixed content to whatever fd it's redirected into
# (mirroring `nerdctl run ... >"$_run_log" 2>&1`), then exits.
nerdctl() {
  echo "run" >> /dev/null   # consume the leading subcommand harmlessly
  printf 'hello from container\nsecond line\n'
}

__MKDIR__

__SETUP__

__INVOCATION__

# Give the background tail/tee pipeline a moment to catch up, then reap it
# exactly as the real EXIT trap would.
sleep 0.5
__REAP__
_reap_tail

printf 'RUN_LOG_GONE=%s\n' "$([ -z "$_run_log" ] && echo yes || echo no)"
printf 'CONTAINER_RC=%s\n' "$container_rc"
"""


def _write_nerdctl_stub(bin_dir: Path) -> None:
    """A real executable `nerdctl` on PATH -- required for the interactive
    branch, which invokes it through `script -c "nerdctl run ..."` (a
    fresh $SHELL -c subprocess that cannot see the harness's own bash
    function)."""
    bin_dir.mkdir(exist_ok=True)
    stub = bin_dir / "nerdctl"
    stub.write_text(
        "#!/usr/bin/env bash\nprintf 'hello from container\\nsecond line\\n'\n"
    )
    stub.chmod(0o755)


def _run(
    state_dir: Path,
    log_file: Path,
    tty_flags: str = "-i",
    stdout_is_tty: bool = False,
    with_path_stub: bool = False,
):
    script = state_dir / "harness.sh"
    script.write_text(
        _HARNESS
        .replace("__STATE__", str(state_dir))
        .replace("__LOGFILE__", str(log_file))
        .replace("__TTYFLAGS__", tty_flags)
        .replace("__MKDIR__", _extract_mkdir_line())
        .replace("__SETUP__", _extract_setup_block())
        .replace("__INVOCATION__", _extract_invocation())
        .replace("__REAP__", _extract_reap_tail())
    )
    env = os.environ.copy()
    if with_path_stub:
        bin_dir = state_dir / "bin"
        _write_nerdctl_stub(bin_dir)
        env["PATH"] = f"{bin_dir}:{env['PATH']}"
    # Run with stdout/stderr piped (never a TTY) to match how this suite's
    # other piped-mode tests exercise the operator's own `| tee` pattern,
    # and how subprocess.run always invokes children.
    return subprocess.run(
        ["bash", str(script)],
        capture_output=True, text=True, timeout=30,
        env=env,
    )


def _setup(tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    return state_dir


def test_piped_run_tees_output_to_resolved_log_file(tmp_path):
    state_dir = _setup(tmp_path)
    log_file = tmp_path / "logs" / "leerie-run.log"
    result = _run(state_dir, log_file)
    assert result.returncode == 0, result.stderr
    assert "hello from container" in result.stdout
    assert log_file.exists(), "resolved --log-file target was never created"
    logged = log_file.read_text()
    assert "hello from container" in logged
    assert "second line" in logged


def test_log_file_lands_under_state_dir_not_repo(tmp_path):
    """Mirrors the resolver's own default (test_log_file_resolution.py) but
    checks the actual written artifact, not just the resolved string."""
    state_dir = _setup(tmp_path)
    log_file = state_dir / "logs" / "leerie-run.log"
    result = _run(state_dir, log_file)
    assert result.returncode == 0, result.stderr
    assert log_file.exists()
    assert str(log_file).startswith(str(state_dir))


def test_explicit_log_file_target_is_honored(tmp_path):
    """An explicit --log-file (already resolved upstream into
    LEERIE_LOG_FILE_RESOLVED by bugfix-001) is the file that receives the
    tee, not some other default path."""
    state_dir = _setup(tmp_path)
    default_log = state_dir / "logs" / "leerie-default.log"
    explicit_log = tmp_path / "explicit" / "custom.log"
    result = _run(state_dir, explicit_log)
    assert result.returncode == 0, result.stderr
    assert explicit_log.exists()
    assert "hello from container" in explicit_log.read_text()
    assert not default_log.exists()


def test_falsified_by_reverting_the_tee_wiring(tmp_path, monkeypatch):
    """Positive falsifier: an invocation block with the `tee -a` branch
    stripped back out (simulating a revert) must NOT populate the log file,
    proving the passing tests above depend on the wiring rather than on
    some other side effect (e.g. the setup block's `mkdir`/probe write)."""
    state_dir = _setup(tmp_path)
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
    script = state_dir / "harness.sh"
    script.write_text(
        _HARNESS
        .replace("__STATE__", str(state_dir))
        .replace("__LOGFILE__", str(log_file))
        .replace("__TTYFLAGS__", "-i")
        .replace("__MKDIR__", _extract_mkdir_line())
        .replace("__SETUP__", _extract_setup_block())
        .replace("__INVOCATION__", reverted_invocation)
        .replace("__REAP__", _extract_reap_tail())
    )
    result = subprocess.run(
        ["bash", str(script)],
        capture_output=True, text=True, timeout=30,
        env=os.environ.copy(),
    )
    assert result.returncode == 0, result.stderr
    assert "hello from container" in result.stdout   # streaming itself unaffected
    assert not log_file.exists() or log_file.read_text() == ""


def test_interactive_tty_path_is_never_gated_into_run_log(tmp_path):
    """Regression guard for the scope_note: the -it interactive/--clarify
    path (TTY_FLAGS=-it) never enters the decoupled/_run_log branch at all
    -- `_run_log` stays empty -- so teeing wiring added here cannot affect
    the documented interactive stdin/pty contract at leerie:7580-7702. It
    is teed via `script`(1) instead (see
    test_interactive_tty_path_is_teed_via_script below), not via
    `$_run_log`/`tail`/`tee`."""
    state_dir = _setup(tmp_path)
    log_file = tmp_path / "logs" / "leerie-run.log"
    result = _run(state_dir, log_file, tty_flags="-it", with_path_stub=True)
    assert result.returncode == 0, result.stderr
    assert "RUN_LOG_GONE=yes" in result.stdout


def test_interactive_tty_path_is_teed_via_script(tmp_path):
    """bugfix-005: --log-file must no longer be a silent no-op in the -it
    interactive/--clarify path. Requires `script`(1) on PATH and a real
    `nerdctl` executable (not a bash function -- `script -c` runs the
    command in a fresh `$SHELL -c` subprocess that cannot see the
    harness's shell functions)."""
    state_dir = _setup(tmp_path)
    log_file = tmp_path / "logs" / "leerie-run.log"
    result = _run(state_dir, log_file, tty_flags="-it", with_path_stub=True)
    assert result.returncode == 0, result.stderr
    assert log_file.exists(), "resolved --log-file target was never created"
    logged = log_file.read_text()
    assert "hello from container" in logged
    assert "second line" in logged


def test_interactive_tty_path_falls_back_when_script_unavailable(tmp_path, monkeypatch):
    """When `script`(1) is not on PATH, the interactive branch must fall
    back to nerdctl inheriting stdout directly (the pre-fix behavior) --
    never fail the run, and never attempt to pipe nerdctl's own stdout
    (which would defeat `-t`)."""
    state_dir = _setup(tmp_path)
    log_file = tmp_path / "logs" / "leerie-run.log"
    # A minimal PATH with only the nerdctl stub and no `script` binary.
    bin_dir = state_dir / "bin"
    _write_nerdctl_stub(bin_dir)
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
    # A curated PATH: symlinks to just the externals this -it scenario
    # actually invokes (mkdir, sleep -- `_tail_pid` stays empty here, so
    # `_reap_tail`'s jobs/awk/kill branch never runs), the nerdctl stub,
    # and NOT `script` -- deliberately excluded to prove the fallback,
    # even though the real `script` binary lives in the same system
    # directory as bash/mkdir/sleep on this host.
    import shutil
    bash_path = shutil.which("bash")
    for _tool in ("mkdir", "sleep"):
        (bin_dir / _tool).symlink_to(shutil.which(_tool))
    env = os.environ.copy()
    env["PATH"] = str(bin_dir)
    result = subprocess.run(
        [bash_path, str(script_path)],
        capture_output=True, text=True, timeout=30,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert "hello from container" in result.stdout   # nerdctl still ran, direct


def test_interactive_script_command_is_a_single_quoted_string(tmp_path):
    """The Linux `script -c` branch must pass the whole `nerdctl run ...`
    invocation as one shell-quoted string, never a raw, unquoted argv
    concatenation that would break on paths/values containing spaces or
    shell metacharacters."""
    invocation = _extract_invocation()
    assert '_nerdctl_run_quoted="nerdctl run"' in invocation
    assert "printf -v _rarg_q '%q' \"$_rarg\"" in invocation
    assert 'script -qe -c "$_nerdctl_run_quoted" -a "$_log_tee_target"' in invocation


def _write_bsd_script_stub(bin_dir: Path) -> None:
    """A `script`(1) stand-in that reproduces the one BSD-script property
    this branch depends on and none of the others: it ALWAYS exits 0,
    regardless of the wrapped command's real exit status (there is no
    util-linux -e/--return equivalent on BSD/macOS script(1) -- confirmed
    via `script --help`). It still execs its trailing positional args
    directly (no shell), matching real BSD script's argv contract, and
    still appends to the `-a <file>` target so the existing teeing
    assertions keep working."""
    bin_dir.mkdir(exist_ok=True)
    stub = bin_dir / "script"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        "# BSD-style: script -q -a <file> <command...>\n"
        "_out=\"$3\"\n"
        "shift 3\n"
        '"$@" >>"$_out" 2>&1\n'
        "exit 0\n"
    )
    stub.chmod(0o755)


def _write_darwin_uname_stub(bin_dir: Path) -> None:
    bin_dir.mkdir(exist_ok=True)
    stub = bin_dir / "uname"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        'if [ "$1" = "-s" ]; then echo Darwin; else /usr/bin/uname "$@"; fi\n'
    )
    stub.chmod(0o755)


def test_darwin_script_path_reports_real_container_exit_code(tmp_path):
    """bugfix-005 completeness gap: on macOS, BSD script(1) always exits 0,
    so `script -q -a ... nerdctl run ...` alone can never surface a real
    container failure -- `container_rc` must come from the sentinel-file
    mechanism, not from script(1)'s own exit status."""
    state_dir = _setup(tmp_path)
    log_file = tmp_path / "logs" / "leerie-run.log"
    bin_dir = state_dir / "bin"
    bin_dir.mkdir()
    # nerdctl stub that fails, mirroring a real containerized-run failure.
    nerdctl_stub = bin_dir / "nerdctl"
    nerdctl_stub.write_text(
        "#!/usr/bin/env bash\n"
        "printf 'hello from container\\nsecond line\\n'\n"
        "exit 3\n"
    )
    nerdctl_stub.chmod(0o755)
    _write_bsd_script_stub(bin_dir)
    _write_darwin_uname_stub(bin_dir)
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
    result = subprocess.run(
        ["bash", str(script_path)],
        capture_output=True, text=True, timeout=30,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert "CONTAINER_RC=3" in result.stdout, (
        "the Darwin branch must recover nerdctl's real exit code via the "
        "sentinel file, not rely on BSD script(1)'s own (always-0) status: "
        f"{result.stdout!r}"
    )
    assert log_file.exists()
    logged = log_file.read_text()
    assert "hello from container" in logged
    assert "second line" in logged


def test_darwin_script_path_reports_success_exit_code(tmp_path):
    """Positive control for the sentinel mechanism: a successful nerdctl
    run still reports CONTAINER_RC=0 through the same Darwin path (proving
    the sentinel isn't just always non-zero)."""
    state_dir = _setup(tmp_path)
    log_file = tmp_path / "logs" / "leerie-run.log"
    bin_dir = state_dir / "bin"
    _write_nerdctl_stub(bin_dir)
    _write_bsd_script_stub(bin_dir)
    _write_darwin_uname_stub(bin_dir)
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
    result = subprocess.run(
        ["bash", str(script_path)],
        capture_output=True, text=True, timeout=30,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert "CONTAINER_RC=0" in result.stdout


def test_unwritable_log_file_disables_teeing_without_failing(tmp_path):
    """A --log-file target under a directory that cannot be created (e.g.
    a component of the path is an existing regular file) must not fail the
    run -- teeing silently disables, matching the setup block's own
    documented best-effort contract."""
    state_dir = _setup(tmp_path)
    blocker = tmp_path / "blocked-file"
    blocker.write_text("not a directory")
    unwritable_log = blocker / "sub" / "leerie.log"
    result = _run(state_dir, unwritable_log)
    assert result.returncode == 0, result.stderr
    assert "hello from container" in result.stdout
    assert not unwritable_log.exists()
