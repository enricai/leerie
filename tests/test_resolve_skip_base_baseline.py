"""Tests for resolve_skip_base_baseline() — the --skip-base-baseline
opt-out that suppresses the base-tree health baseline (DESIGN §9
*Base-tree health baseline*).

Covers the precedence order: CLI flag → LEERIE_SKIP_BASE_BASELINE env
var → False (no leerie.toml tier).

Mirrors test_resolve_strict_conformer.py — both resolvers share
`_resolve_bool_pref`, so this file locks the wiring (env var name +
file key), not the resolution logic.
"""
from __future__ import annotations

import pytest


@pytest.fixture
def repo_root(tmp_path, monkeypatch):
    monkeypatch.delenv("LEERIE_SKIP_BASE_BASELINE", raising=False)
    return tmp_path


def test_default_is_off(leerie, repo_root):
    assert leerie.resolve_skip_base_baseline(
        repo_root, cli_value=False) is False


def test_cli_flag_wins(leerie, repo_root, monkeypatch):
    monkeypatch.setenv("LEERIE_SKIP_BASE_BASELINE", "0")
    (repo_root / "leerie.toml").write_text(
        "skip_base_baseline = false\n")
    assert leerie.resolve_skip_base_baseline(
        repo_root, cli_value=True) is True


def test_env_set_true(leerie, repo_root, monkeypatch):
    monkeypatch.setenv("LEERIE_SKIP_BASE_BASELINE", "1")
    assert leerie.resolve_skip_base_baseline(
        repo_root, cli_value=False) is True


def test_file_is_never_read(leerie, repo_root):
    """A waiver is the operator's (DESIGN §6 *A repository chooses no
    operator authority*): `skip_base_baseline = true` in leerie.toml is not taken."""
    (repo_root / "leerie.toml").write_text(
        "skip_base_baseline = true\n")
    assert leerie.resolve_skip_base_baseline(
        repo_root, cli_value=False) is False


def test_env_wins_over_file(leerie, repo_root, monkeypatch):
    (repo_root / "leerie.toml").write_text(
        "skip_base_baseline = true\n")
    monkeypatch.setenv("LEERIE_SKIP_BASE_BASELINE", "false")
    assert leerie.resolve_skip_base_baseline(
        repo_root, cli_value=False) is False


def test_env_garbage_dies(leerie, repo_root, monkeypatch):
    monkeypatch.setenv("LEERIE_SKIP_BASE_BASELINE", "maybe")
    with pytest.raises(SystemExit):
        leerie.resolve_skip_base_baseline(
            repo_root, cli_value=False)
