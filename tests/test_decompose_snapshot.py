"""Tests for the D3 decompose crash barrier + decompose_snapshot (DESIGN §5½
(P1), §6 *Credential strategy*).

Three behaviors, tested as one unit per the subtask scope note — a crash
barrier without the snapshot cannot show what survived:

1. `recursive_decompose`: a `WorkerError` from the `fit_judge` call degrades
   the node to a **leaf** (`[subtask]` unchanged), matching the existing
   depth-cap and no-progress-guard precedents — it does not propagate and
   discard sibling subtasks' already-completed fit/split decisions.

2. `recursive_decompose`: a `WorkerError` from the coupled-minority
   `splitter` call (the non-migration split path) degrades the node to a
   **leaf** the same way — this is the surviving half of D3: the fit_judge
   guard alone left the splitter call ~70 lines below unguarded, so a crash
   there still discarded every fit/split decision already paid for in
   sibling subtasks.

3. `phase_plan`'s expansion loop persists `st.data["decompose_snapshot"]`
   after each top-level subtask finishes expanding, mirroring
   `plan_snapshot`'s assignment-then-`st.save()` ordering
   (`tests/test_plan_snapshot_wiring.py`), so a later subtask's crash does
   not discard subtasks that already finished.
"""
from __future__ import annotations

import asyncio
import inspect
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers (mirrors tests/test_recursive_decompose.py and
# tests/test_phase_plan_recursion_wiring.py)
# ---------------------------------------------------------------------------

def _make_decompose_state(leerie, caps):
    st = MagicMock()
    st.data = {"worker_count": 0}

    def bump(c):
        st.data["worker_count"] += 1
        count = st.data["worker_count"]
        if count > c.get("max_total_workers", 200):
            raise leerie.WorkerError("budget exhausted")

    st.bump_workers = MagicMock(side_effect=bump)
    return st


def _make_decompose_caps(leerie, **overrides):
    caps = {
        "max_total_workers": 200,
        "decompose_max_depth": leerie.DEFAULT_CAPS["decompose_max_depth"],
        "decompose_fit_threshold": leerie.DEFAULT_CAPS["decompose_fit_threshold"],
        "decompose_noprogress_rounds": leerie.DEFAULT_CAPS["decompose_noprogress_rounds"],
    }
    caps.update(overrides)
    return caps


def _run(coro):
    return asyncio.run(coro)


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


def _make_phase_plan_caps(leerie) -> dict:
    caps = dict(leerie.DEFAULT_CAPS)
    caps["max_parallel"] = 1
    caps["confidence_rounds"] = 8
    caps["planner_samples"] = 1
    caps["planner_check_rounds"] = 1
    return caps


_SUBTASK_A = {
    "id": "feat-001",
    "title": "First subtask",
    "success_criteria_seed": "crit a",
    "files_likely_touched": ["src/a.ts"],
    "intent": "part a",
    "scope_note": "",
    "depends_on": [],
    "requires": [],
    "provides": [],
    "size": "small",
    "investigation_notes": "",
}

_SUBTASK_B = {**_SUBTASK_A, "id": "feat-002", "title": "Second subtask",
              "files_likely_touched": ["src/b.ts"]}

_PLANNER_RESPONSE = {
    "domain": _CATEGORY,
    "status": "ready",
    "confidence": {"root_cause": 9.0, "solution": 9.0, "basis": "ok",
                   "falsifiers_tested": [], "contradictions_reconciled": [],
                   "gap_to_close": {}},
    "subtasks": [_SUBTASK_A, _SUBTASK_B],
}


# ---------------------------------------------------------------------------
# 1. recursive_decompose: fit_judge WorkerError degrades node to leaf
# ---------------------------------------------------------------------------

class TestFitJudgeCrashBarrier:
    def test_fit_judge_crash_degrades_to_leaf(self, leerie):
        """A WorkerError from claude_p on the fit_judge call must not
        propagate — the node is accepted as a leaf, unchanged, exactly like
        the depth-cap and no-progress-guard precedents."""
        subtask = {"id": "t-001", "title": "Some task",
                   "success_criteria_seed": "crit",
                   "files_likely_touched": ["a.py"]}
        caps = _make_decompose_caps(leerie)
        st = _make_decompose_state(leerie, caps)
        models = {"fit_judge": "opus", "splitter": "opus"}
        efforts = {"fit_judge": "high", "splitter": "high"}

        async def crashing_claude_p(*args, schema_key, **kwargs):
            assert schema_key == "fit_judge"
            raise leerie.WorkerError("Failed to authenticate: OAuth session expired")

        with patch.object(leerie, "claude_p", new=crashing_claude_p):
            leaves = _run(leerie.recursive_decompose(
                subtask, 0, st, caps, models, efforts, Path("/tmp")))

        assert leaves == [subtask]

    def test_fit_judge_crash_does_not_raise(self, leerie):
        """The WorkerError must be caught inside recursive_decompose, not
        left to propagate to the caller."""
        subtask = {"id": "t-002", "title": "Another task",
                   "success_criteria_seed": "crit",
                   "files_likely_touched": ["b.py"]}
        caps = _make_decompose_caps(leerie)
        st = _make_decompose_state(leerie, caps)
        models = {"fit_judge": "opus", "splitter": "opus"}
        efforts = {"fit_judge": "high", "splitter": "high"}

        async def crashing_claude_p(*args, **kwargs):
            raise leerie.WorkerError("PID exhaustion")

        with patch.object(leerie, "claude_p", new=crashing_claude_p):
            # Must not raise.
            leaves = _run(leerie.recursive_decompose(
                subtask, 0, st, caps, models, efforts, Path("/tmp")))
        assert leaves == [subtask]


# ---------------------------------------------------------------------------
# 2. recursive_decompose: coupled-minority splitter WorkerError degrades to
#    leaf (the surviving half of D3 — the fit_judge guard alone left this
#    call unguarded)
# ---------------------------------------------------------------------------

class TestSplitterCrashBarrier:
    def test_splitter_crash_degrades_to_leaf(self, leerie):
        """fit_judge scores below threshold (forcing the split path); the
        subsequent splitter call raises WorkerError. recursive_decompose
        must return [subtask] unchanged, not propagate the crash."""
        subtask = {"id": "t-003", "title": "Coupled task",
                   "success_criteria_seed": "crit",
                   "files_likely_touched": ["a.py", "b.py"]}
        caps = _make_decompose_caps(leerie)
        st = _make_decompose_state(leerie, caps)
        models = {"fit_judge": "opus", "splitter": "opus"}
        efforts = {"fit_judge": "high", "splitter": "high"}

        async def fake_claude_p(*args, schema_key, **kwargs):
            if schema_key == "fit_judge":
                return {"score": 0.1, "rationale": "diffuse", "diffuse": "x",
                         "confidence": {"root_cause": 9.0, "solution": 9.0,
                                        "basis": "ok", "falsifiers_tested": [],
                                        "contradictions_reconciled": [],
                                        "gap_to_close": {}}}
            assert schema_key == "splitter"
            raise leerie.WorkerError(
                "Failed to authenticate: OAuth session expired")

        with patch.object(leerie, "claude_p", new=fake_claude_p):
            leaves = _run(leerie.recursive_decompose(
                subtask, 0, st, caps, models, efforts, Path("/tmp")))

        assert leaves == [subtask]

    def test_splitter_crash_does_not_raise(self, leerie):
        """The WorkerError from the splitter call must be caught inside
        recursive_decompose, not left to propagate to the caller."""
        subtask = {"id": "t-004", "title": "Another coupled task",
                   "success_criteria_seed": "crit",
                   "files_likely_touched": ["c.py"]}
        caps = _make_decompose_caps(leerie)
        st = _make_decompose_state(leerie, caps)
        models = {"fit_judge": "opus", "splitter": "opus"}
        efforts = {"fit_judge": "high", "splitter": "high"}

        async def fake_claude_p(*args, schema_key, **kwargs):
            if schema_key == "fit_judge":
                return {"score": 0.1, "rationale": "diffuse", "diffuse": "x",
                         "confidence": {"root_cause": 9.0, "solution": 9.0,
                                        "basis": "ok", "falsifiers_tested": [],
                                        "contradictions_reconciled": [],
                                        "gap_to_close": {}}}
            raise leerie.WorkerError("PID exhaustion")

        with patch.object(leerie, "claude_p", new=fake_claude_p):
            # Must not raise.
            leaves = _run(leerie.recursive_decompose(
                subtask, 0, st, caps, models, efforts, Path("/tmp")))
        assert leaves == [subtask]

    def test_splitter_crash_preserves_sibling_snapshot(self, leerie):
        """End-to-end through phase_plan: feat-001 finishes expanding (its
        leaves land in decompose_snapshot); feat-002's recursive_decompose
        forces the split path and then crashes on the splitter call. The
        snapshot must still contain feat-001's already-completed leaves —
        this is precisely the loss D3 set out to prevent, and the surviving
        half (the unguarded splitter call) must not reopen it."""
        st = _make_phase_plan_state(leerie)
        caps = _make_phase_plan_caps(leerie)
        models = {k: leerie.MODEL_DEFAULT for k in leerie.WORKER_TYPES}
        efforts = {k: None for k in leerie.WORKER_TYPES}
        planner_resp = json.loads(json.dumps(_PLANNER_RESPONSE))
        real_recursive_decompose = leerie.recursive_decompose

        async def fake_recursive_decompose(subtask, depth, st_, caps_,
                                           models_, efforts_, repo_root_,
                                           **kwargs):
            if subtask["id"] == "feat-001":
                return [subtask]
            # feat-002: real recursive_decompose, forced through the split
            # path and crashed on the splitter call itself.
            async def fake_claude_p(*args, schema_key, **kw):
                if schema_key == "fit_judge":
                    return {"score": 0.1, "rationale": "diffuse",
                             "diffuse": "x",
                             "confidence": {"root_cause": 9.0,
                                            "solution": 9.0, "basis": "ok",
                                            "falsifiers_tested": [],
                                            "contradictions_reconciled": [],
                                            "gap_to_close": {}}}
                raise leerie.WorkerError(
                    "Failed to authenticate: OAuth session expired")

            with patch.object(leerie, "claude_p", new=fake_claude_p):
                return await real_recursive_decompose(
                    subtask, depth, st_, caps_, models_, efforts_,
                    repo_root_, **kwargs)

        with (
            patch.object(leerie, "load_prompt", return_value="sys"),
            patch.object(leerie, "extract_task_file_structure", return_value=[]),
            patch.object(leerie, "build_repo_map",
                         side_effect=RuntimeError("no tree-sitter")),
            patch.object(leerie, "claude_p",
                         new=AsyncMock(return_value=planner_resp)),
            patch.object(leerie, "check_planner_output", return_value=[]),
            patch.object(leerie, "check_task_file_coverage", return_value=[]),
            patch.object(leerie, "recursive_decompose",
                         new=AsyncMock(side_effect=fake_recursive_decompose)),
        ):
            plans = _run(leerie.phase_plan("task", st, caps, models, efforts))

        snap = st.data.get("decompose_snapshot")
        assert snap is not None
        leaf_ids = {leaf["id"] for leaf in snap["leaves"]}
        assert leaf_ids == {"feat-001", "feat-002"}, (
            f"feat-002's splitter crash degrades it to a leaf (unchanged), "
            f"not a discard of the whole plan; feat-001's leaves must "
            f"survive alongside it, got {leaf_ids}"
        )
        assert {s["id"] for s in plans[0]["subtasks"]} == {"feat-001", "feat-002"}


# ---------------------------------------------------------------------------
# 3. phase_plan: decompose_snapshot persisted incrementally, survives a
#    sibling subtask's fit_judge crash
# ---------------------------------------------------------------------------

class TestDecomposeSnapshotPersistence:
    def test_snapshot_populated_with_completed_subtasks_before_crash(self, leerie):
        """feat-001 finishes expanding (snapshot records it); feat-002's
        recursive_decompose then raises WorkerError. The snapshot must still
        contain feat-001's already-completed leaves — that work is not
        discarded even though the run as a whole cannot proceed past the
        crash."""
        st = _make_phase_plan_state(leerie)
        caps = _make_phase_plan_caps(leerie)
        models = {k: leerie.MODEL_DEFAULT for k in leerie.WORKER_TYPES}
        efforts = {k: None for k in leerie.WORKER_TYPES}
        planner_resp = json.loads(json.dumps(_PLANNER_RESPONSE))

        async def fake_recursive_decompose(subtask, depth, st_, caps_, models_,
                                           efforts_, repo_root_, **kwargs):
            if subtask["id"] == "feat-001":
                return [subtask]
            raise leerie.WorkerError(
                "Failed to authenticate: OAuth session expired")

        with (
            patch.object(leerie, "load_prompt", return_value="sys"),
            patch.object(leerie, "extract_task_file_structure", return_value=[]),
            patch.object(leerie, "build_repo_map",
                         side_effect=RuntimeError("no tree-sitter")),
            patch.object(leerie, "claude_p",
                         new=AsyncMock(return_value=planner_resp)),
            patch.object(leerie, "check_planner_output", return_value=[]),
            patch.object(leerie, "check_task_file_coverage", return_value=[]),
            patch.object(leerie, "recursive_decompose",
                         new=AsyncMock(side_effect=fake_recursive_decompose)),
        ):
            with pytest.raises(leerie.WorkerError):
                _run(leerie.phase_plan("task", st, caps, models, efforts))

        snap = st.data.get("decompose_snapshot")
        assert snap is not None, (
            "decompose_snapshot must be populated even though phase_plan "
            "raised on the second subtask — it is written incrementally, "
            "not only on successful completion."
        )
        leaf_ids = {leaf["id"] for leaf in snap["leaves"]}
        assert leaf_ids == {"feat-001"}, (
            f"Expected only feat-001's completed leaves in the snapshot "
            f"(feat-002 never finished), got {leaf_ids}"
        )

    def test_leaf_count_conserved_on_full_success(self, leerie):
        """On a normal (non-crashing) run, the final decompose_snapshot's
        leaf count matches plan['subtasks'] — no leaves dropped."""
        st = _make_phase_plan_state(leerie)
        caps = _make_phase_plan_caps(leerie)
        models = {k: leerie.MODEL_DEFAULT for k in leerie.WORKER_TYPES}
        efforts = {k: None for k in leerie.WORKER_TYPES}
        planner_resp = json.loads(json.dumps(_PLANNER_RESPONSE))

        async def fake_recursive_decompose(subtask, depth, st_, caps_, models_,
                                           efforts_, repo_root_, **kwargs):
            return [subtask]

        with (
            patch.object(leerie, "load_prompt", return_value="sys"),
            patch.object(leerie, "extract_task_file_structure", return_value=[]),
            patch.object(leerie, "build_repo_map",
                         side_effect=RuntimeError("no tree-sitter")),
            patch.object(leerie, "claude_p",
                         new=AsyncMock(return_value=planner_resp)),
            patch.object(leerie, "check_planner_output", return_value=[]),
            patch.object(leerie, "check_task_file_coverage", return_value=[]),
            patch.object(leerie, "recursive_decompose",
                         new=AsyncMock(side_effect=fake_recursive_decompose)),
        ):
            plans = _run(leerie.phase_plan("task", st, caps, models, efforts))

        snap = st.data.get("decompose_snapshot")
        assert snap is not None
        assert len(snap["leaves"]) == len(plans[0]["subtasks"]) == 2

    def test_snapshot_is_saved_after_each_top_level_subtask(self, leerie):
        """st.save() is called at least once per top-level subtask expansion
        — not deferred to the end of the loop, where a later crash would
        leave it unpersisted."""
        st = _make_phase_plan_state(leerie)
        caps = _make_phase_plan_caps(leerie)
        models = {k: leerie.MODEL_DEFAULT for k in leerie.WORKER_TYPES}
        efforts = {k: None for k in leerie.WORKER_TYPES}
        planner_resp = json.loads(json.dumps(_PLANNER_RESPONSE))

        async def fake_recursive_decompose(subtask, depth, st_, caps_, models_,
                                           efforts_, repo_root_, **kwargs):
            return [subtask]

        with (
            patch.object(leerie, "load_prompt", return_value="sys"),
            patch.object(leerie, "extract_task_file_structure", return_value=[]),
            patch.object(leerie, "build_repo_map",
                         side_effect=RuntimeError("no tree-sitter")),
            patch.object(leerie, "claude_p",
                         new=AsyncMock(return_value=planner_resp)),
            patch.object(leerie, "check_planner_output", return_value=[]),
            patch.object(leerie, "check_task_file_coverage", return_value=[]),
            patch.object(leerie, "recursive_decompose",
                         new=AsyncMock(side_effect=fake_recursive_decompose)),
        ):
            _run(leerie.phase_plan("task", st, caps, models, efforts))

        # Two top-level subtasks in _PLANNER_RESPONSE → at least 2 saves
        # attributable to the snapshot (plus whatever else phase_plan saves).
        assert st.save.call_count >= 2


# ---------------------------------------------------------------------------
# 3. Source-coupling: mirrors tests/test_plan_snapshot_wiring.py
# ---------------------------------------------------------------------------

class TestSnapshotSourceCoupling:
    def test_declared_in_state_fields(self, leerie):
        assert "decompose_snapshot" in leerie.STATE_FIELDS

    def test_assignment_is_immediately_followed_by_save(self, leerie):
        src = inspect.getsource(leerie.phase_plan)
        idx = src.find('st.data["decompose_snapshot"]')
        assert idx != -1, (
            "phase_plan must write st.data[\"decompose_snapshot\"] as it "
            "expands each top-level subtask."
        )
        after = src[idx:idx + 200]
        assert "st.save()" in after, (
            'st.data["decompose_snapshot"] must be followed by st.save(); '
            "an in-memory-only assignment is lost on a crash."
        )

    def test_snapshot_written_inside_the_expansion_loop(self, leerie):
        """The assignment must sit inside the per-subtask loop (before
        plan['subtasks'] = leaves finalizes the whole plan), not after —
        otherwise a crash on a later subtask loses everything, which is
        exactly the bug this subtask fixes."""
        src = inspect.getsource(leerie.phase_plan)
        loop_pos = src.find("for subtask in first_pass:")
        snap_pos = src.find('st.data["decompose_snapshot"]')
        assign_pos = src.find('plan["subtasks"] = leaves')
        assert loop_pos != -1 and snap_pos != -1 and assign_pos != -1
        assert loop_pos < snap_pos < assign_pos, (
            "decompose_snapshot must be written inside the per-subtask "
            "expansion loop, before plan['subtasks'] is finalized — "
            "otherwise a WorkerError on a later top-level subtask discards "
            "the snapshot of subtasks that already finished."
        )

    def test_fit_judge_call_is_wrapped_in_try_except_workererror(self, leerie):
        """Source-coupling for the barrier itself: the fit_judge claude_p
        call inside recursive_decompose must be guarded."""
        src = inspect.getsource(leerie.recursive_decompose)
        judge_idx = src.find('schema_key="fit_judge"')
        assert judge_idx != -1
        try_idx = src.rfind("try:", 0, judge_idx)
        except_idx = src.find("except WorkerError:", judge_idx)
        assert try_idx != -1, (
            "the fit_judge claude_p call must be inside a try: block"
        )
        assert except_idx != -1, (
            "the fit_judge claude_p call must be followed by "
            "except WorkerError: that degrades to leaf"
        )

    def test_splitter_call_is_wrapped_in_try_except_workererror(self, leerie):
        """Source-coupling for the surviving half of D3: the coupled-minority
        splitter claude_p call inside recursive_decompose (the non-migration
        path, ~70 lines after the fit_judge guard) must also be guarded."""
        src = inspect.getsource(leerie.recursive_decompose)
        split_idx = src.find('schema_key="splitter"')
        assert split_idx != -1
        try_idx = src.rfind("try:", 0, split_idx)
        except_idx = src.find("except WorkerError:", split_idx)
        assert try_idx != -1, (
            "the coupled-minority splitter claude_p call must be inside a "
            "try: block"
        )
        assert except_idx != -1, (
            "the coupled-minority splitter claude_p call must be followed "
            "by except WorkerError: that degrades to leaf"
        )

    def test_decompose_snapshot_precedes_the_die_gates(self, leerie):
        """Cross-function ordering, mirroring
        tests/test_plan_snapshot_wiring.py: decompose_snapshot is written
        inside phase_plan's expansion loop, and _run_phases calls phase_plan
        strictly before it calls check_budget_feasibility / validate_plan —
        the two gates that die() and would otherwise make a discarded
        decomposition unrecoverable. This is what actually matters for the
        subtask's intent: the snapshot must exist on disk before either gate
        can terminate the run, not merely be well-ordered within phase_plan
        itself."""
        run_phases_src = inspect.getsource(leerie._run_phases)
        plan_call = run_phases_src.find("phase_plan(task, st, caps, models, efforts)")
        budget_gate = run_phases_src.find("check_budget_feasibility(")
        validate_gate = run_phases_src.find("validate_plan(subtasks)")
        assert plan_call != -1, "_run_phases must call phase_plan(...)"
        assert budget_gate != -1, "_run_phases must call check_budget_feasibility"
        assert validate_gate != -1, "_run_phases must call validate_plan(subtasks)"
        assert plan_call < budget_gate, (
            "phase_plan (which writes decompose_snapshot) must be called "
            "before check_budget_feasibility — that gate die()s, and the "
            "snapshot must already be on disk for the decomposition spend "
            "to be recoverable."
        )
        assert plan_call < validate_gate, (
            "phase_plan (which writes decompose_snapshot) must be called "
            "before validate_plan — that gate die()s, and the snapshot must "
            "already be on disk for the decomposition spend to be "
            "recoverable."
        )
