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
#   LEERIE_FLY_APP     — Fly.io app name (default: "leerie"; same as provision.sh)
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
#   Files are delivered via `flyctl ssh console -C` with a tar pipe:
#       tar -cC "$STAGE" . | flyctl ssh console --pty=false \
#         -C "sh -c 'tar -xC /home/leerie && chown -R leerie: /home/leerie'"
#   `ssh console -C` is the only flyctl transport that takes the command
#   as a single string AND forwards host stdin (current flyctl dropped
#   `--stdin` and the post-`--` argv form from `machine exec`). The
#   trailing `chown -R leerie:` is necessary because the ssh-console
#   session lands as root by default; without it the orchestrator
#   (running as leerie) couldn't read its own credentials.

set -euo pipefail

# remote_log helper. Sourced directly (not via lib.sh) so the standalone
# test path in tests/test_seed_auth_sh.py — which doesn't source lib.sh —
# still gets the logger without pulling in require_fly_ssh /
# wait_for_fly_ssh_ready (the `command -v` guards below depend on those
# being absent so the test path skips them against stub flyctl).
_SEED_AUTH_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
. "$_SEED_AUTH_DIR/_log.sh"

FLY_APP="${LEERIE_FLY_APP:-leerie}"

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
  # remote command and supports arbitrarily large payloads (we
  # routinely transfer ~640 MB of Claude plugins/agents). Requires
  # an active Fly SSH cert in the leerie-private ssh-agent —
  # require_fly_ssh ensures that.
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
    COPYFILE_DISABLE=1 tar -cC "$STAGE" \
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
             -C "sh -c 'tar -xC /home/leerie && chown -R leerie: /home/leerie'" 2>"$err_log"
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
  # CLI reads: {"claudeAiOauth":{"accessToken":"..."}}.
  if [ ! -s "$STAGE/.claude/.credentials.json" ] && \
     [ -n "${CLAUDE_CODE_OAUTH_TOKEN:-}" ]; then
    local creds_json
    creds_json="$(printf '{"claudeAiOauth":{"accessToken":"%s"}}' \
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

  remote_log "remote: seed_auth complete"
  return 0
}
