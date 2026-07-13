"""Tests for SCHEMAS["splitter"] — the P1 splitter worker output schema.

The schema lives in SCHEMAS["splitter"] and is passed to `claude -p`
via --json-schema. These tests pin the structural contract: children array
shape, required fields on each child, and wiring into WORKER_TYPES.

Mirrors test_fit_judge_schema.py / test_dep_capture_schema.py patterns.
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
    """Validate using jsonschema when available; manual fallback otherwise."""
    schema = leerie.SCHEMAS["splitter"]
    if HAS_JSONSCHEMA:
        jsonschema.validate(instance, schema)
        return
    for k in schema["required"]:
        assert k in instance, f"missing required field {k!r}"
    assert isinstance(instance.get("children", []), list)
    assert len(instance.get("children", [])) >= 1
    for child in instance.get("children", []):
        assert isinstance(child, dict)
        for req in ("id", "title", "success_criteria_seed"):
            assert req in child, f"child missing required field {req!r}"


# --- existence and shape ---------------------------------------------------

def test_splitter_schema_exists(leerie):
    assert "splitter" in leerie.SCHEMAS
    schema = leerie.SCHEMAS["splitter"]
    assert schema["type"] == "object"


def test_splitter_schema_required_fields(leerie):
    """required must be exactly {children}."""
    schema = leerie.SCHEMAS["splitter"]
    assert set(schema["required"]) == {"children"}


def test_splitter_children_is_array(leerie):
    prop = leerie.SCHEMAS["splitter"]["properties"]["children"]
    assert prop["type"] == "array"


def test_splitter_children_min_items(leerie):
    """A split that produces 0 children is a schema error (minItems: 1)."""
    prop = leerie.SCHEMAS["splitter"]["properties"]["children"]
    assert prop.get("minItems") == 1


def test_splitter_child_required_fields(leerie):
    """Each child needs id, title, success_criteria_seed — mirrors planner."""
    item = leerie.SCHEMAS["splitter"]["properties"]["children"]["items"]
    assert set(item["required"]) == {"id", "title", "success_criteria_seed"}


def test_splitter_child_optional_fields_present(leerie):
    """Optional child fields mirror the planner subtask shape."""
    item = leerie.SCHEMAS["splitter"]["properties"]["children"]["items"]
    props = item.get("properties", {})
    for field in ("intent", "scope_note", "files_likely_touched", "depends_on",
                  "requires", "provides", "size", "investigation_notes"):
        assert field in props, f"child schema missing optional field {field!r}"


# --- valid instance acceptance ----------------------------------------------

def test_splitter_accepts_minimal_two_children(leerie):
    _validate(leerie, {
        "children": [
            {"id": "mig-1", "title": "Migrate batch A",
             "success_criteria_seed": "All batch A files use new API"},
            {"id": "mig-2", "title": "Migrate batch B",
             "success_criteria_seed": "All batch B files use new API"},
        ],
    })


def test_splitter_accepts_full_child(leerie):
    _validate(leerie, {
        "children": [
            {
                "id": "task-1-1",
                "title": "Update schema definitions",
                "success_criteria_seed": "All schema files updated, tests pass",
                "files_likely_touched": ["src/schema/user.ts", "src/schema/order.ts"],
                "intent": "Migrate schema files to v2 API shape",
                "scope_note": "Schema layer only — no migrations yet",
                "depends_on": [],
                "requires": [],
                "provides": ["schema-v2"],
                "size": "small",
                "investigation_notes": "user.ts and order.ts are co-located",
            },
            {
                "id": "task-1-2",
                "title": "Run data migrations",
                "success_criteria_seed": "Migration scripts execute successfully",
                "files_likely_touched": ["migrations/001.sql"],
                "depends_on": ["task-1-1"],
                "size": "medium",
                "success_criteria_seed": "Migration verified in staging",
            },
        ],
    })


# --- invalid instance rejection --------------------------------------------

def test_splitter_rejects_empty_children(leerie):
    if not HAS_JSONSCHEMA:
        pytest.skip("jsonschema not available; minItems check requires it")
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"children": []}, leerie.SCHEMAS["splitter"])


def test_splitter_rejects_child_missing_title(leerie):
    if not HAS_JSONSCHEMA:
        pytest.skip("jsonschema not available; required check requires it")
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(
            {"children": [{"id": "x", "success_criteria_seed": "y"}]},
            leerie.SCHEMAS["splitter"],
        )


def test_splitter_rejects_missing_children_field(leerie):
    if not HAS_JSONSCHEMA:
        pytest.skip("jsonschema not available; required check requires it")
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({}, leerie.SCHEMAS["splitter"])


# --- files field not required (splitter never decides partition) -----------

def test_splitter_no_top_level_files_required(leerie):
    """Schema must not require a top-level files field — splitter never decides partition."""
    schema = leerie.SCHEMAS["splitter"]
    assert "files" not in schema.get("required", [])
    assert "files" not in schema.get("properties", {})


def test_splitter_child_requires_item_shape(leerie):
    """Each child's requires array must use the _REQUIRES_ITEM shape (tag + extent enum)."""
    item = leerie.SCHEMAS["splitter"]["properties"]["children"]["items"]
    requires_prop = item.get("properties", {}).get("requires", {})
    assert requires_prop.get("type") == "array"
    item_schema = requires_prop.get("items", {})
    assert item_schema.get("type") == "object"
    assert "tag" in item_schema.get("required", [])
    assert "extent" in item_schema.get("required", [])
    extent_prop = item_schema.get("properties", {}).get("extent", {})
    assert "enum" in extent_prop, "extent must be an enum (in_plan / external)"


# --- JSON serializability ---------------------------------------------------

def test_splitter_schema_is_json_serializable(leerie):
    json.dumps(leerie.SCHEMAS["splitter"])


# --- wiring checks ----------------------------------------------------------

def test_splitter_in_worker_types(leerie):
    """splitter must be in WORKER_TYPES so load_prompt / claude_p accept it."""
    assert "splitter" in leerie.WORKER_TYPES


def test_splitter_not_in_model_default_per_worker(leerie):
    """splitter defaults to opus (MODEL_DEFAULT); must NOT appear in
    MODEL_DEFAULT_PER_WORKER."""
    assert "splitter" not in leerie.MODEL_DEFAULT_PER_WORKER


def test_splitter_effort_default_is_high(leerie):
    """splitter is a judgment worker — EFFORT_DEFAULT_PER_WORKER['splitter']
    must be 'high'."""
    assert leerie.EFFORT_DEFAULT_PER_WORKER.get("splitter") == "high"


def test_splitter_prompt_file_exists(leerie):
    """prompts/splitter.md must exist — load_prompt() reads it at call time."""
    from pathlib import Path
    leerie_path = Path(leerie.__file__)
    prompt = leerie_path.parent.parent / "prompts" / "splitter.md"
    assert prompt.exists(), f"prompts/splitter.md not found at {prompt}"
