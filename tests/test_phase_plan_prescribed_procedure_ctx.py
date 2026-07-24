"""Tests for prescribed_procedure injection into phase_plan context
(the PREVENT half of the instruction-adherence gate — feat-002).

Verifies:
- When st.data["prescribed_procedure"]["is_prescribed"] is True, the ctx
  JSON blob carries "prescribed_procedure" verbatim.
- When st.data has no "prescribed_procedure" key at all, ctx omits it
  (graceful degrade for runs that predate/skip the field).
- When st.data["prescribed_procedure"] is present but is_prescribed is
  False (or missing), ctx omits the key — a goal-only task carries no
  false framing.
- Baseline ctx keys are unaffected in every branch.
- The ctx dict remains JSON-serializable with the key present.

Mirrors tests/test_phase_plan_repo_map_ctx.py's pattern: exercise the
ctx-building logic directly (no live claude subprocess needed).
"""
from __future__ import annotations

import json


# ---------------------------------------------------------------------------
# Helper: build the ctx dict the same way phase_plan does (the
# prescribed_procedure slice of the block, mirroring _build_ctx in
# test_phase_plan_repo_map_ctx.py).
# ---------------------------------------------------------------------------

def _build_ctx(task: str, sot: str, answers: dict, confidence_rounds: int,
               st_data: dict) -> dict:
    """Reproduce the phase_plan ctx-building logic under test (the
    prescribed_procedure slice), without spawning planners or touching
    repo_map."""
    ctx_dict: dict = {
        "task": task,
        "source_of_truth": sot,
        "clarification_answers": answers,
        "confidence_rounds": confidence_rounds,
    }
    prescribed_procedure = st_data.get("prescribed_procedure") or {}
    if prescribed_procedure.get("is_prescribed"):
        ctx_dict["prescribed_procedure"] = prescribed_procedure
    return ctx_dict


# ---------------------------------------------------------------------------
# Branch 1: is_prescribed=True → ctx carries prescribed_procedure verbatim
# ---------------------------------------------------------------------------

class TestPrescribedProcedurePresent:
    def test_ctx_contains_prescribed_procedure(self):
        prescribed = {
            "is_prescribed": True,
            "commands": ["recon browser", "recon generate"],
            "forbid_manual": True,
            "evidence": "task says 'your ONLY job is to run recon'",
        }
        ctx = _build_ctx(
            task="run recon then generate",
            sot="codebase",
            answers={},
            confidence_rounds=8,
            st_data={"prescribed_procedure": prescribed},
        )
        assert "prescribed_procedure" in ctx
        assert ctx["prescribed_procedure"] == prescribed

    def test_baseline_keys_present_when_prescribed(self):
        ctx = _build_ctx(
            task="run recon then generate",
            sot="both",
            answers={"source_of_truth": "both"},
            confidence_rounds=4,
            st_data={"prescribed_procedure": {
                "is_prescribed": True, "commands": ["build"],
                "forbid_manual": False, "evidence": "task says run build",
            }},
        )
        for key in ("task", "source_of_truth", "clarification_answers",
                    "confidence_rounds"):
            assert key in ctx, f"Baseline key '{key}' missing from ctx"

    def test_ctx_serializable_to_json(self):
        prescribed = {
            "is_prescribed": True,
            "commands": ["recon browser", "recon generate"],
            "forbid_manual": True,
            "evidence": "explicit instruction",
        }
        ctx = _build_ctx(
            task="run recon then generate",
            sot="codebase",
            answers={},
            confidence_rounds=8,
            st_data={"prescribed_procedure": prescribed},
        )
        serialized = json.dumps(ctx, indent=2)
        parsed = json.loads(serialized)
        assert parsed["prescribed_procedure"] == prescribed


# ---------------------------------------------------------------------------
# Branch 2: absent from st.data entirely → ctx omits the key
# ---------------------------------------------------------------------------

class TestPrescribedProcedureAbsent:
    def test_ctx_omits_key_when_absent_from_state(self):
        ctx = _build_ctx(
            task="add a field to the User model",
            sot="codebase",
            answers={},
            confidence_rounds=8,
            st_data={},
        )
        assert "prescribed_procedure" not in ctx

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
# Branch 3: present but is_prescribed=False → ctx omits the key (no false
# framing on a goal-only task)
# ---------------------------------------------------------------------------

class TestPrescribedProcedureFalse:
    def test_ctx_omits_key_when_not_prescribed(self):
        ctx = _build_ctx(
            task="add a field to the User model",
            sot="codebase",
            answers={},
            confidence_rounds=8,
            st_data={"prescribed_procedure": {
                "is_prescribed": False, "commands": [],
                "forbid_manual": False, "evidence": "",
            }},
        )
        assert "prescribed_procedure" not in ctx

    def test_ctx_omits_key_when_is_prescribed_missing(self):
        """A prescribed_procedure dict with no is_prescribed key at all
        (e.g. classifier emitted a partial object) must not be injected."""
        ctx = _build_ctx(
            task="add a field to the User model",
            sot="codebase",
            answers={},
            confidence_rounds=8,
            st_data={"prescribed_procedure": {"commands": []}},
        )
        assert "prescribed_procedure" not in ctx

    def test_ctx_omits_key_when_prescribed_procedure_is_empty_dict(self):
        """st.data["prescribed_procedure"] defaults to {} per phase_classify
        (leerie.py) when the classifier omits the field entirely."""
        ctx = _build_ctx(
            task="add a field to the User model",
            sot="codebase",
            answers={},
            confidence_rounds=8,
            st_data={"prescribed_procedure": {}},
        )
        assert "prescribed_procedure" not in ctx
