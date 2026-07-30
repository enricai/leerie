"""Tests for phase_planning_coverage_gate (feat-003): the whole-plan
task-coverage gate that replaces the planner's self-graded
`task_understanding` confidence axis with an independent
`task_coverage_judge` verdict, re-driving `phase_plan` on a found gap
(bounded by `judgment_check_rounds`) and die()ing on exhaustion — modeled
directly on tests/test_phase_adherence_gate.py's wiring-pin + behavioral
discipline.

Two tiers:

1. Source-coupling wiring pins (`TestWiring*`) — the seams only verifiable
   by source inspection: the gate calls claude_p with
   schema_key='task_coverage_judge' inside a _run_checked_loop, a
   non-empty coverage_gaps result re-invokes phase_plan, a WorkerError
   never discards the plan, and the call site precedes schedule()/
   validate_plan in _run_phases.
2. Behavioral integration tests with a stubbed `claude_p` and a stubbed
   `phase_plan`.

Also pins that check_planner_output no longer gates on the planner's own
`task_understanding` confidence axis (grep-level assertion), while the
`confidence` object itself stays schema-required and unchanged.
"""
from __future__ import annotations

import asyncio
import inspect

import pytest


# ===========================================================================
# Helpers
# ===========================================================================

def _subtask(sid: str, *, title: str = "", intent: str = "",
             scs: str = "") -> dict:
    return {
        "id": sid,
        "title": title or f"Subtask {sid}",
        "intent": intent or f"intent for {sid}",
        "success_criteria_seed": scs or f"{sid} succeeds",
        "files_likely_touched": [],
        "provides": [],
        "requires": [],
        "depends_on": [],
        "size": "small",
    }


def _plan(domain: str, *subtasks) -> dict:
    return {"domain": domain, "status": "ready", "subtasks": list(subtasks)}


def _minimal_state(leerie, tmp_path, run_id="test-coverage-gate-aaa111"):
    leerie_root = tmp_path / ".leerie"
    (leerie_root / "runs" / run_id).mkdir(parents=True)
    st = leerie.State(leerie_root, run_id)
    st.data = {"task": "test", "worker_count": 0}
    st.save()
    return st


def _caps(leerie):
    caps = dict(leerie.DEFAULT_CAPS)
    caps["judgment_check_rounds"] = 3
    return caps


MODELS = {"task_coverage_judge": "sonnet"}
EFFORTS = {"task_coverage_judge": "medium"}


# ===========================================================================
# 1. Source-coupling wiring pins
# ===========================================================================

class TestWiringCallsJudge:
    def test_calls_claude_p_with_task_coverage_judge_schema(self, leerie):
        src = inspect.getsource(leerie.phase_planning_coverage_gate)
        assert 'schema_key="task_coverage_judge"' in src, (
            "phase_planning_coverage_gate must invoke claude_p with "
            "schema_key='task_coverage_judge'"
        )

    def test_uses_run_checked_loop(self, leerie):
        src = inspect.getsource(leerie.phase_planning_coverage_gate)
        assert "_run_checked_loop(" in src, (
            "phase_planning_coverage_gate must reuse the existing "
            "_run_checked_loop retry mechanism"
        )


class TestWiringGapRoutesThroughRetry:
    def test_feedback_reinvokes_phase_plan(self, leerie):
        src = inspect.getsource(leerie.phase_planning_coverage_gate)
        assert "await phase_plan(" in src, (
            "phase_planning_coverage_gate's feedback callback must "
            "re-invoke phase_plan to actually re-plan on a coverage gap"
        )

    def test_dies_on_exhaustion(self, leerie):
        src = inspect.getsource(leerie.phase_planning_coverage_gate)
        assert "die(" in src, (
            "phase_planning_coverage_gate must die() when the retry loop "
            "is exhausted without producing a covering plan"
        )

    def test_bounded_by_judgment_check_rounds(self, leerie):
        src = inspect.getsource(leerie.phase_planning_coverage_gate)
        assert 'caps["judgment_check_rounds"]' in src, (
            "phase_planning_coverage_gate must bound its retry loop by "
            "judgment_check_rounds"
        )


class TestWiringWorkerErrorDegrades:
    def test_handles_judge_result_none_without_dying(self, leerie):
        src = inspect.getsource(leerie.phase_planning_coverage_gate)
        assert "if judge_result is None:" in src, (
            "phase_planning_coverage_gate must handle a fully-exhausted "
            "judge loop (judge_result is None) as a distinct branch"
        )
        none_idx = src.find("if judge_result is None:")
        next_idx = src.find("remaining_issues = _check_coverage(", none_idx)
        assert next_idx != -1
        none_branch = src[none_idx:next_idx]
        assert "return cur_plans[0]" in none_branch, (
            "the judge_result is None branch must return the plan "
            "(never discard it)"
        )


class TestWiringPrecedesScheduleAndValidatePlan:
    def test_run_phases_calls_phase_planning_coverage_gate(self, leerie):
        src = inspect.getsource(leerie._run_phases)
        assert "phase_planning_coverage_gate(" in src, (
            "_run_phases() must call phase_planning_coverage_gate"
        )

    def test_coverage_gate_follows_adherence_gate(self, leerie):
        src = inspect.getsource(leerie._run_phases)
        adherence_idx = src.find("phase_adherence_gate(")
        assert adherence_idx != -1
        gate_idx = src.find("phase_planning_coverage_gate(", adherence_idx)
        assert gate_idx != -1, (
            "phase_planning_coverage_gate must be called AFTER "
            "phase_adherence_gate in _run_phases's source order"
        )

    def test_coverage_gate_precedes_schedule(self, leerie):
        src = inspect.getsource(leerie._run_phases)
        gate_idx = src.find("phase_planning_coverage_gate(")
        assert gate_idx != -1
        schedule_idx = src.find("schedule(plans)", gate_idx)
        assert schedule_idx != -1, (
            "phase_planning_coverage_gate must be called BEFORE "
            "schedule(plans) in _run_phases's source order"
        )

    def test_coverage_gate_precedes_validate_plan(self, leerie):
        src = inspect.getsource(leerie._run_phases)
        gate_idx = src.find("phase_planning_coverage_gate(")
        assert gate_idx != -1
        validate_idx = src.find("validate_plan(subtasks)", gate_idx)
        assert validate_idx != -1, (
            "phase_planning_coverage_gate must be called BEFORE "
            "validate_plan in _run_phases's source order"
        )

    def test_plans_reassigned_from_coverage_gate_call(self, leerie):
        src = inspect.getsource(leerie._run_phases)
        assert "plans = await phase_planning_coverage_gate(" in src, (
            "_run_phases must reassign `plans` from "
            "phase_planning_coverage_gate's return value"
        )


class TestWiringStateFields:
    def test_plans_after_coverage_gate_in_state_fields(self, leerie):
        assert "plans_after_coverage_gate" in leerie.STATE_FIELDS


# ===========================================================================
# 2. Behavioral integration tests (stubbed claude_p + stubbed phase_plan)
# ===========================================================================

def test_clean_result_passes_plans_unchanged(leerie, monkeypatch, tmp_path):
    st = _minimal_state(leerie, tmp_path)
    plans = [_plan("feature-implementation", _subtask("feat-001"))]

    calls = []

    async def fake_claude_p(**kwargs):
        calls.append(kwargs)
        return {
            "task_covered": True,
            "coverage_gaps": [],
            "rationale": "every required piece of work is covered",
        }

    async def fake_phase_plan(*args, **kwargs):
        pytest.fail("phase_plan must not be re-invoked on a clean gate")

    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    monkeypatch.setattr(leerie, "phase_plan", fake_phase_plan)

    result = asyncio.run(leerie.phase_planning_coverage_gate(
        plans, "task", st, _caps(leerie), MODELS, EFFORTS))

    assert len(calls) == 1
    assert result == plans
    assert st.data["coverage_gate"]["task_covered"] is True


def test_coverage_gap_triggers_replan_then_converges(
    leerie, monkeypatch, tmp_path
):
    st = _minimal_state(leerie, tmp_path)
    bad_plans = [_plan("feature-implementation", _subtask("feat-001"))]
    good_plans = [_plan(
        "feature-implementation",
        _subtask("feat-001"),
        _subtask("test-001", title="regression test"),
    )]

    judge_calls = []

    async def fake_claude_p(**kwargs):
        judge_calls.append(kwargs)
        if len(judge_calls) == 1:
            return {
                "task_covered": False,
                "coverage_gaps": [{
                    "kind": "missing_work",
                    "description": "no subtask covers the regression test",
                    "concrete_evidence": "task text requires a regression "
                                          "test; subtask set has none",
                }],
                "rationale": "missing the explicitly-requested test work",
            }
        return {
            "task_covered": True,
            "coverage_gaps": [],
            "rationale": "now covers the test work",
        }

    replan_calls = []

    async def fake_phase_plan(task, st_, caps, models, efforts):
        replan_calls.append(task)
        return good_plans

    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    monkeypatch.setattr(leerie, "phase_plan", fake_phase_plan)

    result = asyncio.run(leerie.phase_planning_coverage_gate(
        bad_plans, "task", st, _caps(leerie), MODELS, EFFORTS))

    assert len(judge_calls) == 2, "expected initial call + 1 retry"
    assert len(replan_calls) == 1, "expected exactly one re-plan"
    assert "regression test" in replan_calls[0]
    assert result == good_plans


def test_vague_gap_does_not_gate(leerie, monkeypatch, tmp_path):
    """Anti-gaming: a coverage_gaps entry missing description or
    concrete_evidence is dropped and must not trigger a re-plan."""
    st = _minimal_state(leerie, tmp_path)
    plans = [_plan("feature-implementation", _subtask("feat-001"))]

    async def fake_claude_p(**kwargs):
        return {
            "task_covered": False,
            "coverage_gaps": [{
                "kind": "missing_work",
                "description": "",
                "concrete_evidence": "",
            }],
            "rationale": "vague, unsubstantiated claim",
        }

    async def fake_phase_plan(*args, **kwargs):
        pytest.fail("phase_plan must not be re-invoked on a vague gap")

    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    monkeypatch.setattr(leerie, "phase_plan", fake_phase_plan)

    result = asyncio.run(leerie.phase_planning_coverage_gate(
        plans, "task", st, _caps(leerie), MODELS, EFFORTS))

    assert result == plans


def test_exhaustion_dies(leerie, monkeypatch, tmp_path, capsys):
    st = _minimal_state(leerie, tmp_path)
    plans = [_plan("feature-implementation", _subtask("feat-001"))]

    async def fake_claude_p(**kwargs):
        return {
            "task_covered": False,
            "coverage_gaps": [{
                "kind": "missing_work",
                "description": "still missing the test work",
                "concrete_evidence": "task text requires it; still absent",
            }],
            "rationale": "still not covered",
        }

    async def fake_phase_plan(task, st_, caps, models, efforts):
        return plans  # never fixes it

    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    monkeypatch.setattr(leerie, "phase_plan", fake_phase_plan)

    with pytest.raises(SystemExit):
        asyncio.run(leerie.phase_planning_coverage_gate(
            plans, "task", st, _caps(leerie), MODELS, EFFORTS))

    captured = capsys.readouterr()
    assert "task-coverage gate" in captured.err


def test_worker_error_every_round_degrades(leerie, monkeypatch, tmp_path):
    st = _minimal_state(leerie, tmp_path)
    plans = [_plan("feature-implementation", _subtask("feat-001"))]

    async def fake_claude_p(**kwargs):
        raise leerie.WorkerError("task_coverage_judge crashed")

    async def fake_phase_plan(*args, **kwargs):
        pytest.fail("phase_plan must not be invoked when the judge only "
                     "ever crashes")

    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    monkeypatch.setattr(leerie, "phase_plan", fake_phase_plan)

    result = asyncio.run(leerie.phase_planning_coverage_gate(
        plans, "task", st, _caps(leerie), MODELS, EFFORTS))

    assert result == plans


# ===========================================================================
# 3. check_planner_output no longer self-gates on task_understanding
# ===========================================================================

def test_check_planner_output_source_has_no_confidence_issues_call(leerie):
    src = inspect.getsource(leerie.check_planner_output)
    assert "_confidence_issues" not in src, (
        "check_planner_output must no longer call _confidence_issues — "
        "the independent task_coverage_judge (phase_planning_coverage_gate) "
        "is now the sole coverage gate"
    )


def test_check_planner_output_ignores_low_task_understanding_confidence(
    leerie, tmp_path
):
    """A planner result with a rock-bottom task_understanding confidence
    must produce no issues from that axis alone — the self-score is
    advisory only now."""
    result = {
        "domain": "feature-implementation",
        "status": "ready",
        "subtasks": [{
            "id": "feat-001",
            "title": "t",
            "success_criteria_seed": "works",
            "files_likely_touched": [],
            "depends_on": [],
            "size": "small",
        }],
        "confidence": {
            "task_understanding": 1.0,
            "decomposition_quality": 1.0,
            "basis": "low confidence on purpose",
            "falsifiers_tested": [],
            "contradictions_reconciled": [],
            "gap_to_close": {},
        },
    }
    issues = leerie.check_planner_output(result, tmp_path, "feature-implementation")
    assert not any("task_understanding" in i or "CONFIDENCE" in i
                   for i in issues), (
        "a low task_understanding confidence score must not surface as a "
        "gating issue from check_planner_output"
    )
