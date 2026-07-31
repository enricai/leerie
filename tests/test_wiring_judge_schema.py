"""Tests for SCHEMAS["wiring_judge"] — the independent semantic plan-wiring
verifier (DESIGN §5 *A wiring re-check on the fully-merged plan*, §8).

Mirrors test_adherence_judge_schema.py. The wiring_judge owns the SEMANTIC
wiring gaps a structural provider-existence scan (check_plan_wiring) cannot
see; it carries NO _confidence_schema and gates on a non-empty wiring_defects
array.
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
    schema = leerie.SCHEMAS["wiring_judge"]
    if HAS_JSONSCHEMA:
        jsonschema.validate(instance, schema)
        return
    for k in schema["required"]:
        assert k in instance, f"missing required field {k!r}"
    assert isinstance(instance["plan_reviewed"], bool)
    assert isinstance(instance["wiring_defects"], list)
    for d in instance["wiring_defects"]:
        for k in ("kind", "sid", "tag_or_dep", "concrete_reason", "severity"):
            assert k in d, f"wiring_defect missing {k!r}"
    assert isinstance(instance["rationale"], str)


def test_schema_exists(leerie):
    assert "wiring_judge" in leerie.SCHEMAS
    assert leerie.SCHEMAS["wiring_judge"]["type"] == "object"


def test_required_fields(leerie):
    schema = leerie.SCHEMAS["wiring_judge"]
    assert set(schema["required"]) == {
        "plan_reviewed", "wiring_defects", "rationale"}


def test_wiring_defect_item_shape(leerie):
    item = leerie.SCHEMAS["wiring_judge"]["properties"]["wiring_defects"]["items"]
    assert item["type"] == "object"
    assert set(item["required"]) == {
        "kind", "sid", "tag_or_dep", "concrete_reason", "severity"}
    assert set(item["properties"]["kind"]["enum"]) == {
        "missing_requires", "missing_provides", "broken_by_merge",
        "broken_by_drop", "orphaned_dependent"}


def test_wiring_defect_severity_enum(leerie):
    item = leerie.SCHEMAS["wiring_judge"]["properties"]["wiring_defects"]["items"]
    assert set(item["properties"]["severity"]["enum"]) == {
        "live_defect", "latent_risk"}


def test_no_confidence_subobject(leerie):
    schema = leerie.SCHEMAS["wiring_judge"]
    assert "confidence" not in schema.get("properties", {})
    assert "confidence" not in schema["required"]


def test_accepts_clean(leerie):
    _validate(leerie, {
        "plan_reviewed": True,
        "wiring_defects": [],
        "rationale": "Every real dependency is declared in some channel.",
    })


def test_accepts_missing_requires_defect(leerie):
    _validate(leerie, {
        "plan_reviewed": True,
        "wiring_defects": [{
            "kind": "missing_requires",
            "sid": "feat-003",
            "tag_or_dep": "auth-config-schema",
            "concrete_reason": "feat-003 reads the schema feat-001 provides "
                               "but declares no requires — it may run first.",
            "severity": "live_defect",
        }],
        "rationale": "One consumer does not declare its dependency.",
    })


def test_accepts_latent_risk_defect(leerie):
    """A defect the judge itself doesn't consider live (transitive coverage
    that resolves today, fragile only to a future edit) is a valid,
    schema-accepted severity — see phase_wiring_gate's _check(), which
    reads this field to decide whether to gate."""
    _validate(leerie, {
        "plan_reviewed": True,
        "wiring_defects": [{
            "kind": "missing_requires",
            "sid": "feat-003-1",
            "tag_or_dep": "uchealth-workhistory-gate-fixture",
            "concrete_reason": "feat-003-1 only declares requires on "
                               "feat-002's tag, inheriting feat-001's "
                               "fixture transitively; correct today but "
                               "fragile if feat-002 is later dropped.",
            "severity": "latent_risk",
        }],
        "rationale": "Transitive coverage resolves correctly; flagged as "
                     "a robustness note, not a live defect.",
    })


def test_rejects_missing_required_field(leerie):
    if not HAS_JSONSCHEMA:
        pytest.skip("jsonschema not available")
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(
            {"wiring_defects": [], "rationale": "x"},
            leerie.SCHEMAS["wiring_judge"],
        )


def test_rejects_bad_defect_kind(leerie):
    if not HAS_JSONSCHEMA:
        pytest.skip("jsonschema not available")
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(
            {
                "plan_reviewed": True,
                "wiring_defects": [{
                    "kind": "nonsense", "sid": "x",
                    "tag_or_dep": "y", "concrete_reason": "z",
                    "severity": "live_defect",
                }],
                "rationale": "x",
            },
            leerie.SCHEMAS["wiring_judge"],
        )


def test_rejects_defect_missing_severity(leerie):
    if not HAS_JSONSCHEMA:
        pytest.skip("jsonschema not available")
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(
            {
                "plan_reviewed": True,
                "wiring_defects": [{
                    "kind": "missing_requires", "sid": "x",
                    "tag_or_dep": "y", "concrete_reason": "z",
                }],
                "rationale": "x",
            },
            leerie.SCHEMAS["wiring_judge"],
        )


def test_rejects_bad_severity_value(leerie):
    if not HAS_JSONSCHEMA:
        pytest.skip("jsonschema not available")
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(
            {
                "plan_reviewed": True,
                "wiring_defects": [{
                    "kind": "missing_requires", "sid": "x",
                    "tag_or_dep": "y", "concrete_reason": "z",
                    "severity": "sort_of_a_defect",
                }],
                "rationale": "x",
            },
            leerie.SCHEMAS["wiring_judge"],
        )


def test_schema_round_trips(leerie):
    schema = leerie.SCHEMAS["wiring_judge"]
    assert json.loads(json.dumps(schema)) == schema


def test_in_worker_types(leerie):
    assert "wiring_judge" in leerie.WORKER_TYPES


def test_not_in_model_default_per_worker(leerie):
    assert "wiring_judge" not in leerie.MODEL_DEFAULT_PER_WORKER


def test_effort_default_is_medium(leerie):
    assert leerie.EFFORT_DEFAULT_PER_WORKER.get("wiring_judge") == "medium"


def test_prompt_file_exists(leerie):
    from pathlib import Path
    prompt = (Path(leerie.__file__).parent.parent / "prompts"
              / "wiring_judge.md")
    assert prompt.exists(), f"not found at {prompt}"
