"""Tests for SCHEMAS["rebaser"] — the finalize-time rebase-onto-base
worker's output schema (DESIGN §6 *Finalization* "Rebase-onto-base before
push").

Mirrors SCHEMAS["integrator"]'s resolved/design-conflict/failed trichotomy
shape, with the enum renamed to rebased/irreconcilable/failed. Mirrors
test_dep_capture_schema.py / test_pr_writer_schema.py's HAS_JSONSCHEMA-gated
validate-with-fallback pattern.
"""
from __future__ import annotations

import json

try:
    import jsonschema  # type: ignore
    HAS_JSONSCHEMA = True
except ImportError:
    HAS_JSONSCHEMA = False


def _validate(leerie, instance: dict) -> None:
    schema = leerie.SCHEMAS["rebaser"]
    if HAS_JSONSCHEMA:
        jsonschema.validate(instance, schema)
        return
    for k in schema["required"]:
        assert k in instance, f"missing required field {k!r}"
    assert instance["status"] in ("rebased", "irreconcilable", "failed")
    assert isinstance(instance["final_branch_state"], str)


# --- existence and shape -----------------------------------------------------

def test_rebaser_schema_exists(leerie):
    assert "rebaser" in leerie.SCHEMAS


def test_rebaser_schema_required_fields(leerie):
    schema = leerie.SCHEMAS["rebaser"]
    assert set(schema["required"]) == {
        "status", "final_branch_state", "confidence"}


def test_rebaser_schema_status_enum(leerie):
    schema = leerie.SCHEMAS["rebaser"]
    assert schema["properties"]["status"]["enum"] == [
        "rebased", "irreconcilable", "failed"]


def test_rebaser_schema_has_optional_resolution_summary_and_diagnosis(leerie):
    schema = leerie.SCHEMAS["rebaser"]
    assert "resolution_summary" in schema["properties"]
    assert "diagnosis" in schema["properties"]
    assert "resolution_summary" not in schema["required"]
    assert "diagnosis" not in schema["required"]


def test_rebaser_schema_confidence_uses_resolution_axis(leerie):
    schema = leerie.SCHEMAS["rebaser"]
    conf = schema["properties"]["confidence"]
    assert conf == leerie._confidence_schema(["resolution"])


def test_rebaser_schema_mirrors_integrator_trichotomy_shape(leerie):
    """rebaser and integrator share the same trichotomy shape (status +
    confidence required; resolution_summary/diagnosis optional) — only the
    identifying field differs (incoming_subtask vs final_branch_state) and
    the enum values differ (resolved/design-conflict vs
    rebased/irreconcilable)."""
    rebaser = leerie.SCHEMAS["rebaser"]
    integrator = leerie.SCHEMAS["integrator"]
    assert set(rebaser["required"]) - {"final_branch_state"} == \
        set(integrator["required"]) - {"incoming_subtask"}
    assert rebaser["properties"]["confidence"] == \
        integrator["properties"]["confidence"]


# --- valid / invalid instances ------------------------------------------------

def _full_confidence(resolution: float) -> dict:
    return {
        "resolution": resolution,
        "basis": "checked worktree state before reporting",
        "falsifiers_tested": ["no conflict markers remain"],
        "contradictions_reconciled": [],
        "gap_to_close": {"resolution": ""},
    }


def test_rebaser_valid_rebased_instance(leerie):
    instance = {
        "status": "rebased",
        "final_branch_state": "clean, rebased onto main",
        "resolution_summary": "resolved one additive conflict",
        "diagnosis": "",
        "confidence": _full_confidence(9.5),
    }
    _validate(leerie, instance)


def test_rebaser_valid_irreconcilable_instance(leerie):
    instance = {
        "status": "irreconcilable",
        "final_branch_state": "aborted, back to original tip",
        "diagnosis": "two incompatible pricing rules on the same function",
        "confidence": _full_confidence(9.0),
    }
    _validate(leerie, instance)


def test_rebaser_valid_minimal_instance_no_optional_fields(leerie):
    instance = {
        "status": "failed",
        "final_branch_state": "fetch failed",
        "confidence": _full_confidence(1.0),
    }
    _validate(leerie, instance)


def test_rebaser_invalid_missing_status(leerie):
    instance = {
        "final_branch_state": "x",
        "confidence": _full_confidence(5.0),
    }
    if HAS_JSONSCHEMA:
        import pytest
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(instance, leerie.SCHEMAS["rebaser"])
    else:
        assert "status" not in instance


def test_rebaser_invalid_status_value(leerie):
    instance = {
        "status": "resolved",  # integrator's enum value, not rebaser's
        "final_branch_state": "x",
        "confidence": _full_confidence(5.0),
    }
    if HAS_JSONSCHEMA:
        import pytest
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(instance, leerie.SCHEMAS["rebaser"])
    else:
        assert instance["status"] not in ("rebased", "irreconcilable", "failed")


# --- JSON round-trip -----------------------------------------------------

def test_rebaser_schema_is_json_serializable(leerie):
    json.dumps(leerie.SCHEMAS["rebaser"])


# --- wiring ----------------------------------------------------------------

def test_rebaser_in_worker_types(leerie):
    assert "rebaser" in leerie.WORKER_TYPES


def test_rebaser_prompt_file_exists(leerie):
    import pathlib
    repo_root = pathlib.Path(leerie.__file__).resolve().parent.parent
    assert (repo_root / "prompts" / "rebaser.md").is_file()
