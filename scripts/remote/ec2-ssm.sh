#!/usr/bin/env bash
# scripts/remote/ec2-ssm.sh — SSM Session Manager transport substitution
# for `flyctl ssh console`'s *launch* and *attach* roles (DESIGN §6
# "Transport substitution for `flyctl ssh console`"; the stream-back role
# is ec2-fetch-branch.sh, already shipped; the small-command-exec and
# bulk-upload roles are ec2-lib.sh's ec2_remote_exec / ec2_tar_pipe,
# already shipped).
#
# `aws ssm start-session --target <instance-id> --document-name
# AWS-StartInteractiveCommand --parameters command="python3 -"` is the SSM
# analog of `flyctl ssh console --pty=false -C "python3 -"`. Unlike
# ec2_remote_exec (which embeds its whole wrapped command inside the
# `--parameters command=[...]` document parameter, capped at SSM's ~4 KB
# document-parameter ceiling — fine for the short probe/mkdir/chown
# commands ec2_remote_exec carries), the payloads here are the
# multi-kilobyte Python launch wrapper and the tail-wrapper script, both
# far over that cap. `AWS-StartInteractiveCommand` runs an interactive
# session via the local session-manager-plugin process — the same
# stdin/stdout-forwarding pty session flyctl's `ssh console -C "python3
# -"` gives us — so the fix is the same one flyctl's own path takes:
# keep `command="python3 -"` (or `"tail -F ..."`) tiny and pipe the
# actual payload to this function's *stdin*, which the session-manager-
# plugin forwards to the remote interpreter's stdin exactly like a
# regular interactive ssh session would. No 4 KB ceiling applies to that
# stream — only to the document parameter naming the command itself.
#
# Remote-rc muxing: `aws ssm start-session` (via the session-manager-
# plugin) exits 0 regardless of the remote command's real exit code —
# the same documented limitation ec2_remote_exec's docstring cites
# (aws/session-manager-plugin#59). This file reuses ec2_remote_exec's
# rc-sentinel convention: the piped payload is wrapped so its last line
# of stdout is a parseable `__LEERIE_EC2_RC__:<n>` sentinel, which this
# file's functions strip back off before returning the remote rc as
# their own exit status. This is the only path by which callers recover
# rc=75 (the flock-loser smart-resume pivot, DESIGN §6 "Smart resume in
# remote mode") — a raw remote failure or a truncated session yields
# rc=1 (no sentinel arrived) rather than a fabricated 0.
#
# Usage:
#   export LEERIE_EC2_INSTANCE_ID=i-0123456789abcdef0
#   printf '%s' "$launch_wrapper_script" | ec2_launch_detached
#   printf '%s' "$tail_wrapper_script"   | ec2_attach
#
# Both honor LEERIE_AWS_PROFILE/AWS_PROFILE and
# LEERIE_AWS_REGION/AWS_REGION for --profile/--region passthrough
# (ec2_remote_exec's existing precedence), and are wrapped in
# $(_seed_timeout_prefix) so a stalled session yields rc 124/137 instead
# of hanging.
#
# Both fail closed (return 1, actionable stderr) when
# LEERIE_EC2_INSTANCE_ID is empty — mirroring ec2-fetch-branch.sh's
# fetch_state_ec2 empty-check convention — rather than handing `aws` an
# empty --target and letting it fail with an opaque CLI error.
#
# An SSH fallback (LEERIE_EC2_KEY_NAME + an inbound security-group rule
# on port 22) is documented in DESIGN §6 as available for operators
# whose IAM policy disallows SSM, but is not implemented here — not the
# default, and not required for this file's baseline (ec2-lib.sh already
# exports resolve_key_name/resolve_security_group for that future path).

set -euo pipefail

_EC2_SSM_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Guard against double-sourcing clobbering an already-loaded ec2-lib.sh
# (mirrors ec2-fetch-branch.sh's / ec2-seed-repo.sh's independent-
# sourceability requirement).
if ! command -v ec2_remote_exec >/dev/null 2>&1; then
  # shellcheck disable=SC1091
  . "$_EC2_SSM_DIR/ec2-lib.sh"
fi

# --- _ec2_ssm_session ------------------------------------------------------
# Shared plumbing for ec2_launch_detached / ec2_attach: read the caller's
# payload from stdin and pipe it to `aws ssm start-session ...
# --parameters command="<wrapper>"`, recovering the remote exit code.
# `<interpreter>` ("python3 -" or "sh -s") is the interpreter that
# actually runs the caller's (potentially large) payload; this function's
# stdin is transparently forwarded to it by the session-manager-plugin's
# interactive session, so the ~4 KB document-parameter ceiling never
# applies to the payload itself — only to `<wrapper>`, which stays a
# fixed, tiny shell one-liner regardless of payload size.
#
# `<wrapper>` cannot be `( <payload> ); __rc=$?; ...` the way
# ec2_remote_exec wraps its (shell) command — here the payload is fed to
# a caller-chosen interpreter (python3, not necessarily sh), so the
# payload text itself must never be spliced into the wrapper string.
# Instead the wrapper is a small, payload-agnostic bash driver: it runs
# `<interpreter>` directly against its own inherited stdin (the
# payload), captures *that* process's real exit code, and prints the rc
# sentinel after — a subshell isn't needed here since the driver never
# executes the payload's text itself, so a payload-side `exit N` only
# ever terminates the interpreter subprocess, not this wrapper.
#
# Usage: _ec2_ssm_session <interpreter-cmd>   (payload piped via stdin)
_ec2_ssm_session() {
  local interpreter="$1"
  local instance_id="${LEERIE_EC2_INSTANCE_ID:-}"
  if [ -z "$instance_id" ]; then
    remote_log "error: LEERIE_EC2_INSTANCE_ID is not set — cannot open an SSM session."
    return 1
  fi

  local profile="${LEERIE_AWS_PROFILE:-${AWS_PROFILE:-}}"
  local region="${LEERIE_AWS_REGION:-${AWS_REGION:-}}"
  local marker="__LEERIE_EC2_RC__"
  local wrapper
  wrapper="$(printf '%s; __rc=$?; printf "\n%s:%%d\n" "$__rc"' "$interpreter" "$marker")"
  # Base64-encode the wrapper before it goes into --parameters, exactly
  # like ec2_remote_exec does for its own wrapped command: the wrapper
  # embeds literal double quotes (the `printf "..."` call), which would
  # otherwise have to survive the CLI's own JSON-array parsing of
  # `command=[...]` — base64 sidesteps that entirely, no escaping to get
  # wrong. The remote side decodes and runs it via `bash <(...)` — a
  # process substitution, NOT `... | bash`: piping the decode into bash
  # would consume bash's own stdin as the final stage of that pipeline,
  # shadowing the interactive session's real stdin (the caller's
  # payload) before `<interpreter>` ever gets to read it. Process
  # substitution hands bash the decoded script as a file argument
  # instead, leaving bash's stdin — and therefore the payload — free to
  # flow through to `<interpreter>` unmodified.
  local wrapper_b64
  wrapper_b64="$(printf '%s' "$wrapper" | base64 | tr -d '\n')"
  local aws_args=(--target "$instance_id" \
                   --document-name AWS-StartInteractiveCommand \
                   --parameters "command=[\"bash <(echo $wrapper_b64 | base64 -d)\"]")
  [ -n "$profile" ] && aws_args+=(--profile "$profile")
  [ -n "$region" ] && aws_args+=(--region "$region")

  local payload
  payload="$(cat)"

  local _to=""
  _to="$(_seed_timeout_prefix)"

  local out
  out="$(printf '%s' "$payload" | $_to aws ssm start-session "${aws_args[@]}" 2>&1)"
  local transport_rc=$?
  if [ "$transport_rc" -eq 124 ] || [ "$transport_rc" -eq 137 ]; then
    printf '%s\n' "$out"
    return "$transport_rc"
  fi

  local remote_rc
  remote_rc="$(printf '%s\n' "$out" | sed -n "s/^${marker}:\([0-9][0-9]*\)\$/\1/p" | tail -1)"
  printf '%s\n' "$out" | sed "/^${marker}:[0-9]*\$/d"
  if [ -z "$remote_rc" ]; then
    # Sentinel never arrived — the session failed before the payload
    # could run (e.g. instance unreachable, malformed session). Surface
    # the transport's own rc rather than fabricating a 0.
    if [ "$transport_rc" -eq 0 ]; then
      return 1
    fi
    return "$transport_rc"
  fi
  return "$remote_rc"
}

# --- ec2_launch_detached -----------------------------------------------
# SSM analog of `flyctl ssh console --pty=false -C "python3 -"`: start
# the detached-orchestrator launch wrapper on the target instance via a
# `python3 -` bootstrap, piping the actual wrapper script (passed on this
# function's stdin) through as the interpreter's stdin.
#
# Returns the launch wrapper's real remote exit code, notably rc=75 (the
# flock-loser smart-resume pivot: State.__init__'s own flock probe would
# also produce this, but the launch wrapper's own fast-path flock check
# — see the Fly launch script's fcntl probe — usually fires first).
ec2_launch_detached() {
  _ec2_ssm_session "python3 -"
}

# --- ec2_attach ----------------------------------------------------------
# SSM analog of `flyctl ssh console --pty=false -C "sh -s"`: run the
# tail-wrapper payload (passed on this function's stdin, e.g. from
# render_tail_wrapper) against the running orchestrator's log, or any
# other `sh`-shaped attach command (a bare interactive shell for
# `--shell`).
#
# Returns the remote command's real exit code (e.g. the tail wrapper's
# ORCH_EXIT, per lib.sh's render_tail_wrapper docstring).
ec2_attach() {
  _ec2_ssm_session "sh -s"
}

# --- _attach_to_live_orchestrator_ec2 ---------------------------------------
# EC2 counterpart of lib.sh's _attach_to_live_orchestrator: attach to an
# already-running orchestrator on the instance — either open a bash shell
# at /work (RESUME_SHELL=true) or tail the live log stream via
# render_tail_wrapper (lib.sh) + ec2_attach.
#
# Call sites (leerie launcher's RUNTIME=ec2 branch):
#   - The early flock probe (resume path, before seed_auth).
#   - The launch-wrapper rc=75 pivot (after seed_auth, belt-and-suspenders).
#
# Reads from caller's scope: RESUME_SHELL, LEERIE_RUN_ID,
# LEERIE_EC2_INSTANCE_ID.
#
# No auto-finalize plumbing here (unlike lib.sh's
# tail_with_optional_autofinalize) — EC2 auto-finalize integration is
# deferred to a later subtask, mirroring decide_ec2_teardown's own "no
# auto-finalize integration yet" scope note.
#
# Side-effect: sets container_rc=130 in the caller's scope so
# decide_ec2_teardown leaves the instance alone (DESIGN §6 "Smart resume
# in remote mode").
_attach_to_live_orchestrator_ec2() {
  if [ "${RESUME_SHELL:-false}" = "true" ]; then
    remote_log "--resume: orchestrator already running for $LEERIE_RUN_ID; opening shell at /work"
    local _shell_payload="cd /work && PS1='leerie@$LEERIE_RUN_ID:\\w\\$ ' exec bash --noprofile --norc -i"
    printf '%s' "$_shell_payload" | ec2_attach || true
  else
    remote_log "--resume: orchestrator already running for $LEERIE_RUN_ID; attaching to live log stream (Ctrl-C to detach — orchestrator keeps running)"
    local _tail_script _tail_invocation
    _tail_script="$(render_tail_wrapper)"
    _tail_invocation="LEERIE_TAIL_RUN_ID='$LEERIE_RUN_ID'; export LEERIE_TAIL_RUN_ID
$_tail_script"
    printf '%s' "$_tail_invocation" | ec2_attach || true
  fi
  container_rc=130
}
