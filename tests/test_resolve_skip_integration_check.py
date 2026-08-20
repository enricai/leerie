"""Tests for resolve_skip_integration_check() — the --skip-integration-check
opt-out for `integrate_wave`'s `integration_judge` behavioral-defect gate
(DESIGN §8 *Independent adversarial verification*).

Covers the precedence order: CLI flag → LEERIE_SKIP_INTEGRATION_CHECK env
var → skip_integration_check in leerie.toml → False (the gate runs by
default) — mirrors test_resolve_skip_completeness_check.py, since both
resolvers share `_resolve_bool_pref` and both were added as the missing
escape hatch for a gate with none. Also pins the phase-entry wiring inside
`_run_integration_judge_gate`, mirroring test_wiring_gate_resume.py's shape:
when the flag is set, the gate must return without invoking `claude_p`
at all — a full-phase skip, independent of the accept-integration/
audit-key mechanism.
"""
from __future__ import annotations

import asyncio

import pytest


@pytest.fixture
def repo_root(tmp_path, monkeypatch):
    monkeypatch.delenv("LEERIE_SKIP_INTEGRATION_CHECK", raising=False)
    return tmp_path


# --- resolver precedence ---------------------------------------------------- #

def test_default_is_off(leerie, repo_root):
    assert leerie.resolve_skip_integration_check(
        repo_root, cli_value=False) is False


def test_cli_flag_wins(leerie, repo_root, monkeypatch):
    monkeypatch.setenv("LEERIE_SKIP_INTEGRATION_CHECK", "0")
    (repo_root / "leerie.toml").write_text(
        "skip_integration_check = false\n")
    assert leerie.resolve_skip_integration_check(
        repo_root, cli_value=True) is True


def test_env_set_true(leerie, repo_root, monkeypatch):
    monkeypatch.setenv("LEERIE_SKIP_INTEGRATION_CHECK", "1")
    assert leerie.resolve_skip_integration_check(
        repo_root, cli_value=False) is True


def test_file_set_true_no_env(leerie, repo_root):
    (repo_root / "leerie.toml").write_text(
        "skip_integration_check = true\n")
    assert leerie.resolve_skip_integration_check(
        repo_root, cli_value=False) is True


def test_env_wins_over_file(leerie, repo_root, monkeypatch):
    (repo_root / "leerie.toml").write_text(
        "skip_integration_check = true\n")
    monkeypatch.setenv("LEERIE_SKIP_INTEGRATION_CHECK", "false")
    assert leerie.resolve_skip_integration_check(
        repo_root, cli_value=False) is False


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
def test_env_truthy_spellings(leerie, repo_root, monkeypatch, value):
    monkeypatch.setenv("LEERIE_SKIP_INTEGRATION_CHECK", value)
    assert leerie.resolve_skip_integration_check(
        repo_root, cli_value=False) is True


@pytest.mark.parametrize("value", ["0", "false", "FALSE", "no", "off"])
def test_env_falsy_spellings(leerie, repo_root, monkeypatch, value):
    monkeypatch.setenv("LEERIE_SKIP_INTEGRATION_CHECK", value)
    assert leerie.resolve_skip_integration_check(
        repo_root, cli_value=False) is False


def test_env_garbage_dies(leerie, repo_root, monkeypatch):
    monkeypatch.setenv("LEERIE_SKIP_INTEGRATION_CHECK", "maybe")
    with pytest.raises(SystemExit):
        leerie.resolve_skip_integration_check(repo_root, cli_value=False)


# --- phase-entry wiring: zero claude_p calls when the flag is set ---------- #

def _state(leerie, tmp_path):
    st = leerie.State.__new__(leerie.State)
    # claude_p derives the checkout write-denial from this
    # (_repo_write_denials); State.__new__ skips __init__, so it
    # must be set explicitly or both that and the §12 cwd guard
    # silently no-op.
    st.repo_root = "/leerie-test-user-repo"
    st.data = {}
    st.run_dir = tmp_path
    st.save = lambda: None
    st.bump_workers = lambda caps: None
    return st


def _caps(leerie) -> dict:
    return dict(leerie.DEFAULT_CAPS)


def test_skip_flag_short_circuits_before_any_claude_p_call(
        leerie, tmp_path, monkeypatch):
    """The load-bearing pin: with skip_integration_check set, the gate must
    never invoke claude_p — a stub that raises if called proves it."""
    st = _state(leerie, tmp_path)
    st.data["skip_integration_check"] = True

    async def fake_claude_p(**kwargs):
        raise AssertionError(
            "integration_judge must not be invoked when "
            "skip_integration_check is set")

    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)

    asyncio.run(leerie._run_integration_judge_gate(
        "feat-001", tmp_path, {"intent": "i", "criteria_results": []}, [],
        _caps(leerie), st, {}, {}))

    # No verdict recorded — this is a bypass, not a clean-pass judgment.
    assert "integration_gate" not in st.data


def test_skip_flag_off_still_invokes_the_gate(leerie, tmp_path, monkeypatch):
    """Anti-vacuity control: with the flag unset (default), the gate does
    reach claude_p, proving the test above measures the skip condition
    rather than an already-inert call site."""
    st = _state(leerie, tmp_path)
    called = {"n": 0}

    async def fake_claude_p(**kwargs):
        called["n"] += 1
        return {"defects": [], "advisories": []}

    async def fake_run_proc(argv, cwd=None):
        class _R:
            stdout = "deadbeef\n"
            stderr = ""
            returncode = 0
        return _R()

    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    monkeypatch.setattr(leerie, "run_proc", fake_run_proc)

    asyncio.run(leerie._run_integration_judge_gate(
        "feat-001", tmp_path, {"intent": "i", "criteria_results": []}, [],
        _caps(leerie), st, {}, {}))

    assert called["n"] == 1
    assert st.data["integration_gate"]["feat-001"]["accepted"] is True
