"""Integration test: recursive_decompose leaves feed schedule() into waves.

Proves the seam between Layer B (P1 recursive decomposition) and the
existing Phase 3 scheduler (DESIGN §5½ (P1) end-of-pipeline claim):

  recursive_decompose() → flat leaf set → schedule(plans) → topo-sorted waves

Tests verify:
  - A stubbed low-fit migration subtask decomposes into leaf subtasks
    with valid ids/provides/depends_on that schedule() and validate_plan()
    accept without errors.
  - Independent leaves land in wave 0; a dependent leaf lands in wave 1.
  - Leaf ids preserve a valid domain prefix so schedule() cross-domain
    wiring works and validate_plan()'s id-prefix check passes.
  - validate_plan() accepts the full leaf set without errors.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers (mirrored from test_schedule_blocked.py conventions)
# ---------------------------------------------------------------------------

def _good_subtask(sid: str = "deps-001", **overrides) -> dict:
    """A well-formed subtask with a valid domain prefix."""
    base = {
        "id": sid,
        "title": "a migration subtask",
        "depends_on": [],
        "requires": [],
        "provides": [],
        "success_criteria_seed": "the migration is done",
        "size": "medium",
        "files_likely_touched": ["src/a.ts"],
    }
    base.update(overrides)
    return base


def _ready_plan(domain: str, *subtasks: dict) -> dict:
    """A planner output with status=ready and the given subtasks."""
    return {
        "domain": domain,
        "status": "ready",
        "subtasks": list(subtasks),
    }


def _make_state(leerie, caps: dict) -> MagicMock:
    """Minimal State-like object with bump_workers tracking."""
    st = MagicMock()
    st.data = {"worker_count": 0}

    def bump(c):
        st.data["worker_count"] += 1
        if st.data["worker_count"] > c.get("max_total_workers", 200):
            raise leerie.WorkerError("budget exhausted")

    st.bump_workers = MagicMock(side_effect=bump)
    return st


def _make_caps(leerie, **overrides) -> dict:
    caps = {
        "max_total_workers": 200,
        "decompose_max_depth": leerie.DEFAULT_CAPS["decompose_max_depth"],
        "decompose_fit_threshold": leerie.DEFAULT_CAPS["decompose_fit_threshold"],
        "decompose_noprogress_rounds": leerie.DEFAULT_CAPS["decompose_noprogress_rounds"],
    }
    caps.update(overrides)
    return caps


def _fit_response(score: float) -> dict:
    return {
        "score": score,
        "rationale": f"score={score}",
        "diffuse": "" if score >= 0.70 else "too broad",
        "confidence": {
            "fit": 8.5, "basis": "test",
            "falsifiers_tested": ["x"], "contradictions_reconciled": [],
            "gap_to_close": {},
        },
    }


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Test 1: leaf ids carry valid domain prefix through to schedule()
# ---------------------------------------------------------------------------

def test_leaf_ids_preserve_domain_prefix(leerie):
    """Leaves from recursive_decompose carry a valid domain prefix so
    schedule()'s cross-domain wiring and validate_plan()'s id-prefix
    check both accept the leaf set."""
    big_files = [f"src/migrate_{i:03d}.ts" for i in range(16)]
    parent = _good_subtask(
        sid="deps-sweep",
        title="Date-fns sweep",
        success_criteria_seed="all 16 files migrated",
        files_likely_touched=big_files,
    )
    caps = _make_caps(leerie)
    st = _make_state(leerie, caps)
    models = {"fit_judge": "opus", "splitter": "opus"}
    efforts = {"fit_judge": "high", "splitter": "high"}

    async def fake_claude_p(*args, schema_key, sid="", user_prompt="", **kwargs):
        if schema_key == "fit_judge":
            # Parent (depth 0) is low-fit; children (depth 1) are well-fit.
            if sid.endswith("-d0"):
                return _fit_response(0.20)
            return _fit_response(0.85)
        if schema_key == "splitter":
            # Label-only mode: return distinct titles per pre-assigned chunk id.
            import re
            ids = re.findall(r'"id": "(deps-sweep-\d+)"', user_prompt)
            return {"children": [
                {"id": i, "title": f"Labeled {i}",
                 "success_criteria_seed": f"crit {i}"} for i in ids]}
        pytest.fail(f"unexpected schema_key {schema_key!r} (sid={sid!r})")

    with patch.object(leerie, "claude_p", new=fake_claude_p):
        leaves = _run(leerie.recursive_decompose(
            parent, 0, st, caps, models, efforts, Path("/tmp")))

    # 16 files / 8 per chunk = 2 leaf subtasks
    assert len(leaves) == 2
    # Each leaf id inherits the deps-sweep base id and therefore starts with
    # "deps-" — a valid _ID_PREFIXES prefix.
    for leaf in leaves:
        assert leaf["id"].startswith("deps-"), (
            f"leaf id {leaf['id']!r} must start with 'deps-' so "
            "validate_plan's id-prefix check passes"
        )


# ---------------------------------------------------------------------------
# Test 2: leaf set feeds schedule() and produces correct wave structure
# ---------------------------------------------------------------------------

def test_leaves_feed_schedule_correct_waves(leerie, capsys):
    """Leaves produced by recursive_decompose (stubbed) pass through
    schedule() and emerge in the correct topo-sorted waves.

    Topology:
      deps-chunk-1  (independent)  → wave 0
      deps-chunk-2  (independent)  → wave 0
      deps-verify   depends on deps-chunk-1 and deps-chunk-2  → wave 1
    """
    leaf_a = _good_subtask(
        sid="deps-chunk-1",
        provides=["migration-chunk-1-done"],
    )
    leaf_b = _good_subtask(
        sid="deps-chunk-2",
        provides=["migration-chunk-2-done"],
    )
    leaf_c = _good_subtask(
        sid="deps-verify",
        success_criteria_seed="verify all chunks migrated",
        depends_on=["deps-chunk-1", "deps-chunk-2"],
        requires=[
            {"tag": "migration-chunk-1-done", "extent": "in_plan"},
            {"tag": "migration-chunk-2-done", "extent": "in_plan"},
        ],
    )

    plan = _ready_plan("dependency-migration", leaf_a, leaf_b, leaf_c)
    subtasks, waves = leerie.schedule([plan])

    # All three leaves are in the merged map.
    assert set(subtasks.keys()) == {"deps-chunk-1", "deps-chunk-2", "deps-verify"}

    # Wave 0: both independent chunks (order may vary — compare as sets).
    assert len(waves) == 2
    assert set(waves[0]) == {"deps-chunk-1", "deps-chunk-2"}

    # Wave 1: the verifier depends on both chunks.
    assert waves[1] == ["deps-verify"]

    # schedule() must not emit a WARNING (no blocked domains).
    out = capsys.readouterr().out
    assert "WARNING" not in out


# ---------------------------------------------------------------------------
# Test 3: validate_plan accepts the leaf set without errors
# ---------------------------------------------------------------------------

def test_leaves_survive_validate_plan(leerie):
    """validate_plan() must accept the full leaf set from recursive_decompose:
    valid id prefixes, non-empty success_criteria_seed, and in-plan
    requires resolved by a sibling's provides."""
    leaf_a = _good_subtask(
        sid="deps-chunk-1",
        provides=["migration-chunk-1-done"],
    )
    leaf_b = _good_subtask(
        sid="deps-chunk-2",
        depends_on=["deps-chunk-1"],
        requires=[{"tag": "migration-chunk-1-done", "extent": "in_plan"}],
    )

    subtasks = {s["id"]: s for s in [leaf_a, leaf_b]}
    # validate_plan() calls die() on any error; no error means it passes.
    leerie.validate_plan(subtasks)


# ---------------------------------------------------------------------------
# Test 4: end-to-end seam — recursive_decompose leaves → schedule() → waves
# ---------------------------------------------------------------------------

def test_recursive_decompose_to_schedule_seam(leerie, capsys):
    """Full seam: run recursive_decompose on an oversized migration subtask
    with stubbed claude_p, take the leaves, build a ready plan, call
    schedule(), and assert the wave structure is valid.

    This is the '92% leaves → schedule()' end-of-pipeline claim from the
    task spec, exercised in code (not assumed)."""
    big_files = [f"src/component_{i:03d}.ts" for i in range(24)]
    parent = _good_subtask(
        sid="deps-datefns",
        title="Migrate date-fns across 24 files",
        success_criteria_seed="all 24 files use date-fns v3",
        files_likely_touched=big_files,
        provides=["datefns-migration-done"],
    )
    caps = _make_caps(leerie)
    st = _make_state(leerie, caps)
    models = {"fit_judge": "opus", "splitter": "opus"}
    efforts = {"fit_judge": "high", "splitter": "high"}

    async def fake_claude_p(*args, schema_key, sid="", user_prompt="", **kwargs):
        if schema_key == "fit_judge":
            if sid.endswith("-d0"):
                return _fit_response(0.15)   # oversized → split
            return _fit_response(0.80)        # children → leaf
        if schema_key == "splitter":
            # Label-only mode: partition_files owns the file assignment; the
            # splitter only titles each pre-computed chunk (DESIGN §5½).
            import re
            ids = re.findall(r'"id": "(deps-datefns-\d+)"', user_prompt)
            return {"children": [
                {"id": i, "title": f"Labeled {i}",
                 "success_criteria_seed": f"crit {i}"} for i in ids]}
        pytest.fail(f"unexpected schema_key {schema_key!r} (sid={sid!r})")

    with patch.object(leerie, "claude_p", new=fake_claude_p):
        leaves = _run(leerie.recursive_decompose(
            parent, 0, st, caps, models, efforts, Path("/tmp")))

    # 24 files / 8 per chunk → 3 leaf subtasks.
    assert len(leaves) == 3

    # All original files covered across the leaves.
    leaf_files = [f for leaf in leaves for f in leaf.get("files_likely_touched", [])]
    assert sorted(leaf_files) == sorted(big_files)

    # Every leaf id starts with "deps-".
    for leaf in leaves:
        assert leaf["id"].startswith("deps-")

    # Feed the leaves into schedule() via a ready plan.
    plan = _ready_plan("dependency-migration", *leaves)
    subtasks, waves = leerie.schedule([plan])

    # schedule() accepted all leaves.
    assert len(subtasks) == 3

    # Leaves from partition_files are independent → all in wave 0.
    assert len(waves) == 1
    assert set(waves[0]) == {leaf["id"] for leaf in leaves}

    # validate_plan accepts the leaves without errors.
    leerie.validate_plan(subtasks)

    # No WARNING (no blocked domains).
    out = capsys.readouterr().out
    assert "WARNING" not in out
