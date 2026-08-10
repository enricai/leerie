#!/usr/bin/env bash
# scripts/remote/lib.sh — shared helpers for the remote (Fly.io) lifecycle.
#
# Sourced by provision.sh, resume-machine.sh, and (Phase 4) re-seed.sh.
# Pure functions — no global state, no traps. Callers own their own
# lifecycle decisions; this file only provides reusable building blocks.

# --- _seed_timeout_prefix ------------------------------------------------
# Emit the `timeout --kill-after=5 ${LEERIE_SEED_TIMEOUT_S:-600}` prefix
# used to bound the `flyctl ssh console` side of bulk transfers. On hosts
# without GNU `timeout` (BSD macOS w/o coreutils) the function echoes
# nothing — caller falls back to an unbounded pipe, matching pre-fix
# behavior. The fix converts a silent multi-hour hang into a clean
# non-zero rc (124 on TERM, 137 on KILL) that seed_auth's existing retry
# / failure path already knows how to handle. Background:
# `flyctl ssh console -C` is known to hang without exiting when the
# WireGuard tunnel stalls mid-transfer (observed 2026-06-04 across four
# parallel runs). See plan file for full evidence.
#
# Usage:
#   $(_seed_timeout_prefix) flyctl ssh console ... -C ...
_seed_timeout_prefix() {
  if ! command -v timeout >/dev/null 2>&1; then
    return 0
  fi
  printf 'timeout --kill-after=5 %s' "${LEERIE_SEED_TIMEOUT_S:-600}"
}

# --- _extract_flyctl_remote_rc -------------------------------------------
# flyctl ssh console does not forward the remote process's exit code — it
# returns 1 for any non-zero remote exit. The actual remote code appears
# only in stderr:  Error: ssh shell: Process exited with status <N>
# This helper extracts <N> from a captured stderr file.
#
# Usage:
#   _extract_flyctl_remote_rc "$stderr_file" "$flyctl_rc"
#
# Prints the remote exit code if parseable, otherwise the original flyctl
# rc unchanged. Returns 0 always (callers branch on the printed value).
_extract_flyctl_remote_rc() {
  local stderr_file="$1"
  local flyctl_rc="$2"
  if [ "$flyctl_rc" = "0" ]; then
    printf '%s' "0"
    return 0
  fi
  local remote_rc=""
  remote_rc="$(sed -n 's/.*Process exited with status \([0-9][0-9]*\).*/\1/p' \
                "$stderr_file" 2>/dev/null | tail -1)"
  if [ -n "$remote_rc" ]; then
    printf '%s' "$remote_rc"
  else
    printf '%s' "$flyctl_rc"
  fi
}

# --- update_run_json -----------------------------------------------------
# Atomically merge key/value pairs into a run.json sidecar on the host.
#
# Usage:
#   update_run_json "$LEERIE_STATE_HOST_DIR/runs/<run-id>/run.json" \
#                   key1 value1 [key2 value2 ...]
#
# Values are treated as strings and JSON-encoded. The merge is read →
# patch → temp-file write → rename, mirroring the orchestrator's
# State.save() + _write_run_json() atomicity contract (DESIGN §6).
#
# Returns 0 on success. Returns 1 (and writes to stderr) if the sidecar
# directory does not exist or the rewrite fails.
update_run_json() {
  local sidecar="$1"
  shift
  local dir
  dir="$(dirname "$sidecar")"
  if [ ! -d "$dir" ]; then
    remote_log "update_run_json: $dir does not exist"
    return 1
  fi
  local tmp
  tmp="$(mktemp "$sidecar.XXXXXX")"
  # Python handles the read+merge+write so we don't reimplement JSON
  # escaping in bash. The trailing args are key/value pairs; odd-count
  # is a programming error.
  if ! python3 - "$sidecar" "$tmp" "$@" <<'PY'
import json, os, sys
sidecar, tmp, *rest = sys.argv[1:]
if len(rest) % 2 != 0:
    print(f"update_run_json: expected even number of key/value args, got {len(rest)}", file=sys.stderr)
    sys.exit(1)
data = {}
if os.path.exists(sidecar):
    try:
        with open(sidecar) as f:
            data = json.load(f)
        if not isinstance(data, dict):
            data = {}
    except (OSError, ValueError):
        data = {}
for i in range(0, len(rest), 2):
    k, v = rest[i], rest[i+1]
    # Empty string clears the key (sets to null).
    data[k] = None if v == "" else v
with open(tmp, "w") as f:
    json.dump(data, f, indent=2)
    f.write("\n")
PY
  then
    rm -f "$tmp"
    remote_log "update_run_json: python merge failed for $sidecar"
    return 1
  fi
  mv "$tmp" "$sidecar"
}

# --- iso_now -------------------------------------------------------------
# Emit an ISO-8601 UTC timestamp (sub-second precision). Used as
# paused_at / similar event markers.
iso_now() {
  python3 -c 'import datetime; print(datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"))'
}

# --- remote_log ----------------------------------------------------------
# Defined in _log.sh so seed-auth.sh and fetch-branch.sh can pull in just
# the logger (without the rest of lib.sh) for their standalone-test path.
# shellcheck disable=SC1091
. "$(dirname "${BASH_SOURCE[0]}")/_log.sh"

# --- fly_rsync_wrapper ---------------------------------------------------
# Emit (to stdout) the absolute path of a single-use shell script that
# rsync's `-e` flag accepts, so rsync transports its protocol over
# `flyctl ssh console -C "<remote-cmd>"` instead of plain ssh.
#
# Why this exists:
#   Host-side BSD `tar -c` normalizes filenames NFC → NFD when archiving
#   on macOS (libarchive behavior; documented). On the Linux receiver,
#   the NFD bytes don't match git's NFC index entries, so the working
#   tree on the machine looks dirty after a clean seed of a repo whose
#   filenames contain non-ASCII characters (the live case that exposed
#   this: a PDF with `ó` and 📄 inside a submodule). `rsync` preserves
#   filename bytes verbatim — verified empirically.
#
#   But `rsync` needs an SSH transport, and `flyctl ssh console` is not
#   a drop-in SSH client (no agent forwarding, no port forwarding, no
#   plain stdin command — only `--command "<string>"`). This wrapper
#   bridges the gap: rsync invokes us as
#     <wrapper> <host> rsync --server -se ...
#   we strip the host, quote the rest, and pass it to ssh console as a
#   single `-C` arg. The "host" rsync passes is whatever appears before
#   the `:` in the rsync destination path; we use the Fly machine ID.
#
# Usage:
#   local wrapper
#   wrapper="$(fly_rsync_wrapper "$FLY_APP")"
#   trap 'rm -f "$wrapper"' EXIT
#   LEERIE_FLY_APP="$FLY_APP" rsync -a --from0 --files-from=/tmp/list \
#     -e "$wrapper" "$USER_REPO/" "$LEERIE_MACHINE_ID:/work/"
#
# The wrapper exports `LEERIE_FLY_APP` (from the caller's env) and uses it
# to address the right app; the machine ID arrives via rsync's host arg.
#
# Returns: prints the wrapper's path to stdout. Caller is responsible
# for `rm -f` on the path when done.
fly_rsync_wrapper() {
  local fly_app="${1:-${LEERIE_FLY_APP:-}}"
  local wrapper
  wrapper="$(mktemp -t leerie-fly-rsync.XXXXXX)"
  # The wrapper itself is portable POSIX sh. The heredoc is UNquoted so
  # we can bake the resolved $fly_app value into the wrapper at emit
  # time (default for $LEERIE_FLY_APP). Every OTHER variable inside is
  # escaped with `\$` so it's evaluated when rsync invokes the wrapper
  # later, not when the heredoc is expanded host-side.
  cat > "$wrapper" <<WRAPPER
#!/bin/sh
# rsync -e wrapper for flyctl ssh console.
# Invoked by rsync as: <this-script> <host> <remote-cmd> [args...]
# We pass <remote-cmd> [args...] as a single -C string to flyctl.
set -e
FLY_APP="\${LEERIE_FLY_APP:-$fly_app}"
MACHINE="\$1"
shift
# Build the remote command string. printf %q is bash-only; use a
# portable sh approach: join with spaces, no quoting. rsync's remote
# args (rsync --server --sender -se.iLsfxC . /path) don't contain
# shell metachars in practice and don't need re-quoting.
CMD="\$*"
exec flyctl ssh console --quiet --app "\$FLY_APP" --machine "\$MACHINE" --pty=false -C "\$CMD"
WRAPPER
  chmod 755 "$wrapper"
  echo "$wrapper"
}

# --- render_tail_wrapper -------------------------------------------------
# Emit a POSIX-sh script (to stdout) that:
#   1. Tails the orchestrator log for the given run id.
#   2. Watches the orchestrator pid (from orchestrator.pid). When the pid
#      disappears the orchestrator has exited cleanly. The wrapper kills
#      the tail and prints the finalize banner.
#   3. If AUTO_FINALIZE_TOKEN is set in the wrapper's environment, prints
#      that token on the *last* line of stderr instead of (after) the
#      banner; callers can grep for the token to trigger
#      `leerie finalize` automatically. Decoupled from the wrapper itself
#      because exec'ing `leerie` back inside the Fly machine is wrong; the
#      auto-finalize step has to run on the host.
#
# Usage (caller):
#   _wrapper="$(render_tail_wrapper)"
#   flyctl machine exec "$MID" --app "$APP" -- \
#     sh -c "$_wrapper" -- "$RUN_ID"
#
# The wrapper is purely shell (POSIX sh, runs in the Fly image's /bin/sh)
# so it stays portable across remote sh implementations (busybox / dash /
# bash). It uses `tail -F` which all of those support.
render_tail_wrapper() {
  cat <<'TAIL_SH'
# NOTE: lines below run INSIDE the Fly Machine under /bin/sh
# (busybox/dash, not bash). The host-side `remote_log` helper from
# lib.sh is NOT in scope here, so the timestamp prefix is inlined.
# Repo name is omitted (USER_REPO is a host-side path, not meaningful
# in-machine; the parallel-runs disambiguation problem this tag solves
# is a host-shell problem).
# Run-id input: prefer the LEERIE_TAIL_RUN_ID env var (works under
# `flyctl ssh console --command` which discards positional args), fall
# back to $1 (works under `flyctl machine exec ... -- sh -c "..." -- id`).
ID="${LEERIE_TAIL_RUN_ID:-$1}"
if [ -z "$ID" ]; then
  echo "$(date +%FT%T%z | sed 's/\(..\)$/:\1/') [leerie] remote: render_tail_wrapper got empty run-id (LEERIE_TAIL_RUN_ID unset and \$1 empty)" >&2
  exit 2
fi
# Wait briefly for the orchestrator to write its log file. Without this,
# `tail -F` against a not-yet-existent file just spins.
LOG="/work/.leerie/runs/${ID}/orchestrator.log"
for _ in 1 2 3 4 5 6 7 8 9 10; do
  [ -f "$LOG" ] && break
  sleep 1
done

PID_FILE="/work/.leerie/runs/${ID}/orchestrator.pid"
ORCH_PID="$(head -1 "$PID_FILE" 2>/dev/null)"

tail -F "$LOG" 2>/dev/null &
TAIL_PID=$!

# Cross-check the orchestrator's liveness with two ORed signals:
#   1. orchestrator.pid + kill -0  (cheap, may go stale)
#   2. /proc/[0-9]*/cmdline scan for "orchestrator/leerie.py" AND the
#      run-id as a discrete argv token (authoritative).
# Either signal saying "alive" keeps the watcher tailing. Both
# signals must say "dead" before we print the finalize banner. See
# DESIGN §6 *Single owner per run dir* — stale-pid contagion — for
# why the pid file is not trusted alone.
#
# POSIX-sh compatible: the wrapper runs under /bin/sh in the Fly
# image (busybox/dash). `tr '\0' ' '` converts cmdline's NUL-separated
# argv to space-separated text so the case pattern can match. The
# run-id check uses bounding spaces so a basename-substring collision
# in another process's cmdline doesn't false-positive — _argv is
# wrapped with leading AND trailing spaces explicitly, defending
# against the rare case where /proc/<pid>/cmdline lacks the final NUL
# (Linux's man-page contract says it's terminated, but parallel the
# bullet-proof split-on-NUL approach the Python side uses in
# force-finalize.sh).
_orch_is_alive() {
  # Layer 1: pid file
  if [ -n "$ORCH_PID" ] && kill -0 "$ORCH_PID" 2>/dev/null; then
    return 0
  fi
  # Layer 2: /proc scan. Skip cleanly if /proc isn't mounted.
  [ -d /proc ] || return 1
  # Prefilter with ONE grep instead of forking `tr` once per process.
  #
  # The per-process form cost 11.8s at 789 processes on a real host (measured:
  # user 3.4s, sys 9.0s — almost entirely fork overhead), and this function
  # re-runs every 2 seconds, so the watcher spent essentially all its time
  # forking. Two consequences: the "orchestrator exited" banner the host greps
  # for (and auto-finalize depends on) was delayed by a full scan, and
  # `tests/test_render_tail_wrapper.py` failed on any host with a few hundred
  # processes because one scan exceeded its 10s timeout. Measured after:
  # 0.146s, ~80x faster.
  #
  # The glob is the same one the old loop expanded, so this adds no new
  # ARG_MAX exposure. grep's -l/-a are both in busybox, which is what the Fly
  # image's /bin/sh provides. NUL-vs-space does not matter for this pattern:
  # it contains no spaces, so it matches the raw cmdline bytes either way —
  # the run-id check below still converts NULs so its bounding spaces work.
  for _cmd in $(grep -la "orchestrator/leerie.py" /proc/[0-9]*/cmdline 2>/dev/null); do
    # Skip our own process — the wrapper script text (passed via
    # bash -c) appears in /proc/self/cmdline and would self-match.
    _cpid="${_cmd#/proc/}"
    _cpid="${_cpid%%/*}"
    [ "$_cpid" = "$$" ] && continue
    # The pid may exit between grep and here; `tr` then yields nothing and the
    # bounding-space check below simply does not match.
    _argv=" $(tr '\0' ' ' < "$_cmd" 2>/dev/null) " || continue
    case "$_argv" in
      *" ${ID} "*) return 0 ;;
    esac
  done
  return 1
}
if [ -n "$ORCH_PID" ] || [ -d /proc ]; then
  while _orch_is_alive; do
    sleep 2
  done
else
  # Neither signal source available (no pid file AND no /proc — the
  # degenerate case for very-early reattach on a non-Linux fixture).
  # Block on the tail until it dies.
  wait "$TAIL_PID" 2>/dev/null || true
fi

kill "$TAIL_PID" 2>/dev/null || true
wait "$TAIL_PID" 2>/dev/null || true

# Read the orchestrator's exit code from the sidecar file so
# decide_teardown can route failed runs to the pause branch
# (DESIGN §6 teardown disposition table). Absent file (OOM,
# SIGKILL, crash before the handler) → default 0 (backward compat).
ORCH_EXIT=0
EXIT_FILE="/work/.leerie/runs/${ID}/orchestrator.exit_code"
if [ -f "$EXIT_FILE" ]; then
  _read_rc="$(head -1 "$EXIT_FILE" 2>/dev/null)" || true
  case "$_read_rc" in
    [0-9]|[0-9][0-9]|[0-9][0-9][0-9]) ORCH_EXIT="$_read_rc" ;;
  esac
fi

echo "" >&2
echo "$(date +%FT%T%z | sed 's/\(..\)$/:\1/') [leerie] remote: orchestrator exited — syncing run branch + state to host..." >&2

# Auto-finalize hook: when the calling host sets AUTO_FINALIZE_TOKEN,
# print it as the wrapper's last stderr line. The host-side caller
# greps for the token, captures the final run-id, and exec's
# `leerie finalize <id>` on the host (the machine cannot do it; auth
# lives on the host).
if [ -n "$AUTO_FINALIZE_TOKEN" ]; then
  echo "${AUTO_FINALIZE_TOKEN}${ID}" >&2
fi
exit "$ORCH_EXIT"
TAIL_SH
}

# --- tail_with_optional_autofinalize ------------------------------------
# Run a render_tail_wrapper payload against a Fly Machine via
# `flyctl ssh console`, optionally with AUTO_FINALIZE token plumbing.
#
# Call sites today:
#   - The launcher's fresh-launch tail (after starting the orchestrator).
#   - `_attach_to_live_orchestrator` (this file) — invoked by both the
#     early flock probe and the launch-wrapper rc=75 pivot when the
#     orchestrator is already alive.
#
# Both code paths use this helper to avoid two divergent copies of the
# token-grep-and-exec dance; the helper is the load-bearing surface
# tested by tests/test_resume_attach.py.
#
# Args:
#   $1 _tail_script   POSIX-sh payload from render_tail_wrapper (stdout).
#   $2 _run_id        Run-id to tail (becomes LEERIE_TAIL_RUN_ID inside).
#   $3 _machine_id    Fly Machine id.
#   $4 _app           Fly app name.
#   $5 _do_auto       "true" to enable AUTO_FINALIZE token plumbing.
#
# Behavior when $5="true": captures stderr through a tee + tempfile,
# greps for `<<LEERIE_AUTOFIN_$$>>${RUN_ID}` (emitted by
# render_tail_wrapper's last-line stderr hook), and on clean rc=0
# execs `${LEERIE_REPO}/leerie finalize <final_id>` on the host so
# the host's gh/git auth performs the push. The function then exits
# (never returns) — either via the exec, or via `exit $rc` on
# non-zero rc / missing token. The auto-finalize exec replaces this
# script entirely; the launcher's `decide_teardown` trap coexists
# because it's idempotent (LEERIE_TEARDOWN_DONE guards re-entry).
#
# Behavior when $5="false": runs the tail stream normally and returns
# the flyctl exit code. Caller handles the rc.
tail_with_optional_autofinalize() {
  local _tail_script="$1" _run_id="$2" _machine_id="$3" _app="$4" _do_auto="$5"
  local _tail_invocation _token _stderr_capture _rc final_id

  _tail_invocation="LEERIE_TAIL_RUN_ID='$_run_id'; export LEERIE_TAIL_RUN_ID"

  if [ "$_do_auto" = "true" ]; then
    _token="<<LEERIE_AUTOFIN_$$>>"
    _stderr_capture="$(mktemp -t leerie-tail.XXXXXX)"
    # NOTE: no `trap '... EXIT'` here. This function is *sourced* into
    # the launcher's shell (the launcher sources lib.sh, then calls this
    # via the rc=75 pivot), and the launcher already has
    # `trap 'decide_teardown' EXIT INT TERM` registered by provision.sh.
    # An EXIT trap here would clobber decide_teardown and the Fly
    # machine would never receive its host-side teardown disposition.
    # Instead, clean up the tempfile inline before each terminal path.
    _tail_invocation="${_tail_invocation}
AUTO_FINALIZE_TOKEN='$_token'; export AUTO_FINALIZE_TOKEN
$_tail_script"
    # Capture the pipeline rc without toggling `set -e`. Toggling here
    # would silently re-enable -e on shells that had it off (like the
    # test harness), causing this function's `return $_rc` to terminate
    # the *caller*. Use `|| _rc=$?` instead.
    _rc=0
    printf '%s' "$_tail_invocation" \
      | flyctl ssh console --app "$_app" --machine "$_machine_id" --pty=false -C "sh -s" \
        2> >(tee "$_stderr_capture" >&2) \
      || _rc=$?
    if [ "$_rc" -eq 0 ]; then
      final_id="$(grep -oE "${_token}[^ ]+" "$_stderr_capture" 2>/dev/null \
                  | tail -1 | sed "s|^${_token}||")"
      if [ -n "$final_id" ]; then
        remote_log "auto-finalize: running 'leerie finalize $final_id'"
        rm -f "$_stderr_capture"
        exec "${LEERIE_REPO}/leerie" finalize "$final_id"
      fi
    fi
    # `return`, not `exit`: this is a sourced function. `exit` here
    # would terminate the launcher shell, bypassing the rc=75 pivot's
    # `container_rc=130` assignment and the decide_teardown detach
    # banner. The caller decides what to do with the rc.
    rm -f "$_stderr_capture"
    return "$_rc"
  else
    _tail_invocation="${_tail_invocation}
$_tail_script"
    printf '%s' "$_tail_invocation" \
      | flyctl ssh console --app "$_app" --machine "$_machine_id" --pty=false -C "sh -s"
  fi
}

# --- _attach_to_live_orchestrator -----------------------------------------
# Attach to an already-running orchestrator — either open a bash shell
# at /work (RESUME_SHELL=true) or tail the live log stream via
# render_tail_wrapper + tail_with_optional_autofinalize.
#
# Call sites:
#   - The early flock probe (resume path, before seed_auth).
#   - The launch-wrapper rc=75 pivot (after seed_auth, belt-and-suspenders).
#
# Reads from caller's scope:
#   RESUME_SHELL, LEERIE_RUN_ID, LEERIE_MACHINE_ID, FLY_APP,
#   RESUME_AUTO_FINALIZE
#
# Side-effect: sets container_rc=130 in the caller's scope so
# decide_teardown leaves the machine alone (DESIGN §6 *Smart resume
# in remote mode*).
_attach_to_live_orchestrator() {
  if [ "$RESUME_SHELL" = "true" ]; then
    remote_log "resume: orchestrator already running for $LEERIE_RUN_ID; opening shell at /work"
    local _shell_payload="cd /work && PS1='leerie@$LEERIE_RUN_ID:\\w\\$ ' exec bash --noprofile --norc -i"
    local _shell_payload_q
    _shell_payload_q="$(printf %s "$_shell_payload" | sed "s/'/'\\\\''/g")"
    flyctl ssh console \
      --app "$FLY_APP" \
      --machine "$LEERIE_MACHINE_ID" \
      --command "bash -lc '$_shell_payload_q'" || true
  else
    remote_log "resume: orchestrator already running for $LEERIE_RUN_ID; attaching to live log stream (Ctrl-C to detach — orchestrator keeps running)"
    local _tail_script
    _tail_script="$(render_tail_wrapper)"
    tail_with_optional_autofinalize \
      "$_tail_script" \
      "$LEERIE_RUN_ID" \
      "$LEERIE_MACHINE_ID" \
      "$FLY_APP" \
      "$RESUME_AUTO_FINALIZE" || true
  fi
  container_rc=130
}

# --- require_flyctl ------------------------------------------------------
# Ensure flyctl is on PATH and authenticated, auto-installing if missing.
#
# Behavior:
#   1. command -v flyctl. If found, skip to step 3.
#   2. If --no-runtime-install / LEERIE_NO_RUNTIME_INSTALL=1, print install
#      hint and return 1 (preserves the pre-auto-install contract).
#      Otherwise prompt to install via:
#        - macOS: brew install flyctl
#        - Linux: curl -L https://fly.io/install.sh | sh
#                 (also adds ~/.fly/bin to PATH for this shell)
#      If install fails or user declines, return 1.
#   3. flyctl auth status. If unauthenticated, print "flyctl auth login"
#      instructions and (if stdin is a TTY) prompt to run it now.
#      The prompt opens a browser via `flyctl auth login`; on success,
#      auth check is re-run.
#
# Honors:
#   LEERIE_NO_RUNTIME_INSTALL=1   skip auto-install, fall back to hint+exit
#   LEERIE_NONINTERACTIVE=1        never prompt; install/auth must already be set up
#
# Idempotent: safe to call multiple times. Returns 0 if flyctl is ready.
require_flyctl() {
  if ! command -v flyctl >/dev/null 2>&1; then
    if [ "${LEERIE_NO_RUNTIME_INSTALL:-0}" = "1" ] || [ "${LEERIE_NONINTERACTIVE:-0}" = "1" ]; then
      remote_log "flyctl not found on PATH."
      remote_log "  Install from https://fly.io/docs/flyctl/install/"
      remote_log "  or: brew install flyctl (macOS)"
      return 1
    fi
    if ! _require_flyctl_install; then
      return 1
    fi
    # After install, re-resolve PATH (installers commonly add to ~/.fly/bin).
    if ! command -v flyctl >/dev/null 2>&1; then
      if [ -x "$HOME/.fly/bin/flyctl" ]; then
        export PATH="$HOME/.fly/bin:$PATH"
      fi
    fi
    if ! command -v flyctl >/dev/null 2>&1; then
      remote_log "flyctl install reported success but binary still not on PATH."
      remote_log "  Check $HOME/.fly/bin or restart your shell."
      return 1
    fi
  fi
  if ! flyctl auth status >/dev/null 2>&1; then
    if [ "${LEERIE_NONINTERACTIVE:-0}" = "1" ]; then
      remote_log "flyctl is not authenticated."
      remote_log "  Run: flyctl auth login"
      return 1
    fi
    if ! _require_flyctl_login; then
      return 1
    fi
  fi
  return 0
}

# --- _leerie_fly_agent_ensure --------------------------------------------
# Spawn (or reuse) a leerie-owned ssh-agent at a predictable socket and
# point SSH_AUTH_SOCK at it. The user's main ssh-agent is never touched.
#
# Why: `flyctl ssh issue --agent` appends a 24h cert to whichever agent
# SSH_AUTH_SOCK points at, and never deletes prior certs. If aimed at the
# user's main agent, repeated leerie runs accumulate dozens of certs,
# which OpenSSH then offers to every ssh destination (including
# github.com). After ~5 failed auth attempts per connection, GitHub
# rate-limits the account. A private agent contains the blast radius.
#
# Lifecycle: persistent across runs so the 24h cert is reused (re-issuing
# every invocation defeats the lifetime and is what caused the original
# accumulation). Never auto-killed. Reboot wipes the socket inode → next
# run lazy-spawns fresh.
_leerie_fly_agent_ensure() {
  local agent_dir sock lockdir i
  agent_dir="${XDG_CACHE_HOME:-$HOME/.cache}/leerie/agent"
  sock="$agent_dir/ssh-agent.sock"
  lockdir="$agent_dir/.spawn.lock"
  install -d -m 700 "$agent_dir"
  # Serialize spawn-or-reuse across parallel leerie invocations using
  # mkdir-as-mutex (portable; macOS has no `flock` binary). Spin up to
  # ~10s waiting for a concurrent spawn to finish; then proceed even if
  # we couldn't acquire (the worst case is two ssh-agent procs racing,
  # one wins the bind and the other exits — benign).
  i=0
  while ! mkdir "$lockdir" 2>/dev/null; do
    i=$((i+1))
    if [ "$i" -ge 50 ]; then
      break
    fi
    sleep 0.2
  done
  # NOTE: We don't use `trap '...' RETURN` for lockdir cleanup because
  # bash RETURN traps persist in the caller's scope (they fire on every
  # subsequent function return up the call chain). Instead, we rmdir
  # explicitly at each return path below.
  if [ -S "$sock" ]; then
    SSH_AUTH_SOCK="$sock" ssh-add -l >/dev/null 2>&1
    # ssh-add -l exit codes: 0 = has keys, 1 = agent reachable but has no
    # keys yet, 2 = cannot connect to the agent at all. Only rc 2 means
    # the socket is stale; rc 1 is a perfectly healthy, just-spawned or
    # freshly-cert-expired agent and must be reused, not respawned —
    # respawning on rc 1 unlinks a live agent's socket out from under it
    # (rm -f below) while the agent process keeps running, orphaning it.
    if [ "$?" -ne 2 ]; then
      export SSH_AUTH_SOCK="$sock"
      rmdir "$lockdir" 2>/dev/null || true
      return 0
    fi
  fi
  rm -f "$sock"
  # -t 24h: default max lifetime for IDENTITIES added to this agent,
  # matching the Fly cert lifetime (`flyctl ssh issue --agent` issues a 24h
  # cert, and this agent is reused across runs to hold it — see the header
  # comment). It costs nothing: the cert already expires at 24h either way.
  #
  # What it does NOT do is bound the agent PROCESS. `man ssh-agent`: "-t
  # life — Set a default value for the maximum lifetime of identities added
  # to the agent." Verified empirically: an agent started with `-t 2` is
  # still alive well past its identity's expiry. So this is not a mitigation
  # for orphaned agents — an orphan still leaks forever, holding only an
  # empty keyring. The rc-1-vs-rc-2 predicate above is the actual leak fix
  # (it stops us orphaning a live agent in the first place); reaping an
  # agent orphaned some other way would need a real reaper, which does not
  # exist yet.
  ssh-agent -a "$sock" -t 24h >/dev/null 2>&1
  export SSH_AUTH_SOCK="$sock"
  rmdir "$lockdir" 2>/dev/null || true
}

# --- require_fly_ssh ------------------------------------------------------
# Ensure the leerie-private ssh-agent has a Fly-issued certificate for
# SSH-based file transfer via `flyctl ssh console -C "..." < tar`. Certs
# expire after 24 hours; if missing/expired the SSH attempts hang or
# error. Leerie uses SSH for seeding because flyctl removed `--stdin`
# from `machine exec` and the alternative argv-based payload transfer
# hits ARG_MAX on macOS for typical Claude config (~640 MB).
#
# The Fly-API probe that earlier versions used was removed: it was
# racy under WireGuard latency and unreliable, causing the function to
# fall through to issuance even when a valid cert already existed. With
# a private agent we trust local state: any ED25519-CERT in the agent
# is leerie's own.
#
# Best-effort: if `flyctl ssh issue` fails (e.g. user is on a restricted
# org), seed-auth will surface the original SSH error.
require_fly_ssh() {
  local fly_org="${LEERIE_FLY_ORG:-personal}"
  _leerie_fly_agent_ensure
  if ssh-add -l 2>/dev/null | grep -qE 'CERT\)'; then
    return 0
  fi
  remote_log "remote: issuing Fly SSH certificate (24h) for org=$fly_org"
  if ! flyctl ssh issue --agent --hours 24 "$fly_org" >/dev/null 2>&1; then
    remote_log "warning: flyctl ssh issue failed; seed-auth may fail"
    return 1
  fi
  return 0
}

# --- wait_for_fly_ssh_ready ----------------------------------------------
# Block until hallpass (Fly's in-machine SSH daemon) is ready to accept
# connections. require_fly_ssh proves the cert is valid; this proves the
# *target machine* will actually answer. A freshly-started machine takes
# 5-30 s for hallpass to come up after `flyctl machine start` reports
# "started", so naively running `ssh console -C "tar -x"` against it
# yields "ssh: handshake failed: EOF".
#
# Strategy: probe with `ssh console --pty=false -C true` against the
# actual machine ID; success exits 0, EOF/connection errors retry.
# Bounded by ~175 s total (12 attempts × 10 s per-probe timeout +
# 11 × 5 s sleep between attempts).
#
# Called exactly once per run, from seed_auth, during cold-start of a
# fresh machine. Post-seed_auth call sites used to re-probe before
# bundle/rsync pipes, but the channel is demonstrably warm by then —
# the re-probe only manufactures false-positive failures and noisy
# logs (the 2026-06-05 investigation). Down to one probe + the
# transports' own LEERIE_SEED_TIMEOUT_S wrappers as the authoritative
# failure detector from there on.
#
# Usage:
#   wait_for_fly_ssh_ready "$FLY_APP" "$machine_id"
wait_for_fly_ssh_ready() {
  local fly_app="$1"
  local machine_id="$2"
  local attempts=0
  local max_attempts=12
  local probe_exit=0
  local last_probe_exit=0
  while [ "$attempts" -lt "$max_attempts" ]; do
    # `</dev/null` is load-bearing: if stdin is a TTY (the normal
    # case when leerie is run interactively), flyctl ssh console hangs
    # after the SSH session is established — empirically observed,
    # 11+ minutes without returning. Detaching stdin lets flyctl
    # complete the -C "true" command and exit cleanly in <5 s.
    # `--kill-after=2` ensures `timeout` escalates SIGTERM to
    # SIGKILL after a 2 s grace period; some flyctl code paths
    # ignore SIGTERM while waiting on the WireGuard tunnel.
    #
    # Subshell + `2>/dev/null` on the subshell suppresses bash's
    # job-control "Killed: 9" line if the foreground `timeout` child
    # is SIGKILL'd by something outside the script — most commonly
    # macOS Jetsam when the host is under memory pressure from
    # concurrent leerie runs. The probe still retries; we just don't
    # leak the alarming-looking stderr to the user.
    ( timeout --kill-after=2 10 flyctl ssh console --app "$fly_app" \
        --machine "$machine_id" --pty=false -C "true" \
        </dev/null >/dev/null 2>&1 ) 2>/dev/null
    probe_exit=$?
    if [ "$probe_exit" -eq 0 ]; then
      remote_log "remote: hallpass ready on $machine_id"
      return 0
    fi
    last_probe_exit=$probe_exit
    attempts=$((attempts + 1))
    # Heartbeat every 3rd probe (~15 s) so a slow hallpass startup is
    # visible to the user instead of looking like a silent wait.
    if [ "$attempts" -lt "$max_attempts" ]; then
      if [ $((attempts % 3)) -eq 0 ]; then
        remote_log "remote: still waiting for hallpass on $machine_id (attempt $attempts of $max_attempts)..."
      fi
      sleep 5
    fi
  done
  # Exit 137 = WIFSIGNALED + SIGKILL. Could be `timeout --kill-after`
  # firing on a genuinely-hung flyctl, OR an external SIGKILL (e.g.
  # macOS Jetsam). Either way the operator benefits from knowing it's
  # likely client-host pressure, not a real Fly outage.
  if [ "$last_probe_exit" -eq 137 ]; then
    remote_log "warning: machine $machine_id did not accept SSH within ~175s (last probe killed externally — possible host memory pressure on this client)"
  else
    remote_log "warning: machine $machine_id did not accept SSH within ~175s"
  fi
  return 1
}


# Internal: install flyctl via the OS-appropriate path with user prompt.
_require_flyctl_install() {
  local os
  os="$(uname -s)"
  echo "" >&2
  case "$os" in
    Darwin)
      remote_log "flyctl is not installed. Install via:"
      remote_log "         brew install flyctl"
      printf "       Run it now? [Y/n] " >&2
      local ans
      read -r ans
      case "${ans:-Y}" in
        [Yy]*|"") ;;
        *)
          remote_log "aborted by user; install flyctl manually and re-run."
          return 1
          ;;
      esac
      if ! command -v brew >/dev/null 2>&1; then
        remote_log "brew not found. Install Homebrew first: https://brew.sh"
        return 1
      fi
      if ! brew install flyctl; then
        remote_log "brew install flyctl failed"
        return 1
      fi
      ;;
    Linux)
      remote_log "flyctl is not installed. Install via:"
      remote_log "         curl -L https://fly.io/install.sh | sh"
      printf "       Run it now? [Y/n] " >&2
      local ans
      read -r ans
      case "${ans:-Y}" in
        [Yy]*|"") ;;
        *)
          remote_log "aborted by user; install flyctl manually and re-run."
          return 1
          ;;
      esac
      if ! curl -L https://fly.io/install.sh | sh; then
        remote_log "flyctl install script failed"
        return 1
      fi
      ;;
    *)
      remote_log "don't know how to install flyctl on $os."
      remote_log "  Install manually from https://fly.io/docs/flyctl/install/"
      return 1
      ;;
  esac
  return 0
}

# Internal: prompt + run flyctl auth login. Opens a browser.
_require_flyctl_login() {
  echo "" >&2
  remote_log "flyctl is installed but not authenticated."
  printf "       Run 'flyctl auth login' now (opens browser)? [Y/n] " >&2
  local ans
  read -r ans
  case "${ans:-Y}" in
    [Yy]*|"") ;;
    *)
      remote_log "aborted by user; run 'flyctl auth login' manually and re-run."
      return 1
      ;;
  esac
  if ! flyctl auth login; then
    remote_log "flyctl auth login failed"
    return 1
  fi
  if ! flyctl auth status >/dev/null 2>&1; then
    remote_log "flyctl auth login reported success but auth status still fails."
    return 1
  fi
  return 0
}
