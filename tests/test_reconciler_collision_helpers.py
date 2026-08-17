"""Unit tests for the leaf/formatting helpers underneath the reconciler's
cycle-attribution and collision-resolution machinery (DESIGN §5): SCC edge
attribution, shared-file detection, rename-tag resolution, collision
drop/survivor accessors, wiring-defect label rendering, and the
`depends_on` edge builder.

These are the primitive accessors the multi-drop incident (CLAUDE.md,
`test_apply_multi_drop_preserves_both_survivors`) sits one layer above —
none are referenced directly by name in the existing overlap-judge /
wiring-gate test suites, despite backing behavior those suites depend on.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from orchestrator import leerie


# ===========================================================================
# _attribute_cycle_edges
# ===========================================================================

class TestAttributeCycleEdges:
    def test_planner_declared_when_no_mutation_closed_it(self):
        scc = ["feat-001", "feat-002"]
        succ = {"feat-001": {"feat-002"}, "feat-002": {"feat-001"}}
        edge_sources = {
            ("feat-001", "feat-002"): "depends_on",
            ("feat-002", "feat-001"): "depends_on",
        }
        output = {"renames": [], "added_subtasks": [], "dependency_edges": []}
        edges = leerie._attribute_cycle_edges(
            scc, succ, edge_sources, output, {})
        assert len(edges) == 2
        for e in edges:
            assert e["mutation"] == "planner-declared"

    def test_added_subtask_as_source_attributes_to_it(self):
        scc = ["feat-001", "feat-002"]
        succ = {"feat-001": {"feat-002"}, "feat-002": {"feat-001"}}
        edge_sources = {
            ("feat-001", "feat-002"): "depends_on",
            ("feat-002", "feat-001"): "depends_on",
        }
        output = {
            "renames": [],
            "added_subtasks": [{"id": "feat-001"}],
            "dependency_edges": [],
        }
        edges = leerie._attribute_cycle_edges(
            scc, succ, edge_sources, output, {})
        by_pair = {(e["from"], e["to"]): e for e in edges}
        assert by_pair[("feat-001", "feat-002")]["mutation"] == \
            "added_subtask: feat-001"

    def test_added_subtask_as_destination_attributes_to_it(self):
        scc = ["feat-001", "feat-002"]
        succ = {"feat-001": {"feat-002"}, "feat-002": {"feat-001"}}
        edge_sources = {
            ("feat-001", "feat-002"): "depends_on",
            ("feat-002", "feat-001"): "depends_on",
        }
        output = {
            "renames": [],
            "added_subtasks": [{"id": "feat-002"}],
            "dependency_edges": [],
        }
        edges = leerie._attribute_cycle_edges(
            scc, succ, edge_sources, output, {})
        by_pair = {(e["from"], e["to"]): e for e in edges}
        assert by_pair[("feat-001", "feat-002")]["mutation"] == \
            "added_subtask: feat-002"

    def test_dependency_edge_attribution(self):
        scc = ["feat-001", "feat-002"]
        succ = {"feat-001": {"feat-002"}, "feat-002": {"feat-001"}}
        edge_sources = {
            ("feat-001", "feat-002"): "depends_on",
            ("feat-002", "feat-001"): "depends_on",
        }
        output = {
            "renames": [],
            "added_subtasks": [],
            "dependency_edges": [{"from": "feat-002", "to": "feat-001"}],
        }
        edges = leerie._attribute_cycle_edges(
            scc, succ, edge_sources, output, {})
        by_pair = {(e["from"], e["to"]): e for e in edges}
        assert by_pair[("feat-002", "feat-001")]["mutation"] == \
            "dependency_edge: feat-002 -> feat-001"
        assert by_pair[("feat-001", "feat-002")]["mutation"] == \
            "planner-declared"

    def test_rename_attribution_on_requires_edge(self):
        # feat-001 -> feat-002 means feat-001 provides what feat-002
        # requires; the requires entry actually renamed lives on the
        # CONSUMER (feat-002), looked up by (dst, tag).
        scc = ["feat-001", "feat-002"]
        succ = {"feat-001": {"feat-002"}, "feat-002": {"feat-001"}}
        edge_sources = {
            ("feat-001", "feat-002"): "requires:bar",
            ("feat-002", "feat-001"): "depends_on",
        }
        output = {
            "renames": [{"sid": "feat-002", "from": "foo", "to": "bar"}],
            "added_subtasks": [],
            "dependency_edges": [],
        }
        edges = leerie._attribute_cycle_edges(
            scc, succ, edge_sources, output, {})
        by_pair = {(e["from"], e["to"]): e for e in edges}
        e = by_pair[("feat-001", "feat-002")]
        assert e["mutation"] == \
            "rename: feat-002's 'foo' -> 'bar' (provided by feat-001)"

    def test_requires_edge_no_matching_rename_stays_planner_declared(self):
        scc = ["feat-001", "feat-002"]
        succ = {"feat-001": {"feat-002"}, "feat-002": {"feat-001"}}
        edge_sources = {
            ("feat-001", "feat-002"): "requires:bar",
            ("feat-002", "feat-001"): "depends_on",
        }
        output = {"renames": [], "added_subtasks": [], "dependency_edges": []}
        edges = leerie._attribute_cycle_edges(
            scc, succ, edge_sources, output, {})
        by_pair = {(e["from"], e["to"]): e for e in edges}
        assert by_pair[("feat-001", "feat-002")]["mutation"] == \
            "planner-declared"

    def test_edges_outside_scc_are_excluded(self):
        scc = ["feat-001", "feat-002"]
        succ = {
            "feat-001": {"feat-002"},
            "feat-002": {"feat-001", "feat-003"},
        }
        edge_sources = {
            ("feat-001", "feat-002"): "depends_on",
            ("feat-002", "feat-001"): "depends_on",
            ("feat-002", "feat-003"): "depends_on",
        }
        output = {"renames": [], "added_subtasks": [], "dependency_edges": []}
        edges = leerie._attribute_cycle_edges(
            scc, succ, edge_sources, output, {})
        pairs = {(e["from"], e["to"]) for e in edges}
        assert ("feat-002", "feat-003") not in pairs
        assert len(edges) == 2

    def test_unlabeled_edge_source_defaults_to_question_mark(self):
        scc = ["feat-001", "feat-002"]
        succ = {"feat-001": {"feat-002"}, "feat-002": {"feat-001"}}
        edge_sources = {("feat-002", "feat-001"): "depends_on"}
        output = {"renames": [], "added_subtasks": [], "dependency_edges": []}
        edges = leerie._attribute_cycle_edges(
            scc, succ, edge_sources, output, {})
        by_pair = {(e["from"], e["to"]): e for e in edges}
        assert by_pair[("feat-001", "feat-002")]["source"] == "?"


# ===========================================================================
# _shared_files_in_scc
# ===========================================================================

class TestSharedFilesInScc:
    def test_scc_smaller_than_two_returns_empty(self):
        subtasks = {"feat-001": {"files_likely_touched": ["a.py"]}}
        assert leerie._shared_files_in_scc(["feat-001"], subtasks) == []
        assert leerie._shared_files_in_scc([], subtasks) == []

    def test_shared_file_across_two_members(self):
        subtasks = {
            "feat-001": {"files_likely_touched": ["a.py", "b.py"]},
            "feat-002": {"files_likely_touched": ["b.py", "c.py"]},
        }
        result = leerie._shared_files_in_scc(
            ["feat-001", "feat-002"], subtasks)
        assert result == ["b.py"]

    def test_no_overlap_returns_empty(self):
        subtasks = {
            "feat-001": {"files_likely_touched": ["a.py"]},
            "feat-002": {"files_likely_touched": ["b.py"]},
        }
        assert leerie._shared_files_in_scc(
            ["feat-001", "feat-002"], subtasks) == []

    def test_result_is_sorted(self):
        subtasks = {
            "feat-001": {"files_likely_touched": ["z.py", "a.py"]},
            "feat-002": {"files_likely_touched": ["z.py", "a.py"]},
        }
        result = leerie._shared_files_in_scc(
            ["feat-001", "feat-002"], subtasks)
        assert result == ["a.py", "z.py"]

    def test_file_shared_by_all_three_still_counted_once(self):
        subtasks = {
            "feat-001": {"files_likely_touched": ["a.py"]},
            "feat-002": {"files_likely_touched": ["a.py"]},
            "feat-003": {"files_likely_touched": ["a.py"]},
        }
        result = leerie._shared_files_in_scc(
            ["feat-001", "feat-002", "feat-003"], subtasks)
        assert result == ["a.py"]

    def test_missing_subtask_or_field_tolerated(self):
        subtasks = {"feat-001": {"files_likely_touched": ["a.py"]}}
        # feat-002 absent from the map entirely, feat-003 has no field.
        subtasks["feat-003"] = {}
        result = leerie._shared_files_in_scc(
            ["feat-001", "feat-002", "feat-003"], subtasks)
        assert result == []


# ===========================================================================
# _original_tag_for_rename_edge
# ===========================================================================

class TestOriginalTagForRenameEdge:
    def test_non_requires_edge_returns_empty_string(self):
        edge = {"from": "a", "to": "b", "source": "depends_on"}
        assert leerie._original_tag_for_rename_edge(edge, {}) == ""

    def test_returns_pre_rename_tag_when_rename_present(self):
        edge = {"from": "a", "to": "b", "source": "requires:bar"}
        output = {"renames": [{"sid": "b", "from": "foo", "to": "bar"}]}
        assert leerie._original_tag_for_rename_edge(edge, output) == "foo"

    def test_no_matching_rename_returns_post_rename_tag(self):
        edge = {"from": "a", "to": "b", "source": "requires:bar"}
        output = {"renames": []}
        assert leerie._original_tag_for_rename_edge(edge, output) == "bar"

    def test_rename_for_a_different_sid_is_ignored(self):
        edge = {"from": "a", "to": "b", "source": "requires:bar"}
        output = {"renames": [{"sid": "c", "from": "foo", "to": "bar"}]}
        assert leerie._original_tag_for_rename_edge(edge, output) == "bar"

    def test_rename_to_a_different_tag_is_ignored(self):
        edge = {"from": "a", "to": "b", "source": "requires:bar"}
        output = {
            "renames": [{"sid": "b", "from": "foo", "to": "quux"}],
        }
        assert leerie._original_tag_for_rename_edge(edge, output) == "bar"

    def test_missing_source_label_treated_as_non_requires(self):
        edge = {"from": "a", "to": "b"}
        assert leerie._original_tag_for_rename_edge(edge, {}) == ""


# ===========================================================================
# _collision_dropped_sid
# ===========================================================================

class TestCollisionDroppedSid:
    def test_drop_a_returns_a_sid(self):
        c = {"resolution": "drop_a", "a_sid": "feat-001", "b_sid": "feat-002"}
        assert leerie._collision_dropped_sid(c) == "feat-001"

    def test_drop_b_returns_b_sid(self):
        c = {"resolution": "drop_b", "a_sid": "feat-001", "b_sid": "feat-002"}
        assert leerie._collision_dropped_sid(c) == "feat-002"

    def test_merge_returns_none(self):
        c = {"resolution": "merge", "a_sid": "feat-001", "b_sid": "feat-002"}
        assert leerie._collision_dropped_sid(c) is None

    def test_unresolvable_returns_none(self):
        c = {
            "resolution": "unresolvable",
            "a_sid": "feat-001",
            "b_sid": "feat-002",
        }
        assert leerie._collision_dropped_sid(c) is None

    def test_missing_resolution_returns_none(self):
        assert leerie._collision_dropped_sid({}) is None


# ===========================================================================
# _collision_surviving_sids
# ===========================================================================

class TestCollisionSurvivingSids:
    def test_merge_keeps_both_endpoints(self):
        c = {"resolution": "merge", "a_sid": "feat-001", "b_sid": "feat-002"}
        assert sorted(leerie._collision_surviving_sids(c)) == \
            ["feat-001", "feat-002"]

    def test_drop_a_keeps_b_sid(self):
        c = {"resolution": "drop_a", "a_sid": "feat-001", "b_sid": "feat-002"}
        assert leerie._collision_surviving_sids(c) == ["feat-002"]

    def test_drop_b_keeps_a_sid(self):
        c = {"resolution": "drop_b", "a_sid": "feat-001", "b_sid": "feat-002"}
        assert leerie._collision_surviving_sids(c) == ["feat-001"]

    def test_unresolvable_returns_empty(self):
        c = {
            "resolution": "unresolvable",
            "a_sid": "feat-001",
            "b_sid": "feat-002",
        }
        assert leerie._collision_surviving_sids(c) == []

    def test_merge_missing_a_sid_only_keeps_b(self):
        c = {"resolution": "merge", "b_sid": "feat-002"}
        assert leerie._collision_surviving_sids(c) == ["feat-002"]

    def test_drop_a_missing_b_sid_returns_empty(self):
        c = {"resolution": "drop_a", "a_sid": "feat-001"}
        assert leerie._collision_surviving_sids(c) == []


# ===========================================================================
# _wiring_defect_label
# ===========================================================================

class TestWiringDefectLabel:
    def test_renders_documented_shape(self):
        d = {
            "kind": "missing_requires",
            "sid": "feat-003",
            "tag_or_dep": "parsed-config",
            "concrete_reason": "no in-plan provider declares this tag",
        }
        label = leerie._wiring_defect_label(d)
        assert label == (
            "(missing_requires) feat-003 / parsed-config: "
            "no in-plan provider declares this tag"
        )

    def test_missing_fields_fall_back_to_defaults(self):
        label = leerie._wiring_defect_label({})
        assert label == "(wiring_defect) ? / : "

    def test_strips_whitespace_from_tag_and_reason(self):
        d = {
            "kind": "broken_by_drop",
            "sid": "feat-004",
            "tag_or_dep": "  some-tag  ",
            "concrete_reason": "  the provider was dropped  ",
        }
        label = leerie._wiring_defect_label(d)
        assert label == (
            "(broken_by_drop) feat-004 / some-tag: the provider was dropped"
        )


# ===========================================================================
# _add_depends_on_edge
# ===========================================================================

class TestAddDependsOnEdge:
    def test_appends_to_existing_depends_on(self):
        tree = [{
            "subtasks": [
                {"id": "feat-001", "depends_on": ["feat-000"]},
                {"id": "feat-002", "depends_on": []},
            ]
        }]
        leerie._add_depends_on_edge(tree, "feat-001", "feat-002")
        assert tree[0]["subtasks"][0]["depends_on"] == [
            "feat-000", "feat-002"]

    def test_creates_depends_on_when_absent(self):
        tree = [{"subtasks": [{"id": "feat-001"}]}]
        leerie._add_depends_on_edge(tree, "feat-001", "feat-002")
        assert tree[0]["subtasks"][0]["depends_on"] == ["feat-002"]

    def test_finds_sid_across_multiple_plans(self):
        tree = [
            {"subtasks": [{"id": "feat-001"}]},
            {"subtasks": [{"id": "feat-002", "depends_on": []}]},
        ]
        leerie._add_depends_on_edge(tree, "feat-002", "feat-003")
        assert tree[1]["subtasks"][0]["depends_on"] == ["feat-003"]
        assert "depends_on" not in tree[0]["subtasks"][0]

    def test_unknown_sid_is_a_silent_no_op(self):
        tree = [{"subtasks": [{"id": "feat-001", "depends_on": []}]}]
        leerie._add_depends_on_edge(tree, "feat-999", "feat-002")
        assert tree[0]["subtasks"][0]["depends_on"] == []

    def test_mutates_in_place_not_a_copy(self):
        tree = [{"subtasks": [{"id": "feat-001", "depends_on": []}]}]
        target = tree[0]["subtasks"][0]
        leerie._add_depends_on_edge(tree, "feat-001", "feat-002")
        assert target["depends_on"] == ["feat-002"]

    def test_stops_at_first_matching_sid(self):
        # Guards against a future edit that iterates all plans/subtasks
        # unconditionally instead of returning on first match.
        tree = [{
            "subtasks": [
                {"id": "feat-001", "depends_on": []},
                {"id": "feat-001", "depends_on": []},
            ]
        }]
        leerie._add_depends_on_edge(tree, "feat-001", "feat-002")
        assert tree[0]["subtasks"][0]["depends_on"] == ["feat-002"]
        assert tree[0]["subtasks"][1]["depends_on"] == []
