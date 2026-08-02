"""Unit tests for check_required_items_coverage() — the deterministic
PRIMARY layer of the task-coverage gate (DESIGN §5 *Migration-surface
completeness*-style composition; sibling to
check_prescribed_command_coverage, which is the instruction-adherence
gate's own floor).

Pure JSON→verdict set logic over two structured JSON arrays (the
classifier's `required_items` and each subtask's title +
success_criteria_seed) — no NL parsing anywhere in this file or the
function under test. Matching is normalized (lowercased, stopword-
filtered) token-overlap, not exact string equality, mirroring
check_prescribed_command_coverage's own paraphrase-tolerant matching.
"""
from __future__ import annotations


def _subtask(sid: str, title: str = "", success_criteria_seed: str = "") -> dict:
    return {"id": sid, "title": title,
            "success_criteria_seed": success_criteria_seed}


def _item(item: str, source_ref: str = "") -> dict:
    d = {"item": item}
    if source_ref:
        d["source_ref"] = source_ref
    return d


def test_uncovered_item_fires(leerie):
    required_items = [_item("add rate limiting to the API")]
    subtasks = [_subtask("feat-001", title="Add pagination",
                          success_criteria_seed="pagination works")]
    issues = leerie.check_required_items_coverage(required_items, subtasks)
    assert len(issues) == 1
    assert issues[0].startswith("REQUIRED_ITEM_UNCOVERED:")
    assert "rate limiting" in issues[0]


def test_empty_required_items_is_silent(leerie):
    """The common case: most tasks have no enumerable required_items —
    0 false positives by construction."""
    assert leerie.check_required_items_coverage([], []) == []
    assert leerie.check_required_items_coverage(
        None, [_subtask("feat-001", title="anything")]) == []
    assert leerie.check_required_items_coverage(
        [], [_subtask("feat-001", title="anything")]) == []


def test_paraphrase_coverage_is_silent(leerie):
    """A subtask's title+success_criteria_seed wraps the item's own
    tokens in extra words — covered via salient token-subset matching,
    not exact string equality."""
    required_items = [_item("add rate limiting to the API")]
    subtasks = [_subtask(
        "feat-001", title="Add rate limiting",
        success_criteria_seed="the API enforces rate limiting per client")]
    assert leerie.check_required_items_coverage(required_items, subtasks) == []


def test_all_items_covered_is_silent(leerie):
    required_items = [
        _item("add rate limiting to the API"),
        _item("add pagination to the list endpoint"),
    ]
    subtasks = [
        _subtask("feat-001", title="Add rate limiting to the API"),
        _subtask("feat-002", title="Add pagination to the list endpoint"),
    ]
    assert leerie.check_required_items_coverage(required_items, subtasks) == []


def test_partial_coverage_fires_only_for_uncovered_item(leerie):
    required_items = [
        _item("add rate limiting to the API"),
        _item("add pagination to the list endpoint"),
    ]
    subtasks = [_subtask("feat-001", title="Add rate limiting to the API")]
    issues = leerie.check_required_items_coverage(required_items, subtasks)
    assert len(issues) == 1
    assert "pagination" in issues[0]
    assert "rate limiting" not in issues[0]


def test_no_subtasks_at_all_fires_for_every_required_item(leerie):
    required_items = [_item("item one"), _item("item two")]
    issues = leerie.check_required_items_coverage(required_items, [])
    assert len(issues) == 2


def test_missing_title_and_criteria_on_subtasks_is_tolerated(leerie):
    """A subtask missing title/success_criteria_seed must not crash the
    coverage check — it simply contributes no coverage."""
    required_items = [_item("add pagination")]
    subtasks = [{"id": "feat-001"}, _subtask("feat-002", title="Add pagination")]
    assert leerie.check_required_items_coverage(required_items, subtasks) == []


def test_non_dict_or_blank_items_are_skipped_not_crashed(leerie):
    required_items = [_item("add pagination"), "not a dict", _item("")]
    issues = leerie.check_required_items_coverage(required_items, [])
    assert len(issues) == 1
    assert "add pagination" in issues[0]


def test_case_insensitive_matching(leerie):
    required_items = [_item("Add Rate Limiting")]
    subtasks = [_subtask("feat-001", title="ADD RATE LIMITING to the API")]
    assert leerie.check_required_items_coverage(required_items, subtasks) == []


def test_unrelated_shared_stopword_does_not_falsely_cover(leerie):
    """An item sharing only a stopword (e.g. 'the') with a subtask's
    title/success_criteria_seed must not be considered covered."""
    required_items = [_item("add rate limiting")]
    subtasks = [_subtask(
        "feat-001", title="Refactor the database layer",
        success_criteria_seed="all queries use the new connection pool")]
    issues = leerie.check_required_items_coverage(required_items, subtasks)
    assert len(issues) == 1


def test_source_ref_included_in_issue_when_present(leerie):
    required_items = [_item("add rate limiting", source_ref="item 3 of spec")]
    issues = leerie.check_required_items_coverage(required_items, [])
    assert "item 3 of spec" in issues[0]


def test_no_source_ref_omits_parenthetical(leerie):
    required_items = [_item("add rate limiting")]
    issues = leerie.check_required_items_coverage(required_items, [])
    assert "()" not in issues[0]


# ===========================================================================
# SCHEMAS["classifier"]["properties"]["required_items"] shape
# ===========================================================================

def test_schema_field_exists_on_classifier(leerie):
    schema = leerie.SCHEMAS["classifier"]
    assert "required_items" in schema["properties"]


def test_schema_field_is_array_of_objects_requiring_item(leerie):
    field = leerie.SCHEMAS["classifier"]["properties"]["required_items"]
    assert field["type"] == "array"
    item_schema = field["items"]
    assert item_schema["type"] == "object"
    assert "item" in item_schema["required"]
    assert "source_ref" not in item_schema.get("required", [])


def test_schema_item_field_has_min_length(leerie):
    field = leerie.SCHEMAS["classifier"]["properties"]["required_items"]
    assert field["items"]["properties"]["item"]["minLength"] == 1


def test_schema_not_in_classifier_required_top_level(leerie):
    """required_items must be optional — most tasks have none."""
    schema = leerie.SCHEMAS["classifier"]
    assert "required_items" not in schema["required"]


def test_required_items_registered_in_state_fields(leerie):
    assert "required_items" in leerie.STATE_FIELDS
