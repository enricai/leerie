"""Tests for phase_classification_gate (DESIGN §8 *Independent adversarial
verification*): the classifier-coverage gate that runs an independent
classification_judge, gates on a non-empty miscategorizations array, and
re-drives phase_classify via _run_checked_loop.

Two tiers, mirroring test_phase_adherence_gate.py:
1. Source-coupling wiring pins.
2. Behavioral integration with stubbed claude_p + phase_classify.
"""
from __future__ import annotations

import asyncio
import inspect

import pytest


def _minimal_state(leerie, tmp_path, run_id="test-classification-gate-aaa"):
    leerie_root = tmp_path / ".leerie"
    (leerie_root / "runs" / run_id).mkdir(parents=True)
    st = leerie.State(leerie_root, run_id)
    st.data = {"task": "test", "worker_count": 0, "categories": ["documentation"]}
    st.save()
    return st


def _caps(leerie):
    caps = dict(leerie.DEFAULT_CAPS)
    caps["judgment_check_rounds"] = 3
    return caps


MODELS = {"classification_judge": "opus"}
EFFORTS = {"classification_judge": "medium"}


# === Tier 1: source-coupling wiring ========================================

class TestWiring:
    def test_invokes_classification_judge(self, leerie):
        src = inspect.getsource(leerie.phase_classification_gate)
        assert 'schema_key="classification_judge"' in src

    def test_uses_run_checked_loop(self, leerie):
        src = inspect.getsource(leerie.phase_classification_gate)
        assert "_run_checked_loop(" in src

    def test_re_drives_phase_classify(self, leerie):
        src = inspect.getsource(leerie.phase_classification_gate)
        assert "await phase_classify(" in src

    def test_dies_on_exhaustion(self, leerie):
        src = inspect.getsource(leerie.phase_classification_gate)
        assert "die(" in src

    def test_persists_audit_key(self, leerie):
        src = inspect.getsource(leerie.phase_classification_gate)
        assert 'classification_coverage_gate' in src

    def test_called_in_run_phases_after_classify(self, leerie):
        src = inspect.getsource(leerie._run_phases)
        assert "await phase_classification_gate(" in src
        # Ordering: classify precedes the gate precedes provision.
        i_classify = src.index("await phase_classify(")
        i_gate = src.index("await phase_classification_gate(")
        i_prov = src.index("await phase_provision(")
        assert i_classify < i_gate < i_prov


# === Tier 2: behavioral ====================================================

def test_clean_classification_passes_without_re_drive(leerie, tmp_path,
                                                      monkeypatch):
    st = _minimal_state(leerie, tmp_path)

    async def fake_claude_p(**kwargs):
        return {"categories_reviewed": ["documentation"],
                "miscategorizations": [], "rationale": "ok"}

    async def fake_phase_classify(*a, **k):
        pytest.fail("phase_classify must NOT be re-driven on a clean gate")

    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    monkeypatch.setattr(leerie, "phase_classify", fake_phase_classify)

    asyncio.run(leerie.phase_classification_gate(
        "task", st, _caps(leerie), False, MODELS, EFFORTS))
    assert st.data.get("classification_coverage_gate") is not None


def test_miscategorization_triggers_re_drive_then_converges(leerie, tmp_path,
                                                            monkeypatch):
    st = _minimal_state(leerie, tmp_path)
    calls = {"n": 0}

    async def fake_claude_p(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"categories_reviewed": ["documentation"],
                    "miscategorizations": [{
                        "kind": "missing_category",
                        "category": "feature-implementation",
                        "concrete_work_evidence": "ships a landing page",
                    }], "rationale": "misses feature"}
        return {"categories_reviewed": ["documentation",
                                        "feature-implementation"],
                "miscategorizations": [], "rationale": "ok now"}

    redrives = {"n": 0}

    async def fake_phase_classify(*a, **k):
        redrives["n"] += 1
        st.data["categories"] = ["documentation", "feature-implementation"]

    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    monkeypatch.setattr(leerie, "phase_classify", fake_phase_classify)

    asyncio.run(leerie.phase_classification_gate(
        "task", st, _caps(leerie), False, MODELS, EFFORTS))
    assert redrives["n"] == 1
    assert st.data["classification_coverage_gate"]["miscategorizations"] == []


def test_persistent_miscategorization_dies(leerie, tmp_path, monkeypatch):
    st = _minimal_state(leerie, tmp_path)

    async def fake_claude_p(**kwargs):
        return {"categories_reviewed": ["documentation"],
                "miscategorizations": [{
                    "kind": "missing_category",
                    "category": "feature-implementation",
                    "concrete_work_evidence": "ships a landing page",
                }], "rationale": "still misses"}

    async def fake_phase_classify(*a, **k):
        pass  # never fixes it

    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    monkeypatch.setattr(leerie, "phase_classify", fake_phase_classify)

    with pytest.raises(SystemExit):
        asyncio.run(leerie.phase_classification_gate(
            "task", st, _caps(leerie), False, MODELS, EFFORTS))


def test_vague_miscategorization_does_not_gate(leerie, tmp_path, monkeypatch):
    """Anti-gaming: a miscategorization missing concrete_work_evidence is
    dropped and does NOT gate."""
    st = _minimal_state(leerie, tmp_path)

    async def fake_claude_p(**kwargs):
        return {"categories_reviewed": ["documentation"],
                "miscategorizations": [{
                    "kind": "missing_category",
                    "category": "feature-implementation",
                    "concrete_work_evidence": "",  # vague
                }], "rationale": "hand-wave"}

    async def fake_phase_classify(*a, **k):
        pytest.fail("a vague miscategorization must not re-drive")

    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    monkeypatch.setattr(leerie, "phase_classify", fake_phase_classify)

    asyncio.run(leerie.phase_classification_gate(
        "task", st, _caps(leerie), False, MODELS, EFFORTS))


def test_judge_crash_degrades_without_dying(leerie, tmp_path, monkeypatch):
    st = _minimal_state(leerie, tmp_path)

    async def fake_claude_p(**kwargs):
        raise leerie.WorkerError("judge crashed")

    async def fake_phase_classify(*a, **k):
        pass

    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    monkeypatch.setattr(leerie, "phase_classify", fake_phase_classify)

    # Degrades to the classifier's own categories — no SystemExit.
    asyncio.run(leerie.phase_classification_gate(
        "task", st, _caps(leerie), False, MODELS, EFFORTS))
    assert st.data["categories"] == ["documentation"]
