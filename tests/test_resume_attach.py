"""Invariants for the `--resume` smart router (DESIGN §6 *Smart resume
in remote mode*).

Two surfaces pinned here:

  1. Auto-discovery from $LEERIE_STATE_HOST_DIR/remote/*.json when
     `--resume` is given without `--run-id`. Mirrors the Strategy B
     PID-scan that used to live in `scripts/remote/attach.sh:119-166`.
     Single live record → resolves LEERIE_RUN_ID and continues; multiple
     → list and exit; zero → fall through to existing "no Fly machine
     pointer" error (per-id error path is preserved by guard
     `[ -n "$LEERIE_RUN_ID" ]`).

  2. `tail_with_optional_autofinalize()` in `scripts/remote/lib.sh`:
     when `_do_auto="false"`, runs the tail payload via `flyctl ssh
     console -C "sh -s"` with LEERIE_TAIL_RUN_ID prefixed; when
     `_do_auto="true"`, wraps stderr through `tee` and grep-extracts
     the AUTO_FINALIZE_TOKEN to drive `exec leerie --finalize <id>` on
     the host.

  3. Coupling: the launcher source must contain the auto-discovery
     block, the rc=75 pivot, and the four sub-mode flags. The
     `--attach` case-arm and `scripts/remote/attach.sh` must be gone.

The auto-discovery harness mirrors the launcher dispatch verbatim
(same pattern as test_launcher_resume_fly_lookup.py). The
tail_with_optional_autofinalize tests source lib.sh directly with a
stubbed flyctl.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LEERIE = REPO_ROOT / "leerie"
LIB_SH = REPO_ROOT / "scripts" / "remote" / "lib.sh"
PROVISION_SH = REPO_ROOT / "scripts" / "remote" / "provision.sh"


def _stub_flyctl(tmp_path: Path, ssh_console_rc: int = 0) -> Path:
    """flyctl stub that records every invocation. ssh-console returns
    the given rc so tests can drive the auto-finalize token logic."""
    log = tmp_path / "flyctl.log"
    fake = tmp_path / "flyctl"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        f'echo "$@" >> "{log}"\n'
        "# Capture stdin from `sh -s` invocations so tests can inspect\n"
        "# the tail payload that was piped in.\n"
        "if [ \"$1 $2\" = 'ssh console' ]; then\n"
        f'  cat - >> "{log}.stdin"\n'
        f"  exit {ssh_console_rc}\n"
        "fi\n"
        "case \"$1 $2\" in\n"
        "  'auth status') exit 0 ;;\n"
        "esac\n"
        "exit 0\n"
    )
    fake.chmod(0o755)
    return fake


# =========================================================================
# Auto-discovery: PID-scan over $LEERIE_STATE_HOST_DIR/remote/*.json
# =========================================================================

# Mirror of the auto-discovery block from the launcher (leerie:~2372-2425).
# Test inputs: LEERIE_RUN_ID, IS_RESUME, and pre-populated remote/*.json
# records.
_DISCOVERY_HARNESS = r"""
set -uo pipefail

remote_log() { echo "[leerie] $*" >&2; }

LEERIE_RUN_ID="$1"
IS_RESUME="$2"
container_rc=0

# Verbatim mirror of leerie's auto-discovery block.
if [ -z "$LEERIE_RUN_ID" ] && [ "$IS_RESUME" = "true" ]; then
  _active_records=()
  if [ -d "$LEERIE_STATE_HOST_DIR/remote" ]; then
    for _record_file in "$LEERIE_STATE_HOST_DIR/remote"/*.json; do
      [ -e "$_record_file" ] || continue
      _launcher_pid="$(basename "$_record_file" .json)"
      if [ -n "$_launcher_pid" ] && kill -0 "$_launcher_pid" 2>/dev/null; then
        _active_records+=("$_record_file")
      fi
    done
  fi
  case "${#_active_records[@]}" in
    0)
      :
      ;;
    1)
      LEERIE_RUN_ID="$(python3 -c '
import json, sys
try:
    d = json.load(open(sys.argv[1]))
    print(d.get("run_id") or "")
except Exception:
    pass
' "${_active_records[0]}" 2>/dev/null || true)"
      export LEERIE_RUN_ID
      if [ -n "$LEERIE_RUN_ID" ]; then
        remote_log "--resume: auto-discovered run-id $LEERIE_RUN_ID from active launcher record"
      fi
      ;;
    *)
      remote_log "--resume: multiple active launches — pass the run-id to disambiguate:"
      for _f in "${_active_records[@]}"; do
        python3 -c "
import json, sys
try:
    d = json.load(open(sys.argv[1]))
    print(f'  pid={d.get(\"launcher_pid\",\"?\")} run={d.get(\"run_id\",\"?\")} machine={d.get(\"fly_machine_id\",\"?\")}')
except Exception:
    pass
" "$_f" >&2 || true
      done
      container_rc=1
      ;;
  esac
fi

echo "RESULT LEERIE_RUN_ID=$LEERIE_RUN_ID container_rc=$container_rc"
"""


def _write_record(state_dir: Path, launcher_pid: int, run_id: str,
                  machine_id: str = "mach-abc") -> Path:
    """Write a remote/<pid>.json record matching provision.sh's schema."""
    remote_dir = state_dir / "remote"
    remote_dir.mkdir(parents=True, exist_ok=True)
    record = remote_dir / f"{launcher_pid}.json"
    record.write_text(json.dumps({
        "fly_app": "leerie",
        "fly_machine_id": machine_id,
        "started_at": "2026-06-06T00:00:00+00:00",
        "run_id": run_id,
        "launcher_pid": launcher_pid,
        "host_no_push": False,
    }))
    return record


def _run_discovery(tmp_path: Path, run_id: str, is_resume: str
                   ) -> subprocess.CompletedProcess:
    state_dir = tmp_path / "leerie-state"
    return subprocess.run(
        ["bash", "-c", _DISCOVERY_HARNESS, "_", run_id, is_resume],
        env={
            "PATH": "/usr/bin:/bin",
            "LEERIE_STATE_HOST_DIR": str(state_dir),
        },
        capture_output=True, text=True,
    )


def test_auto_discovery_resolves_single_live_record(tmp_path: Path):
    """Exactly one live launcher PID in remote/ → that record's run_id
    is exported as LEERIE_RUN_ID and the dispatch proceeds (rc=0)."""
    state_dir = tmp_path / "leerie-state"
    # Use os.getpid() — guaranteed alive during the test.
    _write_record(state_dir, os.getpid(), "feat-x-deadbe")
    r = _run_discovery(tmp_path, run_id="", is_resume="true")
    assert r.returncode == 0, r.stderr
    assert "LEERIE_RUN_ID=feat-x-deadbe container_rc=0" in r.stdout, r.stdout
    assert "auto-discovered run-id feat-x-deadbe" in r.stderr, r.stderr


def test_auto_discovery_skips_stale_pid_records(tmp_path: Path):
    """A record whose launcher_pid is gone → treated as stale; treated
    as the zero-record case (falls through, no LEERIE_RUN_ID set)."""
    state_dir = tmp_path / "leerie-state"
    # Pick an unlikely PID. /proc/sys/kernel/pid_max on Linux is usually
    # 4194304; on macOS pid_max is 99999. 99998 is well within both and
    # not in use by any normal process.
    stale_pid = 99998
    while True:
        try:
            os.kill(stale_pid, 0)
        except ProcessLookupError:
            break
        except PermissionError:
            stale_pid -= 1
            continue
        stale_pid -= 1
        if stale_pid <= 1:
            raise RuntimeError("could not find a stale pid for test")
    _write_record(state_dir, stale_pid, "feat-stale-aaaaaa")
    r = _run_discovery(tmp_path, run_id="", is_resume="true")
    assert r.returncode == 0, r.stderr
    # No discovery → LEERIE_RUN_ID stays empty.
    assert "LEERIE_RUN_ID= container_rc=0" in r.stdout, r.stdout
    assert "auto-discovered" not in r.stderr, r.stderr


def test_auto_discovery_zero_records(tmp_path: Path):
    """No records at all → LEERIE_RUN_ID stays empty; downstream
    'no Fly machine pointer' error path takes over (not tested here)."""
    r = _run_discovery(tmp_path, run_id="", is_resume="true")
    assert r.returncode == 0, r.stderr
    assert "LEERIE_RUN_ID= container_rc=0" in r.stdout, r.stdout


def test_auto_discovery_multiple_records_lists_and_exits_1(tmp_path: Path):
    """Two live records → user-disambiguation: list each pid/run/machine
    on stderr and set container_rc=1."""
    state_dir = tmp_path / "leerie-state"
    _write_record(state_dir, os.getpid(), "feat-one-aaaaaa", "mach-a")
    _write_record(state_dir, os.getppid(), "feat-two-bbbbbb", "mach-b")
    r = _run_discovery(tmp_path, run_id="", is_resume="true")
    assert r.returncode == 0, r.stderr  # harness shell exits 0
    assert "LEERIE_RUN_ID= container_rc=1" in r.stdout, r.stdout
    assert "multiple active launches" in r.stderr, r.stderr
    assert "feat-one-aaaaaa" in r.stderr, r.stderr
    assert "feat-two-bbbbbb" in r.stderr, r.stderr


def test_auto_discovery_skipped_when_run_id_given(tmp_path: Path):
    """An explicit --run-id must short-circuit auto-discovery. Even with
    a live record present, LEERIE_RUN_ID stays at the user's value."""
    state_dir = tmp_path / "leerie-state"
    _write_record(state_dir, os.getpid(), "feat-discovered-aaaaaa")
    r = _run_discovery(tmp_path, run_id="explicit-run-zzzzzz",
                       is_resume="true")
    assert r.returncode == 0, r.stderr
    assert "LEERIE_RUN_ID=explicit-run-zzzzzz container_rc=0" in r.stdout, r.stdout
    assert "auto-discovered" not in r.stderr, r.stderr


def test_auto_discovery_skipped_when_not_resume(tmp_path: Path):
    """IS_RESUME=false (fresh run) must not trigger auto-discovery,
    even if records exist. Otherwise a fresh run would hijack an
    existing launcher's run-id."""
    state_dir = tmp_path / "leerie-state"
    _write_record(state_dir, os.getpid(), "feat-live-aaaaaa")
    r = _run_discovery(tmp_path, run_id="", is_resume="false")
    assert r.returncode == 0, r.stderr
    assert "LEERIE_RUN_ID= container_rc=0" in r.stdout, r.stdout
    assert "auto-discovered" not in r.stderr, r.stderr


# =========================================================================
# tail_with_optional_autofinalize: lib.sh helper
# =========================================================================

# Source lib.sh and call the helper with a tiny tail-script stub.
# The tail-script stub is just a literal `echo` so we can pipe it
# through `flyctl ssh console`'s stdin (the stub captures stdin).
_TAIL_HARNESS = f"""
set -uo pipefail

remote_log() {{ echo "[leerie] $*" >&2; }}
# render_tail_wrapper is defined in lib.sh but we override for tests.
# The point of this test is the *wrapper around it*, not the tail
# wrapper itself (covered by test_render_tail_wrapper.py).
. {LIB_SH}

# Invoke the helper. Args mirror the production signature.
LEERIE_REPO='/tmp/fake-leerie-repo' \\
  tail_with_optional_autofinalize \\
  "$1" "$2" "$3" "$4" "$5"
echo "HELPER_RC=$?"
"""


def test_tail_helper_non_autofinalize_pipes_payload_via_flyctl(tmp_path: Path):
    """_do_auto=false: helper invokes `flyctl ssh console -C "sh -s"`
    with the tail payload on stdin. Token plumbing absent."""
    fake = _stub_flyctl(tmp_path)
    r = subprocess.run(
        ["bash", "-c", _TAIL_HARNESS, "_",
         "echo TAIL_PAYLOAD_BODY",   # _tail_script
         "feat-tail-aaaaaa",          # _run_id
         "mach-LIVE-001",             # _machine_id
         "leerie",                    # _app
         "false"],                    # _do_auto
        env={
            "PATH": f"{tmp_path}:/usr/bin:/bin",
        },
        capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr
    log = (tmp_path / "flyctl.log").read_text()
    assert "ssh console --app leerie --machine mach-LIVE-001 --pty=false -C sh -s" in log, log
    stdin_capture = (tmp_path / "flyctl.log.stdin").read_text()
    # LEERIE_TAIL_RUN_ID is the load-bearing handoff to render_tail_wrapper.
    assert "LEERIE_TAIL_RUN_ID='feat-tail-aaaaaa'" in stdin_capture, stdin_capture
    # The user's tail script body must reach flyctl.
    assert "echo TAIL_PAYLOAD_BODY" in stdin_capture, stdin_capture
    # No token plumbing when _do_auto=false.
    assert "AUTO_FINALIZE_TOKEN" not in stdin_capture, (
        "Token must NOT be exported when _do_auto=false.\n" + stdin_capture
    )


def test_tail_helper_autofinalize_sets_token_in_payload(tmp_path: Path):
    """_do_auto=true: helper exports AUTO_FINALIZE_TOKEN into the
    payload stdin and tees stderr through a tempfile. The token is the
    sentinel render_tail_wrapper will echo on its last stderr line.

    The helper must `return`, not `exit`, when no auto-finalize token
    is captured — the helper is sourced into the launcher shell, so
    `exit` would terminate the launcher and bypass `decide_teardown`
    (DESIGN §6 *Detached orchestrator*). Confirm via the trailing
    `HELPER_RC=...` line that the harness shell ran *after* the
    helper returned."""
    fake = _stub_flyctl(tmp_path, ssh_console_rc=1)  # non-zero so the
    # exec leerie --finalize branch doesn't fire (would replace this
    # process and kill the test).
    r = subprocess.run(
        ["bash", "-c", _TAIL_HARNESS, "_",
         "echo TAIL_PAYLOAD_BODY",
         "feat-finalize-aaaaaa",
         "mach-LIVE-002",
         "leerie",
         "true"],
        env={
            "PATH": f"{tmp_path}:/usr/bin:/bin",
        },
        capture_output=True, text=True,
    )
    # The harness shell continues past the helper call (helper returned,
    # didn't exit). Last command is `echo HELPER_RC=...` which exits 0.
    assert r.returncode == 0, (r.stdout, r.stderr)
    # The helper's return code (1 from the stub) reaches $? in the
    # harness and gets captured by the echo.
    assert "HELPER_RC=1" in r.stdout, (
        "Helper should return rc=1; harness should still run echo "
        "afterwards. Output:\n" + r.stdout
    )
    stdin_capture = (tmp_path / "flyctl.log.stdin").read_text()
    assert "AUTO_FINALIZE_TOKEN='<<LEERIE_AUTOFIN_" in stdin_capture, stdin_capture
    assert "export AUTO_FINALIZE_TOKEN" in stdin_capture, stdin_capture
    assert "LEERIE_TAIL_RUN_ID='feat-finalize-aaaaaa'" in stdin_capture, stdin_capture


# =========================================================================
# Coupling: launcher source pins for the new --resume surface
# =========================================================================

def test_launcher_contains_auto_discovery_block():
    """The auto-discovery block must use the kill -0 PID liveness check
    plus a python3 reader for run_id extraction. A future refactor that
    drops the kill -0 check would re-introduce stale-pid contagion."""
    launcher = LEERIE.read_text()
    assert "kill -0 \"$_launcher_pid\"" in launcher, (
        "Auto-discovery must use kill -0 to filter stale records."
    )
    assert "$LEERIE_STATE_HOST_DIR/remote\"/*.json" in launcher, (
        "Auto-discovery must scan $LEERIE_STATE_HOST_DIR/remote/."
    )
    assert "auto-discovered run-id" in launcher, (
        "Auto-discovery must log which run-id it picked (audit trail)."
    )


def test_launcher_contains_rc75_pivot():
    """The rc=75 pivot must invoke tail_with_optional_autofinalize OR
    the shell payload; container_rc=130 must be set so decide_teardown
    routes through the detach-banner arm rather than killing the live
    machine (DESIGN §6 *Single owner per run dir*, contagion fix)."""
    launcher = LEERIE.read_text()
    assert 'if [ "$_launch_rc" = "75" ]; then' in launcher, (
        "The rc=75 branch must exist."
    )
    assert "tail_with_optional_autofinalize" in launcher, (
        "rc=75 default branch must invoke the shared tail helper."
    )
    # Container_rc=130 is the critical routing decision — see DESIGN §6.
    assert "container_rc=130" in launcher, (
        "rc=75 pivot must set container_rc=130 so decide_teardown "
        "leaves the live machine alone."
    )


def test_launcher_contains_resume_submode_flag_extraction():
    """The launcher-only sub-mode flags (--shell, --auto-finalize) must
    be extracted into RESUME_* variables before the REWRITTEN_ARGS
    filter. Without extraction, the rc=75 pivot can't branch on them.

    `--all-logs` was scoped out of v1 — see CHANGELOG and the
    `render_tail_wrapper` plumbing follow-up."""
    launcher = LEERIE.read_text()
    assert "RESUME_SHELL=false" in launcher, "RESUME_SHELL not initialized"
    assert "RESUME_AUTO_FINALIZE=false" in launcher, (
        "RESUME_AUTO_FINALIZE not initialized"
    )
    assert "--shell)         RESUME_SHELL=true" in launcher, (
        "--shell extraction missing"
    )
    # --all-logs must NOT be there until the follow-up lands. A
    # parsing arm without a consumer is dead code that misleads the
    # user.
    assert "--all-logs" not in launcher, (
        "--all-logs parse arm found in launcher but render_tail_wrapper "
        "doesn't honor it — dead flag would silently no-op. Either "
        "implement LEERIE_TAIL_ALL_LOGS plumbing OR remove the flag."
    )


def test_launcher_strips_submode_flags_from_rewritten_args():
    """The orchestrator's argparse doesn't know --shell or
    --auto-finalize. The REWRITTEN_ARGS filter loop must strip them.
    Mirrors the same convention as --no-re-seed / --no-runtime-install."""
    launcher = LEERIE.read_text()
    for flag in ("--shell)", "--auto-finalize)"):
        assert flag in launcher, f"Missing case-arm: {flag}"
    # Spot-check that the strip-comment is present.
    assert "opts into bash shell on the --resume rc=75 pivot" in launcher, (
        "--shell filter arm's launcher-only marker missing"
    )


def test_attach_case_arm_is_id_dispatched():
    """The --attach case-arm exists and dispatches by ID type.

    Previously --attach was an out-of-band launcher verb that just
    SSH'd into a Fly machine. That one was removed. Under v5 Shape A
    --attach is an ID-dispatched verb: UUID → poll
    $LEERIE_STATE_HOST_DIR/runs/*/run.json filtered by chain_id until
    every chain run reaches a terminal state; non-UUID → not-yet-
    implemented for run-mode (the existing --resume reattaches
    single runs).

    This test pins the behavior so a future regression doesn't
    silently bring back the old attach.sh-driven SSH path.
    """
    launcher = LEERIE.read_text()
    assert "--attach)" in launcher, (
        "--attach) case-arm missing from launcher (expected the new "
        "ID-dispatched chain attach verb)"
    )
    assert "ID-dispatched attach verb" in launcher, (
        "--attach case-arm exists but is not the new chain-mode "
        "implementation — check whether the old SSH-style attach has "
        "been reintroduced."
    )


def test_attach_sh_removed():
    """scripts/remote/attach.sh must not exist."""
    assert not (REPO_ROOT / "scripts" / "remote" / "attach.sh").exists(), (
        "attach.sh still exists — should have been deleted."
    )


def test_lib_sh_exports_tail_with_optional_autofinalize():
    """The helper must be defined in lib.sh — currently called by the
    rc=75 pivot. (The fresh-launch tail could also adopt it in a
    follow-up; for v1, only the rc=75 pivot is wired.)"""
    libsh = LIB_SH.read_text()
    assert "tail_with_optional_autofinalize()" in libsh, (
        "tail_with_optional_autofinalize() not defined in lib.sh"
    )
    # Token plumbing: the LEERIE_AUTOFIN_ sentinel binds the helper to
    # render_tail_wrapper's stderr emission (lib.sh:309-311).
    assert "LEERIE_AUTOFIN_" in libsh, (
        "AUTO_FINALIZE_TOKEN sentinel missing from helper"
    )
    # The host-side exec is what makes auto-finalize work — must use
    # ${LEERIE_REPO}/leerie, not just `leerie`.
    assert "exec \"${LEERIE_REPO}/leerie\" --finalize" in libsh, (
        "Helper must exec leerie --finalize via $LEERIE_REPO"
    )


def test_lib_sh_helper_does_not_install_exit_trap():
    """Regression guard for the audit defect A: the helper must NOT
    install a `trap '...' EXIT`. The helper is sourced into the
    launcher's shell, which already has `trap 'decide_teardown'
    EXIT INT TERM` registered by provision.sh. An EXIT trap inside
    the helper would clobber decide_teardown — on every code path,
    not just inside the helper — and the Fly machine would never
    receive its post-run sync/finalize/destroy disposition.

    See DESIGN §6 *Detached orchestrator (remote mode)* and
    scripts/remote/provision.sh:627 (`trap 'decide_teardown' EXIT
    INT TERM`)."""
    libsh = LIB_SH.read_text()
    # Locate the helper body and scan only its lines.
    import re
    match = re.search(
        r"^tail_with_optional_autofinalize\(\) \{(.*?)^\}",
        libsh, re.DOTALL | re.MULTILINE,
    )
    assert match, "tail_with_optional_autofinalize() body not found"
    body = match.group(1)
    # The literal "trap" keyword followed by anything ending in "EXIT"
    # within the helper body would clobber decide_teardown.
    for line in body.splitlines():
        stripped = line.strip()
        # Skip comments — narrative discussion of the bug is fine.
        if stripped.startswith("#"):
            continue
        assert not re.search(r"\btrap\b.*\bEXIT\b", line), (
            f"Helper installs an EXIT trap, clobbering decide_teardown. "
            f"Use inline `rm -f` instead. Offending line:\n  {line!r}"
        )


def test_lib_sh_helper_returns_does_not_exit():
    """Regression guard for the audit defect B: the helper must use
    `return`, not `exit`, on its non-exec terminal paths. The helper
    is sourced into the launcher's shell, so `exit` would terminate
    the launcher entirely — bypassing the rc=75 pivot's
    `container_rc=130` assignment and the detach banner.

    The only valid `exit*` in the helper is `exec` (which replaces
    the process, intentionally taking down the launcher to be
    replaced by `leerie --finalize`)."""
    libsh = LIB_SH.read_text()
    import re
    match = re.search(
        r"^tail_with_optional_autofinalize\(\) \{(.*?)^\}",
        libsh, re.DOTALL | re.MULTILINE,
    )
    assert match, "tail_with_optional_autofinalize() body not found"
    body = match.group(1)
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        # Look for a bare `exit` (not `exec`).
        # The `[^ec]` after exit excludes `exec` which is fine.
        assert not re.search(r"^\s*exit\b", line), (
            f"Helper uses `exit` (terminates launcher) instead of "
            f"`return` (returns to caller). Offending line:\n  {line!r}"
        )
