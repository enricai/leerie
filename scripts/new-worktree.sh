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

ID="${1:?usage: new-worktree.sh <subtask-id> <run-id>}"
RUN_ID="${2:?usage: new-worktree.sh <subtask-id> <run-id>}"
LEERIE_ROOT="${LEERIE_STATE_DIR:-.leerie}"
WT="${LEERIE_ROOT}/runs/${RUN_ID}/worktrees/${ID}"
# git worktree list --porcelain outputs absolute, symlink-resolved paths.
# When LEERIE_STATE_DIR is unset (Fly runtime), $WT is relative and the
# reuse grep below never matches, crashing continuation retries with
# "fatal: '...' already exists". pwd -P resolves symlinks to match git.
case "$WT" in
  /*) ;;
  *)  WT="$(pwd -P)/$WT" ;;
esac
BRANCH="leerie/subtasks/${RUN_ID}/${ID}"
PARENT_BRANCH="leerie/runs/${RUN_ID}"

# Clear stale admin entries whose directory is gone, so the orphan check and
# the `worktree add` fallbacks below all see post-prune truth.
#
# ORDER IS LOAD-BEARING: this MUST run before the orphan-directory check.
# `git worktree prune` is itself an operation that CREATES the orphaned state
# that check exists to repair — it drops the admin entry and leaves the
# directory on disk. Running the check first and pruning after left a window
# where the entry vanished with nothing left to notice: the reuse grep below
# missed, and `worktree add` hit the directory prune had just orphaned.
#
# That is not hypothetical. Run 488c42e5 (2026-08-05) lost `bugfix-009-2`
# this way *after* its implementer had already committed: a mechanical check
# drove the continuation path, this script ran a second time, and it died on
# "fatal: '<path>' already exists" — the wave then refused to close with 25 of
# 26 subtasks complete. Under `--max-parallel` a *sibling's* concurrent prune
# can drop a live registration the same way, which is the likelier trigger.
git worktree prune

# Drop an orphaned worktree directory that git no longer knows about.
# `_cleanup_on_abnormal_exit` deregisters and removes worktrees, but a
# partial cleanup (or an rmtree that lost a race with git's metadata write),
# or the prune immediately above, can leave the directory on disk with no
# registration. `worktree add` then refuses with "fatal: '<path>' already
# exists", which crashes continuation retries — the same class the `pwd -P`
# normalisation above addresses, but reached from cleanup rather than from a
# relative path.
#
# `git worktree prune` does NOT cover this: it only drops admin entries whose
# directory is *gone*. `--force` does not either: it overrides
# branch-already-checked-out and path-assigned-but-missing, not a path that
# simply exists. Removing the directory is the only remedy, and it is safe —
# the branch (and every commit on it) is untouched, so the retry re-attaches
# to the work already done.
if ! git worktree list --porcelain | grep -qxF "worktree $WT" && [ -d "$WT" ]; then
  rm -rf "$WT"
fi

if git worktree list --porcelain | grep -qxF "worktree $WT"; then
  : # already present — reuse it
elif git show-ref --verify --quiet "refs/heads/${BRANCH}"; then
  # branch exists but worktree was removed — re-attach
  git worktree add "$WT" "$BRANCH" >/dev/null
else
  # fresh subtask — branch off the current run-branch tip
  git worktree add "$WT" -b "$BRANCH" "$PARENT_BRANCH" >/dev/null
fi

(cd "$WT" && pwd)
