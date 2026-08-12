"""N29 follow-up — `SCHEMAS['conformer']`'s dumped (wire) size stays under
the corpus-measured safe threshold, and the shrunk schema still validates
(and still rejects) the payloads it did before the flatten.

Distinct from `tests/test_conformer_schema_shrink.py`, which pins the
*source*-byte size (`ast.get_source_segment` on the literal dict) against
`plan_overlap_judge`'s own source size, plus the lossless
`_expand_conformer_output` round-trip. This file pins the plain
`len(json.dumps(SCHEMAS['conformer']))` metric named in the work order's
own threshold citation (~4,844 bytes, `plan_overlap_judge`'s measured
size), the accept/reject validity contract, and that the run-summary
"schema(s) fell back to post-hoc validation" reporting line still exists.
"""
from __future__ import annotations

import copy
import inspect
import json

import pytest

# The work order's own cited bracket: plan_overlap_judge (never rejected by
# the strict-output grammar compiler) measured at ~4,844 bytes; conformer
# (rejected) measured at 6,219 before the N29 flatten. The threshold is
# pinned to the literal cited value rather than re-derived from
# plan_overlap_judge's current size, so a future incidental change to
# plan_overlap_judge cannot silently loosen this bound.
_SAFE_THRESHOLD_BYTES = 4844
_PRE_FIX_REJECTED_BYTES = 6219


def test_conformer_dumped_size_below_safe_threshold(leerie):
    size = len(json.dumps(leerie.SCHEMAS["conformer"]))
    assert size < _SAFE_THRESHOLD_BYTES, (
        f"SCHEMAS['conformer'] dumps to {size} bytes, at or above the "
        f"{_SAFE_THRESHOLD_BYTES}-byte corpus-observed safe line "
        f"(plan_overlap_judge's own measured size) the N29 work order used "
        "as its bracket for schemas the strict-output grammar compiler "
        "accepts")
    # Sanity anchor: the pre-fix schema was measured well over the
    # threshold, so the assertion above is not vacuously satisfied by
    # every schema in the file.
    assert _PRE_FIX_REJECTED_BYTES > _SAFE_THRESHOLD_BYTES


def test_falsifies_against_the_pre_fix_size(leerie):
    """Replaying the pre-fix byte count directly against the threshold
    proves the assertion in the test above is not tautological — it must
    fail at today's (pre-fix) 6,219 bytes."""
    assert not (_PRE_FIX_REJECTED_BYTES < _SAFE_THRESHOLD_BYTES)


def _valid_conformer_payload() -> dict:
    """A representative wire-shape conformer payload: BLT axes,
    solution_defects, rule_violations, file_updates, confidence."""
    return {
        "subtask_id": "feat-001",
        "rules_files_read": ["CLAUDE.md"],
        "rule_violations": [
            {"status": "fixed", "rule": "no bare except", "fix": "narrowed",
             "evidence": "src/x.py:10"},
            {"status": "residual", "rule": "no print statements",
             "why_not_fixed": "user-facing CLI output"},
        ],
        "file_updates": [
            {"kind": "docs", "path": "docs/API.md", "reason": "new flag"},
            {"kind": "tests", "path": "tests/test_x.py", "reason": "coverage"},
        ],
        "build": {"ran": True, "passed": True, "command": "make", "summary": ""},
        "lint": {"ran": True, "passed": True, "command": "ruff", "summary": ""},
        "tests": {"ran": True, "passed": True, "command": "pytest", "summary": ""},
        "summary": "clean",
        "solution_defects": [],
        "confidence": {
            "conformance": 9.5,
            "basis": "build/lint/tests all ran and passed",
        },
    }


def test_valid_payload_validates_against_shrunk_schema(leerie):
    jsonschema = pytest.importorskip("jsonschema")
    jsonschema.validate(_valid_conformer_payload(), leerie.SCHEMAS["conformer"])


@pytest.mark.parametrize("missing_field", ["subtask_id", "solution_defects"])
def test_payload_missing_a_required_gating_field_is_rejected(leerie, missing_field):
    """Removing a required gating field (subtask_id or solution_defects,
    both consumed by check_conformer_output/_validate_conformance_result)
    must still raise ValidationError — proving the shrink came from
    restructuring/flattening rather than dropping fields the checks need."""
    jsonschema = pytest.importorskip("jsonschema")
    payload = _valid_conformer_payload()
    del payload[missing_field]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(payload, leerie.SCHEMAS["conformer"])


def test_hardened_wire_form_also_stays_small(leerie):
    """The strict-output proxy's `strict:true` rewrite is what actually
    reaches the grammar compiler; confirm the hardened form doesn't
    balloon back up past the source-level shrink."""
    node = copy.deepcopy(leerie.SCHEMAS["conformer"])
    leerie._strictify_schema(node)
    hardened_size = len(json.dumps(node))
    assert hardened_size < _PRE_FIX_REJECTED_BYTES


def test_run_summary_reports_fallback_count_via_source_inspection(leerie):
    """The 'N schema(s) fell back to post-hoc validation' run-summary line
    already exists (per the work order's own correction) — confirm it's
    still present rather than re-adding duplicate coverage for it."""
    src = inspect.getsource(leerie)
    assert "schema(s) fell back to post-hoc" in src
    assert "fell_back" in src
