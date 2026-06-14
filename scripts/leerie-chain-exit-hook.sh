#!/usr/bin/env bash
# scripts/leerie-chain-exit-hook.sh — chain-mode worker exit hook.
#
# Sourced (NOT exec'd) by scripts/remote/provision.sh's decide_teardown
# trap when LEERIE_CHAIN_ID is set in the env. Replaces the host_finalize
# step for chain runs: workers have no GitHub push credentials (verified
# at scripts/remote/seed-auth.sh:149-158), so push + PR happen on the
# coordinator. This hook just notifies the coordinator and exits.
#
# Inputs (read from env, set by the coordinator at worker launch):
#   LEERIE_CHAIN_ID         — chain UUID this worker belongs to
#   LEERIE_CHAIN_RUN_UUID   — chain-run UUID within that chain
#                             (coordinator-internal scope; this is the
#                             primary key into chain_runs, distinct from
#                             the orchestrator's Fly-machine-id run_id)
#   LEERIE_COORDINATOR_HOST — <coord-id>.vm.<app>.internal:8080
#
# Inputs from decide_teardown's locals:
#   $rc                — orchestrator exit code (0|10|11|75|other)
#   $run_dir           — host-side run dir (for branch + run.json lookup)
#
# Exposed function:
#   leerie_chain_report — POSTs /report to coordinator; sets
#                         LEERIE_CHAIN_HANDLED=1 on success so the
#                         caller knows to skip host_finalize.

# Map an orchestrator exit code to a coordinator report status.
# The coordinator's accepted terminal statuses are:
#   done         — exit 0, work finished
#   failed       — non-zero exit, real failure
#   stale_creds  — auth 401 from Claude (recoverable via --resume)
#   merge_failed — coordinator's synth-merge step conflicted
#
# This hook is the worker side; "merge_failed" is set by the coordinator,
# never reported by a worker. Workers see exit 0 (done) or non-zero
# (failed). Stale-creds detection requires inspecting run.json for an
# auth-401 marker; on first cut we treat all non-zero as "failed" and
# defer the stale-creds classification to a follow-up.
_leerie_chain_classify_status() {
  local rc="$1"
  case "$rc" in
    0)    echo "done" ;;
    10|11|75)
      # Structured non-error exits — for chain mode, treat as failed
      # because the run did not produce a usable branch. The chain
      # pauses; user resolves manually.
      echo "failed"
      ;;
    *)
      echo "failed"
      ;;
  esac
}

# Read a string field from run.json. jq -r emits "null" for missing
# keys; coerce that to empty. Returns 0 with the value on stdout (empty
# if absent); never errors.
_leerie_chain_runjson_field() {
  local run_dir="$1"
  local field="$2"
  if [ ! -f "$run_dir/run.json" ]; then
    return 0
  fi
  local v
  v="$(jq -r --arg f "$field" '.[$f] // ""' "$run_dir/run.json" 2>/dev/null || true)"
  if [ "$v" = "null" ]; then
    v=""
  fi
  printf '%s' "$v"
}

# Read the run branch from run.json, falling back to the default
# leerie/runs/<machine-id> shape the orchestrator produces.
_leerie_chain_branch() {
  local run_dir="$1"
  local machine_id="$2"
  local b
  b="$(_leerie_chain_runjson_field "$run_dir" branch)"
  if [ -n "$b" ]; then
    echo "$b"
    return 0
  fi
  echo "leerie/runs/$machine_id"
}

# POST a JSON report to the coordinator with retries.
# Returns 0 on first 2xx response; non-zero if all attempts fail.
_leerie_chain_post_report() {
  local url="$1"
  local payload="$2"
  local attempt=0
  local max_attempts=5
  local backoff=2
  while [ "$attempt" -lt "$max_attempts" ]; do
    attempt=$((attempt + 1))
    if curl -sS -X POST \
         -H "Content-Type: application/json" \
         --connect-timeout 5 \
         --max-time 15 \
         -d "$payload" \
         "$url"; then
      echo  # ensure trailing newline after curl's body
      return 0
    fi
    remote_log "chain-exit-hook: /report attempt $attempt/$max_attempts failed; retrying in ${backoff}s"
    sleep "$backoff"
    backoff=$((backoff * 2))
  done
  return 1
}

# Public function called by decide_teardown.
# Usage: leerie_chain_report "$rc" "$run_dir"
leerie_chain_report() {
  local rc="$1"
  local run_dir="$2"

  # Defensive: only run in chain mode.
  if [ -z "${LEERIE_CHAIN_ID:-}" ] || [ -z "${LEERIE_CHAIN_RUN_UUID:-}" ]; then
    return 0
  fi
  if [ -z "${LEERIE_COORDINATOR_HOST:-}" ]; then
    remote_log "chain-exit-hook: LEERIE_COORDINATOR_HOST is not set; cannot report"
    return 1
  fi

  # The orchestrator's run_id (= Fly machine id under --runtime fly)
  # was persisted to run.json by provision.sh; use it to derive the
  # branch and report it as machine_id alongside the chain-run UUID.
  local machine_id
  machine_id="$(_leerie_chain_runjson_field "$run_dir" fly_machine_id)"
  local status
  status="$(_leerie_chain_classify_status "$rc")"
  local branch
  branch="$(_leerie_chain_branch "$run_dir" "$machine_id")"

  local payload
  payload="$(jq -nc \
    --arg chain_run_uuid "$LEERIE_CHAIN_RUN_UUID" \
    --arg machine_id "$machine_id" \
    --arg status "$status" \
    --arg branch "$branch" \
    --argjson exit_code "$rc" \
    '{
      chain_run_uuid: $chain_run_uuid,
      machine_id: $machine_id,
      status: $status,
      exit_code: $exit_code,
      branch: $branch
    }'
  )"

  local url="http://${LEERIE_COORDINATOR_HOST}/report"
  remote_log "chain-exit-hook: POST /report rc=$rc status=$status branch=$branch machine_id=$machine_id"

  local response
  if response="$(_leerie_chain_post_report "$url" "$payload")"; then
    remote_log "chain-exit-hook: coordinator responded: $(echo "$response" | head -c 200)"
    export LEERIE_CHAIN_HANDLED=1
    return 0
  fi

  remote_log "chain-exit-hook: failed to report to coordinator after retries"
  return 1
}
