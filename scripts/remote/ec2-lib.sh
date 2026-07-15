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
