"""Tests for scripts/remote/aws-credentials.sh.

aws-credentials.sh resolves AWS credentials/profile/region on the host in
the same precedence order the AWS CLI and SDKs use (AWS SDKs and Tools
standardized credential providers: explicit static credentials first, then
profile-based SSO/static credentials, then — on an actual EC2 instance,
out of scope here — IMDS instance-role credentials last).

These tests source the real script directly via `source <path>; <call>`,
mirroring tests/test_fetch_branch_sh.py's pattern, against a fake $HOME
with fixture ~/.aws/config, ~/.aws/credentials, and ~/.aws/sso/cache/
files — pure file I/O, no network, no `aws` binary, no boto3.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
AWS_CREDS_SH = REPO_ROOT / "scripts" / "remote" / "aws-credentials.sh"

SSO_START_URL = "https://my-sso-portal.awsapps.com/start"


def _run_bash(script: str, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", script],
        env=env,
        capture_output=True,
        text=True,
    )


def _base_env(home: Path, **extra: str) -> dict:
    env = {
        "HOME": str(home),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
    }
    env.update(extra)
    return env


def _write_sso_cache(home: Path, start_url: str, *, expires_delta: timedelta,
                      token: str = "FAKE_TOKEN") -> None:
    cache_dir = home / ".aws" / "sso" / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha1(start_url.encode()).hexdigest()
    expires_at = (datetime.now(timezone.utc) + expires_delta).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    payload = {
        "startUrl": start_url,
        "region": "us-east-1",
        "accessToken": token,
        "expiresAt": expires_at,
    }
    (cache_dir / f"{digest}.json").write_text(json.dumps(payload))


def test_aws_credentials_sh_exists_and_is_executable():
    assert AWS_CREDS_SH.is_file()
    assert os.access(AWS_CREDS_SH, os.X_OK)


def test_env_credentials_win_over_named_profile(tmp_path):
    """Explicit AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY beat any profile,
    even a fully-configured SSO profile with a valid cached token."""
    home = tmp_path / "home"
    home.mkdir()
    (home / ".aws").mkdir()
    (home / ".aws" / "config").write_text(
        "[profile dev]\n"
        "region = us-west-2\n"
        "sso_session = my-sso\n"
        "\n"
        "[sso-session my-sso]\n"
        f"sso_region = us-east-1\n"
        f"sso_start_url = {SSO_START_URL}\n"
    )
    _write_sso_cache(home, SSO_START_URL, expires_delta=timedelta(hours=1))

    env = _base_env(
        home,
        AWS_PROFILE="dev",
        AWS_ACCESS_KEY_ID="AKIAENVKEY",
        AWS_SECRET_ACCESS_KEY="envsecret",
    )
    result = _run_bash(
        f"source {AWS_CREDS_SH}; resolve_aws_credentials", env
    )
    assert result.returncode == 0, result.stderr
    assert "AWS_ACCESS_KEY_ID=AKIAENVKEY" in result.stdout
    assert "AWS_SECRET_ACCESS_KEY=envsecret" in result.stdout
    # Env creds path carries no session token; SSO must not have been used.
    assert "FAKE_TOKEN" not in result.stdout


def test_aws_profile_env_selects_named_profile_over_default(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".aws").mkdir()
    (home / ".aws" / "config").write_text(
        "[default]\n"
        "region = us-east-1\n"
        "\n"
        "[profile dev]\n"
        "region = us-west-2\n"
    )
    (home / ".aws" / "credentials").write_text(
        "[default]\n"
        "aws_access_key_id = AKIADEFAULT\n"
        "aws_secret_access_key = defaultsecret\n"
        "\n"
        "[dev]\n"
        "aws_access_key_id = AKIADEV\n"
        "aws_secret_access_key = devsecret\n"
    )

    env = _base_env(home, AWS_PROFILE="dev")
    result = _run_bash(
        f"source {AWS_CREDS_SH}; resolve_aws_credentials", env
    )
    assert result.returncode == 0, result.stderr
    assert "AWS_ACCESS_KEY_ID=AKIADEV" in result.stdout
    assert "AWS_SECRET_ACCESS_KEY=devsecret" in result.stdout
    assert "AWS_REGION=us-west-2" in result.stdout


def test_default_profile_used_when_no_profile_selected(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".aws").mkdir()
    (home / ".aws" / "config").write_text("[default]\nregion = us-east-1\n")
    (home / ".aws" / "credentials").write_text(
        "[default]\n"
        "aws_access_key_id = AKIADEFAULT\n"
        "aws_secret_access_key = defaultsecret\n"
    )

    env = _base_env(home)
    result = _run_bash(
        f"source {AWS_CREDS_SH}; resolve_aws_credentials", env
    )
    assert result.returncode == 0, result.stderr
    assert "AWS_ACCESS_KEY_ID=AKIADEFAULT" in result.stdout
    assert "AWS_REGION=us-east-1" in result.stdout


def test_region_precedence_aws_region_over_default_region_over_profile(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".aws").mkdir()
    (home / ".aws" / "config").write_text("[default]\nregion = us-east-1\n")
    (home / ".aws" / "credentials").write_text(
        "[default]\n"
        "aws_access_key_id = AKIADEFAULT\n"
        "aws_secret_access_key = defaultsecret\n"
    )

    # profile region only.
    result = _run_bash(
        f"source {AWS_CREDS_SH}; resolve_aws_credentials",
        _base_env(home),
    )
    assert result.returncode == 0, result.stderr
    assert "AWS_REGION=us-east-1" in result.stdout

    # AWS_DEFAULT_REGION beats profile region.
    result = _run_bash(
        f"source {AWS_CREDS_SH}; resolve_aws_credentials",
        _base_env(home, AWS_DEFAULT_REGION="eu-west-1"),
    )
    assert result.returncode == 0, result.stderr
    assert "AWS_REGION=eu-west-1" in result.stdout

    # AWS_REGION beats AWS_DEFAULT_REGION.
    result = _run_bash(
        f"source {AWS_CREDS_SH}; resolve_aws_credentials",
        _base_env(home, AWS_REGION="ap-south-1", AWS_DEFAULT_REGION="eu-west-1"),
    )
    assert result.returncode == 0, result.stderr
    assert "AWS_REGION=ap-south-1" in result.stdout


def test_region_die_with_hint_when_absent_everywhere(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".aws").mkdir()
    (home / ".aws" / "config").write_text("[default]\n")
    (home / ".aws" / "credentials").write_text(
        "[default]\n"
        "aws_access_key_id = AKIADEFAULT\n"
        "aws_secret_access_key = defaultsecret\n"
    )

    result = _run_bash(
        f"source {AWS_CREDS_SH}; resolve_aws_credentials",
        _base_env(home),
    )
    assert result.returncode != 0
    assert "region" in result.stderr.lower()
    assert result.stdout == ""


def test_expired_sso_cache_token_produces_hint_not_silent_fallthrough(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".aws").mkdir()
    (home / ".aws" / "config").write_text(
        "[profile dev]\n"
        "region = us-west-2\n"
        "sso_session = my-sso\n"
        "\n"
        "[sso-session my-sso]\n"
        "sso_region = us-east-1\n"
        f"sso_start_url = {SSO_START_URL}\n"
    )
    _write_sso_cache(
        home, SSO_START_URL, expires_delta=timedelta(hours=-1), token="EXPIRED"
    )

    result = _run_bash(
        f"source {AWS_CREDS_SH}; resolve_aws_credentials",
        _base_env(home, AWS_PROFILE="dev"),
    )
    assert result.returncode != 0
    assert "aws sso login --profile dev" in result.stderr
    assert result.stdout == ""


def test_sso_never_logged_in_produces_hint(tmp_path):
    """A profile with SSO config but no cache file at all (never ran
    `aws sso login`) — same hint, not a crash."""
    home = tmp_path / "home"
    home.mkdir()
    (home / ".aws").mkdir()
    (home / ".aws" / "config").write_text(
        "[profile dev]\n"
        "region = us-west-2\n"
        "sso_session = my-sso\n"
        "\n"
        "[sso-session my-sso]\n"
        "sso_region = us-east-1\n"
        f"sso_start_url = {SSO_START_URL}\n"
    )

    result = _run_bash(
        f"source {AWS_CREDS_SH}; resolve_aws_credentials",
        _base_env(home, AWS_PROFILE="dev"),
    )
    assert result.returncode != 0
    assert "aws sso login --profile dev" in result.stderr
    assert result.stdout == ""


def test_valid_sso_cache_token_resolves_session_token(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".aws").mkdir()
    (home / ".aws" / "config").write_text(
        "[profile dev]\n"
        "region = us-west-2\n"
        "sso_session = my-sso\n"
        "\n"
        "[sso-session my-sso]\n"
        "sso_region = us-east-1\n"
        f"sso_start_url = {SSO_START_URL}\n"
    )
    _write_sso_cache(home, SSO_START_URL, expires_delta=timedelta(hours=8))

    result = _run_bash(
        f"source {AWS_CREDS_SH}; resolve_aws_credentials",
        _base_env(home, AWS_PROFILE="dev"),
    )
    assert result.returncode == 0, result.stderr
    assert "AWS_SESSION_TOKEN=FAKE_TOKEN" in result.stdout
    assert "AWS_REGION=us-west-2" in result.stdout


def test_legacy_inline_sso_config_resolves(tmp_path):
    """Legacy (pre sso-session) inline sso_start_url/sso_region directly on
    the profile section, without a [sso-session ...] block."""
    home = tmp_path / "home"
    home.mkdir()
    (home / ".aws").mkdir()
    (home / ".aws" / "config").write_text(
        "[profile legacy]\n"
        "region = us-west-2\n"
        f"sso_start_url = {SSO_START_URL}\n"
        "sso_region = us-east-1\n"
        "sso_account_id = 111122223333\n"
        "sso_role_name = ReadOnly\n"
    )
    _write_sso_cache(home, SSO_START_URL, expires_delta=timedelta(hours=8))

    result = _run_bash(
        f"source {AWS_CREDS_SH}; resolve_aws_credentials",
        _base_env(home, AWS_PROFILE="legacy"),
    )
    assert result.returncode == 0, result.stderr
    assert "AWS_SESSION_TOKEN=FAKE_TOKEN" in result.stdout


def test_no_aws_dir_at_all_produces_actionable_error(tmp_path):
    home = tmp_path / "home"
    home.mkdir()  # no .aws subdirectory created

    result = _run_bash(
        f"source {AWS_CREDS_SH}; resolve_aws_credentials",
        _base_env(home),
    )
    assert result.returncode != 0
    assert result.stdout == ""
    assert "aws configure" in result.stderr


def test_nonexistent_profile_does_not_fall_back_to_default(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".aws").mkdir()
    (home / ".aws" / "config").write_text("[default]\nregion = us-east-1\n")
    (home / ".aws" / "credentials").write_text(
        "[default]\n"
        "aws_access_key_id = AKIADEFAULT\n"
        "aws_secret_access_key = defaultsecret\n"
    )

    result = _run_bash(
        f"source {AWS_CREDS_SH}; resolve_aws_credentials",
        _base_env(home, AWS_PROFILE="ghost"),
    )
    assert result.returncode != 0
    assert result.stdout == ""
    assert "ghost" in result.stderr


def test_cli_profile_flag_overrides_aws_profile_env(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".aws").mkdir()
    (home / ".aws" / "config").write_text(
        "[default]\nregion = us-east-1\n\n[profile dev]\nregion = us-west-2\n"
    )
    (home / ".aws" / "credentials").write_text(
        "[default]\n"
        "aws_access_key_id = AKIADEFAULT\n"
        "aws_secret_access_key = defaultsecret\n"
        "\n"
        "[dev]\n"
        "aws_access_key_id = AKIADEV\n"
        "aws_secret_access_key = devsecret\n"
    )

    result = _run_bash(
        f"source {AWS_CREDS_SH}; resolve_aws_credentials --profile dev",
        _base_env(home, AWS_PROFILE="default"),
    )
    assert result.returncode == 0, result.stderr
    assert "AWS_ACCESS_KEY_ID=AKIADEV" in result.stdout


def test_cli_region_flag_overrides_env_and_profile(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".aws").mkdir()
    (home / ".aws" / "config").write_text("[default]\nregion = us-east-1\n")
    (home / ".aws" / "credentials").write_text(
        "[default]\n"
        "aws_access_key_id = AKIADEFAULT\n"
        "aws_secret_access_key = defaultsecret\n"
    )

    result = _run_bash(
        f"source {AWS_CREDS_SH}; resolve_aws_credentials --region ca-central-1",
        _base_env(home, AWS_REGION="ap-south-1"),
    )
    assert result.returncode == 0, result.stderr
    assert "AWS_REGION=ca-central-1" in result.stdout
