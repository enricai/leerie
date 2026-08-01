#!/usr/bin/env bash
# scripts/remote/resume-machine.sh — wake a paused Fly Machine and
# clear the pause sentinels from the run sidecar.
#
# Sourced by the leerie launcher's RUNTIME=fly branch when the run sidecar
# has `paused_at` set. Replaces provision_machine for the resume path:
# the machine already exists (stopped on its Fly volume), so we just
# start it and re-arm the teardown trap.
#
# Usage (invoked from the leerie launcher):
#
#   source scripts/remote/provision.sh    # for wait_for_started + traps
#   source scripts/remote/resume-machine.sh
#   resume_machine "<machine-id>"
#
# Environment variables (set by the launcher):
#
#   LEERIE_FLY_APP        — Fly.io app name (required)
#   FLY_IMAGE_TAG         — current image tag (registry.fly.io/<app>:<ver>)
#   USER_REPO             — host-side path to the user's repo (for sidecar I/O)
#   LEERIE_STATE_HOST_DIR — host-side state directory (preferred over USER_REPO)
#   LEERIE_RUN_ID         — the run id being resumed
#
# Exports:
#   LEERIE_MACHINE_ID — the resumed machine's ID
#
# Returns 0 on success, 1 on failure (machine not found / refuses to start).

set -euo pipefail

# Resolved via the same pattern as provision.sh.
_RESUME_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
. "$_RESUME_DIR/lib.sh"

# --- resume_machine ------------------------------------------------------
resume_machine() {
  local mid="${1:-}"
  if [ -z "$mid" ]; then
    remote_log "resume_machine: machine id required"
    return 1
  fi
  local fly_app="${LEERIE_FLY_APP:-}"
  remote_log "remote: resuming machine $mid (app=$fly_app)..."

  # Resolve sidecar path once — used by the image-update block below
  # and by the pause-sentinel clearing at the end.
  local sidecar=""
  if [ -n "${LEERIE_RUN_ID:-}" ]; then
    if [ -n "${LEERIE_STATE_HOST_DIR:-}" ]; then
      sidecar="$LEERIE_STATE_HOST_DIR/runs/$LEERIE_RUN_ID/run.json"
    elif [ -n "${USER_REPO:-}" ]; then
      sidecar="$USER_REPO/.leerie/runs/$LEERIE_RUN_ID/run.json"
    fi
  fi

  # Update machine image if the leerie version changed since provision.
  # The machine is stopped; --skip-start changes the image config without
  # booting. The subsequent flyctl machine start (below) boots with the
  # new image. Volumes at /work survive the update — only the ephemeral
  # rootfs is replaced (seed_auth re-provisions that on every resume).
  local current_tag="${FLY_IMAGE_TAG:-}"
  if [ -n "$current_tag" ] && [ -n "$sidecar" ] && [ -f "$sidecar" ]; then
    local stored_tag=""
    stored_tag="$(python3 -c "
import json, sys
try:
    print(json.load(open(sys.argv[1])).get('image_tag', ''))
except Exception:
    pass
" "$sidecar" 2>/dev/null || true)"
    if [ "$stored_tag" != "$current_tag" ]; then
      remote_log "remote: updating machine $mid image: $stored_tag → $current_tag"
      if ! flyctl machine update "$mid" --image "$current_tag" \
           --app "$fly_app" --skip-start -y 2>&1; then
        remote_log "warning: image update failed; resuming with existing image"
      else
        update_run_json "$sidecar" image_tag "$current_tag" || true
      fi
    fi
  fi

  if ! flyctl machine start "$mid" --app "$fly_app" >/dev/null 2>&1; then
    # The machine might already be running (idempotency on retry).
    # Check the state directly; only error if it's not start-able.
    local state
    # flyctl machine status does NOT support --json — parse text output.
    state="$(flyctl machine status "$mid" --app "$fly_app" 2>/dev/null \
             | sed 's/\x1b\[[0-9;]*m//g' \
             | awk -F': *' '/^State: / { print $2; exit }' \
             | tr -d '[:space:]' || true)"
    case "$state" in
      started|starting)
        : # already coming up; fall through to wait
        ;;
      destroyed|"")
        remote_log "machine $mid does not exist or has been destroyed"
        echo "  The pause sidecar references a machine that is no longer recoverable." >&2
        echo "  Delete .leerie/runs/<run-id>/run.json paused_at fields, or destroy" >&2
        echo "  the run and start fresh: scripts/cleanup.sh --run-id <id> --branches" >&2
        return 1
        ;;
      *)
        remote_log "machine $mid is in state '$state' — cannot resume"
        return 1
        ;;
    esac
  fi

  LEERIE_MACHINE_ID="$mid"
  export LEERIE_MACHINE_ID

  # Re-arm the teardown trap (provision.sh's decide_teardown). Sourcing
  # provision.sh before this script gives us the function; the trap is
  # registered fresh here because the launcher process is fresh on resume.
  if declare -f decide_teardown >/dev/null 2>&1; then
    # shellcheck disable=SC2064
    trap 'decide_teardown' EXIT INT TERM
  fi

  # Block until the machine is reachable.
  if declare -f wait_for_started >/dev/null 2>&1; then
    if ! wait_for_started "$mid"; then
      return 1
    fi
  fi

  # Clear the pause sentinels so the run no longer renders as
  # paused in `leerie list --status paused` once the resume succeeds.
  if [ -n "$sidecar" ] && [ -f "$sidecar" ]; then
    update_run_json "$sidecar" \
      paused_at "" \
      pause_reason "" || true
  fi

  remote_log "remote: machine $mid resumed"
  return 0
}
