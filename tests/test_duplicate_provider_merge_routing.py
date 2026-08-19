"""M11: the duplicate-provider floor's detections are routed into a merge
resolution rather than left advisory-only (DESIGN §5 *A deterministic floor
underneath the judge*).

`_duplicate_provider_merge_collisions` mirrors `check_duplicate_providers`'s
own detection logic (unmodified — see that function's docstring and this
subtask's scope) but returns `resolution: "merge"` collisions shaped for
`_apply_overlap_collisions`, reusing that helper's per-resolution cycle
avoidance, `skipped_redundant` dedup, and (for 3+ participants) its anchor +
transitive `survivor_of` cluster resolution — the same machinery
`phase_overlap_judge` already uses for the judge's own output.
"""
from __future__ import annotations

import copy

from orchestrator.leerie import (
    _apply_overlap_collisions,
    _duplicate_provider_merge_collisions,
)


def _sub(sid, *, provides=(), files=(), cluster=None, depends_on=()):
    s = {
        "id": sid, "title": sid, "intent": f"intent {sid}",
        "success_criteria_seed": "c", "runs_commands": [],
        "files_likely_touched": list(files), "provides": list(provides),
        "requires": [], "depends_on": list(depends_on), "size": "small",
    }
    if cluster is not None:
        s["_cofile_cluster"] = cluster
    return s


def _plans(*subs_by_domain):
    return [{"domain": d, "status": "ready", "subtasks": list(ss)}
            for d, ss in subs_by_domain]


def _ids(plans):
    return sorted(s["id"] for p in plans for s in p.get("subtasks", []))


class TestDuplicateProviderMergeCollisions:
    def test_pair_sharing_tag_and_file_yields_one_merge_collision(self):
        plans = _plans(("a", [
            _sub("a-1", provides=["widget"], files=["src/widget.py"]),
            _sub("a-2", provides=["widget"], files=["src/widget.py"]),
        ]))
        collisions = _duplicate_provider_merge_collisions(plans)
        assert len(collisions) == 1
        c = collisions[0]
        assert c["resolution"] == "merge"
        assert {c["a_sid"], c["b_sid"]} == {"a-1", "a-2"}
        assert c["artifact"] == "widget"
        assert c["merge_feasibility"]

    def test_no_collision_without_file_overlap(self):
        plans = _plans(("a", [
            _sub("a-1", provides=["widget"], files=["src/x.py"]),
            _sub("a-2", provides=["widget"], files=["src/y.py"]),
        ]))
        assert _duplicate_provider_merge_collisions(plans) == []

    def test_cofile_cluster_pair_excluded(self):
        plans = _plans(("a", [
            _sub("a-1", provides=["widget"], files=["src/widget.py"],
                 cluster="c1"),
            _sub("a-2", provides=["widget"], files=["src/widget.py"],
                 cluster="c1"),
        ]))
        assert _duplicate_provider_merge_collisions(plans) == []

    def test_three_way_collision_yields_a_triangle_of_pairs(self):
        plans = _plans(("a", [
            _sub("a-1", provides=["widget"], files=["src/widget.py"]),
            _sub("a-2", provides=["widget"], files=["src/widget.py"]),
            _sub("a-3", provides=["widget"], files=["src/widget.py"]),
        ]))
        collisions = _duplicate_provider_merge_collisions(plans)
        pairs = {frozenset((c["a_sid"], c["b_sid"])) for c in collisions}
        assert pairs == {
            frozenset(("a-1", "a-2")),
            frozenset(("a-1", "a-3")),
            frozenset(("a-2", "a-3")),
        }
        assert all(c["resolution"] == "merge" for c in collisions)


class TestDuplicateProviderMergeApply:
    def test_pair_merges_to_a_single_survivor(self):
        plans = _plans(("a", [
            _sub("a-1", provides=["widget"], files=["src/widget.py"]),
            _sub("a-2", provides=["widget"], files=["src/widget.py"]),
        ]))
        collisions = _duplicate_provider_merge_collisions(plans)
        applied = _apply_overlap_collisions(plans, collisions)
        assert _ids(plans) == ["a-1"]
        assert any(a["action"] == "merge" for a in applied)
        survivor = plans[0]["subtasks"][0]
        # Intent carries forward — nothing silently discarded.
        assert "intent a-2" in survivor["intent"]

    def test_three_participant_collision_merges_to_one_survivor_no_dangling_dep(
        self,
    ):
        plans = _plans(("a", [
            _sub("a-1", provides=["widget"], files=["src/widget.py"]),
            _sub("a-2", provides=["widget"], files=["src/widget.py"]),
            _sub("a-3", provides=["widget"], files=["src/widget.py"]),
        ]), ("b", [
            _sub("b-1", depends_on=["a-3"]),
        ]))
        before_ids = {"a-1", "a-2", "a-3", "b-1"}
        collisions = _duplicate_provider_merge_collisions(plans)
        applied = _apply_overlap_collisions(plans, collisions)

        remaining_ids = set(_ids(plans))
        # Exactly one of the three duplicate-provider subtasks survives.
        assert len(remaining_ids & {"a-1", "a-2", "a-3"}) == 1
        survivor = next(iter(remaining_ids & {"a-1", "a-2", "a-3"}))

        # No subtask id is both dropped AND left as a dangling dependency
        # target: b-1's depends_on must never reference a vanished sid.
        by_id = {s["id"]: s for p in plans for s in p.get("subtasks", [])}
        for dep in by_id["b-1"]["depends_on"]:
            assert dep in remaining_ids, (
                f"dangling dependency target: {dep!r} not in {remaining_ids!r}"
            )
        # Every dropped participant's intent survived into the merged survivor.
        for dropped in before_ids & {"a-1", "a-2", "a-3"} - {survivor}:
            assert f"intent {dropped}" in by_id[survivor]["intent"]

        # No skipped_would_cycle / unresolved entries for this trivial DAG.
        assert not any(a.get("action") == "skipped_would_cycle"
                        for a in applied)

    def test_no_collisions_is_a_no_op(self):
        plans = _plans(("a", [_sub("a-1")]))
        before = copy.deepcopy(plans)
        collisions = _duplicate_provider_merge_collisions(plans)
        assert collisions == []
        assert plans == before

    def test_subtask_missing_id_is_tolerated_and_never_indexed(self):
        """A subtask dict with no `id` key must not crash the indexing pass
        (the `if sid:` guard) and must never be indexable as a collision
        participant."""
        idless = {
            "title": "no-id", "intent": "i", "success_criteria_seed": "c",
            "runs_commands": [], "files_likely_touched": ["src/widget.py"],
            "provides": ["widget"], "requires": [], "depends_on": [],
            "size": "small",
        }
        plans = _plans(("a", [
            idless,
            _sub("a-2", provides=["widget"], files=["src/widget.py"]),
        ]))
        collisions = _duplicate_provider_merge_collisions(plans)
        assert collisions == []

    def test_non_string_and_blank_provides_tags_are_skipped(self):
        """A `provides` entry that is not a non-blank string (None, an int,
        or whitespace-only) must be skipped rather than indexed as a
        collision-triggering tag."""
        plans = _plans(("a", [
            _sub("a-1", provides=[None, 42, "   ", "widget"],
                 files=["src/widget.py"]),
            _sub("a-2", provides=["widget"], files=["src/widget.py"]),
        ]))
        collisions = _duplicate_provider_merge_collisions(plans)
        assert len(collisions) == 1
        assert collisions[0]["artifact"] == "widget"
