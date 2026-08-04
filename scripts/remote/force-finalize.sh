#!/usr/bin/env bash
# scripts/remote/force-finalize.sh — patch finished_at into a stuck run's
# run.json on the Fly Machine, after verifying the orchestrator process is
# dead.
#
# When does this run?  `leerie finalize <run-id> --force`.  Today's
# fetch-branch.sh discovery loop scans .leerie/runs/*/run.json on the machine
# for entries with `finished_at` set and `pushed_at` unset (DESIGN §6
# *Finalization*).  When the orchestrator dies before phase_finalize (ENOSPC
# mid-merge, worker crash, kill -9, etc.), `finished_at` is never written and
# the discovery loop returns zero — the user cannot recover the work via
# `leerie finalize` because the run isn't "finalized" from the host's view.
#
# The recovery procedure is mechanical: SSH in, check the orchestrator is
# really dead, patch run.json with `finished_at` + audit fields, exit, then
# re-run finalize.  Doing this by hand requires knowing internals;
# automating it via finalize --force keeps the recovery on the user's well-trodden
# path.
#
# Safety belt: this script REFUSES to patch a run where the orchestrator
# process is still alive. Two-layered predicate; either layer firing
# refuses the patch.
#
# Layer 1 — /proc cross-check (authoritative). Scan /proc/[0-9]*/cmdline
# for any process whose NUL-separated argv contains BOTH the literal
# "orchestrator/leerie.py" AND the run-id (as a discrete NUL-delimited
# token). A hit means a live orchestrator owns this run regardless of
# what orchestrator.pid says. This layer protects against the stale-pid
# contagion described in DESIGN §6 *Single owner per run dir*: the
# launcher writes the pid file between Popen and State.__init__, so
# a stillborn flock-loser's pid can land in the file while the real
# orchestrator (the winner) keeps running.
#
# Layer 2 — orchestrator.pid check (defensive, pid-reuse audit).
# Retained because it can speak when /proc cannot (test fixtures
# without /proc, future hardening), and because a REFUSE-ALIVE
# distinct from REFUSE-ALIVE-SCAN names the source of the refusal in
# audit logs.
#
#     pid file MISSING                         → REFUSE-NOPID
#                                                 (early failure or
#                                                  tampering — bail to manual)
#     pid file present + kill -0 succeeds +    → REFUSE-ALIVE
#       /proc/<pid>/cmdline contains "python"   (defensive; covers the
#                                                pid-reuse case)
#     pid file present + kill -0 fails ESRCH + → SAFE (no liveness signal
#       /proc scan also empty                    from either layer)
#
#   * Nothing in orchestrator/leerie.py ever deletes the pid file — it is
#     the expected stale artifact after a clean exit.
#
# The /proc/<pid>/cmdline guard catches pid reuse — short-lived per-run
# Fly machines make this very unlikely but it's a cheap check.
#
# Why cmdline and not comm: the kernel sets /proc/<pid>/comm from argv[0]
# of the parent's execve call (basename of the *invoked* binary), NOT
# from the shebang interpreter the kernel actually loaded. For a
# pip-installed Python script like `pytest`, that means comm = "pytest"
# even though the running ELF is /usr/bin/python3 — "python" not in
# "pytest", so a comm-based check would let an alive orchestrator slip
# through on any non-trivial launcher. /proc/<pid>/cmdline holds the
# full execve argv (NUL-separated) after shebang resolution, which
# always names the interpreter explicitly. Verified inside
# python:3.10-slim on 2026-06-02 that comm='pytest' but cmdline starts
# with '/usr/local/bin/python3.10\0/usr/local/bin/pytest\0…'.
#
# Platform note: the Python payload runs ON THE MACHINE (Linux), which
# always has /proc. The pid-reuse guard works as designed there. If the
# payload is ever exercised on a system without /proc (e.g. a macOS
# host test fixture), pathlib.Path("/proc/<pid>/cmdline").read_bytes()
# raises and the exception handler sets the identity marker to "?" —
# the "python" check then evaluates False and the script falls through
# to the patch branch. That is intentional for the production code path
# (Linux machine only) and documented here for any future Darwin reuse;
# tests/test_force_finalize_sh.py::test_refuses_when_pid_alive gates on
# sys.platform == "linux" for the same reason.
#
# Usage (sourced by the leerie launcher's finalize path):
#
#   source scripts/remote/force-finalize.sh
#   force_finalize_remote "$FLY_APP" "$LEERIE_MACHINE_ID"
#
# Environment consumed:
#   FLY_APP             — Fly.io app name (e.g. "leerie")
#   LEERIE_MACHINE_ID   — ID of the Fly Machine to SSH into
#   FORCE_STOP          — when "1", SIGTERM the orchestrator instead of
#                          refusing on alive.  The process is killed, NOT
#                          the machine.  Falls through to patch after death.
#
# Exit semantics:
#   0  — patch succeeded (or run was already finalized; idempotent)
#        Sentinel: OK:<run_id> or STOPPED:<run_id>:<pid>
#   1  — refused (orchestrator alive, pid file missing, ambiguous run dirs,
#        SSH failure, JSON parse error on the remote side)
#        Sentinel: REFUSE-*, STOP-FAILED:*, ERROR:*
#
# After this returns 0, the caller (`leerie finalize`) falls through to
# the normal fetch_branch path.

set -eu -o pipefail

# Source _log.sh so remote_log is available even when sourced standalone.
# shellcheck disable=SC1091
. "${LEERIE_REPO:-$(cd -- "$(dirname "$(dirname "$(dirname "${BASH_SOURCE[0]}")")")" && pwd -P)}/scripts/remote/_log.sh"

force_finalize_remote() {
  local app="${1:-${FLY_APP:-}}"
  local machine="${2:-${LEERIE_MACHINE_ID:-}}"

  if [ -z "$machine" ]; then
    remote_log "force-finalize: LEERIE_MACHINE_ID not set"
    return 1
  fi

  # The discovery + pid-check + patch payload, run on the machine as a
  # single python3 invocation via `bash -lc`.  Single python so all
  # decisions happen atomically; no risk of a half-patched run.json.
  #
  # The payload prints one of several sentinel lines to stdout that the
  # host-side caller parses to drive logging:
  #   OK:<final_run_id>               — patched (or already finalized); fall through
  #   STOPPED:<run_id>:<pid>          — FORCE_STOP killed the orchestrator, then patched
  #   STOP-FAILED:<run_id>:<pid>      — FORCE_STOP could not kill the orchestrator
  #   REFUSE-ALIVE-SCAN:<pid>:<comm>  — /proc scan found a live orchestrator
  #                                     matching this run-id (authoritative
  #                                     liveness signal; the pid file may
  #                                     point at a stillborn flock-loser
  #                                     and is not trusted alone). See DESIGN
  #                                     §6 *Single owner per run dir* —
  #                                     stale-pid contagion.
  #   REFUSE-ALIVE:<pid>:<comm>       — pid file's pid is alive + looks like
  #                                     python (defensive, post-scan check
  #                                     for pid-reuse audit clarity)
  #   REFUSE-NOPID:<run_id>           — pid file missing
  #   REFUSE-MULTI:<count>            — more than one run dir
  #   REFUSE-NONE                     — no run dir
  #   ERROR:<message>                 — anything else
  local payload
  payload=$(cat <<'PYEOF'
import json
import os
import pathlib
import signal
import sys
import time

force_stop = os.environ.get("FORCE_STOP") == "1"
stopped_pid = None

runs_dir = pathlib.Path("/work/.leerie/runs")
if not runs_dir.is_dir():
    print("ERROR:no /work/.leerie/runs on machine")
    sys.exit(1)

# Discover the single run dir.
candidates = [
    p for p in runs_dir.iterdir()
    if p.is_dir()
]
if len(candidates) == 0:
    print("REFUSE-NONE")
    sys.exit(0)
if len(candidates) > 1:
    print(f"REFUSE-MULTI:{len(candidates)}")
    sys.exit(0)
run_dir = candidates[0]
run_id = run_dir.name

run_json_path = run_dir / "run.json"
if not run_json_path.is_file():
    print(f"ERROR:run.json missing under {run_dir}")
    sys.exit(1)

try:
    data = json.loads(run_json_path.read_text())
except Exception as exc:  # noqa: BLE001
    print(f"ERROR:run.json parse failed: {exc}")
    sys.exit(1)

# Idempotent — if finished_at is already set, no patch needed.
if data.get("finished_at"):
    print(f"OK:{run_id}")
    sys.exit(0)

# /proc cross-check (authoritative liveness signal). orchestrator.pid
# is written by the launcher between Popen and the child's
# State.__init__ flock acquisition; if a concurrent resume's child
# loses the race, its dead pid can land in the file while the real
# orchestrator (the flock winner) keeps running. The pid-file path
# below is retained for pid-reuse audit clarity, but the /proc scan
# is what protects against that contagion. See DESIGN §6 *Single
# owner per run dir*.
#
# Two anchors must both match: (1) the literal substring
# "orchestrator/leerie.py" (the path the launcher hard-codes when it
# Popen's the orchestrator), AND (2) the run-id (passed as a discrete
# NUL-delimited token via --run-id <id>, so it always appears between
# NUL bytes — no false-positive on substring collisions).
run_id_token = run_id.encode()
orch_path_token = b"orchestrator/leerie.py"
scan_hit_pid = None
scan_hit_ident = ""
try:
    proc_root = pathlib.Path("/proc")
    if proc_root.is_dir():
        for entry in proc_root.iterdir():
            if not entry.name.isdigit():
                continue
            try:
                cmdline_raw = (entry / "cmdline").read_bytes()
            except (FileNotFoundError, ProcessLookupError, PermissionError):
                continue
            except Exception:
                continue
            if not cmdline_raw:
                continue
            if orch_path_token not in cmdline_raw:
                continue
            # Run-id must appear as a discrete argv token, bounded by
            # NUL on both sides (or at the start/end of cmdline). Avoids
            # false matches on basename-substring collisions.
            args = cmdline_raw.split(b"\x00")
            if run_id_token not in args:
                continue
            scan_hit_pid = int(entry.name)
            # Identify the binary for audit clarity (first argv element,
            # basename only).
            first = args[0].decode(errors="replace") if args else "?"
            scan_hit_ident = first.rsplit("/", 1)[-1] if first else "?"
            break
except Exception:
    # /proc not mounted or inaccessible: fall through to the pid-file
    # path. On the Fly Linux image /proc is always present; this guard
    # is defensive for non-Linux test environments.
    pass

def _is_zombie(pid: int) -> bool:
    """Check if pid is a zombie (state Z) via /proc."""
    try:
        stat = pathlib.Path(f"/proc/{pid}/stat").read_text()
        return ") Z " in stat
    except Exception:
        return False

def _pid_is_dead(target_pid: int) -> bool:
    try:
        os.kill(target_pid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    # Zombie: killed but not yet reaped by parent — effectively dead.
    return _is_zombie(target_pid)

def _stop_pid(target_pid: int, label: str) -> bool:
    """SIGTERM target_pid, wait up to 30 s, escalate to SIGKILL.
    Returns True when the process is confirmed dead."""
    try:
        os.kill(target_pid, signal.SIGTERM)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    for _ in range(30):
        time.sleep(1)
        if _pid_is_dead(target_pid):
            return True
    # Escalate to SIGKILL.
    try:
        os.kill(target_pid, signal.SIGKILL)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    for _ in range(5):
        time.sleep(1)
        if _pid_is_dead(target_pid):
            return True
    return False

if scan_hit_pid is not None:
    if not force_stop:
        print(f"REFUSE-ALIVE-SCAN:{scan_hit_pid}:{scan_hit_ident}")
        sys.exit(0)
    # FORCE_STOP: kill the orchestrator process, then fall through to patch.
    if _stop_pid(scan_hit_pid, scan_hit_ident):
        stopped_pid = scan_hit_pid
    else:
        print(f"STOP-FAILED:{run_id}:{scan_hit_pid}")
        sys.exit(0)

# Verify orchestrator process is dead.
pid_path = run_dir / "orchestrator.pid"
if not pid_path.is_file():
    print(f"REFUSE-NOPID:{run_id}")
    sys.exit(0)
try:
    pid = int(pid_path.read_text().strip())
except Exception:
    print(f"ERROR:orchestrator.pid not an integer in {run_dir}")
    sys.exit(1)

# kill -0: does the process exist?
alive = False
try:
    os.kill(pid, 0)
    alive = True
except ProcessLookupError:
    alive = False
except PermissionError:
    # EPERM means it exists but is not signalable; treat as alive.
    alive = True

ident = ""
if alive:
    # Read /proc/<pid>/cmdline (NUL-separated execve argv) rather than
    # /proc/<pid>/comm. See the header comment "Why cmdline and not
    # comm" — comm is the basename of the invoked binary (e.g.
    # "pytest"), but cmdline always names the interpreter explicitly.
    try:
        cmdline_raw = pathlib.Path(f"/proc/{pid}/cmdline").read_bytes()
        is_python = b"python" in cmdline_raw
        first = cmdline_raw.split(b"\x00", 1)[0].decode(errors="replace")
        ident = first.rsplit("/", 1)[-1] if first else "?"
    except Exception:
        is_python = False
        ident = "?"
    # Guard against pid reuse: only refuse if the live process looks like
    # the orchestrator (a python process).  On short-lived Fly machines
    # collision is unlikely but the check is cheap.
    if is_python:
        if not force_stop:
            print(f"REFUSE-ALIVE:{pid}:{ident}")
            sys.exit(0)
        # FORCE_STOP: kill the orchestrator process.
        if _stop_pid(pid, ident):
            stopped_pid = pid
        else:
            print(f"STOP-FAILED:{run_id}:{pid}")
            sys.exit(0)
    # If it's NOT python, treat the pid file as a stale collision and
    # proceed.  Log this in audit fields below.

# Safe to patch.
now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
data["finished_at"] = now
data["recovered_at"] = now
data["recovered_via"] = "force-finalize"
# Mechanism flag: the orchestrator died before it could set its own
# no_push intent; treat as user-intent-push (the host-side finalize gate
# remains the source of truth for actual push decisions).
if data.get("no_push") is True:
    data["no_push"] = False

tmp_path = run_json_path.with_suffix(".json.tmp")
tmp_path.write_text(json.dumps(data, indent=2) + "\n")
os.replace(tmp_path, run_json_path)
# Emit STOPPED sentinel when the process was killed via FORCE_STOP,
# otherwise the normal OK sentinel.
if stopped_pid is not None:
    print(f"STOPPED:{run_id}:{stopped_pid}")
else:
    print(f"OK:{run_id}")
sys.exit(0)
PYEOF
)

  # Pipe the Python source via stdin to `python3 -` on the machine. This
  # sidesteps the `flyctl ssh console --command` argv-not-shell semantics
  # — `--command` execs the string as argv (not via a shell), so any
  # shell builtin or operator needs `bash -lc '...'` wrapping with
  # nested single-quote escaping — and Python's own quoting (single vs
  # triple, escapes) entirely. The script body never has to round-trip
  # through a shell quoter.
  # When the caller exports FORCE_STOP=1, propagate it into the remote
  # env so the Python payload SIGTERMs the orchestrator instead of refusing.
  local remote_cmd="python3 -"
  if [ "${FORCE_STOP:-}" = "1" ]; then
    remote_cmd="bash -lc 'FORCE_STOP=1 exec python3 -'"
  fi
  local result
  if ! result="$(printf '%s' "$payload" \
        | flyctl ssh console --app "$app" --machine "$machine" \
            --pty=false -C "$remote_cmd" 2>&1)"; then
    remote_log "force-finalize: SSH to machine $machine failed"
    remote_log "  output: $result"
    return 1
  fi

  # Result lines: the python prints exactly one sentinel; flyctl's own
  # "Connecting to …" stderr lands on stderr (which we merged with 2>&1)
  # so filter for the sentinel.  Strip CR (flyctl ssh console can produce
  # CRLF line endings when fronted by a TTY-emulating layer).
  local sentinel
  sentinel="$(printf '%s\n' "$result" | tr -d '\r' \
              | grep -E '^(OK|STOPPED|STOP-FAILED|REFUSE-ALIVE-SCAN|REFUSE-ALIVE|REFUSE-NOPID|REFUSE-MULTI|REFUSE-NONE|ERROR):?' \
              | tail -1 || true)"
  if [ -z "$sentinel" ]; then
    remote_log "force-finalize: no sentinel in remote output:"
    remote_log "  $result"
    return 1
  fi

  case "$sentinel" in
    OK:*)
      local rid="${sentinel#OK:}"
      remote_log "force-finalize: machine=$machine run=$rid patched (finished_at + recovered_at + recovered_via=force-finalize)"
      return 0
      ;;
    STOPPED:*)
      local rest="${sentinel#STOPPED:}"
      local rid="${rest%%:*}"
      local spid="${rest#*:}"
      remote_log "force-finalize: machine=$machine run=$rid — stopped orchestrator pid $spid, then patched (finished_at + recovered_at + recovered_via=force-finalize)"
      return 0
      ;;
    STOP-FAILED:*)
      local rest="${sentinel#STOP-FAILED:}"
      local rid="${rest%%:*}"
      local spid="${rest#*:}"
      remote_log "force-finalize: STOP-FAILED — could not kill orchestrator pid $spid for run $rid on machine $machine."
      remote_log "  The process did not die after SIGTERM + SIGKILL. Inspect manually with"
      remote_log "  \`leerie resume <run-id> --shell --runtime fly\`, then \`leerie kill <run-id> --runtime fly\` when done."
      return 1
      ;;
    REFUSE-ALIVE-SCAN:*)
      local rest="${sentinel#REFUSE-ALIVE-SCAN:}"
      local pid="${rest%%:*}"
      local comm="${rest#*:}"
      remote_log "force-finalize: REFUSED — /proc scan found live orchestrator pid $pid ($comm) for this run on machine $machine."
      remote_log "  The pid file may point at a stillborn process (see DESIGN §6 *Single owner per run dir*); the scan is authoritative."
      remote_log "  Use \`leerie kill <run-id> --runtime fly\` if you really want to abandon the run, or"
      remote_log "  \`leerie resume <run-id> --runtime fly\` to tail/inspect what it's doing first."
      return 1
      ;;
    REFUSE-ALIVE:*)
      local rest="${sentinel#REFUSE-ALIVE:}"
      local pid="${rest%%:*}"
      local comm="${rest#*:}"
      remote_log "force-finalize: REFUSED — orchestrator pid $pid ($comm) is still alive on machine $machine."
      remote_log "  Use \`leerie kill <run-id> --runtime fly\` if you really want to abandon the run, or"
      remote_log "  \`leerie resume <run-id> --runtime fly\` to tail/inspect what it's doing first."
      return 1
      ;;
    REFUSE-NOPID:*)
      local rid="${sentinel#REFUSE-NOPID:}"
      remote_log "force-finalize: REFUSED — run $rid has no orchestrator.pid on machine $machine."
      remote_log "  This usually means the orchestrator failed very early (before phase_classify)."
      remote_log "  Attach manually with \`leerie resume <run-id> --shell --runtime fly\` to inspect, then \`leerie kill <run-id> --runtime fly\` when done."
      return 1
      ;;
    REFUSE-MULTI:*)
      local count="${sentinel#REFUSE-MULTI:}"
      remote_log "force-finalize: REFUSED — $count run dirs on machine $machine; can't pick one."
      remote_log "  Attach manually with \`leerie resume <run-id> --shell --runtime fly\` to disambiguate."
      return 1
      ;;
    REFUSE-NONE)
      remote_log "force-finalize: REFUSED — no run dir on machine $machine."
      remote_log "  The orchestrator likely never reached phase_classify."
      return 1
      ;;
    ERROR:*)
      remote_log "force-finalize: ${sentinel}"
      return 1
      ;;
    *)
      remote_log "force-finalize: unexpected sentinel: $sentinel"
      return 1
      ;;
  esac
}
