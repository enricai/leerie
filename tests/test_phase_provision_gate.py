"""Tests for phase_provision_gate (DESIGN §8, §6½): the install-recipe gate
that runs an independent provision_judge, gates on a non-empty recipe_failures
array, and re-drives phase_provision via _run_checked_loop.
"""
from __future__ import annotations

import asyncio
import inspect

import pytest


def _state(leerie, tmp_path, recipe, run_id="test-provision-gate-aaa"):
    leerie_root = tmp_path / ".leerie"
    (leerie_root / "runs" / run_id).mkdir(parents=True)
    st = leerie.State(leerie_root, run_id)
    st.data = {"task": "test", "worker_count": 0,
               "provision": {"recipe": recipe, "mise_versions": {}}}
    st.save()
    return st


def _caps(leerie):
    caps = dict(leerie.DEFAULT_CAPS)
    caps["judgment_check_rounds"] = 3
    return caps


MODELS = {"provision_judge": "opus"}
EFFORTS = {"provision_judge": "medium"}

_PIP_RECIPE = [{"kind": "install", "command": ["pip", "install", "-r",
                                               "requirements.txt"],
                "working_dir": ".", "timeout_s": 300}]


# === Tier 1: wiring ========================================================

class TestWiring:
    def test_invokes_provision_judge(self, leerie):
        src = inspect.getsource(leerie.phase_provision_gate)
        assert 'schema_key="provision_judge"' in src

    def test_uses_run_checked_loop(self, leerie):
        src = inspect.getsource(leerie.phase_provision_gate)
        assert "_run_checked_loop(" in src

    def test_is_detect_and_die_no_re_drive(self, leerie):
        """The provision gate is a single detect-and-die pass — it must NOT
        re-drive phase_provision (a recipe re-detects identically), i.e. it
        passes no make_feedback_prompt to _run_checked_loop."""
        src = inspect.getsource(leerie.phase_provision_gate)
        assert "await phase_provision(" not in src
        assert "make_feedback_prompt=" not in src

    def test_dies_on_failure(self, leerie):
        src = inspect.getsource(leerie.phase_provision_gate)
        assert "die(" in src

    def test_called_after_provision_in_run_phases(self, leerie):
        src = inspect.getsource(leerie._run_phases)
        assert "await phase_provision_gate(" in src
        i_prov = src.index("await phase_provision(")
        i_gate = src.index("await phase_provision_gate(")
        assert i_prov < i_gate


# === Tier 2: behavioral ====================================================

def test_kind_none_recipe_cheap_skips(leerie, tmp_path, monkeypatch):
    st = _state(leerie, tmp_path,
                [{"kind": "none", "command": [], "working_dir": ".",
                  "timeout_s": 0}])

    async def fake_claude_p(**kwargs):
        pytest.fail("provision_judge must not run for a kind:none recipe")

    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    asyncio.run(leerie.phase_provision_gate(
        tmp_path, st, _caps(leerie), MODELS, EFFORTS))
    assert "provision_recipe_gate" not in st.data


def test_clean_recipe_passes(leerie, tmp_path, monkeypatch):
    st = _state(leerie, tmp_path, _PIP_RECIPE)

    async def fake_claude_p(**kwargs):
        return {"recipe_reviewed": True, "recipe_failures": [],
                "rationale": "runs fine"}

    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    asyncio.run(leerie.phase_provision_gate(
        tmp_path, st, _caps(leerie), MODELS, EFFORTS))
    assert st.data.get("provision_recipe_gate") is not None


def test_broken_recipe_dies(leerie, tmp_path, monkeypatch):
    """A concrete recipe failure die()s immediately (detect-and-die, no
    re-drive)."""
    st = _state(leerie, tmp_path, _PIP_RECIPE)

    async def fake_claude_p(**kwargs):
        return {"recipe_reviewed": True, "recipe_failures": [{
            "kind": "missing_break_system_packages",
            "command": "pip install -r requirements.txt",
            "concrete_reason": "externally-managed system Python fails",
            "fix": "add --break-system-packages",
        }], "rationale": "would fail"}

    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    with pytest.raises(SystemExit):
        asyncio.run(leerie.phase_provision_gate(
            tmp_path, st, _caps(leerie), MODELS, EFFORTS))


def test_vague_failure_does_not_gate(leerie, tmp_path, monkeypatch):
    """A failure missing concrete_reason is dropped and must NOT die."""
    st = _state(leerie, tmp_path, _PIP_RECIPE)

    async def fake_claude_p(**kwargs):
        return {"recipe_reviewed": True, "recipe_failures": [{
            "kind": "missing_break_system_packages",
            "command": "pip install",
            "concrete_reason": "",  # vague → dropped
            "fix": "x",
        }], "rationale": "hand-wave"}

    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    asyncio.run(leerie.phase_provision_gate(
        tmp_path, st, _caps(leerie), MODELS, EFFORTS))


def test_judge_crash_degrades(leerie, tmp_path, monkeypatch):
    st = _state(leerie, tmp_path, _PIP_RECIPE)

    async def fake_claude_p(**kwargs):
        raise leerie.WorkerError("crash")

    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    asyncio.run(leerie.phase_provision_gate(
        tmp_path, st, _caps(leerie), MODELS, EFFORTS))
