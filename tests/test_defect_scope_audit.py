"""Tests for the pre-planning defect-scope audit (DESIGN §5 *Defect-scope
audit*): `phase_defect_scope_audit`, the planner-ctx injection, and the
mechanical floor `_warn_defect_sites_uncovered`.

Motivation (measured, 2026-09-22..25): a multi-site defect was re-planned
as "the one remaining gap" run after run — 7 of 11 production commits
re-edited the same ~120-line region, each faithful to its own narrow
plan, while the live symptom survived. The full site enumeration was two
greps away on day one; no one was asked to produce it before planning.

Coverage discipline: the phase function and the warn are executed (with a
stubbed `claude_p` dispatching on schema_key); the ctx test drives the
REAL `phase_plan` and asserts the site text lands in the planner's
prompt. Values, not key presence.
"""
from __future__ import annotations

import asyncio
import inspect

import pytest

MODELS = {"defect_scope_auditor": "sonnet", "planner": "sonnet",
          "fit_judge": "sonnet", "splitter": "sonnet"}
EFFORTS = {"defect_scope_auditor": "medium", "planner": "medium",
           "fit_judge": "medium", "splitter": "medium"}

AUDIT = {
    "applicable": True,
    "defect_shape": "candidate matching keys on positional index",
    "sites": [
        {"file": "src/example_module.py", "symbol": "merge_candidates",
         "line_hint": 120, "role": "decision_site"},
        {"file": "src/example_module.py", "symbol": "collect_chain_values",
         "line_hint": 480, "role": "bypass"},
    ],
    "chokepoint": {"exists": True, "file": "src/example_module.py",
                   "symbol": "resolve_identity_key",
                   "rationale": "sole producer of the comparison key"},
}


def _state(leerie, tmp_path, **overrides):
    leerie_root = tmp_path / ".leerie"
    run_id = "test-defect-scope"
    run_dir = leerie_root / "runs" / run_id
    run_dir.mkdir(parents=True)
    st = leerie.State(leerie_root, run_id)
    st.data = {"task": "fix the bug", "worker_count": 0,
               "categories": ["bug-fixing"],
               "classifier_questions": [],
               "needs_source_of_truth": False,
               "source_of_truth_pref": "both",
               "skip_repo_map": True,
               "planning_worktree": str(run_dir / "worktrees" / "planning")}
    st.data.update(overrides)
    st.save()
    return st


def _caps(leerie):
    caps = dict(leerie.DEFAULT_CAPS)
    caps["judgment_check_rounds"] = 3
    return caps


def _patch_auditor(leerie, monkeypatch, result):
    calls: list[dict] = []

    async def fake_claude_p(**kwargs):
        assert kwargs.get("schema_key") == "defect_scope_auditor"
        calls.append(kwargs)
        if result == "CRASH":
            raise leerie.WorkerError("auditor boom")
        return dict(result)

    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    return calls


# === the phase, executed ===================================================

def test_bug_fixing_task_gets_the_enumeration(leerie, tmp_path, monkeypatch):
    st = _state(leerie, tmp_path)
    calls = _patch_auditor(leerie, monkeypatch, AUDIT)
    scope = asyncio.run(leerie.phase_defect_scope_audit(
        "fix the bug", st, _caps(leerie), MODELS, EFFORTS))
    assert len(calls) == 1
    assert scope["applicable"] is True
    assert [s["symbol"] for s in scope["sites"]] == [
        "merge_candidates", "collect_chain_values"]
    assert scope["chokepoint"]["symbol"] == "resolve_identity_key"
    # Judgment-worker scope pins.
    assert calls[0]["autonomous"] is False
    assert calls[0]["allowed_tools"] == leerie.INSPECT_TOOLS


def test_non_bug_fixing_task_pays_nothing(leerie, tmp_path, monkeypatch):
    st = _state(leerie, tmp_path, categories=["documentation"])
    calls = _patch_auditor(leerie, monkeypatch, AUDIT)
    scope = asyncio.run(leerie.phase_defect_scope_audit(
        "write docs", st, _caps(leerie), MODELS, EFFORTS))
    assert scope == {"applicable": False}
    assert calls == []


def test_not_applicable_verdict_is_respected(leerie, tmp_path, monkeypatch):
    st = _state(leerie, tmp_path)
    _patch_auditor(leerie, monkeypatch,
                   {"applicable": False, "sites": []})
    scope = asyncio.run(leerie.phase_defect_scope_audit(
        "fix the bug", st, _caps(leerie), MODELS, EFFORTS))
    assert scope == {"applicable": False}


def test_crash_every_round_degrades(leerie, tmp_path, monkeypatch):
    st = _state(leerie, tmp_path)
    calls = _patch_auditor(leerie, monkeypatch, "CRASH")
    scope = asyncio.run(leerie.phase_defect_scope_audit(
        "fix the bug", st, _caps(leerie), MODELS, EFFORTS))
    assert scope == {"applicable": False}
    assert len(calls) == 3, "bounded retry, then degrade — never die"


def test_malformed_sites_are_dropped(leerie, tmp_path, monkeypatch):
    _patch_auditor(leerie, monkeypatch, {
        "applicable": True,
        "sites": [{"file": "src/a.py", "symbol": "f", "role": "consumer"},
                  {"file": "", "symbol": "g", "role": "consumer"},
                  {"symbol": "h", "role": "consumer"},
                  "not-a-dict"],
    })
    st = _state(leerie, tmp_path)
    scope = asyncio.run(leerie.phase_defect_scope_audit(
        "fix the bug", st, _caps(leerie), MODELS, EFFORTS))
    assert [s["file"] for s in scope["sites"]] == ["src/a.py"]
    assert scope["chokepoint"] == {"exists": False}


# === ctx delivery, through the real phase_plan =============================

def _drive_phase_plan(leerie, monkeypatch, st):
    calls: list[dict] = []

    async def fake_claude_p(**kwargs):
        calls.append(kwargs)
        return {"domain": "bug-fixing", "status": "ready", "subtasks": [],
                "confidence": {"task_understanding": 9.0,
                               "decomposition_quality": 9.0,
                               "basis": "stub"}}

    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    asyncio.run(leerie.phase_plan(
        "t", st, dict(leerie.DEFAULT_CAPS), MODELS, EFFORTS))
    assert calls, "phase_plan never reached claude_p"
    return calls


def test_scope_reaches_the_planner_prompt(leerie, tmp_path, monkeypatch):
    st = _state(leerie, tmp_path, defect_scope=dict(AUDIT))
    calls = _drive_phase_plan(leerie, monkeypatch, st)
    prompt = calls[0].get("user_prompt") or ""
    assert "collect_chain_values" in prompt, (
        "the bypass site — historically the missed one — must reach "
        "the planner")
    assert "resolve_identity_key" in prompt
    assert '"defect_scope"' in prompt


@pytest.mark.parametrize("scope", [
    None,
    {"applicable": False},
    {"applicable": True, "sites": []},
])
def test_inapplicable_scope_is_omitted(leerie, tmp_path, monkeypatch,
                                       scope):
    overrides = {} if scope is None else {"defect_scope": scope}
    st = _state(leerie, tmp_path, **overrides)
    calls = _drive_phase_plan(leerie, monkeypatch, st)
    prompt = calls[0].get("user_prompt") or ""
    assert "defect_scope" not in prompt


# === the mechanical floor ==================================================

def _plans_touching(*files):
    return [{"domain": "bug-fixing", "subtasks": [
        {"id": "bugfix-001", "files_likely_touched": list(files)}]}]


def test_uncovered_site_warns_with_file_and_symbol(leerie, monkeypatch):
    lines: list[str] = []
    monkeypatch.setattr(leerie, "log", lines.append)
    leerie._warn_defect_sites_uncovered(
        _plans_touching("src/other.py"), AUDIT)
    joined = "\n".join(lines)
    assert "src/example_module.py" in joined
    assert "collect_chain_values" in joined
    assert "bypass" in joined


def test_covered_sites_are_silent(leerie, monkeypatch):
    lines: list[str] = []
    monkeypatch.setattr(leerie, "log", lines.append)
    leerie._warn_defect_sites_uncovered(
        _plans_touching("./src/example_module.py"), AUDIT)
    assert lines == [], "dot-prefixed paths must normalize, not warn"


@pytest.mark.parametrize("scope", [
    {}, {"applicable": False, "sites": AUDIT["sites"]},
    {"applicable": True, "sites": []},
])
def test_inapplicable_scope_never_warns(leerie, monkeypatch, scope):
    lines: list[str] = []
    monkeypatch.setattr(leerie, "log", lines.append)
    leerie._warn_defect_sites_uncovered(_plans_touching(), scope)
    assert lines == []


# === wiring pins ===========================================================

class TestWiring:
    def test_run_phases_checkpoints_the_audit(self, leerie):
        src = inspect.getsource(leerie._run_phases)
        assert 'if "defect_scope" not in st.data:' in src
        assert "phase_defect_scope_audit(" in src
        # After the registry, before planning.
        assert (src.index("phase_artifact_registry(")
                < src.index("phase_defect_scope_audit(")
                < src.index("phase_plan("))

    def test_schedule_calls_the_floor(self, leerie):
        src = inspect.getsource(leerie._run_phases)
        assert "_warn_defect_sites_uncovered(" in src

    def test_planner_prompt_documents_the_key(self, leerie):
        text = leerie._load_prompt("planner")
        assert "defect_scope" in text
        assert "chokepoint" in text

    def test_worker_registered(self, leerie):
        assert "defect_scope_auditor" in leerie.WORKER_TYPES
        assert "defect_scope_auditor" in leerie.PLANNING_WORKER_TYPES
        assert "defect_scope_auditor" not in leerie.MODEL_DEFAULT_PER_WORKER
        assert (leerie.EFFORT_DEFAULT_PER_WORKER["defect_scope_auditor"]
                == "medium")
        assert "defect_scope_auditor" not in leerie.TIMEOUT_DEFAULT_PER_WORKER
        assert "defect_scope_auditor" in leerie.SCHEMAS
