"""Tests for resolve_leerie_root() — the LEERIE_STATE_DIR env contract.

Covers both branches of the resolution:
  - LEERIE_STATE_DIR set → leerie_root is anchored at that path (outside repo)
  - LEERIE_STATE_DIR unset (or empty) → leerie_root falls back to repo_root/.leerie
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def clear_state_dir_env(monkeypatch):
    monkeypatch.delenv("LEERIE_STATE_DIR", raising=False)


def test_unset_falls_back_to_repo_relative(leerie, tmp_path):
    """When LEERIE_STATE_DIR is unset, leerie_root is <repo_root>/.leerie."""
    result = leerie.resolve_leerie_root(tmp_path)
    assert result == (tmp_path / ".leerie").resolve()


def test_env_set_overrides_repo_root(leerie, tmp_path, monkeypatch):
    """When LEERIE_STATE_DIR is set, leerie_root is anchored at that path."""
    state_dir = tmp_path / "my-state-dir"
    monkeypatch.setenv("LEERIE_STATE_DIR", str(state_dir))
    result = leerie.resolve_leerie_root(tmp_path / "some-repo")
    assert result == state_dir.resolve()


def test_env_set_is_outside_repo(leerie, tmp_path, monkeypatch):
    """The env-supplied path need not be under the repo root at all."""
    repo = tmp_path / "repos" / "my-project"
    state_dir = tmp_path / "leerie-global-state"
    monkeypatch.setenv("LEERIE_STATE_DIR", str(state_dir))
    result = leerie.resolve_leerie_root(repo)
    assert not str(result).startswith(str(repo))
    assert result == state_dir.resolve()


def test_empty_env_falls_back_to_repo_relative(leerie, tmp_path, monkeypatch):
    """LEERIE_STATE_DIR='' is treated as unset — same as default behavior."""
    monkeypatch.setenv("LEERIE_STATE_DIR", "")
    result = leerie.resolve_leerie_root(tmp_path)
    assert result == (tmp_path / ".leerie").resolve()


def test_env_whitespace_only_falls_back(leerie, tmp_path, monkeypatch):
    """Whitespace-only LEERIE_STATE_DIR is treated as unset."""
    monkeypatch.setenv("LEERIE_STATE_DIR", "   ")
    result = leerie.resolve_leerie_root(tmp_path)
    assert result == (tmp_path / ".leerie").resolve()


def test_result_is_absolute(leerie, tmp_path, monkeypatch):
    """resolve_leerie_root always returns an absolute Path."""
    state_dir = tmp_path / "state"
    monkeypatch.setenv("LEERIE_STATE_DIR", str(state_dir))
    result = leerie.resolve_leerie_root(tmp_path)
    assert result.is_absolute()


def test_default_result_is_absolute(leerie, tmp_path):
    """The default (fallback) path is also absolute."""
    result = leerie.resolve_leerie_root(tmp_path)
    assert result.is_absolute()
