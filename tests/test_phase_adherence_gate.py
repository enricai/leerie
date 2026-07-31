"""Tests for phase_adherence_gate (feat-008): the whole-plan instruction-
adherence gate that runs the deterministic prescribed-command-coverage
floor plus the adherence_judge, fires only on the two-stage
`is_prescribed=true AND (floor violation OR low adherence)` composition,
and re-drives planning via the existing `_run_checked_loop` feedback path.

Two tiers, mirroring the discipline used elsewhere in this suite
(e.g. tests/test_dep_capture_wiring.py + tests/test_reconciler_cycle_gate.py):

1. Source-coupling wiring pins (`TestWiring*`) — the seams that are only
   verifiable by source inspection: the phase runs floor+judge, a low
   result routes through the retry path, a WorkerError never discards the
   plan, and the call site precedes schedule()/validate_plan.
2. Behavioral integration tests with a stubbed `claude_p` and a stubbed
   `phase_plan` (the re-plan action itself is a full planning phase —
   too heavy to run for real in a unit test, so it is monkeypatched to a
   fake that returns canned plans, exactly like other phase-boundary
   tests in this suite stub their downstream collaborators).
"""
from __future__ import annotations

import asyncio
import inspect

import pytest


# ===========================================================================
# Helpers
# ===========================================================================

def _subtask(sid: str, *, runs_commands=(), title: str = "", intent: str = "",
             scs: str = "") -> dict:
    return {
        "id": sid,
        "title": title or f"Subtask {sid}",
        "intent": intent or f"intent for {sid}",
        "success_criteria_seed": scs or f"{sid} succeeds",
        "runs_commands": list(runs_commands),
        "files_likely_touched": [],
        "provides": [],
        "requires": [],
        "depends_on": [],
        "size": "small",
    }


def _plan(domain: str, *subtasks) -> dict:
    return {"domain": domain, "status": "ready", "subtasks": list(subtasks)}


def _minimal_state(leerie, tmp_path, run_id="test-adherence-gate-aaa111"):
    leerie_root = tmp_path / ".leerie"
    (leerie_root / "runs" / run_id).mkdir(parents=True)
    st = leerie.State(leerie_root, run_id)
    st.data = {"task": "test", "worker_count": 0}
    st.save()
    return st


def _prescribed(commands, is_prescribed=True, forbid_manual=True,
                 evidence="task said so"):
    return {
        "is_prescribed": is_prescribed,
        "commands": list(commands),
        "forbid_manual": forbid_manual,
        "evidence": evidence,
    }


def _caps(leerie):
    caps = dict(leerie.DEFAULT_CAPS)
    caps["judgment_check_rounds"] = 3
    return caps


MODELS = {"adherence_judge": "opus"}
EFFORTS = {"adherence_judge": "high"}


# ===========================================================================
# 1. Source-coupling wiring pins
# ===========================================================================

class TestWiringFloorAndJudgeBothRun:
    """The gate must run BOTH the deterministic floor
    (check_prescribed_command_coverage) and the adherence_judge
    worker — the two-stage composition is the corpus-validated (0/21 FP)
    design; gating on the judge alone reintroduces the ~12% false-positive
    rate measured in isolation."""

    def test_calls_check_prescribed_command_coverage(self, leerie):
        src = inspect.getsource(leerie.phase_adherence_gate)
        assert "check_prescribed_command_coverage(" in src, (
            "phase_adherence_gate must call the deterministic floor "
            "check_prescribed_command_coverage — the PRIMARY, "
            "model-independent enforcement layer"
        )

    def test_calls_claude_p_with_adherence_judge_schema(self, leerie):
        src = inspect.getsource(leerie.phase_adherence_gate)
        assert 'schema_key="adherence_judge"' in src, (
            "phase_adherence_gate must invoke claude_p with "
            "schema_key='adherence_judge' — the semantic-layer judge"
        )

    def test_short_circuits_on_skip_flag(self, leerie):
        src = inspect.getsource(leerie.phase_adherence_gate)
        assert 'st.data.get("skip_adherence_check")' in src, (
            "phase_adherence_gate must short-circuit on "
            "st.data['skip_adherence_check']"
        )

    def test_short_circuits_when_not_prescribed(self, leerie):
        src = inspect.getsource(leerie.phase_adherence_gate)
        assert 'prescribed_procedure.get("is_prescribed")' in src, (
            "phase_adherence_gate must short-circuit when "
            "prescribed_procedure.is_prescribed is falsy — the goal-only "
            "~90% common case must never pay for a judge call"
        )


class TestWiringLowResultRoutesThroughRetry:
    """A low adherence verdict (or a floor violation) must route through
    the existing _run_checked_loop retry path, not a bespoke ad hoc loop."""

    def test_uses_run_checked_loop(self, leerie):
        src = inspect.getsource(leerie.phase_adherence_gate)
        assert "_run_checked_loop(" in src, (
            "phase_adherence_gate must reuse the existing "
            "_run_checked_loop retry mechanism — no new pause/resume "
            "machinery per the design"
        )

    def test_feedback_reinvokes_phase_plan(self, leerie):
        """The re-plan action IS the feedback: the make_feedback_prompt
        callback must re-invoke phase_plan so the retry actually produces
        a new plan, not just a new judge call over the same one."""
        src = inspect.getsource(leerie.phase_adherence_gate)
        assert "await phase_plan(" in src, (
            "phase_adherence_gate's feedback callback must re-invoke "
            "phase_plan to actually re-plan on a violation"
        )

    def test_feedback_reconciles_after_replan(self, leerie):
        src = inspect.getsource(leerie.phase_adherence_gate)
        phase_plan_idx = src.index("await phase_plan(")
        reconcile_idx = src.index("await phase_reconcile(", phase_plan_idx)
        assert reconcile_idx > phase_plan_idx, (
            "phase_adherence_gate's feedback callback must re-invoke "
            "phase_reconcile AFTER phase_plan to bridge any cross-domain "
            "tag drift the re-plan introduced, before the re-planned "
            "output reaches the next judge round"
        )

    def test_dies_on_exhaustion(self, leerie):
        src = inspect.getsource(leerie.phase_adherence_gate)
        assert "die(" in src, (
            "phase_adherence_gate must die() when the retry loop is "
            "exhausted without producing a clean plan, matching every "
            "other exhausted planner-adjacent gate (reconciler, "
            "overlap-judge)"
        )

    def test_bounded_by_judgment_check_rounds(self, leerie):
        src = inspect.getsource(leerie.phase_adherence_gate)
        assert 'caps["judgment_check_rounds"]' in src, (
            "phase_adherence_gate must bound its retry loop by "
            "judgment_check_rounds, mirroring every other _run_checked_loop "
            "caller at plan altitude (reconciler, overlap-judge)"
        )


class TestWiringWorkerErrorDegrades:
    """adherence_judge WorkerError must never discard the assembled
    plan — it degrades to the floor's own (still model-independent)
    verdict, per the design's explicit 'WorkerError⇒degrade' requirement."""

    def test_handles_judge_result_none_without_dying_unconditionally(self, leerie):
        src = inspect.getsource(leerie.phase_adherence_gate)
        # The None-result branch must exist and must NOT die() unconditionally
        # — it must first re-check the floor and only die() if the floor
        # itself still finds violations (i.e. it degrades, it doesn't just
        # crash the run because the judge crashed).
        assert "if judge_result is None:" in src, (
            "phase_adherence_gate must handle a fully-exhausted judge "
            "loop (judge_result is None) as a distinct, non-fatal branch"
        )
        none_branch_idx = src.find("if judge_result is None:")
        next_branch_idx = src.find("remaining_issues = _check_adherence(",
                                    none_branch_idx)
        assert next_branch_idx != -1
        none_branch = src[none_branch_idx:next_branch_idx]
        assert "return cur_plans[0]" in none_branch, (
            "the judge_result is None branch must return the plan "
            "(possibly re-planned) rather than discarding it"
        )


class TestWiringPrecedesScheduleAndValidatePlan:
    """The phase must run between phase_overlap_judge and schedule()/
    validate_plan in _run_phases — re-planning after scheduling or
    validation would rebuild an already-scheduled DAG."""

    def test_orchestrate_calls_phase_adherence_gate(self, leerie):
        src = inspect.getsource(leerie._run_phases)
        assert "phase_adherence_gate(" in src, (
            "_run_phases() must call phase_adherence_gate"
        )

    def test_adherence_gate_follows_overlap_judge(self, leerie):
        src = inspect.getsource(leerie._run_phases)
        overlap_idx = src.find("phase_overlap_judge(")
        assert overlap_idx != -1, "orchestrate must call phase_overlap_judge"
        gate_idx = src.find("phase_adherence_gate(", overlap_idx)
        assert gate_idx != -1, (
            "phase_adherence_gate must be called AFTER phase_overlap_judge "
            "in _run_phases's source order"
        )

    def test_adherence_gate_precedes_schedule(self, leerie):
        src = inspect.getsource(leerie._run_phases)
        gate_idx = src.find("phase_adherence_gate(")
        assert gate_idx != -1
        schedule_idx = src.find("schedule(plans)", gate_idx)
        assert schedule_idx != -1, (
            "phase_adherence_gate must be called BEFORE schedule(plans) "
            "in _run_phases's source order — a re-plan must never rebuild "
            "an already-scheduled DAG"
        )

    def test_adherence_gate_precedes_validate_plan(self, leerie):
        src = inspect.getsource(leerie._run_phases)
        gate_idx = src.find("phase_adherence_gate(")
        assert gate_idx != -1
        validate_idx = src.find("validate_plan(subtasks)", gate_idx)
        assert validate_idx != -1, (
            "phase_adherence_gate must be called BEFORE validate_plan "
            "in _run_phases's source order"
        )

    def test_plans_reassigned_from_adherence_gate_call(self, leerie):
        src = inspect.getsource(leerie._run_phases)
        assert "plans = await phase_adherence_gate(" in src, (
            "_run_phases must reassign `plans` from phase_adherence_gate's "
            "return value (it may return a re-planned list)"
        )


# ===========================================================================
# 2. Behavioral integration tests (stubbed claude_p + stubbed phase_plan)
# ===========================================================================

def test_short_circuits_on_skip_flag_returns_plans_unchanged(leerie, tmp_path):
    st = _minimal_state(leerie, tmp_path)
    st.data["skip_adherence_check"] = True
    st.data["prescribed_procedure"] = _prescribed(["recon generate"])
    plans = [_plan("feature-implementation", _subtask("feat-001"))]

    result = asyncio.run(leerie.phase_adherence_gate(
        plans, "task", st, _caps(leerie), MODELS, EFFORTS))
    assert result is plans


def test_short_circuits_when_not_prescribed_returns_plans_unchanged(
    leerie, tmp_path
):
    st = _minimal_state(leerie, tmp_path)
    st.data["prescribed_procedure"] = _prescribed([], is_prescribed=False)
    plans = [_plan("feature-implementation", _subtask("feat-001"))]

    result = asyncio.run(leerie.phase_adherence_gate(
        plans, "task", st, _caps(leerie), MODELS, EFFORTS))
    assert result is plans


def test_no_prescribed_procedure_key_at_all_short_circuits(leerie, tmp_path):
    """A run with no prescribed_procedure key in state at all (e.g. an
    older classifier run, or the field never populated) must short-circuit
    exactly like an explicit is_prescribed=false — never crash on a
    missing key."""
    st = _minimal_state(leerie, tmp_path)
    plans = [_plan("feature-implementation", _subtask("feat-001"))]

    result = asyncio.run(leerie.phase_adherence_gate(
        plans, "task", st, _caps(leerie), MODELS, EFFORTS))
    assert result is plans


def test_clean_plan_passes_without_replanning(leerie, monkeypatch, tmp_path):
    """Both the floor and the judge are clean on round 1 — no re-plan
    should occur (claude_p called exactly once, phase_plan never called)."""
    st = _minimal_state(leerie, tmp_path)
    st.data["prescribed_procedure"] = _prescribed(["recon generate"])
    plans = [_plan(
        "feature-implementation",
        _subtask("feat-001", runs_commands=["run the recon generate step"]),
    )]

    calls = []

    async def fake_claude_p(**kwargs):
        calls.append(kwargs)
        return {
            "user_prescribed_a_procedure": True,
            "instruction_adherence": 9.0,
            "violations": [],
            "rationale": "the plan runs the prescribed command",
        }

    async def fake_phase_plan(*args, **kwargs):
        pytest.fail("phase_plan must not be re-invoked on a clean gate")

    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    monkeypatch.setattr(leerie, "phase_plan", fake_phase_plan)

    result = asyncio.run(leerie.phase_adherence_gate(
        plans, "task", st, _caps(leerie), MODELS, EFFORTS))

    assert len(calls) == 1
    assert result == plans
    assert st.data["adherence_gate"]["floor_issues"] == []


def test_low_adherence_triggers_replan_then_converges(
    leerie, monkeypatch, tmp_path
):
    """Round 1: floor violation + low judge score. The gate must re-plan
    (via phase_plan) and re-check. Round 2's re-planned subtasks cover the
    prescribed command, so the gate converges without dying."""
    st = _minimal_state(leerie, tmp_path)
    st.data["prescribed_procedure"] = _prescribed(["recon generate"])
    bad_plans = [_plan("feature-implementation", _subtask("feat-001"))]
    good_plans = [_plan(
        "feature-implementation",
        _subtask("feat-001", runs_commands=["recon generate"]),
    )]

    judge_calls = []

    async def fake_claude_p(**kwargs):
        judge_calls.append(kwargs)
        if len(judge_calls) == 1:
            return {
                "user_prescribed_a_procedure": True,
                "instruction_adherence": 2.5,
                "violations": ["recon generate never runs"],
                "rationale": "plan substitutes manual work",
            }
        return {
            "user_prescribed_a_procedure": True,
            "instruction_adherence": 9.0,
            "violations": [],
            "rationale": "now honors the prescribed command",
        }

    replan_calls = []

    async def fake_phase_plan(task, st_, caps, models, efforts, replan_round=0):
        replan_calls.append(task)
        return good_plans

    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    monkeypatch.setattr(leerie, "phase_plan", fake_phase_plan)

    result = asyncio.run(leerie.phase_adherence_gate(
        bad_plans, "task", st, _caps(leerie), MODELS, EFFORTS))

    assert len(judge_calls) == 2, "expected initial call + 1 retry"
    assert len(replan_calls) == 1, "expected exactly one re-plan"
    assert "recon generate" in replan_calls[0]
    assert result == good_plans


def test_exhaustion_dies(leerie, monkeypatch, tmp_path, capsys):
    """Every round stays low-adherence — the loop exhausts
    judgment_check_rounds and the gate must die(), not silently proceed
    with a plan that violates the prescribed procedure."""
    st = _minimal_state(leerie, tmp_path)
    st.data["prescribed_procedure"] = _prescribed(["recon generate"])
    plans = [_plan("feature-implementation", _subtask("feat-001"))]

    async def fake_claude_p(**kwargs):
        return {
            "user_prescribed_a_procedure": True,
            "instruction_adherence": 2.0,
            "violations": ["recon generate never runs"],
            "rationale": "still violates",
        }

    async def fake_phase_plan(task, st_, caps, models, efforts, replan_round=0):
        return plans  # never fixes it

    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    monkeypatch.setattr(leerie, "phase_plan", fake_phase_plan)

    with pytest.raises(SystemExit):
        asyncio.run(leerie.phase_adherence_gate(
            plans, "task", st, _caps(leerie), MODELS, EFFORTS))

    captured = capsys.readouterr()
    assert "instruction-adherence gate" in captured.err


def test_worker_error_every_round_degrades_when_floor_clean(
    leerie, monkeypatch, tmp_path
):
    """adherence_judge crashes (WorkerError) on every round. The floor
    itself is clean (the prescribed command IS covered), so the gate must
    degrade gracefully and return the plan rather than dying — the design's
    explicit 'WorkerError⇒degrade (never discard the plan)' requirement."""
    st = _minimal_state(leerie, tmp_path)
    st.data["prescribed_procedure"] = _prescribed(["recon generate"])
    plans = [_plan(
        "feature-implementation",
        _subtask("feat-001", runs_commands=["recon generate"]),
    )]

    async def fake_claude_p(**kwargs):
        raise leerie.WorkerError("adherence_judge crashed")

    async def fake_phase_plan(*args, **kwargs):
        pytest.fail("phase_plan must not be invoked when the floor never "
                    "reports a violation")

    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    monkeypatch.setattr(leerie, "phase_plan", fake_phase_plan)

    result = asyncio.run(leerie.phase_adherence_gate(
        plans, "task", st, _caps(leerie), MODELS, EFFORTS))

    assert result == plans, (
        "a fully-crashed judge loop must degrade to the (clean) floor "
        "verdict and preserve the plan, not discard it"
    )


def test_worker_error_every_round_dies_when_floor_violates(
    leerie, monkeypatch, tmp_path, capsys
):
    """adherence_judge crashes every round AND the deterministic floor
    (model-independent) still finds a violation. The gate must still
    surface this — the floor's own verdict is reliable regardless of the
    judge's availability."""
    st = _minimal_state(leerie, tmp_path)
    st.data["prescribed_procedure"] = _prescribed(["recon generate"])
    plans = [_plan("feature-implementation", _subtask("feat-001"))]

    async def fake_claude_p(**kwargs):
        raise leerie.WorkerError("adherence_judge crashed")

    async def fake_phase_plan(*args, **kwargs):
        return plans  # re-plan doesn't fix it either

    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    monkeypatch.setattr(leerie, "phase_plan", fake_phase_plan)

    with pytest.raises(SystemExit):
        asyncio.run(leerie.phase_adherence_gate(
            plans, "task", st, _caps(leerie), MODELS, EFFORTS))

    captured = capsys.readouterr()
    assert "adherence_judge crashed on every round" in captured.err
