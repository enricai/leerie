#!/usr/bin/env bash
# scripts/remote/seed-auth.sh — seed worker auth + Claude/git config into
# a provisioned Fly.io Machine.
#
# This is the remote equivalent of the `AUTH_MOUNTS` bind-mount block in the
# local `nerdctl run` path (leerie launcher lines 542–726). Instead of mounting
# a $STAGE scratch dir, the same content is delivered over SSH via flyctl.
#
# Usage (invoked from the leerie launcher's RUNTIME=fly branch after
# provision_machine() returns successfully):
#
#   source scripts/remote/seed-auth.sh
#   seed_auth              # blocks until seeding is complete
#
# Environment variables (must be set by the launcher before sourcing):
#
#   LEERIE_MACHINE_ID  — ID of the provisioned Fly Machine (set by provision.sh)
#   LEERIE_FLY_APP     — Fly.io app name (required; same as provision.sh)
#   STAGE            — host-side scratch dir already assembled by the launcher
#                      containing .claude/, .claude.json, and optional .gitconfig
#   HOME             — standard; used to read host git identity
#
# What is seeded:
#   1. ~/.claude.json (projects-stripped copy from $STAGE/.claude.json)
#   2. ~/.claude/ capability dirs (from $STAGE/.claude/, excluding
#      session/history/bulk dirs already filtered during $STAGE assembly)
#   3. ~/.claude/.credentials.json — if present in $STAGE (Keychain-extracted
#      on macOS), or constructed from $CLAUDE_CODE_OAUTH_TOKEN (Linux / fallback)
#   4. git identity: user.name and user.email from the host's git config,
#      set globally on the remote machine so worker commits have a valid author.
#
# Auth credential notes:
#   On macOS the launcher extracts the OAuth token from Keychain and writes it
#   to $STAGE/.claude/.credentials.json before this script runs; the tar pipe
#   in step 2 delivers that file along with the rest of ~/.claude/.
#   On Linux (or when Keychain extraction fails), the token lives in
#   $CLAUDE_CODE_OAUTH_TOKEN. In that case seed_auth() writes a minimal
#   credentials JSON to the machine directly — the same single-token JSON the
#   Claude Code CLI reads from ~/.claude/.credentials.json on Linux.
#
# Seeding mechanism:
#   Files are delivered via `flyctl ssh console -C` with a gzipped tar pipe:
#       tar -czC "$STAGE" . | flyctl ssh console --pty=false \
#         -C "sh -c 'tar -xzC /home/leerie && chown -R leerie: /home/leerie'"
#   `ssh console -C` is the only flyctl transport that takes the command
#   as a single string AND forwards host stdin (current flyctl dropped
#   `--stdin` and the post-`--` argv form from `machine exec`). The
#   trailing `chown -R leerie:` is necessary because the ssh-console
#   session lands as root by default; without it the orchestrator
#   (running as leerie) couldn't read its own credentials.
#
#   ~/.claude/plugins/cache and plugins/marketplaces are excluded from
#   the tar (hundreds of MB; rebuilt on the remote via `claude plugin
#   install` in step 4 below).

set -euo pipefail

# remote_log helper. Sourced directly (not via lib.sh) so the standalone
# test path in tests/test_seed_auth_sh.py — which doesn't source lib.sh —
# still gets the logger without pulling in require_fly_ssh /
# wait_for_fly_ssh_ready (the `command -v` guards below depend on those
# being absent so the test path skips them against stub flyctl).
_SEED_AUTH_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
. "$_SEED_AUTH_DIR/_log.sh"

FLY_APP="${LEERIE_FLY_APP:-}"

# --- seed_auth -----------------------------------------------------------
# Seeds Claude config + git identity into the provisioned Fly Machine.
# Requires: $LEERIE_MACHINE_ID (from provision.sh), $STAGE (from launcher),
#           $FLY_APP, $HOME, and either $STAGE/.claude/.credentials.json or
#           $CLAUDE_CODE_OAUTH_TOKEN.
# Returns: 0 on success; 1 on failure (caller should abort the run).
seed_auth() {
  local machine_id="${LEERIE_MACHINE_ID:-}"
  if [ -z "$machine_id" ]; then
    remote_log "seed_auth: LEERIE_MACHINE_ID is not set — cannot seed"
    return 1
  fi
  if [ -z "${STAGE:-}" ]; then
    remote_log "seed_auth: STAGE is not set — launcher must assemble the scratch dir first"
    return 1
  fi

  remote_log "remote: seeding Claude config + git identity into machine $machine_id ..."

  # --- 1. Seed ~/.claude.json + ~/.claude/ via a single tar pipe ----------
  # The $STAGE dir already has:
  #   .claude.json           (projects-stripped)
  #   .claude/               (bulk/history dirs excluded; settings.json.* stripped)
  #   .claude/.credentials.json  (if Keychain-extracted on macOS)
  # We pipe the whole $STAGE tree (limited to the claude files) as a tar
  # stream into `tar -xC /home/leerie` on the remote, preserving permissions.
  #
  # We explicitly exclude git/ssh/gnupg material — those are git-push auth
  # which lives on the host per DESIGN §6 *Finalization*. Workers only need
  # Claude auth + git identity; SSH keys for pushing are the host's concern.
  # Current flyctl removed `--stdin` from `machine exec`. Use
  # `flyctl ssh console -C` instead: it forwards host stdin to the
  # remote command. Today's residual payload is ~15 MB on the wire
  # (gzipped tar of $STAGE with plugin cache excluded); historically
  # the stage was ~640 MB and hit EOFs on the stdin pipe at that
  # size, which motivated the plugins/cache exclusion (rebuilt on
  # the remote in step 4) and the gzip wrap. Requires an active Fly
  # SSH cert in the leerie-private ssh-agent — require_fly_ssh
  # ensures that.
  # require_fly_ssh lives in lib.sh (sourced by provision.sh, which the
  # launcher sources before seed-auth.sh). Defensive check for callers
  # that source seed-auth.sh standalone (e.g. tests).
  if command -v require_fly_ssh >/dev/null 2>&1; then
    if ! require_fly_ssh; then
      remote_log "seed_auth: Fly SSH setup failed; cannot seed config"
      return 1
    fi
  fi
  # hallpass takes 5-30 s to come up after machine start; wait so the
  # tar-pipe below doesn't fail with "handshake failed: EOF".
  if command -v wait_for_fly_ssh_ready >/dev/null 2>&1; then
    remote_log "remote: waiting for hallpass (SSH) on $machine_id..."
    wait_for_fly_ssh_ready "$FLY_APP" "$machine_id" || true
  fi
  # COPYFILE_DISABLE=1 tells macOS BSD tar to skip the per-file
  # LIBARCHIVE.xattr.com.apple.provenance extended attribute that
  # GNU tar on Debian doesn't understand — silences a per-file
  # "Ignoring unknown extended header keyword" warning. No effect on Linux.
  #
  # Single attempt + one retry after `flyctl agent restart` if the
  # failure matches the transient "tunnel unavailable" pattern
  # observed on cold-start probes. Same retry shape as seed-repo.sh.
  # Build the timeout prefix once (empty string on hosts w/o GNU
  # `timeout`). The prefix bounds the `flyctl ssh console` invocation so
  # a stalled WireGuard tunnel produces a clean rc 124/137 instead of
  # hanging the launcher indefinitely.
  local _seed_to=""
  if command -v _seed_timeout_prefix >/dev/null 2>&1; then
    _seed_to="$(_seed_timeout_prefix)"
  fi
  local tar_rc=0 attempt err_log _hb_pid
  for attempt in 1 2; do
    err_log="$(mktemp)"
    _seed_progress_bg "seed_auth" &
    _hb_pid=$!
    # `-z` (gzip) on both ends: the residual stage is mostly JSON /
    # markdown / small binaries; gzip cuts ~2x off the wire. The
    # Debian 13 image has GNU tar which auto-detects -z. macOS bsdtar
    # also supports -z. COPYFILE_DISABLE=1 strips the macOS provenance
    # xattr so the stream is byte-deterministic.
    COPYFILE_DISABLE=1 tar -czC "$STAGE" \
         --exclude='.gitconfig' \
         --exclude='.gitconfig.local' \
         --exclude='.gitignore' \
         --exclude='.gitignore_global' \
         --exclude='.git-credentials' \
         --exclude='.netrc' \
         --exclude='.ssh' \
         --exclude='.gnupg' \
         --exclude='.config' \
         . \
         | $_seed_to flyctl ssh console \
             --app "$FLY_APP" \
             --machine "$machine_id" \
             --pty=false \
             -C "sh -c 'tar -xzC /home/leerie && chown -R leerie: /home/leerie'" 2>"$err_log"
    tar_rc=${PIPESTATUS[1]}
    kill "$_hb_pid" 2>/dev/null || true
    wait "$_hb_pid" 2>/dev/null || true
    if [ "$tar_rc" -eq 0 ]; then
      rm -f "$err_log"
      break
    fi
    # rc 124 = `timeout` sent SIGTERM after $LEERIE_SEED_TIMEOUT_S;
    # rc 137 = `timeout --kill-after` escalated to SIGKILL. Both mean
    # the flyctl ssh console session stalled (the failure mode that
    # used to manifest as a multi-hour hang). Emit a diagnosis hint
    # and treat as a retryable failure: the existing first-attempt
    # retry path will run; on second-attempt timeout we fall through
    # to the failure-return at the end of the function, which the
    # orchestrator then converts to a PAUSED run via the existing
    # rc≠0 path (DESIGN §6 *Pause on failure*).
    if [ "$tar_rc" -eq 124 ] || [ "$tar_rc" -eq 137 ]; then
      remote_log "seed_auth: flyctl ssh console did not return within ${LEERIE_SEED_TIMEOUT_S:-600}s (rc=$tar_rc) — the remote tar may have completed; verify with 'flyctl ssh console --app $FLY_APP --machine $machine_id -C \"ls -la /home/leerie\"' before resuming"
      if [ "$attempt" -eq 1 ]; then
        remote_log "remote: restarting flyctl agent and retrying once..."
        flyctl agent restart >/dev/null 2>&1 || true
        sleep 2
        rm -f "$err_log"
        continue
      fi
      rm -f "$err_log"
      break
    fi
    if [ "$attempt" -eq 1 ] \
       && grep -qE "tunnel unavailable|context deadline exceeded|i/o timeout" "$err_log"; then
      cat "$err_log" >&2
      remote_log "remote: tunnel unavailable; restarting flyctl agent and retrying once..."
      flyctl agent restart >/dev/null 2>&1 || true
      sleep 2
      rm -f "$err_log"
      continue
    fi
    cat "$err_log" >&2
    rm -f "$err_log"
    break
  done
  if [ "$tar_rc" -ne 0 ]; then
    remote_log "seed_auth: failed to seed Claude config files into machine $machine_id"
    return 1
  fi
  remote_log "remote: Claude config seeded"

  # --- 2. Token fallback: if no credentials file was in $STAGE, write one --
  # On Linux (and on macOS when Keychain extraction fails) the launcher does
  # not write $STAGE/.claude/.credentials.json, but it may set
  # $CLAUDE_CODE_OAUTH_TOKEN. In that case, write a minimal credentials JSON
  # directly to the machine so `claude -p` can authenticate.
  #
  # The file format mirrors what the macOS Keychain stores and what the Linux
  # CLI reads: {"claudeAiOauth":{"accessToken":"...","scopes":["user:inference"]}}.
  # The scopes field is mandatory — CLI 2.1.210's file-auth path rejects a
  # scope-less blob with "Not logged in" (measured against the real image);
  # only "user:inference" satisfies it. Must match leerie's synthesized shape
  # in _extract_claude_credentials_json.
  if [ ! -s "$STAGE/.claude/.credentials.json" ] && \
     [ -n "${CLAUDE_CODE_OAUTH_TOKEN:-}" ]; then
    local creds_json
    creds_json="$(printf '{"claudeAiOauth":{"accessToken":"%s","scopes":["user:inference"]}}' \
                         "$CLAUDE_CODE_OAUTH_TOKEN")"
    # Pipe the small creds JSON through ssh console (same mechanism as
    # the tar-pipe above; the cert was already issued by the
    # require_fly_ssh call earlier in this function).
    if ! printf '%s' "$creds_json" \
         | flyctl ssh console \
             --app "$FLY_APP" \
             --machine "$machine_id" \
             --pty=false \
             -C "sh -c 'cat > /home/leerie/.claude/.credentials.json && chmod 600 /home/leerie/.claude/.credentials.json && chown leerie: /home/leerie/.claude/.credentials.json'" 2>&1; then
      remote_log "seed_auth: failed to write credentials JSON from CLAUDE_CODE_OAUTH_TOKEN"
      return 1
    fi
    remote_log "remote: Claude credentials written from CLAUDE_CODE_OAUTH_TOKEN"
  elif [ ! -s "$STAGE/.claude/.credentials.json" ] && \
       [ -z "${CLAUDE_CODE_OAUTH_TOKEN:-}" ]; then
    remote_log "seed_auth: no credentials available — neither \$STAGE/.claude/.credentials.json"
    echo "  nor \$CLAUDE_CODE_OAUTH_TOKEN is set. Workers will not be able to authenticate." >&2
    echo "  On macOS: grant the launcher Keychain access (the prompt that appears on first run)." >&2
    echo "  On Linux: export CLAUDE_CODE_OAUTH_TOKEN in your shell before running leerie." >&2
    return 1
  fi

  # --- 3. Set git identity on the remote machine -------------------------
  # Read user.name and user.email from the host git config and set them
  # globally on the machine. Workers commit as the host user.
  local git_name git_email
  git_name="$(git config user.name 2>/dev/null || true)"
  git_email="$(git config user.email 2>/dev/null || true)"

  if [ -z "$git_name" ] || [ -z "$git_email" ]; then
    remote_log "seed_auth: git user.name or user.email is not configured on the host."
    echo "  Run: git config --global user.name \"Your Name\"" >&2
    echo "       git config --global user.email \"you@example.com\"" >&2
    return 1
  fi

  # Stream git identity over ssh-console's stdin so names with quotes
  # (O'Brien, "Smith, Jr.") can't break host-side quoting.
  #
  # Write to /home/leerie/.gitconfig directly (not --global, which would
  # write to root's home since the ssh-console session lands as root)
  # and chown afterwards. The orchestrator runs as the leerie user and
  # reads ~/.gitconfig from /home/leerie.
  if ! printf '%s\n%s\n' "$git_name" "$git_email" \
       | flyctl ssh console --app "$FLY_APP" --machine "$machine_id" \
           --pty=false \
           -C "sh -c 'IFS= read -r n; IFS= read -r e; \
              git config --file /home/leerie/.gitconfig user.name \"\$n\" && \
              git config --file /home/leerie/.gitconfig user.email \"\$e\" && \
              chown leerie: /home/leerie/.gitconfig'" >/dev/null 2>&1; then
    remote_log "seed_auth: failed to set git identity on machine $machine_id"
    return 1
  fi

  remote_log "remote: git identity set (${git_name} <${git_email}>)"

  # Pre-warm the Claude CLI. The very first `claude --version` on a
  # cold Fly machine takes ~17 s (Node runtime warm-up, statsig
  # network call, etc); the orchestrator's preflight has a tight
  # timeout. Running it once here as the leerie user makes the
  # orchestrator's subsequent call complete in <0.2 s. Failure
  # here is non-fatal — the orchestrator will still try.
  remote_log "remote: pre-warming Claude CLI..."
  flyctl ssh console --app "$FLY_APP" --machine "$machine_id" --pty=false \
    -C "su leerie -c 'HOME=/home/leerie PATH=/usr/local/share/mise/installs/node/lts-current/bin:/usr/bin:/bin claude --version'" \
    >/dev/null 2>&1 || true

  # --- 4. Rebuild plugin cache on the remote -----------------------------
  # The launcher's CLAUDE_SKIP excludes plugins/cache and
  # plugins/marketplaces from the tar pipe (hundreds of MB on a typical
  # host). Repopulate them on the machine's fast public egress using the
  # small JSON files we did seed:
  #   known_marketplaces.json → claude plugin marketplace add <owner>/<repo>
  #   installed_plugins.json  → claude plugin install <name>@<marketplace>
  # The CLI does not auto-refill the cache on session start; if the
  # plugin's directory is missing it prints "Plugin not found in cache"
  # and skips. So we trigger the installs here before any worker runs.
  # Failures are logged but non-fatal — a missing plugin only matters
  # if a user-supplied task explicitly invokes it, in which case the
  # CLI's existing skip-with-warning behavior is the right surface.
  remote_log "remote: rebuilding plugin cache (marketplaces + installs)..."
  # runuser, not su -c 'sh -s'. su consumes stdin for the password prompt
  # and util-linux's stdin-forwarding to the inner shell is implementation-
  # specific; runuser is the documented non-interactive equivalent (in
  # util-linux on Debian, at /usr/sbin/runuser) and forwards stdin
  # transparently. env (coreutils) sets HOME/PATH for the leerie process
  # without relying on sh -c re-quoting.
  #
  # Bracket with the same timeout prefix + heartbeat the main tar pipe
  # uses (lines 135-200): the rebuild runs `git clone` + `bun install`
  # per plugin and routinely takes 30-90 s on a fresh machine, which
  # without these would (a) hang silently if `flyctl ssh console`
  # exhibits its known Mode-2 stall, and (b) read as "frozen" to the
  # user even on the happy path.
  local _rebuild_hb_pid _rebuild_rc=0
  _seed_progress_bg "plugin_rebuild" &
  _rebuild_hb_pid=$!
  # `|| _rebuild_rc=$?` captures the rc AND suppresses `set -e`
  # (file-level `set -euo pipefail`) in one step. Without the `||`,
  # a non-zero `flyctl ssh console` rc would abort the function
  # before the next line, leaving _rebuild_rc=0 from the local-decl
  # default and making the elif branches below unreachable.
  $_seed_to flyctl ssh console --app "$FLY_APP" --machine "$machine_id" --pty=false \
    -C "runuser -u leerie -- env HOME=/home/leerie PATH=/usr/local/share/mise/installs/node/lts-current/bin:/usr/bin:/bin sh -s" \
    <<'REMOTE_SH' >/dev/null 2>&1 || _rebuild_rc=$?
set -u
mkdir -p "$HOME/.cache/leerie"
LOG="$HOME/.cache/leerie/plugin-install.log"
: > "$LOG"
KNOWN="$HOME/.claude/plugins/known_marketplaces.json"
INSTALLED="$HOME/.claude/plugins/installed_plugins.json"

# (a) Register marketplaces. python3 over jq because jq isn't in the
# leerie image (see Dockerfile). Emit one "owner/repo" per line for
# entries whose source is a GitHub repo; silently skip non-github
# sources (e.g., local-path marketplaces don't survive the trip).
if [ -s "$KNOWN" ]; then
  # `2>>"$LOG"` captures the python3 stderr so a JSON parse error
  # (corrupted file, schema drift) lands in plugin-install.log
  # instead of silently zero-iterating the while loop below.
  python3 -c '
import json, sys
try:
    data = json.load(open(sys.argv[1]))
except Exception as e:
    print(f"# JSON parse error on {sys.argv[1]}: {e}", file=sys.stderr)
    sys.exit(0)
for entry in data.values():
    src = entry.get("source") or {}
    if src.get("source") == "github":
        repo = src.get("repo")
        if repo:
            print(repo)
  ' "$KNOWN" 2>>"$LOG" | while read -r repo; do
    [ -n "$repo" ] || continue
    echo "+ claude plugin marketplace add $repo" >> "$LOG"
    claude plugin marketplace add "$repo" >> "$LOG" 2>&1 \
      || echo "WARN: marketplace $repo add failed (continuing)" >> "$LOG"
  done
fi

# (b) Reinstall plugins. The JSON key is the install spec
# (e.g., "vercel@claude-plugins-official"). Note: project-scoped
# entries on the host carry a host-only projectPath; we install
# everything at the CLI default scope (user) on the remote. Intentional
# — the Fly machine is single-task, so flattening project-scope to
# user-scope makes the plugin available to whichever task is running
# instead of nothing (the host projectPath doesn't exist remotely).
if [ -s "$INSTALLED" ]; then
  python3 -c '
import json, sys
try:
    data = json.load(open(sys.argv[1]))
except Exception as e:
    print(f"# JSON parse error on {sys.argv[1]}: {e}", file=sys.stderr)
    sys.exit(0)
for spec in (data.get("plugins") or {}).keys():
    print(spec)
  ' "$INSTALLED" 2>>"$LOG" | while read -r spec; do
    [ -n "$spec" ] || continue
    echo "+ claude plugin install $spec" >> "$LOG"
    claude plugin install "$spec" >> "$LOG" 2>&1 \
      || echo "WARN: $spec install failed (continuing)" >> "$LOG"
  done
fi
REMOTE_SH
  kill "$_rebuild_hb_pid" 2>/dev/null || true
  wait "$_rebuild_hb_pid" 2>/dev/null || true
  # Non-fatal regardless of rc — a missing plugin only matters if a
  # user-supplied task explicitly invokes it, in which case the Claude
  # CLI's existing "plugin not found in cache" warning is the right
  # surface. But surface the rc honestly: rc 124/137 means $_seed_to
  # fired (stall); any other non-zero means the ssh console or the
  # remote heredoc itself errored out.
  if [ "$_rebuild_rc" -eq 0 ]; then
    remote_log "remote: plugin cache rebuild complete (log on machine: ~/.cache/leerie/plugin-install.log)"
  elif [ "$_rebuild_rc" -eq 124 ] || [ "$_rebuild_rc" -eq 137 ]; then
    remote_log "remote: plugin cache rebuild timed out (rc=$_rebuild_rc) after ${LEERIE_SEED_TIMEOUT_S:-600}s — continuing without rebuild; verify with 'flyctl ssh console --app $FLY_APP --machine $machine_id -C \"cat /home/leerie/.cache/leerie/plugin-install.log\"'"
  else
    remote_log "remote: plugin cache rebuild attempted but ssh console returned rc=$_rebuild_rc — continuing; check /home/leerie/.cache/leerie/plugin-install.log on the machine"
  fi

  remote_log "remote: seed_auth complete"
  return 0
}
