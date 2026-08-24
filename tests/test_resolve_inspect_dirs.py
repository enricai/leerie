"""Tests for resolve_inspect_dirs() — the --inspect-dir resolver.

Covers the precedence order: CLI flag (repeatable) → LEERIE_INSPECT_DIRS
env var (colon-separated) → inspect_dirs in leerie.toml (comma-separated)
→ [].

Also covers path expansion (~ → $HOME), absolute-path normalization, and
dedup of repeated entries.
"""
from __future__ import annotations

import os

import pytest


@pytest.fixture
def repo_root(tmp_path, monkeypatch):
    """An empty repo-root directory with LEERIE_INSPECT_DIRS unset."""
    monkeypatch.delenv("LEERIE_INSPECT_DIRS", raising=False)
    return tmp_path


def test_default_is_empty(leerie, repo_root):
    """No CLI flag, no env, no file → []."""
    assert leerie.resolve_inspect_dirs(repo_root, cli_values=None) == []


def test_default_empty_list_is_empty(leerie, repo_root):
    """An empty CLI list falls through to env/file/default — argparse with
    action='append' uses None for 'no flags', but a deliberate empty list
    should still fall through, not short-circuit to []."""
    assert leerie.resolve_inspect_dirs(repo_root, cli_values=[]) == []


def test_cli_single_path(leerie, repo_root, tmp_path):
    target = tmp_path / "sibling"
    target.mkdir()
    out = leerie.resolve_inspect_dirs(repo_root, cli_values=[str(target)])
    assert out == [str(target.resolve())]


def test_cli_multiple_paths(leerie, repo_root, tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    out = leerie.resolve_inspect_dirs(
        repo_root, cli_values=[str(a), str(b)])
    assert out == [str(a.resolve()), str(b.resolve())]


def test_cli_dedups(leerie, repo_root, tmp_path):
    """Same path twice in CLI args → one entry. Avoids passing a
    duplicate --add-dir to the CLI for no reason."""
    a = tmp_path / "a"
    a.mkdir()
    out = leerie.resolve_inspect_dirs(
        repo_root, cli_values=[str(a), str(a)])
    assert out == [str(a.resolve())]


def test_cli_wins_over_env(leerie, repo_root, tmp_path, monkeypatch):
    """CLI is highest precedence — env and TOML are ignored when CLI is set."""
    cli_dir = tmp_path / "cli"
    env_dir = tmp_path / "env"
    cli_dir.mkdir()
    env_dir.mkdir()
    monkeypatch.setenv("LEERIE_INSPECT_DIRS", str(env_dir))
    out = leerie.resolve_inspect_dirs(repo_root, cli_values=[str(cli_dir)])
    assert out == [str(cli_dir.resolve())]


def test_env_single(leerie, repo_root, tmp_path, monkeypatch):
    target = tmp_path / "x"
    target.mkdir()
    monkeypatch.setenv("LEERIE_INSPECT_DIRS", str(target))
    out = leerie.resolve_inspect_dirs(repo_root, cli_values=None)
    assert out == [str(target.resolve())]


def test_env_colon_separated(leerie, repo_root, tmp_path, monkeypatch):
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    monkeypatch.setenv("LEERIE_INSPECT_DIRS", f"{a}:{b}")
    out = leerie.resolve_inspect_dirs(repo_root, cli_values=None)
    assert out == [str(a.resolve()), str(b.resolve())]


def test_env_wins_over_file(leerie, repo_root, tmp_path, monkeypatch):
    """Env is a session knob and outranks the committed leerie.toml
    default — same precedence pattern as source-of-truth / no-push."""
    env_dir = tmp_path / "env"
    file_dir = tmp_path / "file"
    env_dir.mkdir()
    file_dir.mkdir()
    monkeypatch.setenv("LEERIE_INSPECT_DIRS", str(env_dir))
    (repo_root / "leerie.toml").write_text(
        f'inspect_dirs = "{file_dir}"\n')
    out = leerie.resolve_inspect_dirs(repo_root, cli_values=None)
    assert out == [str(env_dir.resolve())]


def test_file_comma_separated(leerie, repo_root, tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    (repo_root / "leerie.toml").write_text(
        f'inspect_dirs = "{a},{b}"\n')
    out = leerie.resolve_inspect_dirs(repo_root, cli_values=None)
    assert out == [str(a.resolve()), str(b.resolve())]


def test_env_empty_string_falls_through(leerie, repo_root, tmp_path,
                                        monkeypatch):
    """LEERIE_INSPECT_DIRS="" should be treated as unset, not as a single
    empty-path value."""
    file_dir = tmp_path / "file"
    file_dir.mkdir()
    monkeypatch.setenv("LEERIE_INSPECT_DIRS", "")
    (repo_root / "leerie.toml").write_text(
        f'inspect_dirs = "{file_dir}"\n')
    out = leerie.resolve_inspect_dirs(repo_root, cli_values=None)
    assert out == [str(file_dir.resolve())]


def test_tilde_expansion(leerie, repo_root, monkeypatch, tmp_path):
    """`~/foo` in any source must expand to $HOME/foo so a user can write
    `--inspect-dir ~/src/beacon` without shell expansion (e.g. when the
    flag value comes from an env var or TOML, the shell never sees it)."""
    monkeypatch.setenv("HOME", str(tmp_path))
    home_dir = tmp_path / "thing"
    home_dir.mkdir()
    out = leerie.resolve_inspect_dirs(repo_root, cli_values=["~/thing"])
    assert out == [str(home_dir.resolve())]


def test_tilde_naming_no_user_is_left_unexpanded(leerie, repo_root, tmp_path,
                                                 monkeypatch):
    """`~name` for a name absent from the password database must resolve to
    the token itself, exactly as the shell leaves it (`echo ~nosuchuser`
    prints it verbatim) -- it must not raise.

    `Path.expanduser()` raises `RuntimeError("Could not determine home
    directory.")` on that shape regardless of $HOME, because it takes the
    `~user` branch and `pwd.getpwnam` fails. The sibling call site in
    `_unreachable_task_references` had the same hole and killed two runs at
    phase 2 (2026-08-24); here it would abort startup before classify, with a
    traceback instead of a message, on nothing worse than a typo.

    Asserts the resolved VALUE, not merely that the call returned: a fix that
    silently dropped the entry would also "not raise", and this resolver's
    documented posture is to accept the path and let the use site produce the
    clear error.
    """
    monkeypatch.chdir(tmp_path)
    out = leerie.resolve_inspect_dirs(
        repo_root, cli_values=["~nosuchuser-zzz"])
    assert out == [str(tmp_path.resolve() / "~nosuchuser-zzz")]


def test_blank_entries_skipped(leerie, repo_root, tmp_path, monkeypatch):
    """An env value of `a::b` (two colons in a row) shouldn't yield an
    empty-string entry that resolves to cwd. Same with trailing commas in
    TOML."""
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    monkeypatch.setenv("LEERIE_INSPECT_DIRS", f"{a}::{b}:")
    out = leerie.resolve_inspect_dirs(repo_root, cli_values=None)
    assert out == [str(a.resolve()), str(b.resolve())]


def test_inspect_dirs_in_state_fields(leerie):
    """STATE_FIELDS must include 'inspect_dirs' so the orchestrator's
    state-key audit (test_state_fields.py) doesn't flag it as drift."""
    assert "inspect_dirs" in leerie.STATE_FIELDS
