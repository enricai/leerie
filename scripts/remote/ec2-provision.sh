#!/usr/bin/env bash
# scripts/remote/ec2-provision.sh — provision an EC2 instance for one
# leerie run.
#
# The EC2 counterpart to scripts/remote/provision.sh (DESIGN §6 *EC2
# runtime lifecycle*, "Stage mapping" table). Creates an instance from
# LEERIE_EC2_* parameters, blocks until it is reachable (running +
# instance-status-ok + system-status-ok), then terminates it on exit —
# whether the caller exits cleanly, is interrupted by Ctrl-C, or crashes.
#
# Usage (invoked from the leerie launcher's RUNTIME=ec2 branch):
#
#   source scripts/remote/ec2-provision.sh
#   provision_instance            # blocks until instance is ready
#   # ... do work (run leerie inside the instance) ...
#   export LEERIE_REMOTE_EXIT_RC=$orch_rc   # launcher sets this on exit
#   # decide_ec2_teardown is registered as an EXIT trap; classifies the rc
#   # and routes to stop_instance (pause-on-failure) or terminate_instance.
#
# Environment variables (set by the launcher before sourcing):
#
#   LEERIE_EC2_AMI              — AMI id (required)
#   LEERIE_EC2_INSTANCE_TYPE    — instance type, e.g. m5.xlarge (required)
#   LEERIE_EC2_KEY_NAME         — EC2 key pair name (required)
#   LEERIE_EC2_SECURITY_GROUP   — security group id (required)
#   LEERIE_EC2_SUBNET_ID        — subnet id (required)
#   LEERIE_AWS_REGION / AWS_REGION       — region passed to every `aws` call
#     (optional; unset lets the aws CLI's own credential-chain region apply)
#   LEERIE_AWS_PROFILE / AWS_PROFILE     — profile passed to every `aws` call
#     (optional; unset lets the aws CLI use its default profile)
#   LEERIE_RUN_ID     — orchestrator-minted run id (optional; when set,
#                     provision writes ec2_instance_id to the run sidecar
#                     and decide_ec2_teardown writes paused_at on pause)
#   USER_REPO       — host-side path to the user's repo (for sidecar I/O)
#   LEERIE_REMOTE_EXIT_RC — set by the launcher just before exit; read by
#                     the EXIT trap to classify the orchestrator's exit
#                     code. Pause-worthy: any non-zero other than
#                     EXIT_NEEDS_ANSWERS=10, EX_TEMPFAIL=75, SIGINT=130,
#                     SIGTERM=143. Same table as provision.sh's
#                     decide_teardown (DESIGN §6 Remote pause-on-failure;
#                     "the exit-code classification table is
#                     runtime-agnostic by construction").
#
# Exports:
#   LEERIE_EC2_INSTANCE_ID — the created instance's ID (available after
#                             provision_instance)
#
# The teardown trap is registered when provision_instance succeeds. It
# fires on EXIT (clean exit), INT (Ctrl-C), and TERM (SIGTERM). An
# instance that fails to become ready is terminated immediately before
# provision_instance returns 1.
#
# No destroy_volume() counterpart exists here, unlike provision.sh's Fly
# path. DESIGN §6 "EBS volume lifecycle" case 1 (this design's adopted
# default): a bare `run-instances` with no explicit block-device mapping
# gets a single root EBS volume with AWS's own default
# DeleteOnTermination=true, so `terminate-instances` reaps it — no
# leerie-side reap code is needed and no orphan-volume case exists to
# test, unlike a Fly volume ("a Machine can be destroyed without
# destroying its volume").

set -euo pipefail

# --- shared lib (remote_log / update_run_json / iso_now / resolve_*) -----
# lib.sh provides update_run_json/iso_now (Fly-agnostic helpers) and
# pulls in _log.sh itself; ec2-lib.sh provides require_aws/resolve_*.
_EC2_PROVISION_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
. "$_EC2_PROVISION_DIR/lib.sh"
# shellcheck disable=SC1091
. "$_EC2_PROVISION_DIR/ec2-lib.sh"

# Max seconds to wait for the instance to reach running +
# instance-status-ok + system-status-ok.
INSTANCE_START_TIMEOUT="${LEERIE_INSTANCE_START_TIMEOUT:-300}"

# Exported instance ID — empty until provision_instance succeeds.
LEERIE_EC2_INSTANCE_ID=""

# --- _aws_region_profile_args ---------------------------------------------
# Emit (into the caller's array variable, by nameref) the --region/
# --profile flags to append to every `aws ec2`/`aws sts` invocation in
# this file. Empty when unset — lets the aws CLI's own credential chain
# resolve region/profile, matching require_aws()'s existing
# profile-optional pattern in ec2-lib.sh.
_aws_region_profile_args() {
  local -n _out="$1"
  _out=()
  local region="${LEERIE_AWS_REGION:-${AWS_REGION:-}}"
  local profile="${LEERIE_AWS_PROFILE:-${AWS_PROFILE:-}}"
  if [ -n "$region" ]; then
    _out+=(--region "$region")
  fi
  if [ -n "$profile" ]; then
    _out+=(--profile "$profile")
  fi
  return 0
}

# --- stop_instance ---------------------------------------------------------
# Pause-on-failure path: preserves the root EBS volume (StopInstances
# never invokes DeleteOnTermination — that attribute is
# termination-scoped, not stop-scoped; DESIGN §6 "Stop, don't terminate,
# on pause"). Idempotent and tolerant of an already-stopped instance.
stop_instance() {
  local iid="$LEERIE_EC2_INSTANCE_ID"
  if [ -z "$iid" ]; then
    return 0
  fi
  local aws_args=()
  _aws_region_profile_args aws_args
  remote_log "remote: stopping instance $iid (paused)..."
  aws ec2 stop-instances --instance-ids "$iid" "${aws_args[@]}" >/dev/null 2>&1 || true
  # Don't clear LEERIE_EC2_INSTANCE_ID — the launcher's notification block
  # needs it to print the attach/resume commands.
}

# --- terminate_instance -----------------------------------------------------
# Full reap. Idempotent: terminate is no-op if the instance is already gone.
terminate_instance() {
  local iid="$LEERIE_EC2_INSTANCE_ID"
  if [ -z "$iid" ]; then
    return 0
  fi
  local aws_args=()
  _aws_region_profile_args aws_args
  remote_log "remote: terminating instance $iid ..."
  if aws ec2 terminate-instances --instance-ids "$iid" "${aws_args[@]}" >/dev/null 2>&1; then
    remote_log "remote: instance $iid terminated"
  else
    remote_log "remote: instance $iid terminate attempted (may already be gone)"
  fi
  LEERIE_EC2_INSTANCE_ID=""
}

# --- _try_fetch_state_for_ec2_teardown --------------------------------------
# Sync the run branch + state dir from the instance to the host BEFORE
# teardown. Overridable by tests and by ec2-ssm.sh once its transport
# lands — DESIGN §6 "Transport substitution for `flyctl ssh console`"
# scopes the SSM/SSH transport to a separate file (ec2-ssm.sh, not yet
# implemented) rather than this provisioning-only subtask.
#
# Sources ec2-ssm.sh (if present) and calls its fetch_state_ec2 function.
# Until ec2-ssm.sh ships, this fails closed (returns 1) — the safe
# default per the ordering rule below is "leave the instance running,
# do not destroy possibly-unrecovered work."
#
# Requires: LEERIE_EC2_INSTANCE_ID, USER_REPO in scope (already set by
# the time decide_ec2_teardown runs, since provision_instance populates
# them).
_try_fetch_state_for_ec2_teardown() {
  local _leerie_dir="${LEERIE_REPO:-${LEERIE_HOME:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}}"
  if [ ! -f "$_leerie_dir/scripts/remote/ec2-ssm.sh" ]; then
    remote_log "_try_fetch_state_for_ec2_teardown: ec2-ssm.sh not available yet — cannot sync"
    return 1
  fi
  # shellcheck disable=SC1091
  . "$_leerie_dir/scripts/remote/ec2-ssm.sh"
  if ! command -v fetch_state_ec2 >/dev/null 2>&1; then
    remote_log "_try_fetch_state_for_ec2_teardown: ec2-ssm.sh loaded but fetch_state_ec2 is not defined"
    return 1
  fi
  if ! fetch_state_ec2; then
    return 1
  fi
  return 0
}

# --- decide_ec2_teardown (registered as EXIT/INT/TERM trap) ----------------
# The EC2 counterpart of decide_teardown() in provision.sh. Classifies
# $LEERIE_REMOTE_EXIT_RC (set by the launcher just before exit) and
# dispatches to stop_instance (pause-on-failure) or terminate_instance.
# Same classification table as Fly — DESIGN §6: "the exit-code
# classification table is runtime-agnostic by construction ... EC2 needs
# no new table, only a new teardown implementation of the same three
# dispositions."
#
# Clean-exit branch (rc=0/10/11/75): syncs run state to the host via
# _try_fetch_state_for_ec2_teardown BEFORE terminating the instance. If
# sync fails, leaves the instance RUNNING so the user can recover — the
# same one-way-ratchet rule provision.sh:262-272 documents: destroy-then-
# fetch means the user paid for LLM work that is now unrecoverable.
#
# Pause branch (other non-zero): writes paused_at + pause_reason to
# the run sidecar so the resume path can find the instance later.
#
# Idempotent: the trap fires on every exit, including success; the
# stop/terminate primitives no-op on an empty LEERIE_EC2_INSTANCE_ID.
decide_ec2_teardown() {
  # Idempotency: mirrors provision.sh:222-230's LEERIE_TEARDOWN_DONE
  # guard — skip every pass after the first so a SIGINT-then-EXIT
  # double-fire can't re-run teardown with a clobbered rc.
  if [ -n "${LEERIE_TEARDOWN_DONE:-}" ]; then
    return 0
  fi
  local rc="${LEERIE_REMOTE_EXIT_RC:-0}"
  local iid="$LEERIE_EC2_INSTANCE_ID"
  if [ -z "$iid" ]; then
    LEERIE_TEARDOWN_DONE=1
    export LEERIE_TEARDOWN_DONE
    return 0
  fi
  case "$rc" in
    0|10|11|75)
      # Genuine terminal exits of the *run* — same rc meanings as
      # provision.sh's decide_teardown (0 clean, 10 EXIT_NEEDS_ANSWERS,
      # 11 EXIT_BUDGET_INFEASIBLE, 75 EX_TEMPFAIL).
      #
      # SAFETY-CRITICAL: pull the run branch + state dir to the host
      # BEFORE terminating the instance. If we terminate first and then
      # try to fetch, the user has paid for LLM work that is now
      # unrecoverable. This is a one-way ratchet: instance termination
      # is gated on confirmed host-side state.
      if _try_fetch_state_for_ec2_teardown; then
        remote_log "remote: run branch + state synced to host"
        terminate_instance
      else
        local sync_reason="sync-failed-on-clean-exit"
        local sidecar=""
        if [ -n "${LEERIE_RUN_ID:-}" ]; then
          if [ -n "${LEERIE_STATE_HOST_DIR:-}" ]; then
            sidecar="$LEERIE_STATE_HOST_DIR/runs/$LEERIE_RUN_ID/run.json"
          elif [ -n "${USER_REPO:-}" ]; then
            sidecar="$USER_REPO/.leerie/runs/$LEERIE_RUN_ID/run.json"
          fi
        fi
        if [ -n "$sidecar" ] && [ -f "$sidecar" ]; then
          update_run_json "$sidecar" \
            sync_failed_at "$(iso_now)" \
            sync_fail_reason "$sync_reason" \
            ec2_instance_id "$iid" || true
        fi
        echo "" >&2
        echo "================================================================" >&2
        remote_log "WARNING — sync from instance to host FAILED."
        echo "  The orchestrator finished cleanly but the run branch + state" >&2
        echo "  could not be pulled back. The instance is being LEFT RUNNING" >&2
        echo "  so your work is not lost. Recover manually, then:" >&2
        echo "    leerie --kill ${LEERIE_RUN_ID:-<run-id>} --runtime ec2" >&2
        echo "  Instance: $iid (still running on EC2)" >&2
        echo "================================================================" >&2
        # Intentionally NO stop_instance, NO terminate_instance. The user
        # owns this instance until they explicitly --kill it.
      fi
      ;;
    130|143)
      # Host-side SIGINT (130) / SIGTERM (143). Detached orchestrator
      # keeps running on the instance; leave it alone and print reattach
      # hints (mirrors provision.sh's detach banner).
      echo "" >&2
      echo "================================================================" >&2
      remote_log "detached from run ${LEERIE_RUN_ID:-<unknown>} — orchestrator still running on EC2."
      echo "  You stopped watching. The orchestrator was NOT signalled and" >&2
      echo "  keeps making progress on the instance." >&2
      echo "    leerie --resume ${LEERIE_RUN_ID:-<run-id>}" >&2
      echo "    leerie --stop   ${LEERIE_RUN_ID:-<run-id>}" >&2
      echo "    leerie --kill   ${LEERIE_RUN_ID:-<run-id>}" >&2
      echo "  Instance: $iid (still running on EC2)" >&2
      echo "================================================================" >&2
      # Intentionally no stop/terminate — the orchestrator must keep running.
      ;;
    *)
      # Unknown non-zero: pause. Stop the instance (preserves the root
      # EBS volume) and surface the failure to the user via the run
      # sidecar.
      local reason="${LEERIE_PAUSE_REASON:-worker-error}"
      local sidecar=""
      if [ -n "${LEERIE_RUN_ID:-}" ]; then
        if [ -n "${LEERIE_STATE_HOST_DIR:-}" ]; then
          sidecar="$LEERIE_STATE_HOST_DIR/runs/$LEERIE_RUN_ID/run.json"
        elif [ -n "${USER_REPO:-}" ]; then
          sidecar="$USER_REPO/.leerie/runs/$LEERIE_RUN_ID/run.json"
        fi
      fi
      if [ -n "$sidecar" ] && [ -f "$sidecar" ]; then
        update_run_json "$sidecar" \
          paused_at "$(iso_now)" \
          pause_reason "$reason" \
          ec2_instance_id "$iid" || true
      fi
      stop_instance
      echo "" >&2
      remote_log "PAUSED: instance $iid (rc=$rc, reason=$reason)"
      if [ -n "${LEERIE_RUN_ID:-}" ]; then
        echo "  run-id:  $LEERIE_RUN_ID" >&2
      fi
      echo "  resume:  leerie --resume ${LEERIE_RUN_ID:-<run-id>}" >&2
      echo "  kill:    leerie --kill ${LEERIE_RUN_ID:-<run-id>}" >&2
      # Don't clear LEERIE_EC2_INSTANCE_ID — leave the pointer for the user.
      ;;
  esac
  LEERIE_TEARDOWN_DONE=1
  export LEERIE_TEARDOWN_DONE
}

# --- wait_for_instance_ready -------------------------------------------------
# Poll describe-instances for State.Name == running, then
# describe-instance-status for both InstanceStatus.Status == ok and
# SystemStatus.Status == ok. DESIGN §6: "a running EC2 instance is not
# yet SSH/SSM-reachable, unlike a Fly Machine where started and
# hallpass-warm are close together" — running alone is insufficient.
wait_for_instance_ready() {
  local iid="$1"
  local aws_args=()
  _aws_region_profile_args aws_args
  local deadline=$(( $(date +%s) + INSTANCE_START_TIMEOUT ))
  remote_log "remote: waiting for instance $iid to become ready (timeout: ${INSTANCE_START_TIMEOUT}s)..."

  # Phase 1: wait for State.Name == running. Parsed via python3 (not
  # `aws --query/--output text`) so the parsing works uniformly against
  # both the real aws CLI's JSON output and the test stub's plain JSON.
  while true; do
    local describe_out state
    describe_out="$(aws ec2 describe-instances --instance-ids "$iid" "${aws_args[@]}" --output json 2>/dev/null || true)"
    state="$(printf '%s' "$describe_out" | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
    print(d["Reservations"][0]["Instances"][0]["State"]["Name"])
except (ValueError, KeyError, IndexError):
    pass
' 2>/dev/null || true)"
    case "$state" in
      running)
        break
        ;;
      terminated|shutting-down|stopping|stopped)
        remote_log "instance $iid entered state '$state' — cannot proceed"
        return 1
        ;;
    esac
    if [ "$(date +%s)" -ge "$deadline" ]; then
      remote_log "timed out waiting for instance $iid to reach running (${INSTANCE_START_TIMEOUT}s)"
      return 1
    fi
    sleep 2
  done

  # Phase 2: wait for both status checks to pass.
  while true; do
    local status_out instance_status system_status statuses
    status_out="$(aws ec2 describe-instance-status --instance-ids "$iid" "${aws_args[@]}" --output json 2>/dev/null || true)"
    statuses="$(printf '%s' "$status_out" | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
    s = d["InstanceStatuses"][0]
    print(s["InstanceStatus"]["Status"], s["SystemStatus"]["Status"])
except (ValueError, KeyError, IndexError):
    print("", "")
' 2>/dev/null || echo "")"
    instance_status="$(printf '%s' "$statuses" | awk '{print $1}')"
    system_status="$(printf '%s' "$statuses" | awk '{print $2}')"
    if [ "$instance_status" = "ok" ] && [ "$system_status" = "ok" ]; then
      remote_log "remote: instance $iid is ready (running, status checks ok)"
      return 0
    fi
    if [ "$(date +%s)" -ge "$deadline" ]; then
      remote_log "timed out waiting for instance $iid status checks (${INSTANCE_START_TIMEOUT}s)"
      return 1
    fi
    sleep 2
  done
}

# --- provision_instance ------------------------------------------------------
# Creates an EC2 instance from LEERIE_EC2_* parameters, registers the
# terminate trap, and blocks until the instance is ready.
# Exports:  $LEERIE_EC2_INSTANCE_ID
# Returns:  0 on success; 1 on failure (instance is terminated before
#           returning, if one was created).
provision_instance() {
  require_aws || return 1

  local ami instance_type key_name security_group subnet_id
  ami="$(resolve_ami)" || return 1
  instance_type="$(resolve_instance_type)" || return 1
  key_name="$(resolve_key_name)" || return 1
  security_group="$(resolve_security_group)" || return 1
  subnet_id="$(resolve_subnet_id)" || return 1

  local aws_args=()
  _aws_region_profile_args aws_args

  remote_log "remote: creating instance (ami=$ami type=$instance_type)..."

  # No block-device mapping override: a bare run-instances gets a single
  # root EBS volume with AWS's own default DeleteOnTermination=true
  # (DESIGN §6 "EBS volume lifecycle" case 1 — this design's adopted
  # default). No volume-orphan cleanup path exists here because there is
  # nothing created before the instance itself that could be orphaned by
  # a failed create.
  local create_output=""
  local instance_id=""
  if create_output="$(aws ec2 run-instances \
       --image-id "$ami" \
       --instance-type "$instance_type" \
       --key-name "$key_name" \
       --security-group-ids "$security_group" \
       --subnet-id "$subnet_id" \
       --count 1 \
       "${aws_args[@]}" \
       --output json \
       2>&1)"; then
    instance_id="$(printf '%s' "$create_output" \
                    | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["Instances"][0]["InstanceId"])' \
                    2>/dev/null || true)"
  fi

  if [ -z "$instance_id" ]; then
    remote_log "failed to create EC2 instance — aws output:"
    printf '  %s\n' "$create_output" >&2
    return 1
  fi

  remote_log "remote: created instance $instance_id"
  LEERIE_EC2_INSTANCE_ID="$instance_id"
  export LEERIE_EC2_INSTANCE_ID

  # Register teardown trap immediately after a successful creation so
  # Ctrl-C or any error after this point cannot leak the instance (and
  # its cost). decide_ec2_teardown classifies $LEERIE_REMOTE_EXIT_RC and
  # dispatches to stop or terminate.
  # shellcheck disable=SC2064
  trap 'decide_ec2_teardown' EXIT INT TERM

  # Persist ec2_instance_id to the run sidecar immediately so a launcher
  # crash before classification still leaves a recoverable pointer
  # (DESIGN §6 Remote pause-on-failure; mirrors provision.sh's
  # fly_machine_id write timing).
  local _state_base=""
  if [ -n "${LEERIE_STATE_HOST_DIR:-}" ]; then
    _state_base="$LEERIE_STATE_HOST_DIR"
  elif [ -n "${USER_REPO:-}" ]; then
    _state_base="$USER_REPO/.leerie"
  fi
  if [ -n "${LEERIE_RUN_ID:-}" ] && [ -n "$_state_base" ]; then
    local sidecar="$_state_base/runs/$LEERIE_RUN_ID/run.json"
    if [ -f "$sidecar" ]; then
      update_run_json "$sidecar" \
        ec2_instance_id "$instance_id" \
        ec2_ami "$ami" || true
    fi
  fi

  # DESIGN §6 "Run identifier": an ec2-instance.json sidecar (instance
  # id, region, created-at) is the EC2 analog of provision.sh's
  # fly-machine.json crash-recovery pointer — written unconditionally
  # (not gated on LEERIE_RUN_ID) so --resume survives a Ctrl-C between
  # provision_instance() returning and the launcher minting a run id.
  if [ -n "$_state_base" ]; then
    local remote_dir="$_state_base/remote"
    mkdir -p "$remote_dir" 2>/dev/null || true
    local pid_record="$remote_dir/$$.json"
    python3 - "$pid_record" "$instance_id" "${LEERIE_AWS_REGION:-${AWS_REGION:-}}" "${LEERIE_RUN_ID:-}" "$$" <<'PY'
import json, sys, datetime
path, iid, region, run_id, pid = sys.argv[1:]
data = {
    "ec2_instance_id": iid,
    "region": region or None,
    "started_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
    "run_id": run_id or None,
    "launcher_pid": int(pid),
}
with open(path, "w") as f:
    json.dump(data, f, indent=2)
    f.write("\n")
PY
    if [ -n "${LEERIE_RUN_ID:-}" ]; then
      local run_dir="$_state_base/runs/$LEERIE_RUN_ID"
      mkdir -p "$run_dir" 2>/dev/null || true
      cp "$pid_record" "$run_dir/ec2-instance.json" 2>/dev/null || true
    fi
  fi

  # Wait until the instance is ready.
  if ! wait_for_instance_ready "$instance_id"; then
    # decide_ec2_teardown will fire via the EXIT trap as this function
    # returns 1. The non-zero rc the caller sets will route to terminate
    # (not pause) because an instance that never became ready has no
    # useful state to inspect.
    return 1
  fi

  return 0
}
