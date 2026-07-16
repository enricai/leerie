#!/usr/bin/env bash
# scripts/remote/ec2-resume-instance.sh — wake a `stopped` EC2 instance
# and clear the pause sentinels from the run sidecar.
#
# The EC2 counterpart to scripts/remote/resume-machine.sh (DESIGN §6 *EC2
# runtime lifecycle*). Sources ec2-lib.sh + ec2-provision.sh for the
# shared primitives (require_aws, _aws_region_profile_args,
# wait_for_instance_ready, update_run_json/iso_now) rather than
# reimplementing them — this file adds only the resume half: start a
# stopped instance, wait for it to become reachable again, re-resolve
# its (possibly new) public IP into LEERIE_EC2_SSH_TARGET, and clear the
# sidecar's pause fields.
#
# Usage (invoked from the leerie launcher's RUNTIME=ec2 resume path):
#
#   source scripts/remote/ec2-provision.sh   # for wait_for_instance_ready
#   source scripts/remote/ec2-resume-instance.sh
#   resume_instance "<instance-id>"
#
# Environment variables (set by the launcher):
#
#   LEERIE_AWS_REGION / AWS_REGION   — region for every `aws` call (optional)
#   LEERIE_AWS_PROFILE / AWS_PROFILE — profile for every `aws` call (optional)
#   LEERIE_RUN_ID         — the run id being resumed (optional; when set,
#                            enables the run.json sidecar clear)
#   LEERIE_STATE_HOST_DIR — host-side state directory (preferred over USER_REPO)
#   USER_REPO             — host-side path to the user's repo (fallback
#                            sidecar location)
#
# Exports:
#   LEERIE_EC2_INSTANCE_ID — the resumed instance's ID
#   LEERIE_EC2_SSH_TARGET  — re-resolved ssh(1) destination (ec2-user@<ip>)
#
# Honors the one-way-ratchet invariant decide_ec2_teardown() already
# encodes: this file issues neither `terminate-instances` nor
# `delete-volume` on any code path — resume is a pure wake path.
#
# Returns 0 on success, 1 on failure (instance not found / refuses to
# start / never becomes reachable again).

set -euo pipefail

_RESUME_EC2_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
. "$_RESUME_EC2_DIR/lib.sh"
# shellcheck disable=SC1091
. "$_RESUME_EC2_DIR/ec2-lib.sh"
# shellcheck disable=SC1091
. "$_RESUME_EC2_DIR/ec2-provision.sh"

# --- _resolve_resume_sidecar -----------------------------------------------
# Same LEERIE_STATE_HOST_DIR-preferred-over-USER_REPO resolution used
# throughout ec2-provision.sh/resume-machine.sh. Prints the sidecar path,
# or nothing if it cannot be resolved.
_resolve_resume_sidecar() {
  if [ -z "${LEERIE_RUN_ID:-}" ]; then
    return 0
  fi
  if [ -n "${LEERIE_STATE_HOST_DIR:-}" ]; then
    printf '%s' "$LEERIE_STATE_HOST_DIR/runs/$LEERIE_RUN_ID/run.json"
  elif [ -n "${USER_REPO:-}" ]; then
    printf '%s' "$USER_REPO/.leerie/runs/$LEERIE_RUN_ID/run.json"
  fi
}

# --- _describe_instance_state -----------------------------------------------
# Prints the instance's current State.Name, or empty if it cannot be
# described (e.g. it has been terminated and AWS has already dropped the
# record).
_describe_instance_state() {
  local iid="$1"
  local aws_args=() _a
  while IFS= read -r _a; do aws_args+=("$_a"); done \
    < <(_aws_region_profile_args)
  local describe_out
  describe_out="$(aws ec2 describe-instances --instance-ids "$iid" ${aws_args[@]+"${aws_args[@]}"} --output json 2>/dev/null || true)"
  printf '%s' "$describe_out" | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
    print(d["Reservations"][0]["Instances"][0]["State"]["Name"])
except (ValueError, KeyError, IndexError):
    pass
' 2>/dev/null || true
}

# --- _resolve_ssh_target_from_instance ---------------------------------------
# Reads the instance's current PublicIpAddress via describe-instances and
# prints "ec2-user@<ip>". EC2 hands out a new public IP on every
# stop/start cycle unless an Elastic IP is attached, so this must be
# re-resolved on every resume rather than reused from provision time.
_resolve_ssh_target_from_instance() {
  local iid="$1"
  local aws_args=() _a
  while IFS= read -r _a; do aws_args+=("$_a"); done \
    < <(_aws_region_profile_args)
  local describe_out ip
  describe_out="$(aws ec2 describe-instances --instance-ids "$iid" ${aws_args[@]+"${aws_args[@]}"} --output json 2>/dev/null || true)"
  ip="$(printf '%s' "$describe_out" | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
    inst = d["Reservations"][0]["Instances"][0]
    print(inst.get("PublicIpAddress", ""))
except (ValueError, KeyError, IndexError):
    pass
' 2>/dev/null || true)"
  if [ -z "$ip" ]; then
    return 1
  fi
  printf 'ec2-user@%s' "$ip"
}

# --- resume_instance ---------------------------------------------------------
# Starts a stopped instance, waits for it to become ready again,
# re-resolves LEERIE_EC2_SSH_TARGET, and clears the run sidecar's pause
# fields. Idempotent: an already-running instance is a no-op on the
# start-instances call (readiness is still (re-)confirmed and the ssh
# target still (re-)resolved, since neither is expensive and both must
# be correct regardless of how resume_instance was invoked).
resume_instance() {
  local iid="${1:-}"
  if [ -z "$iid" ]; then
    remote_log "resume_instance: instance id required"
    return 1
  fi
  require_aws || return 1

  remote_log "remote: resuming instance $iid..."

  local state
  state="$(_describe_instance_state "$iid")"
  case "$state" in
    running)
      remote_log "remote: instance $iid is already running"
      ;;
    stopped|stopping|pending)
      local aws_args=() _a
      while IFS= read -r _a; do aws_args+=("$_a"); done \
        < <(_aws_region_profile_args)
      if ! aws ec2 start-instances --instance-ids "$iid" ${aws_args[@]+"${aws_args[@]}"} >/dev/null 2>&1; then
        remote_log "error: failed to start instance $iid"
        return 1
      fi
      ;;
    terminated|shutting-down|"")
      remote_log "instance $iid does not exist or has been terminated"
      echo "  The pause sidecar references an instance that is no longer recoverable." >&2
      echo "  Delete .leerie/runs/<run-id>/run.json paused_at fields, or destroy" >&2
      echo "  the run and start fresh." >&2
      return 1
      ;;
    *)
      remote_log "instance $iid is in state '$state' — cannot resume"
      return 1
      ;;
  esac

  LEERIE_EC2_INSTANCE_ID="$iid"
  export LEERIE_EC2_INSTANCE_ID

  if ! wait_for_instance_ready "$iid"; then
    return 1
  fi

  local ssh_target=""
  if ssh_target="$(_resolve_ssh_target_from_instance "$iid")"; then
    LEERIE_EC2_SSH_TARGET="$ssh_target"
    export LEERIE_EC2_SSH_TARGET
    remote_log "remote: ssh target re-resolved to $ssh_target"
  else
    remote_log "warning: could not resolve a public IP for instance $iid"
  fi

  # Re-arm the teardown trap — the launcher process is fresh on resume,
  # same as resume_machine.sh does for the Fly path.
  if declare -f decide_ec2_teardown >/dev/null 2>&1; then
    # shellcheck disable=SC2064
    trap 'decide_ec2_teardown' EXIT INT TERM
  fi

  local sidecar=""
  sidecar="$(_resolve_resume_sidecar)"
  if [ -n "$sidecar" ] && [ -f "$sidecar" ]; then
    update_run_json "$sidecar" \
      paused_at "" \
      pause_reason "" \
      ec2_instance_id "$iid" || true
  fi

  remote_log "remote: instance $iid resumed"
  return 0
}
