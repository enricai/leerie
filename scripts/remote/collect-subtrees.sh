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
# Usage (sourced by the leerie launcher's finalize path):
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
# The remote orchestrator's state root is /work/.leerie (no LEERIE_STATE_DIR
# there), so the scripts below must use the same one: they find the run's
# staging and its own repository under it.
LEERIE_STATE_DIR="/work/.leerie"
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

# Every branch and worktree of the run lives in the run's own repository
# (DESIGN §6 *The run's own repository*); the scripts act on the
# repository at their working directory.
if [ ! -f "$run_dir/repo/HEAD" ]; then
  echo "COLLECT-ERROR:no run repository at $run_dir/repo"
  exit 0
fi
cd "$run_dir/repo" || { echo "COLLECT-ERROR:cannot enter $run_dir/repo"; exit 0; }
# Named, never discovered (safe.bareRepository=explicit): the scripts' and
# this payload's repository-level git.
LEERIE_GIT_DIR="$run_dir/repo"
export LEERIE_GIT_DIR

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
done < <(git --git-dir="$LEERIE_GIT_DIR" for-each-ref --format='%(refname:short)' "refs/heads/${subtask_prefix}")

if [ "${#branches[@]}" -eq 0 ]; then
  echo "COLLECTED-NONE:${run_id}"
  exit 0
fi

# Filter out already-integrated branches.
unintegrated=()
for br in "${branches[@]}"; do
  if git --git-dir="$LEERIE_GIT_DIR" merge-base --is-ancestor "$br" "$run_branch" 2>/dev/null; then
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
sorted_remaining=()
if [ "${#remaining[@]}" -gt 0 ]; then
  mapfile -t sorted_remaining < <(printf '%s\n' "${remaining[@]}" | sort)
fi
for sid in "${sorted_remaining[@]}"; do
  integrate_order+=("$sid")
done

staging="${LEERIE_STATE_DIR}/runs/${run_id}/worktrees/staging"
leerie_dir="${LEERIE_STATE_DIR}/runs/${run_id}"
integrator_prompt="${SCRIPTS}/../prompts/integrator.md"
# Must be kept in sync with SCHEMAS["integrator"] in orchestrator/leerie.py.
# Embedded copies of the orchestrator's ACT_TOOLS / DISALLOWED_TOOLS. This
# script invokes `claude -p` directly on the remote machine; it loads
# orchestrator/leerie.py only through worker-guard.py (below), for the worker
# guard, and falls back to this deny list when that load fails. The schema
# and both lists are duplicated here and guarded by
# tests/test_collect_subtrees_integrator_schema.py. ANY EDIT TO EITHER LIST
# IN leerie.py MUST BE MIRRORED HERE.
integrator_allowed_tools='Read,Grep,Glob,WebSearch,WebFetch,Bash,Write,Edit,NotebookEdit,TaskStop'
integrator_disallowed_tools='Agent,Task,SendMessage,ScheduleWakeup,CronCreate,CronDelete,CronList,RemoteTrigger,PushNotification,Workflow,ReportFindings,Skill,Monitor,TaskCreate,TaskGet,TaskList,TaskUpdate,TaskOutput,ListAgents,EnterWorktree,ExitWorktree,DesignSync,ToolSearch,ListMcpResourcesTool,ReadMcpResourceTool,ReadMcpResourceDirTool'
integrator_schema='{"type":"object","required":["incoming_subtask","status"],"properties":{"incoming_subtask":{"type":"string"},"status":{"type":"string","enum":["resolved","design-conflict","failed"]},"resolution_summary":{"type":"string"},"diagnosis":{"type":["string","null"]},"confidence":{"type":"object","required":["resolution","basis"],"properties":{"resolution":{"type":"number"},"basis":{"type":"string"},"falsifiers_tested":{"type":"array","items":{"type":"string"}},"contradictions_reconciled":{"type":"array","items":{"type":"string"}}}},"superseded":{"type":"array","items":{"type":"object","required":["file","line","by_line","reason"],"properties":{"file":{"type":"string"},"line":{"type":"string"},"by_line":{"type":"string"},"reason":{"type":"string","enum":["other_side_rewrite","duplicate","moved","obsolete_by_merged_intent"]}}}}}}'

integrated_count=0
resolved_count=0
skipped_count=0
skipped_sids=""
integrated_so_far=""

# A branch that does not integrate leaves staging exactly as it found it:
# reset to the commit before its merge, untracked files removed (ignored
# ones kept). `git merge --abort` kept an edit the integrator made outside
# the merge, which the next branch's merge then refused or the next
# integrator swept in (DESIGN §6 *Subtree collection*).
# The reset is setup-run.sh's (`leerie_reset_worktree`, which first restores
# the write bit a killed test's read-only directory took away). One that
# still fails stops the collection: every remaining branch is skipped, never
# merged onto a tree the reset could not restore.
# shellcheck source=scripts/worktree-lib.sh
. "$SCRIPTS/worktree-lib.sh"
# No git command of leerie's own runs a repository hook (DESIGN §6½).
leerie_git_hooks_off
backout_failed=false
_back_out() {
  if ! leerie_reset_worktree "$staging" "$run_branch" "$1" >&2; then
    backout_failed=true
    echo "collect-subtrees: staging could not be reset to ${1}; the remaining branches are skipped" >&2
  fi
}

_skip() {
  skipped_count=$((skipped_count + 1))
  if [ -n "$skipped_sids" ]; then
    skipped_sids="${skipped_sids},${1}"
  else
    skipped_sids="$1"
  fi
}

for sid in "${integrate_order[@]}"; do
  if [ "$backout_failed" = true ]; then
    _skip "$sid"
    continue
  fi
  pre=$(cd "$staging" && git rev-parse HEAD)
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
      _back_out "$pre"
      _skip "$sid"
      continue
    fi
    user_prompt="Resolve the in-progress merge conflict in this worktree.
LEERIE_DIR is ${leerie_dir}.
Incoming subtask: ${sid}
Already-integrated subtasks it may conflict with: ${integrated_so_far:-none}"

    # The per-worker guard (deny rules + notices + PreToolUse hook + script
    # shell env) from the orchestrator module baked into this image — the
    # same `_worker_guard` every orchestrator-spawned worker gets (DESIGN §9
    # *A worker never kills a process by name*). Falls back to the embedded
    # base deny list alone when it cannot be loaded.
    guard_disallowed="$integrator_disallowed_tools"
    guard_notice=""
    guard_settings=""
    guard_env=()
    guard_sh=""
    if guard_sh=$(python3 -I "$SCRIPTS/worker-guard.py" \
        "$integrator_allowed_tools" "$staging" "$leerie_dir/state.json" \
        2>/dev/null) && [ -n "$guard_sh" ]; then
      eval "$guard_sh"
    else
      echo "collect-subtrees: worker guard unavailable for ${sid}; using the embedded deny list" >&2
    fi
    guard_settings_args=()
    if [ -n "$guard_settings" ]; then
      guard_settings_args=(--settings "$guard_settings")
    fi

    claude_out=""
    claude_rc=0
    claude_out=$(cd "$staging" && env ${guard_env[@]+"${guard_env[@]}"} \
      claude -p "${user_prompt}${guard_notice}" \
      --append-system-prompt "$(cat "$integrator_prompt")" \
      --output-format json \
      --json-schema "$integrator_schema" \
      --allowedTools "$integrator_allowed_tools" \
      --disallowedTools "$guard_disallowed" \
      ${guard_settings_args[@]+"${guard_settings_args[@]}"} \
      --max-turns 60 \
      --model sonnet \
      --effort high \
      --strict-mcp-config \
      --dangerously-skip-permissions 2>/dev/null) || claude_rc=$?

    if [ "$claude_rc" -ne 0 ]; then
      _back_out "$pre"
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
      # Verify the merge was actually committed, and that the integrator
      # left its tree clean (I1, DESIGN §8 *A worker leaves its tree
      # clean* — no re-ask here: there is no session to resume).
      merge_head_rc=0
      (cd "$staging" && git rev-parse --verify --quiet MERGE_HEAD 2>/dev/null) || merge_head_rc=$?
      staged_files=""
      staged_files=$(cd "$staging" && git diff --cached --name-only 2>/dev/null || true)
      dirt=$(cd "$staging" && git status --porcelain --untracked-files=all 2>/dev/null || echo "status failed")
      now=$(cd "$staging" && git rev-parse HEAD)
      if [ "$merge_head_rc" -ne 0 ] && [ -z "$staged_files" ] \
          && [ -z "$dirt" ] && [ "$now" != "$pre" ]; then
        resolved_count=$((resolved_count + 1))
        if [ -n "$integrated_so_far" ]; then
          integrated_so_far="${integrated_so_far}, ${sid}"
        else
          integrated_so_far="$sid"
        fi
      else
        _back_out "$pre"
        _skip "$sid"
      fi
    else
      _back_out "$pre"
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
