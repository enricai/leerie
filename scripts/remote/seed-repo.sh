#!/usr/bin/env bash
# scripts/remote/seed-repo.sh — seed the developer's working tree into a
# Fly.io Machine for one leerie remote run.
#
# Two-phase seeding:
#
#   1. `seed_repo_clone` — laptop creates `git bundle` for the parent
#      repo and each submodule (recursive); pipes each bundle to the
#      machine via `flyctl ssh console -C "sh -c 'cat > /tmp/...'"`.
#      Machine then `git clone`s from the parent bundle file on local
#      disk, wires each submodule URL to point at its bundle, and runs
#      `git -c protocol.file.allow=always submodule update --recursive`
#      (the protocol-flag is required by git 2.38+ for file://-style
#      URLs, per CVE-2022-39253). Delivers committed state only.
#
#      Shallow parent transport (heavy repos, DESIGN §6 *Shallow
#      seeding for heavy repos*): for a repo whose `.git` exceeds
#      `LEERIE_SEED_SHALLOW_THRESHOLD_MB` (and with a non-zero
#      `LEERIE_SEED_DEPTH`), the full `git bundle --all` for the
#      *parent* is hundreds of MB of deep history — a single serialized
#      pipe that can blow the seed timeout. Instead the laptop makes a
#      throwaway `git clone --depth=N` of the working branch, tars ONLY
#      its `.git`, pipes that over the SAME channel, and the machine
#      untars + `git checkout`s. CRITICAL: this tars `.git` only, never
#      the working tree — the NFC→NFD safety below depends on it (git
#      recreates working-tree filenames natively on checkout).
#      `git bundle` CANNOT ship a shallow repo (its grafted commits have
#      unreachable parents), which is why the shallow path uses tar, not
#      a shallow bundle. Submodules are UNCHANGED — a `--depth` parent
#      clone doesn't populate `.git/modules`, so each submodule still
#      ships as its own full bundle. See `_seed_use_shallow` /
#      `_seed_shallow_parent`.
#
#   2. `seed_repo_dirty` — laptop rsync's the dirty/untracked delta
#      (uncommitted edits, untracked-not-ignored files, forced-in
#      `.claude/`) into /work via the existing `fly_rsync_wrapper`
#      from lib.sh. Same helper used by `re-seed.sh` for the Phase 4
#      mid-run re-seed flow. Delivers uncommitted state.
#
# Why bundles instead of a tar pipe or pure rsync:
#   macOS BSD `tar -c` normalizes filenames NFC → NFD when archiving
#   (libarchive behavior; documented). The Linux receiver writes NFD
#   bytes to disk; git's index still holds the NFC bytes (because the
#   index was built on macOS where APFS normalizes at the syscall
#   layer). Result: filenames with `ó`, `ñ`, emoji, etc. show as
#   untracked + missing on the machine even when clean on the host,
#   and any submodule containing such a file flags the parent ` M`.
#   This broke `~/src/enric/api`'s preflight in the live test on
#   2026-06-01.
#
#   Bundles sidestep the problem entirely because the bundle file
#   stores pack-format binary objects (tree entries by raw bytes);
#   the receiving Linux git creates filenames natively on disk from
#   those objects. No filename ever serializes through the transport
#   layer where normalization could intervene. rsync (used for the
#   dirty delta) also preserves filename bytes verbatim.
#
# Why bundle each submodule separately:
#   `git bundle create --all` packs only the host repo's own refs.
#   Submodule objects live in `.git/modules/<sm>/` and are NOT
#   included in the parent's bundle. We bundle each submodule
#   separately, transfer each, and on the machine point each
#   submodule's URL at its bundle file (via `git config
#   submodule.<name>.url`) before submodule update.
#
# Transport is the SAME `flyctl ssh console -C "sh -c 'cat > ...'"`
# pipe leerie uses for the Claude home stage (~234 MB, proven) and the
# reverse direction in `fetch-branch.sh` (which moves bundles
# machine → host). The `sh -c` wrapper is load-bearing: bare
# `-C "cat > /tmp/..."` is parsed by flyctl as if `>` were a `cat`
# argument and fails with `cat: invalid option -- 'c'`.
#
# Fly machines deliberately receive no GitHub credentials (DESIGN §6
# *Finalization*: workers commit on the machine, the host pushes via
# `leerie finalize`; no long-lived push tokens on Fly). Bundling from
# the host satisfies the no-credentials constraint without an
# in-machine `git clone` against origin.
#
# After seeding, /work on the machine mirrors the developer's working
# tree: same committed state, same uncommitted edits, same untracked
# files, full git history, submodule working trees populated.
#
# Usage (called by the leerie launcher after provision_machine succeeds):
#
#   source scripts/remote/seed-repo.sh
#   seed_repo         # blocks until seeding is complete
#
# Environment variables consumed:
#
#   LEERIE_MACHINE_ID   — ID of the started Fly Machine (set by provision.sh)
#   LEERIE_FLY_APP      — Fly.io app name (required)
#   USER_REPO         — absolute path to the local git repo (set by launcher)
#
# Requires (host): flyctl on PATH and authenticated; git; python3;
#                  rsync (for the dirty-delta phase).
# Requires (machine, baked into the image): git; rsync.

set -euo pipefail

# --- shared lib (fly_rsync_wrapper / iso_now / require_flyctl / etc.) -----
_SEED_REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
. "$_SEED_REPO_DIR/lib.sh"

FLY_APP="${LEERIE_FLY_APP:-}"

# ---------------------------------------------------------------------------
# _seed_repo_preflight
#
# Common validation for both seed_repo_clone and seed_repo_dirty. Returns 0
# when all required env vars and binaries are present; 1 with an actionable
# stderr message otherwise.
# ---------------------------------------------------------------------------
_seed_repo_preflight() {
  if [ -z "${LEERIE_MACHINE_ID:-}" ]; then
    remote_log "seed_repo: LEERIE_MACHINE_ID is not set"
    return 1
  fi
  if [ -z "${USER_REPO:-}" ]; then
    remote_log "seed_repo: USER_REPO is not set"
    return 1
  fi
  # flyctl presence + auth via the shared helper from lib.sh. The launcher's
  # RUNTIME=fly preflight already calls require_flyctl; this is belt-and-
  # braces for callers that source seed-repo.sh standalone.
  if ! command -v require_flyctl >/dev/null 2>&1; then
    if ! command -v flyctl >/dev/null 2>&1; then
      remote_log "seed_repo: flyctl not found on PATH"
      return 1
    fi
  else
    require_flyctl || return 1
  fi
}

# ---------------------------------------------------------------------------
# _seed_use_shallow / _seed_branch_shallow_safe
#
# Defined in scripts/remote/seed-common.sh, sourced transitively via
# lib.sh above (single definition site, shared with ec2-seed-repo.sh via
# ec2-lib.sh).
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# _seed_shallow_parent
#
# The shallow parent-delivery path used by seed_repo_clone when
# _seed_use_shallow says yes. Makes a throwaway `git clone --depth=N`
# of the working branch on the host, tars ONLY its .git, pipes that
# over the same $(_seed_timeout_prefix)-wrapped ssh-console channel the
# full-bundle path uses, and has the machine untar + checkout.
#
# Tars .git-only (never the working tree) so the NFC→NFD filename bug
# stays sidestepped exactly as with bundles: object contents carry
# filenames as raw bytes; the Linux receiver materializes them natively
# on `git checkout`. `git bundle` can't ship a shallow repo (grafted
# parents), which is why this uses tar. Submodules are shipped by the
# UNCHANGED per-submodule bundle path in seed_repo_clone (a --depth
# clone of the parent does not populate .git/modules).
#
# Args: $1 = working branch to clone (resolved by the caller).
# Reads USER_REPO, FLY_APP, LEERIE_MACHINE_ID, LEERIE_SEED_DEPTH.
# Returns 0 on success, 1 on any failure (caller falls through to its
# own PAUSED-on-failure path). The machine-side submodule wiring is
# invoked by the caller after this returns, identically to the full path.
# ---------------------------------------------------------------------------
_seed_shallow_parent() {
  local _branch="$1" _tmp_shallow _hb_pid _seed_to="" _rc=0
  if [ -z "$_branch" ]; then
    remote_log "seed_repo: _seed_shallow_parent called without a branch"
    return 1
  fi

  # Throwaway shallow clone on the host. --no-local forces a real
  # object transfer (not a hardlink farm), so the resulting .git is a
  # self-contained shallow pack. Real tip hash is preserved (no
  # re-rooting), which keeps the host-side PR merge-base correct.
  _tmp_shallow="$(mktemp -d -t leerie-shallow.XXXXXX)" || return 1
  # shellcheck disable=SC2064
  trap "rm -rf '$_tmp_shallow'" RETURN
  if ! git clone --quiet --depth="$LEERIE_SEED_DEPTH" --no-local \
         --branch "$_branch" "file://$USER_REPO" "$_tmp_shallow/repo" 2>/dev/null; then
    remote_log "seed_repo: shallow clone (depth=$LEERIE_SEED_DEPTH) of $_branch failed"
    return 1
  fi

  if command -v _seed_timeout_prefix >/dev/null 2>&1; then
    _seed_to="$(_seed_timeout_prefix)"
  fi
  _seed_progress_bg "seed_repo (shallow parent .git)" &
  _hb_pid=$!
  # Tar ONLY .git (relative to the clone root) and pipe to the machine.
  # Same `sh -c 'cat > ...'` receiver + timeout wrapper as the bundle pipe.
  tar -C "$_tmp_shallow/repo" -cf - .git 2>/dev/null \
        | $_seed_to flyctl ssh console --quiet --app "$FLY_APP" \
            --machine "$LEERIE_MACHINE_ID" --pty=false \
            -C "sh -c 'cat > /tmp/leerie-seed-git.tar'" >/dev/null 2>&1
  _rc=${PIPESTATUS[1]}
  kill "$_hb_pid" 2>/dev/null || true
  wait "$_hb_pid" 2>/dev/null || true
  if [ "$_rc" -ne 0 ]; then
    if [ "$_rc" -eq 124 ] || [ "$_rc" -eq 137 ]; then
      remote_log "seed_repo: shallow .git pipe did not return within ${LEERIE_SEED_TIMEOUT_S:-600}s (rc=$_rc) — flyctl ssh console likely stalled"
    fi
    remote_log "seed_repo: failed to pipe shallow .git tar to machine"
    return 1
  fi
  return 0
}

# ---------------------------------------------------------------------------
# seed_repo_clone
#
# Ship the host's committed state to /work on the machine. Two parent-
# delivery transports:
#
#   - Full (default): `git bundle create - --all` for the parent piped
#     to the machine, which `git clone`s from the bundle file. Delivers
#     full history. The proven path for normal repos.
#   - Shallow (heavy repos, DESIGN §6 *Shallow seeding for heavy repos*):
#     when _seed_use_shallow says yes, a throwaway `git clone --depth=N`
#     is tarred (.git only) and piped, and the machine untars + checks
#     out. Ships a fraction of the bytes for deep-history repos.
#
# In BOTH cases the per-submodule bundle machinery is identical (a
# --depth parent clone does not populate .git/modules). Always wipes
# /work first — call this only on a fresh provision, never on resume.
#
# Why bundles/.git-tar instead of a working-tree tar or rsync: pack
# objects store tree entries as raw bytes; the receiving Linux git
# materializes filenames natively from those objects. macOS BSD tar's
# NFC→NFD filename normalization (the bug that triggered this whole
# rewrite) doesn't apply because filenames don't transit the wire as
# strings — only commit-hash refs and packed objects do. The shallow
# path preserves this by tarring .git ONLY (never the working tree).
# ---------------------------------------------------------------------------
seed_repo_clone() {
  _seed_repo_preflight || return 1

  # Ensure SSH cert is valid before the pipe. We deliberately do NOT
  # re-probe hallpass here: seed_auth just transferred ~15 MB of config
  # and ran a multi-minute plugin install over hallpass, so the channel
  # is demonstrably warm. The bundle pipe below has its own
  # LEERIE_SEED_TIMEOUT_S wrapper (rc 124/137 handled at line ~193),
  # which is the authoritative failure detector if hallpass did drop.
  # An extra probe here only manufactures false-positive failures and
  # confusing log lines — see the 2026-06-05 investigation.
  if command -v require_fly_ssh >/dev/null 2>&1; then
    if ! require_fly_ssh; then
      remote_log "seed_repo: Fly SSH setup failed; cannot seed repo"
      return 1
    fi
  fi

  # Decide parent transport once, up front, so the log line and both the
  # host-side pipe and the machine-side reconstruction agree. When shallow,
  # resolve the working branch here (the machine-side `git checkout` needs
  # it, and _seed_shallow_parent clones it).
  local _shallow=false _branch=""
  if _seed_use_shallow; then
    _branch="$(git -C "$USER_REPO" rev-parse --abbrev-ref HEAD 2>/dev/null)"
    if [ -z "$_branch" ] || [ "$_branch" = "HEAD" ]; then
      # Detached HEAD (or unresolvable) — fall back to the full-bundle path,
      # which ships all refs and doesn't need a named branch to check out.
      remote_log "seed_repo: detached/unresolvable HEAD; using full bundle (shallow needs a named branch)"
    elif ! _seed_branch_shallow_safe "$_branch"; then
      # Branch name contains a character that isn't shell-safe for the
      # machine-side `sh -c '…'` wrapper. Fall back to the full-bundle path
      # (which never interpolates the branch name) rather than risk a broken
      # or injected remote command.
      remote_log "seed_repo: branch '$_branch' has non-shell-safe characters; using full bundle instead of shallow"
    else
      _shallow=true
    fi
  fi
  if [ "$_shallow" = "true" ]; then
    remote_log "remote: seeding — shallow-cloning $USER_REPO ($_branch, .git > ${LEERIE_SEED_SHALLOW_THRESHOLD_MB}MB, depth=$LEERIE_SEED_DEPTH) + submodules to /work ..."
  else
    remote_log "remote: seeding — bundling $USER_REPO (parent + submodules) to /work ..."
  fi

  # Empty /work's CONTENTS but preserve the directory inode. A bare
  # `rm -rf /work && mkdir -p /work` replaces the inode, which leaves
  # any process whose cwd was /work (e.g. the ssh-console shell, the
  # detached orchestrator we'll launch later) holding a stale fd and
  # causes getcwd() to fail downstream. Common symptom: `shell-init:
  # error retrieving current directory` from sub-shells, and claude
  # version timing out (its node runtime tries to stat ".").
  #
  # Also reset the bundle/tar staging paths in case a prior paused run
  # left them behind.
  if ! flyctl ssh console --app "$FLY_APP" --machine "$LEERIE_MACHINE_ID" \
         --pty=false -C "sh -c 'find /work -mindepth 1 -maxdepth 1 -exec rm -rf {} + && chown leerie: /work && rm -rf /tmp/leerie-seed.bundle /tmp/leerie-seed-git.tar /tmp/leerie-subs && mkdir -p /tmp/leerie-subs'" >/dev/null 2>&1; then
    remote_log "seed_repo: failed to reset /work on machine $LEERIE_MACHINE_ID"
    return 1
  fi

  # 1. Deliver the parent repo's committed state to the machine.
  #    Shallow: tar-of-.git piped to /tmp/leerie-seed-git.tar (see
  #    _seed_shallow_parent). Full: `git bundle create - --all` piped
  #    to /tmp/leerie-seed.bundle.
  #
  #    The receiver MUST be wrapped in `sh -c '...'`; without it, the
  #    remote treats `>` as an argument to `cat` rather than a shell
  #    redirect and fails with `cat: invalid option -- 'c'`.
  if [ "$_shallow" = "true" ]; then
    _seed_shallow_parent "$_branch" || return 1
  else
    local _hb_pid_parent _seed_to="" _parent_rc=0
    # Symmetry with seed-auth.sh: defensive `command -v` so a standalone
    # sourcing path that hasn't loaded lib.sh still works (today
    # seed-repo.sh always sources lib.sh, but the guard keeps the two
    # bulk-pipe call sites uniform).
    if command -v _seed_timeout_prefix >/dev/null 2>&1; then
      _seed_to="$(_seed_timeout_prefix)"
    fi
    _seed_progress_bg "seed_repo (parent bundle)" &
    _hb_pid_parent=$!
    git -C "$USER_REPO" bundle create - --all 2>/dev/null \
          | $_seed_to flyctl ssh console --quiet --app "$FLY_APP" \
              --machine "$LEERIE_MACHINE_ID" --pty=false \
              -C "sh -c 'cat > /tmp/leerie-seed.bundle'" >/dev/null 2>&1
    _parent_rc=${PIPESTATUS[1]}
    kill "$_hb_pid_parent" 2>/dev/null || true
    wait "$_hb_pid_parent" 2>/dev/null || true
    if [ "$_parent_rc" -ne 0 ]; then
      if [ "$_parent_rc" -eq 124 ] || [ "$_parent_rc" -eq 137 ]; then
        remote_log "seed_repo: parent-bundle pipe did not return within ${LEERIE_SEED_TIMEOUT_S:-600}s (rc=$_parent_rc) — flyctl ssh console likely stalled"
      fi
      remote_log "seed_repo: failed to pipe parent bundle to machine"
      return 1
    fi
  fi

  # 2. Bundle each submodule recursively, pipe each into
  #    /tmp/leerie-subs/<flattened-displaypath>.bundle. The display-path
  #    flattening (`/` → `_`) is applied identically here and in the
  #    machine-side clone script (step 3) so both sides agree on the
  #    filename for nested submodules like `vendor/foo` → `vendor_foo`.
  #
  #    `git submodule foreach --recursive` walks every nested submodule.
  #    Inside the foreach, $displaypath is git's superproject-relative
  #    path. We export host-side env vars the foreach body needs.
  if [ -f "$USER_REPO/.gitmodules" ]; then
    local _hb_pid_subs
    _seed_progress_bg "seed_repo (submodule bundles)" &
    _hb_pid_subs=$!
    # shellcheck disable=SC2016  # single quotes are REQUIRED: $displaypath and
    # $bn are expanded by git's own `foreach` shell (and the innermost $bn by
    # the remote `sh -c`), never by this one
    if ! (
      cd "$USER_REPO" && \
      LEERIE_FLY_APP="$FLY_APP" LEERIE_MACHINE_ID="$LEERIE_MACHINE_ID" \
      git submodule --quiet foreach --recursive '
        bn="$(printf %s "$displaypath" | tr / _).bundle"
        # The `-C` arg must be `sh -c "..."` not bare `cat > ...`;
        # without the shell wrapper the remote treats `>` as an arg to
        # `cat`. Single-quote the inner script for the foreach context.
        git bundle create - --all 2>/dev/null \
          | flyctl ssh console --quiet --app "$LEERIE_FLY_APP" \
              --machine "$LEERIE_MACHINE_ID" --pty=false \
              -C "sh -c '\''cat > /tmp/leerie-subs/$bn'\''" >/dev/null 2>&1 \
          || { echo "leerie: seed_repo: failed to pipe submodule $displaypath bundle" >&2; exit 1; }
      '
    ); then
      kill "$_hb_pid_subs" 2>/dev/null || true
      wait "$_hb_pid_subs" 2>/dev/null || true
      remote_log "seed_repo: submodule bundling failed"
      return 1
    fi
    kill "$_hb_pid_subs" 2>/dev/null || true
    wait "$_hb_pid_subs" 2>/dev/null || true
  fi

  # 3. Machine-side: materialize /work, wire each submodule's URL to its
  #    bundle file (in `.git/config`, NOT `.gitmodules` — we never modify
  #    the committed file), then submodule update. Finally chown -R
  #    leerie: /work so the orchestrator (which runs as leerie) owns its
  #    working tree.
  #
  #    The parent-materialization PREFIX differs by transport; the
  #    submodule wiring + chown + cleanup are identical. We build the
  #    script with placeholder substitution (heredoc, quoted delimiter)
  #    to avoid the `'"'"'` quoting acrobatics — same pattern as
  #    _seed_one_inspect_dir_clone.
  #
  #    Full:    `git clone` from the bundle file (git recognizes the
  #             bundle header and treats the file like a remote).
  #    Shallow: untar .git into /work (inode-preserving; /work already
  #             emptied above), `git checkout -f` the branch to
  #             materialize the tree natively, drop the stale file://
  #             origin (inert on the machine, but removed so no worker
  #             attempts a dead fetch). `git checkout` recreates
  #             filenames natively → NFC-safe.
  local _parent_materialize _cleanup_tmp
  if [ "$_shallow" = "true" ]; then
    # $_branch is injected directly (not via a placeholder) — it passed
    # _seed_branch_shallow_safe above, so it contains only shell-safe
    # characters and cannot break the `sh -c '…'` wrapper or collide with a
    # placeholder token.
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
# `protocol.file.allow=always` is required for submodule update to
# accept file://-style local paths (bundle files on disk). Git 2.38+
# blocks `file` by default per CVE-2022-39253. We trust local paths
# because we just put them there.
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
# Clean up the tmpfiles — they served their purpose.
rm -rf __CLEANUP_TMP__
MACHINE_SCRIPT
)"
  # Substitute the placeholders. None of the values contain `|`, so the
  # bash `${var//from/to}` replacement is unambiguous. The branch name is
  # already baked into $_parent_materialize (shell-safe by the gate above),
  # so there is no branch-derived placeholder to collide with these tokens.
  _machine_script="${_machine_script//__PARENT_MATERIALIZE__/$_parent_materialize}"
  _machine_script="${_machine_script//__CLEANUP_TMP__/$_cleanup_tmp}"

  local _mode="clone-from-bundle"
  [ "$_shallow" = "true" ] && _mode="shallow-untar"
  if ! flyctl ssh console --app "$FLY_APP" --machine "$LEERIE_MACHINE_ID" \
         --pty=false -C "sh -c '$_machine_script'" >/dev/null 2>&1; then
    remote_log "seed_repo: machine-side reconstruction of /work failed ($_mode)"
    return 1
  fi

  remote_log "remote: repo seeded to /work"
}

# ---------------------------------------------------------------------------
# seed_repo_dirty
#
# Rsync the dirty/untracked delta from host to /work on the machine.
# Called by both the fresh-provision path (after `seed_repo_clone`
# delivers committed state, this fills in uncommitted edits) AND by
# `scripts/remote/re-seed.sh` (Phase 4) when the user pauses a run,
# edits files locally, and resumes.
#
# The file list, host-side, combines two sources:
#
#   (a) `git status --porcelain` output (modified-but-uncommitted
#       tracked files + untracked-not-ignored files), filtered to drop
#       defensive `.leerie/runs/*/worktrees/*` and `.git/*` entries (the
#       upstream behavior shouldn't surface these today, but the
#       exclude prevents silent clobbering if it ever does).
#
#   (b) Every file under the repo-local `.claude/` directory, force-
#       included even when `.gitignore` excludes it. Most repos list
#       `.claude/` in their gitignore but workers need its hooks,
#       agents, skills, commands, and settings to function. Bundles
#       can't carry gitignored content (only committed state), so this
#       step is where `.claude/` lands on the machine.
#
# Transport: rsync over `flyctl ssh console -C "rsync --server ..."`
# via the `fly_rsync_wrapper` helper in lib.sh. rsync preserves
# filename bytes verbatim (NFC stays NFC on the Linux receiver).
# ---------------------------------------------------------------------------
seed_repo_dirty() {
  _seed_repo_preflight || return 1

  # Compute the dirty set: modified tracked files + untracked files,
  # excluding git-ignored entries.
  local dirty_files
  dirty_files="$(git -C "$USER_REPO" status --porcelain 2>/dev/null \
                  | awk '
                      # Untracked files (including untracked dirs — trailing /)
                      /^\?\? / {
                        f = substr($0, 4)
                        gsub(/\/$/, "", f)
                        print f
                        next
                      }
                      # Modified/deleted/renamed/copied in worktree (column 2)
                      length($0) >= 2 && substr($0,2,1) != " " {
                        f = substr($0, 4)
                        if (index(f, " -> ")) {
                          f = substr(f, index(f, " -> ") + 4)
                        }
                        gsub(/\/$/, "", f)
                        print f
                      }
                  ')"

  # The dirty set is git status output. But we ALSO need to ship the
  # repo-local `.claude/` directory verbatim, even when it's gitignored
  # (the common case — most repos list `.claude/` in their gitignore).
  # Workers need its hooks/agents/skills/commands to function. The
  # bundle-based seed_repo_clone can't carry it (gitignored content
  # isn't in the bundle); this rsync step is where it lands.
  local claude_files=""
  if [ -d "$USER_REPO/.claude" ]; then
    claude_files="$(cd "$USER_REPO" && find .claude -type f 2>/dev/null)"
  fi

  if [ -z "$dirty_files" ] && [ -z "$claude_files" ]; then
    remote_log "remote: seeding — working tree is clean; no delta to sync"
    return 0
  fi

  # Combined count for the user-facing message (dirty entries +
  # .claude/ files). Count nonempty input lines from both sources.
  local file_count
  file_count="$(printf '%s\n%s\n' "$dirty_files" "$claude_files" \
                 | grep -c -v '^$' || true)"
  remote_log "remote: seeding — syncing $file_count dirty / forced-include file(s)..."

  # rsync the dirty delta. rsync preserves filename bytes verbatim
  # (NFC stays NFC on the Linux receiver), important for the same
  # reason seed_repo_clone uses bundles instead of tar.
  #
  # Defensive: the dirty set comes from `git status --porcelain` which
  # operates on the parent repo. Excluding .leerie/runs/*/worktrees/*
  # and .git/* structurally protects against any future change that
  # lets those paths surface in the porcelain output.
  if command -v require_fly_ssh >/dev/null 2>&1; then
    if ! require_fly_ssh; then
      remote_log "seed_repo: Fly SSH setup failed; cannot transfer delta"
      return 1
    fi
  fi
  # No hallpass re-probe here either: seed_repo_clone just bundled the
  # parent repo + every submodule through hallpass. The rsync transport
  # below will fail loudly if the channel actually dropped, and that is
  # the authoritative signal — see the symmetric comment in
  # seed_repo_clone() above.

  local rsync_rc=0
  local file_list wrapper
  file_list="$(mktemp -t leerie-reseed-list.XXXXXX)"
  wrapper="$(fly_rsync_wrapper "$FLY_APP")"

  # Build NUL-delimited file list. Two sources concatenated:
  #   (a) git porcelain dirty set, filtered (drop .git/, .leerie/runs/
  #       worktrees, editor-temp files, vanished entries, blanks)
  #   (b) repo-local .claude/ files (force-included even if gitignored)
  # The python filter applies to both; .claude/* entries from (b)
  # pass the filter trivially (no .git/ or worktree prefix).
  # shellcheck disable=SC2016  # the -c body below is Python, not shell — a
  # $ in it must reach the interpreter unexpanded
  printf '%s\n%s\n' "$dirty_files" "$claude_files" \
    | USER_REPO="$USER_REPO" python3 -c '
import os, re, sys

# Editor-temp filename patterns — these are per-process editor state
# (Emacs lock files, backups, Vim swap files) that should never be
# shipped. Emacs locks in particular are dangling symlinks of the form
# .#NAME -> user@host.pid:timestamp that vanish as buffers close;
# letting them into the rsync file list produces "stat: No such file
# or directory" failures (exit 23).
_VIM_SWAP_RE = re.compile(r"^\..*\.sw[a-z]$")
def _is_editor_temp(path: str) -> bool:
    base = path.rsplit("/", 1)[-1]
    return (
        base.startswith(".#")          # Emacs lock
        or base.endswith("~")          # backup
        or bool(_VIM_SWAP_RE.match(base))  # Vim swap
    )

repo_root = os.environ.get("USER_REPO", "")

for line in sys.stdin.read().splitlines():
    if not line:
        continue
    # .git/ and .leerie/ are coordination state, never transported here.
    # .git/ → bundle path creates it natively on the machine.
    # .leerie/ → host-side run state; the machine writes its own.
    # Exception: committed config files (.leerie/config.toml, .leerie/Dockerfile,
    # .leerie/.leerie-setup.sh) are repo-owned declarations that workers need.
    if line.startswith(".git/") or line == ".git":
        continue
    if line.startswith(".leerie/"):
        if line not in (".leerie/config.toml", ".leerie/Dockerfile",
                        ".leerie/.leerie-setup.sh"):
            continue
    elif line == ".leerie":
        continue
    # Defensive: drop worktree paths if they ever surface.
    if "/.leerie/runs/" in line and "/worktrees/" in line:
        continue
    if _is_editor_temp(line):
        continue
    # Drop entries that vanished between `git status` and now (Emacs
    # closing a buffer, a build tool cleaning a temp file, etc).
    # lexists() is True for a symlink whose target is missing — we
    # want to ship those (rsync -a preserves the link, target irrelevant
    # on the sender). It is False only when the path itself is gone.
    if repo_root and not os.path.lexists(os.path.join(repo_root, line)):
        continue
    sys.stdout.buffer.write(line.encode() + b"\x00")
' > "$file_list"

  # Capture stderr to a temp file so we can replay it on failure. Without
  # this the user sees only "rsync delta transfer failed (exit N)" with no
  # actionable diagnostic; exit 23 in particular has many causes (vanished
  # file, permission denied on the sender, missing path on the receiver,
  # quota) and is impossible to debug from the bare rc alone.
  local rsync_err
  rsync_err="$(mktemp -t leerie-reseed-err.XXXXXX)"
  LEERIE_FLY_APP="$FLY_APP" rsync -a -H \
    --from0 --files-from="$file_list" \
    -e "$wrapper" \
    "$USER_REPO/" "$LEERIE_MACHINE_ID:/work/" \
    >/dev/null 2>"$rsync_err"
  rsync_rc=$?

  if [ "$rsync_rc" -ne 0 ]; then
    remote_log "seed_repo: rsync delta transfer failed (exit $rsync_rc)"
    if [ -s "$rsync_err" ]; then
      while IFS= read -r _ln; do
        remote_log "  rsync: $_ln"
      done < "$rsync_err"
    fi
    rm -f "$file_list" "$wrapper" "$rsync_err"
    return 1
  fi
  rm -f "$rsync_err"

  # Chown the freshly-rsync'd paths. Keep it broad — rsync may have
  # touched directories above the dirty files too.
  if ! flyctl ssh console --app "$FLY_APP" --machine "$LEERIE_MACHINE_ID" \
         --pty=false -C "chown -R leerie: /work" >/dev/null 2>&1; then
    rm -f "$file_list" "$wrapper"
    remote_log "seed_repo: chown -R leerie: /work after delta failed"
    return 1
  fi

  rm -f "$file_list" "$wrapper"
  remote_log "remote: seeding complete"
}

# ---------------------------------------------------------------------------
# _seed_one_inspect_dir_clone <host> <remote>
#
# Bundle + machine-side-clone for a single git-repo inspect dir.
# Adapts seed_repo_clone's bundle pipeline (parent bundle + per-
# submodule bundles + machine-side `git clone`) but scoped to
# /tmp/leerie-inspect-<base>.{bundle,subs/} and a per-dir target
# instead of /work. <base> = basename($remote), e.g. "example-repo".
#
# Why: shipping the working tree via plain rsync over flyctl ssh
# fails for non-trivial repos (the v1 path: a 1.7 GB / 120k-file
# tree like example-repo with node_modules/.next/.pnpm-store hung
# indefinitely). The bundle is committed-state-only — gitignored
# build artifacts stay on the host where they belong. For a 1.7 GB
# example-repo working tree, the bundle is ~600 KB and ships in one
# pipe (measured 2026-06-02).
# ---------------------------------------------------------------------------
_seed_one_inspect_dir_clone() {
  local host="$1" remote="$2" base bundle_path subs_dir
  base="$(basename "$remote")"
  bundle_path="/tmp/leerie-inspect-${base}.bundle"
  subs_dir="/tmp/leerie-inspect-${base}-subs"

  remote_log "remote: bundling inspect-dir $host (parent + submodules) -> $remote ..."

  # Reset per-dir staging on the machine: remove the target, the
  # bundle file, and the subs dir if a prior partial attempt left
  # them. mkdir the subs dir so submodule bundle pipes can land.
  # Also mkdir -p the parent of $remote so we can land into it.
  if ! flyctl ssh console --app "$FLY_APP" --machine "$LEERIE_MACHINE_ID" \
         --pty=false -C "sh -c 'rm -rf ${remote} ${bundle_path} ${subs_dir} && mkdir -p $(dirname "${remote}") ${subs_dir}'" \
         >/dev/null 2>&1; then
    remote_log "seed_inspect_dirs: failed to reset staging for $remote"
    return 1
  fi

  # 1. Bundle the parent and pipe it to /tmp/leerie-inspect-<base>.bundle.
  #    `git bundle create - --all` packs every ref into one pack-format
  #    binary stream on stdout. The `sh -c '...'` wrapper around `cat`
  #    is load-bearing — bare `-C "cat > /tmp/..."` fails because flyctl
  #    treats `>` as an arg to `cat` (`cat: invalid option`).
  if ! git -C "$host" bundle create - --all 2>/dev/null \
        | flyctl ssh console --quiet --app "$FLY_APP" \
            --machine "$LEERIE_MACHINE_ID" --pty=false \
            -C "sh -c 'cat > ${bundle_path}'" >/dev/null 2>&1; then
    remote_log "seed_inspect_dirs: failed to pipe parent bundle for $host"
    return 1
  fi

  # 2. Bundle each submodule recursively, pipe each into
  #    <subs_dir>/<flattened-displaypath>.bundle. Displaypath-flattening
  #    (`/` → `_`) matches the machine-side clone script below so both
  #    sides agree on the filename for nested submodules.
  if [ -f "$host/.gitmodules" ]; then
    # shellcheck disable=SC2016  # single quotes are REQUIRED: $displaypath and
    # $bn are expanded by git's own `foreach` shell (and the innermost $bn by
    # the remote `sh -c`), never by this one
    if ! (
      cd "$host" && \
      LEERIE_FLY_APP="$FLY_APP" \
      LEERIE_MACHINE_ID="$LEERIE_MACHINE_ID" \
      LEERIE_SUBS_DIR="$subs_dir" \
      git submodule --quiet foreach --recursive '
        bn="$(printf %s "$displaypath" | tr / _).bundle"
        git bundle create - --all 2>/dev/null \
          | flyctl ssh console --quiet --app "$LEERIE_FLY_APP" \
              --machine "$LEERIE_MACHINE_ID" --pty=false \
              -C "sh -c '\''cat > $LEERIE_SUBS_DIR/$bn'\''" >/dev/null 2>&1 \
          || { echo "leerie: seed_inspect_dirs: failed to pipe submodule $displaypath bundle" >&2; exit 1; }
      '
    ); then
      remote_log "seed_inspect_dirs: submodule bundling failed for $host"
      return 1
    fi
  fi

  # 3. Machine-side: clone the parent bundle into $remote, wire each
  #    submodule's URL to its bundle file in .git/config (NOT
  #    .gitmodules — never modify the committed file), then submodule
  #    update --recursive. Finally chown -R leerie: $remote so the
  #    orchestrator (which runs as leerie) and inspect-bucket workers
  #    can read the tree. Clean up the staged bundle files.
  #
  #    protocol.file.allow=always is required for submodule update to
  #    accept file://-style local paths — git 2.38+ blocks `file` by
  #    default per CVE-2022-39253. We trust local paths because we
  #    just put them there.
  #
  #    The clone script is sent over `sh -c '...'` via flyctl's -C.
  #    We use envsubst-style replacement at host emit-time (sed) for
  #    the three paths so the inner sh -c script doesn't need any
  #    quoting acrobatics.
  local clone_script
  clone_script="$(cat <<'CLONE_SCRIPT'
set -e
git clone __BUNDLE_PATH__ __REMOTE__
cd __REMOTE__
if [ -f .gitmodules ]; then
  git submodule init
  git submodule status | awk "{print \$2}" | while read sm; do
    bn=$(printf %s "$sm" | tr / _).bundle
    if [ -f "__SUBS_DIR__/$bn" ]; then
      git config "submodule.$sm.url" "__SUBS_DIR__/$bn"
    fi
  done
  git -c protocol.file.allow=always submodule update --recursive
fi
chown -R leerie: __REMOTE__
rm -rf __BUNDLE_PATH__ __SUBS_DIR__
CLONE_SCRIPT
)"
  # Substitute the three paths. None of bundle_path / remote / subs_dir
  # contains a `|` so it's a safe sed delimiter.
  clone_script="${clone_script//__BUNDLE_PATH__/$bundle_path}"
  clone_script="${clone_script//__REMOTE__/$remote}"
  clone_script="${clone_script//__SUBS_DIR__/$subs_dir}"

  if ! flyctl ssh console --app "$FLY_APP" --machine "$LEERIE_MACHINE_ID" \
         --pty=false -C "sh -c '$clone_script'" >/dev/null 2>&1; then
    remote_log "seed_inspect_dirs: machine-side clone failed for $remote"
    return 1
  fi

  remote_log "remote: inspect-dir $remote seeded (bundle)"
}

# ---------------------------------------------------------------------------
# _seed_one_inspect_dir_dirty <host> <remote>
#
# Rsync the dirty/untracked delta for a single git inspect dir into
# $remote/ on the machine. Adapts seed_repo_dirty's porcelain-based
# file-list logic, retargeted from /work to $remote and with the
# .claude/ force-include step DROPPED — workers Read/Grep/Glob the
# inspect dir but don't run inside it, so its .claude/ (if any) is
# not load-bearing.
#
# Empty delta is fine and returns 0 (the bundle already shipped
# committed state, and there's nothing dirty to layer on top).
# ---------------------------------------------------------------------------
_seed_one_inspect_dir_dirty() {
  local host="$1" remote="$2"

  local dirty_files
  dirty_files="$(git -C "$host" status --porcelain 2>/dev/null \
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

  if [ -z "$dirty_files" ]; then
    return 0
  fi

  local file_count
  file_count="$(printf '%s\n' "$dirty_files" | grep -c -v '^$' || true)"
  remote_log "remote: inspect-dir $remote — syncing $file_count dirty file(s)..."

  local wrapper file_list rc=0
  wrapper="$(fly_rsync_wrapper "$FLY_APP")"
  file_list="$(mktemp -t leerie-inspect-dirty-list.XXXXXX)"

  printf '%s\n' "$dirty_files" \
    | python3 -c '
import sys
for line in sys.stdin.read().splitlines():
    if not line:
        continue
    if line.startswith(".git/") or line == ".git":
        continue
    sys.stdout.buffer.write(line.encode() + b"\x00")
' > "$file_list"

  LEERIE_FLY_APP="$FLY_APP" rsync -a -H \
    --from0 --files-from="$file_list" \
    -e "$wrapper" \
    "$host/" "$LEERIE_MACHINE_ID:$remote/" \
    >/dev/null 2>&1
  rc=$?

  if [ "$rc" -ne 0 ]; then
    rm -f "$file_list" "$wrapper"
    remote_log "seed_inspect_dirs: dirty rsync failed for $host -> $remote (exit $rc)"
    return 1
  fi

  # Re-chown to keep the dirty-rsync'd files leerie-owned. Broad-stroke
  # like seed_repo_dirty:390.
  if ! flyctl ssh console --app "$FLY_APP" --machine "$LEERIE_MACHINE_ID" \
         --pty=false -C "chown -R leerie: $remote" >/dev/null 2>&1; then
    rm -f "$file_list" "$wrapper"
    remote_log "seed_inspect_dirs: chown -R leerie: $remote after dirty rsync failed"
    return 1
  fi

  rm -f "$file_list" "$wrapper"
}

# ---------------------------------------------------------------------------
# _seed_one_inspect_dir_rsync_fallback <host> <remote>
#
# Plain rsync for non-git inspect dirs (a docs folder, a scratch dir
# with no .git/). This is what v1 did unconditionally; we keep it as
# the non-git fallback only. The git path uses bundle+clone instead
# (orders of magnitude faster on large trees).
# ---------------------------------------------------------------------------
_seed_one_inspect_dir_rsync_fallback() {
  local host="$1" remote="$2"
  remote_log "remote: rsync-seeding non-git inspect-dir $host -> $remote ..."

  local wrapper rc=0
  wrapper="$(fly_rsync_wrapper "$FLY_APP")"

  LEERIE_FLY_APP="$FLY_APP" rsync -a -H \
    -e "$wrapper" \
    "$host/" "$LEERIE_MACHINE_ID:$remote/" \
    >/dev/null 2>&1
  rc=$?

  if [ "$rc" -ne 0 ]; then
    rm -f "$wrapper"
    remote_log "seed_inspect_dirs: fallback rsync failed for $host -> $remote (exit $rc)"
    return 1
  fi

  if ! flyctl ssh console --app "$FLY_APP" --machine "$LEERIE_MACHINE_ID" \
         --pty=false -C "chown -R leerie: $remote" >/dev/null 2>&1; then
    rm -f "$wrapper"
    remote_log "seed_inspect_dirs: chown -R leerie: $remote (fallback) failed"
    return 1
  fi

  rm -f "$wrapper"
  remote_log "remote: inspect-dir $remote seeded (rsync)"
}

# ---------------------------------------------------------------------------
# seed_inspect_dirs
#
# Transport host-side --inspect-dir paths to /inspect/<basename> on
# the Fly machine. The launcher already rewrote each --inspect-dir
# CLI flag to /inspect/<basename> for the in-machine orchestrator
# (REWRITTEN_ARGS); this function makes those paths exist.
#
# Input: LEERIE_INSPECT_HOST_TARGETS env var, newline-separated records
# of "<host-path>\t<remote-target>". The launcher serializes its
# INSPECT_HOST_TARGETS bash array via _serialize_inspect_host_targets.
# Empty/unset means no inspect dirs — no-op return 0.
#
# Per inspect dir:
#   - If $host is a git repo: probe the machine for /inspect/<base>/.git.
#     If present (resume on an already-seeded run), just refresh the
#     dirty delta. Otherwise full bundle-clone, then dirty rsync.
#   - If $host is NOT a git repo (a docs folder, etc.): probe for
#     dir-existence-and-non-empty. If present, skip; otherwise plain
#     rsync (the v1 fallback).
#
# The bundle path is the same strategy seed_repo_clone uses for /work
# — see _seed_one_inspect_dir_clone's header for the perf rationale.
# ---------------------------------------------------------------------------
seed_inspect_dirs() {
  _seed_repo_preflight || return 1

  if [ -z "${LEERIE_INSPECT_HOST_TARGETS:-}" ]; then
    return 0
  fi

  if command -v require_fly_ssh >/dev/null 2>&1; then
    if ! require_fly_ssh "$FLY_APP"; then
      remote_log "seed_inspect_dirs: Fly SSH setup failed; cannot seed inspect dirs"
      return 1
    fi
  fi
  # No hallpass re-probe: by the time we get here seed_auth has done its
  # cold-start probe and seed_repo has bundled the parent + every
  # submodule across the same channel. Each transport below has its own
  # error path; the probe would only manufacture false positives. See
  # the comment in seed_repo_clone() for the full reasoning.

  # mkdir /inspect once + chown leerie: /inspect so per-dir clones/rsyncs
  # land under a leerie-owned parent.
  if ! flyctl ssh console --app "$FLY_APP" --machine "$LEERIE_MACHINE_ID" \
         --pty=false -C "sh -c 'mkdir -p /inspect && chown leerie: /inspect'" \
         >/dev/null 2>&1; then
    remote_log "seed_inspect_dirs: failed to mkdir /inspect on machine $LEERIE_MACHINE_ID"
    return 1
  fi

  local rc=0 record host remote
  while IFS= read -r record; do
    [ -z "$record" ] && continue
    host="${record%%$'\t'*}"
    remote="${record#*$'\t'}"
    if [ -z "$host" ] || [ -z "$remote" ] || [ "$host" = "$record" ]; then
      remote_log "seed_inspect_dirs: malformed record (no tab separator): $record"
      continue
    fi
    case "$remote" in
      /inspect/*) : ;;
      *)
        remote_log "seed_inspect_dirs: skipping non-/inspect target: $remote"
        continue
        ;;
    esac

    # Is the host path a git repo? Two probes — direct .git/ presence
    # (covers the normal case) and `git rev-parse --git-dir` (covers
    # worktrees / .git files).
    local is_git=false
    if [ -d "$host/.git" ] || git -C "$host" rev-parse --git-dir >/dev/null 2>&1; then
      is_git=true
    fi

    if $is_git; then
      # Resume probe: if /inspect/<base>/.git already exists on the
      # machine, the bundle was shipped on a prior run. Skip the
      # expensive clone, just refresh the dirty delta.
      if flyctl ssh console --app "$FLY_APP" --machine "$LEERIE_MACHINE_ID" \
           --pty=false -C "test -d $remote/.git" >/dev/null 2>&1; then
        remote_log "remote: inspect-dir $remote already present; refreshing dirty delta only"
        if ! _seed_one_inspect_dir_dirty "$host" "$remote"; then
          rc=1
          break
        fi
      else
        if ! _seed_one_inspect_dir_clone "$host" "$remote"; then
          rc=1
          break
        fi
        if ! _seed_one_inspect_dir_dirty "$host" "$remote"; then
          rc=1
          break
        fi
      fi
    else
      # Non-git inspect dir. Probe dir-exists-and-non-empty; if so,
      # assume a prior run shipped it and skip. Otherwise plain rsync.
      if flyctl ssh console --app "$FLY_APP" --machine "$LEERIE_MACHINE_ID" \
           --pty=false -C "sh -c 'test -d $remote && [ -n \"\$(ls -A $remote 2>/dev/null)\" ]'" \
           >/dev/null 2>&1; then
        remote_log "remote: inspect-dir $remote already present (non-git); skipping"
      else
        if ! _seed_one_inspect_dir_rsync_fallback "$host" "$remote"; then
          rc=1
          break
        fi
      fi
    fi
  done <<< "$LEERIE_INSPECT_HOST_TARGETS"

  return $rc
}

# ---------------------------------------------------------------------------
# seed_repo
#
# Used on fresh provisions. Two phases:
#   1. seed_repo_clone — bundles parent + submodules, machine clones from
#      bundles. Delivers committed state only.
#   2. seed_repo_dirty — rsync's the dirty/untracked delta plus
#      forced-in .claude/, completing the working tree state on the
#      machine.
#
# re-seed.sh still calls seed_repo_dirty directly when the user
# pauses + edits + resumes.
#
# Inspect dirs (/inspect/<basename> contents) are transported by
# seed_inspect_dirs, invoked by the launcher after seed_repo on fresh
# provision and after re_seed on resume.
# ---------------------------------------------------------------------------
seed_repo() {
  seed_repo_clone || return 1
  seed_repo_dirty
}
