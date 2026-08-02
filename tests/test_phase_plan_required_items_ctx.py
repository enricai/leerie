"""Tests for required_items injection into phase_plan context (the PRIMARY
floor for the task-coverage gate — DESIGN §8 sibling to the instruction-
adherence gate's prescribed_procedure injection).

Without this injection, check_required_items_coverage() has no way to be
satisfied: the planner never sees the classifier's required_items
checklist, so it can never echo an item's wording into a subtask, so
every declared item reads as REQUIRED_ITEM_UNCOVERED on every real run.
This was caught by an independent re-verification pass after the floor
itself landed — the floor and its test suite were both correct in
isolation, but nothing fed the planner the data it needed to satisfy it.

Verifies:
- When st.data["required_items"] is non-empty, the ctx JSON blob carries
  "required_items" verbatim.
- When st.data has no "required_items" key at all, ctx omits it.
- When st.data["required_items"] is an empty list, ctx omits the key —
  the common case (most tasks have no enumerable requirements) carries
  no false framing.
- Baseline ctx keys are unaffected in every branch.
- The ctx dict remains JSON-serializable with the key present.

Mirrors tests/test_phase_plan_prescribed_procedure_ctx.py's pattern
exactly (same PRIMARY-floor-needs-planner-visibility shape).
"""
from __future__ import annotations

import inspect
import json


def _build_ctx(task: str, sot: str, answers: dict, confidence_rounds: int,
               st_data: dict) -> dict:
    """Reproduce the phase_plan ctx-building logic under test (the
    required_items slice), without spawning planners or touching
    repo_map."""
    ctx_dict: dict = {
        "task": task,
        "source_of_truth": sot,
        "clarification_answers": answers,
        "confidence_rounds": confidence_rounds,
    }
    required_items = st_data.get("required_items") or []
    if required_items:
        ctx_dict["required_items"] = required_items
    return ctx_dict


# ---------------------------------------------------------------------------
# Branch 1: non-empty required_items → ctx carries it verbatim
# ---------------------------------------------------------------------------

class TestRequiredItemsPresent:
    def test_ctx_contains_required_items(self):
        items = [{"item": "add rate limiting to the API",
                  "source_ref": "item 3 of spec"}]
        ctx = _build_ctx(
            task="build the API per the spec",
            sot="codebase",
            answers={},
            confidence_rounds=8,
            st_data={"required_items": items},
        )
        assert "required_items" in ctx
        assert ctx["required_items"] == items

    def test_baseline_keys_present_when_required_items_set(self):
        ctx = _build_ctx(
            task="build the API per the spec",
            sot="both",
            answers={"source_of_truth": "both"},
            confidence_rounds=4,
            st_data={"required_items": [{"item": "add pagination"}]},
        )
        for key in ("task", "source_of_truth", "clarification_answers",
                    "confidence_rounds"):
            assert key in ctx, f"Baseline key '{key}' missing from ctx"

    def test_ctx_serializable_to_json(self):
        items = [{"item": "add rate limiting"}, {"item": "add pagination"}]
        ctx = _build_ctx(
            task="build the API per the spec",
            sot="codebase",
            answers={},
            confidence_rounds=8,
            st_data={"required_items": items},
        )
        serialized = json.dumps(ctx, indent=2)
        parsed = json.loads(serialized)
        assert parsed["required_items"] == items


# ---------------------------------------------------------------------------
# Branch 2: absent from st.data entirely → ctx omits the key
# ---------------------------------------------------------------------------

class TestRequiredItemsAbsent:
    def test_ctx_omits_key_when_absent_from_state(self):
        ctx = _build_ctx(
            task="add a field to the User model",
            sot="codebase",
            answers={},
            confidence_rounds=8,
            st_data={},
        )
        assert "required_items" not in ctx

    def test_baseline_keys_present_when_absent(self):
        ctx = _build_ctx(
            task="add a field to the User model",
            sot="research",
            answers={"q": "a"},
            confidence_rounds=6,
            st_data={},
        )
        for key in ("task", "source_of_truth", "clarification_answers",
                    "confidence_rounds"):
            assert key in ctx, f"Baseline key '{key}' missing when absent"


# ---------------------------------------------------------------------------
# Branch 3: present but empty list → ctx omits the key (the common case
# carries no false framing)
# ---------------------------------------------------------------------------

class TestRequiredItemsEmpty:
    def test_ctx_omits_key_when_empty_list(self):
        ctx = _build_ctx(
            task="add a field to the User model",
            sot="codebase",
            answers={},
            confidence_rounds=8,
            st_data={"required_items": []},
        )
        assert "required_items" not in ctx


# ---------------------------------------------------------------------------
# Source-coupling: the real phase_plan must actually do this, not just
# this file's reproduction of it.
# ---------------------------------------------------------------------------

class TestWiredIntoRealPhasePlan:
    def test_phase_plan_source_injects_required_items(self, leerie):
        src = inspect.getsource(leerie.phase_plan)
        assert 'st.data.get("required_items")' in src
        assert 'ctx_dict["required_items"] = required_items' in src
