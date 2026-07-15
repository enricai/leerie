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

REPO_ROOT = Path(__file__).resolve().parent.parent
EC2_LIB_SH = REPO_ROOT / "scripts" / "remote" / "ec2-lib.sh"
LOG_SH = REPO_ROOT / "scripts" / "remote" / "_log.sh"


def _stub_aws(aws_dir: Path, *, identity_succeeds: bool = True) -> Path:
    """Write a stub `aws` binary handling `sts get-caller-identity`.

    Records argv to aws_dir/aws.log (one line per invocation) so tests can
    assert on whether --profile was passed through.
    """
    stub = aws_dir / "aws"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        f'echo "$@" >> {aws_dir}/aws.log\n'
        'if [ "$1" = "sts" ] && [ "$2" = "get-caller-identity" ]; then\n'
        f"  exit {0 if identity_succeeds else 1}\n"
        "fi\n"
        "exit 0\n"
    )
    stub.chmod(0o755)
    return stub


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
