"""Tests for M2 (perf-002): phase_plan's expansion loop runs the top-level
subtasks' `_recursive_decompose` calls under bounded concurrency
(`asyncio.Semaphore(caps['max_parallel'])` + `_gather_or_cancel`), mirroring
`_filter_satisfied_subtasks`'s accumulate-as-they-complete shape at
:8886, instead of one-at-a-time inside a plain `for` loop.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import _run


_CATEGORY = "feature-implementation"  # a real entry in CATEGORY_ABBREV


def _make_phase_plan_state(leerie, skip_repo_map: bool = False) -> MagicMock:
    st = MagicMock()
    st.data = {
        "categories": [_CATEGORY],
        "answers": {"source_of_truth": "codebase"},
        "current_phase": "",
        "skip_repo_map": skip_repo_map,
    }
    st.leerie_root = Path("/tmp/fake-leerie-root")
    st.save = MagicMock()
    st.bump_workers = MagicMock()
    return st


def _make_phase_plan_caps(leerie, max_parallel: int) -> dict:
    caps = dict(leerie.DEFAULT_CAPS)
    caps["max_parallel"] = max_parallel
    caps["confidence_rounds"] = 8
    caps["planner_samples"] = 1
    caps["planner_check_rounds"] = 1
    return caps


def _subtask(sid: str, path: str) -> dict:
    return {
        "id": sid,
        "title": f"Subtask {sid}",
        "success_criteria_seed": "crit",
        "files_likely_touched": [path],
        "intent": "part",
        "scope_note": "",
        "depends_on": [],
        "requires": [],
        "provides": [],
        "size": "small",
        "investigation_notes": "",
    }



def _planner_response(subtasks: list[dict]) -> dict:
    return {
        "domain": _CATEGORY,
        "status": "ready",
        "confidence": {"root_cause": 9.0, "solution": 9.0, "basis": "ok",
                       "falsifiers_tested": [], "contradictions_reconciled": [],
                       "gap_to_close": {}},
        "subtasks": subtasks,
    }


def _drive_phase_plan(leerie, st, caps, models, efforts, planner_resp,
                       fake_recursive_decompose):
    with (
        patch.object(leerie, "_load_prompt", return_value="sys"),
        patch.object(leerie, "_build_repo_map",
                     side_effect=RuntimeError("no tree-sitter")),
        patch.object(leerie, "claude_p",
                     new=AsyncMock(return_value=planner_resp)),
        patch.object(leerie, "check_planner_output", return_value=[]),
        patch.object(leerie, "_recursive_decompose",
                     new=AsyncMock(side_effect=fake_recursive_decompose)),
    ):
        return _run(leerie.phase_plan("task", st, caps, models, efforts))


class TestConcurrentDecomposition:
    def test_two_subtasks_overlap_in_time(self, leerie):
        """Two top-level subtasks whose _recursive_decompose calls each
        sleep must overlap — not run strictly back-to-back — under a
        max_parallel cap that permits both at once. This is exactly the
        sum-of-latencies vs wall-clock parallelism measurement used to
        detect the pre-fix defect (M2 investigation_notes: 143 calls at
        ~0.7x parallelism)."""
        st = _make_phase_plan_state(leerie)
        caps = _make_phase_plan_caps(leerie, max_parallel=4)
        models = {k: leerie.MODEL_DEFAULT for k in leerie.WORKER_TYPES}
        efforts = {k: None for k in leerie.WORKER_TYPES}
        planner_resp = json.loads(json.dumps(_planner_response(
            [_subtask("feat-001", "a.ts"), _subtask("feat-002", "b.ts")])))

        SLEEP_S = 0.15
        intervals: list[tuple[float, float]] = []

        async def fake_recursive_decompose(subtask, depth, st_, caps_,
                                            models_, efforts_, repo_root_,
                                            **kwargs):
            start = asyncio.get_event_loop().time()
            await asyncio.sleep(SLEEP_S)
            end = asyncio.get_event_loop().time()
            intervals.append((start, end))
            return [subtask]

        import time
        t0 = time.monotonic()
        _drive_phase_plan(leerie, st, caps, models, efforts, planner_resp,
                           fake_recursive_decompose)
        wall = time.monotonic() - t0

        assert len(intervals) == 2
        (s1, e1), (s2, e2) = intervals
        overlap = min(e1, e2) - max(s1, s2)
        assert overlap > 0, (
            f"the two _recursive_decompose calls did not overlap in time "
            f"(intervals={intervals}); the expansion loop is still "
            f"strictly sequential"
        )
        # Sum of latencies is ~2*SLEEP_S; a strictly sequential loop would
        # take at least that long. Concurrent execution should land well
        # under the sum (with generous slack for CI jitter).
        assert wall < (2 * SLEEP_S) * 0.9, (
            f"wall clock ({wall:.3f}s) was not meaningfully shorter than "
            f"strictly-sequential sum-of-latencies ({2 * SLEEP_S:.3f}s)"
        )

    def test_concurrency_bounded_by_max_parallel(self, leerie):
        """No more than caps['max_parallel'] _recursive_decompose calls may
        be in flight at once, even with many more top-level subtasks
        available to run."""
        st = _make_phase_plan_state(leerie)
        MAX_PARALLEL = 2
        caps = _make_phase_plan_caps(leerie, max_parallel=MAX_PARALLEL)
        models = {k: leerie.MODEL_DEFAULT for k in leerie.WORKER_TYPES}
        efforts = {k: None for k in leerie.WORKER_TYPES}
        subtasks = [_subtask(f"feat-{i:03d}", f"f{i}.ts") for i in range(6)]
        planner_resp = json.loads(json.dumps(_planner_response(subtasks)))

        in_flight = 0
        max_in_flight = 0

        async def fake_recursive_decompose(subtask, depth, st_, caps_,
                                            models_, efforts_, repo_root_,
                                            **kwargs):
            nonlocal in_flight, max_in_flight
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
            await asyncio.sleep(0.02)
            in_flight -= 1
            return [subtask]

        _drive_phase_plan(leerie, st, caps, models, efforts, planner_resp,
                           fake_recursive_decompose)

        assert max_in_flight <= MAX_PARALLEL, (
            f"observed {max_in_flight} concurrent _recursive_decompose "
            f"calls, exceeding caps['max_parallel']={MAX_PARALLEL}"
        )
        assert max_in_flight > 1, (
            "expected some overlap under a max_parallel cap > 1 — got no "
            "concurrency at all, which would also (vacuously) satisfy the "
            "bound above"
        )

    def test_decompose_snapshot_accumulates_under_concurrency(self, leerie):
        """st.data['decompose_snapshot'] must still accumulate every
        completed top-level subtask's leaves and be saved on each
        completion under concurrent execution — the accumulate-on-
        completion property tests/test_decompose_snapshot.py's
        TestDecomposeSnapshotPersistence class already asserts for the
        (formerly sequential) loop must also hold now that completion
        order is nondeterministic."""
        st = _make_phase_plan_state(leerie)
        caps = _make_phase_plan_caps(leerie, max_parallel=4)
        models = {k: leerie.MODEL_DEFAULT for k in leerie.WORKER_TYPES}
        efforts = {k: None for k in leerie.WORKER_TYPES}
        subtasks = [_subtask(f"feat-{i:03d}", f"f{i}.ts") for i in range(4)]
        planner_resp = json.loads(json.dumps(_planner_response(subtasks)))

        # Vary sleep duration so completion order differs from first_pass
        # (declaration) order — feat-003 finishes first, feat-000 last.
        DELAYS = {"feat-000": 0.08, "feat-001": 0.06,
                  "feat-002": 0.04, "feat-003": 0.02}

        async def fake_recursive_decompose(subtask, depth, st_, caps_,
                                            models_, efforts_, repo_root_,
                                            **kwargs):
            await asyncio.sleep(DELAYS[subtask["id"]])
            return [subtask]

        plans = _drive_phase_plan(leerie, st, caps, models, efforts,
                                   planner_resp, fake_recursive_decompose)

        snap = st.data.get("decompose_snapshot")
        assert snap is not None
        leaf_ids = {leaf["id"] for leaf in snap["leaves"]}
        assert leaf_ids == {"feat-000", "feat-001", "feat-002", "feat-003"}
        assert {s["id"] for s in plans[0]["subtasks"]} == leaf_ids
        # Snapshot was saved once per top-level subtask completion (plus
        # whatever else phase_plan saves).
        assert st.save.call_count >= 4

    def test_crash_after_partial_completion_preserves_finished_leaves(self, leerie):
        """A WorkerError from one top-level subtask's _recursive_decompose
        must not discard leaves already accumulated from top-level
        subtasks that finished before it, even though completion order is
        now driven by the concurrent scheduler rather than list order —
        mirrors tests/test_decompose_snapshot.py's
        test_snapshot_populated_with_completed_subtasks_before_crash."""
        st = _make_phase_plan_state(leerie)
        caps = _make_phase_plan_caps(leerie, max_parallel=4)
        models = {k: leerie.MODEL_DEFAULT for k in leerie.WORKER_TYPES}
        efforts = {k: None for k in leerie.WORKER_TYPES}
        subtasks = [_subtask("feat-fast", "a.ts"), _subtask("feat-slow", "b.ts")]
        planner_resp = json.loads(json.dumps(_planner_response(subtasks)))

        async def fake_recursive_decompose(subtask, depth, st_, caps_,
                                            models_, efforts_, repo_root_,
                                            **kwargs):
            if subtask["id"] == "feat-fast":
                return [subtask]
            # feat-slow: finishes after feat-fast, then crashes.
            await asyncio.sleep(0.03)
            raise leerie.WorkerError(
                "Failed to authenticate: OAuth session expired")

        with pytest.raises(leerie.WorkerError):
            _drive_phase_plan(leerie, st, caps, models, efforts,
                               planner_resp, fake_recursive_decompose)

        snap = st.data.get("decompose_snapshot")
        assert snap is not None, (
            "decompose_snapshot must be populated from feat-fast's "
            "completed leaves even though feat-slow's crash aborted the "
            "run overall."
        )
        leaf_ids = {leaf["id"] for leaf in snap["leaves"]}
        assert leaf_ids == {"feat-fast"}, (
            f"expected only feat-fast's completed leaves in the snapshot "
            f"(feat-slow crashed before finishing), got {leaf_ids}"
        )
