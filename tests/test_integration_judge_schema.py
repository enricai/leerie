"""Tests for SCHEMAS["integration_judge"] — the independent behavioral
merge-integration verifier (DESIGN §8 *Independent adversarial
verification*).

Mirrors test_wiring_judge_schema.py / test_task_coverage_judge_schema.py.
The integration_judge owns the BEHAVIORAL breakage a conflict-marker scan
and merge-committed check cannot see; it carries NO _confidence_schema and
gates on a non-empty `defects` array.
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
    schema = leerie.SCHEMAS["integration_judge"]
    if HAS_JSONSCHEMA:
        jsonschema.validate(instance, schema)
        return
    for k in schema["required"]:
        assert k in instance, f"missing required field {k!r}"
    assert isinstance(instance["merge_reviewed"], bool)
    assert isinstance(instance["defects"], list)
    for d in instance["defects"]:
        for k in ("kind", "concrete_scenario", "location", "why_broken"):
            assert k in d, f"defect missing {k!r}"
    assert isinstance(instance["rationale"], str)


def test_schema_exists(leerie):
    assert "integration_judge" in leerie.SCHEMAS
    assert leerie.SCHEMAS["integration_judge"]["type"] == "object"


def test_required_fields(leerie):
    schema = leerie.SCHEMAS["integration_judge"]
    assert set(schema["required"]) == {
        "merge_reviewed", "defects", "rationale"}


def test_defect_item_shape(leerie):
    item = leerie.SCHEMAS["integration_judge"]["properties"]["defects"]["items"]
    assert item["type"] == "object"
    assert set(item["required"]) == {
        "kind", "concrete_scenario", "location", "why_broken"}
    assert set(item["properties"]["kind"]["enum"]) == {
        "dropped_change", "reintroduced_conflict", "call_site_mismatch",
        "semantic_regression", "incomplete_resolution"}


def test_no_confidence_subobject(leerie):
    schema = leerie.SCHEMAS["integration_judge"]
    assert "confidence" not in schema.get("properties", {})
    assert "confidence" not in schema["required"]


def test_accepts_clean(leerie):
    _validate(leerie, {
        "merge_reviewed": True,
        "defects": [],
        "rationale": "The merge correctly combines both sides' changes.",
    })


def test_accepts_defect_case(leerie):
    _validate(leerie, {
        "merge_reviewed": True,
        "defects": [{
            "kind": "call_site_mismatch",
            "concrete_scenario": "feat-003 renamed validate_login(form) to "
                                  "validate_login(form, strict=False); the "
                                  "merge kept the rename but a call site "
                                  "from feat-005 still calls it with one arg.",
            "location": "auth_handlers.py:142",
            "why_broken": "This call now raises TypeError at runtime.",
        }],
        "rationale": "One call site was not updated to match a signature "
                     "change from the other branch.",
    })


def test_rejects_missing_required_field(leerie):
    if not HAS_JSONSCHEMA:
        pytest.skip("jsonschema not available")
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(
            {"defects": [], "rationale": "x"},
            leerie.SCHEMAS["integration_judge"],
        )


def test_rejects_bad_defect_kind(leerie):
    if not HAS_JSONSCHEMA:
        pytest.skip("jsonschema not available")
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(
            {
                "merge_reviewed": True,
                "defects": [{
                    "kind": "nonsense", "concrete_scenario": "x",
                    "location": "y", "why_broken": "z",
                }],
                "rationale": "x",
            },
            leerie.SCHEMAS["integration_judge"],
        )


def test_schema_round_trips(leerie):
    schema = leerie.SCHEMAS["integration_judge"]
    assert json.loads(json.dumps(schema)) == schema


def test_in_worker_types(leerie):
    assert "integration_judge" in leerie.WORKER_TYPES


def test_not_in_model_default_per_worker(leerie):
    assert "integration_judge" not in leerie.MODEL_DEFAULT_PER_WORKER


def test_effort_default_is_medium(leerie):
    assert leerie.EFFORT_DEFAULT_PER_WORKER.get("integration_judge") == "medium"


def test_prompt_file_exists(leerie):
    from pathlib import Path
    prompt = (Path(leerie.__file__).parent.parent / "prompts"
              / "integration_judge.md")
    assert prompt.exists(), f"not found at {prompt}"
