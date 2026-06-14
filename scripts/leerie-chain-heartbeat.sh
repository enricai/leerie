#!/usr/bin/env bash
# scripts/leerie-chain-heartbeat.sh — background worker → coordinator heartbeat.
#
# Started by the chain-init step inside the worker's Fly machine, BEFORE
# the orchestrator launches. Loops in the background, POSTing /heartbeat
# to the coordinator every LEERIE_CHAIN_HEARTBEAT_INTERVAL_S seconds
# (default 60). Killed via SIGTERM by the chain-exit-hook trap when the
# orchestrator finishes.
#
# The coordinator marks a run `presumed_failed` if no heartbeat arrives
# for LEERIE_HEARTBEAT_STALENESS_S seconds (default 900 = 15 min).
# Industry benchmarks: Temporal recommends 30s heartbeats, RabbitMQ 5-20s,
# SQL Server AGs 10s with 3-strike timeouts. 60s is a conservative choice
# given that leerie workers run for minutes-to-hours.
#
# Inputs (env):
#   LEERIE_CHAIN_ID                    — chain UUID (required)
#   LEERIE_RUN_ID                      — chain-run id within that chain (required)
#   LEERIE_COORDINATOR_HOST            — <coord-id>.vm.<app>.internal:8080 (required)
#   LEERIE_CHAIN_HEARTBEAT_INTERVAL_S  — beat interval, default 60s
#
# This script writes to remote_log if available (sourced from
# scripts/remote/lib.sh) but is tolerant of missing log helpers — it
# may run before lib.sh is sourced in some boot orders.
set -u

_log() {
  if command -v remote_log >/dev/null 2>&1; then
    remote_log "$@"
  else
    printf '%s %s\n' "$(date -u +%FT%TZ)" "$*" >&2
  fi
}

if [ -z "${LEERIE_CHAIN_ID:-}" ] || [ -z "${LEERIE_RUN_ID:-}" ]; then
  _log "chain-heartbeat: LEERIE_CHAIN_ID/RUN_ID not set; exiting"
  exit 0
fi
if [ -z "${LEERIE_COORDINATOR_HOST:-}" ]; then
  _log "chain-heartbeat: LEERIE_COORDINATOR_HOST not set; exiting"
  exit 0
fi

interval="${LEERIE_CHAIN_HEARTBEAT_INTERVAL_S:-60}"
url="http://${LEERIE_COORDINATOR_HOST}/heartbeat"
payload="$(printf '{"run_id":"%s"}' "$LEERIE_RUN_ID")"

# Exit cleanly on SIGTERM so the chain-exit-hook can stop us deterministically.
trap 'exit 0' TERM INT

while :; do
  # Don't crash the loop on transient errors — coordinator restarts,
  # network blips, etc. should be tolerated until the staleness window.
  curl -sS -o /dev/null \
       -X POST \
       -H "Content-Type: application/json" \
       --connect-timeout 5 \
       --max-time 10 \
       -d "$payload" \
       "$url" \
    || _log "chain-heartbeat: POST /heartbeat failed (will retry in ${interval}s)"
  sleep "$interval"
done
