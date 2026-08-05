"""Tests for resolve_skip_coverage_check() — the --skip-coverage-check
opt-out for the phase 2⅞½ task-coverage gate (a single advisory
`task_coverage_judge` invocation since 2026-08-04; the
`check_required_items_coverage` floor it used to compose with was deleted).

Covers the precedence order: CLI flag → LEERIE_SKIP_COVERAGE_CHECK env
var → skip_coverage_check in leerie.toml → False (the gate runs on
every plan by default).

Mirrors test_resolve_skip_adherence_check.py — all the skip-flag
resolvers share `_resolve_bool_pref`, so this file locks the wiring (env
var name + file key), not the resolution logic.

Added after run 488c42e5: the task-coverage gate was the ONLY planning
gate with no operator override, and the `task_coverage_judge` counted a
task item the task itself marked deferred as `missing_work` — a verdict
no planner could satisfy without contradicting the task.
"""
from __future__ import annotations

import pytest


@pytest.fixture
def repo_root(tmp_path, monkeypatch):
    """An empty repo-root directory with LEERIE_SKIP_COVERAGE_CHECK unset."""
    monkeypatch.delenv("LEERIE_SKIP_COVERAGE_CHECK", raising=False)
    return tmp_path


def test_default_is_off(leerie, repo_root):
    """No CLI flag, no env, no file → False (gate runs on every plan)."""
    assert leerie.resolve_skip_coverage_check(
        repo_root, cli_value=False) is False


def test_cli_flag_wins(leerie, repo_root, monkeypatch):
    """--skip-coverage-check CLI flag is the highest precedence."""
    monkeypatch.setenv("LEERIE_SKIP_COVERAGE_CHECK", "0")
    (repo_root / "leerie.toml").write_text(
        "skip_coverage_check = false\n")
    assert leerie.resolve_skip_coverage_check(
        repo_root, cli_value=True) is True


def test_env_set_true(leerie, repo_root, monkeypatch):
    monkeypatch.setenv("LEERIE_SKIP_COVERAGE_CHECK", "1")
    assert leerie.resolve_skip_coverage_check(
        repo_root, cli_value=False) is True


def test_env_set_false_falls_through_to_default(
        leerie, repo_root, monkeypatch):
    """An env value of 'false' is an explicit "use the default" — the
    default is False, so the result is False either way."""
    monkeypatch.setenv("LEERIE_SKIP_COVERAGE_CHECK", "false")
    assert leerie.resolve_skip_coverage_check(
        repo_root, cli_value=False) is False


def test_file_set_true_no_env(leerie, repo_root):
    (repo_root / "leerie.toml").write_text(
        "skip_coverage_check = true\n")
    assert leerie.resolve_skip_coverage_check(
        repo_root, cli_value=False) is True


def test_env_wins_over_file(leerie, repo_root, monkeypatch):
    """Env is a session knob and outranks the committed leerie.toml default."""
    (repo_root / "leerie.toml").write_text(
        "skip_coverage_check = true\n")
    monkeypatch.setenv("LEERIE_SKIP_COVERAGE_CHECK", "false")
    assert leerie.resolve_skip_coverage_check(
        repo_root, cli_value=False) is False


@pytest.mark.parametrize("value",
                         ["1", "true", "True", "TRUE", "yes", "on", "ON"])
def test_env_truthy_spellings(leerie, repo_root, monkeypatch, value):
    monkeypatch.setenv("LEERIE_SKIP_COVERAGE_CHECK", value)
    assert leerie.resolve_skip_coverage_check(
        repo_root, cli_value=False) is True


@pytest.mark.parametrize("value",
                         ["0", "false", "False", "FALSE", "no", "off", "OFF"])
def test_env_falsy_spellings(leerie, repo_root, monkeypatch, value):
    monkeypatch.setenv("LEERIE_SKIP_COVERAGE_CHECK", value)
    assert leerie.resolve_skip_coverage_check(
        repo_root, cli_value=False) is False


def test_env_garbage_dies(leerie, repo_root, monkeypatch):
    """Unrecognized boolean spelling in env → die so a typo doesn't
    get silently treated as False."""
    monkeypatch.setenv("LEERIE_SKIP_COVERAGE_CHECK", "maybe")
    with pytest.raises(SystemExit):
        leerie.resolve_skip_coverage_check(
            repo_root, cli_value=False)


def test_file_garbage_dies(leerie, repo_root):
    (repo_root / "leerie.toml").write_text(
        "skip_coverage_check = sometimes\n")
    with pytest.raises(SystemExit):
        leerie.resolve_skip_coverage_check(
            repo_root, cli_value=False)


def test_env_empty_string_falls_through(leerie, repo_root, monkeypatch):
    monkeypatch.setenv("LEERIE_SKIP_COVERAGE_CHECK", "")
    assert leerie.resolve_skip_coverage_check(
        repo_root, cli_value=False) is False


def test_cli_false_with_env_true(leerie, repo_root, monkeypatch):
    """cli_value=False means '--skip-coverage-check not passed'. The
    env/TOML can still set it True; CLI doesn't override env in this
    case because cli_value=False is 'I didn't pass the flag', not 'I
    want it off'."""
    monkeypatch.setenv("LEERIE_SKIP_COVERAGE_CHECK", "1")
    assert leerie.resolve_skip_coverage_check(
        repo_root, cli_value=False) is True
