"""N29 follow-up — `SCHEMAS['conformer']`'s dumped (wire) size stays below
the pre-flatten shape's, and the shrunk schema still validates (and still
rejects) the payloads it did before the flatten.

Distinct from `tests/test_conformer_schema_shrink.py`, which pins the
*source*-byte size (`ast.get_source_segment` on the literal dict) against
`plan_overlap_judge`'s own source size. This file owns the plain
`len(json.dumps(...))` wire metric, the accept/reject validity contract,
and the run-summary "schema(s) fell back to post-hoc validation" line.

**Metric discipline — this file previously had none, and was vacuous.**
It asserted `len(json.dumps(conformer)) < 4844`, but 4,844 is
`plan_overlap_judge`'s *source*-byte size; its dumped size is 894. The
conformer dumps to 2,450 now and 2,722 pre-fix, so the assertion PASSED on
the exact schema N29 was written to fix. Its two "falsification" tests
compared 6,219 against 4,844 — constant against constant, with no schema
involved at all. Measured 2026-08-12:

    schema                       dumped   source
    conformer  (pre-fix)          2722     6219
    conformer  (current)          2450     4114
    plan_overlap_judge             894     4844

The rule now: **every assertion in this file measures a real schema, and
the bound is one a real regression crosses.** A source-byte threshold may
not be applied to a wire-byte measurement, in either direction.
"""
from __future__ import annotations

import copy
import inspect
import json

import pytest

# Bound for the WIRE metric, sitting between the current shape (2,450) and
# the pre-flatten shape (2,653 as reconstructed by `_unflattened_conformer`
# below; 2,722 as measured against the real 7721a6e~1 literal, which also
# carried descriptions this reconstruction drops). Reverting the flatten
# therefore crosses it. Deliberately NOT plan_overlap_judge's 894 — the
# conformer legitimately carries more fields, and the work order's ~4.8K
# bracket was a source-byte figure that belongs to the sibling file.
_MAX_DUMPED_BYTES = 2550

# Same discipline for the hardened form the grammar compiler actually sees:
# current 2,880, pre-flatten 3,178 (both measured).
_MAX_HARDENED_BYTES = 3000


def _unflattened_conformer(schema: dict) -> dict:
    """Reconstruct the PRE-N29 shape from the current schema.

    A real schema whose size is really measured, rather than a hard-coded
    byte count — so `test_flatten_is_what_keeps_it_under_the_bound` fails if
    someone reverts the flatten, which a constant-vs-constant comparison
    cannot do. Mirrors `_expand_conformer_output`'s mapping in reverse:
    `rule_violations` splits back into fixed/residual arrays and
    `file_updates` into docs/tests arrays.
    """
    node = copy.deepcopy(schema)
    props = node["properties"]
    rv_items = props.pop("rule_violations")["items"]
    fu_items = props.pop("file_updates")["items"]
    rv_p, fu_p = rv_items["properties"], fu_items["properties"]

    props["rule_violations_fixed"] = {
        "type": "array",
        "items": {"type": "object", "required": ["rule"], "properties": {
            "rule": rv_p["rule"], "fix": rv_p["fix"],
            "evidence": rv_p["evidence"]}}}
    props["rule_violations_residual"] = {
        "type": "array",
        "items": {"type": "object", "required": ["rule"], "properties": {
            "rule": rv_p["rule"], "why_not_fixed": rv_p["why_not_fixed"]}}}
    file_arr = {
        "type": "array",
        "items": {"type": "object", "required": ["path", "reason"],
                  "properties": {"path": fu_p["path"],
                                 "reason": fu_p["reason"]}}}
    props["docs_updates"] = file_arr
    props["tests_updates"] = copy.deepcopy(file_arr)

    node["required"] = [r for r in node["required"]
                        if r not in ("rule_violations", "file_updates")]
    node["required"] += ["rule_violations_fixed", "rule_violations_residual"]
    return node


def test_conformer_dumped_size_below_bound(leerie):
    size = len(json.dumps(leerie.SCHEMAS["conformer"]))
    assert size < _MAX_DUMPED_BYTES, (
        f"SCHEMAS['conformer'] dumps to {size} bytes, at or above the "
        f"{_MAX_DUMPED_BYTES}-byte bound. The conformer is the largest "
        "schema in the file and the one the strict-output grammar compiler "
        "has actually rejected (DESIGN §7); growth here is what N29 fixed.")


def test_flatten_is_what_keeps_it_under_the_bound(leerie):
    """Falsification, against a measured schema rather than a constant.

    Un-flattening the two discriminated arrays must push the wire size back
    over the bound — otherwise the bound is not measuring the thing N29
    changed, which is exactly how the previous version of this file passed
    on the pre-fix schema.
    """
    reverted = len(json.dumps(_unflattened_conformer(leerie.SCHEMAS["conformer"])))
    assert reverted > _MAX_DUMPED_BYTES, (
        f"the pre-flatten shape dumps to {reverted} bytes, still under the "
        f"{_MAX_DUMPED_BYTES}-byte bound — the bound no longer discriminates "
        "between the flattened and un-flattened schema, so it proves nothing")


def test_bound_is_not_a_source_byte_figure(leerie):
    """Guard against re-importing a source-byte threshold into this file.

    `plan_overlap_judge`'s source size (4,844) is ~5.4x its dumped size
    (894). Any bound here that is anywhere near a source figure is a sign
    the two metrics have been crossed again.
    """
    pojd = len(json.dumps(leerie.SCHEMAS["plan_overlap_judge"]))
    assert pojd < 1500, (
        "plan_overlap_judge's dumped size moved; re-derive the note above "
        "rather than assuming the old measurements still hold")
    assert _MAX_DUMPED_BYTES < 4844, (
        "the bound is at or above plan_overlap_judge's SOURCE size — the "
        "exact conflation this file was rewritten to remove")


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
    reaches the grammar compiler, so it gets its own bound — measured on
    the same two shapes (current 2,880 vs pre-flatten 3,178) rather than
    against the old 6,219 source-byte figure, which every hardened form
    trivially cleared."""
    node = copy.deepcopy(leerie.SCHEMAS["conformer"])
    leerie._strictify_schema(node)
    hardened_size = len(json.dumps(node))
    assert hardened_size < _MAX_HARDENED_BYTES, (
        f"hardened conformer schema is {hardened_size} bytes, at or above "
        f"the {_MAX_HARDENED_BYTES}-byte bound")

    reverted = _unflattened_conformer(leerie.SCHEMAS["conformer"])
    leerie._strictify_schema(reverted)
    assert len(json.dumps(reverted)) > _MAX_HARDENED_BYTES, (
        "the hardened bound does not discriminate the pre-flatten shape")


def test_run_summary_reports_fallback_count_via_source_inspection(leerie):
    """The 'N schema(s) fell back to post-hoc validation' run-summary line
    already exists (per the work order's own correction) — confirm it's
    still present rather than re-adding duplicate coverage for it."""
    src = inspect.getsource(leerie)
    assert "schema(s) fell back to post-hoc" in src
    assert "fell_back" in src
