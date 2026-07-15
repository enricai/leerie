"""Tests for resolve_aws_region() and resolve_aws_profile().

Covers the CLI value → env var → per-repo file → None resolution order,
mirroring tests/test_resolve_runtime.py's structure but for the
free-form-string _resolve_str_pref machinery (like resolve_pr_template),
not the enum-validated _resolve_enum_pref machinery. No die() path: these
are unvalidated strings.
"""
from __future__ import annotations

import pytest


@pytest.fixture
def repo_root(tmp_path, monkeypatch):
    """An empty repo-root directory with the AWS env prefs unset."""
    monkeypatch.delenv("LEERIE_AWS_REGION", raising=False)
    monkeypatch.delenv("LEERIE_AWS_PROFILE", raising=False)
    return tmp_path


# ---- resolve_aws_region ----------------------------------------------

def test_region_default_is_none(leerie, repo_root):
    assert leerie.resolve_aws_region(repo_root) is None


def test_region_file_present_env_unset(leerie, repo_root):
    (repo_root / "leerie.toml").write_text("aws_region = us-east-1\n")
    assert leerie.resolve_aws_region(repo_root) == "us-east-1"


def test_region_file_absent_env_set(leerie, repo_root, monkeypatch):
    monkeypatch.setenv("LEERIE_AWS_REGION", "us-west-2")
    assert leerie.resolve_aws_region(repo_root) == "us-west-2"


def test_region_env_wins_over_file(leerie, repo_root, monkeypatch):
    (repo_root / "leerie.toml").write_text("aws_region = us-east-1\n")
    monkeypatch.setenv("LEERIE_AWS_REGION", "us-west-2")
    assert leerie.resolve_aws_region(repo_root) == "us-west-2"


def test_region_cli_value_wins_over_env_and_file(leerie, repo_root, monkeypatch):
    (repo_root / "leerie.toml").write_text("aws_region = us-east-1\n")
    monkeypatch.setenv("LEERIE_AWS_REGION", "us-west-2")
    assert leerie.resolve_aws_region(repo_root, cli_value="eu-west-1") == "eu-west-1"


def test_region_cli_value_none_falls_back(leerie, repo_root, monkeypatch):
    monkeypatch.setenv("LEERIE_AWS_REGION", "us-west-2")
    assert leerie.resolve_aws_region(repo_root, cli_value=None) == "us-west-2"


def test_region_quoted_file_value(leerie, repo_root):
    (repo_root / "leerie.toml").write_text('aws_region = "us-east-1"\n')
    assert leerie.resolve_aws_region(repo_root) == "us-east-1"


def test_region_single_quoted_file_value(leerie, repo_root):
    (repo_root / "leerie.toml").write_text("aws_region = 'us-east-1'\n")
    assert leerie.resolve_aws_region(repo_root) == "us-east-1"


def test_region_empty_env_treated_as_unset(leerie, repo_root, monkeypatch):
    monkeypatch.setenv("LEERIE_AWS_REGION", "")
    assert leerie.resolve_aws_region(repo_root) is None


def test_region_whitespace_only_env_treated_as_unset(leerie, repo_root, monkeypatch):
    monkeypatch.setenv("LEERIE_AWS_REGION", "   ")
    assert leerie.resolve_aws_region(repo_root) is None


def test_region_no_validation_of_value(leerie, repo_root):
    # Free-form string — no enum check, unlike resolve_runtime.
    (repo_root / "leerie.toml").write_text("aws_region = not-a-real-region\n")
    assert leerie.resolve_aws_region(repo_root) == "not-a-real-region"


# ---- resolve_aws_profile ----------------------------------------------

def test_profile_default_is_none(leerie, repo_root):
    assert leerie.resolve_aws_profile(repo_root) is None


def test_profile_file_present_env_unset(leerie, repo_root):
    (repo_root / "leerie.toml").write_text("aws_profile = leerie-ec2\n")
    assert leerie.resolve_aws_profile(repo_root) == "leerie-ec2"


def test_profile_file_absent_env_set(leerie, repo_root, monkeypatch):
    monkeypatch.setenv("LEERIE_AWS_PROFILE", "leerie-ec2")
    assert leerie.resolve_aws_profile(repo_root) == "leerie-ec2"


def test_profile_env_wins_over_file(leerie, repo_root, monkeypatch):
    (repo_root / "leerie.toml").write_text("aws_profile = file-profile\n")
    monkeypatch.setenv("LEERIE_AWS_PROFILE", "env-profile")
    assert leerie.resolve_aws_profile(repo_root) == "env-profile"


def test_profile_cli_value_wins_over_env_and_file(leerie, repo_root, monkeypatch):
    (repo_root / "leerie.toml").write_text("aws_profile = file-profile\n")
    monkeypatch.setenv("LEERIE_AWS_PROFILE", "env-profile")
    assert leerie.resolve_aws_profile(repo_root, cli_value="cli-profile") == "cli-profile"


def test_profile_cli_value_none_falls_back(leerie, repo_root, monkeypatch):
    monkeypatch.setenv("LEERIE_AWS_PROFILE", "env-profile")
    assert leerie.resolve_aws_profile(repo_root, cli_value=None) == "env-profile"


def test_profile_empty_env_treated_as_unset(leerie, repo_root, monkeypatch):
    monkeypatch.setenv("LEERIE_AWS_PROFILE", "")
    assert leerie.resolve_aws_profile(repo_root) is None


def test_profile_whitespace_only_env_treated_as_unset(leerie, repo_root, monkeypatch):
    monkeypatch.setenv("LEERIE_AWS_PROFILE", "   ")
    assert leerie.resolve_aws_profile(repo_root) is None


def test_region_and_profile_are_independent(leerie, repo_root, monkeypatch):
    monkeypatch.setenv("LEERIE_AWS_REGION", "us-west-2")
    monkeypatch.setenv("LEERIE_AWS_PROFILE", "leerie-ec2")
    assert leerie.resolve_aws_region(repo_root) == "us-west-2"
    assert leerie.resolve_aws_profile(repo_root) == "leerie-ec2"
