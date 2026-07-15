"""Tests for resolve_runtime().

Covers the CLI flag → env var → per-repo file → 'local' resolution order,
the value enum, comment/whitespace handling, and the die() path for
invalid values.
"""
from __future__ import annotations

import pytest


@pytest.fixture
def repo_root(tmp_path, monkeypatch):
    """An empty repo-root directory with LEERIE_RUNTIME unset."""
    monkeypatch.delenv("LEERIE_RUNTIME", raising=False)
    return tmp_path


def test_default_is_local(leerie, repo_root):
    assert leerie.resolve_runtime(repo_root) == "local"


def test_file_present_env_unset(leerie, repo_root):
    (repo_root / "leerie.toml").write_text("runtime = fly\n")
    assert leerie.resolve_runtime(repo_root) == "fly"


def test_file_absent_env_set(leerie, repo_root, monkeypatch):
    monkeypatch.setenv("LEERIE_RUNTIME", "fly")
    assert leerie.resolve_runtime(repo_root) == "fly"


def test_env_wins_over_file(leerie, repo_root, monkeypatch):
    (repo_root / "leerie.toml").write_text("runtime = fly\n")
    monkeypatch.setenv("LEERIE_RUNTIME", "local")
    assert leerie.resolve_runtime(repo_root) == "local"


def test_cli_value_wins_over_env_and_file(leerie, repo_root, monkeypatch):
    (repo_root / "leerie.toml").write_text("runtime = fly\n")
    monkeypatch.setenv("LEERIE_RUNTIME", "fly")
    assert leerie.resolve_runtime(repo_root, cli_value="local") == "local"


def test_cli_value_ec2_wins_over_env_and_file(leerie, repo_root, monkeypatch):
    (repo_root / "leerie.toml").write_text("runtime = fly\n")
    monkeypatch.setenv("LEERIE_RUNTIME", "fly")
    assert leerie.resolve_runtime(repo_root, cli_value="ec2") == "ec2"


def test_file_present_env_unset_ec2(leerie, repo_root):
    (repo_root / "leerie.toml").write_text("runtime = ec2\n")
    assert leerie.resolve_runtime(repo_root) == "ec2"


def test_file_absent_env_set_ec2(leerie, repo_root, monkeypatch):
    monkeypatch.setenv("LEERIE_RUNTIME", "ec2")
    assert leerie.resolve_runtime(repo_root) == "ec2"


def test_env_ec2_wins_over_file(leerie, repo_root, monkeypatch):
    (repo_root / "leerie.toml").write_text("runtime = local\n")
    monkeypatch.setenv("LEERIE_RUNTIME", "ec2")
    assert leerie.resolve_runtime(repo_root) == "ec2"


def test_cli_value_none_falls_back(leerie, repo_root, monkeypatch):
    monkeypatch.setenv("LEERIE_RUNTIME", "fly")
    assert leerie.resolve_runtime(repo_root, cli_value=None) == "fly"


def test_quoted_file_value(leerie, repo_root):
    (repo_root / "leerie.toml").write_text('runtime = "fly"\n')
    assert leerie.resolve_runtime(repo_root) == "fly"


def test_single_quoted_file_value(leerie, repo_root):
    (repo_root / "leerie.toml").write_text("runtime = 'local'\n")
    assert leerie.resolve_runtime(repo_root) == "local"


def test_comments_and_blank_lines_tolerated(leerie, repo_root):
    (repo_root / "leerie.toml").write_text(
        "# leerie config\n\n  runtime = fly  \n# trailing\n"
    )
    assert leerie.resolve_runtime(repo_root) == "fly"


@pytest.mark.parametrize("value", ["local", "fly", "ec2"])
def test_all_values_accepted_in_file(leerie, repo_root, value):
    (repo_root / "leerie.toml").write_text(f"runtime = {value}\n")
    assert leerie.resolve_runtime(repo_root) == value


@pytest.mark.parametrize("value", ["local", "fly", "ec2"])
def test_all_values_accepted_in_env(leerie, repo_root, monkeypatch, value):
    monkeypatch.setenv("LEERIE_RUNTIME", value)
    assert leerie.resolve_runtime(repo_root) == value


def test_bad_file_value_dies(leerie, repo_root, capsys):
    (repo_root / "leerie.toml").write_text("runtime = bogus\n")
    with pytest.raises(SystemExit) as exc:
        leerie.resolve_runtime(repo_root)
    assert exc.value.code != 0
    err = capsys.readouterr().err
    assert "is not one of" in err
    assert "bogus" in err


def test_bad_env_value_dies(leerie, repo_root, monkeypatch, capsys):
    monkeypatch.setenv("LEERIE_RUNTIME", "nope")
    with pytest.raises(SystemExit) as exc:
        leerie.resolve_runtime(repo_root)
    assert exc.value.code != 0
    err = capsys.readouterr().err
    assert "is not one of" in err
    assert "nope" in err


def test_empty_env_treated_as_unset(leerie, repo_root, monkeypatch):
    monkeypatch.setenv("LEERIE_RUNTIME", "")
    assert leerie.resolve_runtime(repo_root) == "local"


def test_whitespace_only_env_treated_as_unset(leerie, repo_root, monkeypatch):
    monkeypatch.setenv("LEERIE_RUNTIME", "   ")
    assert leerie.resolve_runtime(repo_root) == "local"
