"""Tests for resolve_skip_classification_check() — the
--skip-classification-check opt-out for `phase_classification_gate`'s
`classification_judge` category-set gate (DESIGN §8 *Independent adversarial
verification*).

Covers the precedence order: CLI flag → LEERIE_SKIP_CLASSIFICATION_CHECK env
var → skip_classification_check in leerie.toml → False (the gate runs by
default) — mirrors test_resolve_skip_integration_check.py, since both
resolvers share `_resolve_bool_pref` and both were added as the missing
escape hatch for a gate that had none. The precedence itself is the shared
helper's; what this file locks is the WIRING — the env-var name and the
leerie.toml key — neither of which any other test would notice a typo in,
because `_resolve_bool_pref` would simply never find the value and fall
through to the default.

This gate is the one that most needs an escape hatch: it runs before any
provision or plan spend and `die()`s on exhaustion, so a gate that will not
converge takes the whole run with it (measured: run 47ee1e9e, v0.29.0, dead
at phase 1).
"""
from __future__ import annotations

import asyncio

import pytest


@pytest.fixture
def repo_root(tmp_path, monkeypatch):
    monkeypatch.delenv("LEERIE_SKIP_CLASSIFICATION_CHECK", raising=False)
    return tmp_path


# --- resolver precedence ---------------------------------------------------- #

def test_default_is_off(leerie, repo_root):
    assert leerie.resolve_skip_classification_check(
        repo_root, cli_value=False) is False


def test_cli_flag_wins(leerie, repo_root, monkeypatch):
    monkeypatch.setenv("LEERIE_SKIP_CLASSIFICATION_CHECK", "0")
    (repo_root / "leerie.toml").write_text(
        "skip_classification_check = false\n")
    assert leerie.resolve_skip_classification_check(
        repo_root, cli_value=True) is True


def test_env_set_true(leerie, repo_root, monkeypatch):
    monkeypatch.setenv("LEERIE_SKIP_CLASSIFICATION_CHECK", "1")
    assert leerie.resolve_skip_classification_check(
        repo_root, cli_value=False) is True


def test_file_set_true_no_env(leerie, repo_root):
    (repo_root / "leerie.toml").write_text(
        "skip_classification_check = true\n")
    assert leerie.resolve_skip_classification_check(
        repo_root, cli_value=False) is True


def test_env_wins_over_file(leerie, repo_root, monkeypatch):
    (repo_root / "leerie.toml").write_text(
        "skip_classification_check = true\n")
    monkeypatch.setenv("LEERIE_SKIP_CLASSIFICATION_CHECK", "false")
    assert leerie.resolve_skip_classification_check(
        repo_root, cli_value=False) is False


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
def test_env_truthy_spellings(leerie, repo_root, monkeypatch, value):
    monkeypatch.setenv("LEERIE_SKIP_CLASSIFICATION_CHECK", value)
    assert leerie.resolve_skip_classification_check(
        repo_root, cli_value=False) is True


@pytest.mark.parametrize("value", ["0", "false", "FALSE", "no", "off"])
def test_env_falsy_spellings(leerie, repo_root, monkeypatch, value):
    monkeypatch.setenv("LEERIE_SKIP_CLASSIFICATION_CHECK", value)
    assert leerie.resolve_skip_classification_check(
        repo_root, cli_value=False) is False


def test_env_garbage_dies(leerie, repo_root, monkeypatch):
    monkeypatch.setenv("LEERIE_SKIP_CLASSIFICATION_CHECK", "maybe")
    with pytest.raises(SystemExit):
        leerie.resolve_skip_classification_check(repo_root, cli_value=False)


def test_file_garbage_dies(leerie, repo_root):
    (repo_root / "leerie.toml").write_text(
        "skip_classification_check = sometimes\n")
    with pytest.raises(SystemExit):
        leerie.resolve_skip_classification_check(repo_root, cli_value=False)


# --- the wiring the shared helper cannot catch ----------------------------- #

def test_the_toml_key_is_not_a_sibling_key(leerie, repo_root):
    """A copy-paste of a sibling's `file_key` would leave this resolver
    reading someone else's setting. Make the two sources DISAGREE so a
    bypass is distinguishable from a correct read (CLAUDE.md: parametrized
    value tests should make inputs disagree)."""
    (repo_root / "leerie.toml").write_text(
        "skip_integration_check = true\n"
        "skip_coverage_check = true\n"
        "skip_classification_check = false\n")
    assert leerie.resolve_skip_classification_check(
        repo_root, cli_value=False) is False


def test_the_env_var_is_not_a_sibling_var(leerie, repo_root, monkeypatch):
    """Same defect on the env side, and the sources must DISAGREE here too:
    setting this variable explicitly false rather than leaving it unset is
    what separates a correct read from a fall-through to the default."""
    monkeypatch.setenv("LEERIE_SKIP_CLASSIFICATION_CHECK", "false")
    monkeypatch.setenv("LEERIE_SKIP_INTEGRATION_CHECK", "1")
    monkeypatch.setenv("LEERIE_SKIP_COVERAGE_CHECK", "1")
    assert leerie.resolve_skip_classification_check(
        repo_root, cli_value=False) is False


# --- anti-vacuity control for the gate file's own bypass pin --------------- #

def _state(leerie, tmp_path):
    # No `repo_root`: the sibling sets it because real `claude_p` derives the
    # checkout write-denial from it, but the one test below that reaches
    # `claude_p` stubs it, so nothing here would read it. `run_dir` IS
    # load-bearing — `_judgment_cwd(st)` is evaluated as a `claude_p` kwarg.
    st = leerie.State.__new__(leerie.State)
    st.data = {"categories": ["bug-fixing"]}
    st.run_dir = tmp_path
    st.save = lambda: None
    st.bump_workers = lambda caps: None
    return st


def _caps(leerie) -> dict:
    return dict(leerie.DEFAULT_CAPS)


def test_skip_flag_off_still_invokes_the_gate(leerie, tmp_path, monkeypatch):
    """Anti-vacuity control: with the flag unset (default) the gate does
    reach claude_p, proving the test above measures the skip condition
    rather than an already-inert call site."""
    st = _state(leerie, tmp_path)
    called = {"n": 0}

    async def fake_claude_p(**kwargs):
        called["n"] += 1
        return {"categories_reviewed": ["bug-fixing"],
                "miscategorizations": [], "rationale": "clean"}

    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)

    routed = asyncio.run(leerie.phase_classification_gate(
        "task", st, _caps(leerie), False, {}, {}))

    assert routed is False
    assert called["n"] == 1
    assert st.data["classification_coverage_gate"]["rationale"] == "clean"
