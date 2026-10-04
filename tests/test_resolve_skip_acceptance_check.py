"""Tests for resolve_skip_acceptance_check() — the --skip-acceptance-check
opt-out that suppresses the held-out acceptance sets and gate (DESIGN §8
*Held-out acceptance tests*).

Covers the precedence order: CLI flag → LEERIE_SKIP_ACCEPTANCE_CHECK env
var → skip_acceptance_check in leerie.toml → False.

Mirrors test_resolve_skip_base_baseline.py — both resolvers share
`_resolve_bool_pref`, so this file locks the wiring (env var name +
file key), not the resolution logic.
"""
from __future__ import annotations

import pytest


@pytest.fixture
def repo_root(tmp_path, monkeypatch):
    monkeypatch.delenv("LEERIE_SKIP_ACCEPTANCE_CHECK", raising=False)
    return tmp_path


def test_default_is_off(leerie, repo_root):
    assert leerie.resolve_skip_acceptance_check(
        repo_root, cli_value=False) is False


def test_cli_flag_wins(leerie, repo_root, monkeypatch):
    monkeypatch.setenv("LEERIE_SKIP_ACCEPTANCE_CHECK", "0")
    (repo_root / "leerie.toml").write_text(
        "skip_acceptance_check = false\n")
    assert leerie.resolve_skip_acceptance_check(
        repo_root, cli_value=True) is True


def test_env_set_true(leerie, repo_root, monkeypatch):
    monkeypatch.setenv("LEERIE_SKIP_ACCEPTANCE_CHECK", "1")
    assert leerie.resolve_skip_acceptance_check(
        repo_root, cli_value=False) is True


def test_file_set_true_no_env(leerie, repo_root):
    (repo_root / "leerie.toml").write_text(
        "skip_acceptance_check = true\n")
    assert leerie.resolve_skip_acceptance_check(
        repo_root, cli_value=False) is True


def test_env_wins_over_file(leerie, repo_root, monkeypatch):
    (repo_root / "leerie.toml").write_text(
        "skip_acceptance_check = true\n")
    monkeypatch.setenv("LEERIE_SKIP_ACCEPTANCE_CHECK", "false")
    assert leerie.resolve_skip_acceptance_check(
        repo_root, cli_value=False) is False


def test_env_garbage_dies(leerie, repo_root, monkeypatch):
    monkeypatch.setenv("LEERIE_SKIP_ACCEPTANCE_CHECK", "maybe")
    with pytest.raises(SystemExit):
        leerie.resolve_skip_acceptance_check(
            repo_root, cli_value=False)
