"""Tests for resolve_token_probe_cache_sec() and the token_probe_cache_sec
cap (DESIGN §6 *Multi-token rotation*).

Covers the env var → per-repo file → DEFAULT_CAPS resolution order,
positive-int validation, and the die() path for invalid values. Mirrors
the structure of test_resolve_confidence_rounds.py.
"""
from __future__ import annotations

import pytest


@pytest.fixture
def repo_root(tmp_path, monkeypatch):
    """An empty repo-root directory with LEERIE_TOKEN_PROBE_CACHE_SEC unset."""
    monkeypatch.delenv("LEERIE_TOKEN_PROBE_CACHE_SEC", raising=False)
    return tmp_path


def test_default_cap_is_180(leerie):
    assert leerie.DEFAULT_CAPS["token_probe_cache_sec"] == 180


def test_default_when_nothing_set(leerie, repo_root):
    assert leerie.resolve_token_probe_cache_sec(repo_root) == 180


def test_file_value(leerie, repo_root):
    (repo_root / "leerie.toml").write_text("token_probe_cache_sec = 300\n")
    assert leerie.resolve_token_probe_cache_sec(repo_root) == 300


def test_env_value(leerie, repo_root, monkeypatch):
    monkeypatch.setenv("LEERIE_TOKEN_PROBE_CACHE_SEC", "60")
    assert leerie.resolve_token_probe_cache_sec(repo_root) == 60


def test_env_wins_over_file(leerie, repo_root, monkeypatch):
    (repo_root / "leerie.toml").write_text("token_probe_cache_sec = 300\n")
    monkeypatch.setenv("LEERIE_TOKEN_PROBE_CACHE_SEC", "60")
    assert leerie.resolve_token_probe_cache_sec(repo_root) == 60


def test_cli_wins_over_env_and_file(leerie, repo_root, monkeypatch):
    (repo_root / "leerie.toml").write_text("token_probe_cache_sec = 300\n")
    monkeypatch.setenv("LEERIE_TOKEN_PROBE_CACHE_SEC", "60")
    assert leerie.resolve_token_probe_cache_sec(repo_root, cli_value=900) == 900


def test_cli_none_falls_back(leerie, repo_root, monkeypatch):
    monkeypatch.setenv("LEERIE_TOKEN_PROBE_CACHE_SEC", "60")
    assert leerie.resolve_token_probe_cache_sec(repo_root, cli_value=None) == 60


def test_bad_env_value_dies(leerie, repo_root, monkeypatch, capsys):
    monkeypatch.setenv("LEERIE_TOKEN_PROBE_CACHE_SEC", "not-a-number")
    with pytest.raises(SystemExit) as exc:
        leerie.resolve_token_probe_cache_sec(repo_root)
    assert exc.value.code != 0
    err = capsys.readouterr().err
    assert "not a positive integer" in err


def test_zero_env_value_dies(leerie, repo_root, monkeypatch, capsys):
    monkeypatch.setenv("LEERIE_TOKEN_PROBE_CACHE_SEC", "0")
    with pytest.raises(SystemExit) as exc:
        leerie.resolve_token_probe_cache_sec(repo_root)
    assert exc.value.code != 0
    err = capsys.readouterr().err
    assert "not a positive integer" in err


def test_negative_env_value_dies(leerie, repo_root, monkeypatch, capsys):
    monkeypatch.setenv("LEERIE_TOKEN_PROBE_CACHE_SEC", "-3")
    with pytest.raises(SystemExit) as exc:
        leerie.resolve_token_probe_cache_sec(repo_root)
    assert exc.value.code != 0


def test_bad_file_value_dies(leerie, repo_root, capsys):
    (repo_root / "leerie.toml").write_text("token_probe_cache_sec = bogus\n")
    with pytest.raises(SystemExit) as exc:
        leerie.resolve_token_probe_cache_sec(repo_root)
    assert exc.value.code != 0
    err = capsys.readouterr().err
    assert "not a positive integer" in err


def test_zero_file_value_dies(leerie, repo_root, capsys):
    (repo_root / "leerie.toml").write_text("token_probe_cache_sec = 0\n")
    with pytest.raises(SystemExit) as exc:
        leerie.resolve_token_probe_cache_sec(repo_root)
    assert exc.value.code != 0


def test_empty_env_treated_as_unset(leerie, repo_root, monkeypatch):
    monkeypatch.setenv("LEERIE_TOKEN_PROBE_CACHE_SEC", "")
    assert leerie.resolve_token_probe_cache_sec(repo_root) == 180


def test_whitespace_only_env_treated_as_unset(leerie, repo_root, monkeypatch):
    monkeypatch.setenv("LEERIE_TOKEN_PROBE_CACHE_SEC", "   ")
    assert leerie.resolve_token_probe_cache_sec(repo_root) == 180
