#!/usr/bin/env bash
# cleanup.sh — remove a leerie run's worktrees and (optionally) branches.
#
# Run from the repo root. Default behavior is run-scoped: cleanup never
# touches more than one run at a time unless --all-runs is passed.
#
# Paths resolve via ${LEERIE_STATE_DIR:-.leerie} (i.e. <state-root>).
# Inside the container this is /leerie-state; on the host without an
# override it falls back to the repo-local .leerie/ (used by tests and non-containerized invocations).
#
# Modes:
#   cleanup.sh --run-id <id> [--branches | --subtask-branches]
#     Remove <state-root>/runs/<id>/worktrees/* and prune git metadata.
#     With --branches also delete leerie/runs/<id> and
#     leerie/subtasks/<id>/* branches.
#     With --subtask-branches delete only the subtask branches and keep
#     leerie/runs/<id> (the post-finalize default — the run branch is
#     the PR head and must outlive the orchestrator).
#
#   cleanup.sh --all-runs [--branches | --subtask-branches]
#     Same as above, applied to every directory under <state-root>/runs/.
#
#   cleanup.sh  (no flag)
#     Scans <state-root>/runs/*/state.json for the most recently failed run
#     (most recent without finished_at), prompts y/N, cleans only that run.
set -euo pipefail

# shellcheck source=scripts/worktree-lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/worktree-lib.sh"

# Honor LEERIE_STATE_DIR — matches setup-run.sh:25 and finalize.sh.
# Without this, in-container invocations (LEERIE_STATE_DIR=/leerie-state)
# silently scan /work/.leerie/runs/ instead of /leerie-state/runs/,
# masking the cleanup as a no-op and leaving worktrees behind.
LEERIE_ROOT="${LEERIE_STATE_DIR:-.leerie}"

# 240s calibration mirrors _cleanup_on_abnormal_exit (leerie.py:3896-3920).
# Override is test-only, to exercise the timeout-fires path without a
# real multi-minute wait.
WORKTREE_REMOVE_TIMEOUT="${CLEANUP_SH_TEST_WORKTREE_REMOVE_TIMEOUT:-240}"

# --- helpers -------------------------------------------------------------

clean_one_run() {
  # Args: $1 = run_id
  #       $2 = branch-scope: "0" = keep all branches (audit trail),
  #                          "1" = delete run branch + subtask branches (--branches),
  #                          "2" = delete subtask branches only, keep the run
  #                                branch (--subtask-branches; the post-finalize
  #                                default, since the run branch is the PR head).
  #
  # Removes ${run_dir}/worktrees/* (the worktree directories), prunes git
  # worktree metadata, and optionally deletes branches. The state dir
  # itself (state.json, run.json, criteria/, logs/, checkpoints/) is KEPT
  # as an audit trail. Full nuke-the-run-entirely is the Ctrl-C
  # (full_purge) path inside the orchestrator, not this script.
  local run_id="$1"
  local branch_scope="$2"
  local run_dir="${LEERIE_ROOT}/runs/${run_id}"

  if [ -d "${run_dir}/worktrees" ]; then
    for d in "${run_dir}/worktrees"/*/; do
      [ -d "$d" ] || continue
      # 240s timeout mirrors the calibrated bound on the abnormal-exit
      # Python path (_cleanup_on_abnormal_exit, leerie.py:3896-3920): a
      # 868MB/41k-file worktree takes ~45-90s uncontested and several-fold
      # more under N-way concurrent wave cleanup, but a genuinely hung
      # `git worktree remove` (stale mount, held index.lock, disk
      # contention) must not block the whole orchestrator forever.
      timeout "$WORKTREE_REMOVE_TIMEOUT" git worktree remove --force "$d" 2>/dev/null || true
      # Fall back to rm -rf when `git worktree remove` administratively
      # succeeded but left the directory behind (timeout mid-rmtree) or
      # when git no longer tracks the worktree (already pruned) and so
      # nonzero-exits without touching disk. Without this fallback, a
      # stale worktree directory persists across cleanups and blocks
      # `resume`'s new-worktree.sh from re-creating the worktree at
      # the same path. Path is sandboxed under
      # .leerie/runs/<run-id>/worktrees/<sid>; the leading `[ -d ]` plus
      # the glob restricts scope to that directory's children.
      if [ -d "$d" ]; then
        rm -rf "$d"
      fi
    done
    # Remove the now-empty worktrees dir if everything went; harmless if
    # something stayed behind (rmdir refuses non-empty).
    rmdir "${run_dir}/worktrees" 2>/dev/null || true
  fi
  # Scoped prune: leerie's OWN registrations only. A bare
  # `git worktree prune` is repository-global with no grace period, so
  # inside a container sharing the host's .git it destroys host-side
  # worktrees whose paths the container cannot see (scripts/worktree-lib.sh;
  # docs/POSTMORTEM-2026-08-14.md, F19).
  prune_leerie_worktrees "${LEERIE_ROOT}"

  # Per-run branches live under two disjoint namespaces:
  #   leerie/runs/<run-id>           (the run branch itself)
  #   leerie/subtasks/<run-id>/<sid> (one branch per subtask)
  # See DESIGN.md §3 for why the namespaces are split.
  case "$branch_scope" in
    1)  # --branches: delete both
      for b in $(git for-each-ref --format='%(refname:short)' \
                 "refs/heads/leerie/runs/${run_id}" \
                 "refs/heads/leerie/subtasks/${run_id}/"); do
        git branch -D "$b" 2>/dev/null || true
      done
      echo "cleanup: removed worktrees + branches for run ${run_id} (state dir kept as audit trail at ${run_dir}; rm -rf manually if no longer needed)"
      ;;
    2)  # --subtask-branches: delete subtask branches only
      for b in $(git for-each-ref --format='%(refname:short)' \
                 "refs/heads/leerie/subtasks/${run_id}/"); do
        git branch -D "$b" 2>/dev/null || true
      done
      echo "cleanup: removed worktrees + subtask branches for run ${run_id} (run branch leerie/runs/${run_id} and state dir kept; pass --branches to delete the run branch too)"
      ;;
    *)  # default: keep both
      echo "cleanup: removed worktrees for run ${run_id} (branches leerie/runs/${run_id} + leerie/subtasks/${run_id}/* and state dir kept; pass --subtask-branches or --branches to delete branches too)"
      ;;
  esac
}

most_recent_failed_run() {
  # Find the most-recent run without finished_at. Echo run_id or "".
  local newest=""
  local newest_started=""
  if [ ! -d "${LEERIE_ROOT}/runs" ]; then
    return
  fi
  for dir in "${LEERIE_ROOT}"/runs/*/; do
    [ -d "$dir" ] || continue
    local base
    base="$(basename "$dir")"
    local state="${dir}state.json"
    [ -f "$state" ] || continue
    # Parse finished_at and started_at from state.json by grep.
    # JSON parsing in bash is fragile; this is intentionally tolerant.
    if grep -q '"finished_at": *"' "$state"; then
      continue
    fi
    local started
    started="$(grep '"started_at":' "$state" | head -1 \
               | sed 's/.*"started_at": *"\([^"]*\)".*/\1/')"
    if [ -z "$newest" ] || [ "$started" \> "$newest_started" ]; then
      newest="$base"
      newest_started="$started"
    fi
  done
  echo "$newest"
}

# --- argument parsing ----------------------------------------------------

RUN_ID=""
ALL_RUNS=false
BRANCHES=false
SUBTASK_BRANCHES=false

while [ $# -gt 0 ]; do
  case "$1" in
    --run-id)
      RUN_ID="${2:?--run-id needs an argument}"
      shift 2
      ;;
    --all-runs)
      ALL_RUNS=true
      shift
      ;;
    --branches)
      BRANCHES=true
      shift
      ;;
    --subtask-branches)
      SUBTASK_BRANCHES=true
      shift
      ;;
    *)
      echo "cleanup.sh: unrecognized arg: $1" >&2
      echo "usage: cleanup.sh [--run-id <id> | --all-runs] [--branches | --subtask-branches]" >&2
      exit 2
      ;;
  esac
done

if [ "$BRANCHES" = "true" ] && [ "$SUBTASK_BRANCHES" = "true" ]; then
  echo "cleanup.sh: --branches and --subtask-branches are mutually exclusive" >&2
  exit 2
fi

# Branch-scope: 0 = keep all, 1 = --branches, 2 = --subtask-branches
BR_FLAG=0
[ "$BRANCHES" = "true" ] && BR_FLAG=1
[ "$SUBTASK_BRANCHES" = "true" ] && BR_FLAG=2

# --- mode dispatch -------------------------------------------------------

if [ "$ALL_RUNS" = "true" ]; then
  # ----- every per-run directory ------------------------------------------
  if [ ! -d "${LEERIE_ROOT}/runs" ]; then
    echo "cleanup: no ${LEERIE_ROOT}/runs/ to clean"
    exit 0
  fi
  cleaned=0
  for dir in "${LEERIE_ROOT}"/runs/*/; do
    [ -d "$dir" ] || continue
    clean_one_run "$(basename "$dir")" "$BR_FLAG"
    cleaned=$((cleaned + 1))
  done
  if [ "$cleaned" -eq 0 ]; then
    echo "cleanup: no runs to clean"
  fi
  exit 0
fi

if [ -n "$RUN_ID" ]; then
  # ----- single-run cleanup ----------------------------------------------
  if [ ! -d "${LEERIE_ROOT}/runs/${RUN_ID}" ]; then
    echo "cleanup: no run directory at ${LEERIE_ROOT}/runs/${RUN_ID}" >&2
    exit 1
  fi
  clean_one_run "$RUN_ID" "$BR_FLAG"
  exit 0
fi

# ----- default: most recently failed run, with confirmation --------------
target="$(most_recent_failed_run)"
if [ -z "$target" ]; then
  echo "cleanup: no in-progress / failed runs found. " \
       "Use --run-id <id> or --all-runs."
  exit 0
fi
printf "cleanup: most-recently-failed run is %s — remove? [y/N] " "$target"
read -r answer
case "$answer" in
  [yY]|[yY][eE][sS])
    clean_one_run "$target" "$BR_FLAG"
    ;;
  *)
    echo "cleanup: aborted"
    exit 0
    ;;
esac
