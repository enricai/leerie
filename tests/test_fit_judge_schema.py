"""Tests for SCHEMAS["fit_judge"] — the P1 fit-judge worker output schema.

The schema lives in SCHEMAS["fit_judge"] and is passed to `claude -p`
via --json-schema. These tests pin the structural contract and guard against
silent drift (required fields, score range, confidence sub-schema).

Mirrors test_pr_writer_schema.py / test_dep_capture_schema.py patterns:
uses a HAS_JSONSCHEMA gate with a manual structural fallback.
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
    schema = leerie.SCHEMAS["fit_judge"]
    if HAS_JSONSCHEMA:
        jsonschema.validate(instance, schema)
        return
    for k in schema["required"]:
        assert k in instance, f"missing required field {k!r}"
    if "score" in instance:
        assert isinstance(instance["score"], (int, float))
        assert 0 <= instance["score"] <= 1
    if "rationale" in instance:
        assert isinstance(instance["rationale"], str)
    if "diffuse" in instance:
        assert isinstance(instance["diffuse"], str)
    if "confidence" in instance:
        conf = instance["confidence"]
        assert isinstance(conf, dict)
        assert "fit" in conf


# --- existence and shape ---------------------------------------------------

def test_fit_judge_schema_exists(leerie):
    assert "fit_judge" in leerie.SCHEMAS
    schema = leerie.SCHEMAS["fit_judge"]
    assert schema["type"] == "object"


def test_fit_judge_schema_required_fields(leerie):
    """required must be exactly {score, rationale, diffuse, confidence}."""
    schema = leerie.SCHEMAS["fit_judge"]
    assert set(schema["required"]) == {"score", "rationale", "diffuse", "confidence"}


def test_fit_judge_score_is_number(leerie):
    prop = leerie.SCHEMAS["fit_judge"]["properties"]["score"]
    assert prop["type"] == "number"


def test_fit_judge_score_range(leerie):
    """score is bounded 0–1 by the schema."""
    prop = leerie.SCHEMAS["fit_judge"]["properties"]["score"]
    assert prop.get("minimum") == 0
    assert prop.get("maximum") == 1


def test_fit_judge_rationale_is_string(leerie):
    prop = leerie.SCHEMAS["fit_judge"]["properties"]["rationale"]
    assert prop["type"] == "string"


def test_fit_judge_diffuse_is_string(leerie):
    prop = leerie.SCHEMAS["fit_judge"]["properties"]["diffuse"]
    assert prop["type"] == "string"


def test_fit_judge_confidence_uses_fit_axis(leerie):
    """confidence sub-schema must include a 'fit' axis (reuses _confidence_schema)."""
    conf_schema = leerie.SCHEMAS["fit_judge"]["properties"]["confidence"]
    assert "fit" in conf_schema.get("required", [])
    assert "fit" in conf_schema.get("properties", {})


# --- valid instance acceptance ----------------------------------------------

def test_fit_judge_accepts_well_fit(leerie):
    _validate(leerie, {
        "score": 0.85,
        "rationale": "Single verifiable unit, bounded surface.",
        "diffuse": "",
        "confidence": {
            "fit": 9.0,
            "basis": "files_likely_touched count + coherent intent",
            "falsifiers_tested": ["checked for hidden broad surface: no"],
            "contradictions_reconciled": [],
            "gap_to_close": {},
        },
    })


def test_fit_judge_accepts_low_score(leerie):
    _validate(leerie, {
        "score": 0.25,
        "rationale": "Migration sweep of 40 files — multiple conceptual units.",
        "diffuse": "Covers both schema and data-migration in one subtask.",
        "confidence": {
            "fit": 8.5,
            "basis": "file count + lack of single verifiable criterion",
            "falsifiers_tested": ["checked if mechanical: no, requires logic changes"],
            "contradictions_reconciled": [],
            "gap_to_close": {},
        },
    })


def test_fit_judge_accepts_boundary_score(leerie):
    """Score of exactly 0.70 — the measured threshold (DEFAULT_CAPS)."""
    _validate(leerie, {
        "score": 0.70,
        "rationale": "Borderline case; acceptable leaf.",
        "diffuse": "",
        "confidence": {
            "fit": 8.0,
            "basis": "marginally within threshold",
            "falsifiers_tested": ["tested for hidden surface: none found"],
            "contradictions_reconciled": [],
            "gap_to_close": {},
        },
    })


# --- invalid instance rejection --------------------------------------------

def test_fit_judge_rejects_score_above_1(leerie):
    if not HAS_JSONSCHEMA:
        pytest.skip("jsonschema not available; maximum check requires it")
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(
            {
                "score": 1.1,
                "rationale": "x",
                "diffuse": "",
                "confidence": {
                    "fit": 8.0,
                    "basis": "x",
                    "falsifiers_tested": [],
                    "contradictions_reconciled": [],
                    "gap_to_close": {},
                },
            },
            leerie.SCHEMAS["fit_judge"],
        )


def test_fit_judge_rejects_score_below_0(leerie):
    if not HAS_JSONSCHEMA:
        pytest.skip("jsonschema not available; minimum check requires it")
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(
            {
                "score": -0.1,
                "rationale": "x",
                "diffuse": "",
                "confidence": {
                    "fit": 8.0,
                    "basis": "x",
                    "falsifiers_tested": [],
                    "contradictions_reconciled": [],
                    "gap_to_close": {},
                },
            },
            leerie.SCHEMAS["fit_judge"],
        )


def test_fit_judge_rejects_missing_score(leerie):
    if not HAS_JSONSCHEMA:
        pytest.skip("jsonschema not available; required check requires it")
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(
            {"rationale": "x", "diffuse": "", "confidence": {}},
            leerie.SCHEMAS["fit_judge"],
        )


# --- JSON serializability and round-trip ------------------------------------

def test_fit_judge_schema_is_json_serializable(leerie):
    json.dumps(leerie.SCHEMAS["fit_judge"])


def test_fit_judge_schema_round_trips(leerie):
    """Schema survives json.dumps → json.loads with identical content."""
    schema = leerie.SCHEMAS["fit_judge"]
    assert json.loads(json.dumps(schema)) == schema


# --- wiring checks ----------------------------------------------------------

def test_fit_judge_in_worker_types(leerie):
    """fit_judge must be in WORKER_TYPES so load_prompt / claude_p accept it."""
    assert "fit_judge" in leerie.WORKER_TYPES


def test_fit_judge_not_in_model_default_per_worker(leerie):
    """fit_judge defaults to opus (MODEL_DEFAULT); must NOT appear in
    MODEL_DEFAULT_PER_WORKER (that dict only holds non-default overrides)."""
    assert "fit_judge" not in leerie.MODEL_DEFAULT_PER_WORKER


def test_fit_judge_effort_default_is_high(leerie):
    """fit_judge is a judgment worker — EFFORT_DEFAULT_PER_WORKER["fit_judge"]
    must be 'high' to match other judgment workers."""
    assert leerie.EFFORT_DEFAULT_PER_WORKER.get("fit_judge") == "high"


def test_fit_judge_prompt_file_exists(leerie):
    """prompts/fit_judge.md must exist — load_prompt() reads it at call time."""
    import importlib.util
    from pathlib import Path
    leerie_path = Path(leerie.__file__)
    prompt = leerie_path.parent.parent / "prompts" / "fit_judge.md"
    assert prompt.exists(), f"prompts/fit_judge.md not found at {prompt}"
