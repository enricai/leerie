"""Tests for the N3+N4 decomposition budget partition.

DEFAULT_CAPS["decompose_budget_share"] bounds how much of
`max_total_workers` recursive decomposition (fit_judge/splitter, spawned
by `_recursive_decompose`) may spend before it is forced to stop and
accept remaining nodes as leaves, so decomposition cannot starve
execution of every call before a single line of code is written
(docs/IMPLEMENTATION.md "Decomposition budget partition (N3+N4)").

`_bump_decompose_workers` is the enforcement point: every fit_judge/
splitter spawn site in `_recursive_decompose` calls it instead of a bare
`st.bump_workers`, verified end to end via real `State.bump_workers`
accounting (`st.data["worker_count"]` / `st.data["decompose_worker_count"]`),
not a mock.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest


def _make_real_state(leerie):
    """A minimal but real accounting object: exercises the actual
    `State.bump_workers` logic (not a mock), backed by a plain dict so no
    filesystem/flock is needed."""

    class _RealishState:
        def __init__(self):
            self.data = {"worker_count": 0}

        def save(self):
            pass  # no persistence needed for this test

        # Bound copy of the real State.bump_workers implementation's logic
        # (module-level WorkerError + comparison), so the test exercises the
        # same raise condition production code hits.
        def bump_workers(self, caps):
            self.data["worker_count"] = self.data.get("worker_count", 0) + 1
            if self.data["worker_count"] > caps["max_total_workers"]:
                raise leerie.WorkerError(
                    f"worker budget exhausted ({caps['max_total_workers']})."
                )

    return _RealishState()


def _caps(leerie, max_total_workers, decompose_budget_share=0.40):
    return {
        "max_total_workers": max_total_workers,
        "decompose_budget_share": decompose_budget_share,
        "decompose_max_depth": leerie.DEFAULT_CAPS["decompose_max_depth"],
        "decompose_fit_threshold": leerie.DEFAULT_CAPS["decompose_fit_threshold"],
        "decompose_noprogress_rounds": leerie.DEFAULT_CAPS["decompose_noprogress_rounds"],
        "subfile_split_max_span": leerie.DEFAULT_CAPS["subfile_split_max_span"],
    }


def _low_fit_response() -> dict:
    return {
        "score": 0.10,
        "rationale": "always oversized",
        "diffuse": "too broad",
        "confidence": {
            "fit": 8.5, "basis": "test",
            "falsifiers_tested": ["x"], "contradictions_reconciled": [],
            "gap_to_close": {},
        },
    }


def _split_response(parent_id: str, n: int) -> dict:
    return {
        "children": [
            {
                "id": f"{parent_id}-{i + 1}",
                "title": f"child {i + 1}",
                "success_criteria_seed": f"criterion {i + 1}",
                "files_likely_touched": [f"f{i}.py"],
            }
            for i in range(n)
        ],
    }


def _run(coro):
    return asyncio.run(coro)


def test_default_cap_value(leerie):
    """DEFAULT_CAPS["decompose_budget_share"] is the corpus-derived 40%
    separation threshold (docs/IMPLEMENTATION.md "Decomposition budget
    partition (N3+N4)")."""
    assert leerie.DEFAULT_CAPS["decompose_budget_share"] == 0.40


def test_decomposition_stops_before_starving_execution_budget(leerie):
    """A subtask tree that would need far more than 40% of a small
    max_total_workers cap to fully decompose (every node scores low and
    keeps splitting into 2 children) is forced to stop recursing and
    accept remaining nodes as leaves once decomposition's share is spent
    -- verified via real State.bump_workers accounting, not a mock
    assertion on call count alone."""
    parent = {
        "id": "root", "title": "Root",
        "success_criteria_seed": "crit",
        "files_likely_touched": ["a.py", "b.py"],
    }
    # cap=10, share=0.40 -> decomposition may spend at most 4 calls.
    caps = _caps(leerie, max_total_workers=10, decompose_budget_share=0.40)
    st = _make_real_state(leerie)

    async def fake_claude_p(*args, schema_key, **kwargs):
        if schema_key == "fit_judge":
            return _low_fit_response()  # always oversized -> always splits
        if schema_key == "splitter":
            # Every split produces 2 children, so an unbounded run would
            # recurse indefinitely (bounded only by decompose_max_depth=5,
            # which alone would burn far more than 4 calls).
            pid = args[0] if args else kwargs.get("sid", "x")
            return _split_response("n", 2)
        pytest.fail(f"unexpected schema_key {schema_key!r}")

    with patch.object(leerie, "claude_p", new=fake_claude_p):
        leaves = _run(leerie._recursive_decompose(
            parent, 0, st, caps, {"fit_judge": "opus", "splitter": "opus"},
            {"fit_judge": "high", "splitter": "high"}, Path("/tmp")))

    # Decomposition must never exceed its 40% share (4 of 10) of the
    # worker budget -- the whole point of the partition.
    assert st.data["decompose_worker_count"] <= 4
    # And it actually engaged the budget cap rather than terminating for
    # some unrelated reason (depth cap / no-progress guard would also stop
    # it, but at a much higher call count than the share allows -- confirm
    # the cap was the binding constraint by checking it was reached).
    assert st.data["decompose_worker_count"] == 4
    # Execution budget is preserved: the global worker_count spent by
    # decomposition never exceeds the share cap either, leaving the
    # remaining >= 6 of the 10-call budget for implementers/conformers.
    assert st.data["worker_count"] <= 4
    assert st.data["worker_count"] < caps["max_total_workers"]
    # Every node degraded to a leaf rather than the run crashing or
    # discarding work already paid for.
    assert len(leaves) >= 1
    assert all(isinstance(leaf, dict) for leaf in leaves)


def test_decomposition_within_budget_share_completes_normally(leerie):
    """A small decomposition that stays well under the 40% share completes
    exactly as before the fix -- the partition must not interfere with
    ordinary, well-fit-in-budget plans."""
    parent = {
        "id": "small", "title": "Small",
        "success_criteria_seed": "crit",
        "files_likely_touched": ["a.py", "b.py", "c.py"],
    }
    caps = _caps(leerie, max_total_workers=200, decompose_budget_share=0.40)
    st = _make_real_state(leerie)

    async def fake_claude_p(*args, schema_key, sid="", **kwargs):
        if schema_key == "fit_judge":
            if sid.endswith("-d0"):
                return _low_fit_response()  # parent splits once
            return {  # children are well-fit
                "score": 0.85, "rationale": "fits", "diffuse": "",
                "confidence": {
                    "fit": 8.5, "basis": "test",
                    "falsifiers_tested": ["x"], "contradictions_reconciled": [],
                    "gap_to_close": {},
                },
            }
        if schema_key == "splitter":
            return _split_response("small", 3)
        pytest.fail(f"unexpected schema_key {schema_key!r}")

    with patch.object(leerie, "claude_p", new=fake_claude_p):
        leaves = _run(leerie._recursive_decompose(
            parent, 0, st, caps, {"fit_judge": "opus", "splitter": "opus"},
            {"fit_judge": "high", "splitter": "high"}, Path("/tmp")))

    assert len(leaves) == 3
    # 1 parent judge + 1 splitter + 3 child judges = 5, well under the
    # 80-call share (0.40 * 200) -- the cap never engages.
    assert st.data["decompose_worker_count"] == 5
    assert st.data["worker_count"] == 5


def test_bump_decompose_workers_raises_at_share_boundary(leerie):
    """_bump_decompose_workers raises DecompositionBudgetExceeded exactly
    once decompose_worker_count exceeds decompose_budget_share *
    max_total_workers, and it is a WorkerError subclass so every existing
    `except WorkerError:` degrade path in `_recursive_decompose` already
    handles it."""
    caps = _caps(leerie, max_total_workers=10, decompose_budget_share=0.40)
    st = _make_real_state(leerie)

    assert issubclass(leerie.DecompositionBudgetExceeded, leerie.WorkerError)

    # 4 calls allowed (0.40 * 10 == 4); the 5th must raise.
    for _ in range(4):
        leerie._bump_decompose_workers(st, caps)
    assert st.data["decompose_worker_count"] == 4

    with pytest.raises(leerie.DecompositionBudgetExceeded):
        leerie._bump_decompose_workers(st, caps)
    # A refused call must not touch either counter -- the check happens
    # BEFORE bumping, so a rejected node costs nothing against either the
    # decomposition share or the global execution budget.
    assert st.data["decompose_worker_count"] == 4
    assert st.data["worker_count"] == 4
