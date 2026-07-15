#!/usr/bin/env bash
# scripts/remote/ec2-seed-repo.sh — seed the developer's working tree into
# an EC2 instance for one leerie remote run.
#
# EC2 counterpart to scripts/remote/seed-repo.sh (DESIGN §6 *EC2 runtime
# lifecycle*, "Seed" row: "same two steps, transport substituted"). The
# payload logic — git bundle / shallow-.git-tar construction, the
# .gitignore-aware file set, the .leerie/ exclusion, the shallow-threshold
# decision — is IDENTICAL to the Fly path; only the transport differs:
#
#   Fly: `flyctl ssh console -C "sh -c 'cat > ...'"` for both bulk data
#        AND small remote commands (mkdir/chown/clone).
#   EC2: `ec2_tar_pipe` (plain ssh, DESIGN §6 "Transport substitution")
#        for bulk data — SSM's AWS-StartInteractiveCommand has no stdin
#        pipe and a ~4 KB document-parameter ceiling, so it cannot carry
#        a bundle/tar payload — and `ec2_remote_exec` (SSM Session
#        Manager, the default transport) for small remote commands.
#
# Two-phase seeding, same split as seed-repo.sh:
#
#   1. `ec2_seed_repo_clone` — laptop creates a `git bundle` for the
#      parent repo (or a shallow `.git` tar above the size threshold —
#      see `_seed_use_shallow` in ec2-lib.sh) and pipes it to the
#      instance via `ec2_tar_pipe` (the payload is itself a gzipped tar
#      containing exactly one file, since ec2_tar_pipe's receiver is
#      `tar -xzC <dir>`, not a bare `cat >`). The instance then
#      `git clone`s from the bundle (or untars + checks out the shallow
#      .git), wires submodule URLs to their bundles, and runs
#      `git -c protocol.file.allow=always submodule update --recursive`.
#      Delivers committed state only.
#
#   2. `ec2_seed_repo_dirty` — laptop rsync's the dirty/untracked delta
#      (uncommitted edits, untracked-not-ignored files, forced-in
#      `.claude/`) into /work directly over `ssh` (SSH is a real,
#      directly-usable transport for EC2 — unlike Fly, which tunnels
#      rsync through `flyctl ssh console -C "rsync --server ..."`, EC2's
#      SSH transport needs no such wrapper). Delivers uncommitted state.
#
# Why bundles/.git-tar instead of a working-tree tar or plain rsync for
# the committed state: identical reasoning to seed-repo.sh — macOS BSD
# `tar -c` normalizes filenames NFC → NFD; pack objects (and rsync,
# separately) carry filenames as raw bytes and never trigger that
# normalization. See seed-repo.sh's header comment for the full history.
#
# EC2 instances receive no GitHub credentials, mirroring Fly (DESIGN §6
# *Finalization*): workers commit on the instance, the host pushes via
# `leerie --finalize`.
#
# Usage (called by the leerie launcher after ec2 provisioning succeeds):
#
#   source scripts/remote/ec2-lib.sh
#   source scripts/remote/ec2-seed-repo.sh
#   ec2_seed_repo         # blocks until seeding is complete
#
# Environment variables consumed:
#
#   LEERIE_EC2_INSTANCE_ID — id of the running EC2 instance (set by
#                            ec2-provision.sh once it lands)
#   LEERIE_EC2_SSH_TARGET  — ssh(1) destination for the instance (e.g.
#                            "ec2-user@<public-ip>" or an ssh_config Host
#                            alias). Resolving an instance id to a
#                            reachable address is provisioning's job
#                            (out of scope here, same as ec2_tar_pipe's
#                            own docstring states) — this script only
#                            consumes the resolved target, exactly like
#                            ec2_tar_pipe's own <ssh-target> argument.
#   USER_REPO               — absolute path to the local git repo (set by
#                            the launcher)
#   LEERIE_SEED_DEPTH / LEERIE_SEED_SHALLOW_THRESHOLD_MB / LEERIE_SEED_TIMEOUT_S
#                          — same knobs seed-repo.sh honors (runtime-
#                            agnostic; see docs/IMPLEMENTATION.md).
#
# Requires (host): aws CLI on PATH and authenticated (require_aws); ssh;
#                  git; python3; rsync (for the dirty-delta phase).
# Requires (instance, baked into the AMI/image): git; rsync; tar.

set -euo pipefail

# --- shared lib (ec2_remote_exec / ec2_tar_pipe / require_aws / etc.) -----
_EC2_SEED_REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
. "$_EC2_SEED_REPO_DIR/ec2-lib.sh"

# ---------------------------------------------------------------------------
# _ec2_seed_repo_preflight
#
# Common validation for both ec2_seed_repo_clone and ec2_seed_repo_dirty.
# Mirrors seed-repo.sh's _seed_repo_preflight, substituting the EC2
# instance id/ssh-target env vars and require_aws for their Fly
# counterparts.
# ---------------------------------------------------------------------------
_ec2_seed_repo_preflight() {
  if [ -z "${LEERIE_EC2_INSTANCE_ID:-}" ]; then
    remote_log "ec2_seed_repo: LEERIE_EC2_INSTANCE_ID is not set"
    return 1
  fi
  if [ -z "${LEERIE_EC2_SSH_TARGET:-}" ]; then
    remote_log "ec2_seed_repo: LEERIE_EC2_SSH_TARGET is not set"
    return 1
  fi
  if [ -z "${USER_REPO:-}" ]; then
    remote_log "ec2_seed_repo: USER_REPO is not set"
    return 1
  fi
  if command -v require_aws >/dev/null 2>&1; then
    require_aws || return 1
  elif ! command -v aws >/dev/null 2>&1; then
    remote_log "ec2_seed_repo: aws CLI not found on PATH"
    return 1
  fi
}

# ---------------------------------------------------------------------------
# _seed_use_shallow / _seed_branch_shallow_safe
#
# Transport-independent decisions — identical logic to seed-repo.sh's
# functions of the same name (DESIGN §6 *Shallow seeding for heavy
# repos*: depth/threshold/branch-safety are runtime-agnostic knobs). Not
# sourced from seed-repo.sh (this file must stay independently
# sourceable without pulling in Fly-specific lib.sh state); duplicated
# here verbatim instead — mirror any change in seed-repo.sh here too.
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

_seed_branch_shallow_safe() {
  case "$1" in
    ''|*[!A-Za-z0-9/._-]*) return 1 ;;
    *__PARENT_MATERIALIZE__*|*__CLEANUP_TMP__*) return 1 ;;
    *) return 0 ;;
  esac
}

# ---------------------------------------------------------------------------
# _ec2_pipe_file_via_tar <local-file> <remote-name>
#
# ec2_tar_pipe's receiver is `tar -xzC <extract_dir>` — it extracts a
# gzipped tar archive, not a bare `cat > file` (that's the Fly transport's
# shape; SSH gives us a real stdin pipe, so we use a one-entry tar as the
# uniform wire format for both the parent bundle and each submodule
# bundle). This helper wraps a single local file into a one-entry gzipped
# tar (named <remote-name> inside the archive, flattening any local
# directory structure) and streams it through ec2_tar_pipe into
# <extract-dir> on the instance, so the file lands at
# <extract-dir>/<remote-name>.
#
# Wrapped in $(_seed_timeout_prefix) via ec2_tar_pipe itself — a stalled
# SSH session yields rc 124/137, never hangs.
# ---------------------------------------------------------------------------
_ec2_pipe_file_via_tar() {
  local local_file="$1" remote_name="$2" extract_dir="$3"
  tar -C "$(dirname "$local_file")" -czf - "$(basename "$local_file")" \
    | ec2_tar_pipe "$LEERIE_EC2_SSH_TARGET" "$extract_dir"
  local rc=$?
  if [ "$rc" -ne 0 ]; then
    return "$rc"
  fi
  # tar preserves the source basename; rename to the requested remote name
  # when they differ (the submodule bundle path flattens displaypath with
  # "/" -> "_", which the source basename may not already match).
  if [ "$(basename "$local_file")" != "$remote_name" ]; then
    ec2_remote_exec "$LEERIE_EC2_INSTANCE_ID" \
      "mv '$extract_dir/$(basename "$local_file")' '$extract_dir/$remote_name'" \
      >/dev/null
    rc=$?
  fi
  return "$rc"
}

# ---------------------------------------------------------------------------
# _ec2_seed_shallow_parent <branch>
#
# EC2 analog of seed-repo.sh's _seed_shallow_parent. Makes a throwaway
# `git clone --depth=N` of the working branch on the host, tars ONLY its
# .git, and pipes that (via _ec2_pipe_file_via_tar, over ec2_tar_pipe) to
# /tmp/leerie-seed-git.tar on the instance.
#
# See seed-repo.sh's _seed_shallow_parent docstring for the full NFC-safety
# and merge-base-preservation rationale — identical here, transport aside.
# ---------------------------------------------------------------------------
_ec2_seed_shallow_parent() {
  local _branch="$1" _tmp_shallow _hb_pid _rc=0
  if [ -z "$_branch" ]; then
    remote_log "ec2_seed_repo: _ec2_seed_shallow_parent called without a branch"
    return 1
  fi

  _tmp_shallow="$(mktemp -d -t leerie-shallow.XXXXXX)" || return 1
  # shellcheck disable=SC2064
  trap "rm -rf '$_tmp_shallow'" RETURN
  if ! git clone --quiet --depth="$LEERIE_SEED_DEPTH" --no-local \
         --branch "$_branch" "file://$USER_REPO" "$_tmp_shallow/repo" 2>/dev/null; then
    remote_log "ec2_seed_repo: shallow clone (depth=$LEERIE_SEED_DEPTH) of $_branch failed"
    return 1
  fi

  tar -C "$_tmp_shallow/repo" -cf "$_tmp_shallow/leerie-seed-git.tar" .git 2>/dev/null

  _seed_progress_bg "ec2_seed_repo (shallow parent .git)" &
  _hb_pid=$!
  _ec2_pipe_file_via_tar "$_tmp_shallow/leerie-seed-git.tar" \
    "leerie-seed-git.tar" "/tmp"
  _rc=$?
  kill "$_hb_pid" 2>/dev/null || true
  wait "$_hb_pid" 2>/dev/null || true
  if [ "$_rc" -ne 0 ]; then
    if [ "$_rc" -eq 124 ] || [ "$_rc" -eq 137 ]; then
      remote_log "ec2_seed_repo: shallow .git pipe did not return within ${LEERIE_SEED_TIMEOUT_S:-600}s (rc=$_rc) — ssh likely stalled"
    fi
    remote_log "ec2_seed_repo: failed to pipe shallow .git tar to instance"
    return 1
  fi
  return 0
}

# ---------------------------------------------------------------------------
# ec2_seed_repo_clone
#
# Ship the host's committed state to /work on the instance. Same two
# parent-delivery transports as seed_repo_clone (full bundle vs. shallow
# .git tar), same shallow decision (_seed_use_shallow /
# _seed_branch_shallow_safe), same per-submodule bundle machinery — only
# the wire transport differs (ec2_tar_pipe/ec2_remote_exec instead of
# `flyctl ssh console`).
#
# Always wipes /work first — call this only on a fresh provision, never
# on resume.
# ---------------------------------------------------------------------------
ec2_seed_repo_clone() {
  _ec2_seed_repo_preflight || return 1

  local _shallow=false _branch=""
  if _seed_use_shallow; then
    _branch="$(git -C "$USER_REPO" rev-parse --abbrev-ref HEAD 2>/dev/null)"
    if [ -z "$_branch" ] || [ "$_branch" = "HEAD" ]; then
      remote_log "ec2_seed_repo: detached/unresolvable HEAD; using full bundle (shallow needs a named branch)"
    elif ! _seed_branch_shallow_safe "$_branch"; then
      remote_log "ec2_seed_repo: branch '$_branch' has non-shell-safe characters; using full bundle instead of shallow"
    else
      _shallow=true
    fi
  fi
  if [ "$_shallow" = "true" ]; then
    remote_log "remote: seeding — shallow-cloning $USER_REPO ($_branch, .git > ${LEERIE_SEED_SHALLOW_THRESHOLD_MB}MB, depth=$LEERIE_SEED_DEPTH) + submodules to /work (ec2) ..."
  else
    remote_log "remote: seeding — bundling $USER_REPO (parent + submodules) to /work (ec2) ..."
  fi

  # Empty /work's CONTENTS but preserve the directory inode (same
  # rationale as seed-repo.sh: a process with cwd=/work must not see its
  # inode replaced out from under it). Also reset the bundle/tar staging
  # paths in case a prior paused run left them behind.
  if ! ec2_remote_exec "$LEERIE_EC2_INSTANCE_ID" \
        "find /work -mindepth 1 -maxdepth 1 -exec rm -rf {} + && chown leerie: /work && rm -rf /tmp/leerie-seed.bundle /tmp/leerie-seed-git.tar /tmp/leerie-subs && mkdir -p /tmp/leerie-subs" \
        >/dev/null; then
    remote_log "ec2_seed_repo: failed to reset /work on instance $LEERIE_EC2_INSTANCE_ID"
    return 1
  fi

  # 1. Deliver the parent repo's committed state to the instance.
  if [ "$_shallow" = "true" ]; then
    _ec2_seed_shallow_parent "$_branch" || return 1
  else
    local _tmp_bundle _hb_pid_parent _parent_rc=0
    _tmp_bundle="$(mktemp -d -t leerie-bundle.XXXXXX)" || return 1
    # shellcheck disable=SC2064
    trap "rm -rf '$_tmp_bundle'" RETURN
    if ! git -C "$USER_REPO" bundle create "$_tmp_bundle/leerie-seed.bundle" --all 2>/dev/null; then
      remote_log "ec2_seed_repo: failed to create parent bundle"
      return 1
    fi
    _seed_progress_bg "ec2_seed_repo (parent bundle)" &
    _hb_pid_parent=$!
    _ec2_pipe_file_via_tar "$_tmp_bundle/leerie-seed.bundle" \
      "leerie-seed.bundle" "/tmp"
    _parent_rc=$?
    kill "$_hb_pid_parent" 2>/dev/null || true
    wait "$_hb_pid_parent" 2>/dev/null || true
    if [ "$_parent_rc" -ne 0 ]; then
      if [ "$_parent_rc" -eq 124 ] || [ "$_parent_rc" -eq 137 ]; then
        remote_log "ec2_seed_repo: parent-bundle pipe did not return within ${LEERIE_SEED_TIMEOUT_S:-600}s (rc=$_parent_rc) — ssh likely stalled"
      fi
      remote_log "ec2_seed_repo: failed to pipe parent bundle to instance"
      return 1
    fi
  fi

  # 2. Bundle each submodule recursively, pipe each into
  #    /tmp/leerie-subs/<flattened-displaypath>.bundle. Same displaypath
  #    flattening ("/" -> "_") as seed-repo.sh, applied identically here
  #    and in the machine-side clone script (step 3) below.
  if [ -f "$USER_REPO/.gitmodules" ]; then
    local _hb_pid_subs _tmp_subs
    _tmp_subs="$(mktemp -d -t leerie-subs.XXXXXX)" || return 1
    # shellcheck disable=SC2064
    trap "rm -rf '$_tmp_subs'" RETURN
    _seed_progress_bg "ec2_seed_repo (submodule bundles)" &
    _hb_pid_subs=$!
    if ! (
      cd "$USER_REPO" && \
      LEERIE_EC2_INSTANCE_ID="$LEERIE_EC2_INSTANCE_ID" \
      LEERIE_EC2_SSH_TARGET="$LEERIE_EC2_SSH_TARGET" \
      LEERIE_TMP_SUBS="$_tmp_subs" \
      git submodule --quiet foreach --recursive '
        bn="$(printf %s "$displaypath" | tr / _).bundle"
        git bundle create "$LEERIE_TMP_SUBS/$bn" --all 2>/dev/null \
          || { echo "leerie: ec2_seed_repo: failed to bundle submodule $displaypath" >&2; exit 1; }
      '
    ); then
      kill "$_hb_pid_subs" 2>/dev/null || true
      wait "$_hb_pid_subs" 2>/dev/null || true
      remote_log "ec2_seed_repo: submodule bundling failed"
      return 1
    fi
    local _sub_bundle
    for _sub_bundle in "$_tmp_subs"/*.bundle; do
      [ -e "$_sub_bundle" ] || continue
      if ! _ec2_pipe_file_via_tar "$_sub_bundle" "$(basename "$_sub_bundle")" "/tmp/leerie-subs"; then
        kill "$_hb_pid_subs" 2>/dev/null || true
        wait "$_hb_pid_subs" 2>/dev/null || true
        remote_log "ec2_seed_repo: failed to pipe submodule bundle $(basename "$_sub_bundle") to instance"
        return 1
      fi
    done
    kill "$_hb_pid_subs" 2>/dev/null || true
    wait "$_hb_pid_subs" 2>/dev/null || true
  fi

  # 3. Instance-side: materialize /work, wire each submodule's URL to its
  #    bundle file, submodule update, chown -R leerie: /work. Same
  #    placeholder-substitution build as seed-repo.sh.
  local _parent_materialize _cleanup_tmp
  if [ "$_shallow" = "true" ]; then
    _parent_materialize="find /work -mindepth 1 -maxdepth 1 -exec rm -rf {} +
tar -C /work -xf /tmp/leerie-seed-git.tar
cd /work
git checkout -f $_branch
git remote remove origin 2>/dev/null || true"
    _cleanup_tmp='/tmp/leerie-seed-git.tar /tmp/leerie-subs'
  else
    _parent_materialize='git clone /tmp/leerie-seed.bundle /work
cd /work'
    _cleanup_tmp='/tmp/leerie-seed.bundle /tmp/leerie-subs'
  fi

  local _machine_script
  _machine_script="$(cat <<'MACHINE_SCRIPT'
set -e
__PARENT_MATERIALIZE__
if [ -f .gitmodules ]; then
  git submodule init
  git submodule status | awk "{print \$2}" | while read sm; do
    bn=$(printf %s "$sm" | tr / _).bundle
    if [ -f "/tmp/leerie-subs/$bn" ]; then
      git config "submodule.$sm.url" "/tmp/leerie-subs/$bn"
    fi
  done
  git -c protocol.file.allow=always submodule update --recursive
fi
chown -R leerie: /work
rm -rf __CLEANUP_TMP__
MACHINE_SCRIPT
)"
  _machine_script="${_machine_script//__PARENT_MATERIALIZE__/$_parent_materialize}"
  _machine_script="${_machine_script//__CLEANUP_TMP__/$_cleanup_tmp}"

  local _mode="clone-from-bundle"
  [ "$_shallow" = "true" ] && _mode="shallow-untar"
  if ! ec2_remote_exec "$LEERIE_EC2_INSTANCE_ID" "$_machine_script" >/dev/null; then
    remote_log "ec2_seed_repo: instance-side reconstruction of /work failed ($_mode)"
    return 1
  fi

  remote_log "remote: repo seeded to /work (ec2)"
}

# ---------------------------------------------------------------------------
# ec2_seed_repo_dirty
#
# Rsync the dirty/untracked delta from host to /work on the instance.
# Same dirty-set computation as seed_repo_dirty (git status --porcelain
# filtered to drop .git/*, non-whitelisted .leerie/* paths, and defensive
# worktree paths, plus the repo-local .claude/ force-include), but the
# transport is plain `ssh` (no rsync-server-over-flyctl-console wrapper
# needed — SSH is directly usable for EC2, per DESIGN §6).
# ---------------------------------------------------------------------------
ec2_seed_repo_dirty() {
  _ec2_seed_repo_preflight || return 1

  local dirty_files
  dirty_files="$(git -C "$USER_REPO" status --porcelain 2>/dev/null \
                  | awk '
                      /^\?\? / {
                        f = substr($0, 4)
                        gsub(/\/$/, "", f)
                        print f
                        next
                      }
                      length($0) >= 2 && substr($0,2,1) != " " {
                        f = substr($0, 4)
                        if (index(f, " -> ")) {
                          f = substr(f, index(f, " -> ") + 4)
                        }
                        gsub(/\/$/, "", f)
                        print f
                      }
                  ')"

  local claude_files=""
  if [ -d "$USER_REPO/.claude" ]; then
    claude_files="$(cd "$USER_REPO" && find .claude -type f 2>/dev/null)"
  fi

  if [ -z "$dirty_files" ] && [ -z "$claude_files" ]; then
    remote_log "remote: seeding — working tree is clean; no delta to sync"
    return 0
  fi

  local file_count
  file_count="$(printf '%s\n%s\n' "$dirty_files" "$claude_files" \
                 | grep -c -v '^$' || true)"
  remote_log "remote: seeding — syncing $file_count dirty / forced-include file(s) (ec2)..."

  local rsync_rc=0
  local file_list wrapper
  file_list="$(mktemp -t leerie-reseed-list.XXXXXX)"
  wrapper="$(_ec2_ssh_wrapper)"

  printf '%s\n%s\n' "$dirty_files" "$claude_files" \
    | USER_REPO="$USER_REPO" python3 -c '
import os, re, sys

_VIM_SWAP_RE = re.compile(r"^\..*\.sw[a-z]$")
def _is_editor_temp(path: str) -> bool:
    base = path.rsplit("/", 1)[-1]
    return (
        base.startswith(".#")
        or base.endswith("~")
        or bool(_VIM_SWAP_RE.match(base))
    )

repo_root = os.environ.get("USER_REPO", "")

for line in sys.stdin.read().splitlines():
    if not line:
        continue
    if line.startswith(".git/") or line == ".git":
        continue
    if line.startswith(".leerie/"):
        if line not in (".leerie/config.toml", ".leerie/Dockerfile",
                        ".leerie/.leerie-setup.sh"):
            continue
    elif line == ".leerie":
        continue
    if "/.leerie/runs/" in line and "/worktrees/" in line:
        continue
    if _is_editor_temp(line):
        continue
    if repo_root and not os.path.lexists(os.path.join(repo_root, line)):
        continue
    sys.stdout.buffer.write(line.encode() + b"\x00")
' > "$file_list"

  local rsync_err
  rsync_err="$(mktemp -t leerie-reseed-err.XXXXXX)"
  rsync -a -H \
    --from0 --files-from="$file_list" \
    -e "$wrapper" \
    "$USER_REPO/" "$LEERIE_EC2_SSH_TARGET:/work/" \
    >/dev/null 2>"$rsync_err"
  rsync_rc=$?

  if [ "$rsync_rc" -ne 0 ]; then
    remote_log "ec2_seed_repo: rsync delta transfer failed (exit $rsync_rc)"
    if [ -s "$rsync_err" ]; then
      while IFS= read -r _ln; do
        remote_log "  rsync: $_ln"
      done < "$rsync_err"
    fi
    rm -f "$file_list" "$wrapper" "$rsync_err"
    return 1
  fi
  rm -f "$rsync_err"

  if ! ec2_remote_exec "$LEERIE_EC2_INSTANCE_ID" "chown -R leerie: /work" >/dev/null; then
    rm -f "$file_list" "$wrapper"
    remote_log "ec2_seed_repo: chown -R leerie: /work after delta failed"
    return 1
  fi

  rm -f "$file_list" "$wrapper"
  remote_log "remote: seeding complete (ec2)"
}

# ---------------------------------------------------------------------------
# _ec2_ssh_wrapper
#
# rsync -e wrapper: a plain ssh invocation with the same BatchMode /
# StrictHostKeyChecking flags ec2_tar_pipe uses, wrapped in
# $(_seed_timeout_prefix) so a stalled rsync-over-ssh session yields rc
# 124/137 rather than hanging. Written to a small executable script (not
# passed inline) so rsync's `-e` can invoke it as a single argv[0] the
# same way fly_rsync_wrapper does for the Fly path.
# ---------------------------------------------------------------------------
_ec2_ssh_wrapper() {
  local wrapper
  wrapper="$(mktemp -t leerie-ec2-ssh-wrapper.XXXXXX)"
  local _to=""
  _to="$(_seed_timeout_prefix)"
  cat > "$wrapper" <<EOF
#!/usr/bin/env bash
exec $_to ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new "\$@"
EOF
  chmod +x "$wrapper"
  printf '%s' "$wrapper"
}

# ---------------------------------------------------------------------------
# ec2_seed_repo
#
# Used on fresh EC2 provisions. Two phases:
#   1. ec2_seed_repo_clone — bundles parent + submodules, instance clones
#      from bundles (or reconstructs a shallow .git). Delivers committed
#      state only.
#   2. ec2_seed_repo_dirty — rsync's the dirty/untracked delta plus
#      forced-in .claude/, completing the working tree state on the
#      instance.
# ---------------------------------------------------------------------------
ec2_seed_repo() {
  ec2_seed_repo_clone || return 1
  ec2_seed_repo_dirty
}
