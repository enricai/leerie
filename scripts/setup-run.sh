#!/usr/bin/env bash
# setup-run.sh <run-id> [<working-branch>] — initialize the per-run branch
# and worktree.
#
# Each leerie invocation has a unique run_id; this script sets up the
# directory and branch for that run. Records the working branch, creates
# `leerie/runs/<run-id>` off its tip, and adds a worktree at
# `<state-root>/runs/<run-id>/worktrees/staging`.
#
# <working-branch> is the branch the run started on (the orchestrator's
# `working_branch`). When given and it names a local branch, the run follows
# THAT branch's tip rather than whatever is checked out now: the checkout
# sentinel no longer halts on a mid-run change (DESIGN §12 L4), so an
# operator who checks out another branch while planning runs must not
# silently re-base this run onto it. A pull of the same branch is still
# followed. Without the argument (detached start, older callers such as
# collect-subtrees.sh) the live HEAD is used, as before.
#
# State root: $LEERIE_STATE_DIR when set (centralized, outside the repo);
# falls back to `.leerie` (repo-relative; used by tests and direct invocations).
#
# GENUINELY idempotent: if `leerie/runs/<run-id>` already exists (a run
# is in progress, or this is a resume), the branch is LEFT WHERE IT IS.
# It is never force-reset — doing so would discard every integration
# commit from the waves already completed.
#
# Branch shape: the `runs/` segment is mandatory. Subtask branches live
# under `leerie/subtasks/<run-id>/<sid>` (a sibling namespace) so the
# run-branch and its subtask branches can never be parent/child refs in
# git's loose ref store. See DESIGN.md §3 ("The run branch as an
# integration buffer") for the loose-ref-store collision being avoided.
set -euo pipefail

# shellcheck source=scripts/worktree-lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/worktree-lib.sh"
# No git command of leerie's own runs a repository hook (DESIGN §6½).
leerie_git_hooks_off

RUN_ID="${1:?usage: setup-run.sh <run-id> [<working-branch>]}"
REQUESTED_BRANCH="${2:-}"
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

if ! leerie_repo_git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "error: not inside a git repository" >&2
  exit 1
fi

# A bare branch named 'leerie' blocks creation of leerie/runs/* and
# leerie/subtasks/* (git's loose ref store cannot hold a file and a
# directory at the same path). Defense-in-depth: preflight() checks
# this too, but resume skips preflight.
if leerie_repo_git show-ref --verify --quiet refs/heads/leerie; then
  echo "error: branch 'leerie' conflicts with leerie's internal namespace (leerie/runs/*, leerie/subtasks/*)." >&2
  echo "Rename it: git branch -m leerie leerie-old" >&2
  exit 1
fi

mkdir -p "${RUN_DIR}/worktrees" "${RUN_DIR}/subtasks" "${RUN_DIR}/notes" "${RUN_DIR}/artifacts"

# Record the working branch only on first setup. On a resume the file already
# exists and the live HEAD may be anything; the original value must be kept.
# A requested branch is recorded even when it no longer resolves, so this
# file keeps agreeing with the orchestrator's working_branch (the diff base
# and PR base): a deleted branch then fails loudly at finalize rather than
# silently diffing against whatever is checked out.
# `git rev-parse --abbrev-ref HEAD` prints `heads/<name>` when a tag shares
# the branch's name (and `refs/heads/<name>` when even that is ambiguous),
# so those forms are tried after the name itself — the same lookup as the
# orchestrator's `_working_branch_ref`.
BASE_REF="HEAD"
if [ -n "$REQUESTED_BRANCH" ] && [ "$REQUESTED_BRANCH" != "HEAD" ]; then
  if leerie_repo_git show-ref --verify --quiet "refs/heads/${REQUESTED_BRANCH}"; then
    BASE_REF="refs/heads/${REQUESTED_BRANCH}"
  elif [ "${REQUESTED_BRANCH#heads/}" != "$REQUESTED_BRANCH" ] \
     && leerie_repo_git show-ref --verify --quiet "refs/heads/${REQUESTED_BRANCH#heads/}"; then
    BASE_REF="refs/heads/${REQUESTED_BRANCH#heads/}"
  elif [ "${REQUESTED_BRANCH#refs/heads/}" != "$REQUESTED_BRANCH" ] \
     && leerie_repo_git show-ref --verify --quiet "$REQUESTED_BRANCH"; then
    BASE_REF="$REQUESTED_BRANCH"
  else
    echo "warning: working branch '${REQUESTED_BRANCH}' not found; using HEAD" >&2
  fi
fi
if [ ! -f "${WORKING_BRANCH_FILE}" ]; then
  if [ -n "$REQUESTED_BRANCH" ] && [ "$REQUESTED_BRANCH" != "HEAD" ]; then
    printf '%s\n' "$REQUESTED_BRANCH" > "${WORKING_BRANCH_FILE}"
  else
    leerie_repo_git rev-parse --abbrev-ref HEAD > "${WORKING_BRANCH_FILE}"
  fi
fi
WORKING_BRANCH="$(cat "${WORKING_BRANCH_FILE}")"

# Create the run branch ONLY if it does not already exist. An existing
# leerie/runs/<run-id> carries the integrated work of completed waves — never reset it.
if leerie_repo_git show-ref --verify --quiet "refs/heads/${BRANCH}"; then
  echo "run-branch: ${BRANCH} (existing — preserved, not reset)"
else
  leerie_repo_git branch "${BRANCH}" "${BASE_REF}"
  echo "run-branch: ${BRANCH} (created at ${BASE_REF})"
fi

# Staging is a worktree of its own repository, `<run-dir>/tree-repos/staging`
# (DESIGN §6 *A repository per committing tree (I6)*): the integrator and the
# final conformer commit there, and leerie takes its branch — the run
# branch — back into the run repository. `leerie_tree` takes a surviving
# staging's branch (a SIGKILL may have left a worker's commits untaken) or
# makes staging afresh from the run repository's run branch, and an orphaned
# directory or a stale tree repository goes with the remake. Under the
# repository-wide worktree lock (`leerie_repo_lock`), like every other script
# that adds a worktree; the run repository's own registry is pruned for this
# run only (never a bare `git worktree prune`: POSTMORTEM-2026-08-14 F19).
leerie_repo_lock
prune_leerie_worktrees "${RUN_DIR}"
TREE_REPOS="$(cd "$RUN_DIR" && pwd -P)/tree-repos"
# A tree that survived its process: present, and a worktree of its own
# repository (the test `leerie_tree` reuses by).
_survived() {
  [ -f "$1/HEAD" ] && [ -d "$2" ] \
    && [ "$(git -C "$2" rev-parse --path-format=absolute --git-common-dir 2>/dev/null)" = "$1" ]
}
staging_survived=false
_survived "${TREE_REPOS}/staging" "$STAGING_WT" && staging_survived=true
leerie_tree "${TREE_REPOS}/staging" "${BRANCH}" "${STAGING_WT}" >/dev/null
if [ "$staging_survived" = true ]; then
  # A staging that survived its process holds whatever the killed worker
  # left — partial edits, a merge half resolved, a HEAD a worker detached.
  # Every process starts on staging checked out on the run branch at its
  # tip, with nothing uncommitted (DESIGN §8 *The final diff describes the
  # tree that ships*): the one restore primitive, ignored files (installed
  # dependencies) kept.
  leerie_reset_worktree "${STAGING_WT}" "${BRANCH}"
fi

# The same for every subtask tree of this run that survived its process
# (new-worktree.sh reuses a surviving tree as it is): its branch taken, then
# checked out on that branch at its tip, commits kept, ignored files kept.
# Found by its tree repository's name (`leerie_worktree_name`), never by the
# branch git reports in the tree: a worktree a worker detached reports none
# (review round 15 H2).
WT_DIR="$(dirname "$STAGING_WT")"
WT_PREFIX="$(leerie_worktree_name "$RUN_ID" x)"
WT_PREFIX="${WT_PREFIX%x}"
for tr in "${TREE_REPOS}"/*; do
  [ -d "$tr" ] || continue
  name="$(basename "$tr")"
  [ "$name" != staging ] || continue
  subtask_wt="${WT_DIR}/${name}"
  _survived "$tr" "$subtask_wt" || continue
  sid="${name#"$WT_PREFIX"}"
  sub_branch="leerie/subtasks/${RUN_ID}/${sid}"
  leerie_take_tree_branch "$tr" "$sub_branch"
  leerie_reset_worktree "$subtask_wt" "$sub_branch"
done

echo "working-branch: ${WORKING_BRANCH}"
echo "staging-worktree: $(cd "${STAGING_WT}" && pwd)"
