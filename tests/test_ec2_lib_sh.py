"""Tests for scripts/remote/ec2-lib.sh.

ec2-lib.sh provides require_aws(), the EC2 runtime's host-side preflight —
parallel to require_flyctl() in scripts/remote/lib.sh (DESIGN precedent:
fly's RUNTIME=fly branch calls require_flyctl before provisioning; the ec2
branch calls require_aws the same way). It checks the `aws` CLI is on PATH
and that credentials resolve (via `aws sts get-caller-identity`), reusing
the exact credential-error vocabulary already established by
bedrock_preflight() in the `leerie` launcher (`aws sso login --profile
<profile>` as the recovery hint) rather than inventing a second one.

These tests source the real script directly (`source <path>; require_aws`),
mirroring tests/test_aws_credentials.py's pattern, against a stubbed `aws`
binary on PATH — the repo's established bash-test idiom for external-binary
preflights (tests/test_ensure_image.py's stubbed flyctl).
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from tests.ec2_stub import make_sts_only_stub_aws

REPO_ROOT = Path(__file__).resolve().parent.parent
EC2_LIB_SH = REPO_ROOT / "scripts" / "remote" / "ec2-lib.sh"
LOG_SH = REPO_ROOT / "scripts" / "remote" / "_log.sh"


def _stub_aws(aws_dir: Path, *, identity_succeeds: bool = True) -> Path:
    """Write a stub `aws` binary handling `sts get-caller-identity`.

    Records argv to aws_dir/aws.log (one line per invocation) so tests can
    assert on whether --profile was passed through. Thin wrapper over
    tests/ec2_stub.py's shared builder.
    """
    return make_sts_only_stub_aws(aws_dir, identity_succeeds=identity_succeeds)


def _run_require_aws(env: dict, *, aws_dir: Path | None = None,
                      cwd: Path | None = None) -> subprocess.CompletedProcess:
    base_env = {k: v for k, v in os.environ.items()}
    if aws_dir is not None:
        base_env["PATH"] = f"{aws_dir}:{base_env.get('PATH', '')}"
    else:
        # Simulate `aws` being entirely absent from PATH: strip to a
        # minimal, aws-free bin set (bash/env/coreutils only).
        base_env["PATH"] = "/usr/bin:/bin"
    base_env.update(env)
    script = f"source {LOG_SH}; source {EC2_LIB_SH}; require_aws"
    return subprocess.run(
        ["bash", "-c", script],
        env=base_env,
        cwd=str(cwd) if cwd is not None else None,
        capture_output=True,
        text=True,
    )


def test_ec2_lib_sh_exists_and_is_executable():
    assert EC2_LIB_SH.is_file()
    assert os.access(EC2_LIB_SH, os.X_OK)


def test_require_aws_succeeds_when_aws_present_and_authenticated(tmp_path):
    aws_dir = tmp_path / "bin"
    aws_dir.mkdir()
    _stub_aws(aws_dir, identity_succeeds=True)

    result = _run_require_aws({"USER_REPO": str(tmp_path)}, aws_dir=aws_dir)

    assert result.returncode == 0, result.stderr


def test_require_aws_fails_with_install_hint_when_aws_absent():
    result = _run_require_aws({"USER_REPO": "/tmp/repo"})

    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "aws" in combined.lower()
    assert (
        "install" in combined.lower()
        or "docs.aws.amazon.com" in combined
        or "brew install awscli" in combined
    ), combined


def test_require_aws_fails_with_sso_login_hint_when_credentials_unresolvable(tmp_path):
    aws_dir = tmp_path / "bin"
    aws_dir.mkdir()
    _stub_aws(aws_dir, identity_succeeds=False)

    result = _run_require_aws({"USER_REPO": str(tmp_path)}, aws_dir=aws_dir)

    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "aws sso login" in combined


def test_require_aws_passes_profile_from_leerie_aws_profile_env(tmp_path):
    aws_dir = tmp_path / "bin"
    aws_dir.mkdir()
    _stub_aws(aws_dir, identity_succeeds=True)

    result = _run_require_aws(
        {"USER_REPO": str(tmp_path), "LEERIE_AWS_PROFILE": "myprofile"},
        aws_dir=aws_dir,
        cwd=tmp_path,
    )

    assert result.returncode == 0, result.stderr
    log = (aws_dir / "aws.log").read_text()
    assert "--profile myprofile" in log


def test_require_aws_sso_login_hint_includes_profile_when_set(tmp_path):
    aws_dir = tmp_path / "bin"
    aws_dir.mkdir()
    _stub_aws(aws_dir, identity_succeeds=False)

    result = _run_require_aws(
        {"USER_REPO": str(tmp_path), "LEERIE_AWS_PROFILE": "myprofile"},
        aws_dir=aws_dir,
    )

    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "aws sso login --profile myprofile" in combined


def test_require_aws_falls_back_to_aws_profile_env(tmp_path):
    aws_dir = tmp_path / "bin"
    aws_dir.mkdir()
    _stub_aws(aws_dir, identity_succeeds=True)

    result = _run_require_aws(
        {"USER_REPO": str(tmp_path), "AWS_PROFILE": "fallback-profile"},
        aws_dir=aws_dir,
        cwd=tmp_path,
    )

    assert result.returncode == 0, result.stderr
    log = (aws_dir / "aws.log").read_text()
    assert "--profile fallback-profile" in log


def test_require_aws_leerie_aws_profile_wins_over_aws_profile(tmp_path):
    aws_dir = tmp_path / "bin"
    aws_dir.mkdir()
    _stub_aws(aws_dir, identity_succeeds=True)

    result = _run_require_aws(
        {
            "USER_REPO": str(tmp_path),
            "LEERIE_AWS_PROFILE": "preferred",
            "AWS_PROFILE": "fallback-profile",
        },
        aws_dir=aws_dir,
        cwd=tmp_path,
    )

    assert result.returncode == 0, result.stderr
    log = (aws_dir / "aws.log").read_text()
    assert "--profile preferred" in log
    assert "fallback-profile" not in log


# ---------------------------------------------------------------------------
# Actionable preflight errors: each failing precondition exits nonzero and
# names the knob (env var / flag) AND the fix command, and the messages differ
# per failure so one generic message cannot satisfy all of them. Asserted on
# documented tokens only, never full prose.
# ---------------------------------------------------------------------------

EC2_DEFAULTS_SH = REPO_ROOT / "scripts" / "remote" / "ec2-defaults.sh"


def _run_defaults(fn: str, aws_dir: Path, env: dict) -> subprocess.CompletedProcess:
    base_env = {k: v for k, v in os.environ.items()
                if not k.startswith(("LEERIE_EC2_", "AWS_"))}
    base_env["PATH"] = f"{aws_dir}:{base_env.get('PATH', '')}"
    base_env.update(env)
    script = f"source {LOG_SH}; source {EC2_DEFAULTS_SH}; {fn}"
    return subprocess.run(["bash", "-c", script], env=base_env,
                          capture_output=True, text=True)


def _no_vpc_aws(aws_dir: Path) -> None:
    aws = aws_dir / "aws"
    aws.write_text('#!/usr/bin/env bash\necho \'{"Vpcs": []}\'\n')
    aws.chmod(0o755)


def _no_ip_curl(aws_dir: Path) -> None:
    curl = aws_dir / "curl"
    curl.write_text("#!/usr/bin/env bash\nexit 7\n")
    curl.chmod(0o755)


def test_no_default_vpc_names_create_default_vpc_and_subnet_override(tmp_path):
    aws_dir = tmp_path / "bin"
    aws_dir.mkdir()
    _no_vpc_aws(aws_dir)

    result = _run_defaults("ec2_default_subnet_id", aws_dir, {})

    assert result.returncode != 0
    assert "aws ec2 create-default-vpc" in result.stderr
    assert "LEERIE_EC2_SUBNET_ID" in result.stderr
    assert "--ec2-subnet-id" in result.stderr


def test_unreachable_egress_lookup_names_ssh_cidr_override(tmp_path):
    aws_dir = tmp_path / "bin"
    aws_dir.mkdir()
    _no_ip_curl(aws_dir)

    result = _run_defaults("ec2_egress_cidr", aws_dir, {})

    assert result.returncode != 0
    assert "LEERIE_EC2_SSH_CIDR" in result.stderr


def test_missing_aws_cli_and_bad_credentials_messages_differ(tmp_path):
    missing = _run_require_aws({"USER_REPO": "/tmp/repo"})
    aws_dir = tmp_path / "bin"
    aws_dir.mkdir()
    _stub_aws(aws_dir, identity_succeeds=False)
    creds = _run_require_aws({"USER_REPO": str(tmp_path)}, aws_dir=aws_dir)

    assert missing.returncode != 0 and creds.returncode != 0
    assert "brew install awscli" in missing.stderr
    assert "aws sso login" not in missing.stderr
    assert "aws sso login" in creds.stderr
    assert "brew install awscli" not in creds.stderr
    # the credentials hint also names the profile env knobs
    assert "AWS_PROFILE" in creds.stderr


def test_each_preflight_failure_names_a_distinct_fix_command(tmp_path):
    aws_dir = tmp_path / "bin"
    aws_dir.mkdir()
    _no_vpc_aws(aws_dir)
    _no_ip_curl(aws_dir)
    vpc = _run_defaults("ec2_default_subnet_id", aws_dir, {}).stderr
    cidr = _run_defaults("ec2_egress_cidr", aws_dir, {}).stderr

    assert vpc != cidr
    assert "LEERIE_EC2_SSH_CIDR" not in vpc
    assert "create-default-vpc" not in cidr
