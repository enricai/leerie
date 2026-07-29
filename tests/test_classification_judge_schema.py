"""Tests for SCHEMAS["classification_judge"] — the independent classifier
category-coverage verifier (DESIGN §8 *Independent adversarial verification*).

Mirrors test_adherence_judge_schema.py: a HAS_JSONSCHEMA gate with a manual
structural fallback. The load-bearing assertion is that this worker carries NO
_confidence_schema sub-object (it IS the independent check) and gates on a
non-empty miscategorizations array, not a score.
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
    schema = leerie.SCHEMAS["classification_judge"]
    if HAS_JSONSCHEMA:
        jsonschema.validate(instance, schema)
        return
    for k in schema["required"]:
        assert k in instance, f"missing required field {k!r}"
    assert isinstance(instance["categories_reviewed"], list)
    assert isinstance(instance["miscategorizations"], list)
    for m in instance["miscategorizations"]:
        for k in ("kind", "category", "concrete_work_evidence"):
            assert k in m, f"miscategorization missing {k!r}"
    assert isinstance(instance["rationale"], str)


# --- existence and shape ---------------------------------------------------

def test_schema_exists(leerie):
    assert "classification_judge" in leerie.SCHEMAS
    assert leerie.SCHEMAS["classification_judge"]["type"] == "object"


def test_required_fields(leerie):
    schema = leerie.SCHEMAS["classification_judge"]
    assert set(schema["required"]) == {
        "categories_reviewed", "miscategorizations", "rationale",
    }


def test_miscategorization_item_shape(leerie):
    item = (leerie.SCHEMAS["classification_judge"]["properties"]
            ["miscategorizations"]["items"])
    assert item["type"] == "object"
    assert set(item["required"]) == {
        "kind", "category", "concrete_work_evidence"}
    assert set(item["properties"]["kind"]["enum"]) == {
        "missing_category", "spurious_category"}


def test_no_confidence_subobject(leerie):
    """The load-bearing mirror-of-adherence_judge assertion: an independent
    verifier must NOT carry a _confidence_schema self-grade."""
    schema = leerie.SCHEMAS["classification_judge"]
    assert "confidence" not in schema.get("properties", {})
    assert "confidence" not in schema["required"]


# --- valid / invalid instances ---------------------------------------------

def test_accepts_clean_empty_miscategorizations(leerie):
    _validate(leerie, {
        "categories_reviewed": ["bug-fixing", "testing"],
        "miscategorizations": [],
        "rationale": "The set covers the bug fix and its regression test.",
    })


def test_accepts_missing_category_defect(leerie):
    _validate(leerie, {
        "categories_reviewed": ["documentation"],
        "miscategorizations": [{
            "kind": "missing_category",
            "category": "feature-implementation",
            "concrete_work_evidence": "Task ships a landing page (UI feature) "
                                      "but the set is documentation-only.",
        }],
        "rationale": "The primary deliverable has no category.",
    })


def test_rejects_missing_required_field(leerie):
    if not HAS_JSONSCHEMA:
        pytest.skip("jsonschema not available")
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(
            {"miscategorizations": [], "rationale": "x"},
            leerie.SCHEMAS["classification_judge"],
        )


def test_rejects_bad_miscategorization_kind(leerie):
    if not HAS_JSONSCHEMA:
        pytest.skip("jsonschema not available")
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(
            {
                "categories_reviewed": [],
                "miscategorizations": [{
                    "kind": "not_a_kind",
                    "category": "testing",
                    "concrete_work_evidence": "x",
                }],
                "rationale": "x",
            },
            leerie.SCHEMAS["classification_judge"],
        )


# --- serializability --------------------------------------------------------

def test_schema_round_trips(leerie):
    schema = leerie.SCHEMAS["classification_judge"]
    assert json.loads(json.dumps(schema)) == schema


# --- wiring -----------------------------------------------------------------

def test_in_worker_types(leerie):
    assert "classification_judge" in leerie.WORKER_TYPES


def test_not_in_model_default_per_worker(leerie):
    """Defaults to sonnet via the global MODEL_DEFAULT fallback."""
    assert "classification_judge" not in leerie.MODEL_DEFAULT_PER_WORKER


def test_effort_default_is_medium(leerie):
    assert leerie.EFFORT_DEFAULT_PER_WORKER.get(
        "classification_judge") == "medium"


def test_prompt_file_exists(leerie):
    from pathlib import Path
    prompt = (Path(leerie.__file__).parent.parent / "prompts"
              / "classification_judge.md")
    assert prompt.exists(), f"not found at {prompt}"
