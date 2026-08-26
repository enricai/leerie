"""Pins that planner/reconciler/splitter's child-subtask item schema is
built by the single shared `_subtask_item_schema()` helper rather than
three independently-written literals (refactor-001).

The three call sites emit structurally different (but overlapping)
optional fields (see `_subtask_item_schema`'s own docstring), so this does
not assert the three resulting dicts are identical to each other -- only
that each is reproducible byte-for-byte by re-invoking the shared builder
with the right include_* flags, which is what makes a future field
addition/removal at the builder apply to all relevant call sites at once
instead of silently landing on only one or two.
"""
from __future__ import annotations

import json


def test_planner_subtasks_items_built_by_shared_helper(leerie):
    items = leerie.SCHEMAS["planner"]["properties"]["subtasks"]["items"]
    rebuilt = leerie._subtask_item_schema(
        include_requires=True,
        include_migration_targets=True,
        include_runs_commands=True,
        include_fixes_reported_symptom=True,
        include_change_shape=True,
    )
    assert json.dumps(items, sort_keys=True) == json.dumps(
        rebuilt, sort_keys=True)


def test_reconciler_added_subtasks_items_built_by_shared_helper(leerie):
    items = leerie.SCHEMAS["reconciler"]["properties"][
        "added_subtasks"]["items"]
    rebuilt = leerie._subtask_item_schema()
    assert json.dumps(items, sort_keys=True) == json.dumps(
        rebuilt, sort_keys=True)


def test_splitter_children_items_built_by_shared_helper(leerie):
    items = leerie.SCHEMAS["splitter"]["properties"]["children"]["items"]
    rebuilt = leerie._subtask_item_schema(include_requires=True)
    assert json.dumps(items, sort_keys=True) == json.dumps(
        rebuilt, sort_keys=True)


def test_three_call_sites_are_not_independently_written_literals(leerie):
    """Anti-vacuity: reconciler (narrowest) must NOT accept fields planner
    grants (requires/migration_targets/runs_commands/fixes_reported_symptom)
    -- proving the shared builder's include_* flags actually narrow the
    accepted-field set rather than the three schemas silently converging
    on one shape."""
    planner_props = set(
        leerie.SCHEMAS["planner"]["properties"]["subtasks"]["items"][
            "properties"])
    reconciler_props = set(
        leerie.SCHEMAS["reconciler"]["properties"]["added_subtasks"][
            "items"]["properties"])
    splitter_props = set(
        leerie.SCHEMAS["splitter"]["properties"]["children"]["items"][
            "properties"])

    assert "requires" in planner_props
    assert "requires" not in reconciler_props
    assert "requires" in splitter_props

    assert "migration_targets" in planner_props
    assert "migration_targets" not in reconciler_props
    assert "migration_targets" not in splitter_props

    assert "runs_commands" in planner_props
    assert "runs_commands" not in reconciler_props
    assert "runs_commands" not in splitter_props

    assert "fixes_reported_symptom" in planner_props
    assert "fixes_reported_symptom" not in reconciler_props
    assert "fixes_reported_symptom" not in splitter_props
