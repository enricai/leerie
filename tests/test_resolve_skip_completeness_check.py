"""Tests for resolve_skip_completeness_check() — the --skip-completeness-check
opt-out for the conformer's gating solution_defects completeness axis
(DESIGN §9 *The one gating axis: solution completeness*).

Covers the precedence order: CLI flag → LEERIE_SKIP_COMPLETENESS_CHECK env
var → skip_completeness_check in leerie.toml → False (the gate runs by
default). Mirrors test_resolve_skip_adherence_check.py — both resolvers share
`_resolve_bool_pref`, so this locks the wiring (env var name + file key).
"""
from __future__ import annotations

import pytest


@pytest.fixture
def repo_root(tmp_path, monkeypatch):
    monkeypatch.delenv("LEERIE_SKIP_COMPLETENESS_CHECK", raising=False)
    return tmp_path


def test_default_is_off(leerie, repo_root):
    assert leerie.resolve_skip_completeness_check(
        repo_root, cli_value=False) is False


def test_cli_flag_wins(leerie, repo_root, monkeypatch):
    monkeypatch.setenv("LEERIE_SKIP_COMPLETENESS_CHECK", "0")
    (repo_root / "leerie.toml").write_text(
        "skip_completeness_check = false\n")
    assert leerie.resolve_skip_completeness_check(
        repo_root, cli_value=True) is True


def test_env_set_true(leerie, repo_root, monkeypatch):
    monkeypatch.setenv("LEERIE_SKIP_COMPLETENESS_CHECK", "1")
    assert leerie.resolve_skip_completeness_check(
        repo_root, cli_value=False) is True


def test_file_set_true_no_env(leerie, repo_root):
    (repo_root / "leerie.toml").write_text(
        "skip_completeness_check = true\n")
    assert leerie.resolve_skip_completeness_check(
        repo_root, cli_value=False) is True


def test_env_wins_over_file(leerie, repo_root, monkeypatch):
    (repo_root / "leerie.toml").write_text(
        "skip_completeness_check = true\n")
    monkeypatch.setenv("LEERIE_SKIP_COMPLETENESS_CHECK", "false")
    assert leerie.resolve_skip_completeness_check(
        repo_root, cli_value=False) is False


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
def test_env_truthy_spellings(leerie, repo_root, monkeypatch, value):
    monkeypatch.setenv("LEERIE_SKIP_COMPLETENESS_CHECK", value)
    assert leerie.resolve_skip_completeness_check(
        repo_root, cli_value=False) is True


@pytest.mark.parametrize("value", ["0", "false", "FALSE", "no", "off"])
def test_env_falsy_spellings(leerie, repo_root, monkeypatch, value):
    monkeypatch.setenv("LEERIE_SKIP_COMPLETENESS_CHECK", value)
    assert leerie.resolve_skip_completeness_check(
        repo_root, cli_value=False) is False


def test_env_garbage_dies(leerie, repo_root, monkeypatch):
    monkeypatch.setenv("LEERIE_SKIP_COMPLETENESS_CHECK", "maybe")
    with pytest.raises(SystemExit):
        leerie.resolve_skip_completeness_check(repo_root, cli_value=False)
