"""Tests for resolve_skip_repo_map() — the --skip-repo-map opt-out that
suppresses the P6 repo-map structural context injection (DESIGN §5½ (P6)).

Covers the precedence order: CLI flag → LEERIE_SKIP_REPO_MAP env var →
skip_repo_map in leerie.toml → False.

Mirrors test_resolve_skip_base_baseline.py — both resolvers share
`_resolve_bool_pref`, so this file locks the wiring (env var name +
file key), not the resolution logic.
"""
from __future__ import annotations

import pytest


@pytest.fixture
def repo_root(tmp_path, monkeypatch):
    monkeypatch.delenv("LEERIE_SKIP_REPO_MAP", raising=False)
    return tmp_path


def test_default_is_off(leerie, repo_root):
    assert leerie.resolve_skip_repo_map(
        repo_root, cli_value=False) is False


def test_cli_flag_wins(leerie, repo_root, monkeypatch):
    monkeypatch.setenv("LEERIE_SKIP_REPO_MAP", "0")
    (repo_root / "leerie.toml").write_text(
        "skip_repo_map = false\n")
    assert leerie.resolve_skip_repo_map(
        repo_root, cli_value=True) is True


@pytest.mark.parametrize("value", ["1", "true"])
def test_env_set_true(leerie, repo_root, monkeypatch, value):
    monkeypatch.setenv("LEERIE_SKIP_REPO_MAP", value)
    assert leerie.resolve_skip_repo_map(
        repo_root, cli_value=False) is True


@pytest.mark.parametrize("value", ["0", "false"])
def test_env_set_false(leerie, repo_root, monkeypatch, value):
    monkeypatch.setenv("LEERIE_SKIP_REPO_MAP", value)
    assert leerie.resolve_skip_repo_map(
        repo_root, cli_value=False) is False


def test_file_set_true_no_env(leerie, repo_root):
    (repo_root / "leerie.toml").write_text(
        "skip_repo_map = true\n")
    assert leerie.resolve_skip_repo_map(
        repo_root, cli_value=False) is True


def test_env_wins_over_file(leerie, repo_root, monkeypatch):
    (repo_root / "leerie.toml").write_text(
        "skip_repo_map = true\n")
    monkeypatch.setenv("LEERIE_SKIP_REPO_MAP", "false")
    assert leerie.resolve_skip_repo_map(
        repo_root, cli_value=False) is False


def test_env_garbage_dies(leerie, repo_root, monkeypatch):
    monkeypatch.setenv("LEERIE_SKIP_REPO_MAP", "maybe")
    with pytest.raises(SystemExit):
        leerie.resolve_skip_repo_map(
            repo_root, cli_value=False)
