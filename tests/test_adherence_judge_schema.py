"""Tests for SCHEMAS["adherence_judge"] — the plan instruction-adherence
judge worker output schema.

The schema lives in SCHEMAS["adherence_judge"] and is passed to `claude -p`
via --json-schema. These tests pin the structural contract and guard against
silent drift (required fields, score range, wiring).

Mirrors test_fit_judge_schema.py's pattern: uses a HAS_JSONSCHEMA gate with
a manual structural fallback.
"""
from __future__ import annotations

import json
import pytest

try:
    import jsonschema  # type: ignore
    HAS_JSONSCHEMA = True
except ImportError:
    HAS_JSONSCHEMA = False


def _validate(leerie, instance: dict) -> None:
    """Validate using jsonschema when available; otherwise fall back to
    structural assertions that mirror what the schema declares."""
    schema = leerie.SCHEMAS["adherence_judge"]
    if HAS_JSONSCHEMA:
        jsonschema.validate(instance, schema)
        return
    for k in schema["required"]:
        assert k in instance, f"missing required field {k!r}"
    if "user_prescribed_a_procedure" in instance:
        assert isinstance(instance["user_prescribed_a_procedure"], bool)
    if "instruction_adherence" in instance:
        assert isinstance(instance["instruction_adherence"], (int, float))
        assert 0 <= instance["instruction_adherence"] <= 10
    if "violations" in instance:
        assert isinstance(instance["violations"], list)
    if "rationale" in instance:
        assert isinstance(instance["rationale"], str)


# --- existence and shape ---------------------------------------------------

def test_adherence_judge_schema_exists(leerie):
    assert "adherence_judge" in leerie.SCHEMAS
    schema = leerie.SCHEMAS["adherence_judge"]
    assert schema["type"] == "object"


def test_adherence_judge_schema_required_fields(leerie):
    """required must be exactly {user_prescribed_a_procedure,
    instruction_adherence, violations, rationale}."""
    schema = leerie.SCHEMAS["adherence_judge"]
    assert set(schema["required"]) == {
        "user_prescribed_a_procedure", "instruction_adherence",
        "violations", "rationale",
    }


def test_adherence_judge_prescribed_flag_is_boolean(leerie):
    prop = leerie.SCHEMAS["adherence_judge"]["properties"][
        "user_prescribed_a_procedure"]
    assert prop["type"] == "boolean"


def test_adherence_judge_score_is_number(leerie):
    prop = leerie.SCHEMAS["adherence_judge"]["properties"][
        "instruction_adherence"]
    assert prop["type"] == "number"


def test_adherence_judge_score_range(leerie):
    """instruction_adherence is bounded 0-10 by the schema."""
    prop = leerie.SCHEMAS["adherence_judge"]["properties"][
        "instruction_adherence"]
    assert prop.get("minimum") == 0
    assert prop.get("maximum") == 10


def test_adherence_judge_violations_is_array_of_strings(leerie):
    prop = leerie.SCHEMAS["adherence_judge"]["properties"]["violations"]
    assert prop["type"] == "array"
    assert prop["items"]["type"] == "string"


def test_adherence_judge_rationale_is_string(leerie):
    prop = leerie.SCHEMAS["adherence_judge"]["properties"]["rationale"]
    assert prop["type"] == "string"


def test_adherence_judge_schema_has_no_confidence_subobject(leerie):
    """Deliberately absent — this worker IS the independent check that
    replaces a self-report, so a nested self-confidence axis would
    reintroduce the self-grading bias the gate exists to remove."""
    schema = leerie.SCHEMAS["adherence_judge"]
    assert "confidence" not in schema.get("properties", {})
    assert "confidence" not in schema["required"]


# --- valid instance acceptance ----------------------------------------------

def test_adherence_judge_accepts_goal_only_high_score(leerie):
    _validate(leerie, {
        "user_prescribed_a_procedure": False,
        "instruction_adherence": 9.0,
        "violations": [],
        "rationale": "No prescribed procedure; plan is a normal implementation.",
    })


def test_adherence_judge_accepts_prescribed_and_violated_low_score(leerie):
    _validate(leerie, {
        "user_prescribed_a_procedure": True,
        "instruction_adherence": 2.5,
        "violations": [
            "Task said 'run recon generate' as the final step; no subtask "
            "runs it — the plan hand-authors the plugin instead.",
        ],
        "rationale": "Plan substitutes prohibited manual work for the "
                      "prescribed procedure.",
    })


def test_adherence_judge_accepts_prescribed_and_honored(leerie):
    _validate(leerie, {
        "user_prescribed_a_procedure": True,
        "instruction_adherence": 9.0,
        "violations": [],
        "rationale": "Plan runs every prescribed command in the prescribed order.",
    })


def test_adherence_judge_accepts_boundary_scores(leerie):
    _validate(leerie, {
        "user_prescribed_a_procedure": True,
        "instruction_adherence": 0,
        "violations": ["Every prescribed step was skipped."],
        "rationale": "Complete non-adherence.",
    })
    _validate(leerie, {
        "user_prescribed_a_procedure": False,
        "instruction_adherence": 10,
        "violations": [],
        "rationale": "Perfect adherence; nothing prescribed to violate.",
    })


# --- invalid instance rejection --------------------------------------------

def test_adherence_judge_rejects_score_above_10(leerie):
    if not HAS_JSONSCHEMA:
        pytest.skip("jsonschema not available; maximum check requires it")
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(
            {
                "user_prescribed_a_procedure": True,
                "instruction_adherence": 10.5,
                "violations": [],
                "rationale": "x",
            },
            leerie.SCHEMAS["adherence_judge"],
        )


def test_adherence_judge_rejects_score_below_0(leerie):
    if not HAS_JSONSCHEMA:
        pytest.skip("jsonschema not available; minimum check requires it")
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(
            {
                "user_prescribed_a_procedure": True,
                "instruction_adherence": -0.1,
                "violations": [],
                "rationale": "x",
            },
            leerie.SCHEMAS["adherence_judge"],
        )


def test_adherence_judge_rejects_missing_required_field(leerie):
    if not HAS_JSONSCHEMA:
        pytest.skip("jsonschema not available; required check requires it")
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(
            {"instruction_adherence": 5, "violations": [], "rationale": "x"},
            leerie.SCHEMAS["adherence_judge"],
        )


def test_adherence_judge_rejects_non_boolean_prescribed_flag(leerie):
    if not HAS_JSONSCHEMA:
        pytest.skip("jsonschema not available; type check requires it")
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(
            {
                "user_prescribed_a_procedure": "yes",
                "instruction_adherence": 5,
                "violations": [],
                "rationale": "x",
            },
            leerie.SCHEMAS["adherence_judge"],
        )


# --- JSON serializability and round-trip ------------------------------------

def test_adherence_judge_schema_is_json_serializable(leerie):
    json.dumps(leerie.SCHEMAS["adherence_judge"])


def test_adherence_judge_schema_round_trips(leerie):
    """Schema survives json.dumps -> json.loads with identical content."""
    schema = leerie.SCHEMAS["adherence_judge"]
    assert json.loads(json.dumps(schema)) == schema


# --- wiring checks ----------------------------------------------------------

def test_adherence_judge_in_worker_types(leerie):
    """adherence_judge must be in WORKER_TYPES so load_prompt / claude_p
    accept it and per-worker CLI/env/TOML resolution registers it."""
    assert "adherence_judge" in leerie.WORKER_TYPES


def test_adherence_judge_not_in_model_default_per_worker(leerie):
    """adherence_judge defaults to opus (MODEL_DEFAULT); must NOT appear in
    MODEL_DEFAULT_PER_WORKER (that dict only holds non-default overrides).
    This is empirically required, not merely preferred: sonnet
    false-positived a legitimate plan in calibration testing."""
    assert "adherence_judge" not in leerie.MODEL_DEFAULT_PER_WORKER


def test_adherence_judge_effort_default_is_medium(leerie):
    """adherence_judge is a judgment worker — EFFORT_DEFAULT_PER_WORKER
    must be 'medium' to match other judgment workers (lowered from 'high'
    post-Opus-5; see IMPLEMENTATION.md §2)."""
    assert leerie.EFFORT_DEFAULT_PER_WORKER.get("adherence_judge") == "medium"


def test_adherence_judge_prompt_file_exists(leerie):
    """prompts/adherence_judge.md must exist — load_prompt() reads it at
    call time."""
    from pathlib import Path
    leerie_path = Path(leerie.__file__)
    prompt = leerie_path.parent.parent / "prompts" / "adherence_judge.md"
    assert prompt.exists(), f"prompts/adherence_judge.md not found at {prompt}"
