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
# The scoped version below prunes only registrations under a root the caller
# names. Every in-container caller names ITS OWN RUN DIRECTORY, not the state
# root: a sibling run's registrations are no more this run's to reap than the
# host's, and a state-root-wide prune racing a sibling's `worktree add` (an
# admin entry written, the worktree's `.git` link not yet) deleted live
# registrations — 4 run deaths across 4 chains (DESIGN §6 *Never a
# repository-global git operation*).
#
# Callers serialise the whole prune -> orphan check -> add sequence with
# `leerie_repo_lock` (below), one lock per repository, shared by every run and
# container using this `.git`; and the prune re-checks each entry immediately
# before deleting it.

# leerie_repo_git <git args...>
#
# A script's repository-level git (refs, branches, the worktree registry):
# the repository named explicitly as LEERIE_GIT_DIR when the caller gives
# it — the run's own bare repository (DESIGN §6 *The run's own
# repository*), which git refuses to find from a working directory under
# safe.bareRepository=explicit — else the repository at the cwd, as for a
# script run by hand. Worktree-level git (`cd "$wt"`, `git -C "$wt"`) never
# goes through it: a linked worktree is found through its `.git` file.
leerie_repo_git() {
  if [ -n "${LEERIE_GIT_DIR:-}" ]; then
    git --git-dir="$LEERIE_GIT_DIR" "$@"
  else
    git "$@"
  fi
}

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
  git_dir="$(leerie_repo_git rev-parse --git-common-dir 2>/dev/null)" || return 0
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
    # Re-check right before deleting: only an entry whose worktree `.git`
    # link is really gone is stale. git's verdict above was read before this
    # loop reached the entry.
    [ -e "$wt/.git" ] && continue
    rm -rf "$git_dir/worktrees/$name" 2>/dev/null || true
  # `2>&1`, not `2>/dev/null`: `git worktree prune -n -v` reports on STDERR,
  # so discarding stderr reads an empty list and silently prunes nothing --
  # a no-op that looks exactly like "there was nothing stale". Verified
  # against git 2.53: stdout is empty and stderr carries every line.
  # LC_ALL=C / LANGUAGE=: git wraps this line in gettext, and only the
  # FORMAT string is translated -- so under any non-English locale the
  # `Removing worktrees/` case above never matches and this becomes a
  # total silent no-op.
  done < <(LC_ALL=C LANGUAGE='' leerie_repo_git worktree prune -n -v 2>&1 || true)
  return 0
}

# leerie_repo_lock
#
# Take the repository-wide worktree lock on fd 201 for the rest of the calling
# script: `<git-common-dir>/leerie-worktree.lock`, shared by every run and
# every container that bind-mounts this `.git`. The per-run lock it replaced
# serialised siblings of one run but not two runs (or a run and its own
# planning worktree) against each other. The file sits where git already
# writes its own worktree admin entries, so it is writable wherever a
# `worktree add` is, on every runtime. Measured with the run-id-prefixed
# names, run-scoped prune and re-check (6 concurrent runs x 200 iterations,
# once with no other worktrees and once with 40 live ones): 0 failed
# iterations of 2400.
leerie_repo_lock() {
  local common
  common="$(leerie_repo_git rev-parse --git-common-dir)"
  exec 201>"$common/leerie-worktree.lock"
  flock 201
}

# leerie_worktree_name <run-id> <name>
#
# The directory name of a run's subtask or planning worktree: the run id's
# first 12 characters, a dash, then the name. git names a worktree's admin
# entry (`.git/worktrees/<basename>`) after the directory, so without the
# prefix every run's `bugfix-001` competed for one admin name in the shared
# `.git`. Mirrored by `_worktree_dirname` in orchestrator/leerie.py.
leerie_worktree_name() {
  local run_id="${1:?usage: leerie_worktree_name <run-id> <name>}"
  local name="${2:?usage: leerie_worktree_name <run-id> <name>}"
  printf '%s-%s' "${run_id:0:12}" "$name"
}

# leerie_git_hooks_off
#
# No git command of leerie's own runs a repository hook (DESIGN §6½ *Hooks
# off*): even `git status` fires post-index-change, so it is not a
# per-command flag. Appends `core.hooksPath=/dev/null` to the environment's
# git config entries (GIT_CONFIG_COUNT/KEY/VALUE), once, keeping any already
# set — the same entry `_git_hooks_off` puts in the orchestrator's
# environment, so a script run outside it (the subtree collection over
# `flyctl ssh`) holds the rule too.
leerie_git_hooks_off() {
  [ -n "${LEERIE_GIT_HOOKS_OFF:-}" ] && return 0
  local n="${GIT_CONFIG_COUNT:-0}"
  export "GIT_CONFIG_KEY_${n}=core.hooksPath"
  export "GIT_CONFIG_VALUE_${n}=/dev/null"
  export GIT_CONFIG_COUNT=$((n + 1))
  export LEERIE_GIT_HOOKS_OFF="$n"
}

# leerie_run_settings <worktree>
#
# Prints `<run-dir>/repo-settings` — leerie's copy of the settings git reads
# to report a tree of the run (DESIGN §8 *The settings are leerie's*) — when
# <worktree> belongs to a run's own repository (its common directory is a
# bare `<run-dir>/repo`); prints nothing otherwise. `_run_settings_of` in
# Python.
leerie_run_settings() {
  local common run_dir
  common="$(git -C "$1" rev-parse --path-format=absolute --git-common-dir 2>/dev/null)" || return 0
  [ -n "$common" ] || return 0
  if [ "${common##*/}" = repo ]; then
    run_dir="${common%/*}"
  else
    # A tree of a tree repository, `<run-dir>/tree-repos/<tree>` (DESIGN §6
    # *A repository per committing tree*): read with the run's settings.
    run_dir="${common%/*}"
    [ "${run_dir##*/}" = tree-repos ] || return 0
    run_dir="${run_dir%/*}"
  fi
  # Named explicitly (`safe.bareRepository=explicit` refuses discovery),
  # never through `core.bare`, a setting a worker can change: the same
  # test as `_run_repo_of`.
  git --git-dir="$run_dir/repo" rev-parse --git-dir >/dev/null 2>&1 || return 0
  printf '%s\n' "$run_dir/repo-settings"
}

# leerie_vgit <worktree> <git args...>
#
# git in <worktree> through the run's settings: outside the run repository,
# leerie's copies (system and global config, attributes, the global ignore
# file); `core.fsmonitor` and `core.untrackedCache` off. A tree of no run
# repository keeps its own settings, the two optimisations off. `_git_view`
# in Python.
leerie_vgit() {
  local wt="$1" s
  shift
  s="$(leerie_run_settings "$wt")"
  if [ -n "$s" ]; then
    GIT_CONFIG_SYSTEM=/dev/null GIT_CONFIG_GLOBAL="$s/host.config" \
      GIT_ATTR_NOSYSTEM=1 git -C "$wt" \
      -c core.attributesFile="$s/host.attributes" \
      -c core.excludesFile="$s/host.exclude" \
      -c core.fsmonitor=false -c core.untrackedCache=false "$@"
  else
    git -C "$wt" -c core.fsmonitor=false -c core.untrackedCache=false "$@"
  fi
}

# leerie_untracked <worktree> [--directory]
#
# THE listing of untracked, not-ignored files (DESIGN §8 *One ignore rule*),
# NUL-separated: git's standard ignore rules, read through the run's
# settings. `_untracked_cmd` in Python; no other `ls-files --others` exists
# in leerie's scripts (`tests/test_one_ignore_rule.py`).
leerie_untracked() {
  local wt="$1"
  if [ "${2-}" = --directory ]; then
    leerie_vgit "$wt" --literal-pathspecs ls-files -z --others --exclude-standard --directory --no-empty-directory
  else
    leerie_vgit "$wt" --literal-pathspecs ls-files -z --others --exclude-standard
  fi
}

# leerie_settings_dirt <worktree>
#
# I1's settings lines: one per run-repository settings path (`config`,
# `info`) that differs from leerie's copy, or one when the copy is missing.
# `_settings_dirt` in Python, line for line.
leerie_settings_dirt() {
  local s common n
  s="$(leerie_run_settings "$1")"
  [ -n "$s" ] || return 0
  common="${s%/repo-settings}/repo"
  if [ ! -f "$s/config" ]; then
    printf "git settings unknown: leerie's copy %s of the run repository's settings is missing\n" "$s"
    return 0
  fi
  for n in config info; do
    if [ -e "$s/$n" ] || [ -L "$s/$n" ] || [ -e "$common/$n" ] || [ -L "$common/$n" ]; then
      diff -r --no-dereference -q -- "$s/$n" "$common/$n" >/dev/null 2>&1 \
        || printf "git settings changed: %s differs from leerie's copy %s\n" "$common/$n" "$s/$n"
    fi
  done
}

# leerie_restore_settings <worktree>
#
# Copies leerie's settings back over the run repository's `config` and
# `info/` where they differ. `_restore_settings` in Python.
leerie_restore_settings() {
  local s common
  s="$(leerie_run_settings "$1")"
  [ -n "$s" ] && [ -f "$s/config" ] || return 0
  common="${s%/repo-settings}/repo"
  if ! diff -q -- "$s/config" "$common/config" >/dev/null 2>&1; then
    cp -- "$s/config" "$common/.config.leerie-$$" \
      && mv -f -- "$common/.config.leerie-$$" "$common/config" || return 1
  fi
  if ! diff -r --no-dereference -q -- "$s/info" "$common/info" >/dev/null 2>&1; then
    rm -rf -- "$common/info" || return 1
    if [ -d "$s/info" ]; then cp -a -- "$s/info" "$common/info" || return 1; fi
  fi
  return 0
}

# leerie_fresh_status <worktree>
#
# `git status --porcelain --untracked-files=no` through a fresh index of
# HEAD, which records no file's stat, so git hashes every tracked file
# again. `_fresh_status` in Python.
leerie_fresh_status() {
  local wt="$1" d rc=0
  d="$(mktemp -d)" || return 1
  GIT_INDEX_FILE="$d/index" leerie_vgit "$wt" read-tree HEAD >/dev/null 2>&1 \
    && GIT_INDEX_FILE="$d/index" GIT_OPTIONAL_LOCKS=0 \
       leerie_vgit "$wt" status --porcelain --untracked-files=no || rc=$?
  [ -f "$d/index" ] || rc=1
  rm -rf -- "$d"
  return "$rc"
}

# leerie_fresh_only <real lines> <fresh lines>
#
# The fresh read's lines whose path (from the fourth character) no real
# line names. `_fresh_only` in Python.
leerie_fresh_only() {
  local real="$1" line
  [ -n "$2" ] || return 0
  while IFS= read -r line; do
    [ -n "$line" ] || continue
    if [ -z "$real" ] || ! printf '%s\n' "$real" | cut -c4- | grep -Fxq -- "${line:3}"; then
      printf '%s\n' "$line"
    fi
  done <<< "$2"
}

# The index entries `git ls-files -v` tags as hidden from status — a
# lowercase tag is assume-unchanged, `S`/`s` skip-worktree (git's fixed
# one-letter tags) — as NUL-separated paths. `_flagged_entries` in Python.
leerie_flagged_paths() {
  git -C "$1" rev-parse --git-dir >/dev/null 2>&1 || return 1
  leerie_vgit "$1" ls-files -v -z | while IFS= read -r -d '' rec; do
    case "${rec:0:2}" in
      [abcdefghijklmnopqrstuvwxyzS]" ") printf '%s\0' "${rec:2}" ;;
    esac
  done
}

# I1's checker, the shell half of `_tree_dirt` (DESIGN §8 *A worker leaves
# its tree clean*), read through the run's settings (`leerie_vgit`), line
# for line in the same order: the settings lines, tracked and staged
# changes, what a fresh index of HEAD shows that the tree's index hid,
# every flagged index entry, and every untracked, not-ignored file (the one
# listing, `leerie_untracked`). Prints one line per finding; empty is
# clean; status 1 when git cannot answer.
leerie_tree_dirt() {
  local wt="$1" st fresh
  st="$(leerie_vgit "$wt" status --porcelain --untracked-files=no)" || return 1
  fresh="$(leerie_fresh_status "$wt")" || return 1
  leerie_untracked "$wt" >/dev/null || return 1
  leerie_settings_dirt "$wt"
  [ -z "$st" ] || printf '%s\n' "$st"
  leerie_fresh_only "$st" "$fresh"
  leerie_vgit "$wt" ls-files -v -z | while IFS= read -r -d '' rec; do
    case "${rec:0:2}" in
      [Ss]" ") printf '%s is marked skip-worktree (an edit git status does not show)\n' "${rec:2}" ;;
      [abcdefghijklmnopqrtuvwxyz]" ") printf '%s is marked assume-unchanged (an edit git status does not show)\n' "${rec:2}" ;;
    esac
  done
  leerie_untracked "$wt" | while IFS= read -r -d '' p; do printf '?? %s\n' "$p"; done
  return 0
}

# leerie_reset_worktree <worktree> <branch> [<rev>]
#
# THE restore primitive's shell half (DESIGN §8 *One restore*; Python's is
# `_restore_tree`): <worktree> on <branch> (empty: a detached checkout stays
# detached) at <rev> (default the branch tip, or HEAD), with nothing
# uncommitted. Leerie's settings are copied back over the run repository's
# first (DESIGN §8 *The settings are leerie's*), and every git step after
# runs through them (`leerie_vgit`). A worker may have left HEAD detached or
# on another branch, which `reset --hard HEAD` kept (review round 15 H2), so
# the branch is checked out (`checkout -f`, hooks off), then `reset --hard
# <rev>` (ending any merge) and `clean -ffdq`: nested repositories go too,
# ignored files stay (review round 15 M2). The owner's write and search bits
# go back first on every tracked directory and every untracked, not-ignored
# path (DESIGN §6½: a killed test's read-only directory). Every
# assume-unchanged and skip-worktree flag is cleared before the reset (review
# round 16 H1), and the index is dropped when its stat cache vouches for a
# file the fresh read says differs (the reset would trust it). Postcondition,
# I1's own checker (`leerie_tree_dirt`, the same rule as Python's
# `_tree_dirt`): it prints nothing and HEAD is <branch>; otherwise the lines
# go to stderr and it returns 1.
leerie_reset_worktree() {
  local wt="${1:?usage: leerie_reset_worktree <worktree> <branch> [<rev>]}"
  local branch="${2-}"
  local rev="${3:-}"
  leerie_restore_settings "$wt" || return 1
  chmod u+wx "$wt" 2>/dev/null || true
  # Never through a symlink: a link named on chmod's command line is
  # followed, and an untracked link to /work would chmod the user's checkout
  # (review round 16 exec L1). `_restore_write_bits` skips links the same
  # way; recursion itself never follows one (GNU chmod -R).
  (
    cd "$wt" || exit 0
    git ls-tree -r -d -z --name-only HEAD 2>/dev/null \
      | while IFS= read -r -d '' p; do
          [ -L "$p" ] || chmod u+wx -- "$p" 2>/dev/null || true
        done
    leerie_untracked . --directory 2>/dev/null \
      | while IFS= read -r -d '' p; do
          p="${p%/}"
          [ -L "$p" ] || chmod -R u+w -- "$p" 2>/dev/null || true
        done
  )
  # Index flags off first: `reset --hard` leaves a skip-worktree edit in
  # place, and either flag hides an edit from status (review round 16 H1).
  # One option per `--stdin` call: git applies only the last of two.
  # (Piped, never captured: a command substitution drops the NULs.)
  leerie_flagged_paths "$wt" \
    | leerie_vgit "$wt" update-index --no-skip-worktree -z --stdin || return 1
  leerie_flagged_paths "$wt" \
    | leerie_vgit "$wt" update-index --no-assume-unchanged -z --stdin || return 1
  local real fresh idx
  real="$(leerie_vgit "$wt" status --porcelain --untracked-files=no)" || return 1
  fresh="$(leerie_fresh_status "$wt")" || return 1
  if [ -n "$(leerie_fresh_only "$real" "$fresh")" ]; then
    idx="$(git -C "$wt" rev-parse --path-format=absolute --git-path index)" || return 1
    rm -f -- "$idx"
  fi
  if [ -n "$branch" ]; then
    leerie_vgit "$wt" checkout -q -f "$branch" || return 1
  fi
  leerie_vgit "$wt" reset -q --hard "${rev:-HEAD}" || return 1
  leerie_vgit "$wt" clean -ffdq || return 1
  local dirt
  dirt="$(leerie_tree_dirt "$wt")" || return 1
  if [ -n "$dirt" ]; then
    printf 'leerie_reset_worktree: %s is not clean after the reset:\n%s\n' \
      "$wt" "$dirt" >&2
    return 1
  fi
  if [ -n "$branch" ] && [ "$(git -C "$wt" symbolic-ref -q HEAD)" != "refs/heads/$branch" ]; then
    printf 'leerie_reset_worktree: %s is not on %s after the reset\n' \
      "$wt" "$branch" >&2
    return 1
  fi
}

# --- A repository per committing tree (DESIGN §6 *A repository per
# committing tree (I6)*) -----------------------------------------------------
#
# Each committing tree (staging, each subtask tree) is a worktree of its own
# bare repository `<run-dir>/tree-repos/<tree>`, which borrows the run
# repository's objects (alternates) and holds one ref, the tree's branch. A
# worker writes only that repository; leerie alone moves the run
# repository's refs, by taking a tree's branch back. These run with
# LEERIE_GIT_DIR naming the run repository.

# leerie_take_tree_branch <tree-repo> <branch>
#
# Take a tree's branch into the run repository: its new objects are copied
# and exactly that branch moves. Nothing else of the tree repository's refs
# is read.
leerie_take_tree_branch() {
  local tr="${1:?usage: leerie_take_tree_branch <tree-repo> <branch>}"
  local branch="${2:?usage: leerie_take_tree_branch <tree-repo> <branch>}"
  leerie_repo_git fetch --quiet --no-tags --no-write-fetch-head \
    --no-auto-maintenance "$tr" "+refs/heads/$branch:refs/heads/$branch"
}

# leerie_tree_branch_of <run-id> <tree-name>
#
# The branch leerie takes from a tree repository, by its name alone (never
# the tree's HEAD, which a worker sets): `staging` holds the run branch,
# `<run-id[:12]>-<sid>` the subtask branch. `_tree_branch_of` in Python.
leerie_tree_branch_of() {
  local run_id="${1:?usage: leerie_tree_branch_of <run-id> <tree-name>}"
  local name="${2:?usage: leerie_tree_branch_of <run-id> <tree-name>}"
  local prefix
  if [ "$name" = staging ]; then
    printf 'leerie/runs/%s' "$run_id"
    return
  fi
  prefix="$(leerie_worktree_name "$run_id" x)"
  printf 'leerie/subtasks/%s/%s' "$run_id" "${name#"${prefix%x}"}"
}

# leerie_drop_tree <tree-repo> <worktree>
#
# Remove a tree and its repository. The caller takes the branch first when
# it keeps the work.
leerie_drop_tree() {
  local tr="${1:?usage: leerie_drop_tree <tree-repo> <worktree>}"
  local wt="${2:?usage: leerie_drop_tree <tree-repo> <worktree>}"
  chmod -R u+w "$wt" 2>/dev/null || true
  rm -rf "$wt" "$tr"
}

# leerie_tree <tree-repo> <branch> <worktree>
#
# The committing tree at <worktree> on <branch>, a worktree of its own
# repository <tree-repo>. Present and a worktree of <tree-repo>: its branch
# is taken (a crash may have left a worker's commits untaken) and it is
# reused. Otherwise it is made afresh from the run repository's <branch>: a
# tree of the run repository itself at that path (a run made before tree
# repositories) is deregistered and removed; a stale <tree-repo> has its
# branch taken first (work a crash left there survives the remake) and is
# removed; then the tree repository is made — `init --bare`, the run
# repository's objects through `objects/info/alternates`, `config` and
# `info/` from the run's settings copy (`<run-dir>/repo-settings`, the run
# directory being the tree repository's grandparent, as
# `leerie_run_settings` reads it), the one branch fetched — and the tree
# added from it. The caller holds `leerie_repo_lock`.
leerie_tree() {
  local tr="${1:?usage: leerie_tree <tree-repo> <branch> <worktree>}"
  local branch="${2:?usage: leerie_tree <tree-repo> <branch> <worktree>}"
  local wt="${3:?usage: leerie_tree <tree-repo> <branch> <worktree>}"
  local run_repo settings fmt
  run_repo="$(leerie_repo_git rev-parse --path-format=absolute --git-common-dir)" || return 1
  if [ -f "$tr/HEAD" ] && [ -d "$wt" ] \
      && [ "$(git -C "$wt" rev-parse --path-format=absolute --git-common-dir 2>/dev/null)" = "$tr" ]; then
    leerie_take_tree_branch "$tr" "$branch"
    return
  fi
  if grep -qxF "worktree $wt" <<<"$(leerie_repo_git worktree list --porcelain)"; then
    leerie_repo_git worktree remove --force "$wt" >/dev/null 2>&1 || true
  fi
  if [ -f "$tr/HEAD" ] \
      && git --git-dir="$tr" rev-parse --verify -q "refs/heads/$branch" >/dev/null; then
    leerie_take_tree_branch "$tr" "$branch" || return 1
  fi
  leerie_drop_tree "$tr" "$wt"
  mkdir -p "${tr%/*}"
  fmt="$(leerie_repo_git rev-parse --show-object-format)" || return 1
  git init --quiet --bare --template= --object-format="$fmt" "$tr" || return 1
  mkdir -p "$tr/objects/info"
  printf '%s\n' "$run_repo/objects" > "$tr/objects/info/alternates"
  settings="${tr%/*}"
  settings="${settings%/*}/repo-settings"
  cp "$settings/config" "$tr/config" 2>/dev/null || {
    # No settings copy (a repository leerie did not make, as in a test):
    # the repository's own config, as a bare repository's.
    settings="$run_repo"
    cp "$settings/config" "$tr/config"
    git config -f "$tr/config" core.bare true
  }
  rm -rf "$tr/info"
  if [ -d "$settings/info" ]; then
    cp -R "$settings/info" "$tr/info"
  fi
  git --git-dir="$tr" fetch --quiet --no-tags --no-write-fetch-head \
    --no-auto-maintenance "$run_repo" "+refs/heads/$branch:refs/heads/$branch" \
    || return 1
  git --git-dir="$tr" worktree add --quiet "$wt" "$branch"
}
