#!/usr/bin/env bash
# scripts/remote/collect-subtrees.sh — integrate un-merged subtask branches
# into the run branch on a Fly Machine.
#
# When does this run?  After `force_finalize_remote()` patches `finished_at`
# into `run.json` (or confirms it's already set), and BEFORE `fetch_branch()`
# streams the result to the host.  The orchestrator may have died mid-wave,
# leaving subtask branches with committed work that was never integrated into
# the run branch.  This script discovers those branches, runs `setup-run.sh`
# to ensure the staging worktree exists, and merges each un-integrated branch
# via `integrate.sh`.  Conflicts are resolved by spawning a `claude -p`
# integrator worker (same prompt and schema as the orchestrator's
# `integrate_wave`).  Branches the integrator cannot resolve are skipped
# and reported.
#
# Usage (sourced by the leerie launcher's --finalize path):
#
#   source scripts/remote/collect-subtrees.sh
#   collect_subtrees_remote "$FLY_APP" "$LEERIE_MACHINE_ID"
#
# Environment consumed:
#   FLY_APP             — Fly.io app name (e.g. "leerie")
#   LEERIE_MACHINE_ID   — ID of the Fly Machine to SSH into
#
# Exit semantics:
#   0  — collection completed (COLLECTED-ALL, COLLECTED, COLLECTED-NONE)
#   1  — error (COLLECT-ERROR, SSH failure, no sentinel)
#
# Sentinel protocol (single line on stdout from the remote payload):
#   COLLECTED-ALL:<run_id>:<count>
#       All un-integrated subtask branches merged cleanly.
#   COLLECTED:<run_id>:<integrated>:<skipped>:<sid1,sid2,...>
#       Some subtasks merged, some skipped (conflicts or precondition failure).
#   COLLECTED-NONE:<run_id>
#       No un-integrated subtask branches found.
#   COLLECT-ERROR:<message>
#       setup-run.sh failed, no subtask branches, or other error.

set -eu -o pipefail

# Source _log.sh so remote_log is available even when sourced standalone.
# shellcheck disable=SC1091
. "${LEERIE_REPO:-$(cd -- "$(dirname "$(dirname "$(dirname "${BASH_SOURCE[0]}")")")" && pwd -P)}/scripts/remote/_log.sh"

collect_subtrees_remote() {
  local app="${1:-${FLY_APP:-}}"
  local machine="${2:-${LEERIE_MACHINE_ID:-}}"

  if [ -z "$machine" ]; then
    remote_log "collect-subtrees: LEERIE_MACHINE_ID not set"
    return 1
  fi

  # The payload runs on the machine as bash.  It discovers the run-id,
  # ensures the staging worktree exists via setup-run.sh, lists subtask
  # branches, filters already-integrated ones, and merges the rest via
  # integrate.sh.  Conflicts are resolved by a `claude -p` integrator
  # worker; unresolvable conflicts are skipped.
  #
  # The scripts are baked into the image at /opt/leerie-image/scripts/.
  local payload
  payload=$(cat <<'BASH_EOF'
set -eu -o pipefail

SCRIPTS="/opt/leerie-image/scripts"
LEERIE_STATE_DIR="${LEERIE_STATE_DIR:-/leerie-state}"
export LEERIE_STATE_DIR

RUNS_DIR="/work/.leerie/runs"
if [ ! -d "$RUNS_DIR" ]; then
  echo "COLLECT-ERROR:no /work/.leerie/runs on machine"
  exit 0
fi

# Discover the single run dir.
candidates=()
for d in "$RUNS_DIR"/*/; do
  [ -d "$d" ] || continue
  candidates+=("$d")
done

if [ "${#candidates[@]}" -eq 0 ]; then
  echo "COLLECT-ERROR:no run dir"
  exit 0
fi
if [ "${#candidates[@]}" -gt 1 ]; then
  echo "COLLECT-ERROR:multiple run dirs (${#candidates[@]})"
  exit 0
fi

run_dir="${candidates[0]%/}"
run_id="$(basename "$run_dir")"
run_branch="leerie/runs/${run_id}"

cd /work

# Ensure the run branch + staging worktree exist.  setup-run.sh is
# idempotent — it never resets an existing branch.
if ! bash "$SCRIPTS/setup-run.sh" "$run_id" >/dev/null 2>&1; then
  echo "COLLECT-ERROR:setup-run.sh failed for ${run_id}"
  exit 0
fi

# List subtask branches for this run.
subtask_prefix="leerie/subtasks/${run_id}/"
branches=()
while IFS= read -r ref; do
  [ -n "$ref" ] || continue
  branches+=("$ref")
done < <(git for-each-ref --format='%(refname:short)' "refs/heads/${subtask_prefix}")

if [ "${#branches[@]}" -eq 0 ]; then
  echo "COLLECTED-NONE:${run_id}"
  exit 0
fi

# Filter out already-integrated branches.
unintegrated=()
for br in "${branches[@]}"; do
  if git merge-base --is-ancestor "$br" "$run_branch" 2>/dev/null; then
    continue
  fi
  unintegrated+=("$br")
done

if [ "${#unintegrated[@]}" -eq 0 ]; then
  echo "COLLECTED-NONE:${run_id}"
  exit 0
fi

# Try to order by wave membership from state.json.  The Python snippet
# reads waves + subtask_status and emits subtask IDs in wave order
# (completed waves first, then later waves).  Falls back to the
# unordered list on any failure.
state_json="${LEERIE_STATE_DIR}/runs/${run_id}/state.json"
ordered_sids=()
if [ -f "$state_json" ]; then
  while IFS= read -r sid; do
    [ -n "$sid" ] || continue
    ordered_sids+=("$sid")
  done < <(python3 -c '
import json, sys
try:
    st = json.load(open(sys.argv[1]))
    waves = st.get("waves") or []
    for wave in waves:
        for sid in wave:
            print(sid)
except Exception:
    pass
' "$state_json" 2>/dev/null || true)
fi

# Build the integration order: wave-ordered sids first (if they appear
# in the unintegrated set), then any remaining unintegrated branches
# sorted alphabetically.
integrate_order=()
seen=()

# Extract sid from branch name: leerie/subtasks/<run-id>/<sid> → <sid>
declare -A unintegrated_by_sid
for br in "${unintegrated[@]}"; do
  sid="${br#"${subtask_prefix}"}"
  unintegrated_by_sid["$sid"]="$br"
done

for sid in "${ordered_sids[@]}"; do
  if [ -n "${unintegrated_by_sid[$sid]+x}" ]; then
    integrate_order+=("$sid")
    seen+=("$sid")
  fi
done

# Remaining (not in wave order) — sorted alphabetically.
remaining=()
for sid in "${!unintegrated_by_sid[@]}"; do
  found=false
  for s in "${seen[@]}"; do
    if [ "$s" = "$sid" ]; then
      found=true
      break
    fi
  done
  if [ "$found" = "false" ]; then
    remaining+=("$sid")
  fi
done
IFS=$'\n' sorted_remaining=($(printf '%s\n' "${remaining[@]}" | sort)); unset IFS
for sid in "${sorted_remaining[@]}"; do
  integrate_order+=("$sid")
done

staging="${LEERIE_STATE_DIR}/runs/${run_id}/worktrees/staging"
leerie_dir="${LEERIE_STATE_DIR}/runs/${run_id}"
integrator_prompt="${SCRIPTS}/../prompts/integrator.md"
# Must be kept in sync with SCHEMAS["integrator"] in orchestrator/leerie.py —
# this script can't import Python, so there's no other guard against drift.
integrator_schema='{"type":"object","required":["incoming_subtask","status","confidence"],"properties":{"incoming_subtask":{"type":"string"},"status":{"type":"string","enum":["resolved","design-conflict","failed"]},"resolution_summary":{"type":"string"},"diagnosis":{"type":["string","null"]},"confidence":{"type":"object","required":["resolution","basis","falsifiers_tested","contradictions_reconciled","gap_to_close"],"properties":{"resolution":{"type":"number"},"basis":{"type":"string","maxLength":2000},"falsifiers_tested":{"type":"array","items":{"type":"string","maxLength":500}},"contradictions_reconciled":{"type":"array","items":{"type":"string","maxLength":500}},"gap_to_close":{"type":"object","properties":{"resolution":{"type":"string"}}}}}}}'

integrated_count=0
resolved_count=0
skipped_count=0
skipped_sids=""
integrated_so_far=""

_skip() {
  skipped_count=$((skipped_count + 1))
  if [ -n "$skipped_sids" ]; then
    skipped_sids="${skipped_sids},${1}"
  else
    skipped_sids="$1"
  fi
}

for sid in "${integrate_order[@]}"; do
  branch="${subtask_prefix}${sid}"
  rc=0
  bash "$SCRIPTS/integrate.sh" "$sid" "$run_id" >/dev/null 2>&1 || rc=$?
  if [ "$rc" -eq 0 ]; then
    integrated_count=$((integrated_count + 1))
    if [ -n "$integrated_so_far" ]; then
      integrated_so_far="${integrated_so_far}, ${sid}"
    else
      integrated_so_far="$sid"
    fi
  elif [ "$rc" -eq 1 ]; then
    # Conflict — staging worktree is mid-merge. Spawn integrator.
    if [ ! -f "$integrator_prompt" ]; then
      (cd "$staging" && git merge --abort 2>/dev/null || true)
      _skip "$sid"
      continue
    fi
    user_prompt="Resolve the in-progress merge conflict in this worktree.
LEERIE_DIR is ${leerie_dir}.
Incoming subtask: ${sid}
Already-integrated subtasks it may conflict with: ${integrated_so_far:-none}"

    claude_out=""
    claude_rc=0
    claude_out=$(cd "$staging" && claude -p "$user_prompt" \
      --append-system-prompt "$(cat "$integrator_prompt")" \
      --output-format json \
      --json-schema "$integrator_schema" \
      --allowedTools "Read,Grep,Glob,WebSearch,WebFetch,Bash,Write,Edit" \
      --max-turns 60 \
      --model sonnet \
      --effort high \
      --dangerously-skip-permissions 2>/dev/null) || claude_rc=$?

    if [ "$claude_rc" -ne 0 ]; then
      (cd "$staging" && git merge --abort 2>/dev/null || true)
      _skip "$sid"
      continue
    fi

    # Extract status from structured_output.
    status=$(printf '%s' "$claude_out" | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
    so = d.get("structured_output") or {}
    print(so.get("status", ""))
except Exception:
    print("")
' 2>/dev/null || true)

    if [ "$status" = "resolved" ]; then
      # Verify the merge was actually committed.
      merge_head_rc=0
      (cd "$staging" && git rev-parse --verify --quiet MERGE_HEAD 2>/dev/null) || merge_head_rc=$?
      staged_files=""
      staged_files=$(cd "$staging" && git diff --cached --name-only 2>/dev/null || true)
      if [ "$merge_head_rc" -ne 0 ] && [ -z "$staged_files" ]; then
        resolved_count=$((resolved_count + 1))
        if [ -n "$integrated_so_far" ]; then
          integrated_so_far="${integrated_so_far}, ${sid}"
        else
          integrated_so_far="$sid"
        fi
      else
        (cd "$staging" && git merge --abort 2>/dev/null || true)
        _skip "$sid"
      fi
    else
      (cd "$staging" && git merge --abort 2>/dev/null || true)
      _skip "$sid"
    fi
  else
    # Precondition failure (exit 2) or other error — skip.
    _skip "$sid"
  fi
done

total_integrated=$((integrated_count + resolved_count))
if [ "$skipped_count" -eq 0 ]; then
  echo "COLLECTED-ALL:${run_id}:${total_integrated}"
elif [ "$total_integrated" -eq 0 ]; then
  echo "COLLECTED:${run_id}:0:${skipped_count}:${skipped_sids}"
else
  echo "COLLECTED:${run_id}:${total_integrated}:${skipped_count}:${skipped_sids}"
fi
exit 0
BASH_EOF
)

  local result
  if ! result="$(printf '%s' "$payload" \
        | flyctl ssh console --app "$app" --machine "$machine" \
            --pty=false -C "bash -s" 2>&1)"; then
    remote_log "collect-subtrees: SSH to machine $machine failed"
    remote_log "  output: $result"
    return 1
  fi

  # Parse the sentinel from the output (strip CR, filter for known prefixes).
  local sentinel
  sentinel="$(printf '%s\n' "$result" | tr -d '\r' \
              | grep -E '^(COLLECTED-ALL|COLLECTED-NONE|COLLECTED|COLLECT-ERROR):' \
              | tail -1 || true)"
  if [ -z "$sentinel" ]; then
    remote_log "collect-subtrees: no sentinel in remote output:"
    remote_log "  $result"
    return 1
  fi

  case "$sentinel" in
    COLLECTED-ALL:*)
      local rest="${sentinel#COLLECTED-ALL:}"
      local rid="${rest%%:*}"
      local count="${rest#*:}"
      remote_log "collect-subtrees: machine=$machine run=$rid integrated $count subtask branch(es), 0 skipped"
      return 0
      ;;
    COLLECTED-NONE:*)
      local rid="${sentinel#COLLECTED-NONE:}"
      remote_log "collect-subtrees: machine=$machine run=$rid — all subtask branches already integrated (or none exist)"
      return 0
      ;;
    COLLECTED:*)
      local rest="${sentinel#COLLECTED:}"
      # Format: <run_id>:<integrated>:<skipped>:<skipped_sids>
      local rid count_i count_s sids
      rid="$(echo "$rest" | cut -d: -f1)"
      count_i="$(echo "$rest" | cut -d: -f2)"
      count_s="$(echo "$rest" | cut -d: -f3)"
      sids="$(echo "$rest" | cut -d: -f4-)"
      remote_log "collect-subtrees: machine=$machine run=$rid integrated $count_i, skipped $count_s (conflicts: $sids)"
      return 0
      ;;
    COLLECT-ERROR:*)
      remote_log "collect-subtrees: ${sentinel}"
      return 1
      ;;
    *)
      remote_log "collect-subtrees: unexpected sentinel: $sentinel"
      return 1
      ;;
  esac
}
