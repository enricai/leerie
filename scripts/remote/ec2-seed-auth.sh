#!/usr/bin/env bash
# scripts/remote/ec2-seed-auth.sh — seed worker auth + Claude/git config into
# a provisioned EC2 instance.
#
# EC2 counterpart to scripts/remote/seed-auth.sh (DESIGN §6 "EC2 runtime
# lifecycle", Seed row: "same two steps, transport substituted"). The
# payload logic — what gets seeded and why — is IDENTICAL to the Fly
# path; only the transport differs, following the exact discipline
# ec2-seed-repo.sh already established for the repo-seeding half:
#
#   Fly: `flyctl ssh console -C "sh -c '...'"` for both bulk data
#        AND small remote commands (tar extraction, credential writes,
#        git config, plugin-cache rebuild).
#   EC2: `ec2_tar_pipe` (plain ssh, DESIGN §6 "Transport substitution")
#        for the bulk $STAGE tar — SSM's AWS-StartInteractiveCommand has
#        no stdin-pipe facility and a ~4 KB document-parameter ceiling,
#        so it cannot carry a multi-MB tar payload — and `ec2_remote_exec`
#        (SSM Session Manager, the default transport) for every small
#        remote command (credential write, git identity, pre-warm,
#        plugin-cache rebuild).
#
# Usage (invoked from the leerie launcher's RUNTIME=ec2 branch after
# provisioning succeeds):
#
#   source scripts/remote/ec2-lib.sh
#   source scripts/remote/ec2-seed-auth.sh
#   ec2_seed_auth              # blocks until seeding is complete
#
# Environment variables (must be set by the launcher before sourcing):
#
#   LEERIE_EC2_INSTANCE_ID — id of the running EC2 instance (set by
#                            ec2-provision.sh once it lands)
#   LEERIE_EC2_SSH_TARGET  — ssh(1) destination for the instance (e.g.
#                            "ec2-user@<public-ip>" or an ssh_config Host
#                            alias). Resolving an instance id to a
#                            reachable address is provisioning's job
#                            (out of scope here, same as ec2_tar_pipe's
#                            own docstring states) — this script only
#                            consumes the resolved target.
#   STAGE                  — host-side scratch dir already assembled by
#                            the launcher containing .claude/,
#                            .claude.json, and optional .gitconfig
#                            (same STAGE the local nerdctl AUTH_MOUNTS
#                            path and seed-auth.sh both consume)
#   HOME                    — standard; used to read host git identity
#
# What is seeded — identical to seed-auth.sh:
#   1. ~/.claude.json (projects-stripped copy from $STAGE/.claude.json)
#   2. ~/.claude/ capability dirs (from $STAGE/.claude/, excluding
#      session/history/bulk dirs already filtered during $STAGE assembly)
#   3. ~/.claude/.credentials.json — if present in $STAGE (Keychain-extracted
#      on macOS), or constructed from $CLAUDE_CODE_OAUTH_TOKEN (Linux / fallback)
#   4. git identity: user.name and user.email from the host's git config,
#      set globally on the remote instance so worker commits have a valid
#      author.
#
# Auth credential notes — identical to seed-auth.sh:
#   On macOS the launcher extracts the OAuth token from Keychain and writes it
#   to $STAGE/.claude/.credentials.json before this script runs; the tar pipe
#   in step 2 delivers that file along with the rest of ~/.claude/.
#   On Linux (or when Keychain extraction fails), the token lives in
#   $CLAUDE_CODE_OAUTH_TOKEN. In that case ec2_seed_auth() writes a minimal
#   credentials JSON to the instance directly — the same single-token JSON the
#   Claude Code CLI reads from ~/.claude/.credentials.json on Linux.
#
# Seeding mechanism:
#   The $STAGE tree is delivered via a gzipped tar over `ec2_tar_pipe`
#   (plain ssh), landing at /home/leerie on the instance, followed by a
#   `chown -R leerie:` over `ec2_remote_exec` (SSM) — the instance's ssh
#   target may land as the AMI default user (e.g. ec2-user/ubuntu) or
#   root depending on transport, so the chown step is unconditional,
#   mirroring seed-auth.sh's own "ssh-console session lands as root by
#   default" rationale.
#
#   ~/.claude/plugins/cache and plugins/marketplaces are excluded from
#   the tar (hundreds of MB; rebuilt on the remote via `claude plugin
#   install` in step 4 below).

set -euo pipefail

# shared lib (_log.sh: remote_log / _seed_progress_bg). Sourced directly
# so the standalone test path (which sources this file without going
# through ec2-lib.sh first) still gets the logger.
_EC2_SEED_AUTH_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
. "$_EC2_SEED_AUTH_DIR/_log.sh"

# --- ec2_seed_auth ---------------------------------------------------------
# Seeds Claude config + git identity into the provisioned EC2 instance.
# Requires: $LEERIE_EC2_INSTANCE_ID, $LEERIE_EC2_SSH_TARGET, $STAGE (from
#           launcher), $HOME, and either $STAGE/.claude/.credentials.json
#           or $CLAUDE_CODE_OAUTH_TOKEN.
# Returns: 0 on success; 1 on failure (caller should abort the run).
ec2_seed_auth() {
  local instance_id="${LEERIE_EC2_INSTANCE_ID:-}"
  if [ -z "$instance_id" ]; then
    remote_log "ec2_seed_auth: LEERIE_EC2_INSTANCE_ID is not set — cannot seed"
    return 1
  fi
  local ssh_target="${LEERIE_EC2_SSH_TARGET:-}"
  if [ -z "$ssh_target" ]; then
    remote_log "ec2_seed_auth: LEERIE_EC2_SSH_TARGET is not set — cannot seed"
    return 1
  fi
  if [ -z "${STAGE:-}" ]; then
    remote_log "ec2_seed_auth: STAGE is not set — launcher must assemble the scratch dir first"
    return 1
  fi
  if command -v require_aws >/dev/null 2>&1; then
    require_aws || return 1
  elif ! command -v aws >/dev/null 2>&1; then
    remote_log "ec2_seed_auth: aws CLI not found on PATH"
    return 1
  fi

  remote_log "remote: seeding Claude config + git identity into instance $instance_id (ec2) ..."

  # --- 1. Seed ~/.claude.json + ~/.claude/ via a single tar pipe ----------
  # The $STAGE dir already has:
  #   .claude.json           (projects-stripped)
  #   .claude/               (bulk/history dirs excluded; settings.json.* stripped)
  #   .claude/.credentials.json  (if Keychain-extracted on macOS)
  # We pipe the whole $STAGE tree (limited to the claude files) as a tar
  # stream into `tar -xC /home/leerie` on the instance via ec2_tar_pipe,
  # preserving permissions.
  #
  # We explicitly exclude git/ssh/gnupg material — those are git-push auth
  # which lives on the host per DESIGN §6 *Finalization*. Workers only need
  # Claude auth + git identity; SSH keys for pushing are the host's concern.
  # Same exclusion list as seed-auth.sh.
  #
  # Single attempt + one retry, mirroring seed-auth.sh's transient-failure
  # retry shape (there: "tunnel unavailable"; here: a generic non-timeout
  # transport failure — EC2/SSH has no flyctl-agent-restart equivalent, so
  # the retry is a plain re-attempt after a short sleep).
  # Wrapped with $(_seed_timeout_prefix) via ec2_tar_pipe so a stalled SSH
  # session yields a clean rc 124/137 instead of hanging the launcher
  # indefinitely.
  local tar_rc=0 attempt _hb_pid
  for attempt in 1 2; do
    _seed_progress_bg "ec2_seed_auth" &
    _hb_pid=$!
    # `-z` (gzip) on both ends: the residual stage is mostly JSON /
    # markdown / small binaries; gzip cuts ~2x off the wire.
    # COPYFILE_DISABLE=1 strips the macOS provenance xattr so the stream
    # is byte-deterministic (no effect on Linux).
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
         | ec2_tar_pipe "$ssh_target" /home/leerie
    tar_rc=$?
    kill "$_hb_pid" 2>/dev/null || true
    wait "$_hb_pid" 2>/dev/null || true
    if [ "$tar_rc" -eq 0 ]; then
      break
    fi
    # rc 124 = timeout sent SIGTERM after $LEERIE_SEED_TIMEOUT_S;
    # rc 137 = timeout --kill-after escalated to SIGKILL. Both mean the
    # ssh session stalled. Emit a diagnosis hint and treat as retryable:
    # the first-attempt retry runs; on second-attempt timeout we fall
    # through to the failure-return below, which the orchestrator then
    # converts to a PAUSED run via the existing rc≠0 path.
    if [ "$tar_rc" -eq 124 ] || [ "$tar_rc" -eq 137 ]; then
      remote_log "ec2_seed_auth: ec2_tar_pipe did not return within ${LEERIE_SEED_TIMEOUT_S:-600}s (rc=$tar_rc) — the remote tar may have completed; verify with 'ssh $ssh_target ls -la /home/leerie' before resuming"
      if [ "$attempt" -eq 1 ]; then
        remote_log "remote: retrying tar pipe once..."
        sleep 2
        continue
      fi
      break
    fi
    if [ "$attempt" -eq 1 ]; then
      remote_log "remote: tar pipe to instance failed (rc=$tar_rc); retrying once..."
      sleep 2
      continue
    fi
    break
  done
  if [ "$tar_rc" -ne 0 ]; then
    remote_log "ec2_seed_auth: failed to seed Claude config files into instance $instance_id"
    return 1
  fi

  # ec2_tar_pipe's ssh target may land as the AMI default user (not
  # root, unlike flyctl ssh console) — fix ownership unconditionally so
  # the leerie user (which the orchestrator runs as) can read its own
  # credentials, mirroring seed-auth.sh's chown step.
  if ! ec2_remote_exec "$instance_id" "chown -R leerie: /home/leerie" >/dev/null; then
    remote_log "ec2_seed_auth: failed to chown /home/leerie to leerie: on instance $instance_id"
    return 1
  fi
  remote_log "remote: Claude config seeded (ec2)"

  # --- 2. Token fallback: if no credentials file was in $STAGE, write one --
  # On Linux (and on macOS when Keychain extraction fails) the launcher does
  # not write $STAGE/.claude/.credentials.json, but it may set
  # $CLAUDE_CODE_OAUTH_TOKEN. In that case, write a minimal credentials JSON
  # directly to the instance so `claude -p` can authenticate.
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
    local creds_b64
    creds_b64="$(printf '%s' "$creds_json" | base64 | tr -d '\n')"
    if ! ec2_remote_exec "$instance_id" \
         "echo $creds_b64 | base64 -d > /home/leerie/.claude/.credentials.json && chmod 600 /home/leerie/.claude/.credentials.json && chown leerie: /home/leerie/.claude/.credentials.json" \
         >/dev/null; then
      remote_log "ec2_seed_auth: failed to write credentials JSON from CLAUDE_CODE_OAUTH_TOKEN"
      return 1
    fi
    remote_log "remote: Claude credentials written from CLAUDE_CODE_OAUTH_TOKEN (ec2)"
  elif [ ! -s "$STAGE/.claude/.credentials.json" ] && \
       [ -z "${CLAUDE_CODE_OAUTH_TOKEN:-}" ]; then
    remote_log "ec2_seed_auth: no credentials available — neither \$STAGE/.claude/.credentials.json"
    echo "  nor \$CLAUDE_CODE_OAUTH_TOKEN is set. Workers will not be able to authenticate." >&2
    echo "  On macOS: grant the launcher Keychain access (the prompt that appears on first run)." >&2
    echo "  On Linux: export CLAUDE_CODE_OAUTH_TOKEN in your shell before running leerie." >&2
    return 1
  fi

  # --- 3. Set git identity on the remote instance -------------------------
  # Read user.name and user.email from the host git config and set them
  # globally on the instance. Workers commit as the host user.
  local git_name git_email
  git_name="$(git config user.name 2>/dev/null || true)"
  git_email="$(git config user.email 2>/dev/null || true)"

  if [ -z "$git_name" ] || [ -z "$git_email" ]; then
    remote_log "ec2_seed_auth: git user.name or user.email is not configured on the host."
    echo "  Run: git config --global user.name \"Your Name\"" >&2
    echo "       git config --global user.email \"you@example.com\"" >&2
    return 1
  fi

  # Base64-encode name/email before handing them to ec2_remote_exec so
  # names with quotes (O'Brien, "Smith, Jr.") can't break the wrapped
  # command's shell quoting — ec2_remote_exec's own base64 wrap only
  # protects the outer transport, not values embedded inside `cmd`
  # itself. Write to /home/leerie/.gitconfig directly (not --global,
  # which could land in a different home dir depending on which user
  # the transport lands as) and chown afterwards. The orchestrator runs
  # as the leerie user and reads ~/.gitconfig from /home/leerie.
  local git_name_b64 git_email_b64
  git_name_b64="$(printf '%s' "$git_name" | base64 | tr -d '\n')"
  git_email_b64="$(printf '%s' "$git_email" | base64 | tr -d '\n')"
  if ! ec2_remote_exec "$instance_id" \
       "n=\$(echo $git_name_b64 | base64 -d); e=\$(echo $git_email_b64 | base64 -d); git config --file /home/leerie/.gitconfig user.name \"\$n\" && git config --file /home/leerie/.gitconfig user.email \"\$e\" && chown leerie: /home/leerie/.gitconfig" \
       >/dev/null; then
    remote_log "ec2_seed_auth: failed to set git identity on instance $instance_id"
    return 1
  fi

  remote_log "remote: git identity set (${git_name} <${git_email}>) (ec2)"

  # Pre-warm the Claude CLI. The very first `claude --version` on a cold
  # instance takes tens of seconds (Node runtime warm-up, statsig network
  # call, etc); the orchestrator's preflight has a tight timeout. Running
  # it once here as the leerie user makes the orchestrator's subsequent
  # call complete quickly. Failure here is non-fatal — the orchestrator
  # will still try.
  remote_log "remote: pre-warming Claude CLI (ec2)..."
  ec2_remote_exec "$instance_id" \
    "runuser -u leerie -- env HOME=/home/leerie PATH=/usr/local/share/mise/installs/node/lts-current/bin:/usr/bin:/bin claude --version" \
    >/dev/null 2>&1 || true

  # --- 4. Rebuild plugin cache on the remote -----------------------------
  # The launcher's $STAGE build skips plugins/cache and
  # plugins/marketplaces from the tar pipe (hundreds of MB on a typical
  # host). Repopulate them on the instance's fast public egress using the
  # small JSON files we did seed:
  #   known_marketplaces.json → claude plugin marketplace add <owner>/<repo>
  #   installed_plugins.json  → claude plugin install <name>@<marketplace>
  # The CLI does not auto-refill the cache on session start; if the
  # plugin's directory is missing it prints "Plugin not found in cache"
  # and skips. So we trigger the installs here before any worker runs.
  # Failures are logged but non-fatal — a missing plugin only matters if
  # a user-supplied task explicitly invokes it, in which case the CLI's
  # existing skip-with-warning behavior is the right surface.
  #
  # Identical payload to seed-auth.sh step 4, transport substituted:
  # passed whole to ec2_remote_exec (which base64-wraps the entire
  # multi-line script), instead of piped over flyctl ssh console's stdin
  # into a `runuser ... sh -s` heredoc.
  remote_log "remote: rebuilding plugin cache (marketplaces + installs) (ec2)..."
  local _rebuild_script
  _rebuild_script="$(cat <<'REMOTE_SH'
runuser -u leerie -- env HOME=/home/leerie PATH=/usr/local/share/mise/installs/node/lts-current/bin:/usr/bin:/bin sh -c '
set -u
mkdir -p "$HOME/.cache/leerie"
LOG="$HOME/.cache/leerie/plugin-install.log"
: > "$LOG"
KNOWN="$HOME/.claude/plugins/known_marketplaces.json"
INSTALLED="$HOME/.claude/plugins/installed_plugins.json"

if [ -s "$KNOWN" ]; then
  python3 -c "
import json, sys
try:
    data = json.load(open(sys.argv[1]))
except Exception as e:
    print(f\"# JSON parse error on {sys.argv[1]}: {e}\", file=sys.stderr)
    sys.exit(0)
for entry in data.values():
    src = entry.get(\"source\") or {}
    if src.get(\"source\") == \"github\":
        repo = src.get(\"repo\")
        if repo:
            print(repo)
  " "$KNOWN" 2>>"$LOG" | while read -r repo; do
    [ -n "$repo" ] || continue
    echo "+ claude plugin marketplace add $repo" >> "$LOG"
    claude plugin marketplace add "$repo" >> "$LOG" 2>&1 \
      || echo "WARN: marketplace $repo add failed (continuing)" >> "$LOG"
  done
fi

if [ -s "$INSTALLED" ]; then
  python3 -c "
import json, sys
try:
    data = json.load(open(sys.argv[1]))
except Exception as e:
    print(f\"# JSON parse error on {sys.argv[1]}: {e}\", file=sys.stderr)
    sys.exit(0)
for spec in (data.get(\"plugins\") or {}).keys():
    print(spec)
  " "$INSTALLED" 2>>"$LOG" | while read -r spec; do
    [ -n "$spec" ] || continue
    echo "+ claude plugin install $spec" >> "$LOG"
    claude plugin install "$spec" >> "$LOG" 2>&1 \
      || echo "WARN: $spec install failed (continuing)" >> "$LOG"
  done
fi
'
REMOTE_SH
)"
  local _rebuild_hb_pid _rebuild_rc=0
  _seed_progress_bg "plugin_rebuild" &
  _rebuild_hb_pid=$!
  ec2_remote_exec "$instance_id" "$_rebuild_script" >/dev/null 2>&1 || _rebuild_rc=$?
  kill "$_rebuild_hb_pid" 2>/dev/null || true
  wait "$_rebuild_hb_pid" 2>/dev/null || true
  # Non-fatal regardless of rc — a missing plugin only matters if a
  # user-supplied task explicitly invokes it, in which case the Claude
  # CLI's existing "plugin not found in cache" warning is the right
  # surface. But surface the rc honestly: rc 124/137 means the transport
  # timeout fired (stall); any other non-zero means the remote exec or
  # the remote script itself errored out.
  if [ "$_rebuild_rc" -eq 0 ]; then
    remote_log "remote: plugin cache rebuild complete (ec2; log on instance: ~/.cache/leerie/plugin-install.log)"
  elif [ "$_rebuild_rc" -eq 124 ] || [ "$_rebuild_rc" -eq 137 ]; then
    remote_log "remote: plugin cache rebuild timed out (rc=$_rebuild_rc) after ${LEERIE_SEED_TIMEOUT_S:-600}s — continuing without rebuild; verify with 'ssh $ssh_target cat /home/leerie/.cache/leerie/plugin-install.log'"
  else
    remote_log "remote: plugin cache rebuild attempted but remote exec returned rc=$_rebuild_rc — continuing; check /home/leerie/.cache/leerie/plugin-install.log on the instance"
  fi

  remote_log "remote: ec2_seed_auth complete"
  return 0
}
