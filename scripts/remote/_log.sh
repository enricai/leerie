#!/usr/bin/env bash
# Tiny shared helper for timestamped, repo-tagged stderr logs.
# Sourced by lib.sh (for all host-side remote scripts) and directly by
# seed-auth.sh and fetch-branch.sh (whose standalone-test sourcing path
# doesn't go through lib.sh, and which want this helper without the rest
# of lib.sh's surface in scope).
#
# Format: <ISO-8601 seconds + ±HH:MM offset> [leerie] [<repo>] <msg>
# e.g.    2026-06-03T05:07:10-05:00 [leerie] [stackpulse] hello
# Repo derives from $USER_REPO (basename); falls back to "?" if unset.
# The `sed` fixes up BSD `date` output (`-0500`) to ISO-8601's `-05:00`
# — GNU `%:z` is not portable to the macOS launcher host.
remote_log() {
  local _repo _ts
  _repo="$(basename "${USER_REPO:-?}")"
  _ts="$(date +%FT%T%z | sed 's/\(..\)$/:\1/')"
  printf '%s [leerie] [%s] %s\n' "$_ts" "$_repo" "$*" >&2
}

# _seed_progress_bg <label> — background heartbeat for long bulk transfers
# over `flyctl ssh console`. Emits `remote_log "<label>: still streaming
# (Ns elapsed)"` every LEERIE_PROGRESS_INTERVAL_S seconds (default 10)
# until killed by the caller, so the user sees activity instead of a
# silent multi-minute pause.
#
# Why this exists: the host-side tar | flyctl ssh console pipe in
# seed_auth/seed_repo can hang for ~minutes when flyctl's SSH session
# stalls without exiting (observed 2026-06-04: four parallel runs all
# silent for 30+ minutes). Adding a heartbeat makes Mode-2 stalls
# obvious in the stream output instead of invisible.
#
# Implementation note: the function re-opens fd 2 to /dev/tty inside the
# bg subshell. Without this, a backgrounded process inheriting the
# caller's stderr keeps subprocess.run's captured pipe open even after
# kill, because the bg's copy of the pipe write end is never closed.
# Re-opening to /dev/tty also handles the no-tty case (tests, headless
# runs): the redirect fails and the function returns silently — heart-
# beat is suppressed, which is the right behavior when no one is
# watching anyway.
#
# Set LEERIE_PROGRESS_INTERVAL_S=0 to suppress the heartbeat explicitly.
#
# Usage:
#   _seed_progress_bg "seed_auth" &
#   _hb_pid=$!
#   tar -c ... | flyctl ssh console ... -C ...
#   kill "$_hb_pid" 2>/dev/null || true
#   wait "$_hb_pid" 2>/dev/null || true
_seed_progress_bg() {
  local _label="$1"
  local _interval="${LEERIE_PROGRESS_INTERVAL_S:-10}"
  if [ "$_interval" = "0" ]; then
    return 0
  fi
  # Re-open stderr from /dev/tty so the bg doesn't hold the caller's
  # stderr fd open. The caller's stderr may be a pipe (subprocess.run
  # captured output, leerie's stream-verbosity formatter); a backgrounded
  # process inheriting that pipe blocks the reader from getting EOF
  # until *every* copy of the write end is closed, even after the bg is
  # killed. Re-opening fd 2 to /dev/tty gives the bg its own writer that
  # the kill closes cleanly. If /dev/tty isn't available (the test
  # harness runs from a subprocess with no controlling terminal), the
  # redirect fails and the function returns immediately — heartbeat is
  # silently disabled, which matches the "no user is watching" intent.
  exec 2>/dev/tty || return 0
  local _start _now _elapsed
  _start="$(date +%s)"
  while sleep "$_interval"; do
    _now="$(date +%s)"
    _elapsed=$((_now - _start))
    remote_log "$_label: still streaming (${_elapsed}s elapsed)"
  done
}
