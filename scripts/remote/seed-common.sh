#!/usr/bin/env bash
# scripts/remote/seed-common.sh — transport-agnostic seeding helpers shared
# by both the Fly path (lib.sh / seed-repo.sh) and the EC2 path (ec2-lib.sh /
# ec2-seed-repo.sh).
#
# Sourced by both lib.sh and ec2-lib.sh, which is what makes this the single
# definition site: seed-repo.sh sources lib.sh (transitively this file) and
# ec2-seed-repo.sh sources ec2-lib.sh (transitively this file too), so
# neither seed script needs to source this file directly. Bash 3.2
# portable — no namerefs, no bash-4-only syntax (CLAUDE.md; see
# tests/test_ec2_bash32_portability.py).

_SEED_COMMON_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --- _seed_dirty_filter -----------------------------------------------------
# Runs on the HOST (never shipped to the remote machine): filters the
# newline-delimited candidate file list on stdin down to what should
# actually rsync, writing NUL-delimited survivors to stdout. Single-owner
# implementation lives in seed_dirty_filter.py, right beside this file, so
# the Fly transport (seed-repo.sh) and the EC2 transport (ec2-seed-repo.sh)
# can no longer drift on editor-temp detection, the .git/.leerie exclusion
# + whitelist, the worktree-path defense, or the vanished-entry check.
# USER_REPO must already be exported by the caller — it anchors the
# lexists() vanished-entry check.
_seed_dirty_filter() {
  python3 "$_SEED_COMMON_DIR/seed_dirty_filter.py"
}

# --- _seed_timeout_prefix --------------------------------------------------
# Emit the `timeout --kill-after=5 ${LEERIE_SEED_TIMEOUT_S:-600}` prefix
# used to bound the bulk-transfer side of `flyctl ssh console` (Fly) / `aws
# ssm start-session` (EC2). On hosts without GNU `timeout` (BSD macOS w/o
# coreutils) the function echoes nothing — caller falls back to an
# unbounded pipe, matching pre-fix behavior. The fix converts a silent
# multi-hour hang into a clean non-zero rc (124 on TERM, 137 on KILL) that
# the seed_auth/seed_repo retry / failure paths already know how to handle.
# Background: `flyctl ssh console -C` is known to hang without exiting when
# the WireGuard tunnel stalls mid-transfer (observed 2026-06-04 across four
# parallel runs). See plan file for full evidence.
#
# Usage:
#   $(_seed_timeout_prefix) flyctl ssh console ... -C ...
#   $(_seed_timeout_prefix) aws ssm start-session ...
_seed_timeout_prefix() {
  if ! command -v timeout >/dev/null 2>&1; then
    return 0
  fi
  printf 'timeout --kill-after=5 %s' "${LEERIE_SEED_TIMEOUT_S:-600}"
}

# ---------------------------------------------------------------------------
# _seed_use_shallow
#
# Decide whether the parent repo should be shipped via the shallow
# tar-of-.git path instead of the full `git bundle --all`. Returns 0
# (shallow) when BOTH hold: LEERIE_SEED_DEPTH is a non-zero integer AND
# the host repo's .git exceeds LEERIE_SEED_SHALLOW_THRESHOLD_MB. Returns
# 1 (full bundle) otherwise — including on any probe failure, so the
# safe default is always the proven full-bundle path.
# (DESIGN §6 *Shallow seeding for heavy repos*.)
# ---------------------------------------------------------------------------
_seed_use_shallow() {
  local _depth="${LEERIE_SEED_DEPTH:-0}" _thresh="${LEERIE_SEED_SHALLOW_THRESHOLD_MB:-200}" _git_kb
  case "$_depth" in ''|*[!0-9]*|0) return 1 ;; esac
  case "$_thresh" in ''|*[!0-9]*|0) return 1 ;; esac
  # .git size in KB. --git-dir handles worktrees / .git-file layouts.
  local _gitdir
  _gitdir="$(git -C "$USER_REPO" rev-parse --git-dir 2>/dev/null)" || return 1
  case "$_gitdir" in /*) : ;; *) _gitdir="$USER_REPO/$_gitdir" ;; esac
  _git_kb="$(du -sk "$_gitdir" 2>/dev/null | awk '{print $1}')" || return 1
  case "$_git_kb" in ''|*[!0-9]*) return 1 ;; esac
  [ "$_git_kb" -gt "$(( _thresh * 1024 ))" ]
}

# ---------------------------------------------------------------------------
# _seed_branch_shallow_safe <branch>
#
# The shallow path injects the branch name into a `git checkout -f <branch>`
# line inside the machine-side script, which is sent as `flyctl … -C
# "sh -c '<script>'"` (or the EC2 SSM equivalent). A branch name is under
# user control and git permits characters that would break that
# single-quoted wrapper or inject into the remote shell — an apostrophe
# (`feat/it's-a-branch` is a valid ref) closes the quote early; `$` /
# backtick could construct commands. Rather than escape (fragile), we allow
# the shallow path ONLY for a conservative, shell-safe charset (the
# overwhelming majority of real branches) and fall back to the proven
# full-bundle path for anything else. Returns 0 (safe) when the branch is
# non-empty and matches ^[A-Za-z0-9/._-]+$, 1 otherwise.
#
# Also reject the machine-script placeholder tokens: the branch is baked
# into $_parent_materialize before the `${//__CLEANUP_TMP__/…}` pass, so a
# branch literally named __CLEANUP_TMP__ / __PARENT_MATERIALIZE__ would be
# mangled by that later substitution. Such branch names don't exist in
# practice; rejecting them (→ full bundle) is free insurance.
# ---------------------------------------------------------------------------
_seed_branch_shallow_safe() {
  case "$1" in
    ''|*[!A-Za-z0-9/._-]*) return 1 ;;
    *__PARENT_MATERIALIZE__*|*__CLEANUP_TMP__*) return 1 ;;
    *) return 0 ;;
  esac
}
