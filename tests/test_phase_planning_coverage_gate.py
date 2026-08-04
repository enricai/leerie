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
   never discards the plan, and the call site precedes _schedule()/
   _validate_plan in _run_phases.
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


# `plan_overlap_judge` is present because a re-plan from this gate re-runs
# phase_overlap_judge alongside phase_reconcile (DESIGN §5 *A re-plan
# invalidates every phase that already ran*), and that phase indexes
# `models`/`efforts` by its own worker name.
MODELS = {"task_coverage_judge": "sonnet", "plan_overlap_judge": "sonnet"}
EFFORTS = {"task_coverage_judge": "medium", "plan_overlap_judge": "medium"}


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

    def test_feedback_reconciles_after_replan(self, leerie):
        src = inspect.getsource(leerie.phase_planning_coverage_gate)
        phase_plan_idx = src.index("await phase_plan(")
        reconcile_idx = src.index("await phase_reconcile(", phase_plan_idx)
        assert reconcile_idx > phase_plan_idx, (
            "phase_planning_coverage_gate's feedback callback must "
            "re-invoke phase_reconcile AFTER phase_plan to bridge any "
            "cross-domain tag drift the re-plan introduced, before the "
            "re-planned output reaches the next judge round"
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
        schedule_idx = src.find("_schedule(plans)", gate_idx)
        assert schedule_idx != -1, (
            "phase_planning_coverage_gate must be called BEFORE "
            "_schedule(plans) in _run_phases's source order"
        )

    def test_coverage_gate_precedes_validate_plan(self, leerie):
        src = inspect.getsource(leerie._run_phases)
        gate_idx = src.find("phase_planning_coverage_gate(")
        assert gate_idx != -1
        validate_idx = src.find("_validate_plan(subtasks)", gate_idx)
        assert validate_idx != -1, (
            "phase_planning_coverage_gate must be called BEFORE "
            "_validate_plan in _run_phases's source order"
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

    async def fake_phase_plan(task, st_, caps, models, efforts, replan_round=0):
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


def test_replan_with_cross_category_tag_drift_gets_reconciled(
    leerie, monkeypatch, tmp_path
):
    """Regression fixture reproducing the sibling-service incident shape: a
    coverage gap triggers a re-plan, and the re-plan's two categories
    (bug-fixing / testing) invent DIFFERENT tag strings for the same
    capability — `bugfix-006` provides `events-create-contract-fixed`,
    `test-004` requires `events-payload-casing-fixed`. Without the
    post-replan phase_reconcile call, this mismatch would reach
    _schedule()/phase_wiring_gate unresolved (the exact failure that
    motivated this fix — DESIGN §5 *Bridge cross-domain capability-tag
    mismatches*). Falsify by commenting out the `phase_reconcile` call in
    phase_planning_coverage_gate's `_on_feedback` and re-running: the
    reconciler stub below is then never invoked."""
    st = _minimal_state(leerie, tmp_path)
    bad_plans = [_plan("feature-implementation", _subtask("feat-001"))]

    # The re-plan's output: two categories, tags that mean the same thing
    # but are spelled differently — exactly the shape a re-plan produces
    # when each category's planner has no visibility into the other's
    # already-declared vocabulary.
    mismatched_plans = [
        _plan(
            "bug-fixing",
            {
                "id": "bugfix-006", "title": "fix payload casing",
                "intent": "fix casing", "success_criteria_seed": "fixed",
                "files_likely_touched": [], "depends_on": [],
                "provides": ["events-create-contract-fixed"],
                "requires": [], "size": "small",
            },
        ),
        _plan(
            "testing",
            {
                "id": "test-004", "title": "test payload casing",
                "intent": "test casing", "success_criteria_seed": "tested",
                "files_likely_touched": [], "depends_on": [],
                "provides": ["events-contract-tests-updated"],
                "requires": [{
                    "tag": "events-payload-casing-fixed",
                    "extent": "in_plan", "reason": "",
                }],
                "size": "small",
            },
        ),
    ]

    judge_calls = []
    reconciler_calls = []

    async def fake_claude_p(**kwargs):
        schema_key = kwargs.get("schema_key")
        if schema_key == "task_coverage_judge":
            judge_calls.append(kwargs)
            if len(judge_calls) == 1:
                return {
                    "task_covered": False,
                    "coverage_gaps": [{
                        "kind": "missing_work",
                        "description": "payload casing fix not covered",
                        "concrete_evidence": "no subtask fixes the "
                                              "events payload casing",
                    }],
                    "rationale": "missing the casing fix",
                }
            return {
                "task_covered": True,
                "coverage_gaps": [],
                "rationale": "now covers the casing fix",
            }
        if schema_key == "reconciler":
            reconciler_calls.append(kwargs)
            # Resolve the mismatch exactly like a real reconciler would:
            # rename test-004's requires tag to the tag bugfix-006 actually
            # provides.
            return {
                "renames": [{
                    "sid": "test-004",
                    "from": "events-payload-casing-fixed",
                    "to": "events-create-contract-fixed",
                }],
                "added_provides": [], "added_subtasks": [],
                "conditional_drops": [], "dropped_requires": [],
                "dependency_edges": [], "merged_subtasks": [],
                "unresolvable": [],
                "confidence": {"score": 0.9, "reasoning": "clean rename"},
            }
        if schema_key == "plan_overlap_judge":
            # A re-plan also invalidates phase_overlap_judge, which this
            # gate re-runs alongside phase_reconcile (DESIGN §5 *A re-plan
            # invalidates every phase that already ran*). This test is
            # about the reconcile path, so wave the judge through with no
            # collisions; the overlap-judge re-run has its own coverage in
            # tests/test_replan_reruns_upstream_phases.py.
            return {
                "collisions": [],
                "confidence": {"judgment": 9.0, "basis": "",
                               "falsifiers_tested": [],
                               "contradictions_reconciled": [],
                               "gap_to_close": {}},
            }
        raise AssertionError(f"unexpected schema_key: {schema_key}")

    async def fake_phase_plan(task, st_, caps, models, efforts, replan_round=0):
        return mismatched_plans

    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    monkeypatch.setattr(leerie, "phase_plan", fake_phase_plan)

    # phase_reconcile's _spawn_reconciler indexes models["reconciler"] /
    # efforts["reconciler"] directly (not .get), so the models/efforts
    # dicts handed to phase_planning_coverage_gate must carry an entry
    # for the reconciler too, not just task_coverage_judge.
    models = {**MODELS, "reconciler": "sonnet"}
    efforts = {**EFFORTS, "reconciler": "medium"}

    result = asyncio.run(leerie.phase_planning_coverage_gate(
        bad_plans, "task", st, _caps(leerie), models, efforts))

    assert len(judge_calls) == 2, "expected initial call + 1 retry"
    assert len(reconciler_calls) == 1, (
        "phase_reconcile must run exactly once against the re-planned "
        "output, resolving the cross-category tag mismatch before the "
        "gate's next judge round"
    )

    test_004 = next(
        s for plan in result for s in plan["subtasks"]
        if s["id"] == "test-004"
    )
    assert test_004["requires"][0]["tag"] == "events-create-contract-fixed", (
        "the reconciler's rename must have been applied to the plan "
        "phase_planning_coverage_gate returns"
    )

    # The wiring invariant this whole fix protects: every requires tag
    # resolves to some subtask's provides in the final plan.
    all_provides = {
        tag for plan in result for s in plan["subtasks"]
        for tag in s.get("provides", [])
    }
    for plan in result:
        for s in plan["subtasks"]:
            for req in s.get("requires", []) or []:
                if req.get("extent") == "in_plan":
                    assert req["tag"] in all_provides, (
                        f"{s['id']} requires {req['tag']!r} but no "
                        "subtask provides it — the exact dangle "
                        "phase_wiring_gate died on in the sibling-service "
                        "incident"
                    )


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

    async def fake_phase_plan(task, st_, caps, models, efforts, replan_round=0):
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
# 2b. required_items PRIMARY floor (gap #2 close-out)
# ===========================================================================

def test_floor_clean_and_judge_clean_passes_unchanged(
        leerie, monkeypatch, tmp_path):
    st = _minimal_state(leerie, tmp_path)
    st.data["required_items"] = [{"item": "add rate limiting to the API"}]
    plans = [_plan("feature-implementation", _subtask(
        "feat-001", title="Add rate limiting to the API"))]

    async def fake_claude_p(**kwargs):
        return {"task_covered": True, "coverage_gaps": [],
                "rationale": "covered"}

    async def fake_phase_plan(*args, **kwargs):
        pytest.fail("phase_plan must not be re-invoked on a clean gate")

    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    monkeypatch.setattr(leerie, "phase_plan", fake_phase_plan)

    result = asyncio.run(leerie.phase_planning_coverage_gate(
        plans, "task", st, _caps(leerie), MODELS, EFFORTS))

    assert result == plans


def test_floor_uncovered_item_triggers_replan_even_when_judge_says_covered(
        leerie, monkeypatch, tmp_path):
    """The judge can be schema-valid and say task_covered=True while the
    deterministic floor still finds an unaddressed required item — the
    floor's whole purpose is to catch what judgment alone misses."""
    st = _minimal_state(leerie, tmp_path)
    st.data["required_items"] = [{"item": "add rate limiting to the API"}]
    bad_plans = [_plan("feature-implementation", _subtask(
        "feat-001", title="Add pagination"))]
    good_plans = [_plan("feature-implementation", _subtask(
        "feat-001", title="Add rate limiting to the API"))]

    async def fake_claude_p(**kwargs):
        # The judge itself never objects — only the floor does.
        return {"task_covered": True, "coverage_gaps": [],
                "rationale": "judge missed it"}

    replanned = [False]

    async def fake_phase_plan(task, st_, caps, models, efforts, replan_round=0):
        replanned[0] = True
        return good_plans

    async def fake_phase_reconcile(plans, *a, **k):
        return plans

    async def fake_phase_overlap_judge(plans, *a, **k):
        return plans

    async def fake_phase_adherence_gate(plans, *a, **k):
        return plans

    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    monkeypatch.setattr(leerie, "phase_plan", fake_phase_plan)
    monkeypatch.setattr(leerie, "phase_reconcile", fake_phase_reconcile)
    monkeypatch.setattr(leerie, "phase_overlap_judge", fake_phase_overlap_judge)
    monkeypatch.setattr(leerie, "phase_adherence_gate", fake_phase_adherence_gate)

    result = asyncio.run(leerie.phase_planning_coverage_gate(
        bad_plans, "task", st, _caps(leerie), MODELS, EFFORTS))

    assert replanned[0], "the floor issue must have forced a re-plan"
    assert result == good_plans


def test_floor_still_evaluated_and_dies_when_judge_crashes_every_round(
        leerie, monkeypatch, tmp_path, capsys):
    """The bug this close-out fixes: a crashed judge used to skip the
    floor entirely (`if judge_result is None: return cur_plans[0]` with
    no floor check), so a WorkerError became a way to bypass required-item
    enforcement. The floor must still be evaluated and still die()."""
    st = _minimal_state(leerie, tmp_path)
    st.data["required_items"] = [{"item": "add rate limiting to the API"}]
    plans = [_plan("feature-implementation", _subtask(
        "feat-001", title="Add pagination"))]

    async def fake_claude_p(**kwargs):
        raise leerie.WorkerError("task_coverage_judge crashed")

    async def fake_phase_plan(*args, **kwargs):
        pytest.fail("phase_plan must not be invoked when the judge only "
                     "ever crashes")

    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    monkeypatch.setattr(leerie, "phase_plan", fake_phase_plan)

    with pytest.raises(SystemExit):
        asyncio.run(leerie.phase_planning_coverage_gate(
            plans, "task", st, _caps(leerie), MODELS, EFFORTS))

    captured = capsys.readouterr()
    assert "task-coverage gate" in captured.err
    assert "rate limiting" in captured.err


def test_floor_silent_and_judge_crashes_every_round_still_degrades(
        leerie, monkeypatch, tmp_path):
    """No required_items at all (the common case) — a crashed judge must
    still degrade to the assembled plan, not die() on an empty floor."""
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


class TestWiringFloorInvoked:
    def test_source_calls_check_required_items_coverage(self, leerie):
        src = inspect.getsource(leerie.phase_planning_coverage_gate)
        assert "check_required_items_coverage(" in src

    def test_source_reads_required_items_from_state(self, leerie):
        src = inspect.getsource(leerie.phase_planning_coverage_gate)
        assert 'st.data.get("required_items")' in src


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


# ===========================================================================
# --skip-coverage-check (the operator escape hatch)
# ===========================================================================

class TestSkipCoverageCheck:
    """Run 488c42e5 died with no way through: the `task_coverage_judge`
    counted a task item the task ITSELF marked `[DESIGN FIRST]` (deferred by
    design) as `missing_work`. No planner could satisfy that without
    contradicting the task, so every re-plan was rejected again — and this
    was the only planning gate with no skip flag, unlike
    `--skip-adherence-check` / `--skip-overlap-judge` /
    `--skip-completeness-check` / `--skip-satisfied-check`."""

    def test_skip_short_circuits_with_zero_worker_calls(
        self, leerie, monkeypatch, tmp_path
    ):
        st = _minimal_state(leerie, tmp_path)
        st.data["skip_coverage_check"] = True
        plans = [_plan("feature-implementation", _subtask("feat-001"))]

        async def fail_claude_p(**kwargs):
            pytest.fail("no worker may be spawned when the gate is skipped")

        async def fail_phase_plan(*a, **k):
            pytest.fail("phase_plan must not be re-invoked when skipped")

        monkeypatch.setattr(leerie, "claude_p", fail_claude_p)
        monkeypatch.setattr(leerie, "phase_plan", fail_phase_plan)

        result = asyncio.run(leerie.phase_planning_coverage_gate(
            plans, "task", st, _caps(leerie), MODELS, EFFORTS))
        assert result is plans

    def test_skip_does_not_write_a_coverage_gate_verdict(
        self, leerie, monkeypatch, tmp_path
    ):
        """A skipped gate reached no verdict. Writing one would let a later
        resume believe the gate passed."""
        st = _minimal_state(leerie, tmp_path)
        st.data["skip_coverage_check"] = True

        async def fail_claude_p(**kwargs):
            pytest.fail("unreachable")

        monkeypatch.setattr(leerie, "claude_p", fail_claude_p)
        asyncio.run(leerie.phase_planning_coverage_gate(
            [_plan("feature-implementation", _subtask("feat-001"))],
            "task", st, _caps(leerie), MODELS, EFFORTS))
        assert "coverage_gate" not in st.data

    def test_gate_still_runs_when_flag_unset(
        self, leerie, monkeypatch, tmp_path
    ):
        """ANTI-VACUITY: the flag must not have disabled the gate outright."""
        st = _minimal_state(leerie, tmp_path)
        plans = [_plan("feature-implementation", _subtask("feat-001"))]
        calls = []

        async def fake_claude_p(**kwargs):
            calls.append(kwargs)
            return {"task_covered": True, "coverage_gaps": [],
                    "rationale": "covered"}

        monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
        monkeypatch.setattr(leerie, "phase_plan", None)
        asyncio.run(leerie.phase_planning_coverage_gate(
            plans, "task", st, _caps(leerie), MODELS, EFFORTS))
        assert len(calls) == 1, "gate must run when the flag is absent"

    def test_falsy_flag_does_not_skip(self, leerie, monkeypatch, tmp_path):
        """Explicit False must behave as absent, not as truthy-present."""
        st = _minimal_state(leerie, tmp_path)
        st.data["skip_coverage_check"] = False
        calls = []

        async def fake_claude_p(**kwargs):
            calls.append(kwargs)
            return {"task_covered": True, "coverage_gaps": [],
                    "rationale": "covered"}

        monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
        asyncio.run(leerie.phase_planning_coverage_gate(
            [_plan("feature-implementation", _subtask("feat-001"))],
            "task", st, _caps(leerie), MODELS, EFFORTS))
        assert len(calls) == 1


class TestSkipCoverageCheckWiring:
    """Source-coupling: the flag is inert unless resolved and stored."""

    def test_resolver_exists_and_follows_the_bool_pref_pattern(self, leerie):
        src = inspect.getsource(leerie.resolve_skip_coverage_check)
        assert "_resolve_bool_pref" in src
        assert "SKIP_COVERAGE_CHECK_ENV" in src
        assert 'file_key="skip_coverage_check"' in src

    def test_env_constant_matches_the_documented_name(self, leerie):
        assert leerie.SKIP_COVERAGE_CHECK_ENV == "LEERIE_SKIP_COVERAGE_CHECK"

    def test_state_field_declared(self, leerie):
        assert "skip_coverage_check" in leerie.STATE_FIELDS

    def test_short_circuit_precedes_any_work(self, leerie):
        """The guard must sit before the phase log/state write, or the gate
        records itself as having started when it did not."""
        src = inspect.getsource(leerie.phase_planning_coverage_gate)
        guard = src.index('st.data.get("skip_coverage_check")')
        logline = src.index('log("phase 2\u215e\u00bd: task-coverage gate")')
        assert guard < logline

    def test_main_resolves_the_flag(self, leerie):
        src = inspect.getsource(leerie.main)
        assert "resolve_skip_coverage_check(" in src

