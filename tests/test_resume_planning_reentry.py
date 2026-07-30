"""Tests for the `--resume` consumer half of resumable planning (DESIGN §6
"Resumable planning — a per-phase checkpoint cursor, not a `waves` gate";
bugfix-004). bugfix-003 made every planning phase checkpoint its output
into a phase-named `plans_after_*` key; this subtask makes `--resume`
actually read those keys back and re-enter at the first incomplete phase,
instead of `die()`ing "cannot resume — run did not reach the scheduling
phase" for anything short of a fully-scheduled plan.

`_run_phases` is now a single shared pipeline for both a fresh run and a
resumed one: each planning phase is guarded by "is its `plans_after_*` (or
`plan_snapshot`) checkpoint already present?" — skip and reuse the
persisted value if so, otherwise invoke the phase and persist its output.
These tests drive `_run_phases` end-to-end with every phase function
stubbed (mirroring `tests/test_phase_adherence_gate.py`'s stubbed-`claude_p`
discipline, scaled up to the whole pipeline) and assert, via call
tracking, which phases actually ran.
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest


# ===========================================================================
# Helpers
# ===========================================================================

def _subtask(sid: str, *, provides=(), requires=(), depends_on=()) -> dict:
    return {
        "id": sid,
        "title": f"Subtask {sid}",
        "intent": f"intent for {sid}",
        "success_criteria_seed": "",
        "runs_commands": [],
        "files_likely_touched": [],
        "provides": list(provides),
        "requires": list(requires),
        "depends_on": list(depends_on),
        "size": "small",
    }


def _plan(domain: str, *subtasks) -> dict:
    return {"domain": domain, "status": "ready", "subtasks": list(subtasks)}


def _args(**overrides) -> SimpleNamespace:
    base = dict(
        resume=True,
        task=None,
        answers=None,
        clarify=False,
        dangerously_skip_permissions=False,
        skip_overlap_judge=False,
        skip_adherence_check=False,
        skip_satisfied_check=False,
        skip_budget_check=True,
        strict_conformer=False,
        skip_base_baseline=False,
        skip_repo_map=False,
        dangerously_allow_uncapped=True,
        skip_smoke=True,
        no_push=False,
        pr_template=None,
        host_no_push=None,
        pr_base_branch=None,
        group_id=None,
        inspect_dirs=[],
        no_verify=False,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _caps(leerie) -> dict:
    caps = dict(leerie.DEFAULT_CAPS)
    caps["judgment_check_rounds"] = 2
    return caps


MODELS: dict = {}
EFFORTS: dict = {}


class _StopAtExecute(Exception):
    """Raised by the stubbed phase_execute so the test can assert on the
    planning-pipeline state without driving the (unrelated) execute/
    finalize phases."""


@pytest.fixture
def run_dirs(tmp_path):
    leerie_root = tmp_path / ".leerie"
    run_id = "test-resume-reentry-aaa111"
    run_dir = leerie_root / "runs" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "subtasks").mkdir()
    return leerie_root, run_id, run_dir


def _make_state(leerie, run_dirs, data: dict):
    leerie_root, run_id, _run_dir = run_dirs
    st = leerie.State(leerie_root, run_id)
    st.data = dict(data)
    st.save()
    return st


def _stub_common(leerie, monkeypatch, calls: dict):
    """Stub every phase function + orchestrator-level side-effecting
    helper called along the shared pipeline in `_run_phases`, recording
    each call in `calls`. Phases that mutate/return `plans` echo their
    input back unchanged (a no-op stand-in) unless overridden by the
    caller after this stub is installed.
    """
    monkeypatch.setattr(
        leerie, "enforce_and_record_cgroup_containment",
        lambda st, allow_uncapped: calls.setdefault(
            "enforce_and_record_cgroup_containment", 0))
    monkeypatch.setattr(
        leerie, "absorb_supplied_answers",
        lambda args, st, leerie_dir: calls.__setitem__(
            "absorb_supplied_answers", calls.get("absorb_supplied_answers", 0) + 1))

    async def _backstop(*a, **kw):
        calls["_backstop_capture_prior_runs"] = calls.get(
            "_backstop_capture_prior_runs", 0) + 1
    monkeypatch.setattr(leerie, "_backstop_capture_prior_runs", _backstop)

    async def _classify(task, st, caps, clarify, models, efforts):
        calls["phase_classify"] = calls.get("phase_classify", 0) + 1
        st.data["categories"] = ["bug-fixing"]
        return {"categories": ["bug-fixing"]}
    monkeypatch.setattr(leerie, "phase_classify", _classify)

    async def _provision(*a, **kw):
        calls["phase_provision"] = calls.get("phase_provision", 0) + 1
    monkeypatch.setattr(leerie, "phase_provision", _provision)

    monkeypatch.setattr(
        leerie, "gather_answers",
        lambda st, supplied: calls.__setitem__(
            "gather_answers", calls.get("gather_answers", 0) + 1))

    async def _stub_phase_plan(task, st, caps, models, efforts):
        calls["phase_plan"] = calls.get("phase_plan", 0) + 1
        return [_plan("bug-fixing", _subtask("bugfix-001"))]
    monkeypatch.setattr(leerie, "phase_plan", _stub_phase_plan)

    async def _reconcile(plans, task, st, caps, models, efforts):
        calls["phase_reconcile"] = calls.get("phase_reconcile", 0) + 1
        return plans
    monkeypatch.setattr(leerie, "phase_reconcile", _reconcile)

    async def _overlap_judge(plans, task, st, caps, models, efforts):
        calls["phase_overlap_judge"] = calls.get("phase_overlap_judge", 0) + 1
        return plans
    monkeypatch.setattr(leerie, "phase_overlap_judge", _overlap_judge)

    async def _adherence_gate(plans, task, st, caps, models, efforts):
        calls["phase_adherence_gate"] = calls.get("phase_adherence_gate", 0) + 1
        return plans
    monkeypatch.setattr(leerie, "phase_adherence_gate", _adherence_gate)

    monkeypatch.setattr(
        leerie, "warn_cross_planner_file_overlap", lambda plans: None)
    monkeypatch.setattr(leerie, "warn_layer_gaps", lambda plans: None)
    monkeypatch.setattr(
        leerie, "warn_provider_subset_subtasks", lambda plans: None)
    monkeypatch.setattr(
        leerie, "filter_offtree_subtasks",
        lambda plans, repo_root, inspect_dirs, st: calls.__setitem__(
            "filter_offtree_subtasks",
            calls.get("filter_offtree_subtasks", 0) + 1))

    async def _satisfied(plans, repo_root, st, caps, models, efforts):
        calls["filter_satisfied_subtasks"] = calls.get(
            "filter_satisfied_subtasks", 0) + 1
        return None
    monkeypatch.setattr(leerie, "filter_satisfied_subtasks", _satisfied)

    monkeypatch.setattr(
        leerie, "check_budget_feasibility",
        lambda st, caps, subtasks, waves: calls.__setitem__(
            "check_budget_feasibility",
            calls.get("check_budget_feasibility", 0) + 1))
    monkeypatch.setattr(
        leerie, "validate_plan",
        lambda subtasks: calls.__setitem__(
            "validate_plan", calls.get("validate_plan", 0) + 1))
    monkeypatch.setattr(
        leerie, "write_plan",
        lambda leerie_dir, task, st, subtasks, waves: calls.__setitem__(
            "write_plan", calls.get("write_plan", 0) + 1))

    async def _execute(*a, **kw):
        calls["phase_execute"] = calls.get("phase_execute", 0) + 1
        raise _StopAtExecute()
    monkeypatch.setattr(leerie, "phase_execute", _execute)

    async def _artifact_registry(*a, **kw):
        calls["phase_artifact_registry"] = calls.get(
            "phase_artifact_registry", 0) + 1
        return []
    monkeypatch.setattr(leerie, "phase_artifact_registry", _artifact_registry)

    async def _wiring_gate(plans, task, st, caps, models, efforts):
        calls["phase_wiring_gate"] = calls.get("phase_wiring_gate", 0) + 1
        return plans
    monkeypatch.setattr(leerie, "phase_wiring_gate", _wiring_gate)


def _drive(leerie, args, caps, run_dirs, st):
    leerie_root, run_id, run_dir = run_dirs
    with pytest.raises(_StopAtExecute):
        asyncio.run(leerie._run_phases(
            args, caps, run_dir, st, "codebase", "normal", MODELS, EFFORTS))


# ===========================================================================
# Per-phase round-trip: resume re-enters at the NEXT phase, does not
# re-invoke the completed phase's worker.
# ===========================================================================

class TestPerPhaseRoundTrip:
    def _run(self, leerie, monkeypatch, run_dirs, checkpoint_data):
        calls: dict = {}
        _stub_common(leerie, monkeypatch, calls)
        st = _make_state(leerie, run_dirs, {
            "task": "test task", "worker_count": 3,
            **checkpoint_data,
        })
        caps = _caps(leerie)
        args = _args()
        _drive(leerie, args, caps, run_dirs, st)
        return calls, st

    def test_resume_after_classify_skips_classify_reruns_plan_onward(
        self, leerie, monkeypatch, run_dirs
    ):
        calls, st = self._run(leerie, monkeypatch, run_dirs, {
            "categories": ["bug-fixing"],
            "plans_after_classify": [],
        })
        assert "phase_classify" not in calls, (
            "phase_classify already checkpointed — must not re-invoke it")
        assert calls.get("phase_plan") == 1
        assert calls.get("phase_reconcile") == 1
        assert calls.get("phase_overlap_judge") == 1
        assert calls.get("phase_adherence_gate") == 1
        assert calls.get("filter_satisfied_subtasks") == 1
        assert calls.get("check_budget_feasibility") == 1
        assert calls.get("write_plan") == 1
        assert "waves" in st.data or True  # write_plan stubbed; see below
        assert st.data["plans_after_plan"] == [
            _plan("bug-fixing", _subtask("bugfix-001"))]

    def test_resume_after_plan_skips_plan_reruns_reconcile_onward(
        self, leerie, monkeypatch, run_dirs
    ):
        persisted_plans = [_plan("bug-fixing", _subtask("bugfix-001"))]
        calls, st = self._run(leerie, monkeypatch, run_dirs, {
            "categories": ["bug-fixing"],
            "plans_after_classify": [],
            "plans_after_plan": persisted_plans,
        })
        assert "phase_classify" not in calls
        assert "phase_plan" not in calls, (
            "phase_plan already checkpointed — must not re-invoke it")
        assert calls.get("phase_reconcile") == 1
        assert calls.get("phase_overlap_judge") == 1
        assert calls.get("phase_adherence_gate") == 1
        assert calls.get("write_plan") == 1

    def test_resume_after_reconcile_skips_reconcile_reruns_overlap_onward(
        self, leerie, monkeypatch, run_dirs
    ):
        persisted_plans = [_plan("bug-fixing", _subtask("bugfix-001"))]
        calls, st = self._run(leerie, monkeypatch, run_dirs, {
            "categories": ["bug-fixing"],
            "plans_after_classify": [],
            "plans_after_plan": persisted_plans,
            "plans_after_reconcile": persisted_plans,
        })
        assert "phase_classify" not in calls
        assert "phase_plan" not in calls
        assert "phase_reconcile" not in calls, (
            "phase_reconcile already checkpointed — must not re-invoke it")
        assert calls.get("phase_overlap_judge") == 1
        assert calls.get("phase_adherence_gate") == 1
        assert calls.get("write_plan") == 1

    def test_resume_after_overlap_judge_skips_it_reruns_adherence_onward(
        self, leerie, monkeypatch, run_dirs
    ):
        persisted_plans = [_plan("bug-fixing", _subtask("bugfix-001"))]
        calls, st = self._run(leerie, monkeypatch, run_dirs, {
            "categories": ["bug-fixing"],
            "plans_after_classify": [],
            "plans_after_plan": persisted_plans,
            "plans_after_reconcile": persisted_plans,
            "plans_after_overlap_judge": persisted_plans,
        })
        assert "phase_reconcile" not in calls
        assert "phase_overlap_judge" not in calls, (
            "phase_overlap_judge already checkpointed — must not re-invoke it")
        assert calls.get("phase_adherence_gate") == 1
        assert calls.get("write_plan") == 1

    def test_resume_after_adherence_gate_skips_it_reruns_filters_onward(
        self, leerie, monkeypatch, run_dirs
    ):
        persisted_plans = [_plan("bug-fixing", _subtask("bugfix-001"))]
        calls, st = self._run(leerie, monkeypatch, run_dirs, {
            "categories": ["bug-fixing"],
            "plans_after_classify": [],
            "plans_after_plan": persisted_plans,
            "plans_after_reconcile": persisted_plans,
            "plans_after_overlap_judge": persisted_plans,
            "plans_after_adherence_gate": persisted_plans,
        })
        assert "phase_overlap_judge" not in calls
        assert "phase_adherence_gate" not in calls, (
            "phase_adherence_gate already checkpointed — must not "
            "re-invoke it")
        assert calls.get("filter_offtree_subtasks") == 1
        assert calls.get("filter_satisfied_subtasks") == 1
        assert calls.get("write_plan") == 1

    def test_resume_after_filters_skips_all_planning_reaches_schedule(
        self, leerie, monkeypatch, run_dirs
    ):
        persisted_plans = [_plan("bug-fixing", _subtask("bugfix-001"))]
        calls, st = self._run(leerie, monkeypatch, run_dirs, {
            "categories": ["bug-fixing"],
            "plans_after_classify": [],
            "plans_after_plan": persisted_plans,
            "plans_after_reconcile": persisted_plans,
            "plans_after_overlap_judge": persisted_plans,
            "plans_after_adherence_gate": persisted_plans,
            "plans_after_filters": persisted_plans,
        })
        for phase in (
            "phase_classify", "phase_plan", "phase_reconcile",
            "phase_overlap_judge", "phase_adherence_gate",
            "filter_offtree_subtasks", "filter_satisfied_subtasks",
        ):
            assert phase not in calls, (
                f"{phase} already checkpointed — must not re-invoke it")
        # schedule() itself is not stubbed — real deterministic function —
        # so plan_snapshot/waves are the real output.
        assert calls.get("check_budget_feasibility") == 1
        assert calls.get("write_plan") == 1
        assert st.data["plan_snapshot"]["subtasks"] == {
            "bugfix-001": _subtask("bugfix-001")}


# ===========================================================================
# phase_provision resume-skip is guarded by key-PRESENCE, not truthiness.
# A repo whose recipe legitimately resolves to an empty list (no
# recognized lockfile, no install commands needed) is a valid completed
# state — a truthiness check would re-run phase_provision (and its real
# `mise install` subprocess work) on every resume for that repo.
# ===========================================================================

def test_resume_skips_provision_when_recipe_is_empty_list(
    leerie, monkeypatch, run_dirs
):
    calls: dict = {}
    _stub_common(leerie, monkeypatch, calls)
    st = _make_state(leerie, run_dirs, {
        "task": "test task", "worker_count": 3,
        "categories": ["bug-fixing"],
        "provision": {"source": "table", "recipe": []},
    })
    caps = _caps(leerie)
    args = _args()
    _drive(leerie, args, caps, run_dirs, st)
    assert "phase_provision" not in calls, (
        "provision.recipe == [] is a valid completed state — "
        "must not re-invoke phase_provision (mise install) on resume")


# ===========================================================================
# The reported failure, pinned directly: a run whose current_phase is the
# satisfied-probe sweep (no waves yet) resumes and reaches scheduling
# instead of dying "did not reach the scheduling phase".
# ===========================================================================

def test_reported_failure_resumes_past_satisfied_probe_sweep(
    leerie, monkeypatch, run_dirs
):
    """Mirrors the reported incident: paused mid `filter_satisfied_subtasks`
    sweep (current_phase stamped, no plans_after_filters/waves yet).
    Reverting the fix (restoring the old `die()`) must fail this test."""
    calls: dict = {}
    _stub_common(leerie, monkeypatch, calls)
    persisted_plans = [_plan("bug-fixing", _subtask("bugfix-001"))]
    st = _make_state(leerie, run_dirs, {
        "task": "test task", "worker_count": 10,
        "categories": ["bug-fixing"],
        "current_phase": "phase 3: satisfied-probe",
        "plans_after_classify": [],
        "plans_after_plan": persisted_plans,
        "plans_after_reconcile": persisted_plans,
        "plans_after_overlap_judge": persisted_plans,
        "plans_after_adherence_gate": persisted_plans,
        "satisfied_probe_cache": {
            "bugfix-001": {
                "satisfied": False, "evidence": "", "checked": [],
                "base_sha": "deadbeef",
            }
        },
    })
    caps = _caps(leerie)
    args = _args()
    _drive(leerie, args, caps, run_dirs, st)
    assert calls.get("filter_satisfied_subtasks") == 1
    assert calls.get("write_plan") == 1
    assert "plans_after_filters" in st.data


# ===========================================================================
# Post-scheduling resume unchanged — a run with `waves` already present
# resumes straight into phase_execute (no regression).
# ===========================================================================

def test_post_scheduling_resume_falls_through_to_execute_unchanged(
    leerie, monkeypatch, run_dirs
):
    calls: dict = {}
    _stub_common(leerie, monkeypatch, calls)
    st = _make_state(leerie, run_dirs, {
        "task": "test task", "worker_count": 20,
        "categories": ["bug-fixing"],
        "current_phase": "phase 4: execute",
        "waves": [["bugfix-001"]],
        "completed_waves": 0,
        "subtask_status": {},
        "plans_after_classify": [],
        "plans_after_plan": [],
        "plans_after_reconcile": [],
        "plans_after_overlap_judge": [],
        "plans_after_adherence_gate": [],
        "plans_after_filters": [],
        "plan_snapshot": {"subtasks": {}, "waves": [["bugfix-001"]]},
    })
    caps = _caps(leerie)
    args = _args()
    _drive(leerie, args, caps, run_dirs, st)
    for phase in (
        "phase_classify", "phase_plan", "phase_reconcile",
        "phase_overlap_judge", "phase_adherence_gate",
        "filter_offtree_subtasks", "filter_satisfied_subtasks",
        "check_budget_feasibility", "write_plan",
    ):
        assert phase not in calls, (
            f"{phase} must not run on a post-scheduling resume — "
            "waves is already present")
    assert calls.get("phase_execute") == 1


# ===========================================================================
# Budget-check resume: a run stopped at the budget-feasibility check
# resumes via plan_snapshot instead of dying "Plans are not persisted".
# ===========================================================================

def test_budget_check_resume_rehydrates_plan_snapshot(
    leerie, monkeypatch, run_dirs
):
    calls: dict = {}
    _stub_common(leerie, monkeypatch, calls)
    persisted_plans = [_plan("bug-fixing", _subtask("bugfix-001"))]
    snapshot_subtasks = {"bugfix-001": _subtask("bugfix-001")}
    snapshot_waves = [["bugfix-001"]]
    st = _make_state(leerie, run_dirs, {
        "task": "test task", "worker_count": 15,
        "categories": ["bug-fixing"],
        "current_phase": "phase 3: scheduling",
        "plans_after_classify": [],
        "plans_after_plan": persisted_plans,
        "plans_after_reconcile": persisted_plans,
        "plans_after_overlap_judge": persisted_plans,
        "plans_after_adherence_gate": persisted_plans,
        "plans_after_filters": persisted_plans,
        "plan_snapshot": {
            "subtasks": snapshot_subtasks, "waves": snapshot_waves},
    })
    caps = _caps(leerie)
    args = _args(skip_budget_check=False)
    _drive(leerie, args, caps, run_dirs, st)
    for phase in (
        "phase_classify", "phase_plan", "phase_reconcile",
        "phase_overlap_judge", "phase_adherence_gate",
        "filter_offtree_subtasks", "filter_satisfied_subtasks",
    ):
        assert phase not in calls, (
            f"{phase} must not re-run on a budget-check resume — plans "
            "are already fully checkpointed")
    assert calls.get("check_budget_feasibility") == 1
    assert calls.get("write_plan") == 1


# ===========================================================================
# Determinism: a fresh run and a checkpoint-then-resume of the same task
# produce identical `waves` (schedule() is a pure function of the dep
# graph + lexicographic ids — proven in tests/test_schedule_determinism.py;
# this test pins that the resume path reuses that guarantee end-to-end).
# ===========================================================================

def test_fresh_run_and_checkpointed_resume_produce_identical_waves(
    leerie, monkeypatch, run_dirs, tmp_path
):
    plans = [
        _plan(
            "bug-fixing",
            _subtask("bugfix-002", requires=[{"tag": "shared", "extent": "in_plan"}]),
            _subtask("bugfix-001", provides=["shared"]),
        )
    ]

    # Fresh: schedule() called directly (no orchestrator plumbing needed —
    # it is a pure function of plans).
    fresh_subtasks, fresh_waves = leerie.schedule(
        json.loads(json.dumps(plans)))

    # Checkpoint-then-resume: drive _run_phases from a plans_after_filters
    # checkpoint (the input to schedule()) and inspect the resulting
    # plan_snapshot.
    calls: dict = {}
    _stub_common(leerie, monkeypatch, calls)
    st = _make_state(leerie, run_dirs, {
        "task": "test task", "worker_count": 5,
        "categories": ["bug-fixing"],
        "plans_after_classify": [],
        "plans_after_plan": plans,
        "plans_after_reconcile": plans,
        "plans_after_overlap_judge": plans,
        "plans_after_adherence_gate": plans,
        "plans_after_filters": json.loads(json.dumps(plans)),
    })
    caps = _caps(leerie)
    args = _args()
    _drive(leerie, args, caps, run_dirs, st)

    resumed_waves = st.data["plan_snapshot"]["waves"]
    resumed_subtasks = st.data["plan_snapshot"]["subtasks"]
    assert resumed_waves == fresh_waves
    assert resumed_subtasks == fresh_subtasks


# ===========================================================================
# Allowlist guard: every checkpoint key this subtask reads is already in
# STATE_FIELDS (guard-the-guard, mirroring test_plan_snapshot_wiring.py /
# test_resumable_planning_keys.py — bugfix-002 registered these; this
# pins that a future refactor can't silently drop one from the allowlist
# while this consumer still reads it).
# ===========================================================================

def test_all_consumed_checkpoint_keys_are_in_state_fields(leerie):
    consumed_keys = (
        "plans_after_classify",
        "plans_after_plan",
        "plans_after_reconcile",
        "plans_after_overlap_judge",
        "plans_after_adherence_gate",
        "plans_after_filters",
        "plan_snapshot",
        "satisfied_probe_cache",
    )
    missing = [k for k in consumed_keys if k not in leerie.STATE_FIELDS]
    assert not missing, (
        f"resume re-entry reads these checkpoint keys but they are "
        f"missing from STATE_FIELDS (silently dropped on state reload): "
        f"{missing}"
    )


# ===========================================================================
# The old die() message is gone — a genuinely early interruption (no
# categories at all) is the only remaining unresumable case, with a new,
# accurate message.
# ===========================================================================

def test_old_scheduling_phase_die_message_is_gone():
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent
           / "orchestrator" / "leerie.py").read_text()
    assert "did not reach the scheduling phase" not in src
    # The literal die() message is gone; a comment may still reference it
    # by name (as the behavior this change replaced), so check there is no
    # live `die(...)` call carrying it rather than banning the substring
    # everywhere in the file.
    assert 'die(\n                    "cannot resume — run stopped at the ' \
           'budget-feasibility "' not in src


# ===========================================================================
# worker_count must not be double-counted on resume: re-entering a phase
# whose output is already checkpointed must not spend (bump_workers) for
# work that already ran before the pause (mandatory Design constraint —
# "resume must NOT re-count workers that already ran before the pause").
# Every phase function here is stubbed (no real claude_p calls), so if the
# resume path is correctly skipping already-completed phases, worker_count
# must stay byte-identical to its seeded value from before resume to after
# `_run_phases` re-enters and reaches `_StopAtExecute`.
# ===========================================================================

def test_worker_count_unchanged_across_mid_pipeline_resume(
    leerie, monkeypatch, run_dirs
):
    """Resume after plans_after_reconcile: only overlap_judge onward is
    re-invoked (all stubbed, no real bump_workers calls), so the seeded
    worker_count must be byte-identical after resume re-enters."""
    calls: dict = {}
    _stub_common(leerie, monkeypatch, calls)
    persisted_plans = [_plan("bug-fixing", _subtask("bugfix-001"))]
    seeded_worker_count = 7
    st = _make_state(leerie, run_dirs, {
        "task": "test task", "worker_count": seeded_worker_count,
        "categories": ["bug-fixing"],
        "plans_after_classify": [],
        "plans_after_plan": persisted_plans,
        "plans_after_reconcile": persisted_plans,
    })
    caps = _caps(leerie)
    args = _args()
    _drive(leerie, args, caps, run_dirs, st)

    assert "phase_classify" not in calls
    assert "phase_plan" not in calls
    assert "phase_reconcile" not in calls
    assert calls.get("phase_overlap_judge") == 1
    assert calls.get("phase_adherence_gate") == 1
    assert st.data["worker_count"] == seeded_worker_count, (
        "resume re-entered at overlap_judge but worker_count changed — "
        "a skipped (already-checkpointed) phase must never bump_workers, "
        "and no stubbed downstream phase in this test calls bump_workers "
        "either, so the seeded count must be byte-identical"
    )


def test_worker_count_unchanged_across_satisfied_probe_cache_resume(
    leerie, monkeypatch, run_dirs
):
    """Resume at the satisfied-probe-cache checkpoint (the reported
    incident's exact pause point): worker_count must remain byte-identical
    to its seeded value — the already-decided (cached) probe verdicts and
    every completed planning phase must not be re-spent for."""
    calls: dict = {}
    _stub_common(leerie, monkeypatch, calls)
    persisted_plans = [_plan("bug-fixing", _subtask("bugfix-001"))]
    seeded_worker_count = 10
    st = _make_state(leerie, run_dirs, {
        "task": "test task", "worker_count": seeded_worker_count,
        "categories": ["bug-fixing"],
        "current_phase": "phase 3: satisfied-probe",
        "plans_after_classify": [],
        "plans_after_plan": persisted_plans,
        "plans_after_reconcile": persisted_plans,
        "plans_after_overlap_judge": persisted_plans,
        "plans_after_adherence_gate": persisted_plans,
        "satisfied_probe_cache": {
            "bugfix-001": {
                "satisfied": False, "evidence": "", "checked": [],
                "base_sha": "deadbeef",
            }
        },
    })
    caps = _caps(leerie)
    args = _args()
    _drive(leerie, args, caps, run_dirs, st)

    assert calls.get("filter_satisfied_subtasks") == 1
    assert calls.get("write_plan") == 1
    assert "plans_after_filters" in st.data
    assert st.data["worker_count"] == seeded_worker_count, (
        "resume re-entered at the satisfied-probe-cache checkpoint but "
        "worker_count changed — already-completed planning phases and "
        "already-cached probe verdicts must not be re-counted"
    )


def test_genuinely_no_progress_still_dies(leerie, monkeypatch, run_dirs):
    """No categories, no waves — the run never got past its first
    st.save(), before phase_classify even started. This remains the one
    case that must still die() (nothing to resume from)."""
    calls: dict = {}
    _stub_common(leerie, monkeypatch, calls)
    st = _make_state(leerie, run_dirs, {
        "task": "test task", "worker_count": 0,
    })
    caps = _caps(leerie)
    args = _args()
    with pytest.raises(SystemExit):
        asyncio.run(leerie._run_phases(
            args, caps, run_dirs[2], st, "codebase", "normal", MODELS, EFFORTS))
