#!/usr/bin/env bash
# scripts/remote/aws-credentials.sh — resolve AWS credentials/profile/region
# on the host, in the same precedence order the AWS CLI and SDKs use, so
# leerie's EC2 runtime authenticates as the identity the user already
# expects from `aws` commands run in the same shell.
#
# Pure file I/O + bash/python3 stdlib — no `aws` binary and no boto3/
# botocore dependency (mirrors the existing Bedrock-mode precedent:
# `detect_bedrock_mode()`/`bedrock_preflight()` in the `leerie` launcher,
# and IMPLEMENTATION.md:621's documented fact that the AWS SDK resolves
# credentials via pure file I/O against ~/.aws/config and
# ~/.aws/sso/cache/*.json). This keeps the helper testable against
# fixtures with no network and no dependency on config-005 (the AWS SDK
# packaging subtask) having landed.
#
# Standard SDK/CLI credential precedence (AWS SDKs and Tools standardized
# credential providers — static keys, then SSO/assume-role, then shared
# config/credentials files, then IMDS instance-role last):
#   1. AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY (+ optional
#      AWS_SESSION_TOKEN) environment variables.
#   2. Named profile (AWS_PROFILE, else [default]) in ~/.aws/config /
#      ~/.aws/credentials:
#        a. Static aws_access_key_id/aws_secret_access_key in
#           ~/.aws/credentials.
#        b. SSO (sso_session block or legacy inline sso_start_url) —
#           resolved via the cached token in ~/.aws/sso/cache/*.json.
#   3. EC2 instance role via IMDS — only meaningful when this code runs
#      ON an EC2 instance; on the operator's host (the only place this
#      script runs today) there is no instance role to fall back to, so
#      the chain ends here with an actionable error instead of silently
#      returning nothing.
#
# Region precedence (same standardized ordering, AWS_REGION beats the
# older AWS_DEFAULT_REGION which boto3 also honors):
#   AWS_REGION > AWS_DEFAULT_REGION > profile `region` > die-with-hint.
#
# Usage:
#   source scripts/remote/aws-credentials.sh
#   resolve_aws_credentials [--profile NAME] [--region NAME]
# On success, prints shell-sourceable `export KEY=value` lines for
# AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_SESSION_TOKEN (if any),
# and AWS_REGION to stdout, and returns 0. On failure, prints nothing to
# stdout, prints an actionable error to stderr, and returns 1.

# --- _aws_dir --------------------------------------------------------------
# Root of the AWS config directory. Honors AWS_CONFIG_FILE's directory when
# set (matching the SDK's AWS_SHARED_CREDENTIALS_FILE/AWS_CONFIG_FILE
# overrides is out of scope for this helper — leerie does not need per-file
# overrides — but ~/.aws is resolved once here so every helper agrees).
_aws_dir() {
  printf '%s/.aws' "$HOME"
}

# --- _aws_resolve_profile_name ----------------------------------------------
# Echo the profile name to use: --profile arg > AWS_PROFILE env > "default".
_aws_resolve_profile_name() {
  local cli_profile="$1"
  if [ -n "$cli_profile" ]; then
    printf '%s' "$cli_profile"
  elif [ -n "${AWS_PROFILE:-}" ]; then
    printf '%s' "$AWS_PROFILE"
  else
    printf 'default'
  fi
}

# --- _aws_read_config_value --------------------------------------------------
# _aws_read_config_value <ini-file> <section> <key>
# Print the value, or nothing (rc 1) if the file/section/key is absent.
# `section` must already be the raw ConfigParser section name (e.g.
# "profile dev", "default", "sso-session my-sso").
_aws_read_config_value() {
  local file="$1" section="$2" key="$3"
  [ -f "$file" ] || return 1
  python3 - "$file" "$section" "$key" <<'PY'
import configparser
import sys

path, section, key = sys.argv[1], sys.argv[2], sys.argv[3]
cp = configparser.ConfigParser()
try:
    cp.read(path)
except Exception:
    sys.exit(1)
if not cp.has_section(section) or key not in cp[section]:
    sys.exit(1)
print(cp[section][key])
PY
}

# --- _aws_credentials_section_name ------------------------------------------
# ~/.aws/credentials uses bare profile names (no "profile " prefix), even
# for non-default profiles — unlike ~/.aws/config.
_aws_credentials_section_name() {
  printf '%s' "$1"
}

# --- _aws_config_section_name ------------------------------------------------
# ~/.aws/config prefixes every profile but "default" with "profile ".
_aws_config_section_name() {
  local profile="$1"
  if [ "$profile" = "default" ]; then
    printf 'default'
  else
    printf 'profile %s' "$profile"
  fi
}

# --- _aws_static_credentials --------------------------------------------------
# Look up static aws_access_key_id/aws_secret_access_key/aws_session_token
# for `profile` in ~/.aws/credentials. Prints three lines
# (access_key, secret_key, session_token — session_token may be empty) and
# returns 0 on a hit; returns 1 with no output on a miss.
_aws_static_credentials() {
  local profile="$1" creds_file
  creds_file="$(_aws_dir)/credentials"
  local section
  section="$(_aws_credentials_section_name "$profile")"
  local access_key secret_key session_token
  access_key="$(_aws_read_config_value "$creds_file" "$section" aws_access_key_id)" || return 1
  secret_key="$(_aws_read_config_value "$creds_file" "$section" aws_secret_access_key)" || return 1
  session_token="$(_aws_read_config_value "$creds_file" "$section" aws_session_token)" || true
  printf '%s\n%s\n%s\n' "$access_key" "$secret_key" "$session_token"
}

# --- _aws_sso_start_url_and_region --------------------------------------------
# Resolve the (sso_start_url, sso_region) pair for `profile`, handling both
# the modern sso_session-reference form and the legacy inline form. Prints
# two lines; returns 1 if the profile has no SSO configuration at all.
_aws_sso_start_url_and_region() {
  local profile="$1" config_file section
  config_file="$(_aws_dir)/config"
  section="$(_aws_config_section_name "$profile")"

  local sso_session
  sso_session="$(_aws_read_config_value "$config_file" "$section" sso_session)" || true
  if [ -n "$sso_session" ]; then
    local session_section start_url region
    session_section="sso-session $sso_session"
    start_url="$(_aws_read_config_value "$config_file" "$session_section" sso_start_url)" || return 1
    region="$(_aws_read_config_value "$config_file" "$session_section" sso_region)" || return 1
    printf '%s\n%s\n' "$start_url" "$region"
    return 0
  fi

  # Legacy inline form: sso_start_url/sso_region live directly on the profile.
  local start_url region
  start_url="$(_aws_read_config_value "$config_file" "$section" sso_start_url)" || return 1
  region="$(_aws_read_config_value "$config_file" "$section" sso_region)" || true
  printf '%s\n%s\n' "$start_url" "$region"
}

# --- _aws_sso_cache_token ------------------------------------------------------
# Given an sso_start_url, find its cached token in ~/.aws/sso/cache/*.json
# (filename is sha1(start_url).json, but every *.json is scanned by content
# so a renamed/copied cache file still matches) and print the accessToken
# if present AND unexpired. Returns:
#   0  — valid token found; prints the access token on stdout.
#   1  — no cache file matches this start_url at all (never logged in).
#   2  — a matching cache file exists but its token is expired.
_aws_sso_cache_token() {
  local start_url="$1" cache_dir
  cache_dir="$(_aws_dir)/sso/cache"
  [ -d "$cache_dir" ] || return 1
  python3 - "$cache_dir" "$start_url" <<'PY'
import glob
import json
import os
import sys
from datetime import datetime, timezone

cache_dir, start_url = sys.argv[1], sys.argv[2]

best_expired = False
for path in glob.glob(os.path.join(cache_dir, "*.json")):
    try:
        data = json.load(open(path))
    except Exception:
        continue
    if data.get("startUrl") != start_url:
        continue
    token = data.get("accessToken")
    expires_at = data.get("expiresAt")
    if not token or not expires_at:
        continue
    try:
        # expiresAt is ISO-8601, e.g. "2026-07-15T18:30:00Z" or with an
        # explicit offset. Normalize the trailing "Z" (not accepted by
        # fromisoformat on Python < 3.11) to "+00:00".
        exp = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    except Exception:
        continue
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    if exp > datetime.now(timezone.utc):
        print(token)
        sys.exit(0)
    best_expired = True

sys.exit(2 if best_expired else 1)
PY
}

# --- _aws_die_with_hint --------------------------------------------------------
_aws_die_with_hint() {
  echo "aws-credentials: error: $1" >&2
  if [ -n "${2:-}" ]; then
    echo "  $2" >&2
  fi
}

# --- resolve_aws_credentials ---------------------------------------------------
# resolve_aws_credentials [--profile NAME] [--region NAME]
#
# Resolves credentials and region using the standard AWS SDK/CLI precedence
# and prints `export KEY=value` lines to stdout on success (rc 0). On
# failure, prints an actionable error to stderr and returns 1 (nothing on
# stdout).
resolve_aws_credentials() {
  local cli_profile="" cli_region=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --profile) cli_profile="$2"; shift 2 ;;
      --region) cli_region="$2"; shift 2 ;;
      *)
        _aws_die_with_hint "unknown argument: $1"
        return 1
        ;;
    esac
  done

  local access_key="" secret_key="" session_token="" source=""

  # 1. Explicit env-var credentials — highest precedence, always wins
  #    regardless of profile/SSO state.
  if [ -n "${AWS_ACCESS_KEY_ID:-}" ] && [ -n "${AWS_SECRET_ACCESS_KEY:-}" ]; then
    access_key="$AWS_ACCESS_KEY_ID"
    secret_key="$AWS_SECRET_ACCESS_KEY"
    session_token="${AWS_SESSION_TOKEN:-}"
    source="environment variables"
  fi

  local profile
  profile="$(_aws_resolve_profile_name "$cli_profile")"

  if [ -z "$source" ]; then
    if [ ! -d "$(_aws_dir)" ]; then
      _aws_die_with_hint \
        "no AWS credentials found (no \$AWS_ACCESS_KEY_ID/\$AWS_SECRET_ACCESS_KEY and no $(_aws_dir) directory)." \
        "Run 'aws configure' or 'aws configure sso' to set up a profile, or export AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY."
      return 1
    fi

    # 2a. Static credentials for the resolved profile.
    local static_out
    if static_out="$(_aws_static_credentials "$profile")"; then
      access_key="$(sed -n '1p' <<<"$static_out")"
      secret_key="$(sed -n '2p' <<<"$static_out")"
      session_token="$(sed -n '3p' <<<"$static_out")"
      source="profile '$profile' (static credentials)"
    else
      # 2b. SSO — via sso_session reference or legacy inline config.
      local sso_out start_url sso_region
      if sso_out="$(_aws_sso_start_url_and_region "$profile")"; then
        start_url="$(sed -n '1p' <<<"$sso_out")"
        sso_region="$(sed -n '2p' <<<"$sso_out")"
        local token rc
        token="$(_aws_sso_cache_token "$start_url")"
        rc=$?
        if [ "$rc" -eq 0 ]; then
          access_key=""
          secret_key=""
          session_token="$token"
          source="profile '$profile' (SSO)"
        elif [ "$rc" -eq 2 ]; then
          _aws_die_with_hint \
            "SSO session for profile '$profile' has expired." \
            "Run: aws sso login --profile $profile"
          return 1
        else
          _aws_die_with_hint \
            "SSO session for profile '$profile' is not signed in." \
            "Run: aws sso login --profile $profile"
          return 1
        fi
      else
        # 3. No env creds, no static creds, no SSO config for this
        #    profile. On the operator's host there is no EC2 instance
        #    role to fall back to (IMDS is only reachable from inside
        #    an EC2 instance) — end the chain here with a hint rather
        #    than silently returning nothing.
        if [ -n "$cli_profile" ] || [ -n "${AWS_PROFILE:-}" ]; then
          _aws_die_with_hint \
            "no credentials found for profile '$profile'." \
            "Check $(_aws_dir)/credentials and $(_aws_dir)/config, or run 'aws configure sso --profile $profile'."
        else
          _aws_die_with_hint \
            "no AWS credentials found (no \$AWS_ACCESS_KEY_ID/\$AWS_SECRET_ACCESS_KEY, no default profile in $(_aws_dir))." \
            "Run 'aws configure' or 'aws configure sso', or export AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY."
        fi
        return 1
      fi
    fi
  fi

  # Region: AWS_REGION > AWS_DEFAULT_REGION > profile region > die-with-hint.
  # A --region argument, when given, is treated the same as AWS_REGION
  # (explicit-wins), consistent with how --profile overrides AWS_PROFILE.
  local region="$cli_region"
  if [ -z "$region" ]; then region="${AWS_REGION:-}"; fi
  if [ -z "$region" ]; then region="${AWS_DEFAULT_REGION:-}"; fi
  if [ -z "$region" ]; then
    region="$(_aws_read_config_value "$(_aws_dir)/config" "$(_aws_config_section_name "$profile")" region)" || true
  fi
  if [ -z "$region" ]; then
    _aws_die_with_hint \
      "no AWS region resolved for profile '$profile'." \
      "Set AWS_REGION, or add 'region = <region>' to profile '$profile' in $(_aws_dir)/config."
    return 1
  fi

  printf 'export AWS_ACCESS_KEY_ID=%q\n' "$access_key"
  printf 'export AWS_SECRET_ACCESS_KEY=%q\n' "$secret_key"
  if [ -n "$session_token" ]; then
    printf 'export AWS_SESSION_TOKEN=%q\n' "$session_token"
  fi
  printf 'export AWS_REGION=%q\n' "$region"
  return 0
}
