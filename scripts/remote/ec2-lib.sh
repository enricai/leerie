#!/usr/bin/env bash
# scripts/remote/ec2-lib.sh — shared bash helpers for the EC2 lifecycle.
#
# Sourced by the `leerie` launcher's RUNTIME=ec2 branch, parallel to how
# scripts/remote/lib.sh is sourced by the RUNTIME=fly branch. Today this
# file provides only the host-side preflight (`require_aws`); the
# provisioning/seed/teardown helpers land in later subtasks.
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

  if ! aws sts get-caller-identity "${aws_args[@]}" >/dev/null 2>&1; then
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
