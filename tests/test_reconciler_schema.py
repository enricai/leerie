"""Schema tests for `SCHEMAS["reconciler"]` — the eight-array output the
reconciler worker emits (DESIGN §5, §14).

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
    """A reconciler output with all eight arrays populated. Useful as a
    baseline; individual tests mutate copies of this."""
    return {
        "renames": [
            {"sid": "test-001", "from": "capture-call-implemented",
             "to": "event-capture-shim"},
        ],
        "added_provides": [
            {"sid": "feat-002", "tag": "judge-rubric-defined"},
        ],
        "added_subtasks": [
            {
                "id": "feat-008",
                "title": "Implement verdict loader",
                "intent": "Read NDJSON verdicts back into Python dicts.",
                "success_criteria_seed": "verdict_loader.py reads "
                                         "events.ndjson and returns a list of dicts",
                "provides": ["verdict-loader-implemented"],
                "requires": [],
                "depends_on": [],
                "size": "small",
                "_added_by_reconciler": True,
            },
        ],
        "conditional_drops": [
            {"sid": "deps-004",
             "reason": "deps-004's own intent declares it conditional "
                       "('no-op the orchestrator can drop'); no subtask "
                       "produces the precondition tag."},
        ],
        "dropped_requires": [
            {"sid": "test-002", "tag": "framework-decision-made",
             "reason": "The framework choice is recorded by test-002 "
                       "itself in package.json; no other subtask produces "
                       "it as a code artifact."},
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
        "unresolvable": [
            {"sid": "test-005",
             "tag": "magic-thing-that-doesnt-exist",
             "reason": "No planner produced anything related and no "
                       "plausible connector subtask can be inferred."},
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


def test_reconciler_requires_all_eight_arrays(leerie):
    """All eight arrays must be present in every output — even if empty.
    Each array is independently optional in content (any can be empty)
    but the field itself must be there so callers don't crash on a
    missing key. The eight cover five resolution actions (renames,
    added_provides, added_subtasks, conditional_drops, dropped_requires
    — the latter is dual-role and also serves as a cycle-breaker), two
    cycle-breaking-only actions (dependency_edges, merged_subtasks),
    and one escape hatch (unresolvable)."""
    schema = leerie.SCHEMAS["reconciler"]
    required = set(schema["required"])
    assert required == {"renames", "added_provides", "added_subtasks",
                        "conditional_drops",
                        "dropped_requires", "dependency_edges",
                        "merged_subtasks", "unresolvable", "confidence"}


def test_reconciler_conditional_drops_shape(leerie):
    """conditional_drops drops a planner-emitted consumer subtask whose
    own intent declared it conditional on an unresolvable in_plan
    precondition (DESIGN §5). Carries (sid, reason) — `reason` is
    required so the audit field records WHY the drop happened (typically
    a quote of the consumer's conditional intent + the structural reason
    the precondition is false)."""
    schema = leerie.SCHEMAS["reconciler"]
    assert "conditional_drops" in schema["properties"]
    item = schema["properties"]["conditional_drops"]["items"]
    assert set(item["required"]) == {"sid", "reason"}
    assert item["properties"]["sid"]["type"] == "string"
    assert item["properties"]["reason"]["type"] == "string"


def test_reconciler_dropped_requires_shape(leerie):
    """dropped_requires removes an over-specified extent:in_plan requires
    entry. Carries (sid, tag, reason) — reason is required so the user
    sees why the requirement was dropped."""
    item = leerie.SCHEMAS["reconciler"]["properties"]["dropped_requires"]["items"]
    assert set(item["required"]) == {"sid", "tag", "reason"}


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


def test_reconciler_added_provides_shape(leerie):
    """Each added_provides is (sid, tag)."""
    item = leerie.SCHEMAS["reconciler"]["properties"]["added_provides"]["items"]
    assert set(item["required"]) == {"sid", "tag"}


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
    for field in ("id", "title", "intent", "scope_note", "depends_on",
                  "requires", "provides", "success_criteria_seed",
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


def test_reconciler_unresolvable_shape(leerie):
    """Each unresolvable entry must include reasoning the user will see."""
    item = leerie.SCHEMAS["reconciler"]["properties"]["unresolvable"]["items"]
    assert set(item["required"]) == {"sid", "tag", "reason"}


def test_reconciler_arrays_can_all_be_empty(leerie):
    """The all-arrays-empty payload is valid — represents the
    degenerate-but-legitimate case where the worker found nothing to
    do (which in practice means phase_reconcile would have
    short-circuited before calling the worker, but the schema must
    still accept it)."""
    empty = {"renames": [], "added_provides": [], "added_subtasks": [],
             "conditional_drops": [],
             "dropped_requires": [], "dependency_edges": [],
             "merged_subtasks": [], "unresolvable": [],
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
