"""Tests for resolve_dangerously_skip_permissions() — the
--dangerously-skip-permissions escape hatch (DESIGN §12).

Covers the precedence order: CLI flag →
LEERIE_DANGEROUSLY_SKIP_PERMISSIONS env var →
False (no leerie.toml tier) (judgment workers
stay narrow-allowlisted by default).

Also covers boolean parsing (1/0, true/false, yes/no, on/off) and the
die() path for typos in env or TOML. Mirrors test_resolve_no_push.py
exactly — both resolvers share `_resolve_bool_pref`, so this file
locks the wiring (env var name + file key), not the resolution logic.
"""
from __future__ import annotations

import pytest


@pytest.fixture
def repo_root(tmp_path, monkeypatch):
    """An empty repo-root directory with LEERIE_DANGEROUSLY_SKIP_PERMISSIONS unset."""
    monkeypatch.delenv("LEERIE_DANGEROUSLY_SKIP_PERMISSIONS", raising=False)
    return tmp_path


def test_default_is_off(leerie, repo_root):
    """No CLI flag, no env, no file → False (§12 enforcement holds)."""
    assert leerie.resolve_dangerously_skip_permissions(
        repo_root, cli_value=False) is False


def test_cli_flag_wins(leerie, repo_root, monkeypatch):
    """--dangerously-skip-permissions CLI flag is the highest precedence."""
    monkeypatch.setenv("LEERIE_DANGEROUSLY_SKIP_PERMISSIONS", "0")
    (repo_root / "leerie.toml").write_text(
        "dangerously_skip_permissions = false\n")
    assert leerie.resolve_dangerously_skip_permissions(
        repo_root, cli_value=True) is True


def test_env_set_true(leerie, repo_root, monkeypatch):
    monkeypatch.setenv("LEERIE_DANGEROUSLY_SKIP_PERMISSIONS", "1")
    assert leerie.resolve_dangerously_skip_permissions(
        repo_root, cli_value=False) is True


def test_env_set_false_falls_through_to_default(leerie, repo_root, monkeypatch):
    """An env value of 'false' is an explicit "use the default" — the
    default is False, so the result is False either way. Pin behavior
    so callers know an env-false isn't 'unset'."""
    monkeypatch.setenv("LEERIE_DANGEROUSLY_SKIP_PERMISSIONS", "false")
    assert leerie.resolve_dangerously_skip_permissions(
        repo_root, cli_value=False) is False


def test_file_is_never_read(leerie, repo_root):
    """A waiver is the operator's (DESIGN §6 *A repository chooses no
    operator authority*): `dangerously_skip_permissions = true` in leerie.toml is not taken."""
    (repo_root / "leerie.toml").write_text(
        "dangerously_skip_permissions = true\n")
    assert leerie.resolve_dangerously_skip_permissions(
        repo_root, cli_value=False) is False


def test_env_wins_over_file(leerie, repo_root, monkeypatch):
    """Env is a session knob and outranks the committed leerie.toml
    default — same precedence pattern as source-of-truth / no-push."""
    (repo_root / "leerie.toml").write_text(
        "dangerously_skip_permissions = true\n")
    monkeypatch.setenv("LEERIE_DANGEROUSLY_SKIP_PERMISSIONS", "false")
    assert leerie.resolve_dangerously_skip_permissions(
        repo_root, cli_value=False) is False


@pytest.mark.parametrize("value", ["1", "true", "True", "TRUE", "yes", "on", "ON"])
def test_env_truthy_spellings(leerie, repo_root, monkeypatch, value):
    monkeypatch.setenv("LEERIE_DANGEROUSLY_SKIP_PERMISSIONS", value)
    assert leerie.resolve_dangerously_skip_permissions(
        repo_root, cli_value=False) is True


@pytest.mark.parametrize("value", ["0", "false", "False", "FALSE", "no", "off", "OFF"])
def test_env_falsy_spellings(leerie, repo_root, monkeypatch, value):
    monkeypatch.setenv("LEERIE_DANGEROUSLY_SKIP_PERMISSIONS", value)
    assert leerie.resolve_dangerously_skip_permissions(
        repo_root, cli_value=False) is False


def test_env_garbage_dies(leerie, repo_root, monkeypatch):
    """Unrecognized boolean spelling in env → die so a typo doesn't
    get silently treated as False — the safe default in this case,
    but the user wrote the wrong thing and should be told."""
    monkeypatch.setenv("LEERIE_DANGEROUSLY_SKIP_PERMISSIONS", "maybe")
    with pytest.raises(SystemExit):
        leerie.resolve_dangerously_skip_permissions(
            repo_root, cli_value=False)


def test_file_garbage_is_not_read(leerie, repo_root):
    """Never read, so not validated either."""
    (repo_root / "leerie.toml").write_text(
        "dangerously_skip_permissions = sometimes\n")
    assert leerie.resolve_dangerously_skip_permissions(
        repo_root, cli_value=False) is False


def test_env_empty_string_falls_through(leerie, repo_root, monkeypatch):
    """LEERIE_DANGEROUSLY_SKIP_PERMISSIONS="" should be treated as
    unset, not as a value."""
    monkeypatch.setenv("LEERIE_DANGEROUSLY_SKIP_PERMISSIONS", "")
    assert leerie.resolve_dangerously_skip_permissions(
        repo_root, cli_value=False) is False


def test_cli_false_with_env_true(leerie, repo_root, monkeypatch):
    """CLI cli_value=False means '--dangerously-skip-permissions not
    passed' (action=store_true default). The env/TOML can still set
    it True. CLI doesn't override env in this case because
    cli_value=False isn't an explicit 'I want it off' signal — it's
    just 'I didn't pass the flag'."""
    monkeypatch.setenv("LEERIE_DANGEROUSLY_SKIP_PERMISSIONS", "1")
    assert leerie.resolve_dangerously_skip_permissions(
        repo_root, cli_value=False) is True
