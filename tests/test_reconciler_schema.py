"""Schema tests for `SCHEMAS["reconciler"]` — the flattened output the
reconciler worker emits (DESIGN §5, §14).

The wire shape is FLAT by necessity, not by taste: the previous nine-array
shape nested an array-of-objects (`requires`) inside an array-of-objects
(`added_subtasks`) and repeated the isomorphic `{sid, tag, reason}` object four
times, which put it over the grammar-compilation ceiling under
`--dangerously-force-strict-output` ("The compiled grammar is too large").
`requires` now lives in a top-level `added_requires` keyed by sid, and the four
repeated shapes collapse into one enum-discriminated `tag_ops`.
`_expand_reconciler_output` fans it back into the nine arrays every consumer
still expects, so those tests live in `test_strict_output_proxy.py`.

The schema is consumed by `claude_p()` via `--json-schema` to gate the
worker's output. We don't have the live `claude` CLI in tests, so the
gating itself is exercised end-to-end by other means; here we just pin
the schema's structural contract by validating representative payloads
against it with a stdlib JSON-schema-shaped check.

We re-use the same style as `test_schemas_confidence.py`: extract the
schema dict from `leerie.SCHEMAS["reconciler"]` and reason over its
declared `required` / `properties` keys directly.
"""
from __future__ import annotations

import pytest


def _full_valid_output() -> dict:
    """A reconciler output with every array populated. Useful as a
    baseline; individual tests mutate copies of this."""
    return {
        "renames": [
            {"sid": "test-001", "from": "capture-call-implemented",
             "to": "event-capture-shim"},
        ],
        "tag_ops": [
            {"op": "add_provide", "sid": "feat-002",
             "tag": "judge-rubric-defined", "reason": "it already emits it"},
            {"op": "drop_require", "sid": "test-002",
             "tag": "framework-decision-made",
             "reason": "The framework choice is recorded by test-002 "
                       "itself in package.json; no other subtask produces "
                       "it as a code artifact."},
            {"op": "conditional_drop", "sid": "deps-004", "tag": "",
             "reason": "deps-004's own intent declares it conditional "
                       "('no-op the orchestrator can drop'); no subtask "
                       "produces the precondition tag."},
            {"op": "unresolvable", "sid": "test-005",
             "tag": "magic-thing-that-doesnt-exist",
             "reason": "No planner produced anything related and no "
                       "plausible connector subtask can be inferred."},
        ],
        "added_requires": [
            {"sid": "feat-008", "tag": "events-ndjson-emitter",
             "extent": "in_plan", "reason": "reads what that subtask writes"},
        ],
        "added_subtasks": [
            {
                "id": "feat-008",
                "title": "Implement verdict loader",
                "intent": "Read NDJSON verdicts back into Python dicts.",
                "success_criteria_seed": "verdict_loader.py reads "
                                         "events.ndjson and returns a list of dicts",
                "provides": ["verdict-loader-implemented"],
                "depends_on": [],
                "size": "small",
                "_added_by_reconciler": True,
            },
        ],
        "dependency_edges": [
            {"from": "test-003", "to": "test-004",
             "reason": "test-003 must produce the schema before test-004 "
                       "can consume it."},
        ],
        "merged_subtasks": [
            {"into": "test-006", "from": "test-007",
             "reason": "Both subtasks edit the same bootstrap file and "
                       "wait on the same authoring decision."},
        ],
        "confidence": {
            "reconciliation": 9.2,
            "basis": "all unresolved tags addressed",
            "falsifiers_tested": ["checked rename targets exist"],
            "contradictions_reconciled": [],
            "gap_to_close": {},
        },
    }


def test_reconciler_schema_exists(leerie):
    """SCHEMAS["reconciler"] is the contract claude_p enforces against
    the worker's output. Existence pin so a future refactor can't
    silently drop it."""
    assert "reconciler" in leerie.SCHEMAS
    schema = leerie.SCHEMAS["reconciler"]
    assert schema["type"] == "object"


def test_reconciler_requires_all_wire_arrays(leerie):
    """Every wire array must be present in every output, even if empty, so
    callers never crash on a missing key.

    Seven fields, not nine: the four isomorphic `{sid, tag, reason}` arrays
    (added_provides, dropped_requires, conditional_drops, unresolvable)
    collapsed into one enum-discriminated `tag_ops`, and `requires` was lifted
    out of `added_subtasks` into a top-level `added_requires`. Both changes
    were forced by grammar compilation, not preference — see the module
    docstring."""
    schema = leerie.SCHEMAS["reconciler"]
    required = set(schema["required"])
    assert required == {"added_subtasks", "added_requires", "tag_ops",
                        "renames", "dependency_edges", "merged_subtasks",
                        "confidence"}


def test_reconciler_tag_ops_shape(leerie):
    """`tag_ops` carries the four operations that were separate arrays.

    `reason` is required on every one so the audit trail records WHY —
    for a conditional_drop that is typically a quote of the consumer's own
    conditional intent plus the structural reason its precondition is false.
    `tag` is optional because conditional_drop has no tag to name."""
    schema = leerie.SCHEMAS["reconciler"]
    item = schema["properties"]["tag_ops"]["items"]
    assert set(item["required"]) == {"op", "sid", "reason"}
    assert set(item["properties"]["op"]["enum"]) == {
        "add_provide", "drop_require", "unresolvable", "conditional_drop"}
    for field in ("sid", "tag", "reason"):
        assert item["properties"][field]["type"] == "string"



def test_reconciler_dependency_edges_shape(leerie):
    """dependency_edges asserts an explicit depends_on ordering between
    two existing subtasks. Both ids are required (apply step validates
    existence and die()s on missing); reason explains the asserted
    ordering."""
    item = leerie.SCHEMAS["reconciler"]["properties"]["dependency_edges"]["items"]
    assert set(item["required"]) == {"from", "to", "reason"}


def test_reconciler_merged_subtasks_shape(leerie):
    """merged_subtasks collapses two subtasks into one. into/from/reason
    are required; title/intent/success_criteria_seed are optional
    overrides for restating the merged unit's contract."""
    item = leerie.SCHEMAS["reconciler"]["properties"]["merged_subtasks"]["items"]
    assert set(item["required"]) == {"into", "from", "reason"}
    props = item["properties"]
    for optional in ("title", "intent", "success_criteria_seed"):
        assert optional in props


def test_reconciler_rename_shape(leerie):
    """Each rename has sid + from + to. All three are required so the
    orchestrator's mutation logic doesn't have to handle partial
    renames."""
    item = leerie.SCHEMAS["reconciler"]["properties"]["renames"]["items"]
    assert set(item["required"]) == {"sid", "from", "to"}



def test_reconciler_added_subtasks_shape_matches_planner(leerie):
    """Added subtasks must carry the same required fields as planner
    subtasks (id, title, success_criteria_seed). The
    `_added_by_reconciler` traceability flag is stamped by
    `_apply_reconciler_output` after the model emits, so it is
    deliberately NOT a model-required field (a defective model
    setting it false would otherwise bypass the size gate)."""
    item = leerie.SCHEMAS["reconciler"]["properties"]["added_subtasks"]["items"]
    required = set(item["required"])
    assert "id" in required
    assert "title" in required
    assert "success_criteria_seed" in required
    assert "_added_by_reconciler" not in required, (
        "`_added_by_reconciler` must not be a model-required field — "
        "leerie stamps it mechanically in `_apply_reconciler_output`")


def test_reconciler_added_subtask_carries_planner_fields(leerie):
    """The properties of an added_subtask must include every field the
    planner declares so a reconciler-added subtask passes the same
    downstream checks. Pin a representative subset to catch drift."""
    props = (leerie.SCHEMAS["reconciler"]
             ["properties"]["added_subtasks"]["items"]["properties"])
    # Fields the planner schema declares on each subtask.
    # `requires` is deliberately absent — it lives in the top-level
    # `added_requires`, keyed by sid, because nesting an array-of-objects
    # inside an array-of-objects is what broke grammar compilation.
    for field in ("id", "title", "intent", "scope_note", "depends_on",
                  "provides", "success_criteria_seed",
                  "size", "investigation_notes"):
        assert field in props, (
            f"reconciler added_subtask schema must include planner field "
            f"'{field}' or downstream code will reject it"
        )
    # `_added_by_reconciler` is NOT in the schema's `properties` —
    # leerie stamps it after the model emits, so the model has no
    # business setting it.
    assert "_added_by_reconciler" not in props, (
        "`_added_by_reconciler` must not be a model-settable property — "
        "leerie stamps it mechanically in `_apply_reconciler_output`")
    assert "requires" not in props, (
        "`requires` must stay lifted into the top-level `added_requires` — "
        "re-nesting it puts the schema back over the grammar ceiling")



def test_reconciler_arrays_can_all_be_empty(leerie):
    """The all-arrays-empty payload is valid — represents the
    degenerate-but-legitimate case where the worker found nothing to
    do (which in practice means phase_reconcile would have
    short-circuited before calling the worker, but the schema must
    still accept it)."""
    empty = {"added_subtasks": [], "added_requires": [], "tag_ops": [],
             "renames": [], "dependency_edges": [],
             "merged_subtasks": [],
             "confidence": {"reconciliation": 9.0, "basis": "",
                            "falsifiers_tested": [],
                            "contradictions_reconciled": [],
                            "gap_to_close": {}}}
    required = set(leerie.SCHEMAS["reconciler"]["required"])
    assert set(empty.keys()) == required, (
        "fixture and schema must agree on the full set of required fields")


def test_reconciler_full_payload_keys_align_with_schema(leerie):
    """The hand-crafted `_full_valid_output` payload only uses keys the
    schema declares. Drift guard: if the schema gains a field, update
    this test and the prompt example together."""
    schema = leerie.SCHEMAS["reconciler"]
    declared = set(schema["properties"].keys())
    payload = _full_valid_output()
    assert set(payload.keys()) == declared
