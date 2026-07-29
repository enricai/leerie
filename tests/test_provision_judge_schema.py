"""Tests for SCHEMAS["provision_judge"] — the independent install-recipe
verifier (DESIGN §8 *Independent adversarial verification*, §6½).

Mirrors test_adherence_judge_schema.py. Carries NO _confidence_schema; gates
on a non-empty recipe_failures array (a command that would fail on the image).
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
    schema = leerie.SCHEMAS["provision_judge"]
    if HAS_JSONSCHEMA:
        jsonschema.validate(instance, schema)
        return
    for k in schema["required"]:
        assert k in instance, f"missing required field {k!r}"
    assert isinstance(instance["recipe_reviewed"], bool)
    assert isinstance(instance["recipe_failures"], list)
    for f in instance["recipe_failures"]:
        for k in ("kind", "command", "concrete_reason", "fix"):
            assert k in f, f"recipe_failure missing {k!r}"
    assert isinstance(instance["rationale"], str)


def test_schema_exists(leerie):
    assert "provision_judge" in leerie.SCHEMAS
    assert leerie.SCHEMAS["provision_judge"]["type"] == "object"


def test_required_fields(leerie):
    schema = leerie.SCHEMAS["provision_judge"]
    assert set(schema["required"]) == {
        "recipe_reviewed", "recipe_failures", "rationale"}


def test_recipe_failure_item_shape(leerie):
    item = (leerie.SCHEMAS["provision_judge"]["properties"]
            ["recipe_failures"]["items"])
    assert item["type"] == "object"
    assert set(item["required"]) == {
        "kind", "command", "concrete_reason", "fix"}
    assert set(item["properties"]["kind"]["enum"]) == {
        "missing_break_system_packages", "wrong_package_manager",
        "lockfile_mismatch", "missing_runtime_dep", "wrong_image_assumption"}


def test_no_confidence_subobject(leerie):
    schema = leerie.SCHEMAS["provision_judge"]
    assert "confidence" not in schema.get("properties", {})
    assert "confidence" not in schema["required"]


def test_accepts_clean(leerie):
    _validate(leerie, {
        "recipe_reviewed": True,
        "recipe_failures": [],
        "rationale": "Every command runs on the image.",
    })


def test_accepts_break_system_packages_defect(leerie):
    _validate(leerie, {
        "recipe_reviewed": True,
        "recipe_failures": [{
            "kind": "missing_break_system_packages",
            "command": "pip install -r requirements.txt",
            "concrete_reason": "Debian 13 system Python is externally-managed "
                               "(PEP 668); bare pip install fails.",
            "fix": "pip install --break-system-packages -r requirements.txt",
        }],
        "rationale": "The pip install lacks --break-system-packages.",
    })


def test_rejects_missing_required_field(leerie):
    if not HAS_JSONSCHEMA:
        pytest.skip("jsonschema not available")
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(
            {"recipe_failures": [], "rationale": "x"},
            leerie.SCHEMAS["provision_judge"],
        )


def test_rejects_bad_failure_kind(leerie):
    if not HAS_JSONSCHEMA:
        pytest.skip("jsonschema not available")
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(
            {
                "recipe_reviewed": True,
                "recipe_failures": [{
                    "kind": "nope", "command": "x",
                    "concrete_reason": "y", "fix": "z",
                }],
                "rationale": "x",
            },
            leerie.SCHEMAS["provision_judge"],
        )


def test_schema_round_trips(leerie):
    schema = leerie.SCHEMAS["provision_judge"]
    assert json.loads(json.dumps(schema)) == schema


def test_in_worker_types(leerie):
    assert "provision_judge" in leerie.WORKER_TYPES


def test_not_in_model_default_per_worker(leerie):
    assert "provision_judge" not in leerie.MODEL_DEFAULT_PER_WORKER


def test_effort_default_is_medium(leerie):
    assert leerie.EFFORT_DEFAULT_PER_WORKER.get("provision_judge") == "medium"


def test_prompt_file_exists(leerie):
    from pathlib import Path
    prompt = (Path(leerie.__file__).parent.parent / "prompts"
              / "provision_judge.md")
    assert prompt.exists(), f"not found at {prompt}"
