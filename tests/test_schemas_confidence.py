"""Structural tests for the confidence/status fields on all worker schemas
(DESIGN §8).

The point of pinning these structural contracts is mechanical enforcement
of DESIGN §12 / §8: a worker that skipped self-gating fails its own JSON
schema before the orchestrator sees the payload. If a future change removes
one of these fields without an accompanying DESIGN update, this test catches
it.

**Scope narrowed 2026-08-03.** The structural part of the gate is now the
numeric score *axes* plus `basis` — the number every §8 gate actually reads,
and the evidence it is anchored to. `falsifiers_tested` and
`contradictions_reconciled` remain schema *properties* and remain asked for by
every prompt, but are no longer `required`, and `gap_to_close` is gone
entirely. Requiring all four made the block match `anthropics/claude-code#49747`'s
trigger profile field-for-field; a live A/B measured 8/8 submissions corrupted
with that shape versus 0/8 without it. A required field that reliably
annihilates the payload it validates — including the score the gate reads —
enforces nothing. See `test_confidence_length_caps.py` for the full contract.
"""
from __future__ import annotations

import pytest


def test_planner_schema_top_level_required(leerie):
    """Planner must emit domain, subtasks, status, and confidence."""
    planner = leerie.SCHEMAS["planner"]
    required = set(planner["required"])
    assert {"domain", "subtasks", "status", "confidence"}.issubset(required)


def test_planner_schema_status_enum(leerie):
    """status is the ready/blocked enum."""
    status = leerie.SCHEMAS["planner"]["properties"]["status"]
    assert status["type"] == "string"
    assert set(status["enum"]) == {"ready", "blocked"}


def test_planner_schema_confidence_required_fields(leerie):
    """The score axes and `basis` are required-when-confidence-is-present.
    Combined with confidence being top-level required, a planner that emitted
    no score, or a score with no evidential basis, fails its own schema."""
    conf = leerie.SCHEMAS["planner"]["properties"]["confidence"]
    assert conf["type"] == "object"
    required = set(conf["required"])
    expected = {"task_understanding", "decomposition_quality", "basis"}
    assert expected.issubset(required)
    for relaxed in ("falsifiers_tested", "contradictions_reconciled"):
        assert relaxed in conf["properties"], f"{relaxed} deleted, not relaxed"
        assert relaxed not in required


def test_planner_confidence_axes_are_numbers(leerie):
    props = leerie.SCHEMAS["planner"]["properties"]["confidence"]["properties"]
    assert props["task_understanding"]["type"] == "number"
    assert props["decomposition_quality"]["type"] == "number"


def test_implementer_schema_top_level_required(leerie):
    """Implementer must emit subtask_id, status, and confidence."""
    impl = leerie.SCHEMAS["implementer"]
    required = set(impl["required"])
    assert {"subtask_id", "status", "confidence"}.issubset(required)


def test_implementer_schema_confidence_required_fields(leerie):
    """Same contract as the planner's (DESIGN §8 — same disciplines,
    different axes): axes + `basis` required, the arrays optional."""
    conf = leerie.SCHEMAS["implementer"]["properties"]["confidence"]
    assert conf["type"] == "object"
    required = set(conf["required"])
    assert {"root_cause", "solution", "basis"}.issubset(required)
    for relaxed in ("falsifiers_tested", "contradictions_reconciled"):
        assert relaxed in conf["properties"] and relaxed not in required


def test_implementer_confidence_axes_are_numbers(leerie):
    props = leerie.SCHEMAS["implementer"]["properties"]["confidence"]["properties"]
    assert props["root_cause"]["type"] == "number"
    assert props["solution"]["type"] == "number"


def test_conformer_schema_top_level_required(leerie):
    """Conformer must emit confidence (the §8 self-gate). Same
    structural enforcement as planner/implementer — the orchestrator
    does not read it, but the schema rejects payloads that skip it."""
    conf = leerie.SCHEMAS["conformer"]
    required = set(conf["required"])
    assert "confidence" in required


def test_conformer_schema_confidence_required_fields(leerie):
    """Same contract as planner/implementer (DESIGN §8 — same disciplines,
    different axes): axes + `basis` required, the arrays optional."""
    conf = leerie.SCHEMAS["conformer"]["properties"]["confidence"]
    assert conf["type"] == "object"
    required = set(conf["required"])
    assert {"conformance", "basis"}.issubset(required)
    for relaxed in ("falsifiers_tested", "contradictions_reconciled"):
        assert relaxed in conf["properties"] and relaxed not in required


def test_gap_to_close_is_absent_from_every_confidence_block(leerie):
    """Replaces the old axis-mirroring test. `gap_to_close` was the block's
    only nested object — the sharpest edge of #49747's trigger profile — and
    nothing decided anything on it (its sole consumer was a diagnostic log
    line naming a blocked planner's gap, now reading `confidence.basis`),
    so relaxing it to optional would have kept the cost and none of the value.
    It is removed outright."""
    for worker in ("planner", "implementer", "conformer", "classifier",
                   "reconciler", "provision", "plan_overlap_judge",
                   "integrator", "fit_judge"):
        conf = leerie.SCHEMAS[worker]["properties"]["confidence"]
        assert "gap_to_close" not in conf["properties"], worker
        assert "gap_to_close" not in set(conf["required"]), worker


# --- New schemas: classifier, reconciler, provision, overlap judge, integrator ---

# The structural gate is the axes plus `basis`. The two discipline arrays are
# asked for by every prompt and kept as properties, but no longer required.
_REQUIRED_FIELDS = {"basis"}
_OPTIONAL_DISCIPLINE_FIELDS = {"falsifiers_tested", "contradictions_reconciled"}


def _assert_confidence_schema(leerie, schema_key: str, axes: list[str]):
    """Shared structural assertions for any confidence schema."""
    schema = leerie.SCHEMAS[schema_key]
    assert "confidence" in set(schema["required"]), (
        f"{schema_key} must require confidence at the top level")
    conf = schema["properties"]["confidence"]
    assert conf["type"] == "object"
    required = set(conf["required"])
    assert _REQUIRED_FIELDS.issubset(required), (
        f"{schema_key} confidence missing required fields: "
        f"{_REQUIRED_FIELDS - required}")
    for relaxed in _OPTIONAL_DISCIPLINE_FIELDS:
        assert relaxed in conf["properties"], (
            f"{schema_key} deleted {relaxed} rather than relaxing it")
        assert relaxed not in required, (
            f"{schema_key} still requires {relaxed}")
    for ax in axes:
        assert ax in required, f"{schema_key} confidence missing axis {ax!r}"
        assert conf["properties"][ax]["type"] == "number", (
            f"{schema_key} confidence.{ax} must be a number")
    assert "gap_to_close" not in conf["properties"], (
        f"{schema_key} reintroduced gap_to_close")


@pytest.mark.parametrize("schema_key, axes", [
    ("classifier", ["classification"]),
    ("reconciler", ["reconciliation"]),
    ("provision", ["recipe_correctness"]),
    ("plan_overlap_judge", ["judgment"]),
    ("integrator", ["resolution"]),
])
def test_new_schema_confidence_structure(leerie, schema_key, axes):
    """Every worker schema has a required confidence object with the §8
    discipline fields and worker-specific numeric score axes."""
    _assert_confidence_schema(leerie, schema_key, axes)


def test_confidence_schema_helper_produces_correct_structure(leerie):
    """The _confidence_schema helper builds the same shape regardless
    of the number of axes."""
    single = leerie._confidence_schema(["x"])
    assert set(single["required"]) == {"x", "basis"}
    assert single["properties"]["x"]["type"] == "number"
    assert "gap_to_close" not in single["properties"]

    multi = leerie._confidence_schema(["a", "b", "c"])
    assert set(multi["required"]) == {"a", "b", "c", "basis"}
    for ax in ("a", "b", "c"):
        assert multi["properties"][ax]["type"] == "number"
    assert "gap_to_close" not in multi["properties"]
