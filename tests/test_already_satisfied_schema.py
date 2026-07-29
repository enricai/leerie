"""Tests for SCHEMAS["classifier"]["properties"]["likely_already_satisfied"] /
["likely_already_satisfied_evidence"] — the additive, optional signal the
classifier's own investigation surfaces when the task's described
deliverable already appears present on HEAD (DESIGN §8 *Reaching the
cleared-but-empty state from classification*).

Mirrors test_prescribed_procedure_schema.py's structure (same schema, a
sibling optional field pair) and its HAS_JSONSCHEMA-gated structural
fallback.
"""
from __future__ import annotations

import inspect
import json

import pytest

try:
    import jsonschema  # type: ignore
    HAS_JSONSCHEMA = True
except ImportError:
    HAS_JSONSCHEMA = False


def _validate(leerie, instance: dict) -> None:
    schema = leerie.SCHEMAS["classifier"]
    if HAS_JSONSCHEMA:
        jsonschema.validate(instance, schema)
        return
    for k in schema["required"]:
        assert k in instance, f"missing required field {k!r}"
    if "likely_already_satisfied" in instance:
        assert isinstance(instance["likely_already_satisfied"], bool)
    if "likely_already_satisfied_evidence" in instance:
        assert isinstance(instance["likely_already_satisfied_evidence"], str)


def _conf(**axes):
    return {**axes, "basis": "test", "falsifiers_tested": [],
            "contradictions_reconciled": [], "gap_to_close": {}}


# --- schema shape ------------------------------------------------------- #

def test_likely_already_satisfied_field_exists(leerie):
    props = leerie.SCHEMAS["classifier"]["properties"]
    assert "likely_already_satisfied" in props
    assert props["likely_already_satisfied"]["type"] == "boolean"


def test_likely_already_satisfied_evidence_field_exists(leerie):
    props = leerie.SCHEMAS["classifier"]["properties"]
    assert "likely_already_satisfied_evidence" in props
    assert props["likely_already_satisfied_evidence"]["type"] == "string"


def test_likely_already_satisfied_not_required(leerie):
    """Optional and additive: a classifier that never sets it (the common
    case) must not fail validation."""
    required = leerie.SCHEMAS["classifier"]["required"]
    assert "likely_already_satisfied" not in required
    assert "likely_already_satisfied_evidence" not in required


# --- valid instance acceptance ------------------------------------------ #

def test_classifier_schema_accepts_likely_already_satisfied_true_with_evidence(
        leerie):
    _validate(leerie, {
        "categories": ["bug-fixing"],
        "confidence": _conf(classification=9.0),
        "likely_already_satisfied": True,
        "likely_already_satisfied_evidence": (
            "resolveActAsFdUser is wired into all 4 target routes, "
            "act-as tests exist under src/tests/app/api/v1/{users,"
            "account,auth}/, and prisma/seed.ts already creates the "
            "admin/staff FdUsers, orders, guestbook, and invoices."),
    })


def test_classifier_schema_accepts_likely_already_satisfied_false(leerie):
    _validate(leerie, {
        "categories": ["feature-implementation"],
        "confidence": _conf(classification=9.0),
        "likely_already_satisfied": False,
        "likely_already_satisfied_evidence": "",
    })


def test_classifier_schema_accepts_missing_likely_already_satisfied(leerie):
    """The common case: the classifier omits both fields entirely (a task
    that is not already done). Must still validate — schema-optional."""
    _validate(leerie, {
        "categories": ["bug-fixing"],
        "confidence": _conf(classification=9.0),
    })


# --- round-trip and serialization --------------------------------------- #

def test_classifier_schema_is_json_serializable_with_new_fields(leerie):
    json.dumps(leerie.SCHEMAS["classifier"])


def test_classifier_schema_round_trips_with_new_fields(leerie):
    schema = leerie.SCHEMAS["classifier"]
    assert json.loads(json.dumps(schema)) == schema


# --- check_classifier_output: EMPTY_EVIDENCE mechanical check ----------- #
# Mirrors the existing prescribed_procedure.is_prescribed → evidence
# discipline (leerie.py check_classifier_output, EMPTY_EVIDENCE).

def test_check_output_flags_true_with_empty_evidence(leerie, tmp_path):
    issues = leerie.check_classifier_output(
        {"categories": ["bug-fixing"],
         "likely_already_satisfied": True,
         "likely_already_satisfied_evidence": ""},
        tmp_path)
    assert any("EMPTY_EVIDENCE" in i and "likely_already_satisfied" in i
               for i in issues)


def test_check_output_flags_true_with_whitespace_only_evidence(leerie, tmp_path):
    issues = leerie.check_classifier_output(
        {"categories": ["bug-fixing"],
         "likely_already_satisfied": True,
         "likely_already_satisfied_evidence": "   "},
        tmp_path)
    assert any("EMPTY_EVIDENCE" in i and "likely_already_satisfied" in i
               for i in issues)


def test_check_output_accepts_true_with_evidence(leerie, tmp_path):
    issues = leerie.check_classifier_output(
        {"categories": ["bug-fixing"],
         "likely_already_satisfied": True,
         "likely_already_satisfied_evidence": "route.ts already has the fix"},
        tmp_path)
    assert not any("likely_already_satisfied" in i for i in issues)


def test_check_output_silent_when_false(leerie, tmp_path):
    issues = leerie.check_classifier_output(
        {"categories": ["bug-fixing"],
         "likely_already_satisfied": False,
         "likely_already_satisfied_evidence": ""},
        tmp_path)
    assert not any("likely_already_satisfied" in i for i in issues)


def test_check_output_silent_when_absent(leerie, tmp_path):
    """Neither field present at all — the common case — must not gate."""
    issues = leerie.check_classifier_output(
        {"categories": ["bug-fixing"]}, tmp_path)
    assert not any("likely_already_satisfied" in i for i in issues)


# --- persistence wiring (source-coupling, mirrors test_prescribed_procedure) #

def test_phase_classify_persists_likely_already_satisfied(leerie):
    """phase_classify must persist both new fields to st.data on every
    invocation (default False / ""), alongside 'categories'. Source-coupled
    since phase_classify calls claude_p()."""
    src = inspect.getsource(leerie.phase_classify)
    assert 'st.data["categories"] = cats' in src
    assert 'st.data["likely_already_satisfied"]' in src
    assert 'st.data["likely_already_satisfied_evidence"]' in src
    idx_categories = src.index('st.data["categories"] = cats')
    idx_satisfied = src.index('st.data["likely_already_satisfied"]')
    idx_save = src.rindex("st.save()")
    assert idx_categories < idx_save
    assert idx_satisfied < idx_save


def test_phase_classify_likely_already_satisfied_defaults_false(leerie):
    src = inspect.getsource(leerie.phase_classify)
    assert 'bool(\n        result.get("likely_already_satisfied"))' in src \
        or 'bool(result.get("likely_already_satisfied"))' in src


# --- STATE_FIELDS parity (CLAUDE.md contributor discipline) ------------- #

def test_state_fields_declares_likely_already_satisfied(leerie):
    assert "likely_already_satisfied" in leerie.STATE_FIELDS
    assert "likely_already_satisfied_evidence" in leerie.STATE_FIELDS
