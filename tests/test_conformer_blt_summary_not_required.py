"""Pins that `_CONFORMER_BLT_PROP["required"]` no longer forces `summary`
on the conformer schema's build/lint/tests axis objects (bugfix-002),
while `summary` remains a valid optional property.

Mirrors test_adherence_judge_schema.py's HAS_JSONSCHEMA gate + fallback
pattern.
"""
from __future__ import annotations

import pytest

try:
    import jsonschema  # type: ignore
except ImportError:
    jsonschema = None  # type: ignore

from tests.conftest import HAS_JSONSCHEMA


def _minimal_conformer_instance(axis_without_summary: dict) -> dict:
    return {
        "subtask_id": "bugfix-002",
        "rules_files_read": [],
        "rule_violations": [],
        "file_updates": [],
        "build": axis_without_summary,
        "lint": axis_without_summary,
        "tests": axis_without_summary,
        "summary": "did the thing",
        "confidence": {
            "conformance": 9.0,
            "basis": "checked the diff",
        },
        "solution_defects": [],
    }


def test_conformer_blt_prop_required_omits_summary(leerie):
    assert "summary" not in leerie._CONFORMER_BLT_PROP["required"]


def test_conformer_blt_prop_properties_still_has_summary(leerie):
    assert "summary" in leerie._CONFORMER_BLT_PROP["properties"]


def test_conformer_schema_accepts_blt_axis_without_summary(leerie):
    axis = {"ran": False, "passed": False, "command": "(none)"}
    instance = _minimal_conformer_instance(axis)
    if not HAS_JSONSCHEMA:
        for k in leerie._CONFORMER_BLT_PROP["required"]:
            assert k in axis, f"missing required field {k!r}"
        return
    jsonschema.validate(instance, leerie.SCHEMAS["conformer"])


def test_conformer_schema_still_accepts_blt_axis_with_summary(leerie):
    axis = {
        "ran": True,
        "passed": True,
        "command": "pytest",
        "summary": "all green",
    }
    instance = _minimal_conformer_instance(axis)
    if not HAS_JSONSCHEMA:
        pytest.skip("jsonschema not available")
    jsonschema.validate(instance, leerie.SCHEMAS["conformer"])
