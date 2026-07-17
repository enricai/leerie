#!/usr/bin/env bash
# setup-run.sh <run-id> — initialize the per-run branch and worktree.
#
# Each leerie invocation has a unique run_id; this script sets up the
# directory and branch for that run. Records the current working branch,
# creates `leerie/runs/<run-id>` off the current HEAD, and adds a worktree
# at `<state-root>/runs/<run-id>/worktrees/staging`.
#
# State root: $LEERIE_STATE_DIR when set (centralized, outside the repo);
# falls back to `.leerie` (repo-relative; used by tests and direct invocations).
#
# GENUINELY idempotent: if `leerie/runs/<run-id>` already exists (a run
# is in progress, or this is a --resume), the branch is LEFT WHERE IT IS.
# It is never force-reset — doing so would discard every integration
# commit from the waves already completed.
#
# Branch shape: the `runs/` segment is mandatory. Subtask branches live
# under `leerie/subtasks/<run-id>/<sid>` (a sibling namespace) so the
# run-branch and its subtask branches can never be parent/child refs in
# git's loose ref store. See DESIGN.md §3 ("The run branch as an
# integration buffer") for the loose-ref-store collision being avoided.
set -euo pipefail

RUN_ID="${1:?usage: setup-run.sh <run-id>}"
LEERIE_ROOT="${LEERIE_STATE_DIR:-.leerie}"
RUN_DIR="${LEERIE_ROOT}/runs/${RUN_ID}"
BRANCH="leerie/runs/${RUN_ID}"
STAGING_WT="${RUN_DIR}/worktrees/staging"
# git worktree list --porcelain outputs absolute, symlink-resolved paths.
# $STAGING_WT must match that shape before the reuse grep below: when
# LEERIE_STATE_DIR is unset it is relative, and git resolves symlinks a
# relative path does not. pwd -P produces the form git prints.
case "$STAGING_WT" in
  /*) ;;
  *)  STAGING_WT="$(pwd -P)/$STAGING_WT" ;;
esac
WORKING_BRANCH_FILE="${RUN_DIR}/working-branch"

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "error: not inside a git repository" >&2
  exit 1
fi

# A bare branch named 'leerie' blocks creation of leerie/runs/* and
# leerie/subtasks/* (git's loose ref store cannot hold a file and a
# directory at the same path). Defense-in-depth: preflight() checks
# this too, but --resume skips preflight.
if git show-ref --verify --quiet refs/heads/leerie; then
  echo "error: branch 'leerie' conflicts with leerie's internal namespace (leerie/runs/*, leerie/subtasks/*)." >&2
  echo "Rename it: git branch -m leerie leerie-old" >&2
  exit 1
fi

mkdir -p "${RUN_DIR}/worktrees" "${RUN_DIR}/subtasks" "${RUN_DIR}/criteria" "${RUN_DIR}/checkpoints" "${RUN_DIR}/artifacts"

# Record the working branch only on first setup. On a resume the file already
# exists and the live HEAD may be anything; the original value must be kept.
if [ ! -f "${WORKING_BRANCH_FILE}" ]; then
  git rev-parse --abbrev-ref HEAD > "${WORKING_BRANCH_FILE}"
fi
WORKING_BRANCH="$(cat "${WORKING_BRANCH_FILE}")"

# Create the run branch ONLY if it does not already exist. An existing
# leerie/runs/<run-id> carries the integrated work of completed waves — never reset it.
if git show-ref --verify --quiet "refs/heads/${BRANCH}"; then
  echo "run-branch: ${BRANCH} (existing — preserved, not reset)"
else
  git branch "${BRANCH}" HEAD
  echo "run-branch: ${BRANCH} (created at HEAD)"
fi

# Drop an orphaned staging directory that git no longer knows about. On Fly,
# `machine stop` SIGKILLs the orchestrator, so `_cleanup_on_abnormal_exit`
# never runs and the directory outlives its admin entry on the volume.
# `worktree add` then refuses with "fatal: '<path>' already exists" and every
# --resume dies in phase 4 — the first one only appears to recover because its
# own die() unwinds into the cleanup that removes this directory.
#
# `git worktree prune` does NOT cover this: it only drops admin entries whose
# directory is *gone*. `--force` does not either: it overrides
# branch-already-checked-out and path-assigned-but-missing, not a path that
# simply exists. Removing the directory is the only remedy, and it is safe —
# the run branch (and every integration commit on it) is untouched, so the
# resume re-attaches to the waves already completed.
if ! git worktree list --porcelain | grep -qxF "worktree $STAGING_WT" && [ -d "$STAGING_WT" ]; then
  rm -rf "$STAGING_WT"
fi
# Clear stale admin entries whose directory is gone, so the reuse check below
# sees post-cleanup truth.
git worktree prune

# Add the run-branch worktree if it is not already present. The check must use
# -xF (fixed-string, whole-line): the old `grep -q "worktree .*/${STAGING_WT}$"`
# expanded to a double slash for an absolute $STAGING_WT (`/leerie-state` in the
# container), so it never matched and this add ran unconditionally.
if ! git worktree list --porcelain | grep -qxF "worktree $STAGING_WT"; then
  git worktree add "${STAGING_WT}" "${BRANCH}" >/dev/null
fi

echo "working-branch: ${WORKING_BRANCH}"
echo "staging-worktree: $(cd "${STAGING_WT}" && pwd)"
