"""Tests for phase_wiring_gate (DESIGN §5 *A wiring re-check on the
fully-merged plan*, §8): the SEMANTIC plan-wiring gate that runs an
independent wiring_judge, gates on a non-empty wiring_defects array, and
re-drives phase_reconcile via _run_checked_loop. (The deterministic
structural counterpart check_plan_wiring is tested in
test_check_plan_wiring.py.)
"""
from __future__ import annotations

import asyncio
import inspect

import pytest


def _state(leerie, tmp_path, run_id="test-wiring-gate-aaa"):
    leerie_root = tmp_path / ".leerie"
    (leerie_root / "runs" / run_id).mkdir(parents=True)
    st = leerie.State(leerie_root, run_id)
    st.data = {"task": "test", "worker_count": 0, "dropped_subtasks": {}}
    st.save()
    return st


def _caps(leerie):
    caps = dict(leerie.DEFAULT_CAPS)
    caps["judgment_check_rounds"] = 3
    return caps


MODELS = {"wiring_judge": "opus"}
EFFORTS = {"wiring_judge": "medium"}

_PLANS = [{"domain": "feat", "status": "ready", "subtasks": [
    {"id": "feat-001", "provides": ["schema"], "requires": [],
     "depends_on": [], "intent": "write schema", "title": "s",
     "files_likely_touched": []},
    {"id": "feat-002", "provides": [], "requires": [], "depends_on": [],
     "intent": "read schema", "title": "r", "files_likely_touched": []},
]}]


class TestWiring:
    def test_invokes_wiring_judge(self, leerie):
        src = inspect.getsource(leerie.phase_wiring_gate)
        assert 'schema_key="wiring_judge"' in src

    def test_uses_run_checked_loop(self, leerie):
        src = inspect.getsource(leerie.phase_wiring_gate)
        assert "_run_checked_loop(" in src

    def test_is_detect_and_die_no_re_drive(self, leerie):
        """The wiring gate is a single detect-and-die pass — it must NOT
        re-drive phase_reconcile (the reconciler can't invent a missing edge),
        i.e. it passes no make_feedback_prompt to _run_checked_loop."""
        src = inspect.getsource(leerie.phase_wiring_gate)
        assert "await phase_reconcile(" not in src
        assert "make_feedback_prompt=" not in src

    def test_dies_on_defect(self, leerie):
        src = inspect.getsource(leerie.phase_wiring_gate)
        assert "die(" in src

    def test_die_message_does_not_recommend_skip_overlap_judge(self, leerie):
        """The wiring gate is a distinct, later gate than the overlap judge;
        --skip-overlap-judge does NOT bypass it (there is no skip guard in
        phase_wiring_gate). The die() message must not advise it as a bypass —
        an operator who follows that advice re-runs and re-dies on the same
        defect. It may still name the flag to explain what it does NOT do."""
        src = inspect.getsource(leerie.phase_wiring_gate)
        # The die() text must not present --skip-overlap-judge as a way to
        # "bypass" this gate. Assert the retired bypass phrasing is gone.
        assert "to bypass reconciliation gates" not in src
        # And the message must state this gate has no bypass flag.
        assert "no bypass flag" in src

    def test_deterministic_check_and_gate_both_in_run_phases(self, leerie):
        src = inspect.getsource(leerie._run_phases)
        assert "await phase_wiring_gate(" in src
        assert "check_plan_wiring(" in src
        # The deterministic check runs before validate_plan.
        i_check = src.index("check_plan_wiring(")
        i_validate = src.index("validate_plan(subtasks)")
        assert i_check < i_validate

    def test_wiring_gate_runs_after_the_drop_filters(self, leerie):
        """Regression pin (post-merge Finding C): the LLM wiring_judge must run
        on the POST-DROP plan — after both soft-drop filters and schedule(),
        and before validate_plan. It reads `dropped_subtasks` (populated by the
        filters) for its broken_by_drop / broken_by_merge reasoning and is told
        the plan is 'post-drop', so a pre-filter placement feeds it a plan that
        still contains to-be-dropped subtasks + an incomplete drop audit (the
        bug shipped in PR #117 and preserved through the #116 rebase — no test
        guarded the placement).
        """
        src = inspect.getsource(leerie._run_phases)
        i_offtree = src.index("filter_offtree_subtasks(")
        i_satisfied = src.index("await filter_satisfied_subtasks(")
        i_schedule = src.index("schedule(plans)")
        i_gate = src.index("await phase_wiring_gate(")
        i_validate = src.index("validate_plan(subtasks)")
        # Both drop filters + schedule() precede the gate; the gate precedes
        # validate_plan (IMPLEMENTATION.md "3 Schedule" sequence + the
        # phase_wiring_gate docstring + DESIGN §5).
        assert i_offtree < i_gate
        assert i_satisfied < i_gate
        assert i_schedule < i_gate
        assert i_gate < i_validate
        # Exactly one call site — a rebase must not duplicate or leave a stale
        # pre-filter copy.
        assert src.count("await phase_wiring_gate(") == 1

    def test_wiring_gate_is_not_re_invoked_on_budget_check_resume(self, leerie):
        """The LLM gate is expensive, so it lives INSIDE the fresh
        `if "plan_snapshot" not in st.data:` branch — a budget-check resume
        (plan_snapshot already persisted) rehydrates in the `else:` and must not
        re-invoke it. Pin by source order: the gate call sits between the
        `st.data["plan_snapshot"] = ` write and the `else:` of that if."""
        src = inspect.getsource(leerie._run_phases)
        i_snapshot_write = src.index('st.data["plan_snapshot"] = ')
        i_gate = src.index("await phase_wiring_gate(")
        # The gate is after the snapshot write (same fresh branch).
        assert i_snapshot_write < i_gate
        # And before the budget-check-resume `else` rehydration.
        i_rehydrate = src.index('snap = st.data["plan_snapshot"]')
        assert i_gate < i_rehydrate


def test_clean_wiring_passes(leerie, tmp_path, monkeypatch):
    st = _state(leerie, tmp_path)

    async def fake_claude_p(**kwargs):
        return {"plan_reviewed": True, "wiring_defects": [],
                "rationale": "wired"}

    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    out = asyncio.run(leerie.phase_wiring_gate(
        _PLANS, "task", st, _caps(leerie), MODELS, EFFORTS))
    assert st.data.get("wiring_gate") is not None
    assert out == _PLANS


def test_defect_dies(leerie, tmp_path, monkeypatch):
    """A concrete wiring defect die()s immediately (detect-and-die, no
    re-drive)."""
    st = _state(leerie, tmp_path)

    async def fake_claude_p(**kwargs):
        return {"plan_reviewed": True, "wiring_defects": [{
            "kind": "missing_requires", "sid": "feat-002",
            "tag_or_dep": "schema",
            "concrete_reason": "reads the schema but declares no requires",
        }], "rationale": "missing edge"}

    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    with pytest.raises(SystemExit):
        asyncio.run(leerie.phase_wiring_gate(
            _PLANS, "task", st, _caps(leerie), MODELS, EFFORTS))


def test_vague_defect_does_not_gate(leerie, tmp_path, monkeypatch):
    """A defect missing concrete_reason/tag_or_dep is dropped and must NOT
    die."""
    st = _state(leerie, tmp_path)

    async def fake_claude_p(**kwargs):
        return {"plan_reviewed": True, "wiring_defects": [{
            "kind": "missing_requires", "sid": "feat-002",
            "tag_or_dep": "",  # vague → dropped
            "concrete_reason": "",
        }], "rationale": "hand-wave"}

    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    out = asyncio.run(leerie.phase_wiring_gate(
        _PLANS, "task", st, _caps(leerie), MODELS, EFFORTS))
    assert out == _PLANS


def test_judge_crash_degrades(leerie, tmp_path, monkeypatch):
    st = _state(leerie, tmp_path)

    async def fake_claude_p(**kwargs):
        raise leerie.WorkerError("crash")

    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    out = asyncio.run(leerie.phase_wiring_gate(
        _PLANS, "task", st, _caps(leerie), MODELS, EFFORTS))
    assert out == _PLANS
