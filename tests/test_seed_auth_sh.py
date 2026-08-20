"""Tests for scripts/remote/seed-auth.sh.

seed-auth.sh is sourced (not exec'd) by the leerie launcher's RUNTIME=fly
branch after provision_machine() returns.  These tests exercise the script's
bash logic in isolation via subprocess, with flyctl stubbed out so no real
Fly.io calls are made.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from tests.conftest import run_bash

REPO_ROOT = Path(__file__).resolve().parent.parent
SEED_AUTH_SH = REPO_ROOT / "scripts" / "remote" / "seed-auth.sh"


def test_seed_auth_sh_exists():
    assert SEED_AUTH_SH.exists(), "scripts/remote/seed-auth.sh is missing"


def test_seed_auth_sh_is_executable():
    assert os.access(SEED_AUTH_SH, os.X_OK), (
        "scripts/remote/seed-auth.sh is not executable"
    )


def test_seed_auth_fails_without_machine_id(tmp_path):
    """seed_auth returns 1 when LEERIE_MACHINE_ID is unset."""
    stage = tmp_path / "stage"
    stage.mkdir()
    result = run_bash(
        f"source {SEED_AUTH_SH}; seed_auth",
        env={"LEERIE_MACHINE_ID": "", "STAGE": str(stage)},
    )
    assert result.returncode != 0
    assert "LEERIE_MACHINE_ID" in result.stderr


def test_seed_auth_fails_without_stage(tmp_path):
    """seed_auth returns 1 when STAGE is unset."""
    result = run_bash(
        f"source {SEED_AUTH_SH}; seed_auth",
        env={"LEERIE_MACHINE_ID": "test-machine-001", "STAGE": ""},
    )
    assert result.returncode != 0
    assert "STAGE" in result.stderr


def test_seed_auth_fails_without_credentials_or_token(tmp_path):
    """seed_auth returns 1 with an actionable error when no credentials are available."""
    stage = tmp_path / "stage"
    (stage / ".claude").mkdir(parents=True)
    # .credentials.json absent; CLAUDE_CODE_OAUTH_TOKEN unset

    fake_flyctl = tmp_path / "flyctl"
    fake_flyctl.write_text(
        "#!/usr/bin/env bash\n"
        # tar pipe (machine exec --stdin) succeeds. Drain stdin so the
        # gzipped tar producer doesn't SIGPIPE — GNU tar (Linux CI) forks
        # gzip, and gzip dying with SIGPIPE makes tar exit non-zero,
        # which `set -euo pipefail` in seed-auth.sh then propagates.
        "cat >/dev/null\n"
        "exit 0\n"
    )
    fake_flyctl.chmod(0o755)

    result = run_bash(
        f"source {SEED_AUTH_SH}; seed_auth",
        env={
            "LEERIE_MACHINE_ID": "test-machine-001",
            "STAGE": str(stage),
            "CLAUDE_CODE_OAUTH_TOKEN": "",
            "PATH": f"{tmp_path}:/usr/bin:/bin",
        },
    )
    assert result.returncode != 0
    assert "credentials" in result.stderr.lower() or "CLAUDE_CODE_OAUTH_TOKEN" in result.stderr


def test_seed_auth_fails_without_git_identity(tmp_path):
    """seed_auth returns 1 with an actionable error when git user identity is missing."""
    stage = tmp_path / "stage"
    (stage / ".claude").mkdir(parents=True)
    creds = stage / ".claude" / ".credentials.json"
    creds.write_text('{"claudeAiOauth":{"accessToken":"tok"}}')

    # stub flyctl: tar exec succeeds, git config calls succeed.
    # Drain stdin so the gzipped tar producer doesn't SIGPIPE under GNU
    # tar (Linux CI); see test_seed_auth_fails_without_credentials_or_token
    # for the full explanation.
    fake_flyctl = tmp_path / "flyctl"
    fake_flyctl.write_text(
        "#!/usr/bin/env bash\n"
        "cat >/dev/null\n"
        "exit 0\n"
    )
    fake_flyctl.chmod(0o755)

    # stub git: returns empty for user.name and user.email
    fake_git = tmp_path / "git"
    fake_git.write_text(
        "#!/usr/bin/env bash\n"
        "exit 1\n"  # git config user.name / user.email returns 1 (not set)
    )
    fake_git.chmod(0o755)

    result = run_bash(
        f"source {SEED_AUTH_SH}; seed_auth",
        env={
            "LEERIE_MACHINE_ID": "test-machine-001",
            "STAGE": str(stage),
            "CLAUDE_CODE_OAUTH_TOKEN": "",
            "PATH": f"{tmp_path}:/usr/bin:/bin",
        },
    )
    assert result.returncode != 0
    assert "user.name" in result.stderr or "user.email" in result.stderr


def test_seed_auth_succeeds_with_credentials_file(tmp_path):
    """seed_auth returns 0 when $STAGE has .credentials.json and git identity is set."""
    stage = tmp_path / "stage"
    (stage / ".claude").mkdir(parents=True)
    creds = stage / ".claude" / ".credentials.json"
    creds.write_text('{"claudeAiOauth":{"accessToken":"tok"}}')

    # Drain stdin so the gzipped tar producer doesn't SIGPIPE under GNU
    # tar (Linux CI); see test_seed_auth_fails_without_credentials_or_token
    # for the full explanation.
    fake_flyctl = tmp_path / "flyctl"
    fake_flyctl.write_text(
        "#!/usr/bin/env bash\n"
        "cat >/dev/null\n"
        "exit 0\n"
    )
    fake_flyctl.chmod(0o755)

    fake_git = tmp_path / "git"
    fake_git.write_text(
        "#!/usr/bin/env bash\n"
        "if [ \"$1\" = 'config' ] && [ \"$2\" = 'user.name' ]; then echo 'Test User'; exit 0; fi\n"
        "if [ \"$1\" = 'config' ] && [ \"$2\" = 'user.email' ]; then echo 'test@example.com'; exit 0; fi\n"
        "exit 0\n"
    )
    fake_git.chmod(0o755)

    result = run_bash(
        f"source {SEED_AUTH_SH}; seed_auth",
        env={
            "LEERIE_MACHINE_ID": "test-machine-001",
            "STAGE": str(stage),
            "CLAUDE_CODE_OAUTH_TOKEN": "",
            "PATH": f"{tmp_path}:/usr/bin:/bin",
        },
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert "seed_auth complete" in result.stderr


def test_seed_auth_uses_token_fallback_when_no_credentials_file(tmp_path):
    """seed_auth writes a credentials file from $CLAUDE_CODE_OAUTH_TOKEN when none in $STAGE."""
    stage = tmp_path / "stage"
    (stage / ".claude").mkdir(parents=True)
    # No .credentials.json in $STAGE

    received_stdin = tmp_path / "received_stdin.txt"
    fake_flyctl = tmp_path / "flyctl"
    # On the second invocation (credentials write via --stdin sh -c), capture stdin.
    # Drain stdin so the gzipped tar producer doesn't SIGPIPE under GNU
    # tar (Linux CI); see test_seed_auth_fails_without_credentials_or_token
    # for the full explanation.
    fake_flyctl.write_text(
        "#!/usr/bin/env bash\n"
        "cat >/dev/null\n"
        "exit 0\n"
    )
    fake_flyctl.chmod(0o755)

    fake_git = tmp_path / "git"
    fake_git.write_text(
        "#!/usr/bin/env bash\n"
        "if [ \"$1\" = 'config' ] && [ \"$2\" = 'user.name' ]; then echo 'Test User'; exit 0; fi\n"
        "if [ \"$1\" = 'config' ] && [ \"$2\" = 'user.email' ]; then echo 'test@example.com'; exit 0; fi\n"
        "exit 0\n"
    )
    fake_git.chmod(0o755)

    result = run_bash(
        f"source {SEED_AUTH_SH}; seed_auth",
        env={
            "LEERIE_MACHINE_ID": "test-machine-001",
            "STAGE": str(stage),
            "CLAUDE_CODE_OAUTH_TOKEN": "my-oauth-token",
            "PATH": f"{tmp_path}:/usr/bin:/bin",
        },
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert "CLAUDE_CODE_OAUTH_TOKEN" in result.stderr
    assert "seed_auth complete" in result.stderr


def test_seed_auth_tar_failure_returns_nonzero(tmp_path):
    """seed_auth returns 1 when the tar pipe into flyctl machine exec fails."""
    stage = tmp_path / "stage"
    (stage / ".claude").mkdir(parents=True)
    creds = stage / ".claude" / ".credentials.json"
    creds.write_text('{"claudeAiOauth":{"accessToken":"tok"}}')

    # stub flyctl: machine exec fails on the tar pipe call
    fake_flyctl = tmp_path / "flyctl"
    fake_flyctl.write_text(
        "#!/usr/bin/env bash\n"
        "exit 1\n"
    )
    fake_flyctl.chmod(0o755)

    fake_git = tmp_path / "git"
    fake_git.write_text("#!/usr/bin/env bash\nexit 0\n")
    fake_git.chmod(0o755)

    result = run_bash(
        f"source {SEED_AUTH_SH}; seed_auth",
        env={
            "LEERIE_MACHINE_ID": "test-machine-001",
            "STAGE": str(stage),
            "CLAUDE_CODE_OAUTH_TOKEN": "",
            "PATH": f"{tmp_path}:/usr/bin:/bin",
        },
    )
    assert result.returncode != 0
    assert "failed to seed" in result.stderr.lower() or "seed" in result.stderr.lower()
