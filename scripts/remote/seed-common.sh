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

# --- _seed_auth_tar_excludes -------------------------------------------------
# Echoes the `tar --exclude=...` flags (space-separated, one per excluded
# path) guarding git/ssh/gnupg auth material — which lives on the HOST per
# DESIGN §6 *Finalization* — from the staged `.claude`/`.claude.json`/
# `.gitconfig` tar both seed-auth.sh (Fly) and ec2-seed-auth.sh (EC2) ship
# to the remote machine. Single-owner list; both callers consume it via
# `$(_seed_auth_tar_excludes)` on an unquoted `tar` command line, same
# convention as `_seed_timeout_prefix`. No quoting in the emitted text —
# unquoted command substitution word-splits but never re-parses quote
# characters, so a literal `'` here would reach `tar` as part of the
# exclude pattern instead of being stripped.
_seed_auth_tar_excludes() {
  printf '%s' \
    "--exclude=.gitconfig --exclude=.gitconfig.local --exclude=.gitignore" \
    " --exclude=.gitignore_global --exclude=.git-credentials --exclude=.netrc" \
    " --exclude=.ssh --exclude=.gnupg --exclude=.config"
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

# --- _render_launch_prefix / _render_launch_suffix -------------------------
# Shared halves of the Python heredoc both the Fly and EC2 remote branches
# pipe over `flyctl ssh console` / `ec2_launch_detached` to spawn the
# detached in-machine orchestrator. The launcher's own launch heredocs
# (leerie's Fly and EC2 dispatch blocks) sandwich their own
# `child_env = dict(os.environ)` block between a call to
# `_render_launch_prefix` and a call to `_render_launch_suffix` — the
# child_env block itself stays per-call-site because its TZ/LEERIE_COMMIT
# variable names and the Fly-only Bedrock activation legitimately differ
# per runtime (and tests/test_bedrock_bearer_token.py's stray-${...} and
# backtick scans, plus tests/launcher_blocks.py's block splitter, are keyed
# on finding one such block per runtime — moving it here would make those
# scans unable to find or attribute it).
#
# Single definition site for the chown loop, the single-owner-per-run-dir
# advisory-flock probe, the subprocess.Popen invocation, and the
# 10-iteration poll-for-early-exit-then-write-pidfile loop, previously
# duplicated verbatim across both launch blocks.
#
# _render_launch_prefix args:
#   $1  runtime label ("fly" or "ec2") — spliced into the flock-probe's
#       "leerie resume <run_id> --runtime <label>" hint text only.
#   $2  JSON-encoded argv (built host-side via the same
#       `json.dumps(sys.argv[1:])` technique at both call sites — no
#       further encoding happens here).
# Prints Python source up through `pid_path = ...`, stopping just before
# the caller's own `child_env = dict(os.environ)` line.
#
# _render_launch_suffix takes no args. Prints Python source starting at
# the PATH fixup and continuing through the Popen call, the pidfile poll,
# and the pidfile write — run immediately after the caller's own
# `child_env[...] = ...` lines.
#
# NOTE: both are themselves unquoted heredocs (<<PY) — the same
# substitution hazards CLAUDE.md documents apply: every value substituted
# here must be JSON-encoded (never a raw "${VAR}"), and no comment in the
# fixed scaffold below may contain a stray ${...} or a balanced backtick
# pair.
_render_launch_prefix() {
  local runtime="$1"
  local argv_json="$2"
  cat <<PY
import fcntl, os, pwd, subprocess, sys, time
argv = ${argv_json}
run_id = argv[0]
orch_args = argv[1:]
run_dir = "/work/.leerie/runs/" + run_id
os.makedirs(run_dir, exist_ok=True)
# Make /work/.leerie, /work/.leerie/runs, and the run dir itself writable
# by leerie — os.makedirs created them as root (this wrapper runs as
# root via ssh-console). The orchestrator runs as leerie and may need
# to write into /work/.leerie/runs/<run-id>/.
leerie_pw = pwd.getpwnam("leerie")
for d in ("/work/.leerie", "/work/.leerie/runs", run_dir):
    try:
        os.chown(d, leerie_pw.pw_uid, leerie_pw.pw_gid)
    except OSError:
        pass
# Refuse to spawn a second orchestrator on the same run dir. The real
# enforcement is in State.__init__ (which acquires the same flock and
# dies with EXIT_LOCKED=75 if held). This probe is the fast-path: it
# avoids the cost of spawning a Python process that would just die in
# startup. Same primitive (advisory flock on run_dir) used at both
# layers. See DESIGN §6 *Single owner per run dir*.
_probe_fd = os.open(run_dir, os.O_RDONLY)
try:
    try:
        fcntl.flock(_probe_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(_probe_fd, fcntl.LOCK_UN)
    except BlockingIOError:
        sys.stderr.write("orchestrator already running for run " + run_id + "\\n")
        sys.stderr.write("Tail the existing run instead of spawning a duplicate:\\n")
        sys.stderr.write("  leerie resume " + run_id + " --runtime ${runtime}\\n")
        sys.exit(75)
finally:
    os.close(_probe_fd)
log_path = run_dir + "/orchestrator.log"
pid_path = run_dir + "/orchestrator.pid"
PY
}

_render_launch_suffix() {
  cat <<PY
# Ensure the launcher's PATH includes the mise-managed claude binary.
# (The Dockerfile installs claude into the mise-managed node prefix;
# the ssh-console/SSM session inherits a minimal PATH that may not
# include /usr/local/share/mise/...; add it defensively.)
extra_path = "/usr/local/share/mise/installs/node/lts-current/bin"
if extra_path not in child_env.get("PATH", ""):
    child_env["PATH"] = extra_path + ":" + child_env.get("PATH", "")
with open(log_path, "ab") as log_f:
    p = subprocess.Popen(
        ["python3", "/opt/leerie-image/orchestrator/leerie.py", "--no-push", *orch_args],
        stdin=subprocess.DEVNULL,
        stdout=log_f,
        stderr=log_f,
        start_new_session=True,
        # cwd=/work so the orchestrator's os.getcwd() returns /work
        # regardless of the launching shell's (possibly stale) cwd.
        cwd="/work",
        # Run as the leerie user — the image's USER leerie line only
        # applies to the entrypoint; ssh-console/SSM sessions land as
        # root, and any process we spawn here inherits root unless we
        # set this explicitly. The orchestrator needs to run as leerie
        # so claude finds creds at /home/leerie/.claude/.credentials.json
        # and so files it creates are owned by leerie (not root).
        user="leerie",
        group=leerie_pw.pw_gid,
        env=child_env,
    )
# Poll briefly before recording the pid. If this Popen lost the
# State.__init__ flock race against an already-running orchestrator
# for this run (the concurrent-spawn race described in DESIGN §6
# *Single owner per run dir*), the child exits 75 within
# milliseconds. Writing its pid to orchestrator.pid before the race
# resolves would overwrite the winning orchestrator's pid with a
# dead one — the stale-pid contagion that breaks resume's tail
# liveness check and finalize --force's safety belt.
#
# Budget: 2 s (10 x 0.2 s). The realistic time from Popen to the
# child's State.__init__ flock attempt is ~300-500 ms (Python
# interpreter cold start + leerie.py imports + main()'s pre-State
# config resolution); under disk pressure it could reach ~1 s.
# State.__init__ itself is microseconds — open the run_dir fd,
# attempt fcntl.flock, return or raise — but the relevant budget
# is the end-to-end time from Popen to that point. 2 s leaves
# comfortable headroom for both paths:
#   - Winner: child reaches State.__init__ and proceeds. Poll
#     times out, we write the pid.
#   - Loser: child reaches State.__init__ and exits 75. We
#     observe rc=75 and skip the pid write.
# The reader-side /proc cross-check in lib.sh and
# force-finalize.sh catches any residual edge case where the
# budget is exceeded on the loser path.
for _ in range(10):
    if p.poll() is not None:
        break
    time.sleep(0.2)
if p.poll() == 75:
    # Stillborn flock-loser — the winner still owns the run. Do not
    # touch the pid file. The caller's existing rc=75 short-circuit
    # pivots the user's resume to the live-orchestrator attach path
    # via container_rc=130.
    sys.exit(75)
with open(pid_path, "w") as pid_f:
    pid_f.write(str(p.pid) + "\n")
PY
}
