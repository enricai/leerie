"""Tests for the length bounds on _confidence_schema()'s free-text fields
(basis, falsifiers_tested, contradictions_reconciled).

Mitigation for anthropics/claude-code#49747 (open, unfixed as of
2026-07-31): the `claude` CLI intermittently corrupts a StructuredOutput
tool call — valid JSON switching mid-payload into legacy XML
`<parameter name="...">` tags — correlated with the length of a single
tool-call string argument. Observed directly in leerie run
d8302c0d46d8... (barnacle, 2026-07-31): the raw `__unparsedToolInput`
capture showed the JSON-to-XML switch happening exactly where the
(uncapped) `confidence.basis` field's ~16KB of prose began. The cap
reduces how often a worker's own output is long enough to trigger the
bug; it does not fix the underlying CLI defect.
"""
from __future__ import annotations

import json

import pytest

try:
    import jsonschema  # type: ignore
    HAS_JSONSCHEMA = True
except ImportError:
    HAS_JSONSCHEMA = False


def _valid_confidence(leerie, axes):
    conf = {ax: 9.5 for ax in axes}
    conf.update({
        "basis": "short evidence citation",
        "falsifiers_tested": ["short falsifier"],
        "contradictions_reconciled": [],
        "gap_to_close": {},
    })
    return conf


def test_basis_has_max_length(leerie):
    schema = leerie._confidence_schema(["x"])
    assert schema["properties"]["basis"]["maxLength"] == (
        leerie._CONFIDENCE_BASIS_MAX_LENGTH)


def test_falsifiers_tested_items_have_max_length(leerie):
    schema = leerie._confidence_schema(["x"])
    item = schema["properties"]["falsifiers_tested"]["items"]
    assert item["maxLength"] == leerie._CONFIDENCE_LIST_ITEM_MAX_LENGTH


def test_contradictions_reconciled_items_have_max_length(leerie):
    schema = leerie._confidence_schema(["x"])
    item = schema["properties"]["contradictions_reconciled"]["items"]
    assert item["maxLength"] == leerie._CONFIDENCE_LIST_ITEM_MAX_LENGTH


def test_bounds_apply_across_all_nine_confidence_schemas(leerie):
    """_confidence_schema is called once per worker (planner, implementer,
    conformer, classifier, reconciler, provision, plan_overlap_judge,
    integrator, and one more per docs/IMPLEMENTATION.md) — the cap must
    reach every one of them, not just planner."""
    checked = 0
    for key, schema in leerie.SCHEMAS.items():
        conf = schema.get("properties", {}).get("confidence")
        if not conf or conf.get("type") != "object":
            continue
        if "basis" not in conf.get("properties", {}):
            continue
        assert conf["properties"]["basis"].get("maxLength") == (
            leerie._CONFIDENCE_BASIS_MAX_LENGTH), (
            f"{key} confidence.basis missing the length cap")
        checked += 1
    assert checked >= 8, (
        f"expected the cap on ~9 confidence-bearing schemas, found {checked}")


def test_accepts_short_basis(leerie):
    if not HAS_JSONSCHEMA:
        pytest.skip("jsonschema not available")
    schema = leerie._confidence_schema(["x"])
    jsonschema.validate(_valid_confidence(leerie, ["x"]), schema)


def test_rejects_oversized_basis(leerie):
    if not HAS_JSONSCHEMA:
        pytest.skip("jsonschema not available")
    schema = leerie._confidence_schema(["x"])
    instance = _valid_confidence(leerie, ["x"])
    instance["basis"] = "x" * (leerie._CONFIDENCE_BASIS_MAX_LENGTH + 1)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance, schema)


def test_rejects_oversized_falsifier_item(leerie):
    if not HAS_JSONSCHEMA:
        pytest.skip("jsonschema not available")
    schema = leerie._confidence_schema(["x"])
    instance = _valid_confidence(leerie, ["x"])
    instance["falsifiers_tested"] = [
        "x" * (leerie._CONFIDENCE_LIST_ITEM_MAX_LENGTH + 1)]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance, schema)


def test_rejects_oversized_contradiction_item(leerie):
    if not HAS_JSONSCHEMA:
        pytest.skip("jsonschema not available")
    schema = leerie._confidence_schema(["x"])
    instance = _valid_confidence(leerie, ["x"])
    instance["contradictions_reconciled"] = [
        "x" * (leerie._CONFIDENCE_LIST_ITEM_MAX_LENGTH + 1)]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance, schema)


def test_at_the_boundary_is_accepted(leerie):
    """maxLength is inclusive — exactly the cap must still pass."""
    if not HAS_JSONSCHEMA:
        pytest.skip("jsonschema not available")
    schema = leerie._confidence_schema(["x"])
    instance = _valid_confidence(leerie, ["x"])
    instance["basis"] = "x" * leerie._CONFIDENCE_BASIS_MAX_LENGTH
    jsonschema.validate(instance, schema)


def test_schema_round_trips(leerie):
    schema = leerie._confidence_schema(["x"])
    assert json.loads(json.dumps(schema)) == schema
