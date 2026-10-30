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

# Serialize the prune -> orphan-check -> rm -rf -> add sequence below across
# every worker of every run on this repository. Each step is individually
# check-then-act against git's shared worktree admin metadata, and under
# --max-parallel a dozen siblings run this script concurrently: one worker's
# `git worktree prune` can deregister a sibling's live worktree mid-add
# (leaving its directory orphaned, "already exists" on retry), and the
# orphan-cleanup guard's check-then-act `rm -rf` can delete a directory that
# IS correctly registered under a concurrent `git worktree add`. Measured in
# a harness at ~2.5%/~8% rates respectively and eliminated by a lock. The
# lock is repository-wide (`leerie_repo_lock`), not per run: a per-run lock
# left two concurrent runs, and a run's planning worktree, racing each other.
RUN_DIR="${LEERIE_ROOT}/runs/${RUN_ID}"
mkdir -p "$RUN_DIR"
leerie_repo_lock

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
# Scoped prune: THIS run's registrations only. A bare
# `git worktree prune` is repository-global with no grace period, so
# inside a container sharing the host's .git it destroys host-side
# worktrees whose paths the container cannot see (scripts/worktree-lib.sh;
# docs/POSTMORTEM-2026-08-14.md, F19); a state-root-wide one reaches
# sibling runs' live worktrees.
prune_leerie_worktrees "${RUN_DIR}"

# A run started before the run-id prefix keeps its subtask worktree at the
# unprefixed path, with the branch checked out there; `worktree add` at the
# new path would then fail "already used by worktree" on every resume. Move
# it (uncommitted work included) instead.
LEGACY_WT="$(dirname "$WT")/${ID}"
if [ "$LEGACY_WT" != "$WT" ] && [ -d "$LEGACY_WT" ] && [ ! -e "$WT" ] \
    && grep -qxF "worktree $LEGACY_WT" <<<"$(git worktree list --porcelain)"; then
  git worktree move "$LEGACY_WT" "$WT" >/dev/null 2>&1 || true
fi

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
#
# `git worktree list` is captured BEFORE grep reads it (here and at every
# registration check below). Piped into `grep -q` under `pipefail`, grep exits
# at the first match, git takes SIGPIPE once its output passes the 4 KiB pipe
# buffer, and the pipeline reports "not registered" for a registered
# worktree: 300/300 false negatives for an entry in the first 4 KiB of a
# 9 KiB list; on a live repo, 19 of 35 registered worktrees read as absent
# at least once in 10 checks.
if ! grep -qxF "worktree $WT" <<<"$(git worktree list --porcelain)" \
    && [ -d "$WT" ]; then
  rm -rf "$WT"
fi

# Add with a repair-and-retry.
#
# Everything above is CHECK-THEN-ACT, and the checks run against a repo a
# dozen sibling subtasks are mutating concurrently (`--max-parallel`). Any
# window between a check and the `add` it selected can invalidate it, so the
# add must verify its own outcome rather than trust the earlier check. That
# assumption — not any one race — is the root cause of run 488c42e5 losing
# `bugfix-009-2` after its implementer had committed: the surviving evidence
# there is self-contradictory (the admin entry outlived the failure, yet the
# reuse check missed), which is exactly the kind of detail a correct
# implementation must not depend on.
#
# The repair re-DERIVES state; it never parses git's prose. Modes measured
# against git 2.53:
#   M1  path exists, branch free      -> "fatal: '<path>' already exists"
#                                        repair: remove the orphaned directory
#   M3  registered, directory gone    -> "use 'add -f' … or 'prune' …"
#                                        repair: `git worktree prune`
#   M2  branch checked out elsewhere  -> "already used by worktree at '<other>'"
#                                        NO repair: forcing would steal a live
#                                        sibling's branch. Surface it instead —
#                                        `worktree_setup` is retryable, so the
#                                        subtask retries rather than dying.
# Decide the add form from CURRENT state every time this is called. The
# retry below MUST re-derive it, not reuse the caller's earlier decision:
# `git worktree add <path> -b <branch> <start>` CREATES THE BRANCH and can
# still fail on the path, so a retry of the same `-b` form then dies with
# "a branch named '<branch>' already exists". Measured, not theorised — the
# race test caught exactly this.
_create_worktree() {
  if git show-ref --verify --quiet "refs/heads/${BRANCH}"; then
    # branch exists (pre-existing, or created by a failed first attempt) —
    # attach to it, preserving any commits already on it
    git worktree add "$WT" "$BRANCH"
  else
    git worktree add "$WT" -b "$BRANCH" "$PARENT_BRANCH"
  fi
}

# Create with a repair-and-retry.
#
# Everything above is CHECK-THEN-ACT, and the checks run against a repo a
# dozen sibling subtasks are mutating concurrently (`--max-parallel`). Any
# window between a check and the act it selected can invalidate it, so the
# operation must verify its own outcome rather than trust the earlier check.
# That assumption — not any one race — is the root cause of run 488c42e5
# losing `bugfix-009-2` after its implementer had committed: the surviving
# evidence there is self-contradictory (the admin entry outlived the failure,
# yet the reuse check missed), which is exactly the kind of detail a correct
# implementation must not depend on.
#
# The repair re-DERIVES state; it never parses git's prose. Modes measured
# against git 2.53:
#   M1  path exists, branch free      -> "fatal: '<path>' already exists"
#                                        repair: remove the orphaned directory
#   M3  registered, directory gone    -> "use 'add -f' … or 'prune' …"
#                                        repair: `git worktree prune`
#   M5  branch created by a failed
#       first attempt                 -> "a branch named '<b>' already exists"
#                                        repair: re-derive -> attach form
#   M2  branch checked out elsewhere  -> "already used by worktree at '<other>'"
#                                        NO repair: forcing would steal a live
#                                        sibling's branch. Surface it instead —
#                                        `worktree_setup` is retryable, so the
#                                        subtask retries rather than dying.
_add_with_repair() {
  local err
  if err="$(_create_worktree 2>&1)"; then
    return 0
  fi
  if ! grep -qxF "worktree $WT" <<<"$(git worktree list --porcelain)" \
      && [ -d "$WT" ]; then
    rm -rf "$WT"
  fi
  # Scoped prune: THIS run's registrations only (see above).
  prune_leerie_worktrees "${RUN_DIR}"
  if err="$(_create_worktree 2>&1)"; then
    return 0
  fi
  printf '%s\n' "$err" >&2
  return 1
}

if grep -qxF "worktree $WT" <<<"$(git worktree list --porcelain)"; then
  : # already present — reuse it
else
  _add_with_repair >/dev/null
fi

(cd "$WT" && pwd)
