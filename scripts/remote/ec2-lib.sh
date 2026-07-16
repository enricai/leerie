#!/usr/bin/env bash
# scripts/remote/ec2-lib.sh — shared bash helpers for the EC2 lifecycle.
#
# Sourced by the `leerie` launcher's RUNTIME=ec2 branch, parallel to how
# scripts/remote/lib.sh is sourced by the RUNTIME=fly branch. Exports the
# host-side preflight (`require_aws`) and the remote-exec/tar-pipe
# transport primitive (`ec2_remote_exec` / `ec2_tar_pipe`) that stands in
# for `flyctl ssh console -C` — every EC2 seed/fetch script (this file's
# `ec2-seed-auth.sh` sibling, and future `ec2-provision.sh` /
# `ec2-ssm.sh` work) is built on top of it. Provisioning/instance-
# lifecycle helpers (RunInstances, wait-ready, teardown) land in a later
# subtask.
#
# shellcheck disable=SC1091
. "$(dirname "${BASH_SOURCE[0]}")/_log.sh"

# --- require_aws -----------------------------------------------------------
# Ensure the AWS CLI is on PATH and credentials resolve, before the EC2
# runtime tries to provision anything. Modeled directly on require_flyctl()
# in scripts/remote/lib.sh — same two-stage shape (binary present? →
# authenticated?) — and reuses the credential-error vocabulary already
# established by bedrock_preflight() in the `leerie` launcher (`aws sts
# get-caller-identity` as the auth probe, `aws sso login --profile <p>` as
# the recovery hint) rather than inventing a second one.
#
# Behavior:
#   1. command -v aws. If missing, print an actionable install hint
#      (AWS CLI v2 docs) and return 1. Unlike require_flyctl, this does not
#      auto-install — the AWS CLI's official installers write outside
#      $HOME (a pkg/msi installer, or /usr/local/bin via the bundled
#      installer's sudo install step) and auto-installing anything that
#      needs `sudo` from an unattended preflight is out of scope.
#   2. Resolve the profile: LEERIE_AWS_PROFILE > AWS_PROFILE (unset means
#      "no --profile flag" — let the CLI use its own default-profile
#      resolution, matching bedrock_preflight's behavior of only passing
#      --profile when one is actually configured).
#   3. `aws sts get-caller-identity` (with --profile when resolved). On
#      failure, print the `aws sso login --profile <profile>` (or bare
#      `aws sso login`) recovery hint and return 1.
#
# Honors:
#   LEERIE_AWS_PROFILE   preferred profile (falls back to AWS_PROFILE)
#
# Idempotent: safe to call multiple times. Returns 0 if the AWS CLI is
# ready to authenticate EC2 API calls.
require_aws() {
  if ! command -v aws >/dev/null 2>&1; then
    remote_log "error: 'aws' CLI not found on PATH."
    echo "  Install the AWS CLI v2: https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html" >&2
    echo "  or: brew install awscli (macOS)" >&2
    return 1
  fi

  local profile="${LEERIE_AWS_PROFILE:-${AWS_PROFILE:-}}"
  local aws_args=()
  [ -n "$profile" ] && aws_args+=(--profile "$profile")

  # Use ${arr[@]+"${arr[@]}"} so an empty array expands to nothing under
  # `set -u` (bash 3.2, the macOS system default, otherwise errors with
  # "aws_args[@]: unbound variable"). Same idiom and rationale as the
  # launcher's nerdctl argv assembly. `aws_args` is empty whenever no
  # profile is set, which is the common case.
  if ! aws sts get-caller-identity ${aws_args[@]+"${aws_args[@]}"} >/dev/null 2>&1; then
    remote_log "error: AWS credentials are expired or missing."
    if [ -n "$profile" ]; then
      echo "  Run: aws sso login --profile $profile" >&2
    else
      echo "  Run: aws sso login  (or set AWS_PROFILE/LEERIE_AWS_PROFILE and re-run)" >&2
    fi
    echo "  Then re-run leerie." >&2
    return 1
  fi
  return 0
}

# --- _seed_timeout_prefix ---------------------------------------------------
# Identical to lib.sh's helper of the same name (Fly path): emits
# `timeout --kill-after=5 ${LEERIE_SEED_TIMEOUT_S:-600}` so a stalled
# transport session yields a clean non-zero rc instead of hanging.
# Duplicated here (not sourced from lib.sh) because ec2-lib.sh must stay
# independently sourceable — pulling in lib.sh would drag Fly-specific
# state (require_fly_ssh, the private ssh-agent) into the EC2 runtime.
# On hosts without GNU `timeout` (BSD macOS w/o coreutils) this echoes
# nothing and the caller falls back to an unbounded pipe.
#
# Usage:
#   $(_seed_timeout_prefix) aws ssm start-session ...
_seed_timeout_prefix() {
  if ! command -v timeout >/dev/null 2>&1; then
    return 0
  fi
  printf 'timeout --kill-after=5 %s' "${LEERIE_SEED_TIMEOUT_S:-600}"
}

# --- ec2_remote_exec ---------------------------------------------------
# Run a single command on the target EC2 instance, mirroring `flyctl ssh
# console --pty=false -C "<cmd>"`.
#
# Transport: `aws ssm start-session --document-name
# AWS-StartInteractiveCommand --parameters command="<cmd>"` (DESIGN §6
# "Transport substitution for `flyctl ssh console`" — SSM Session
# Manager is the default for exec/attach roles: it needs no inbound
# security-group rule, no key-pair distribution, and no public IP, and
# reuses the same AWS credential chain `require_aws` already
# establishes). This is the exec half of the transport, for the two
# roles DESIGN §6 names as SSM's job: piping the detached-orchestrator
# launch wrapper, and opening an attach/tail session. It does NOT
# forward host stdin — `AWS-StartInteractiveCommand` is a document
# parameter, not a stdin pipe (SSM's own 4 KB document-parameter size
# ceiling rules out embedding any real payload in it, unlike `flyctl
# ssh console -C`). Bulk data transfer is `ec2_tar_pipe`'s job, over
# SSH — see that function's docstring for why the two roles need two
# different transports.
#
# Remote-rc muxing: unlike `flyctl ssh console` (which forwards the
# remote exit code natively) or plain `ssh` (same), `aws ssm
# start-session` ALWAYS exits 0 regardless of the remote command's exit
# status — a documented session-manager-plugin limitation (the plugin
# does not propagate the interactive command's exit code; see
# aws/session-manager-plugin#59, "Sessions opened with the session
# manager plugin always report an exit code of zero even when the last
# executed command ... returns a non-zero value"). To recover the real
# remote rc, the command this function actually runs on the instance is
# wrapped so it prints a sentinel line carrying its own exit code to
# stdout; this function strips that sentinel back off before returning
# the remote output, and returns the recovered rc as its own exit
# status — the SSM analog of lib.sh's `_extract_flyctl_remote_rc`,
# which recovers flyctl's muxed remote rc from a captured stderr file.
#
# Usage:
#   ec2_remote_exec <instance-id> <command>
#
# Honors LEERIE_AWS_PROFILE/AWS_PROFILE (via require_aws's precedence)
# and LEERIE_AWS_REGION/AWS_REGION for --profile/--region passthrough.
# Wrapped in $(_seed_timeout_prefix) so a stalled session yields rc
# 124/137 instead of hanging (the same failure mode `flyctl ssh console`
# has under a stalled WireGuard tunnel).
#
# Prints the remote command's stdout (sentinel stripped). Returns the
# remote command's exit code, or 124/137 if the transport itself
# stalled and timeout fired.
ec2_remote_exec() {
  local instance_id="$1"
  local cmd="$2"
  local profile="${LEERIE_AWS_PROFILE:-${AWS_PROFILE:-}}"
  local region="${LEERIE_AWS_REGION:-${AWS_REGION:-}}"
  local aws_args=(--target "$instance_id" \
                   --document-name AWS-StartInteractiveCommand)
  [ -n "$profile" ] && aws_args+=(--profile "$profile")
  [ -n "$region" ] && aws_args+=(--region "$region")

  local marker="__LEERIE_EC2_RC__"
  # Wrap the caller's command so it always emits a parseable rc sentinel
  # as its very last line, even on failure. `cmd` runs inside a
  # subshell `( ... )` rather than being chained with `;` directly —
  # if `cmd` itself calls `exit N` (common: `exit 1` on error), a bare
  # `cmd; __rc=$?; printf ...` chain would have the `exit` terminate
  # the wrapper immediately and skip the sentinel printf entirely. The
  # subshell contains that exit so $? is still capturable afterward.
  # The wrapped command is base64-encoded before it goes into
  # --parameters: `cmd` is caller-supplied and may itself contain
  # quotes/newlines, which would otherwise have to survive two nested
  # shells (the SSM document's own quoting AND the wrapping `bash -c`
  # in the sentinel printf) plus the CLI's own shell-word-splitting of
  # --parameters. Base64 sidesteps all of that — the remote side
  # decodes and executes verbatim, no escaping to get wrong.
  local wrapped_cmd
  wrapped_cmd="$(printf '( %s ); __rc=$?; printf "\n%s:%%d\n" "$__rc"' "$cmd" "$marker")"
  local wrapped_b64
  wrapped_b64="$(printf '%s' "$wrapped_cmd" | base64 | tr -d '\n')"
  aws_args+=(--parameters "command=[\"echo $wrapped_b64 | base64 -d | bash\"]")

  local _to=""
  _to="$(_seed_timeout_prefix)"

  local out
  out="$($_to aws ssm start-session "${aws_args[@]}" 2>&1)"
  local transport_rc=$?
  if [ "$transport_rc" -eq 124 ] || [ "$transport_rc" -eq 137 ]; then
    printf '%s\n' "$out"
    return "$transport_rc"
  fi

  local remote_rc
  remote_rc="$(printf '%s\n' "$out" | sed -n "s/^${marker}:\([0-9][0-9]*\)\$/\1/p" | tail -1)"
  printf '%s\n' "$out" | sed "/^${marker}:[0-9]*\$/d"
  if [ -z "$remote_rc" ]; then
    # Sentinel never arrived — the session itself failed before the
    # remote command could run (e.g. instance unreachable). Surface the
    # transport's own rc rather than fabricating a 0.
    return "$transport_rc"
  fi
  return "$remote_rc"
}

# --- ec2_tar_pipe --------------------------------------------------------
# Stream a gzipped tar of stdin through to the instance and extract it
# remotely, mirroring seed-auth.sh's
#   tar -czC "$STAGE" . | flyctl ssh console --pty=false -C "sh -c 'tar -xzC ...'"
# idiom.
#
# Transport: plain `ssh` (the key-pair-based fallback DESIGN §6 names
# for "operators whose account policy disallows the SSM Agent or
# Session Manager IAM permissions"), NOT `aws ssm start-session`. SSM's
# AWS-StartInteractiveCommand document takes its command as a bounded
# JSON parameter (a ~4 KB document-parameter ceiling) with no stdin-pipe
# facility at all — real bulk transfer over SSM requires a session-
# oriented port-forwarding document plus a second local relay process
# (e.g. `AWS-StartPortForwardingSession` + `nc`), a materially different
# and heavier mechanism than the single `tar | ssh ... -C '...'` pipe
# this function models. Plain `ssh` is, as DESIGN §6 puts it, "the
# closer textual analog to `flyctl ssh console`" for exactly this
# reason: it natively forwards stdin/stdout and the remote exit code,
# the same properties the Fly transport relies on — so the bulk-data
# role uses it directly rather than reimplementing SSM's missing stdin
# pipe. (`ec2_remote_exec` above is the SSM-based half, for the
# detached-launch and attach/tail roles DESIGN explicitly assigns to
# SSM.) Requires `LEERIE_EC2_KEY_NAME`'s key pair to already be usable
# for SSH auth against the instance (out of scope here — provisioning's
# job); this primitive only shapes the exec, not the credential setup.
#
# Usage:
#   tar -czC "$STAGE" . | ec2_tar_pipe <ssh-target> <extract-dir>
#
# <ssh-target> is whatever `ssh` accepts as its destination argument
# (e.g. "ec2-user@<public-ip>" or an ssh_config Host alias) — this
# function does not resolve instance id → address; the caller does.
#
# The extract command runs as whichever user the ssh target logs in as
# (typically the AMI's default user, e.g. `ec2-user`/`ubuntu`), NOT root
# — unlike `flyctl ssh console`, which always lands as root. Callers
# needing a different owner must `sudo chown` after, the EC2 analog of
# seed-auth.sh's `chown -R leerie:` step.
#
# Wrapped in $(_seed_timeout_prefix) so a stalled SSH session yields rc
# 124/137 instead of hanging. Returns the remote tar extraction's exit
# code (SSH forwards it natively — no rc-muxing workaround needed here,
# unlike ec2_remote_exec's SSM path).
ec2_tar_pipe() {
  local ssh_target="$1"
  local extract_dir="$2"
  local _to=""
  _to="$(_seed_timeout_prefix)"
  $_to ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new \
    "$ssh_target" \
    "sh -c 'mkdir -p '\''$extract_dir'\'' && tar -xzC '\''$extract_dir'\'''"
}

# --- resolve_* (LEERIE_EC2_* required-var reads) ---------------------------
# One thin helper per RunInstances parameter (IMPLEMENTATION.md "EC2
# instance-lifecycle vars"). Each prints the var's value on success; on an
# unset/empty var, prints an actionable error naming the missing var to
# stderr and returns 1 rather than letting `${VAR:?}` under `set -u` kill
# the whole sourcing shell with bash's generic "parameter null or not set"
# message. No defaults exist for any of these — DESIGN §6 / IMPLEMENTATION.md
# are explicit that there is no sensible AMI/instance-type/key-pair/
# security-group/subnet leerie can pick on the operator's behalf.
#
# Shared here (not in ec2-provision.sh) because ec2-ssm.sh's SSH-fallback
# transport also needs resolve_key_name/resolve_security_group.
_resolve_ec2_var() {
  local var_name="$1"
  local value="${!var_name:-}"
  if [ -z "$value" ]; then
    remote_log "error: $var_name is not set — required for --runtime ec2."
    echo "  Set $var_name and re-run. See docs/IMPLEMENTATION.md \"EC2 instance-lifecycle vars\"." >&2
    return 1
  fi
  printf '%s' "$value"
}

resolve_ami() { _resolve_ec2_var LEERIE_EC2_AMI; }
resolve_instance_type() { _resolve_ec2_var LEERIE_EC2_INSTANCE_TYPE; }
resolve_key_name() { _resolve_ec2_var LEERIE_EC2_KEY_NAME; }
resolve_security_group() { _resolve_ec2_var LEERIE_EC2_SECURITY_GROUP; }
resolve_subnet_id() { _resolve_ec2_var LEERIE_EC2_SUBNET_ID; }
