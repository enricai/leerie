#!/usr/bin/env bash
# worktree-lib.sh — scoped replacement for `git worktree prune`.
#
# Sourced by the container-side scripts that need to drop leerie's own stale
# worktree registrations: new-worktree.sh, setup-run.sh, cleanup.sh.
#
# WHY THIS EXISTS
#
# `git worktree prune` is repository-global and has no grace period. The
# 3-month `gc.worktreePruneExpire` default applies to `git gc`, which invokes
# `git worktree prune --expire 3.months.ago`; a bare `git worktree prune`
# drops EVERY registration whose directory is missing, immediately.
#
# leerie runs these scripts inside a container that bind-mounts the user's
# repository whole, so every container shares the host's `.git`. A worktree
# registered by the HOST at a path that does not exist inside the container's
# mount namespace therefore looks stale to a bare prune, and is destroyed.
#
# That is not hypothetical. host-finalize.sh creates its rebase worktree at
# `/tmp/tmp.XXXX/rebase-<run-id>` on the host — a path no container can see.
# During one run's rebase window a SIBLING run spawned three workers, each
# invoking new-worktree.sh, each running a bare prune; the rebaser then
# reported that its git metadata directory "has vanished … without any
# destructive action on my part". See docs/POSTMORTEM-2026-08-14.md, F19.
#
# The scoped version below prunes only registrations under leerie's own state
# root, which is exactly the set these scripts are entitled to clean up.

# prune_leerie_worktrees <leerie-root>
#
# Drop stale worktree admin entries whose path lies under <leerie-root>.
# A registration is stale when its directory no longer exists. Entries
# outside <leerie-root> — the host's rebase worktree, a developer's own
# worktree of the same repo — are left strictly alone.
#
# Never fails the caller: every git invocation is guarded, because all three
# call sites run under `set -e` and a prune is housekeeping, not a gate.
prune_leerie_worktrees() {
  local root="${1:?usage: prune_leerie_worktrees <leerie-root>}"
  local git_dir abs_root name gitdir_file wt
  git_dir="$(git rev-parse --git-common-dir 2>/dev/null)" || return 0
  [ -n "$git_dir" ] || return 0
  # Absolute and symlink-resolved, so the prefix test below compares like
  # with like.
  abs_root="$(cd "$root" 2>/dev/null && pwd -P)" || return 0

  # WHICH entries are stale is git's determination, not ours. `prune -n -v`
  # reports exactly what a real prune would remove, one `Removing
  # worktrees/<name>: <reason>` line each. Reimplementing the predicate is a
  # trap: "the directory is missing" is only ONE of git's staleness cases —
  # an entry whose gitdir link file has been deleted is prunable while its
  # directory is still fully populated on disk, which is precisely the shape
  # `tests/test_new_worktree_idempotency.py` constructs. Asking git keeps this
  # helper correct as git's own rules evolve; all we add is the scoping.
  while IFS= read -r line; do
    case "$line" in
      "Removing worktrees/"*) ;;
      *) continue ;;
    esac
    name="${line#Removing worktrees/}"
    name="${name%%:*}"
    [ -n "$name" ] || continue

    # Attribute the entry to a path before touching it. `gitdir` holds the
    # absolute path of the worktree's own `.git` link file.
    gitdir_file="$git_dir/worktrees/$name/gitdir"
    [ -f "$gitdir_file" ] || continue
    wt="$(cat "$gitdir_file" 2>/dev/null)" || continue
    wt="${wt%/.git}"
    [ -n "$wt" ] || continue

    # Only ours. An entry we cannot place under our own root is left strictly
    # alone — including one we simply failed to attribute, since deleting a
    # registration we cannot identify is exactly the accident this exists to
    # prevent.
    case "$wt" in
      "$abs_root"/*) ;;
      *) continue ;;
    esac
    [ -d "$git_dir/worktrees/$name" ] || continue
    rm -rf "$git_dir/worktrees/$name" 2>/dev/null || true
  # `2>&1`, not `2>/dev/null`: `git worktree prune -n -v` reports on STDERR,
  # so discarding stderr reads an empty list and silently prunes nothing --
  # a no-op that looks exactly like "there was nothing stale". Verified
  # against git 2.53: stdout is empty and stderr carries every line.
  # LC_ALL=C / LANGUAGE=: git wraps this line in gettext, and only the
  # FORMAT string is translated -- so under any non-English locale the
  # `Removing worktrees/` case above never matches and this becomes a
  # total silent no-op.
  done < <(LC_ALL=C LANGUAGE='' git worktree prune -n -v 2>&1 || true)
  return 0
}
