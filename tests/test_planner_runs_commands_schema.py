"""Tests for SCHEMAS["planner"] subtask items' optional `runs_commands` field.

`runs_commands` declares the commands a subtask actually invokes, as
structured data, so a prescribed-procedure coverage check can set-compare
over it rather than re-interpret subtask prose. It is additive and
optional — most subtasks run no prescribed command.
"""
from __future__ import annotations

from tests.conftest import HAS_JSONSCHEMA, validate_or_fallback_required

try:
    import jsonschema  # type: ignore
except ImportError:
    pass

import pytest


def _subtask_item_schema(leerie):
    return leerie.SCHEMAS["planner"]["properties"]["subtasks"]["items"]


def _validate_subtask_item(leerie, instance: dict) -> None:
    schema = _subtask_item_schema(leerie)
    if validate_or_fallback_required(schema, instance):
        return
    if "runs_commands" in instance:
        assert isinstance(instance["runs_commands"], list)
        assert all(isinstance(c, str) for c in instance["runs_commands"])


def test_runs_commands_field_declared(leerie):
    props = _subtask_item_schema(leerie)["properties"]
    assert "runs_commands" in props
    assert props["runs_commands"]["type"] == "array"
    assert props["runs_commands"]["items"] == {"type": "string"}


def test_runs_commands_is_optional(leerie):
    required = _subtask_item_schema(leerie)["required"]
    assert "runs_commands" not in required


def test_subtask_with_runs_commands_validates(leerie):
    instance = {
        "id": "feat-001",
        "title": "Run recon:generate",
        "success_criteria_seed": "recon:generate is invoked and succeeds",
        "runs_commands": ["recon:browser", "recon:generate"],
        "change_shape": "point",
    }
    _validate_subtask_item(leerie, instance)


def test_subtask_without_runs_commands_validates(leerie):
    instance = {
        "id": "feat-002",
        "title": "Add the /volumes endpoint",
        "success_criteria_seed": "POST /volumes returns 201",
        "change_shape": "point",
    }
    _validate_subtask_item(leerie, instance)


def test_subtask_with_empty_runs_commands_validates(leerie):
    instance = {
        "id": "feat-003",
        "title": "Refactor the auth middleware",
        "success_criteria_seed": "existing auth tests still pass",
        "runs_commands": [],
        "change_shape": "point",
    }
    _validate_subtask_item(leerie, instance)


@pytest.mark.skipif(not HAS_JSONSCHEMA, reason="jsonschema not installed")
def test_runs_commands_rejects_non_string_items(leerie):
    instance = {
        "id": "feat-004",
        "title": "Bad subtask",
        "success_criteria_seed": "n/a",
        "runs_commands": [123],
        "change_shape": "point",
    }
    with pytest.raises(jsonschema.exceptions.ValidationError):
        jsonschema.validate(instance, _subtask_item_schema(leerie))
