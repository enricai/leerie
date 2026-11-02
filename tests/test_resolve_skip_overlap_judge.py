"""Tests for resolve_skip_overlap_judge() — the --skip-overlap-judge
opt-out (DESIGN §5 *Cross-domain surface overlap*).

Covers the precedence order: CLI flag → LEERIE_SKIP_OVERLAP_JUDGE env
var → False (no leerie.toml tier) (the judge runs on
multi-planner runs by default).

Mirrors test_resolve_dangerously_skip_permissions.py — both resolvers
share `_resolve_bool_pref`, so this file locks the wiring (env var
name + file key), not the resolution logic.
"""
from __future__ import annotations

import pytest


@pytest.fixture
def repo_root(tmp_path, monkeypatch):
    """An empty repo-root directory with LEERIE_SKIP_OVERLAP_JUDGE unset."""
    monkeypatch.delenv("LEERIE_SKIP_OVERLAP_JUDGE", raising=False)
    return tmp_path


def test_default_is_off(leerie, repo_root):
    """No CLI flag, no env, no file → False (judge runs on multi-planner runs)."""
    assert leerie.resolve_skip_overlap_judge(
        repo_root, cli_value=False) is False


def test_cli_flag_wins(leerie, repo_root, monkeypatch):
    """--skip-overlap-judge CLI flag is the highest precedence."""
    monkeypatch.setenv("LEERIE_SKIP_OVERLAP_JUDGE", "0")
    (repo_root / "leerie.toml").write_text(
        "skip_overlap_judge = false\n")
    assert leerie.resolve_skip_overlap_judge(
        repo_root, cli_value=True) is True


def test_env_set_true(leerie, repo_root, monkeypatch):
    monkeypatch.setenv("LEERIE_SKIP_OVERLAP_JUDGE", "1")
    assert leerie.resolve_skip_overlap_judge(
        repo_root, cli_value=False) is True


def test_env_set_false_falls_through_to_default(
        leerie, repo_root, monkeypatch):
    """An env value of 'false' is an explicit "use the default" — the
    default is False, so the result is False either way."""
    monkeypatch.setenv("LEERIE_SKIP_OVERLAP_JUDGE", "false")
    assert leerie.resolve_skip_overlap_judge(
        repo_root, cli_value=False) is False


def test_file_is_never_read(leerie, repo_root):
    """A waiver is the operator's (DESIGN §6 *A repository chooses no
    operator authority*): `skip_overlap_judge = true` in leerie.toml is not taken."""
    (repo_root / "leerie.toml").write_text(
        "skip_overlap_judge = true\n")
    assert leerie.resolve_skip_overlap_judge(
        repo_root, cli_value=False) is False


def test_env_wins_over_file(leerie, repo_root, monkeypatch):
    """Env is a session knob and outranks the committed leerie.toml default."""
    (repo_root / "leerie.toml").write_text(
        "skip_overlap_judge = true\n")
    monkeypatch.setenv("LEERIE_SKIP_OVERLAP_JUDGE", "false")
    assert leerie.resolve_skip_overlap_judge(
        repo_root, cli_value=False) is False


@pytest.mark.parametrize("value",
                         ["1", "true", "True", "TRUE", "yes", "on", "ON"])
def test_env_truthy_spellings(leerie, repo_root, monkeypatch, value):
    monkeypatch.setenv("LEERIE_SKIP_OVERLAP_JUDGE", value)
    assert leerie.resolve_skip_overlap_judge(
        repo_root, cli_value=False) is True


@pytest.mark.parametrize("value",
                         ["0", "false", "False", "FALSE", "no", "off", "OFF"])
def test_env_falsy_spellings(leerie, repo_root, monkeypatch, value):
    monkeypatch.setenv("LEERIE_SKIP_OVERLAP_JUDGE", value)
    assert leerie.resolve_skip_overlap_judge(
        repo_root, cli_value=False) is False


def test_env_garbage_dies(leerie, repo_root, monkeypatch):
    """Unrecognized boolean spelling in env → die so a typo doesn't
    get silently treated as False."""
    monkeypatch.setenv("LEERIE_SKIP_OVERLAP_JUDGE", "maybe")
    with pytest.raises(SystemExit):
        leerie.resolve_skip_overlap_judge(
            repo_root, cli_value=False)


def test_file_garbage_is_not_read(leerie, repo_root):
    """Never read, so not validated either."""
    (repo_root / "leerie.toml").write_text(
        "skip_overlap_judge = sometimes\n")
    assert leerie.resolve_skip_overlap_judge(
        repo_root, cli_value=False) is False


def test_env_empty_string_falls_through(leerie, repo_root, monkeypatch):
    monkeypatch.setenv("LEERIE_SKIP_OVERLAP_JUDGE", "")
    assert leerie.resolve_skip_overlap_judge(
        repo_root, cli_value=False) is False


def test_cli_false_with_env_true(leerie, repo_root, monkeypatch):
    """cli_value=False means '--skip-overlap-judge not passed'. The
    env/TOML can still set it True; CLI doesn't override env in this
    case because cli_value=False is 'I didn't pass the flag', not 'I
    want it off'."""
    monkeypatch.setenv("LEERIE_SKIP_OVERLAP_JUDGE", "1")
    assert leerie.resolve_skip_overlap_judge(
        repo_root, cli_value=False) is True
