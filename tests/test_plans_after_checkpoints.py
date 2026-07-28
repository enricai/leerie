"""Source-coupling pins for the per-phase planning checkpoints (DESIGN §6
"Resumable planning — a per-phase checkpoint cursor, not a `waves` gate").

This is the producer half of the fix (bugfix-003): after each planning
phase in `_run_phases`'s fresh-run branch completes, its output is written
to a phase-named `plans_after_*` key and `st.save()`'d. The resume path
(a separate subtask) reads these keys back; this module only pins that
they are written, in the right order, and strictly AFTER the phase call
that produces them — never at phase entry, mirroring
`tests/test_plan_snapshot_wiring.py`'s `inspect.getsource` approach (that
module is the direct model: `_run_phases` spawns real workers and shells
out to git/preflight, so driving it end-to-end in a unit test is not
feasible — source-coupling is the correct tier here, same as
`test_dep_capture_wiring.py` and `test_phase_plan_recursion_wiring.py`).

The #1 trap this guards against (per the subtask's investigation notes):
every planning phase stamps `current_phase` at ENTRY, before it spends —
so `current_phase` alone is never proof a phase's output is safe to
reuse. The checkpoint key must be written only after the phase call
returns.
"""
from __future__ import annotations

import inspect

CHECKPOINT_KEYS_IN_ORDER = (
    "plans_after_classify",
    "plans_after_plan",
    "plans_after_reconcile",
    "plans_after_overlap_judge",
    "plans_after_adherence_gate",
    "plans_after_filters",
)


def _phases_src(leerie) -> str:
    return inspect.getsource(leerie._run_phases)


class TestEachCheckpointIsWrittenAndSaved:
    def test_all_keys_present(self, leerie):
        src = _phases_src(leerie)
        missing = [
            k for k in CHECKPOINT_KEYS_IN_ORDER
            if f'st.data["{k}"]' not in src
        ]
        assert not missing, (
            f"_run_phases must checkpoint these plans_after_* keys: {missing}"
        )

    def test_each_assignment_is_saved(self, leerie):
        """An assignment without a following st.save() never reaches disk —
        the only place --resume can read it back from."""
        src = _phases_src(leerie)
        for key in CHECKPOINT_KEYS_IN_ORDER:
            idx = src.find(f'st.data["{key}"]')
            assert idx != -1, f"missing assignment for {key}"
            after = src[idx:idx + 200]
            assert "st.save()" in after, (
                f'st.data["{key}"] must be followed by st.save(); an '
                "in-memory-only assignment is lost on pause/crash."
            )


class TestCheckpointsFollowTheirPhaseCall:
    """Each key must be written strictly AFTER its phase call returns —
    never at entry. This is the load-bearing ordering the whole feature
    depends on (DESIGN §6's "#1 implementation trap")."""

    PHASE_CALL_FOR_KEY = {
        "plans_after_classify": (
            "await phase_classify(task, st, caps, args.clarify, models, "
            "efforts)"
        ),
        "plans_after_plan": "plans = await phase_plan(task, st, caps, models, efforts)",
        "plans_after_reconcile": (
            "plans = await phase_reconcile(plans, task, st, caps, models, "
            "efforts)"
        ),
        "plans_after_overlap_judge": "await phase_overlap_judge(",
        "plans_after_adherence_gate": "await phase_adherence_gate(",
    }

    def test_classify_checkpoint_follows_classify_call(self, leerie):
        src = _phases_src(leerie)
        call = src.find(self.PHASE_CALL_FOR_KEY["plans_after_classify"])
        key = src.find('st.data["plans_after_classify"]')
        assert call != -1, "_run_phases must call phase_classify"
        assert key != -1
        assert call < key, (
            "plans_after_classify must be written AFTER phase_classify "
            "returns, not before (current_phase is stamped at entry — "
            "the checkpoint must not repeat that mistake)."
        )

    def test_plan_checkpoint_follows_plan_call(self, leerie):
        src = _phases_src(leerie)
        call = src.find(self.PHASE_CALL_FOR_KEY["plans_after_plan"])
        key = src.find('st.data["plans_after_plan"]')
        assert call != -1, "_run_phases must call phase_plan"
        assert key != -1
        assert call < key

    def test_reconcile_checkpoint_follows_reconcile_call(self, leerie):
        src = _phases_src(leerie)
        call = src.find(self.PHASE_CALL_FOR_KEY["plans_after_reconcile"])
        key = src.find('st.data["plans_after_reconcile"]')
        assert call != -1, "_run_phases must call phase_reconcile"
        assert key != -1
        assert call < key

    def test_reconcile_checkpoint_precedes_no_work_short_circuit(self, leerie):
        """detect_no_work can `return` immediately after phase_reconcile —
        the reconcile checkpoint must land before that short-circuit or a
        run that turns out to have work loses it."""
        src = _phases_src(leerie)
        key = src.find('st.data["plans_after_reconcile"]')
        no_work = src.find("no_work_map = detect_no_work(plans)")
        assert key != -1
        assert no_work != -1, "_run_phases must call detect_no_work(plans)"
        assert key < no_work

    def test_overlap_judge_checkpoint_follows_overlap_judge_call(self, leerie):
        src = _phases_src(leerie)
        call = src.find(self.PHASE_CALL_FOR_KEY["plans_after_overlap_judge"])
        key = src.find('st.data["plans_after_overlap_judge"]')
        assert call != -1, "_run_phases must call phase_overlap_judge"
        assert key != -1
        assert call < key

    def test_adherence_gate_checkpoint_follows_adherence_gate_call(self, leerie):
        src = _phases_src(leerie)
        call = src.find(self.PHASE_CALL_FOR_KEY["plans_after_adherence_gate"])
        key = src.find('st.data["plans_after_adherence_gate"]')
        assert call != -1, "_run_phases must call phase_adherence_gate"
        assert key != -1
        assert call < key

    def test_filters_checkpoint_follows_both_filters(self, leerie):
        src = _phases_src(leerie)
        offtree = src.find("filter_offtree_subtasks(plans, Path(os.getcwd()),")
        satisfied = src.find(
            "satisfied_no_work = await filter_satisfied_subtasks("
        )
        key = src.find('st.data["plans_after_filters"]')
        assert offtree != -1, "_run_phases must call filter_offtree_subtasks"
        assert satisfied != -1, (
            "_run_phases must call filter_satisfied_subtasks"
        )
        assert key != -1
        assert offtree < satisfied < key

    def test_filters_checkpoint_precedes_satisfied_no_work_short_circuit_check(
        self, leerie
    ):
        """The satisfied-probe sweep can itself return a no-work map that
        short-circuits via _finish_no_work_run — the filters checkpoint
        must land after that check resolves (plans_after_filters is written
        only for a run that has real work to schedule)."""
        src = _phases_src(leerie)
        key = src.find('st.data["plans_after_filters"]')
        no_work_check = src.find("if satisfied_no_work is not None:")
        assert key != -1
        assert no_work_check != -1
        assert no_work_check < key

    def test_filters_checkpoint_precedes_schedule(self, leerie):
        src = _phases_src(leerie)
        key = src.find('st.data["plans_after_filters"]')
        sched = src.find("subtasks, waves = schedule(plans)")
        assert key != -1
        assert sched != -1, "_run_phases must call schedule(plans)"
        assert key < sched


class TestCheckpointsAppearInPipelineOrder:
    """The keys' first-occurrence order in source must match the pipeline
    order — a scrambled insertion would still pass the per-key ordering
    tests above but silently produce a resume cursor that skips ahead."""

    def test_source_order_matches_pipeline_order(self, leerie):
        src = _phases_src(leerie)
        positions = [
            (key, src.find(f'st.data["{key}"]'))
            for key in CHECKPOINT_KEYS_IN_ORDER
        ]
        for key, pos in positions:
            assert pos != -1, f"missing {key}"
        sorted_by_pos = sorted(positions, key=lambda kv: kv[1])
        assert [k for k, _ in sorted_by_pos] == list(CHECKPOINT_KEYS_IN_ORDER)


class TestPlanSnapshotStillAuthoritativeAfterSchedule:
    """plan_snapshot (existing, DESIGN §6) remains the post-schedule
    checkpoint — this subtask must not disturb its established ordering
    relative to schedule()/the die() gates, already pinned in
    tests/test_plan_snapshot_wiring.py. Pinned again here, narrowly, as a
    regression guard against this subtask's edits landing in the wrong
    place relative to plan_snapshot."""

    def test_plans_after_filters_precedes_plan_snapshot(self, leerie):
        src = _phases_src(leerie)
        filters_key = src.find('st.data["plans_after_filters"]')
        snapshot_key = src.find('st.data["plan_snapshot"]')
        assert filters_key != -1
        assert snapshot_key != -1
        assert filters_key < snapshot_key
