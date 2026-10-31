#!/usr/bin/env bash
# new-worktree.sh <subtask-id> <run-id> — create (or reuse) an isolated
# worktree for a subtask.
#
# The worktree branches off the CURRENT leerie/runs/<run-id> tip, so a
# subtask sees the integrated results of all prior waves. Idempotent: if
# the worktree or branch already exists (e.g. resuming after a handoff),
# it is reused. Prints the absolute worktree path on stdout.
#
# Branch shape: subtask branches live under `leerie/subtasks/<run-id>/<sid>`,
# disjoint from the run-branch namespace `leerie/runs/<run-id>`. The two
# sub-namespaces must stay disjoint so neither is an ancestor ref of the
# other in git's loose ref store (see DESIGN.md §3).
set -euo pipefail

# shellcheck source=scripts/worktree-lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/worktree-lib.sh"
# No git command of leerie's own runs a repository hook (DESIGN §6½).
leerie_git_hooks_off

ID="${1:?usage: new-worktree.sh <subtask-id> <run-id>}"
RUN_ID="${2:?usage: new-worktree.sh <subtask-id> <run-id>}"
LEERIE_ROOT="${LEERIE_STATE_DIR:-.leerie}"
WT="${LEERIE_ROOT}/runs/${RUN_ID}/worktrees/$(leerie_worktree_name "$RUN_ID" "$ID")"
# git worktree list --porcelain outputs absolute, symlink-resolved paths,
# and `leerie_tree` compares $WT against it: when LEERIE_STATE_DIR is unset
# (Fly runtime) $WT is relative, so it is made absolute here.
case "$WT" in
  /*) ;;
  *)  WT="$(pwd -P)/$WT" ;;
esac
BRANCH="leerie/subtasks/${RUN_ID}/${ID}"
PARENT_BRANCH="leerie/runs/${RUN_ID}"

# Serialize this script's changes to the run repository (the scoped prune of
# its worktree registry, the branch cut) and the tree it makes, across the
# siblings that run it concurrently under --max-parallel. The lock is in the
# run repository (`leerie_repo_lock`), which is this run's alone (DESIGN §6
# *The run's own repository*), so it is per run.
RUN_DIR="${LEERIE_ROOT}/runs/${RUN_ID}"
mkdir -p "$RUN_DIR"
leerie_repo_lock

# The tree is a worktree of its own repository (DESIGN §6 *A repository per
# committing tree (I6)*): a worker of it writes only that repository, and
# leerie takes its branch back into the run repository. Its registration is
# private to that repository, so siblings never race on it; the run
# repository's own registry is pruned for this run (scoped, never a bare
# `git worktree prune`: POSTMORTEM-2026-08-14 F19).
prune_leerie_worktrees "${RUN_DIR}"

# The branch is leerie's, in the run repository: cut from the run branch's
# current tip when absent, kept (with every commit on it) when present.
if ! leerie_repo_git show-ref --verify --quiet "refs/heads/${BRANCH}"; then
  leerie_repo_git branch "$BRANCH" "$PARENT_BRANCH"
fi
RUN_DIR_ABS="$(cd "$RUN_DIR" && pwd -P)"
leerie_tree "${RUN_DIR_ABS}/tree-repos/$(basename "$WT")" "$BRANCH" "$WT" >/dev/null

(cd "$WT" && pwd)
