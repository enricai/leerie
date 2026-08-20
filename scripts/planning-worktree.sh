#!/usr/bin/env bash
# planning-worktree.sh <run-id> — create/reset the disposable worktree that
# every judgment worker runs in (DESIGN §12 *Judgment-worker isolation*).
#
# Judgment workers (classifier, planner, reconciler, the judges, the
# satisfied-probe) used to run with cwd = the user's real checkout. Combined
# with `--dangerously-skip-permissions` that left nothing between a worker and
# the operator's branch: measured live, a classifier implemented an entire task
# in the user's checkout on `main`. Removing the flag from those workers
# (orchestrator/leerie.py, `claude_p`) restores the CLI's working-directory
# boundary; this script is what makes that boundary land somewhere harmless.
#
# DETACHED, never a branch. Three reapers glob leerie's ref namespaces —
# cleanup.sh, _cleanup_on_abnormal_exit's branch_globs, and `leerie prune` —
# and all three know only `leerie/runs/<id>` and `leerie/subtasks/<id>/`. A
# fourth namespace would leak forever, which is the stale-branch problem
# `prune` exists to fix. Nothing downstream reads these commits: planning
# output is JSON in state.json, and setup-run.sh cuts the run branch from the
# real checkout's HEAD, so a commit made here is unreachable by construction.
#
# PER-RUN, under <run-dir>/worktrees/, so it inherits the cleanup that already
# sweeps that directory (cleanup.sh's clean_one_run, and
# _cleanup_on_abnormal_exit) with no new reaping code, and so two concurrent
# runs against one repo cannot reset each other's tree mid-phase.
#
# RESET ON RE-ENTRY, not merely reused. Two distinct reasons, both real:
#   1. The satisfied-probe judges "is this already done?" by inspecting its own
#      cwd — no diff is passed to it. A tree an earlier judgment worker wrote
#      into would make it answer yes and silently drop real work.
#   2. On resume the real checkout's HEAD may have moved (a sibling run merged
#      its PR). A worktree pinned at the old sha would plan against a tree that
#      no longer exists.
# `clean -fd` and NOT `-fdx`: gitignored content (node_modules, .venv) must
# survive, or every resume re-pays the install. That is deliberate — do not
# "fix" it to -fdx.
set -euo pipefail

# shellcheck source=scripts/worktree-lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/worktree-lib.sh"

RUN_ID="${1:?usage: planning-worktree.sh <run-id>}"
LEERIE_ROOT="${LEERIE_STATE_DIR:-.leerie}"
RUN_DIR="${LEERIE_ROOT}/runs/${RUN_ID}"
WT="${RUN_DIR}/worktrees/planning"

# `git worktree list --porcelain` prints absolute, symlink-resolved paths, so
# $WT must be in that shape before the reuse grep. Same normalization as
# setup-run.sh — when LEERIE_STATE_DIR is unset (the Fly/EC2 runtimes, and
# direct python callers) $WT is repo-relative.
case "$WT" in
  /*) ;;
  *)  WT="$(pwd -P)/$WT" ;;
esac

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "error: not inside a git repository" >&2
  exit 1
fi

# The tree to mirror: the real checkout's current HEAD. Read here, in the main
# checkout, rather than inside the worktree — a detached worktree reports its
# own (possibly stale) HEAD, which is the value this reset exists to correct.
TARGET_SHA="$(git rev-parse HEAD)"

mkdir -p "${RUN_DIR}/worktrees"

# Drop an orphaned directory git no longer knows about. Same failure mode
# setup-run.sh documents: a SIGKILLed container leaves the directory without
# its admin entry, and `worktree add` then refuses with "already exists" on
# every subsequent resume. Neither `prune` nor `--force` covers it.
if ! git worktree list --porcelain | grep -qxF "worktree $WT" && [ -d "$WT" ]; then
  rm -rf "$WT"
fi

# Scoped prune (never a bare `git worktree prune`): the repo's .git is shared
# with the host and other containers, and a repository-global prune destroys
# registrations whose paths this namespace cannot see.
prune_leerie_worktrees "${LEERIE_ROOT}"

if git worktree list --porcelain | grep -qxF "worktree $WT"; then
  # Reuse: force the tree back to the current HEAD and drop anything a
  # previous judgment worker left behind. --force because the checkout is
  # detached and may carry commits; that is expected and discarded.
  git -C "$WT" reset --hard "$TARGET_SHA" >/dev/null
  git -C "$WT" clean -fd >/dev/null
else
  git worktree add --detach "$WT" "$TARGET_SHA" >/dev/null
fi

echo "planning-worktree: $(cd "$WT" && pwd)"
