"""Regression pin: `confidence` is advisory across worker schemas.

`confidence` is a self-report field no control flow gates on (the only
consumer, `_format_blocked_gap`, reads it via `plan.get("confidence")` and
handles `None`). Requiring it on a schema caused the CLI to reject and
retry otherwise-valid worker output whenever a worker omitted it. This
test pins that `confidence` stays declared in `properties` (so a worker
that does emit it is still recorded/displayed) but is absent from
`required` for every schema listed below.
"""
from __future__ import annotations

import json

try:
    import jsonschema  # type: ignore
    HAS_JSONSCHEMA = True
except ImportError:
    HAS_JSONSCHEMA = False


CONFIDENCE_OPTIONAL_SCHEMAS = [
    "classifier",
    "planner",
    "reconciler",
    "implementer",
    "integrator",
    "rebaser",
    "conformer",
    "provision",
    "plan_overlap_judge",
    "fit_judge",
]


def test_confidence_absent_from_required_present_in_properties(leerie):
    for name in CONFIDENCE_OPTIONAL_SCHEMAS:
        schema = leerie.SCHEMAS[name]
        assert "confidence" not in schema.get("required", []), (
            f"{name}: confidence must not be required"
        )
        assert "confidence" in schema.get("properties", {}), (
            f"{name}: confidence must still be declared in properties"
        )


# Minimal valid instances for each schema, omitting `confidence` entirely,
# built from each schema's own `required` list plus the minimal shape its
# other required fields need (arrays required-empty unless the schema
# itself demands non-empty items).
_FIXTURES = {
    "classifier": {"categories": ["bug-fixing"]},
    "planner": {"domain": "bugfix", "subtasks": [], "status": "ready"},
    "reconciler": {
        "added_subtasks": [],
        "added_requires": [],
        "tag_ops": [],
        "renames": [],
        "dependency_edges": [],
        "merged_subtasks": [],
    },
    "implementer": {"subtask_id": "bugfix-001", "status": "complete"},
    "integrator": {"incoming_subtask": "bugfix-001", "status": "resolved"},
    "rebaser": {"status": "rebased", "final_branch_state": "rebased"},
    "conformer": {
        "subtask_id": "bugfix-001",
        "rules_files_read": [],
        "rule_violations_fixed": [],
        "rule_violations_residual": [],
        "docs_updates": [],
        "tests_updates": [],
        "build": {"ran": True, "passed": True, "command": "true", "summary": "ok"},
        "lint": {"ran": True, "passed": True, "command": "true", "summary": "ok"},
        "tests": {"ran": True, "passed": True, "command": "true", "summary": "ok"},
        "summary": "ok",
        "solution_defects": [],
    },
    "provision": {"recipe": []},
    "plan_overlap_judge": {"collisions": []},
    "fit_judge": {"score": 0.9, "rationale": "well scoped", "diffuse": "no"},
}


def test_confidence_optional_schemas_have_a_fixture():
    assert set(_FIXTURES) == set(CONFIDENCE_OPTIONAL_SCHEMAS)


def test_schemas_validate_without_confidence(leerie):
    for name, instance in _FIXTURES.items():
        schema = leerie.SCHEMAS[name]
        assert "confidence" not in instance
        if HAS_JSONSCHEMA:
            jsonschema.validate(instance, schema)
        else:
            for k in schema.get("required", []):
                assert k in instance, f"{name}: missing required field {k}"


def test_schemas_are_json_serializable(leerie):
    for name in CONFIDENCE_OPTIONAL_SCHEMAS:
        json.dumps(leerie.SCHEMAS[name])
