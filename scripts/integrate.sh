#!/usr/bin/env bash
# integrate.sh <subtask-id> <run-id> — merge a completed subtask branch
# into the run branch.
#
# Run from the repo root. Merges leerie/subtasks/<run-id>/<id> into
# leerie/runs/<run-id> inside the run-branch worktree at
# .leerie/runs/<run-id>/worktrees/staging. Exit 0 on a clean merge;
# non-zero if the merge conflicts, leaving the worktree mid-merge for
# the leerie-integrator to resolve.
set -euo pipefail

# shellcheck source=scripts/worktree-lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/worktree-lib.sh"
# No git command of leerie's own runs a repository hook (DESIGN §6½).
leerie_git_hooks_off

ID="${1:?usage: integrate.sh <subtask-id> <run-id>}"
RUN_ID="${2:?usage: integrate.sh <subtask-id> <run-id>}"
LEERIE_ROOT="${LEERIE_STATE_DIR:-.leerie}"
STAGING="${LEERIE_ROOT}/runs/${RUN_ID}/worktrees/staging"
BRANCH="leerie/subtasks/${RUN_ID}/${ID}"

if [ ! -d "$STAGING" ]; then
  echo "error: run-branch worktree missing — run setup-run.sh ${RUN_ID} first" >&2
  exit 2
fi
if ! leerie_repo_git show-ref --verify --quiet "refs/heads/${BRANCH}"; then
  echo "error: branch ${BRANCH} does not exist" >&2
  exit 2
fi

# Staging is a worktree of its own repository (DESIGN §6 *A repository per
# committing tree (I6)*), which holds only the run branch: leerie fetches the
# subtask branch into it from the run repository right before the merge,
# and takes the run branch back after it. A staging of the run repository
# itself (a run made before tree repositories) has both already.
RUN_REPO="$(leerie_repo_git rev-parse --path-format=absolute --git-common-dir)"
STAGING_REPO="$(git -C "$STAGING" rev-parse --path-format=absolute --git-common-dir)"
if [ "$STAGING_REPO" != "$RUN_REPO" ]; then
  git -C "$STAGING" fetch --quiet --no-tags --no-write-fetch-head \
    --no-auto-maintenance "$RUN_REPO" "+refs/heads/${BRANCH}:refs/heads/${BRANCH}"
fi
_take_staging() {
  if [ "$STAGING_REPO" != "$RUN_REPO" ]; then
    leerie_take_tree_branch "$STAGING_REPO" "leerie/runs/${RUN_ID}"
  fi
}

if git -C "$STAGING" merge --no-ff -m "leerie: integrate ${ID}" "$BRANCH"; then
  _take_staging
  echo "integrated: ${ID}"
  exit 0
else
  echo "conflict: ${ID} — run-branch worktree left mid-merge for the integrator" >&2
  exit 1
fi
