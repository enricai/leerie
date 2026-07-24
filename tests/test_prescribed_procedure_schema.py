"""Tests for SCHEMAS["classifier"]["properties"]["prescribed_procedure"] —
the language→JSON channel the classifier uses to declare that the user
prescribed an explicit procedure/command-sequence (DESIGN §12 sibling:
language interpretation is the LLM's job, returned as structured data;
Python only ever compares already-structured fields).

Mirrors test_dep_capture_schema.py's HAS_JSONSCHEMA-gated structural
fallback so CI without jsonschema installed still catches drift.
"""
from __future__ import annotations

import inspect
import json

import pytest

try:
    import jsonschema  # type: ignore
    HAS_JSONSCHEMA = True
except ImportError:
    HAS_JSONSCHEMA = False


def _validate(leerie, instance: dict) -> None:
    schema = leerie.SCHEMAS["classifier"]
    if HAS_JSONSCHEMA:
        jsonschema.validate(instance, schema)
        return
    for k in schema["required"]:
        assert k in instance, f"missing required field {k!r}"
    prescribed = instance.get("prescribed_procedure")
    if prescribed is not None:
        assert isinstance(prescribed, dict)
        if "is_prescribed" in prescribed:
            assert isinstance(prescribed["is_prescribed"], bool)
        if "commands" in prescribed:
            assert isinstance(prescribed["commands"], list)
            for c in prescribed["commands"]:
                assert isinstance(c, str)
        if "forbid_manual" in prescribed:
            assert isinstance(prescribed["forbid_manual"], bool)
        if "evidence" in prescribed:
            assert isinstance(prescribed["evidence"], str)


def _conf(**axes):
    return {**axes, "basis": "test", "falsifiers_tested": [],
            "contradictions_reconciled": [], "gap_to_close": {}}


# --- schema shape ------------------------------------------------------- #

def test_prescribed_procedure_field_exists(leerie):
    props = leerie.SCHEMAS["classifier"]["properties"]
    assert "prescribed_procedure" in props
    assert props["prescribed_procedure"]["type"] == "object"


def test_prescribed_procedure_has_expected_properties(leerie):
    prop = leerie.SCHEMAS["classifier"]["properties"]["prescribed_procedure"]
    assert set(prop["properties"].keys()) == {
        "is_prescribed", "commands", "forbid_manual", "evidence"}


def test_prescribed_procedure_is_prescribed_is_boolean(leerie):
    prop = leerie.SCHEMAS["classifier"]["properties"]["prescribed_procedure"]
    assert prop["properties"]["is_prescribed"]["type"] == "boolean"


def test_prescribed_procedure_commands_is_array_of_strings(leerie):
    prop = leerie.SCHEMAS["classifier"]["properties"]["prescribed_procedure"]
    commands = prop["properties"]["commands"]
    assert commands["type"] == "array"
    assert commands["items"]["type"] == "string"


def test_prescribed_procedure_forbid_manual_is_boolean(leerie):
    prop = leerie.SCHEMAS["classifier"]["properties"]["prescribed_procedure"]
    assert prop["properties"]["forbid_manual"]["type"] == "boolean"


def test_prescribed_procedure_evidence_is_string(leerie):
    prop = leerie.SCHEMAS["classifier"]["properties"]["prescribed_procedure"]
    assert prop["properties"]["evidence"]["type"] == "string"


# --- valid instance acceptance ------------------------------------------ #

def test_classifier_schema_accepts_prescribed_procedure_incident_shape(leerie):
    _validate(leerie, {
        "categories": ["feature-implementation"],
        "confidence": _conf(classification=9.2),
        "prescribed_procedure": {
            "is_prescribed": True,
            "commands": ["recon browser", "recon generate"],
            "forbid_manual": True,
            "evidence": "your ONLY job is to run recon browser then "
                        "recon generate",
        },
    })


def test_classifier_schema_accepts_prescribed_procedure_goal_only_shape(leerie):
    _validate(leerie, {
        "categories": ["feature-implementation"],
        "confidence": _conf(classification=9.0),
        "prescribed_procedure": {
            "is_prescribed": False,
            "commands": [],
            "forbid_manual": False,
            "evidence": "",
        },
    })


def test_classifier_schema_accepts_missing_prescribed_procedure(leerie):
    """prescribed_procedure is not in `required` — older/degenerate worker
    output omitting it entirely must still validate."""
    _validate(leerie, {
        "categories": ["bug-fixing"],
        "confidence": _conf(classification=9.0),
    })


def test_prescribed_procedure_not_required(leerie):
    assert "prescribed_procedure" not in leerie.SCHEMAS["classifier"]["required"]


# --- round-trip and serialization --------------------------------------- #

def test_classifier_schema_is_json_serializable(leerie):
    json.dumps(leerie.SCHEMAS["classifier"])


def test_classifier_schema_round_trips(leerie):
    schema = leerie.SCHEMAS["classifier"]
    assert json.loads(json.dumps(schema)) == schema


# --- persistence wiring (source-coupling, mirrors test_dep_capture_wiring.py) #

def test_phase_classify_persists_prescribed_procedure(leerie):
    """phase_classify must persist result['prescribed_procedure'] to
    st.data alongside 'categories' — the PREVENT-half signal the planner
    (and a future deterministic gate) reads. Source-coupled rather than
    a live-invocation test since phase_classify calls claude_p()."""
    src = inspect.getsource(leerie.phase_classify)
    assert 'st.data["categories"] = cats' in src
    assert 'st.data["prescribed_procedure"]' in src
    # Persisted from the worker result, not fabricated.
    idx_categories = src.index('st.data["categories"] = cats')
    idx_prescribed = src.index('st.data["prescribed_procedure"]')
    idx_save = src.rindex("st.save()")
    assert idx_categories < idx_save
    assert idx_prescribed < idx_save


def test_phase_classify_prescribed_procedure_defaults_to_empty_dict(leerie):
    src = inspect.getsource(leerie.phase_classify)
    assert 'result.get("prescribed_procedure") or {}' in src
