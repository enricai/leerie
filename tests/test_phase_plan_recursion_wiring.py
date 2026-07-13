"""Tests for P1 Layer C: recursive_decompose wired into phase_plan (DESIGN §5½).

Verifies:

1. Source-coupling guard: phase_plan calls recursive_decompose with the right
   arguments (inspect.getsource) — any refactor that breaks the wiring fails
   loudly here.

2. Integration: a plan_one result containing one oversized subtask is expanded
   via a stubbed recursive_decompose into multiple leaves, and that leaf set
   replaces the original subtasks in the plan before phase_plan returns.

3. Skip / well-fit path: when the stubbed recursive_decompose returns the
   original subtask as-is (well-fit leaf; score ≥ threshold on the first
   judge call), phase_plan's plan["subtasks"] is the same single-element
   list — no mutation, no extra subtasks.

These tests stub claude_p and recursive_decompose directly, so no live worker
is spawned. Phase-plan is exercised end-to-end except for the network/subprocess
boundary.
"""
from __future__ import annotations

import asyncio
import inspect
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# 1. Source-coupling guard
# ---------------------------------------------------------------------------

class TestSourceCouplingGuard:
    """phase_plan must call recursive_decompose after plan_one collects results."""

    def test_phase_plan_calls_recursive_decompose(self, leerie):
        src = inspect.getsource(leerie.phase_plan)
        assert "recursive_decompose(" in src, (
            "phase_plan must call recursive_decompose() for P1 Layer C. "
            "The call was removed or renamed — first-pass subtasks will no "
            "longer be expanded to leaves before reaching phase_reconcile."
        )

    def test_phase_plan_passes_depth_zero(self, leerie):
        """recursive_decompose must be called at depth 0 (top-level entry)."""
        src = inspect.getsource(leerie.phase_plan)
        assert "recursive_decompose(" in src
        # The depth-0 call is the entry point for each first-pass subtask.
        assert ", 0," in src, (
            "recursive_decompose must be called with depth=0 in phase_plan "
            "so the recursion depth cap counts from the planner's level."
        )

    def test_phase_plan_replaces_subtasks_with_leaves(self, leerie):
        """plan['subtasks'] must be reassigned to the leaf union."""
        src = inspect.getsource(leerie.phase_plan)
        assert 'plan["subtasks"] = leaves' in src or "plan['subtasks'] = leaves" in src, (
            "phase_plan must assign the leaf union back to plan['subtasks'] "
            "so that downstream (reconcile→schedule) sees the expanded set."
        )

    def test_phase_plan_passes_repo_map_to_recursive_decompose(self, leerie):
        """G2 caller-side seam: phase_plan must pass the built repo_map into
        recursive_decompose, else the per-node P6 grounding is dead code
        (the callee has the injection logic but never receives a map)."""
        src = inspect.getsource(leerie.phase_plan)
        assert "repo_map=repo_map" in src, (
            "phase_plan must call recursive_decompose(..., repo_map=repo_map) "
            "so the once-built symbol graph reaches fit_judge/splitter for "
            "per-node re-ranking (DESIGN §5½ P6)."
        )

    def test_phase_plan_expands_before_logging(self, leerie):
        """The recursive_decompose loop must precede the final logging loop."""
        src = inspect.getsource(leerie.phase_plan)
        decompose_pos = src.index("recursive_decompose(")
        # The final logging line uses the category+plan zip
        logging_pos = src.index('log(f"  {category}:')
        assert decompose_pos < logging_pos, (
            "recursive_decompose must run before the final per-category "
            "logging so the logged subtask count reflects the expanded set."
        )


# ---------------------------------------------------------------------------
# Helpers for integration tests
# ---------------------------------------------------------------------------

_CATEGORY = "feature-implementation"  # a real entry in CATEGORY_ABBREV


def _make_state(leerie, skip_repo_map: bool = False) -> MagicMock:
    st = MagicMock()
    st.data = {
        "categories": [_CATEGORY],
        "answers": {"source_of_truth": "codebase"},
        "current_phase": "",
        "skip_repo_map": skip_repo_map,
    }
    # st.data is a real dict; .get, __getitem__, __setitem__ all work natively.
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


_OVERSIZED_SUBTASK = {
    "id": "feat-001",
    "title": "Migrate 20 files",
    "success_criteria_seed": "all 20 files migrated",
    "files_likely_touched": [f"src/f{i}.ts" for i in range(20)],
    "intent": "big migration",
    "scope_note": "",
    "depends_on": [],
    "requires": [],
    "provides": [],
    "size": "large",
    "investigation_notes": "",
}

_LEAF_A = {**_OVERSIZED_SUBTASK, "id": "feat-001-1",
           "files_likely_touched": [f"src/f{i}.ts" for i in range(10)]}
_LEAF_B = {**_OVERSIZED_SUBTASK, "id": "feat-001-2",
           "files_likely_touched": [f"src/f{i}.ts" for i in range(10, 20)]}

_PLANNER_RESPONSE = {
    "domain": _CATEGORY,
    "status": "ready",
    "confidence": {"root_cause": 9.0, "solution": 9.0, "basis": "ok",
                   "falsifiers_tested": [], "contradictions_reconciled": [],
                   "gap_to_close": {}},
    "subtasks": [_OVERSIZED_SUBTASK],
}


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# 2. Integration: oversized subtask expands into multiple leaves
# ---------------------------------------------------------------------------

class TestRecursionExpansion:
    """phase_plan replaces an oversized subtask with its recursive leaves."""

    def test_oversized_subtask_expanded_to_leaves(self, leerie, tmp_path):
        """Stubbed recursive_decompose returning two leaves → plan has 2 subtasks."""
        st = _make_state(leerie)
        caps = _make_caps(leerie)
        models = {k: leerie.MODEL_DEFAULT for k in leerie.WORKER_TYPES}
        efforts = {k: None for k in leerie.WORKER_TYPES}

        async def fake_recursive_decompose(subtask, depth, st_, caps_, models_,
                                           efforts_, repo_root_, **kwargs):
            # Simulate expansion: one oversized subtask → two leaves.
            return [_LEAF_A, _LEAF_B]

        # plan_one returns the planner response with one oversized subtask.
        fake_planner_result = json.loads(json.dumps(_PLANNER_RESPONSE))

        with (
            patch.object(leerie, "load_prompt", return_value="system-prompt"),
            patch.object(leerie, "extract_task_file_structure", return_value=[]),
            patch.object(leerie, "build_repo_map",
                         side_effect=RuntimeError("no tree-sitter")),
            patch.object(leerie, "claude_p",
                         new=AsyncMock(return_value=fake_planner_result)),
            patch.object(leerie, "check_planner_output", return_value=[]),
            patch.object(leerie, "check_task_file_coverage", return_value=[]),
            patch.object(leerie, "recursive_decompose",
                         new=AsyncMock(side_effect=fake_recursive_decompose)),
        ):
            plans = _run(leerie.phase_plan(
                "Migrate 20 files", st, caps, models, efforts))

        assert len(plans) == 1
        plan = plans[0]
        subtasks = plan.get("subtasks", [])
        assert len(subtasks) == 2, (
            f"Expected 2 leaves after expansion, got {len(subtasks)}: "
            f"{[s['id'] for s in subtasks]}"
        )
        ids = {s["id"] for s in subtasks}
        assert ids == {"feat-001-1", "feat-001-2"}

    def test_multiple_first_pass_subtasks_all_expanded(self, leerie, tmp_path):
        """Two first-pass subtasks → recursive_decompose called for each."""
        st = _make_state(leerie)
        caps = _make_caps(leerie)
        models = {k: leerie.MODEL_DEFAULT for k in leerie.WORKER_TYPES}
        efforts = {k: None for k in leerie.WORKER_TYPES}

        subtask_a = {**_OVERSIZED_SUBTASK, "id": "feat-001"}
        subtask_b = {**_OVERSIZED_SUBTASK, "id": "feat-002",
                     "title": "Another migration"}
        planner_resp = {**_PLANNER_RESPONSE, "subtasks": [subtask_a, subtask_b]}

        call_log: list[str] = []

        async def fake_recursive_decompose(subtask, depth, st_, caps_, models_,
                                           efforts_, repo_root_, **kwargs):
            call_log.append(subtask["id"])
            # Each subtask stays as itself (already a leaf in this stub).
            return [subtask]

        with (
            patch.object(leerie, "load_prompt", return_value="sys"),
            patch.object(leerie, "extract_task_file_structure", return_value=[]),
            patch.object(leerie, "build_repo_map",
                         side_effect=RuntimeError("no tree-sitter")),
            patch.object(leerie, "claude_p",
                         new=AsyncMock(return_value=json.loads(
                             json.dumps(planner_resp)))),
            patch.object(leerie, "check_planner_output", return_value=[]),
            patch.object(leerie, "check_task_file_coverage", return_value=[]),
            patch.object(leerie, "recursive_decompose",
                         new=AsyncMock(side_effect=fake_recursive_decompose)),
        ):
            plans = _run(leerie.phase_plan(
                "Migrate files", st, caps, models, efforts))

        # recursive_decompose was called once per first-pass subtask.
        assert sorted(call_log) == ["feat-001", "feat-002"], (
            f"Expected recursive_decompose called for each first-pass subtask; "
            f"call_log={call_log}"
        )
        plan = plans[0]
        assert len(plan.get("subtasks", [])) == 2


# ---------------------------------------------------------------------------
# 3. Pass-through: well-fit subtask returned unchanged
# ---------------------------------------------------------------------------

class TestWellFitPassThrough:
    """When recursive_decompose returns the original subtask (well-fit leaf),
    plan['subtasks'] is the same single-element list — no mutation."""

    def test_well_fit_subtask_passed_through_unchanged(self, leerie, tmp_path):
        """Stubbed recursive_decompose returning the input subtask as-is."""
        well_fit = {**_OVERSIZED_SUBTASK, "id": "feat-001",
                    "files_likely_touched": ["src/one_file.ts"]}
        planner_resp = {**_PLANNER_RESPONSE, "subtasks": [well_fit]}

        st = _make_state(leerie)
        caps = _make_caps(leerie)
        models = {k: leerie.MODEL_DEFAULT for k in leerie.WORKER_TYPES}
        efforts = {k: None for k in leerie.WORKER_TYPES}

        async def fake_recursive_decompose(subtask, depth, st_, caps_, models_,
                                           efforts_, repo_root_, **kwargs):
            # Well-fit: return the subtask unchanged (leaf on first judge call).
            return [subtask]

        with (
            patch.object(leerie, "load_prompt", return_value="sys"),
            patch.object(leerie, "extract_task_file_structure", return_value=[]),
            patch.object(leerie, "build_repo_map",
                         side_effect=RuntimeError("no tree-sitter")),
            patch.object(leerie, "claude_p",
                         new=AsyncMock(return_value=json.loads(
                             json.dumps(planner_resp)))),
            patch.object(leerie, "check_planner_output", return_value=[]),
            patch.object(leerie, "check_task_file_coverage", return_value=[]),
            patch.object(leerie, "recursive_decompose",
                         new=AsyncMock(side_effect=fake_recursive_decompose)),
        ):
            plans = _run(leerie.phase_plan(
                "Fix one file", st, caps, models, efforts))

        plan = plans[0]
        subtasks = plan.get("subtasks", [])
        assert len(subtasks) == 1
        assert subtasks[0]["id"] == "feat-001"

    def test_empty_subtasks_plan_passes_through(self, leerie, tmp_path):
        """A plan with no subtasks is left untouched (no recursive_decompose call)."""
        planner_resp = {**_PLANNER_RESPONSE, "subtasks": []}
        call_count = [0]

        st = _make_state(leerie)
        caps = _make_caps(leerie)
        models = {k: leerie.MODEL_DEFAULT for k in leerie.WORKER_TYPES}
        efforts = {k: None for k in leerie.WORKER_TYPES}

        async def fake_recursive_decompose(*args, **kwargs):
            call_count[0] += 1
            return []

        with (
            patch.object(leerie, "load_prompt", return_value="sys"),
            patch.object(leerie, "extract_task_file_structure", return_value=[]),
            patch.object(leerie, "build_repo_map",
                         side_effect=RuntimeError("no tree-sitter")),
            patch.object(leerie, "claude_p",
                         new=AsyncMock(return_value=json.loads(
                             json.dumps(planner_resp)))),
            patch.object(leerie, "check_planner_output", return_value=[]),
            patch.object(leerie, "check_task_file_coverage", return_value=[]),
            patch.object(leerie, "recursive_decompose",
                         new=AsyncMock(side_effect=fake_recursive_decompose)),
        ):
            plans = _run(leerie.phase_plan(
                "Nothing to do", st, caps, models, efforts))

        assert call_count[0] == 0, (
            "recursive_decompose should not be called for a plan with no subtasks"
        )
        assert plans[0].get("subtasks", []) == []
