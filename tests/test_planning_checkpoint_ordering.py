"""Source-coupling pin for the planning-checkpoint write-ordering
invariant (DESIGN §6 "Resumable planning — a per-phase checkpoint cursor,
not a `waves` gate").

Modeled directly on `tests/test_plan_snapshot_wiring.py`'s
`TestSnapshotPrecedesTheDieGates` — the established prior art for this
tier: `_run_phases` spawns real workers and shells out to git/preflight,
so driving it to a real mid-phase pause is not something this module
attempts (that is `tests/test_resume_planning_reentry.py`'s job, via a
fully-stubbed pipeline). This module is pure `inspect.getsource` +
string-index comparison — the same discipline as
`tests/test_plans_after_checkpoints.py`, `tests/test_dep_capture_wiring.py`,
and `tests/test_phase_plan_recursion_wiring.py`.

The invariant this guards: every planning phase stamps `current_phase` at
ENTRY, before it spends (DESIGN §6's "#1 implementation trap") — so
`current_phase` alone is never proof a phase's output is safe to reuse on
resume. Each `plans_after_<phase>` checkpoint must be written strictly
AFTER its phase call returns and must be followed by `st.save()` — an
in-memory-only assignment is lost on pause/crash, and a checkpoint written
before the phase call would mark an incomplete phase as done, so a resume
would skip it and continue with a half-built plan.
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

PHASE_CALL_FOR_KEY = {
    "plans_after_classify": (
        "await phase_classify(task, st, caps, args.clarify, models, efforts)"
    ),
    "plans_after_plan": "plans = await phase_plan(task, st, caps, models, efforts)",
    "plans_after_reconcile": (
        "plans = await phase_reconcile(plans, task, st, caps, models, efforts)"
    ),
    "plans_after_overlap_judge": "await phase_overlap_judge(",
    "plans_after_adherence_gate": "await phase_adherence_gate(",
}


def _phases_src(leerie) -> str:
    return inspect.getsource(leerie._run_phases)


class TestCheckpointFollowsItsPhaseCall:
    """The load-bearing ordering: index(phase call) < index(checkpoint
    assignment) for every planning phase. Moving any assignment above its
    phase call — i.e. reproducing the current_phase-at-entry mistake for a
    plans_after_* key — must fail this class."""

    def test_classify_checkpoint_follows_classify_call(self, leerie):
        src = _phases_src(leerie)
        call = src.find(PHASE_CALL_FOR_KEY["plans_after_classify"])
        key = src.find('st.data["plans_after_classify"]')
        assert call != -1, "_run_phases must call phase_classify"
        assert key != -1, 'missing st.data["plans_after_classify"] assignment'
        assert call < key, (
            "plans_after_classify must be written AFTER phase_classify "
            "returns, never before — current_phase is stamped at entry, "
            "and the checkpoint must not repeat that mistake."
        )

    def test_plan_checkpoint_follows_plan_call(self, leerie):
        src = _phases_src(leerie)
        call = src.find(PHASE_CALL_FOR_KEY["plans_after_plan"])
        key = src.find('st.data["plans_after_plan"]')
        assert call != -1, "_run_phases must call phase_plan"
        assert key != -1, 'missing st.data["plans_after_plan"] assignment'
        assert call < key

    def test_reconcile_checkpoint_follows_reconcile_call(self, leerie):
        src = _phases_src(leerie)
        call = src.find(PHASE_CALL_FOR_KEY["plans_after_reconcile"])
        key = src.find('st.data["plans_after_reconcile"]')
        assert call != -1, "_run_phases must call phase_reconcile"
        assert key != -1, 'missing st.data["plans_after_reconcile"] assignment'
        assert call < key

    def test_overlap_judge_checkpoint_follows_overlap_judge_call(self, leerie):
        src = _phases_src(leerie)
        call = src.find(PHASE_CALL_FOR_KEY["plans_after_overlap_judge"])
        key = src.find('st.data["plans_after_overlap_judge"]')
        assert call != -1, "_run_phases must call phase_overlap_judge"
        assert key != -1, (
            'missing st.data["plans_after_overlap_judge"] assignment')
        assert call < key

    def test_adherence_gate_checkpoint_follows_adherence_gate_call(self, leerie):
        src = _phases_src(leerie)
        call = src.find(PHASE_CALL_FOR_KEY["plans_after_adherence_gate"])
        key = src.find('st.data["plans_after_adherence_gate"]')
        assert call != -1, "_run_phases must call phase_adherence_gate"
        assert key != -1, (
            'missing st.data["plans_after_adherence_gate"] assignment')
        assert call < key

    def test_filters_checkpoint_follows_both_filter_calls(self, leerie):
        src = _phases_src(leerie)
        offtree = src.find("filter_offtree_subtasks(plans, Path(os.getcwd()),")
        satisfied = src.find(
            "satisfied_no_work = await filter_satisfied_subtasks(")
        key = src.find('st.data["plans_after_filters"]')
        assert offtree != -1, "_run_phases must call filter_offtree_subtasks"
        assert satisfied != -1, (
            "_run_phases must call filter_satisfied_subtasks")
        assert key != -1, 'missing st.data["plans_after_filters"] assignment'
        assert offtree < key
        assert satisfied < key


class TestCheckpointIsSavedImmediately:
    """An assignment without a following st.save() never reaches disk —
    the only place --resume can read it back from."""

    def test_each_checkpoint_followed_by_save(self, leerie):
        src = _phases_src(leerie)
        for key in CHECKPOINT_KEYS_IN_ORDER:
            idx = src.find(f'st.data["{key}"]')
            assert idx != -1, f"missing assignment for {key}"
            after = src[idx:idx + 200]
            assert "st.save()" in after, (
                f'st.data["{key}"] must be followed by st.save() within '
                "~200 chars; an in-memory-only assignment is lost on "
                "pause/crash."
            )


class TestCheckpointsAppearInPipelineOrder:
    """The keys' first-occurrence order in source must match the pipeline
    order the phases actually run in — a scrambled insertion could still
    pass the per-key ordering tests above (each is only compared against
    its own phase call) while producing a resume cursor that silently
    skips a phase."""

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


class TestResumeCursorReferencesOutputKeys:
    """The resume cursor lives inline in `_run_phases` — for both a fresh
    run and a resumed one, each planning phase is guarded by `if
    "plans_after_<phase>" not in st.data:` (skip + reuse the persisted
    value if present, otherwise invoke the phase and checkpoint its
    output). This pins that the cursor derives from checkpoint-key
    PRESENCE, never from `current_phase` alone — the #1 implementation
    trap this whole feature exists to avoid, since current_phase is
    stamped at phase entry, before the phase spends."""

    def test_cursor_gates_on_each_checkpoint_key_presence(self, leerie):
        src = _phases_src(leerie)
        missing = [
            key for key in CHECKPOINT_KEYS_IN_ORDER
            if f'"{key}" not in st.data' not in src
        ]
        assert not missing, (
            "_run_phases must gate re-entry into each planning phase on "
            f"checkpoint-key absence (not on current_phase alone): {missing}"
        )

    def test_earliest_resume_reentry_gate_checks_categories_not_current_phase(
        self, leerie
    ):
        """The one genuinely-unresumable case (no progress at all, not
        even phase_classify started) is detected via absence of both
        `waves` and `categories` — never by inspecting `current_phase`,
        which would be stamped even for a phase that has not finished."""
        src = _phases_src(leerie)
        assert '"waves" not in st.data and "categories" not in st.data' in src, (
            "the earliest resume re-entry gate must key on output-key "
            "presence (waves / categories), not on current_phase"
        )
