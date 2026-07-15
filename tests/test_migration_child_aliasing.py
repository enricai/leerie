"""`_migration_child` must give each child its own list objects.

The dict literal inherits the parent's graph edges with
`subtask.get("depends_on", [])` — which hands every child *the same list
object* as the parent and as each other. Nothing in the return value is
copied, so `kids[0]["provides"] is kids[1]["provides"] is parent["provides"]`.

That is live, not theoretical: `_apply_overlap_drop` unions the dropped
subtask's tags into the survivor with `surv_provides.append(tag)` — an
in-place mutation. If the survivor is a migration child, the tag lands on
every sibling too, and `_build_predecessor_graph` turns each one into real
predecessor edges for every consumer that `requires` it. A subtask silently
gains dependencies it never declared.

This is the same class as DESIGN §5 *Id-vanishing operations*: a graph edge
materializing without any op declaring it. The remedy is the same in spirit —
the code owns the invariant, so the code enforces it by construction rather
than by every future caller remembering to rebind instead of mutate.

Assertions are by *identity*, deliberately. The existing migration tests
assert by value (`==`) and pass either way, which is exactly why this survived.
"""
from __future__ import annotations


def _parent() -> dict:
    return {
        "id": "config-003",
        "depends_on": ["config-002"],
        "provides": ["cfg"],
        "requires": [{"tag": "schema", "extent": "in_plan"}],
        "intent": "migrate config",
        "scope_note": "",
        "investigation_notes": "",
    }


class TestChildrenOwnTheirLists:
    """Each child gets its own list objects — not the parent's, not a
    sibling's."""

    def test_siblings_do_not_share_list_objects(self, leerie):
        p = _parent()
        a = leerie._migration_child(p, ["a.py"], "config-003-1", "T", "C")
        b = leerie._migration_child(p, ["b.py"], "config-003-2", "T", "C")
        for field in ("depends_on", "requires", "provides"):
            assert a[field] is not b[field], (
                f"siblings share one {field!r} list object; an in-place "
                f"mutation on one child silently rewrites the other"
            )

    def test_children_do_not_share_with_parent(self, leerie):
        p = _parent()
        a = leerie._migration_child(p, ["a.py"], "config-003-1", "T", "C")
        for field in ("depends_on", "requires", "provides"):
            assert a[field] is not p[field], (
                f"child shares the parent's {field!r} list object"
            )

    def test_values_are_still_inherited(self, leerie):
        """Copying must not change the contract: children still inherit the
        parent's edges by value (DESIGN §5½ — the graph edges carry over;
        only the files are re-decided)."""
        p = _parent()
        a = leerie._migration_child(p, ["a.py"], "config-003-1", "T", "C")
        assert a["depends_on"] == ["config-002"]
        assert a["provides"] == ["cfg"]
        assert a["requires"] == [{"tag": "schema", "extent": "in_plan"}]
        assert a["files_likely_touched"] == ["a.py"]

    def test_mutating_one_child_does_not_touch_siblings(self, leerie):
        """The direct expression of the bug, independent of any caller."""
        p = _parent()
        a = leerie._migration_child(p, ["a.py"], "config-003-1", "T", "C")
        b = leerie._migration_child(p, ["b.py"], "config-003-2", "T", "C")
        a["provides"].append("earned-by-a-only")
        assert "earned-by-a-only" not in b["provides"]
        assert "earned-by-a-only" not in p["provides"]

    def test_missing_parent_fields_default_independently(self, leerie):
        """A parent with no edges must not hand every child the *same*
        default list either — `dict.get(k, [])` returns a fresh list per call
        here, but pin it so a future refactor to a shared module-level
        default is caught."""
        p = {"id": "config-003", "intent": "", "scope_note": "",
             "investigation_notes": ""}
        a = leerie._migration_child(p, ["a.py"], "config-003-1", "T", "C")
        b = leerie._migration_child(p, ["b.py"], "config-003-2", "T", "C")
        assert a["depends_on"] == [] and b["depends_on"] == []
        assert a["depends_on"] is not b["depends_on"]


class TestOverlapDropDoesNotLeakTagsToSiblings:
    """The live path where the aliasing bites: `_apply_overlap_drop` unions
    the dropped subtask's `provides` into the survivor in place."""

    def test_dropping_into_one_child_does_not_tag_its_siblings(self, leerie):
        p = _parent()
        kids = [leerie._migration_child(p, ["a.py"], "config-003-1", "T", "C"),
                leerie._migration_child(p, ["b.py"], "config-003-2", "T", "C")]
        donor = {"id": "feat-009", "provides": ["extra-tag"], "depends_on": [],
                 "requires": [], "title": "t", "success_criteria_seed": "c",
                 "size": "small", "files_likely_touched": ["z.py"]}
        plans = [{"domain": "d", "status": "ready", "subtasks": kids + [donor]}]

        leerie._apply_overlap_drop(plans, "feat-009", "config-003-1")

        assert "extra-tag" in kids[0]["provides"], (
            "the survivor must absorb the dropped subtask's tags")
        assert "extra-tag" not in kids[1]["provides"], (
            "a sibling gained a `provides` tag it never earned — "
            "_build_predecessor_graph turns that into real predecessor edges "
            "for every consumer that requires it"
        )
