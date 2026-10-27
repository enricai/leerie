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
# shellcheck disable=SC1091
. "$(dirname "${BASH_SOURCE[0]}")/seed-common.sh"

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

  if [ -z "$(_ec2_region)" ]; then
    remote_log "error: no AWS region configured."
    echo "  Set: --aws-region <region> or LEERIE_AWS_REGION (or run: aws configure set region <region>), then re-run leerie." >&2
    return 1
  fi

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
# Shared with the Fly path (lib.sh); single definition site is
# scripts/remote/seed-common.sh, sourced above alongside _log.sh. Not
# sourced from lib.sh directly — ec2-lib.sh must stay independently
# sourceable, and pulling in lib.sh would drag Fly-specific state
# (require_fly_ssh, the private ssh-agent) into the EC2 runtime.

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
  # shellcheck disable=SC2016  # $? and $__rc are evaluated by the REMOTE
  # shell this string is sent to, so they must survive unexpanded
  wrapped_cmd="$(printf '( %s ); __rc=$?; printf "\n%s:%%d\n" "$__rc"' "$cmd" "$marker")"
  local wrapped_b64
  wrapped_b64="$(printf '%s' "$wrapped_cmd" | base64 | tr -d '\n')"
  aws_args+=(--parameters "command=[\"$(_ec2_ssm_sudo_command "$wrapped_b64")\"]")

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
    # remote command could run (e.g. instance unreachable, or sudo
    # refused). Surface the transport's own rc rather than fabricating a
    # 0, and since `aws ssm start-session` exits 0 regardless, a 0 here
    # is still a failure (same rule as ec2-ssm.sh's _ec2_ssm_session).
    [ "$transport_rc" -eq 0 ] && return 1
    return "$transport_rc"
  fi
  return "$remote_rc"
}

# --- _ec2_ssm_sudo_command -------------------------------------------------
# The --parameters command for an SSM session that runs a base64-encoded
# script as root. Session Manager lands as ssm-user, never root (DESIGN §6
# "Neither transport lands as root"), and the payloads chown, write
# leerie-owned paths and drop to the leerie user. The decoded script goes
# to a temp file rather than `bash <(...)` (sudo closes inherited fds, so
# the /dev/fd path is gone for the elevated shell) or `... | bash` (that
# would consume the session's stdin, which carries ec2_launch_detached's
# payload). No double quotes: the result is embedded in the CLI's
# `command=["..."]` shorthand. -n fails fast instead of prompting.
_ec2_ssm_sudo_command() {
  # shellcheck disable=SC2016  # expanded by the REMOTE shell
  printf 'f=$(mktemp) && echo %s | base64 -d > $f && sudo -n bash $f; __r=$?; rm -f $f; exit $__r' "$1"
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
# The ssh target logs in as `ec2-user`, not root (unlike `flyctl ssh
# console`), and the extract dirs (/home/leerie, /work) are leerie-owned,
# so the extract runs under `sudo -n`; callers still `chown -R leerie:`
# afterwards, the EC2 analog of seed-auth.sh's step.
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
  local _id=() _a
  while IFS= read -r _a; do _id+=("$_a"); done < <(ec2_ssh_identity_args; ec2_ssh_known_hosts_args)
  $_to ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new \
    ${_id[@]+"${_id[@]}"} \
    "$ssh_target" \
    "sudo -n sh -c 'mkdir -p '\''$extract_dir'\'' && tar -xzC '\''$extract_dir'\'''"
}

# --- resolve_* (LEERIE_EC2_* required-var reads) ---------------------------
# One thin helper per RunInstances parameter (IMPLEMENTATION.md "EC2
# instance-lifecycle vars"). By the time this is sourced, the `leerie`
# launcher has already resolved each LEERIE_EC2_* var through its own
# CLI > env > leerie.toml tier (mirroring FLY_VM_DISK_GB / the seed knobs)
# and exported the result — this helper is just the final required-var
# read against whatever that ladder produced. Each prints the var's value,
# or nothing when unset: every knob is an optional override, and the
# empty case is filled by ec2-defaults.sh / ec2-ami.sh.
#
# Shared here (not in ec2-provision.sh) because ec2-ssm.sh's SSH-fallback
# transport also needs resolve_key_name/resolve_security_group.
_resolve_ec2_var() {
  # Unset is success-with-empty: every knob has a default supplied by
  # ec2-defaults.sh, so the caller decides whether empty is acceptable and
  # names the fix (e.g. --aws-region, aws ec2 create-default-vpc).
  printf '%s' "${!1:-}"
}

resolve_ami() { _resolve_ec2_var LEERIE_EC2_AMI; }
resolve_instance_type() { _resolve_ec2_var LEERIE_EC2_INSTANCE_TYPE; }
resolve_key_name() { _resolve_ec2_var LEERIE_EC2_KEY_NAME; }
resolve_security_group() { _resolve_ec2_var LEERIE_EC2_SECURITY_GROUP; }
resolve_subnet_id() { _resolve_ec2_var LEERIE_EC2_SUBNET_ID; }
resolve_instance_profile() { _resolve_ec2_var LEERIE_EC2_INSTANCE_PROFILE; }

# --- _aws_region_profile_args ---------------------------------------------
# Emit, one token per line, the --region/--profile flags to append to
# every `aws ec2`/`aws sts` invocation in this file. Emits nothing when
# both are unset — lets the aws CLI's own credential chain resolve
# region/profile, matching require_aws()'s existing profile-optional
# pattern in ec2-lib.sh.
#
# One-token-per-line (not a bare string) so values containing spaces
# survive: a named profile may legitimately contain them, and word
# splitting would shred it. Callers read it back with the `while read`
# idiom below rather than a nameref (`local -n`), which is bash 4.3+ and
# fails outright on macOS's /bin/bash 3.2 with "local: -n: invalid
# option".
#
# Usage:
#   local aws_args=()
#   while IFS= read -r _a; do aws_args+=("$_a"); done \
#     < <(_aws_region_profile_args)
_aws_region_profile_args() {
  local region="${LEERIE_AWS_REGION:-${AWS_REGION:-}}"
  local profile="${LEERIE_AWS_PROFILE:-${AWS_PROFILE:-}}"
  if [ -n "$region" ]; then
    printf '%s\n%s\n' --region "$region"
  fi
  if [ -n "$profile" ]; then
    printf '%s\n%s\n' --profile "$profile"
  fi
  return 0
}

# --- _ec2_region -------------------------------------------------------------
# The region every EC2 helper resolves, in the AWS CLI's own order. One
# resolver so preflight (require_aws) can never accept a region the key path
# or the bake cannot find: `aws configure get` reads only the config file,
# never AWS_DEFAULT_REGION, so the env tiers come first.
_ec2_region() {
  local r="${LEERIE_AWS_REGION:-${AWS_REGION:-${AWS_DEFAULT_REGION:-}}}"
  if [ -z "$r" ]; then
    local profile="${LEERIE_AWS_PROFILE:-${AWS_PROFILE:-}}"
    r="$(aws configure get region ${profile:+--profile "$profile"} 2>/dev/null || true)"
  fi
  printf '%s' "$r"
}

# --- ec2_default_key_file ---------------------------------------------------
# Path of leerie's own private key for the active account + region. The AWS
# key pair is account- and region-scoped, so the file is too — a per-repo
# location made a second repo find the key pair but not its key. The dot
# prefix keeps per-repo `$HOME/.leerie/*` globs (prune, list --groups) off
# it. Prints nothing and returns 1 when the account cannot be resolved.
EC2_KEY_NAME_DEFAULT="leerie-ssh"
ec2_default_key_file() {
  local args=() _a account region
  while IFS= read -r _a; do args+=("$_a"); done < <(_aws_region_profile_args)
  account="$(aws sts get-caller-identity ${args[@]+"${args[@]}"} --output json 2>/dev/null \
    | python3 -c 'import json,sys
try: print(json.load(sys.stdin).get("Account",""))
except Exception: pass' 2>/dev/null)"
  region="$(_ec2_region)"
  [ -n "$account" ] && [ -n "$region" ] || return 1
  printf '%s/.leerie/.ec2-keys/%s/%s/%s.pem' "$HOME" "$account" "$region" "$EC2_KEY_NAME_DEFAULT"
}

# _ec2_ssh_key_file — the private key ssh should use: LEERIE_EC2_SSH_KEY_FILE
# when set, else leerie's own key when no explicit LEERIE_EC2_KEY_NAME says
# the operator owns the key pair. Empty when neither applies.
_ec2_ssh_key_file() {
  if [ -n "${LEERIE_EC2_SSH_KEY_FILE:-}" ]; then
    printf '%s' "$LEERIE_EC2_SSH_KEY_FILE"
  elif [ -z "${LEERIE_EC2_KEY_NAME:-}" ]; then
    ec2_default_key_file || true
  fi
}

# --- ec2_ssh_identity_args --------------------------------------------------
# Emit, one token per line, `-i <key> -o IdentitiesOnly=yes` when a known
# private key exists (_ec2_ssh_key_file); nothing otherwise, so an
# operator-supplied LEERIE_EC2_KEY_NAME with no known private key leaves
# ssh's own identity selection untouched. Read with the `while read` idiom
# (bash 3.2: no namerefs/mapfile).
ec2_ssh_identity_args() {
  local key
  key="$(_ec2_ssh_key_file)"
  if [ -n "$key" ] && [ -f "$key" ]; then
    printf '%s\n%s\n%s\n%s\n' -i "$key" -o IdentitiesOnly=yes
  fi
  return 0
}

# --- ec2_ssh_known_hosts_args -----------------------------------------------
# Emit, one token per line, `-o UserKnownHostsFile=<file>` for the instance in
# LEERIE_EC2_INSTANCE_ID; nothing when it is unset. Host keys are trusted on
# first use per instance, never in the user's own known_hosts: EC2 recycles
# public IPs, and accept-new refuses a reused address's new host key.
_ec2_known_hosts_file() {
  local iid="${LEERIE_EC2_INSTANCE_ID:-}"
  [ -n "$iid" ] || return 0
  local dir="$HOME/.leerie/.ec2-keys/known_hosts"
  ( umask 077; mkdir -p "$dir" ) 2>/dev/null || return 0
  printf '%s/%s' "$dir" "$iid"
}

ec2_ssh_known_hosts_args() {
  local f
  f="$(_ec2_known_hosts_file)"
  [ -n "$f" ] && printf '%s\n%s\n' -o "UserKnownHostsFile=$f"
  return 0
}

_ec2_ssh_known_hosts_string() {
  local f
  f="$(_ec2_known_hosts_file)"
  [ -n "$f" ] && printf -- "-o UserKnownHostsFile='%s'" "$f"
  return 0
}

# _ec2_ssh_identity_string — same flags as one shell-quoted string, for the
# generated rsync -e wrapper script.
_ec2_ssh_identity_string() {
  local key
  key="$(_ec2_ssh_key_file)"
  if [ -n "$key" ] && [ -f "$key" ]; then
    printf -- "-i '%s' -o IdentitiesOnly=yes" "$key"
  fi
  return 0
}
