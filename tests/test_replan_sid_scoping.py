"""Tests for the round-scoped planner worker sid (DESIGN §6, incident
870cf82cdcd4c3df2c860b94c4608b63f4debf4f211c99cb1a2c5517c62cb9b4).

A re-plan (phase_adherence_gate / phase_planning_coverage_gate's
_on_feedback) re-invokes phase_plan for every category in the SAME run.
Before this fix, the second invocation's plan_one workers reused the
identical bare sid (e.g. "planner-documentation") as the first
invocation — a real, verified hazard independent of whatever actually
caused the incident's cgroup-enroll ProcessLookupError. phase_plan now
takes an explicit `replan_round` (default 0) that plan_one folds into
the worker sid so a re-plan's cgroup/log sid never collides with an
earlier invocation's in the same run.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

_CATEGORY = "feature-implementation"  # a real entry in CATEGORY_ABBREV


def _make_state(leerie) -> MagicMock:
    st = MagicMock()
    st.data = {
        "categories": [_CATEGORY],
        "answers": {"source_of_truth": "codebase"},
        "current_phase": "",
        "skip_repo_map": True,
    }
    st.leerie_root = Path("/tmp/fake-leerie-root")
    st.save = MagicMock()
    st.bump_workers = MagicMock()
    return st


def _make_caps(leerie) -> dict:
    caps = dict(leerie.DEFAULT_CAPS)
    caps["max_parallel"] = 1
    caps["confidence_rounds"] = 8
    caps["planner_samples"] = 1
    caps["planner_check_rounds"] = 1
    return caps


_SUBTASK = {
    "id": "feat-001",
    "title": "Do a thing",
    "success_criteria_seed": "the thing is done",
    "files_likely_touched": ["src/f.ts"],
    "intent": "small change",
    "scope_note": "",
    "depends_on": [],
    "requires": [],
    "provides": [],
    "size": "small",
    "investigation_notes": "",
}

_PLANNER_RESPONSE = {
    "domain": _CATEGORY,
    "status": "ready",
    "confidence": {"root_cause": 9.0, "solution": 9.0, "basis": "ok",
                   "falsifiers_tested": [], "contradictions_reconciled": [],
                   "gap_to_close": {}},
    "subtasks": [_SUBTASK],
}


def _run(coro):
    return asyncio.run(coro)


def _run_phase_plan(leerie, task: str, replan_round: int = 0,
                    recursive_decompose_side_effect=None):
    """Drive phase_plan end-to-end (claude_p stubbed) and capture the sid
    passed to claude_p for the one plan_one worker that runs."""
    st = _make_state(leerie)
    caps = _make_caps(leerie)
    models = {k: leerie.MODEL_DEFAULT for k in leerie.WORKER_TYPES}
    efforts = {k: None for k in leerie.WORKER_TYPES}

    captured_sids: list[str] = []

    async def fake_claude_p(**kwargs):
        captured_sids.append(kwargs.get("sid"))
        return json.loads(json.dumps(_PLANNER_RESPONSE))

    async def fake_recursive_decompose(subtask, depth, st_, caps_, models_,
                                       efforts_, repo_root_, **kwargs):
        return [subtask]  # already a leaf

    with (
        patch.object(leerie, "load_prompt", return_value="system-prompt"),
        patch.object(leerie, "extract_task_file_structure", return_value=[]),
        patch.object(leerie, "claude_p", new=AsyncMock(side_effect=fake_claude_p)),
        patch.object(leerie, "check_planner_output", return_value=[]),
        patch.object(leerie, "check_task_file_coverage", return_value=[]),
        patch.object(leerie, "recursive_decompose",
                     new=AsyncMock(side_effect=fake_recursive_decompose)),
    ):
        plans = _run(leerie.phase_plan(
            task, st, caps, models, efforts, replan_round=replan_round))

    assert len(captured_sids) == 1
    return plans, captured_sids[0]


class TestReplanRoundScopedSid:
    def test_default_round_zero_sid_is_bare(self, leerie):
        """A fresh top-level call (replan_round=0, the default) keeps the
        pre-existing bare sid — no behavior change for the common case."""
        _, sid = _run_phase_plan(leerie, "do the task")
        assert sid == f"planner-{_CATEGORY}"

    def test_replan_round_one_sid_is_distinct(self, leerie):
        """A re-plan (replan_round=1) must NOT reuse the first invocation's
        bare sid — this is the fix itself."""
        _, sid = _run_phase_plan(leerie, "do the task", replan_round=1)
        assert sid != f"planner-{_CATEGORY}"
        assert sid == f"planner-{_CATEGORY}-r1"

    def test_replan_round_two_sid_is_distinct_from_round_one(self, leerie):
        """A second re-plan (replan_round=2) must be distinct from BOTH the
        original invocation and the first re-plan — collisions are not
        just a one-shot problem, every round needs its own identity."""
        _, sid_r1 = _run_phase_plan(leerie, "do the task", replan_round=1)
        _, sid_r2 = _run_phase_plan(leerie, "do the task", replan_round=2)
        assert sid_r1 != sid_r2
        assert sid_r2 == f"planner-{_CATEGORY}-r2"

    def test_phase_plan_default_replan_round_is_zero(self, leerie):
        """Callers that don't pass replan_round explicitly (the original
        top-level call site) must get the round-0 (bare) sid — the
        parameter must default to 0, not be mandatory."""
        import inspect
        sig = inspect.signature(leerie.phase_plan)
        assert sig.parameters["replan_round"].default == 0


class TestReplanCallSitesIncrementRound:
    """phase_adherence_gate and phase_planning_coverage_gate's _on_feedback
    must pass an incrementing replan_round on each re-plan — not a fixed
    value — so a THIRD invocation (second re-plan) doesn't collide with
    the second (first re-plan) either."""

    def test_adherence_gate_increments_round_across_retries(self, leerie, tmp_path):
        st = MagicMock()
        st.data = {}
        caps = dict(leerie.DEFAULT_CAPS)
        caps["judgment_check_rounds"] = 3
        models = {k: leerie.MODEL_DEFAULT for k in leerie.WORKER_TYPES}
        efforts = {k: None for k in leerie.WORKER_TYPES}
        plans = [{"domain": _CATEGORY, "subtasks": [dict(_SUBTASK)]}]
        prescribed = {
            "is_prescribed": True,
            "commands": ["recon generate"],
        }
        st.data["prescribed_procedure"] = prescribed

        judge_calls = 0

        async def fake_claude_p(**kwargs):
            nonlocal judge_calls
            judge_calls += 1
            return {
                "user_prescribed_a_procedure": True,
                "instruction_adherence": 2.0,  # always low -> always retry
                "violations": ["recon generate never runs"],
                "rationale": "still violates",
            }

        seen_rounds: list[int] = []

        async def fake_phase_plan(task, st_, caps_, models_, efforts_,
                                  replan_round=0):
            seen_rounds.append(replan_round)
            return plans

        with (
            patch.object(leerie, "claude_p", new=AsyncMock(side_effect=fake_claude_p)),
            patch.object(leerie, "phase_plan", new=AsyncMock(side_effect=fake_phase_plan)),
            patch.object(leerie, "load_prompt", return_value="sys"),
        ):
            try:
                _run(leerie.phase_adherence_gate(
                    plans, "task", st, caps, models, efforts))
            except SystemExit:
                pass  # exhaustion die() is expected; we only care about seen_rounds

        assert seen_rounds == sorted(seen_rounds), (
            "replan_round must increase monotonically across retries"
        )
        assert len(seen_rounds) == len(set(seen_rounds)), (
            f"replan_round values must all be distinct, got {seen_rounds} "
            "— a repeated value means two re-plan invocations would collide "
            "on the same worker sid"
        )
        assert seen_rounds[0] == 1, "the first re-plan must be round 1, not 0"
