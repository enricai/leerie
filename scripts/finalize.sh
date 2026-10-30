#!/usr/bin/env bash
# finalize.sh <run-id> [<base>] — verify the run branch is ready to push.
#
# Run from the repo root after every wave is integrated and the run branch
# is green. The working branch is NOT modified locally — leerie does not
# merge into it. The PR opened by `push_and_open_pr()` (in leerie.py) is
# the proposed integration into the working branch.
#
# This script's job is narrow: confirm `leerie/runs/<run-id>` exists and
# has at least one commit beyond the working branch. If it doesn't, the
# orchestrator dies before attempting the push.
#
# `<base>`, when given, is the commit the run branch was cut from
# (`base_sha`): the orchestrator passes it, and the count is taken from it
# rather than from the working branch's name, which in the run's own
# repository is a ref a worker can move (DESIGN §6 *The run's own
# repository*).
set -euo pipefail

# shellcheck source=scripts/worktree-lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/worktree-lib.sh"
# No git command of leerie's own runs a repository hook (DESIGN §6½).
leerie_git_hooks_off

RUN_ID="${1:?usage: finalize.sh <run-id> [<base>]}"
BASE="${2:-}"
# Honor LEERIE_STATE_DIR — matches setup-run.sh:25. Without this,
# in-container invocations (LEERIE_STATE_DIR=/leerie-state) look at
# /work/.leerie/runs/<id>/working-branch (does not exist) instead of
# /leerie-state/runs/<id>/working-branch (where setup-run.sh wrote it).
LEERIE_ROOT="${LEERIE_STATE_DIR:-.leerie}"
RUN_DIR="${LEERIE_ROOT}/runs/${RUN_ID}"
BRANCH="leerie/runs/${RUN_ID}"
WORKING_BRANCH_FILE="${RUN_DIR}/working-branch"

if [ ! -f "${WORKING_BRANCH_FILE}" ]; then
  echo "error: ${WORKING_BRANCH_FILE} missing — run setup-run.sh ${RUN_ID} first" >&2
  exit 2
fi
WORKING_BRANCH="$(cat "${WORKING_BRANCH_FILE}")"

if ! leerie_repo_git show-ref --verify --quiet "refs/heads/${BRANCH}"; then
  echo "error: ${BRANCH} does not exist — nothing to finalize" >&2
  exit 2
fi

# The PR base must exist locally for the rev-list comparison below; if the
# user renamed or deleted it after the run started, surface that distinctly
# rather than letting it look like an "already caught up" state.
if [ -n "$BASE" ]; then
  if ! leerie_repo_git rev-parse --verify --quiet "${BASE}^{commit}" >/dev/null; then
    echo "error: base ${BASE} is not a commit in this repository" >&2
    exit 2
  fi
  WORKING_BRANCH="$BASE"
elif ! leerie_repo_git show-ref --verify --quiet "refs/heads/${WORKING_BRANCH}"; then
  echo "error: working branch ${WORKING_BRANCH} (recorded at run start) no longer exists — " >&2
  echo "       recreate it (e.g. \`git branch ${WORKING_BRANCH} <commit>\`) before retrying" >&2
  exit 2
fi

# Confirm the run branch has work the working branch doesn't already have.
# `git rev-list --count <working>..<run>` counts commits reachable from the
# run branch but not from the working branch. Zero means the run produced
# no work — likely a misconfiguration the user should see.
AHEAD="$(leerie_repo_git rev-list --count "${WORKING_BRANCH}..${BRANCH}")"
if [ "${AHEAD}" = "0" ]; then
  echo "error: ${BRANCH} has no commits beyond ${WORKING_BRANCH} — nothing to push" >&2
  exit 1
fi

echo "finalized: ${BRANCH} ready to push (${AHEAD} commit(s) ahead of ${WORKING_BRANCH})"
exit 0
