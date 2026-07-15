"""Unit tests for `_remap_vanished_deps` (DESIGN §5 *Id-vanishing operations*).

The helper is the single mechanism behind all four id-vanishing call sites:
`recursive_decompose` (intra-generation), `phase_plan` (cross-subtask), and the
two phase-3 soft-drop filters. Fan-out (a parent becomes N leaves) and prune (a
drop maps to `[]`) are the same operation over the mapping.
"""
from __future__ import annotations


def _sub(sid: str, deps: list[str] | None = None) -> dict:
    return {"id": sid, "depends_on": list(deps or [])}


class TestFanOut:
    """Expansion: one vanished id -> N successors."""

    def test_dependent_fans_out_to_every_leaf(self, leerie):
        """The reported bug: config-005 depends_on a parent that expanded.

        Fan-out targets every leaf, not a representative one — the tag channel
        already behaves this way (each leaf inherits `provides`), so the id
        channel must match or scheduling would depend on which channel the
        planner happened to use.
        """
        subs = [_sub("config-003-1"), _sub("config-003-2"),
                _sub("config-005", ["config-003"])]
        leerie._remap_vanished_deps(
            subs, {"config-003": ["config-003-1", "config-003-2"]})
        assert subs[2]["depends_on"] == ["config-003-1", "config-003-2"]

    def test_multiple_dependents_each_fan_out(self, leerie):
        subs = [_sub("config-004", ["config-003"]),
                _sub("config-005", ["config-003"])]
        leerie._remap_vanished_deps(
            subs, {"config-003": ["config-003-1", "config-003-2"]})
        for s in subs:
            assert s["depends_on"] == ["config-003-1", "config-003-2"]

    def test_order_within_deps_is_preserved(self, leerie):
        """A non-expanded dep keeps its position relative to the fan-out."""
        subs = [_sub("x-1", ["a-1", "config-003", "z-1"])]
        leerie._remap_vanished_deps(subs, {"config-003": ["c-1", "c-2"]})
        assert subs[0]["depends_on"] == ["a-1", "c-1", "c-2", "z-1"]


class TestPrune:
    """Drop: one vanished id -> no successor."""

    def test_dropped_id_is_pruned(self, leerie):
        subs = [_sub("config-004", ["config-003", "config-002"])]
        leerie._remap_vanished_deps(subs, {"config-003": []})
        assert subs[0]["depends_on"] == ["config-002"]

    def test_pruning_sole_dep_yields_empty_list(self, leerie):
        subs = [_sub("config-004", ["config-003"])]
        leerie._remap_vanished_deps(subs, {"config-003": []})
        assert subs[0]["depends_on"] == []

    def test_multiple_drops_at_once(self, leerie):
        """Both filters pass `{sid: [] for sid in dropped}` — many at once."""
        subs = [_sub("x-1", ["a-1", "b-1", "keep-1"])]
        leerie._remap_vanished_deps(subs, {"a-1": [], "b-1": []})
        assert subs[0]["depends_on"] == ["keep-1"]


class TestNoOp:
    """Cases that must not touch anything."""

    def test_empty_mapping_is_a_no_op(self, leerie):
        subs = [_sub("config-004", ["config-003"])]
        leerie._remap_vanished_deps(subs, {})
        assert subs[0]["depends_on"] == ["config-003"]

    def test_dep_absent_from_mapping_passes_through(self, leerie):
        """Guards against over-eager rewriting of untouched edges."""
        subs = [_sub("config-005", ["config-004"])]
        leerie._remap_vanished_deps(subs, {"config-003": ["config-003-1"]})
        assert subs[0]["depends_on"] == ["config-004"]

    def test_subtask_with_no_deps_untouched(self, leerie):
        subs = [_sub("config-003-1")]
        leerie._remap_vanished_deps(subs, {"config-003": ["config-003-1"]})
        assert subs[0]["depends_on"] == []

    def test_empty_subtask_list(self, leerie):
        subs: list[dict] = []
        leerie._remap_vanished_deps(subs, {"config-003": ["config-003-1"]})
        assert subs == []


class TestDedup:
    """Mirrors `_apply_overlap_merge`'s dedup-after-rewrite discipline."""

    def test_existing_leaf_dep_is_not_duplicated(self, leerie):
        subs = [_sub("x-1", ["config-003", "config-003-1"])]
        leerie._remap_vanished_deps(
            subs, {"config-003": ["config-003-1", "config-003-2"]})
        assert subs[0]["depends_on"] == ["config-003-1", "config-003-2"]

    def test_two_vanished_ids_sharing_a_successor(self, leerie):
        subs = [_sub("x-1", ["a", "b"])]
        leerie._remap_vanished_deps(subs, {"a": ["shared"], "b": ["shared"]})
        assert subs[0]["depends_on"] == ["shared"]


class TestSelfReference:
    """The `repl != sid` guard.

    Dead code on every reachable input — a leaf can only fan back to itself if
    a subtask declares `depends_on` on its own parent, and `schedule()` already
    die()s on a self-edge before recursion runs. Retained to match
    `_apply_overlap_merge`'s discipline; pinned here so a future caller that
    *can* reach it keeps the guarantee.
    """

    def test_leaf_does_not_depend_on_itself(self, leerie):
        subs = [_sub("config-003-1", ["config-003"])]
        leerie._remap_vanished_deps(
            subs, {"config-003": ["config-003-1", "config-003-2"]})
        assert "config-003-1" not in subs[0]["depends_on"]
        assert subs[0]["depends_on"] == ["config-003-2"]

    def test_self_only_fanout_yields_empty(self, leerie):
        subs = [_sub("config-003-1", ["config-003"])]
        leerie._remap_vanished_deps(subs, {"config-003": ["config-003-1"]})
        assert subs[0]["depends_on"] == []
