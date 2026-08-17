#!/usr/bin/env bash
# scripts/host-finalize.sh — host-side finalize block (push + PR creation).
#
# Sourced by the `leerie` launcher's normal post-run path and by the
# `leerie finalize <run-id>` fast-path. Both code paths share the same
# push/PR mechanics — the discovery of *which* run to finalize differs.
#
# Exports: host_finalize <run-dir>
#
# Inputs (env or args):
#   $1                — absolute path to .leerie/runs/<run-id>/
#   USER_REPO         — git repo root (set by launcher)
#   NO_VERIFY_PUSH    — "true" to pass --no-verify to git push (optional)
#
# Side effects:
#   - git push -u origin <run-branch>
#   - gh pr create
#   - update_run_json on the sidecar to record pushed_at / pr_url / errors
#
# Exit code semantics:
#   0 — success (push OK; PR may have failed non-fatally)
#   1 — push failed (no PR attempted)
#
# DESIGN §6 *Finalization*. The host owns this step because gh auth,
# ssh-agent, and Keychain are host-side; the Fly Machine cannot push.

# update_run_json (local jq-based helper). Kept here rather than in
# scripts/remote/lib.sh because that one uses python3, and the launcher
# already imports jq via the preflight; using jq keeps this file
# python-free and matches the in-place semantics of the original block.
_host_finalize_update_run_json() {
  local sidecar="$1"
  shift
  local tmp="$sidecar.tmp"
  local jq_filter='.'
  for kv in "$@"; do
    local key="${kv%%=*}"
    local val="${kv#*=}"
    if [ -z "$val" ]; then
      jq_filter="${jq_filter} | .${key} = null"
    else
      jq_filter="${jq_filter} | .${key} = \$${key}"
    fi
  done
  local args=()
  for kv in "$@"; do
    local key="${kv%%=*}"
    local val="${kv#*=}"
    [ -z "$val" ] || args+=(--arg "$key" "$val")
  done
  jq "${args[@]+"${args[@]}"}" "$jq_filter" "$sidecar" > "$tmp"
  mv "$tmp" "$sidecar"
}

# _host_finalize_pre_push_hook_present <repo> — mechanical hook-classification
# probe (N24). Resolves the hooks directory the same way git itself does
# (honoring `core.hooksPath` when set, falling back to git's own git-dir-
# relative default via `rev-parse --git-path hooks` so worktrees and
# non-standard `.git` layouts resolve correctly too) and tests for an
# executable `pre-push` file there. Returns 0 when an executable pre-push
# hook exists, 1 otherwise; prints nothing. This is
# structural — it never inspects push stderr text, unlike vendor-specific
# prose (husky's banner, "exit code 254") which is arbitrary and misses
# non-husky or newer-husky hook failures entirely.
_host_finalize_pre_push_hook_present() {
  local repo="$1"
  local hooks_dir
  hooks_dir="$(git -C "$repo" config --get core.hooksPath 2>/dev/null || true)"
  if [ -n "$hooks_dir" ]; then
    case "$hooks_dir" in
      /*) : ;;                              # already absolute
      *) hooks_dir="$repo/$hooks_dir" ;;    # relative to the worktree top
    esac
  else
    hooks_dir="$(git -C "$repo" rev-parse --git-path hooks 2>/dev/null || true)"
    # --git-path is always relative to the directory `-C` pointed at, not
    # to our own cwd (the launcher/repo may differ), so anchor it there.
    case "$hooks_dir" in
      /*|"") : ;;
      *) hooks_dir="$repo/$hooks_dir" ;;
    esac
  fi
  [ -n "$hooks_dir" ] && [ -x "$hooks_dir/pre-push" ]
}

# _host_finalize_is_auth_or_network_push_error <stderr> — is this push
# failure git's own auth/network problem, rather than a hook that failed?
#
# Anchored to how git FRAMES its failures, not to bare English phrases. The
# previous list matched fragments like `connection refused` and
# `authentication failed` anywhere in stderr, which any tool emits: measured,
# a pre-push hook that runs integration tests against Postgres, curls a
# registry, or hits a dev server tripped it and lost the `--no-verify` hint
# this function gates. Scored against a 23-case corpus (9 real `git push`
# failures reproduced against real git, 14 realistic hook outputs): the bare
# list classified 5/14 hook cases correctly, this one 14/14, both 9/9 on git.
#
# ONE arm: a phrase on a line git itself prefixes (`fatal:` / `remote:`).
# `error:` is deliberately NOT a prefix here — git uses it for `error: failed
# to push some refs`, which is present on EVERY failed push including hook
# failures, and Java emits `Error: Unable to access jarfile`.
#
# An earlier revision had a second arm matching an `ssh:` / `git@host:` line
# "only when git also reports it could not read the remote". That arm was
# PROVABLY DEAD and is gone: its companion condition is
# `^fatal: could not read from remote repository`, and the arm above already
# matches that exact line via `^fatal:` + `could not read from remote
# repository`. Every input satisfying arm 2 satisfied arm 1, which ran first
# — verified by running both over the ssh-family corpus cases. The companion
# line alone was doing the discriminating; the `ssh:` prefix contributed
# nothing. An ssh-transport push failure is still classified, by that same
# companion line (see the `connection-refused` / `publickey-denied` /
# `ssh-timeout` corpus cases, which pass on arm 1).
#
# Three alternatives carry a deliberate qualifier, because `-i` makes git's
# `fatal:` indistinguishable from a third-party `FATAL:`:
#   - `authentication failed for '` keeps the quote: git writes `Authentication
#     failed for '<url>'`, Postgres writes `FATAL: password authentication
#     failed for user "..."`.
#   - `permission denied \(publickey` keeps the paren for the same reason —
#     a bare `permission denied` matches Postgres's `FATAL: permission denied
#     for database "..."`, which is a hook failure, not a push failure.
#   - `unable to access '` keeps the quote: git always quotes the URL
#     (`fatal: unable to access 'https://…/': …`), while a hook can emit
#     `fatal: unable to access node: EACCES`.
#
# The bare transport phrases — `could not resolve host`, `connection
# refused|timed out`, `operation timed out`, `no route to host` — are
# deliberately NOT here. Behind the `^(fatal|remote):` anchor they are
# unreachable for real git, which emits them either on an `ssh:` line (no
# git prefix, so the anchor rejects it) or as the tail of an
# `unable to access '<url>':` line that this list already matches. Measured
# by ablating each alternative against the corpus: dropping all four keeps
# 9/9 on real git — including https connect-refused and DNS-failure, both
# still caught via `unable to access '` — while removing the false
# positives on a hook that emits `FATAL: could not resolve host: db.internal`
# or `FATAL: connection refused`. Ablating each surviving alternative in
# turn, FOUR are load-bearing for the corpus: `authentication failed for '`,
# `could not read from remote repository`, `unable to access '` (the sole
# matcher for the DNS-failure case, since dropping the bare transport phrases
# is what left it carrying that shape alone) and `repository .*not found`.
# The rest are kept as git's exact wording for shapes the corpus does not
# contain, and each is qualified enough that it cannot match third-party
# prose. `tests/test_host_finalize_hook_probe.py` re-derives that count from
# this regex and fails if this comment and the measurement disagree — the
# claim said "three" for one release because it was written from an ablation
# run against the PREVIOUS, wider alternative list and never re-run.
#
# PROCESS SUBSTITUTION, and both of the things it is not are deliberate.
#
# Not `printf … | grep -q`: `grep -q` exits at its first match and closes the
# pipe, so with more than a pipe buffer (64 KiB) still unread the writer dies
# of SIGPIPE and — under the `set -o pipefail` every caller of this file sets
# — the pipeline reports 141 even though grep MATCHED. Measured: a 1.19 MB
# stderr whose first line is a real credential failure classified as a hook
# failure, and the operator told to retry with `--no-verify`, which cannot fix
# credentials. A pre-push hook running a test suite reaches that size easily.
#
# Not a herestring either, which is what replaced the pipe first and moved the
# failure rather than removing it: bash backs a herestring larger than a pipe
# buffer with a temp file (measured — 32 KiB is a pipe, 64 KiB is
# sh-thd.XXXXXX, i.e. the SAME input range). It honours $TMPDIR and falls
# back to /tmp when that is unusable, so the dependency is on *some* writable
# temp dir. When the file cannot be created the redirection fails and the
# command returns 1, indistinguishable here from "grep did not match".
# Reproduced with /tmp as a full 256 KiB tmpfs: identical misclassification.
# That matters because on macOS /tmp shares the APFS container with $HOME, so
# the disk-full case N30 exists for IMPLIES a full /tmp.
#
# Process substitution needs no temp file, and `pipefail` never sees the
# writer because it is not part of a pipeline — so neither failure mode is
# reachable. Verified in the same full-/tmp container: large auth -> AUTH,
# small auth -> AUTH, large hook -> HOOK.
_host_finalize_git_framed_auth_or_network() {
  grep -qiE \
    "^(fatal|remote):.*(authentication failed for '|permission denied \(publickey|could not read (username|password) for|could not read from remote repository|terminal prompts disabled|unable to access '|repository .*not found)" \
    < <(printf '%s\n' "$1")
}

# Kept as a named wrapper even though it is now a pure pass-through: it is the
# name `host_finalize` calls, the name both docs describe, and the seam a
# second classification arm would return to. Collapsing it would rename a
# documented function to save one line.
_host_finalize_is_auth_or_network_push_error() {
  _host_finalize_git_framed_auth_or_network "$1"
}

# host_prepush_preflight <repo> <branch> — would this host's pre-push hook
# reject the push this run is going to end with?
#
# Everything above turns a hook rejection into a legible message. This turns
# it into one the operator gets BEFORE the run spends. The prediction is
# sound by construction rather than by luck: the hook measures the host
# checkout's working tree, and leerie never modifies that tree during a run
# (workers run in the container; the finalize rebase uses a disposable
# worktree), so a probe at t=0 and the real push at t=end see the same
# inputs. Measured on the run that motivated this: the host's manifests were
# rewritten at 18:46:10 and the run started at 18:48:14, so the defect that
# rejected the push 2h19m later was already present — and the same is true
# of all four earlier `pnpm: not found` rejections.
#
# `--dry-run` is what makes this safe AND representative:
#   - it runs the pre-push hook (verified against real git), and
#   - it creates no ref, locally or on the remote (verified: the remote's
#     ref list is unchanged after a dry-run to a brand-new branch name).
#
# The refspec is a NEW ref under leerie's own namespace, not the working
# branch, and that choice is load-bearing. Pushing an already-up-to-date
# branch still runs the hook but hands it an EMPTY stdin (verified), so a
# hook that iterates the refs git feeds it does nothing and exits 0 — a
# false pass. A new ref reproduces exactly the ref line finalize will
# produce: `<local> <sha> refs/heads/leerie/runs/... 0000…` (all-zero old
# sha = "new branch").
#
# Silent (rc 0) when: no pre-push hook, the dry-run passes, or the failure
# is git's own auth/network problem — the last of those is not what this
# probe is for, and the real push reports it properly. Returns 1 and prints
# a warning when a hook is present and rejected the dry-run. The caller
# decides whether that is fatal; the launcher treats it as a warning,
# because a hook can legitimately fail on a tree the run is about to fix.
host_prepush_preflight() {
  local repo="$1" branch="$2"
  [ -n "$repo" ] && [ -n "$branch" ] || return 0
  _host_finalize_pre_push_hook_present "$repo" || return 0

  local _o _e rc=0 out err
  _o="$(mktemp 2>/dev/null || true)"
  _e="$(mktemp 2>/dev/null || true)"
  if [ -z "$_o" ] || [ -z "$_e" ]; then
    rm -f "$_o" "$_e" 2>/dev/null || true
    return 0   # no temp dir: skip the probe rather than lose its output
  fi
  # GIT_TERMINAL_PROMPT=0 so an HTTPS remote with no cached credential fails
  # fast instead of blocking run start on a username prompt — a check meant
  # to save the operator time must never be the thing that hangs. It needs no
  # new branch below: git then emits `fatal: could not read Username for
  # '…': terminal prompts disabled`, and the classifier already carries both
  # of those alternatives on a `^fatal:` line, so this lands in the
  # auth/network arm and returns 0 silently. Deliberately not also forcing
  # ssh's BatchMode: an ssh passphrase prompt moved earlier is arguably an
  # improvement, and BatchMode would break agent-less setups that work today.
  GIT_TERMINAL_PROMPT=0 git -C "$repo" push --dry-run origin \
      "$branch:refs/heads/leerie/runs/preflight-probe" \
      >"$_o" 2>"$_e" || rc=$?
  out="$(cat "$_o")"; err="$(cat "$_e")"
  rm -f "$_o" "$_e" 2>/dev/null || true
  [ "$rc" -eq 0 ] && return 0
  # Same split as the push path: classify on stderr only.
  if _host_finalize_is_auth_or_network_push_error "$err"; then
    return 0
  fi

  local combined="$err"
  [ -n "$out" ] && combined="$err
--- pre-push hook output (stdout) ---
$out"
  echo "leerie: warning: this repository's \`pre-push\` hook already fails on your" >&2
  echo "  checked-out tree (\`$branch\`), before this run has changed anything." >&2
  echo "  git runs pre-push against the WORKING TREE, not against the commits being" >&2
  echo "  pushed, and leerie never checks out the run branch — so unless you fix this," >&2
  echo "  finalize will reject the push after the run has already been paid for." >&2
  echo "  Probed with: git push --dry-run (no ref was created, nothing was pushed)." >&2
  # Say so when the tail is a tail. The push path prints a marker for the
  # same reason; there is no run.json to point at here, but silently cutting
  # output and presenting it as complete is the part worth avoiding.
  local shown
  shown="$(printf '%s' "$combined" | tail -c 1500)"
  if [ "$shown" != "$combined" ]; then
    echo "  Hook output (last 1500 bytes):" >&2
  else
    echo "  Hook output:" >&2
  fi
  printf '%s\n' "$shown" | sed 's/^/    /' >&2
  echo "  Fix the hook's complaint, or run with --no-verify to bypass hooks at push time." >&2
  echo "  Set LEERIE_SKIP_PREPUSH_PREFLIGHT=1 to skip this probe." >&2
  return 1
}

host_finalize() {
  local run_dir="$1"
  if [ -z "$run_dir" ] || [ ! -d "$run_dir" ]; then
    echo "host_finalize: missing or invalid <run-dir>: $run_dir" >&2
    return 1
  fi

  local run_json="$run_dir/run.json"
  local state_json="$run_dir/state.json"
  if [ ! -f "$run_json" ]; then
    echo "host_finalize: no run.json at $run_json" >&2
    return 1
  fi

  # Honor run.json.no_push (--no-push or no-work short-circuit).
  if [ "$(jq -r '.no_push // false' "$run_json")" = "true" ]; then
    echo "[leerie] finalize: run.json has no_push=true; skipping push + PR" >&2
    return 0
  fi

  # Early idempotency short-circuit (DESIGN §6 *Finalization*). `pushed_at`
  # records *that* a push happened, not *what* it pushed — a finalize that
  # fired mid-integration (a mid-wave die() stamped finished_at early) can
  # leave pushed_at set on a PARTIAL branch. So this is tip-aware: only
  # no-op when origin ALREADY matches the local run-branch tip (the
  # genuinely-pushed case). Doing this *before* the completion gate
  # preserves the invariant that re-finalizing an already-pushed run is
  # always a safe no-op — even if state.json shows a stale/incomplete
  # wave count (a resume artifact, or a run pushed under old semantics).
  # If pushed_at is set but origin is BEHIND local (a partial prior push),
  # we deliberately fall through to the completion gate below, so the
  # re-push is still gated on completed_waves == len(waves). We resolve the
  # branch here (duplicated with the block below) rather than reordering
  # the whole function, keeping the change surgical.
  if [ -n "$(jq -r '.pushed_at // ""' "$run_json")" ]; then
    local _rb _lt _ot
    _rb="$(jq -r '.branch // ""' "$run_json")"
    if [ -n "$_rb" ] && git -C "$USER_REPO" rev-parse --verify "refs/heads/$_rb" >/dev/null 2>&1; then
      # `|| true`: under the launcher's `set -euo pipefail`, a failing
      # `ls-remote` (no origin remote, or origin lacks the ref — the exact
      # partial-push shape) would otherwise abort finalize via pipefail.
      _lt="$(git -C "$USER_REPO" rev-parse "refs/heads/$_rb" 2>/dev/null || true)"
      _ot="$(git -C "$USER_REPO" ls-remote origin "refs/heads/$_rb" 2>/dev/null | cut -f1 || true)"
      if [ -n "$_ot" ] && [ "$_lt" = "$_ot" ]; then
        echo "[leerie] finalize: run already pushed (origin up to date); nothing to do" >&2
        return 0
      fi
      # Only fall through to re-push when the re-push would fast-forward —
      # i.e. origin's tip is a strict ancestor of the local tip. A DIVERGED
      # origin (has commits local lacks) is NOT a partial push we can
      # safely fast-forward; short-circuit it here rather than letting the
      # plain `git push` below reject it into the push_error path. Origin
      # absent (`_ot` empty) is treated as behind → re-push creates the ref.
      if [ -n "$_ot" ] && \
         ! git -C "$USER_REPO" merge-base --is-ancestor "$_ot" "$_lt" 2>/dev/null; then
        echo "[leerie] finalize: run already pushed; origin has diverged from the local run branch — not re-pushing (resolve manually)" >&2
        return 0
      fi
    fi
    # pushed_at set but origin strictly behind (or absent) → partial push
    # we can fast-forward. Fall through to the completion gate + re-push.
  fi

  # Completion gate (DESIGN §6 *finished_at is a discovery sentinel, not a
  # completion signal*). run.json's finished_at is stamped by the die-path
  # SystemExit handler on ANY mid-wave abort (needed for run discovery), so
  # it does NOT mean the run's waves all integrated. Pushing such a run
  # opens a PR containing only the waves that finished before the crash
  # (the PR-#22 incident). This is the single chokepoint every host-side
  # push path funnels through (the auto-finalize block, the finalize
  # verb, and Fly decide_teardown all call host_finalize), so gating here
  # covers them all. The signal lives in state.json (run.json never carries
  # completed_waves/waves); this mirrors _derive_run_status case 6½.
  # Fail-open: if state.json is absent or its wave fields are non-numeric,
  # do NOT block — a legitimately complete run must never be refused over a
  # missing/unreadable file. The cleared-but-empty terminal state
  # (waves==[]) sets no_push=true and already returned above; even if it
  # reached here, `0 < len([])` is false, so it is not blocked.
  if [ -f "$state_json" ]; then
    local _no_work _completed _wave_total
    _no_work="$(jq -r '.no_work_required // false' "$state_json" 2>/dev/null)"
    _completed="$(jq -r '.completed_waves // 0' "$state_json" 2>/dev/null)"
    _wave_total="$(jq -r '.waves | length' "$state_json" 2>/dev/null)"
    case "$_completed:$_wave_total" in
      *[!0-9:]*) : ;;  # non-numeric (jq error / null) → fail-open, skip gate
      *)
        if [ "$_no_work" != "true" ] && [ "$_completed" -lt "$_wave_total" ]; then
          echo "leerie: error — refusing to finalize run ${run_dir##*/}: only" \
               "$_completed of $_wave_total waves integrated (run crashed" \
               "mid-wave). Resume to finish: leerie resume ${run_dir##*/}" >&2
          return 1
        fi
        ;;
    esac
  fi

  local run_id run_branch working_branch pr_base_branch
  run_id="$(basename "$run_dir")"
  run_branch="$(jq -r '.branch // ""' "$run_json")"
  working_branch="$(jq -r '.working_branch // ""' "$run_json")"
  if [ -z "$run_branch" ] || [ -z "$working_branch" ]; then
    echo "leerie: error — run.json at $run_json is missing branch info." >&2
    echo "  Skipping push + PR. Push the run branch manually if it exists." >&2
    return 1
  fi

  # pr_base_branch (IMPLEMENTATION.md "PR base branch override") is the
  # actual PR base; working_branch stays the diff fork-point regardless.
  # Falls back to working_branch when absent — older runs finalized before
  # this field existed, or a run where no override was given (the
  # orchestrator itself defaults pr_base_branch to working_branch at run
  # start, so this fallback is mostly a defense-in-depth for pre-existing
  # run.json files rather than something a fresh run ever hits).
  pr_base_branch="$(jq -r '.pr_base_branch // ""' "$run_json")"
  [ -z "$pr_base_branch" ] && pr_base_branch="$working_branch"

  # Defense-in-depth: a run branch named in run.json that does not
  # exist locally cannot be pushed. This shape is legitimate for the
  # cleared-but-empty terminal state (DESIGN §8 — no setup-run.sh
  # ran, so no branch was created). Treat it as a no-op rather than
  # attempting a `git push` that will fail with `src refspec ... does
  # not match any`. Upstream callers (fetch-branch.sh's stripper and
  # the finalize stripper in `leerie`) already preserve no_push=true
  # for this case; this guard backstops them.
  if ! git -C "$USER_REPO" rev-parse --verify "refs/heads/$run_branch" >/dev/null 2>&1; then
    echo "[leerie] finalize: run branch $run_branch absent locally; treating as no-op" >&2
    return 0
  fi

  # Empty-run-branch guard (DESIGN §6 *Finalization*). Refuse to push a run
  # branch that has no commits beyond the working branch — pushing it and
  # then calling `gh pr create` fails with "No commits between <base> and
  # <branch>", the exact failure this guard exists to convert into an
  # actionable message. `finalize.sh` (the in-container finalize) already
  # has this check, but a run that reached the host push path with an
  # un-integrated branch (e.g. via a died in-container finalize that the
  # resume completion guard mistook for success) never re-ran it — so the
  # host push path needs its own copy as defense in depth. The base is the
  # working branch (the diff fork-point), matching finalize.sh; when the
  # working branch is unresolvable, skip the check rather than block a push.
  if [ -n "$working_branch" ] && \
     git -C "$USER_REPO" rev-parse --verify "refs/heads/$working_branch" >/dev/null 2>&1; then
    local _ahead
    _ahead="$(git -C "$USER_REPO" rev-list --count "$working_branch..$run_branch" 2>/dev/null || echo 0)"
    if [ "$_ahead" = "0" ]; then
      _host_finalize_update_run_json "$run_json" \
        "push_error=run branch $run_branch has no commits beyond $working_branch — nothing to push"
      echo "leerie: error: run branch \`$run_branch\` has no commits beyond \`$working_branch\` — refusing to push an empty branch." >&2
      echo "  This usually means the run's waves were never integrated into the run branch." >&2
      echo "  The per-subtask work (if any) is on the leerie/subtasks/<run-id>/* branches." >&2
      echo "  Inspect and recover:" >&2
      echo "    git -C $USER_REPO branch --list 'leerie/subtasks/*'" >&2
      echo "    git -C $USER_REPO log --oneline $working_branch..$run_branch" >&2
      return 1
    fi
  fi

  # --- best-effort rebase onto the latest base (DESIGN §6 *Finalization*
  # "Rebase-onto-base before push") ---------------------------------------
  # A run whose base advanced while it ran opens a PR that conflicts at
  # merge time or reviews against a stale diff. This is strictly
  # best-effort: every branch of this logic (worktree-add failure, a
  # successful rebase, a resolved conflict, an aborted rebase) falls
  # through to the push below unchanged — never blocks or pauses finalize.
  #
  # This is a scoped, fully-agentic exception to §12 ("prompts are
  # advisory, code enforces"): the `rebaser` worker (invoked via
  # `run_rebaser`, the same host-side python3 seam `./leerie
  # config --recapture` uses for `run_recapture_deps`) does the ENTIRE
  # rebase workflow itself — fetch, rebase, per-hunk conflict resolution,
  # and the abort-if-irreconcilable judgment call — rather than each
  # mechanical step being coded here with only conflict-resolution content
  # delegated to a worker. `run_rebaser` mechanically re-verifies the
  # worker's claimed outcome before returning (see
  # `check_rebaser_worktree_state` in orchestrator/leerie.py) — this shell
  # function trusts the returned `status` as-is.
  # rebase_diagnosis_note is folded into pr_body once it's composed in step 2
  # below (pr_body does not exist yet at this point in the function).
  local rebase_diagnosis_note=""
  if git -C "$USER_REPO" rev-parse --verify "refs/heads/$pr_base_branch" \
       >/dev/null 2>&1 || \
     git -C "$USER_REPO" ls-remote --exit-code --heads origin \
       "$pr_base_branch" >/dev/null 2>&1; then
    local _rebase_scratch _rebase_worktree
    _rebase_scratch="$(mktemp -d)"
    _rebase_worktree="$_rebase_scratch/rebase-${run_id}"
    if git -C "$USER_REPO" worktree add "$_rebase_worktree" "$run_branch" \
         >/dev/null 2>&1; then
      echo "[leerie] finalize: attempting rebase of $run_branch onto $pr_base_branch" >&2
      # Write the python3 seam to a script file rather than a heredoc so the
      # invocation below stays a plain command (no command substitution), which
      # keeps it parseable under bash 3.2 (macOS's /bin/bash; see CLAUDE.md
      # "must run on bash 3.2") regardless of how the redirections are laid out.
      #
      # THE VERDICT GETS ITS OWN CHANNEL — argv[9], a file — and is NEVER
      # printed to stdout. `run_rebaser` calls `claude_p`, whose `log()` writes
      # the worker's whole progress trace to **stdout** (orchestrator/leerie.py
      # `log()` is a bare `print(..., flush=True)`). Capturing stdout here and
      # feeding it to `jq` therefore fed `jq` a few hundred lines of log text
      # followed by the JSON, so `jq` returned rc 5 and every run fell into the
      # `*)` arm below: measured, `rebase_disposition_status` was `unusable` in
      # 9 of 9 runs that ever reached the rebaser, i.e. the `rebased` and
      # `irreconcilable|failed` arms had NEVER executed. A run whose rebaser
      # returned a perfectly valid `{"status":"failed", ...}` with a full
      # conflict diagnosis had that diagnosis silently dropped instead of folded
      # into the PR body. Keeping the two on separate channels is the fix; the
      # log stream now reaches the operator's terminal (via stderr) instead of
      # being swallowed by a command substitution.
      local _rebaser_py="$_rebase_scratch/rebaser.py"
      local _rebaser_out="$_rebase_scratch/rebaser.json"
      cat > "$_rebaser_py" <<'PY'
import json, sys, importlib.util, pathlib

leerie_root, repo_root, run_id, worktree, run_branch, working_branch, \
    pr_base_branch, orch_path, out_path = (
        pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2]), sys.argv[3],
        pathlib.Path(sys.argv[4]), sys.argv[5], sys.argv[6], sys.argv[7],
        pathlib.Path(sys.argv[8]), pathlib.Path(sys.argv[9]),
    )

spec = importlib.util.spec_from_file_location("leerie_orch", orch_path)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

result = m.run_rebaser(leerie_root, repo_root, run_id, worktree,
                        run_branch, working_branch, pr_base_branch)
# The verdict goes to its own file, never stdout: stdout carries the worker's
# log stream (leerie's `log()` prints there), and mixing them is what made this
# unparseable for every run before this fix.
out_path.write_text(json.dumps(result))
PY
      local _rebaser_json="" _rebaser_rc=0
      # The worker's log stream goes to the operator's terminal (stderr) and the
      # verdict is read back from its own file — but the invocation stays inside
      # a SUBSHELL. That is load-bearing, not cosmetic: callers source this file
      # under `set -euo pipefail`, and the old `$( … )` capture happened to
      # contain any `set -u` abort (e.g. an unset LEERIE_STATE_HOST_DIR, which
      # is genuinely unset on some paths) inside the substitution's subshell,
      # where `|| _rebaser_rc=$?` absorbed it. Running the command directly in
      # the current shell instead makes that same abort kill `host_finalize`
      # outright — measured, it broke 20 of 32 tests in
      # tests/test_host_finalize_sh.py, none of them about the rebase. `( … )`
      # restores the containment without restoring the shared channel.
      ( python3 "$_rebaser_py" "$LEERIE_STATE_HOST_DIR" \
          "$USER_REPO" "$run_id" "$_rebase_worktree" "$run_branch" \
          "$working_branch" "$pr_base_branch" \
          "$LEERIE_REPO/orchestrator/leerie.py" "$_rebaser_out" \
          >&2 ) || _rebaser_rc=$?
      if [ -f "$_rebaser_out" ]; then
        _rebaser_json="$(cat "$_rebaser_out" 2>/dev/null || true)"
      fi

      local _rebaser_status=""
      if [ "$_rebaser_rc" -eq 0 ] && [ -n "$_rebaser_json" ]; then
        _rebaser_status="$(printf '%s' "$_rebaser_json" | jq -r '.status // ""' 2>/dev/null || true)"
      else
        echo "[leerie] finalize: rebaser python seam failed (rc=$_rebaser_rc); skipping rebase" >&2
        echo "$_rebaser_json" >&2
      fi

      # Fetch the worktree's HEAD into a scratch ref BEFORE removing the
      # worktree — git refuses to fetch into a branch checked out in a
      # worktree, and the run branch is exactly that inside
      # $_rebase_worktree, so the fetch must land somewhere else first and
      # only update the real branch ref once the worktree (which pins the
      # checkout) is gone. Done unconditionally (regardless of status) so a
      # "rebased" claim always has a scratch-ref snapshot available; unused
      # scratch refs are deleted below.
      local _rebase_scratch_ref="refs/leerie-rebase-scratch/${run_id}"
      git -C "$USER_REPO" fetch "$_rebase_worktree" \
        "HEAD:$_rebase_scratch_ref" --force >/dev/null 2>&1 || true
      git -C "$USER_REPO" worktree remove --force "$_rebase_worktree" \
        >/dev/null 2>&1 || true
      rm -rf "$_rebase_scratch" 2>/dev/null || true

      case "$_rebaser_status" in
        rebased)
          # Advance the run branch's local ref to the scratch-ref snapshot,
          # then advance working_branch to the fresh base — otherwise a
          # later working_branch..run_branch diff (rev_range / DIFF_BASE)
          # would silently pick up the base's own unrelated commits (the
          # PROVEN pitfall: git rebase --onto replays only the old range,
          # so the stale working_branch is no longer the merge-base of the
          # rebased branch).
          if git -C "$USER_REPO" rev-parse --verify \
               "$_rebase_scratch_ref" >/dev/null 2>&1 && \
             git -C "$USER_REPO" update-ref \
               "refs/heads/$run_branch" "$_rebase_scratch_ref"; then
            working_branch="origin/$pr_base_branch"
            _host_finalize_update_run_json "$run_json" \
              "working_branch=$working_branch"
            echo "[leerie] finalize: rebased $run_branch onto $pr_base_branch; working_branch now $working_branch" >&2
          else
            echo "[leerie] finalize: rebaser reported 'rebased' but fetch-back failed; pushing original branch" >&2
          fi
          ;;
        irreconcilable|failed)
          local _diagnosis
          _diagnosis="$(printf '%s' "$_rebaser_json" | jq -r '.diagnosis // ""' 2>/dev/null || true)"
          if [ -n "$_diagnosis" ] && [ "$_diagnosis" != "null" ]; then
            rebase_diagnosis_note="

## ⚠ Rebase onto \`$pr_base_branch\` was not applied

$_diagnosis"
          fi
          echo "[leerie] finalize: rebase not applied ($_rebaser_status); pushing $run_branch as-is" >&2
          ;;
        *)
          # The one artifact that would identify why the rebase degraded is
          # $_rebaser_json — don't discard it. Truncate to keep the sidecar
          # and stderr bounded (the payload can carry a full worker
          # transcript on a crash) and record the jq parse status alongside
          # it, since a non-zero jq rc here means the JSON itself was
          # unparseable rather than merely missing `.status`.
          #
          # `tail`, not `head`: a JSON object's discriminating fields sit at
          # its END as often as its start, and when this arm fires because the
          # payload is malformed the tail is where the truncation/corruption
          # shows. The previous `head -c 2000` preserved 2000 bytes of whatever
          # came first and dropped the rest — which, while the verdict shared a
          # channel with the log stream, meant it preserved 2000 bytes of pure
          # log noise and discarded the JSON entirely, in all 9 runs that ever
          # reached this arm.
          local _rebaser_json_trunc _rebaser_jq_rc=0
          printf '%s' "$_rebaser_json" | jq -e '.status // ""' >/dev/null 2>&1 || _rebaser_jq_rc=$?
          _rebaser_json_trunc="$(printf '%s' "$_rebaser_json" | tail -c 2000)"
          echo "[leerie] finalize: rebaser returned no usable status; pushing $run_branch as-is" >&2
          echo "[leerie] finalize: rebaser raw payload (truncated, jq_rc=$_rebaser_jq_rc): $_rebaser_json_trunc" >&2
          _host_finalize_update_run_json "$run_json" \
            "rebase_disposition_status=unusable" \
            "rebase_disposition_jq_rc=$_rebaser_jq_rc" \
            "rebase_disposition_raw_json=$_rebaser_json_trunc"
          ;;
      esac
      git -C "$USER_REPO" update-ref -d "$_rebase_scratch_ref" \
        >/dev/null 2>&1 || true
    else
      rm -rf "$_rebase_scratch" 2>/dev/null || true
      echo "[leerie] finalize: could not create rebase worktree; skipping rebase" >&2
    fi
  fi

  # Note the re-push (DESIGN §6 *Finalization*). If pushed_at is set and we
  # reached here, the early short-circuit above already ruled out both the
  # equal-tips no-op AND the diverged-origin case — so origin is a strict
  # ancestor of (or absent vs) local: a prior finalize pushed a PARTIAL
  # branch. We have now PASSED the completion gate, so a re-push publishes
  # a complete branch, and the push below is guaranteed to fast-forward.
  # pushed_at stays set, so the chain wave-skip signal (which reads the
  # field, not the tip) is unaffected.
  if [ -n "$(jq -r '.pushed_at // ""' "$run_json")" ]; then
    echo "[leerie] finalize: run marked pushed but origin is not up to date (partial prior push); re-pushing" >&2
  fi

  # --- step 1: push -----------------------------------------------------
  local push_args=(git -C "$USER_REPO" push -u origin "$run_branch")
  [ "${NO_VERIFY_PUSH:-false}" = "true" ] && push_args+=(--no-verify)

  echo "[leerie] finalize: pushing $run_branch to origin$([ "${NO_VERIFY_PUSH:-false}" = "true" ] && echo " (--no-verify)")" >&2
  # Capture the push's two streams SEPARATELY, because the two
  # consumers below want different things and merging them breaks one.
  #
  # The CLASSIFIER must keep seeing stderr alone. git forwards a pre-push
  # hook's stdout to git's own stdout, and a hook that refreshes submodules
  # or runs `git ls-remote` prints git's own `fatal:`/`remote:`-framed lines
  # there — folding stdout into the classified blob flips a hook failure to
  # "auth/network" and suppresses the `--no-verify` hint that would have
  # helped. Measured against this file's own classifier: 3 of 3 adversarial
  # hook shapes flip, while real `tsc`/`vitest` output does not. Keeping the
  # streams apart leaves the committed 23-case corpus score unchanged BY
  # CONSTRUCTION rather than by re-measurement.
  #
  # The OPERATOR needs stdout. `tsc` and `biome` write diagnostics there
  # (jest and vitest use stderr, which is why this went unnoticed), so
  # discarding it recorded a `push_error` of two pnpm deprecation warnings
  # for a push whose real cause was 13 lines of TS2307 — undiagnosable from
  # leerie's own output, three misdiagnoses, on a $57 run (barnacle,
  # 2026-08-17).
  #
  # `push_hook_out` holds the hook's stdout WITHOUT the section marker, and
  # exists for one reason: the marker text below contains the words
  # "pre-push" and "hook", so the `_hook_name` grep further down matches the
  # marker itself and reports the label as the hook's name (measured — the
  # hint read "(pre-push hook failed)" where husky's own banner further down
  # the same blob says "pre-push script failed"). Grepping a variable that
  # never contained leerie's own prose is what keeps a label from being read
  # as evidence.
  local push_stderr push_hook_out push_all _po _pe _prc=0
  push_hook_out=""
  _po="$(mktemp 2>/dev/null || true)"
  _pe="$(mktemp 2>/dev/null || true)"
  if [ -z "$_po" ] || [ -z "$_pe" ]; then
    # No writable temp dir — the full-`/tmp` case the classifier's own
    # comment documents (N30). Degrade to the historical stderr-only
    # capture rather than losing the push output entirely.
    rm -f "$_po" "$_pe" 2>/dev/null || true
    push_stderr="$("${push_args[@]}" 2>&1 >/dev/null)" || _prc=$?
    push_all="$push_stderr"
  else
    "${push_args[@]}" >"$_po" 2>"$_pe" || _prc=$?
    push_stderr="$(cat "$_pe")"
    push_all="$push_stderr"
    if [ -s "$_po" ]; then
      push_hook_out="$(cat "$_po")"
      push_all="$push_all
--- pre-push hook output (stdout) ---
$push_hook_out"
    fi
    rm -f "$_po" "$_pe" 2>/dev/null || true
  fi
  if [ "$_prc" -ne 0 ]; then
    # Bound what is PERSISTED, not just what is printed. `push_error` reaches
    # `run.json` as a single `jq --arg` value, and a single argv element
    # cannot exceed MAX_ARG_STRLEN (131,072 bytes = 32 * PAGE_SIZE). One real
    # recorded push_error is already 104,520 bytes of jest output — 80% of
    # that ceiling on stderr ALONE — so appending hook stdout to the same
    # value is what makes the ceiling reachable. Past it, `jq` cannot be
    # exec'd at all, and under the `set -euo pipefail` every caller sets that
    # aborts host_finalize BEFORE the diagnostic below prints: the operator
    # would lose the very output this capture exists to preserve. Same
    # argv-E2BIG class the orchestrator hit on 2026-07-19. 32 KiB leaves ~99
    # KiB of headroom for the jq filter, the sidecar path and the other
    # --arg pairs; tail-anchored because the informative end of a compiler or
    # test-runner report is its tail.
    #
    # `tail -c` unconditionally, rather than a `${#push_all} -gt 32768` test,
    # because `${#var}` counts CHARACTERS while both `tail -c` and the argv
    # ceiling count BYTES — under a UTF-8 locale a 7-character Japanese
    # string reports 7 and occupies 21. A character-based guard therefore
    # under-measures by up to 4x, and 32,768 four-byte characters is 131,072
    # bytes: exactly MAX_ARG_STRLEN, with the guard not firing. Piping
    # always is byte-exact and needs no arithmetic; `tail -c` returns a
    # shorter input unchanged, so the equality test below adds the marker
    # only when something was really cut. Both sides have been through
    # `$( )`, so trailing-newline stripping is symmetric and cannot make a
    # whole-input case look truncated.
    #
    # A byte cut can land mid-character in a UTF-8 report. That is fine and
    # was verified rather than assumed: `jq --arg` substitutes U+FFFD for
    # the orphaned byte and still writes valid JSON at rc 0, so the worst
    # case is one replacement character at the cut point — not the aborted
    # write this bound exists to prevent.
    local push_persist
    push_persist="$(printf '%s' "$push_all" | tail -c 32768)"
    if [ "$push_persist" != "$push_all" ]; then
      push_persist="…(truncated to the last 32 KiB; a single jq --arg value
cannot exceed MAX_ARG_STRLEN)…
$push_persist"
    fi
    _host_finalize_update_run_json "$run_json" \
      "pushed_at=" "push_error=${push_persist:-git push failed}" \
      "pr_url=" "pr_error="
    echo "leerie: error: git push failed for branch \`$run_branch\`." >&2
    echo "  Local state is intact:" >&2
    echo "    - run branch:     $run_branch (holds all wave merges)" >&2
    echo "    - working branch: $working_branch (unchanged from run start)" >&2
    echo "    - PR base branch: $pr_base_branch (intended PR base)" >&2
    echo "  Resolve and retry manually:" >&2
    echo "    git push -u origin $run_branch$([ "${NO_VERIFY_PUSH:-false}" = "true" ] && echo " --no-verify")" >&2
    # The terminal gets a much tighter bound than run.json — a pre-push hook
    # running a test suite reaches megabytes easily, and the same tail-anchor
    # reasoning applies as above (and as the rebaser's `*)` arm applies to a
    # malformed JSON payload). `${#}` is fine here where it is not above:
    # this bound is cosmetic, so counting characters rather than bytes only
    # changes how much scrollback a multibyte payload occupies, never
    # whether a later command can be exec'd.
    local push_display="$push_all"
    if [ "${#push_all}" -gt 4000 ]; then
      push_display="…(truncated; more in run.json \`push_error\`)…
$(printf '%s' "$push_all" | tail -c 4000)"
    fi
    echo "  Push output was (stderr, plus any pre-push hook stdout):" >&2
    # `sed` rather than `printf '    %s\n'`: that form indents only the
    # FIRST line of a multi-line value, which ran the git error onto the
    # end of a hook's last line and read as one corrupted sentence.
    printf '%s\n' "$push_display" | sed 's/^/    /' >&2
    if _host_finalize_pre_push_hook_present "$USER_REPO" \
        && ! _host_finalize_is_auth_or_network_push_error "$push_stderr"; then
      local _hook_name _checked_out _blt_passed
      # Vendor-text grep kept only as a supplementary "which hook" naming
      # signal, per N24's scope note — never as the classification itself.
      # `|| true` guards against `set -o pipefail`: under the non-branded-
      # hook case this grep is EXPECTED to find nothing, and pipefail would
      # otherwise make that no-match propagate as the pipeline's exit status
      # (even though `head -1` itself succeeds), aborting the whole function
      # under `set -e` before the hint below ever prints.
      #
      # It reads the hook's stdout as well as stderr, and that is not
      # cosmetic: husky v9 prints its banner on STDOUT. Measured — a repo
      # with `core.hooksPath=.husky/_` runs `.husky/_/h`, whose line 20 is
      # `[ $c != 0 ] && echo "husky - $n script failed (code $c)"`, a bare
      # `echo` with no `>&2`. So under the historical stderr-only capture
      # this grep could never match the commonest hook runner in existence,
      # and the hint always fell back to its generic default. Safe because
      # this is the naming signal alone — classification stays on
      # `push_stderr` in the guard above, which is what keeps the corpus
      # score intact. It reads `push_hook_out` rather than `push_all` so the
      # section marker's own "pre-push hook" text cannot be matched first.
      _hook_name="$(printf '%s\n%s' "$push_stderr" "$push_hook_out" | grep -oE '(pre-push|pre-commit|commit-msg|pre-receive) (script|hook)' | head -1 || true)"
      echo "  This looks like a failing git hook (${_hook_name:-pre-push} failed) rather than a push/auth/network problem." >&2
      # WHICH TREE the hook measured is the fact that ends the confusion,
      # and nothing used to say it. git runs pre-push in the repo root
      # against whatever is CHECKED OUT; leerie never checks out the run
      # branch (the rebase uses a disposable worktree), so a hook that
      # lints or typechecks the working tree is reporting on the host's
      # state, not on the commits being pushed. Measured once: the hook
      # rejected a push over 13 TS2307 errors that existed on the checked-
      # out base branch because the host's node_modules was three weeks
      # stale, while the run's own two files were clean.
      _checked_out="$(git -C "$USER_REPO" rev-parse --abbrev-ref HEAD 2>/dev/null || true)"
      echo "  Note the hook ran against your checkout's working tree (${_checked_out:-current branch}), NOT the" >&2
      echo "  commits being pushed — leerie never checks out $run_branch. A hook that lints or" >&2
      echo "  typechecks the working tree is measuring host state; check that your dependencies" >&2
      echo "  are installed and up to date before assuming this run is at fault." >&2
      # `[]?` tolerates an absent/!object blt_results; `|| true` keeps a
      # missing or unreadable state.json (the fail-open completion-gate
      # case reaches here) from aborting the hint under `set -e`.
      _blt_passed="$(jq -r '[.blt_results[]? | select(.passed == true) | .command]
                            | unique | join(", ")' "$state_json" 2>/dev/null || true)"
      if [ -n "$_blt_passed" ] && [ "$_blt_passed" != "null" ]; then
        echo "  leerie already verified the integrated tree in-container; these passed there:" >&2
        echo "    $(printf '%s' "$_blt_passed" | cut -c1-300)" >&2
      fi
      echo "  If the hook failure is expected or unrelated to this run's changes, bypass it with:" >&2
      echo "    git push -u origin $run_branch --no-verify" >&2
      echo "  (or set NO_VERIFY_PUSH=true before invoking finalize)." >&2
    fi
    return 1
  fi
  local pushed_at
  pushed_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  _host_finalize_update_run_json "$run_json" "pushed_at=$pushed_at" "push_error="
  echo "[leerie] finalize: pushed $run_branch" >&2

  # --- step 2: PR creation ---------------------------------------------
  # Primary path: the pr_writer worker (DESIGN §6 *Finalization*) wrote
  # pr_title + pr_body to run.json. Use them when present.
  # Fallback: compose a deterministic body from state.json.
  local pr_title pr_body pr_title_llm pr_body_llm
  pr_title_llm="$(jq -r '.pr_title // ""' "$run_json")"
  pr_body_llm="$(jq -r '.pr_body // ""' "$run_json")"

  if [ -n "$pr_title_llm" ] && [ -n "$pr_body_llm" ]; then
    pr_title="leerie: $pr_title_llm"
    pr_body="$pr_body_llm"
  else
    # Deterministic fallback. Every `jq` here reads $state_json, which may
    # be ABSENT (the fail-open completion-gate case reaches here when the
    # pr_writer worker didn't populate pr_title/pr_body). `2>/dev/null ||
    # true` keeps each read from aborting finalize under the launcher's
    # `set -euo pipefail` when the file is missing/unreadable; the empty
    # result then degrades to "n/a" (via or_na_helper) or an empty section.
    local task first_cat source_of_truth started_at finished_at
    local wave_count subtask_count worker_count working_branch_display
    or_na_helper() { [ -n "$1" ] && [ "$1" != "null" ] && printf '%s' "$1" || printf 'n/a'; }
    task="$(jq -r '.task // ""' "$state_json" 2>/dev/null || true)"
    first_cat="$(or_na_helper "$(jq -r '.categories[0] // ""' "$state_json" 2>/dev/null || true)")"
    source_of_truth="$(or_na_helper "$(jq -r '.answers.source_of_truth // ""' "$state_json" 2>/dev/null || true)")"
    started_at="$(or_na_helper "$(jq -r '.started_at // ""' "$state_json" 2>/dev/null || true)")"
    finished_at="$(or_na_helper "$(jq -r '.finished_at // ""' "$state_json" 2>/dev/null || true)")"
    wave_count="$(jq -r '.waves | length' "$state_json" 2>/dev/null || true)"
    subtask_count="$(jq -r '[.waves[] | length] | add // 0' "$state_json" 2>/dev/null || true)"
    worker_count="$(or_na_helper "$(jq -r '.worker_count // ""' "$state_json" 2>/dev/null || true)")"
    working_branch_display="$(or_na_helper "$working_branch")"

    # Cost line — mirror of compose_pr_body's cost_line (orchestrator/leerie.py).
    # jq emits the "- Cost: ..." line only when a telemetry block is present
    # (absent on pre-classify orphans), matching the Python guard; the leading
    # newline is trimmed by the heredoc when cost_line is empty. `group` and
    # `money` reproduce Python's `${x:,.2f}` (thousands separators + 2-decimal
    # cents) for both the dollar figure and the token counts, so this fallback
    # is format-identical to compose_pr_body — with ONE residual edge: an exact
    # half-cent (e.g. 2.675) rounds up here (`round` is half-up) but down in
    # Python (IEEE-754 repr of 2.675 is 2.67499…), a sub-cent difference that
    # never arises on a real summed cost. Under the launcher's `set -euo
    # pipefail`, `2>/dev/null || true` keeps a missing/malformed state.json from
    # aborting finalize (cost_line then degrades to empty).
    local cost_line
    cost_line="$(jq -r '
      def group:
        tostring | . as $s | ($s | explode | reverse) as $r
        | ([range(0; ($r|length))]
           | map( ([$r[.]] | implode)
                  + (if . > 0 and (. % 3 == 0) then "," else "" end) )
           | reverse | join(""));
      def money:
        (. * 100 | round) as $c
        | (($c / 100) | floor) as $dollars
        | ($c % 100) as $cents
        | "$" + ($dollars | group) + "."
          + (if $cents < 10 then "0" else "" end) + ($cents | tostring);
      (.telemetry // {})
      | select(. != {})
      | "- Cost: " + ((.cost_usd // 0) | money)
        + " (" + ((.calls // 0) | tostring) + " calls, "
        + ((.input_tokens // 0) | group) + " in / "
        + ((.output_tokens // 0) | group) + " out tokens)"
    ' "$state_json" 2>/dev/null || true)"

    pr_title="leerie: $run_id"
    pr_body="$(cat <<EOF
## Task

$task

## Classification

- Category: $first_cat
- Source of truth: $source_of_truth

## Run summary

- Run ID: $run_id
- Started: $started_at
- Finished: $finished_at
- Waves: $wave_count, subtasks: $subtask_count
- Workers: $worker_count
${cost_line:+$cost_line
}- Generated by [leerie](https://github.com/enricai/leerie) on \`$working_branch_display\`.

See \`.leerie/runs/$run_id/state.json\` for full run state.
EOF
)"
    # Deploy-ordering note (DESIGN §20 run groups). Mirror the Python
    # compose_pr_body renderer (orchestrator/leerie.py) so a run that falls
    # all the way through to this LLM-less bash fallback still surfaces
    # cross-repo prerequisites. Read external_preconditions straight from
    # state.json — it lives in STATE_FIELDS, so no run.json persistence is
    # needed. jq emits nothing when the key is absent/empty; `2>/dev/null ||
    # true` keeps a missing/unreadable state.json from aborting finalize
    # under `set -euo pipefail`.
    local deploy_note
    deploy_note="$(jq -r '
      (.external_preconditions // [])
      | select(length > 0)
      | "\n## ⚠ Deploy-ordering\n\nOne or more cross-repo prerequisites were declared by the planner. Merge and deploy the following before merging this PR:\n\n"
        + ([.[]
            | "- **" + (.tag // "(unknown)") + "**"
              + ( (([.reasons // [] | .[] | .reason // "" | select(. != "")]) | join("; "))
                  | if . != "" then " — " + . else "" end )
           ] | join("\n"))
    ' "$state_json" 2>/dev/null || true)"
    if [ -n "$deploy_note" ]; then
      pr_body="$pr_body$deploy_note"
    fi
  fi

  # Fold in the rebaser's diagnosis (if the best-effort rebase above was
  # not applied) regardless of which pr_body composition path ran.
  if [ -n "$rebase_diagnosis_note" ]; then
    pr_body="$pr_body$rebase_diagnosis_note"
  fi

  # The resolved PR base (pr_base_branch, or working_branch when no
  # override) may no longer exist on origin — e.g. a stacked run whose
  # parent was squash-merged (and branch-deleted) while this run was in
  # flight, or an overridden base that was renamed/removed. Detect and
  # fall back to the repo's default branch so `gh pr create` doesn't 404.
  local original_pr_base_branch="$pr_base_branch"
  if ! git -C "$USER_REPO" ls-remote --exit-code --heads origin "$pr_base_branch" >/dev/null 2>&1; then
    local default_branch
    default_branch="$(git -C "$USER_REPO" remote show origin 2>/dev/null \
                       | sed -n 's/.*HEAD branch: //p')"
    if [ -n "$default_branch" ]; then
      echo "[leerie] finalize: base branch $pr_base_branch no longer exists on origin; falling back to $default_branch" >&2
      pr_base_branch="$default_branch"
    fi
  fi

  echo "[leerie] finalize: opening PR against $pr_base_branch" >&2
  local pr_output pr_ok=false
  # GitHub's API may not have indexed the freshly-pushed refs yet;
  # retry with backoff to ride out the race (symptom: "Head sha
  # can't be blank" / "No commits between" immediately after push).
  # Indexing lag has been observed to exceed the original 11 s window
  # (PR-#22 incident: a manual PR 8 min post-push was the first to
  # succeed); 0/5/10/20/30 gives ~68 s of coverage.
  for _pr_delay in 0 5 10 20 30; do
    [ "$_pr_delay" -gt 0 ] && {
      echo "[leerie] finalize: gh pr create failed; retrying in ${_pr_delay}s…" >&2
      sleep "$_pr_delay"
    }
    if pr_output="$(echo "$pr_body" | gh pr create \
                      --base "$pr_base_branch" \
                      --head "$run_branch" \
                      --title "$pr_title" \
                      --body-file - 2>&1)"; then
      pr_ok=true
      break
    fi
  done
  if [ "$pr_ok" != "true" ]; then
    # PR-creation failure is NON-fatal — push succeeded.
    _host_finalize_update_run_json "$run_json" \
      "pr_url=" "pr_error=${pr_output:-gh pr create failed}"
    echo "⚠  \`gh pr create\` failed; branch was pushed successfully." >&2
    echo "  Pushed branch: $run_branch (on origin)" >&2
    if [ "$pr_base_branch" != "$original_pr_base_branch" ]; then
      echo "  (base branch $original_pr_base_branch was already deleted from origin;" >&2
      echo "   tried fallback to $pr_base_branch, which also failed)" >&2
    fi
    echo "  Open the PR manually:" >&2
    echo "    gh pr create --base $pr_base_branch --head $run_branch" >&2
    echo "  Or via the GitHub web UI for the repo." >&2
    echo "  gh stderr was:" >&2
    printf '    %s\n' "$pr_output" >&2
    return 0
  fi
  local pr_url
  pr_url="$(printf '%s' "$pr_output" | tail -n 1)"
  _host_finalize_update_run_json "$run_json" "pr_url=$pr_url" "pr_error="
  echo "[leerie] finalize: opened PR $pr_url" >&2
  return 0
}
