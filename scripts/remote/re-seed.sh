#!/usr/bin/env bash
# scripts/remote/re-seed.sh — mid-run re-rsync of the host's working tree
# into a paused/running Fly Machine.
#
# Phase 4: realizes "Mid-run correction" from remote-task-system.md line 50
# ("a second rsync of current laptop state into the task, user-triggered").
# Only meaningful when there's a controlled moment to "pick" — i.e., after
# a pause (Phase 2 sets paused_at + fly_machine_id in the sidecar) or when
# the user explicitly invokes `leerie re-seed <run-id>`.
#
# Usage (invoked from the leerie launcher):
#
#   source scripts/remote/provision.sh    # for wait_for_started
#   source scripts/remote/seed-repo.sh    # for seed_repo_dirty
#   source scripts/remote/re-seed.sh
#   re_seed                                # reads fly_machine_id from sidecar
#
# Three operations, in order:
#   1. flyctl machine start (if stopped) + wait_for_started.
#   2. Refuse re-seed if /work has tracked-file dirty state on the machine
#      (unless LEERIE_RE_SEED_FORCE=1) — prevents silent clobbering of
#      in-flight worker edits that haven't yet been committed to a
#      per-subtask branch.
#   3. Run seed_repo_dirty — recompute the host's dirty set + force-include
#      .claude/, rsync to /work on the machine via fly_rsync_wrapper.
#      The full-history clone is preserved (no re-clone — that would
#      obliterate the run branch and per-subtask branches).
#
# Environment variables (set by the launcher):
#
#   LEERIE_RUN_ID         — run id whose sidecar holds fly_machine_id
#   USER_REPO           — host-side repo path (for git status + rsync source)
#   LEERIE_FLY_APP        — Fly.io app name (required)
#   LEERIE_RE_SEED_FORCE  — set to "1" to bypass the dirty-machine safety check

set -euo pipefail

_RESEED_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
. "$_RESEED_DIR/lib.sh"

# --- re_seed -------------------------------------------------------------
re_seed() {
  if [ -z "${LEERIE_RUN_ID:-}" ]; then
    remote_log "re_seed: LEERIE_RUN_ID is not set"
    return 1
  fi
  if [ -z "${USER_REPO:-}" ]; then
    remote_log "re_seed: USER_REPO is not set"
    return 1
  fi
  # run_id IS the machine ID (DESIGN §6).
  local mid="$LEERIE_RUN_ID"
  if [ -z "$mid" ]; then
    remote_log "re_seed: LEERIE_RUN_ID is not set"
    return 1
  fi

  LEERIE_MACHINE_ID="$mid"
  export LEERIE_MACHINE_ID

  local fly_app="${LEERIE_FLY_APP:-}"
  FLY_APP="$fly_app"
  export FLY_APP

  # --- Step 1: wake the machine if it's stopped ---------------------------
  local state
  # flyctl machine status does NOT support --json — parse text output.
  state="$(flyctl machine status "$mid" --app "$fly_app" 2>/dev/null \
           | sed 's/\x1b\[[0-9;]*m//g' \
           | awk -F': *' '/^State: / { print $2; exit }' \
           | tr -d '[:space:]' || true)"
  case "$state" in
    started|starting) : ;;
    stopped)
      remote_log "remote: re-seed: starting paused machine $mid..."
      if ! flyctl machine start "$mid" --app "$fly_app" >/dev/null 2>&1; then
        remote_log "re_seed: flyctl machine start failed for $mid"
        return 1
      fi
      if declare -f wait_for_started >/dev/null 2>&1; then
        wait_for_started "$mid" || return 1
      fi
      ;;
    destroyed|"")
      remote_log "re_seed: machine $mid is destroyed or missing — cannot re-seed"
      return 1
      ;;
    *)
      remote_log "re_seed: machine $mid is in state '$state' — cannot re-seed"
      return 1
      ;;
  esac

  # --- Step 2: refuse if machine has uncommitted edits to tracked files ----
  # Skip the safety check when --force is set. The check is one flyctl exec
  # (~1s); it catches the high-cost case where an implementer edited files
  # mid-task that the orchestrator hadn't committed to a per-subtask branch
  # yet. Re-seeding over those files silently produces a wrong PR.
  if [ "${LEERIE_RE_SEED_FORCE:-0}" != "1" ]; then
    local remote_dirty
    remote_dirty="$(flyctl ssh console --app "$fly_app" --machine "$mid" \
                      --pty=false -C "git -C /work status --porcelain" \
                      2>/dev/null || true)"
    # Filter out:
    #   - .leerie/ paths (worker state lives there and is expected to change)
    #   - untracked entries (porcelain "??") — these are not tracked-file
    #     edits the orchestrator might have failed to checkpoint. The rsync
    #     this guard protects doesn't use --delete, and for any path where
    #     both host and machine have the file the host's copy is source-of-
    #     truth by design (the whole point of re-seed). Refusing on `??`
    #     blocks every resume of any repo that has even one untracked file.
    remote_dirty="$(printf '%s\n' "$remote_dirty" \
                      | awk 'length($0) > 0 \
                             && substr($0,1,2) != "??" \
                             && substr($0,4) !~ /^\.leerie\// \
                             { print }')"
    if [ -n "$remote_dirty" ]; then
      remote_log "re_seed: machine /work has uncommitted edits to tracked files:"
      printf '%s\n' "$remote_dirty" | head -10 >&2
      echo "" >&2
      echo "  These edits would be clobbered by re-seed." >&2
      echo "  Inspect via: leerie resume $LEERIE_RUN_ID --shell" >&2
      echo "  Or bypass:   leerie re-seed $LEERIE_RUN_ID --force" >&2
      return 1
    fi
  fi

  # --- Step 3: rsync the host dirty set -----------------------------------
  if ! declare -f seed_repo_dirty >/dev/null 2>&1; then
    remote_log "re_seed: seed_repo_dirty not loaded — source seed-repo.sh first"
    return 1
  fi
  seed_repo_dirty
}
