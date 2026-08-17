"""Unit tests for the migration/decompose child-partition helpers
(DESIGN §5½ (P1) *Sub-file* / *Migration*, DESIGN §5 *Id-vanishing
operations*): `_grep_old_pattern`, `_deterministic_chunk_label`,
`_subfile_child`, `_prune_orphaned_requires`.

These back `_partition_files`, `_recursive_decompose` and
`_remap_vanished_deps` (extensively tested elsewhere) but had no direct
coverage of their own.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
LEERIE_PY = REPO_ROOT / "orchestrator" / "leerie.py"


@pytest.fixture(scope="session")
def leerie():
    spec = importlib.util.spec_from_file_location("leerie", LEERIE_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# _grep_old_pattern
# ---------------------------------------------------------------------------

def test_grep_old_pattern_finds_literal_across_allowlisted_extensions(leerie, tmp_path):
    (tmp_path / "a.ts").write_text("const x = OldWidget();\n")
    (tmp_path / "b.py").write_text("x = OldWidget()\n")
    (tmp_path / "c.txt").write_text("OldWidget mentioned in prose\n")  # not in allowlist
    (tmp_path / "d.rb").write_text("puts 'unrelated'\n")

    found = leerie._grep_old_pattern("OldWidget", tmp_path)

    assert found == {"a.ts", "b.py"}


def test_grep_old_pattern_prefers_src_subdir_when_present(leerie, tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "in_src.ts").write_text("OldWidget()\n")
    (tmp_path / "outside.ts").write_text("OldWidget()\n")  # outside src/, must not be searched

    found = leerie._grep_old_pattern("OldWidget", tmp_path)

    assert found == {"src/in_src.ts"}
    assert "outside.ts" not in found


def test_grep_old_pattern_returns_relative_paths(leerie, tmp_path):
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "deep.js").write_text("OldWidget();\n")

    found = leerie._grep_old_pattern("OldWidget", tmp_path)

    assert found == {"nested/deep.js"}
    for f in found:
        assert not Path(f).is_absolute()


def test_grep_old_pattern_no_match_returns_empty_set(leerie, tmp_path):
    (tmp_path / "a.ts").write_text("nothing relevant here\n")

    assert leerie._grep_old_pattern("OldWidget", tmp_path) == set()


def test_grep_old_pattern_missing_dir_degrades_to_empty_set(leerie, tmp_path):
    missing = tmp_path / "does-not-exist"

    assert leerie._grep_old_pattern("OldWidget", missing) == set()


# ---------------------------------------------------------------------------
# _deterministic_chunk_label
# ---------------------------------------------------------------------------

def test_deterministic_chunk_label_distinct_per_index(leerie):
    subtask = {"title": "Migrate widgets", "success_criteria_seed": "all sites migrated"}
    chunks = [["a.ts"], ["b.ts"], ["c.ts"]]

    labels = [
        leerie._deterministic_chunk_label(subtask, chunk, idx, len(chunks))
        for idx, chunk in enumerate(chunks)
    ]
    titles = [t for t, _ in labels]
    criteria = [c for _, c in labels]

    assert len(set(titles)) == len(titles)
    assert len(set(criteria)) == len(criteria)


def test_deterministic_chunk_label_shape(leerie):
    subtask = {"title": "Migrate widgets", "success_criteria_seed": "all sites migrated"}
    title, criteria = leerie._deterministic_chunk_label(
        subtask, ["a.ts", "b.ts"], 1, 3)

    assert title == "Migrate widgets (part 2/3)"
    assert "all sites migrated" in criteria
    assert "a.ts, b.ts" in criteria


def test_deterministic_chunk_label_same_index_different_files_still_distinct(leerie):
    subtask = {"title": "Migrate widgets", "success_criteria_seed": "seed"}
    _, criteria_a = leerie._deterministic_chunk_label(subtask, ["a.ts"], 0, 2)
    _, criteria_b = leerie._deterministic_chunk_label(subtask, ["z.ts"], 0, 2)

    # same idx/total but different chunk contents -> criteria (file list) differ
    assert criteria_a != criteria_b


# ---------------------------------------------------------------------------
# _subfile_child
# ---------------------------------------------------------------------------

def _base_parent():
    return {
        "id": "feat-005",
        "title": "Thread FrameTarget",
        "success_criteria_seed": "all sites routed",
        "intent": "Route FrameTarget through the flow runner",
        "scope_note": "flow-runner.ts only",
        "files_likely_touched": ["flow-runner.ts"],
        "depends_on": ["feat-004"],
        "requires": [{"tag": "frame-shape", "extent": "in_plan"}],
        "provides": ["frame-target"],
        "investigation_notes": "see flow-runner.ts:1-2000",
    }


def test_subfile_child_owns_region(leerie):
    parent = _base_parent()
    child = leerie._subfile_child(
        parent, "flow-runner.ts", (1, 700), ["runFrame", "FrameTarget"],
        "feat-005-r1", "flow-runner.ts", 0, 3)

    assert child["id"] == "feat-005-r1"
    assert child["files_likely_touched"] == ["flow-runner.ts"]
    assert child["owned_region"] == {
        "file": "flow-runner.ts", "start": 1, "end": 700,
        "symbols": ["runFrame", "FrameTarget"],
    }
    assert child["_cofile_cluster"] == "flow-runner.ts"
    assert "lines 1-700" in child["title"]
    assert "lines 1-700" in child["success_criteria_seed"]
    assert "lines 1-700" in child["intent"]


def test_subfile_child_deep_copies_requires_and_provides_no_aliasing(leerie):
    """Mirrors the _migration_child aliasing discipline (CLAUDE.md,
    tests/test_recursive_decompose.py's peel test): a region child's
    depends_on/requires/provides must not alias the parent's lists/dicts,
    or a later in-place edit (e.g. _apply_overlap_drop, _prune_orphaned_requires)
    on the child silently mutates the parent (and vice versa)."""
    parent = _base_parent()
    child = leerie._subfile_child(
        parent, "flow-runner.ts", (1, 700), ["runFrame"],
        "feat-005-r1", "flow-runner.ts", 0, 2)

    assert child["depends_on"] == parent["depends_on"]
    assert child["depends_on"] is not parent["depends_on"]

    assert child["requires"] == parent["requires"]
    assert child["requires"] is not parent["requires"]
    assert child["requires"][0] is not parent["requires"][0]  # nested dict deep-copied

    assert child["provides"] == parent["provides"]
    assert child["provides"] is not parent["provides"]

    # mutating the child must not leak back to the parent
    child["depends_on"].append("feat-999")
    child["requires"][0]["tag"] = "mutated"
    child["provides"].append("new-tag")

    assert parent["depends_on"] == ["feat-004"]
    assert parent["requires"][0]["tag"] == "frame-shape"
    assert parent["provides"] == ["frame-target"]


def test_subfile_child_intent_is_region_scoped_not_verbatim(leerie):
    """Unlike _migration_child (verbatim intent inheritance is safe because
    chunks own disjoint files), region children co-own the SAME file, so
    the intent must be scoped to the region to give plan_overlap_judge a
    textual signal distinguishing siblings (DESIGN §5 *Cross-domain surface
    overlap*)."""
    parent = _base_parent()
    child = leerie._subfile_child(
        parent, "flow-runner.ts", (701, 1400), ["otherFn"],
        "feat-005-r2", "flow-runner.ts", 1, 3)

    assert child["intent"] != parent["intent"]
    assert parent["intent"] in child["intent"]
    assert "lines 701-1400" in child["intent"]
    assert "sibling subtask owns the rest" in child["intent"]


def test_subfile_child_no_symbols_in_range(leerie):
    parent = _base_parent()
    child = leerie._subfile_child(
        parent, "flow-runner.ts", (1, 50), [], "feat-005-r1",
        "flow-runner.ts", 0, 2)

    assert "(no named symbols in range)" in child["intent"]
    assert "(no named symbols in range)" in child["success_criteria_seed"]


# ---------------------------------------------------------------------------
# _prune_orphaned_requires
# ---------------------------------------------------------------------------

def _plan_with(subtasks):
    return {"domain": "test", "subtasks": subtasks}


def test_prune_orphaned_requires_removes_tag_with_no_surviving_provider(leerie):
    plans = [_plan_with([
        {"id": "feat-002", "requires": [{"tag": "widget-shape", "extent": "in_plan"}]},
    ])]

    leerie._prune_orphaned_requires(plans, dropped_provides={"widget-shape"})

    assert plans[0]["subtasks"][0]["requires"] == []


def test_prune_orphaned_requires_keeps_tag_still_provided_by_a_survivor(leerie):
    """A tag that was dropped by one subtask but is ALSO provided by a
    surviving subtask (possibly in a different plan) must be kept."""
    plans = [
        _plan_with([
            {"id": "feat-002", "requires": [{"tag": "widget-shape", "extent": "in_plan"}]},
        ]),
        _plan_with([
            {"id": "feat-010", "provides": ["widget-shape"]},
        ]),
    ]

    leerie._prune_orphaned_requires(plans, dropped_provides={"widget-shape"})

    assert plans[0]["subtasks"][0]["requires"] == [
        {"tag": "widget-shape", "extent": "in_plan"}]


def test_prune_orphaned_requires_leaves_never_provided_tag_intact(leerie):
    """A tag no subtask ever provided (a genuine planner error) must not
    be silently pruned — _validate_plan needs to still surface it."""
    plans = [_plan_with([
        {"id": "feat-002", "requires": [{"tag": "nonexistent-tag", "extent": "in_plan"}]},
    ])]

    leerie._prune_orphaned_requires(plans, dropped_provides={"widget-shape"})

    assert plans[0]["subtasks"][0]["requires"] == [
        {"tag": "nonexistent-tag", "extent": "in_plan"}]


def test_prune_orphaned_requires_leaves_other_tags_and_subtasks_untouched(leerie):
    plans = [_plan_with([
        {
            "id": "feat-002",
            "requires": [
                {"tag": "widget-shape", "extent": "in_plan"},
                {"tag": "kept-tag", "extent": "in_plan"},
            ],
        },
        {"id": "feat-003", "requires": [{"tag": "kept-tag", "extent": "in_plan"}]},
    ])]

    leerie._prune_orphaned_requires(plans, dropped_provides={"widget-shape"})

    assert plans[0]["subtasks"][0]["requires"] == [
        {"tag": "kept-tag", "extent": "in_plan"}]
    assert plans[0]["subtasks"][1]["requires"] == [
        {"tag": "kept-tag", "extent": "in_plan"}]


def test_prune_orphaned_requires_empty_dropped_provides_is_noop(leerie):
    plans = [_plan_with([
        {"id": "feat-002", "requires": [{"tag": "widget-shape", "extent": "in_plan"}]},
    ])]
    original = plans[0]["subtasks"][0]["requires"]

    leerie._prune_orphaned_requires(plans, dropped_provides=set())

    # short-circuits before touching anything
    assert plans[0]["subtasks"][0]["requires"] is original


def test_prune_orphaned_requires_operates_across_all_plans_at_once(leerie):
    """A per-plan prune would wrongly drop a tag a surviving subtask in a
    *different* plan still provides — this pins the cross-plan gate."""
    plans = [
        _plan_with([
            {"id": "feat-a", "requires": [{"tag": "shared-tag", "extent": "in_plan"}]},
        ]),
        _plan_with([
            {"id": "feat-b", "provides": ["shared-tag"]},
            {"id": "feat-c", "requires": [{"tag": "shared-tag", "extent": "in_plan"}]},
        ]),
    ]

    leerie._prune_orphaned_requires(plans, dropped_provides={"shared-tag"})

    for plan in plans:
        for s in plan["subtasks"]:
            if s["id"] in ("feat-a", "feat-c"):
                assert s["requires"] == [{"tag": "shared-tag", "extent": "in_plan"}]
