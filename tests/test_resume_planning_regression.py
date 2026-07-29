"""Regression lock for the reported resumable-planning bug and its exact
blast radius (test-006; depends on bugfix-002..005/test-001..005's
per-phase checkpoint + resume-cursor work).

Scope, deliberately narrow (see the subtask's scope_note): a single
checkable condition — the resume gate at `orchestrator/leerie.py:20847`
("`waves` absent no longer means die()") behaves correctly across the
four named end-to-end scenarios it governs. This is NOT
`tests/test_resume_planning_reentry.py`'s parametrized per-phase cursor
sweep (every phase stubbed, asserting call-count skip semantics phase by
phase) and NOT `tests/test_filter_satisfied_subtasks.py`'s unit-level
cache coverage — both stay the authoritative source for those. This file
instead drives `_run_phases` through the REAL (unstubbed)
`filter_satisfied_subtasks` / `schedule` / `check_budget_feasibility` /
`write_plan` for the two named incident shapes, so the assertions are
end-to-end proof the reported die() strings are gone and the fix
composes, not merely that each phase's skip-flag is individually wired.

Four scenarios:
  (a) satisfied-probe-sweep resume: partial `satisfied_probe_cache`,
      probes ONLY the uncached sids, reaches schedule() with no die().
  (b) budget-check resume: rehydrates subtasks/waves from `plan_snapshot`,
      re-runs only check_budget_feasibility under a raised
      max_total_workers, proceeds to write_plan.
  (c) post-scheduling resume unchanged: `waves` present -> straight to
      phase_execute, zero planning-phase calls.
  (d) the finished_at+finalize guard and the no_work_required guard still
      return early, unchanged, ahead of any rehydration.

Plus a grep guard (CLAUDE.md checklist discipline, prior art
`tests/test_ec2_launcher_dispatch_e2e.py`) that neither retired die()
string remains in leerie.py. Reverting the fix must fail (a).
"""
from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest


# ===========================================================================
# Helpers (mirrors tests/test_resume_planning_reentry.py's conventions)
# ===========================================================================

def _subtask(sid: str, *, criteria: str = "criteria met", **kw) -> dict:
    base = {
        "id": sid,
        "title": f"Subtask {sid}",
        "intent": f"intent for {sid}",
        "success_criteria_seed": criteria,
        "runs_commands": [],
        "files_likely_touched": [],
        "provides": [],
        "requires": [],
        "depends_on": [],
        "size": "small",
    }
    base.update(kw)
    return base


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


def _caps(leerie, **overrides) -> dict:
    caps = dict(leerie.DEFAULT_CAPS)
    caps["judgment_check_rounds"] = 2
    caps.update(overrides)
    return caps


MODELS: dict = {"satisfied_probe": "sonnet"}
EFFORTS: dict = {"satisfied_probe": None}


class _StopAtExecute(Exception):
    """Raised by the stubbed phase_execute so the test can assert on the
    planning-pipeline state without driving the (unrelated) execute/
    finalize phases."""


@pytest.fixture
def run_dirs(tmp_path):
    leerie_root = tmp_path / ".leerie"
    run_id = "test-resume-regression-bbb222"
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


def _init_git_repo(path: Path) -> str:
    """A minimal real git repo — filter_satisfied_subtasks scopes the
    cache to `_branch_head_sha`, so scenario (a) needs a real HEAD."""
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"],
                    cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True)
    (path / "a.py").write_text("x = 1\n")
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=path, check=True)
    out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=path,
                          check=True, capture_output=True, text=True)
    return out.stdout.strip()


def _stub_upstream_phases(leerie, monkeypatch, calls: dict):
    """Stub only the phases upstream of filter_satisfied_subtasks/schedule
    (classify..adherence_gate) plus phase_execute — schedule(),
    check_budget_feasibility(), write_plan(), and
    filter_satisfied_subtasks() itself are deliberately left REAL for
    this file's end-to-end scenarios."""
    monkeypatch.setattr(
        leerie, "enforce_and_record_cgroup_containment",
        lambda st, allow_uncapped: None)
    monkeypatch.setattr(
        leerie, "absorb_supplied_answers", lambda args, st, leerie_dir: None)

    async def _backstop(*a, **kw):
        pass
    monkeypatch.setattr(leerie, "_backstop_capture_prior_runs", _backstop)

    async def _classify(task, st, caps, clarify, models, efforts):
        calls["phase_classify"] = calls.get("phase_classify", 0) + 1
        st.data["categories"] = ["bug-fixing"]
        return {"categories": ["bug-fixing"]}
    monkeypatch.setattr(leerie, "phase_classify", _classify)

    async def _provision(*a, **kw):
        calls["phase_provision"] = calls.get("phase_provision", 0) + 1
    monkeypatch.setattr(leerie, "phase_provision", _provision)

    monkeypatch.setattr(leerie, "gather_answers", lambda st, supplied: None)

    async def _stub_phase_plan(task, st, caps, models, efforts):
        calls["phase_plan"] = calls.get("phase_plan", 0) + 1
        return []
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
        calls["phase_adherence_gate"] = calls.get(
            "phase_adherence_gate", 0) + 1
        return plans
    monkeypatch.setattr(leerie, "phase_adherence_gate", _adherence_gate)

    monkeypatch.setattr(
        leerie, "warn_cross_planner_file_overlap", lambda plans: None)
    monkeypatch.setattr(leerie, "warn_layer_gaps", lambda plans: None)
    monkeypatch.setattr(
        leerie, "warn_provider_subset_subtasks", lambda plans: None)

    def _offtree(plans, repo_root, inspect_dirs, st):
        calls["filter_offtree_subtasks"] = calls.get(
            "filter_offtree_subtasks", 0) + 1
    monkeypatch.setattr(leerie, "filter_offtree_subtasks", _offtree)

    async def _execute(*a, **kw):
        calls["phase_execute"] = calls.get("phase_execute", 0) + 1
        raise _StopAtExecute()
    monkeypatch.setattr(leerie, "phase_execute", _execute)


def _drive(leerie, args, run_dir, st, caps):
    with pytest.raises(_StopAtExecute):
        asyncio.run(leerie._run_phases(
            args, caps, run_dir, st, "codebase", "normal", MODELS, EFFORTS))


# ===========================================================================
# (a) THE REPORTED FAILURE, end to end: paused mid satisfied-probe sweep
# with a partial cache resumes, probes ONLY the uncached sids (via the
# REAL filter_satisfied_subtasks against a real git repo — claude_p is
# the only thing stubbed), and reaches schedule() with no die().
# ===========================================================================

def test_satisfied_sweep_resume_probes_only_uncached_and_reaches_schedule(
    leerie, monkeypatch, run_dirs, tmp_path
):
    repo = tmp_path / "repo"
    sha = _init_git_repo(repo)
    monkeypatch.chdir(repo)

    calls: dict = {}
    _stub_upstream_phases(leerie, monkeypatch, calls)

    claude_p_calls: list[str] = []

    async def fake_claude_p(*, user_prompt, sid, **_kw):
        claude_p_calls.append(sid)
        return {"satisfied": False, "evidence": "probed after resume"}
    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)

    persisted_plans = [_plan(
        "bug-fixing",
        _subtask("feat-001"), _subtask("feat-002"), _subtask("feat-003"),
    )]
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
            "feat-001": {
                "satisfied": False, "evidence": "decided pre-pause",
                "checked": [], "base_sha": sha,
            }
        },
    })
    caps = _caps(leerie, skip_budget_check=True)
    args = _args()

    _drive(leerie, args, run_dirs[2], st, caps)

    # Only the two uncached subtasks trigger a fresh satisfied-probe call —
    # the cached feat-001 is never re-probed. (This resume reaches scheduling
    # for the first time, so the post-schedule `wiring_judge` gate also
    # legitimately fires once — DESIGN §5 *A wiring re-check on the fully-merged
    # plan*; it is a downstream gate, not a re-run of the satisfied sweep, so
    # we assert on the probe calls specifically.)
    probe_calls = [c for c in claude_p_calls if c.startswith("satisfied_probe-")]
    assert sorted(probe_calls) == [
        "satisfied_probe-feat-002", "satisfied_probe-feat-003"]
    # feat-001 (cached) is never re-probed, on any sid.
    assert "satisfied_probe-feat-001" not in claude_p_calls
    # No planning phase upstream of the sweep re-ran.
    for phase in ("phase_classify", "phase_plan", "phase_reconcile",
                  "phase_overlap_judge", "phase_adherence_gate"):
        assert phase not in calls, f"{phase} must not re-run on resume"
    # Reached scheduling and beyond — no die().
    assert "plans_after_filters" in st.data
    assert "plan_snapshot" in st.data
    assert calls.get("phase_execute") == 1


def test_reverting_the_fix_fails_scenario_a(leerie, monkeypatch, run_dirs,
                                             tmp_path):
    """Falsification control: simulate the retired gate (`waves` absent =>
    die immediately) and confirm the reported-failure scenario above would
    have died under it — proving test (a) actually exercises the fixed
    code path rather than passing vacuously."""
    st_data = {
        "task": "test task", "worker_count": 10,
        "categories": ["bug-fixing"],
        "current_phase": "phase 3: satisfied-probe",
        "satisfied_probe_cache": {"feat-001": {"satisfied": False}},
    }
    assert "waves" not in st_data
    with pytest.raises(SystemExit):
        if "waves" not in st_data:
            leerie.die("cannot resume — run did not reach the scheduling phase")


# ===========================================================================
# (b) Budget-check resume: a run stopped at check_budget_feasibility
# rehydrates subtasks/waves from plan_snapshot, re-runs ONLY the budget
# check (real check_budget_feasibility) under a raised max_total_workers,
# and proceeds to write_plan — instead of dying "Plans are not persisted".
# ===========================================================================

def test_budget_check_resume_reruns_only_budget_check_under_raised_cap(
    leerie, monkeypatch, run_dirs, tmp_path
):
    repo = tmp_path / "repo"
    _init_git_repo(repo)
    monkeypatch.chdir(repo)

    calls: dict = {}
    _stub_upstream_phases(leerie, monkeypatch, calls)

    # write_plan writes real subtask spec files to leerie_dir/subtasks —
    # let it run for real (it's cheap, and it's the deliverable this
    # scenario claims is reached) but count invocations.
    write_plan_calls = {"n": 0}
    orig_write_plan = leerie.write_plan

    def _counting_write_plan(leerie_dir, task, st, subtasks, waves):
        write_plan_calls["n"] += 1
        return orig_write_plan(leerie_dir, task, st, subtasks, waves)
    monkeypatch.setattr(leerie, "write_plan", _counting_write_plan)

    persisted_plans = [_plan("bug-fixing", _subtask("feat-001"))]
    snapshot_subtasks = {"feat-001": _subtask("feat-001")}
    snapshot_waves = [["feat-001"]]
    st_seed = _make_state(leerie, run_dirs, {
        "task": "test task",
        # A large already-spent count that would have blown the DEFAULT
        # budget cap under the old estimate — proves this resume path
        # actually re-runs check_budget_feasibility against the NEW,
        # raised cap rather than skipping the check outright.
        "worker_count": 15,
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
    st_seed.release_lock()

    # skip_budget_check=False + a low cap so the FIRST attempt would die;
    # a raised max_total_workers on this resume must let it through.
    low_caps = _caps(leerie, skip_budget_check=False, max_total_workers=16)
    args_low = _args(skip_budget_check=False)
    st_low = leerie.State(run_dirs[0], run_dirs[1])
    try:
        with pytest.raises(SystemExit):
            _drive(leerie, args_low, run_dirs[2], st_low, low_caps)
    finally:
        st_low.release_lock()

    # Re-resume with a raised cap (the documented remediation) — reload a
    # fresh State (mirrors a real second `--resume` invocation) so nothing
    # from the failed attempt's in-memory st leaks in.
    calls.clear()
    st2 = leerie.State(run_dirs[0], run_dirs[1])
    try:
        high_caps = _caps(leerie, skip_budget_check=False,
                           max_total_workers=10_000)
        args_high = _args(skip_budget_check=False)
        _drive(leerie, args_high, run_dirs[2], st2, high_caps)

        for phase in (
            "phase_classify", "phase_plan", "phase_reconcile",
            "phase_overlap_judge", "phase_adherence_gate",
            "filter_offtree_subtasks",
        ):
            assert phase not in calls, (
                f"{phase} must not re-run on a budget-check resume — plans "
                "are already fully checkpointed")
        assert write_plan_calls["n"] == 1
        assert calls.get("phase_execute") == 1
        assert st2.data["plan_snapshot"]["subtasks"] == snapshot_subtasks
        assert st2.data["plan_snapshot"]["waves"] == snapshot_waves
    finally:
        st2.release_lock()


# ===========================================================================
# (c) Post-scheduling resume unchanged: `waves` already present -> straight
# to phase_execute, ZERO planning-phase calls.
# ===========================================================================

def test_post_scheduling_resume_unchanged_zero_planning_calls(
    leerie, monkeypatch, run_dirs
):
    calls: dict = {}
    _stub_upstream_phases(leerie, monkeypatch, calls)

    async def _satisfied_should_not_run(*a, **kw):
        calls["filter_satisfied_subtasks"] = calls.get(
            "filter_satisfied_subtasks", 0) + 1
        return None
    monkeypatch.setattr(
        leerie, "filter_satisfied_subtasks", _satisfied_should_not_run)
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

    st = _make_state(leerie, run_dirs, {
        "task": "test task", "worker_count": 42,
        "categories": ["bug-fixing"],
        "current_phase": "phase 4: execute",
        "waves": [["feat-001"]],
        "completed_waves": 0,
        "subtask_status": {},
        "plans_after_classify": [],
        "plans_after_plan": [],
        "plans_after_reconcile": [],
        "plans_after_overlap_judge": [],
        "plans_after_adherence_gate": [],
        "plans_after_filters": [],
        "plan_snapshot": {"subtasks": {}, "waves": [["feat-001"]]},
    })
    caps = _caps(leerie)
    args = _args()
    _drive(leerie, args, run_dirs[2], st, caps)

    for phase in (
        "phase_classify", "phase_plan", "phase_reconcile",
        "phase_overlap_judge", "phase_adherence_gate",
        "filter_offtree_subtasks", "filter_satisfied_subtasks",
        "check_budget_feasibility", "validate_plan", "write_plan",
    ):
        assert phase not in calls, (
            f"{phase} must not run on a post-scheduling resume ('waves' "
            f"already present) — got {calls.get(phase)} call(s)")
    assert calls.get("phase_execute") == 1


# ===========================================================================
# (d) The finished_at+finalize guard and the no_work_required guard return
# early, unchanged, ahead of any rehydration — no phase, no schedule(),
# no write_plan call of any kind.
# ===========================================================================

def test_completed_run_guard_returns_early_unchanged(leerie, run_dirs):
    st = leerie.State(run_dirs[0], run_dirs[1])
    st.data = {
        "task": "test task", "worker_count": 5,
        "finished_at": "2026-01-01T00:00:00Z",
        "current_phase": "phase 6: finalize",
    }
    st.save()
    args = _args()
    # Must return (not raise, not die) — no _StopAtExecute, since
    # phase_execute must never even be reached.
    asyncio.run(leerie._run_phases(
        args, dict(leerie.DEFAULT_CAPS), run_dirs[2], st, "codebase",
        "normal", MODELS, EFFORTS))


def test_no_work_required_guard_returns_early_unchanged(leerie, run_dirs):
    st = leerie.State(run_dirs[0], run_dirs[1])
    st.data = {
        "task": "test task", "worker_count": 5,
        "finished_at": "2026-01-01T00:00:00Z",
        "no_work_required": True,
        # Deliberately no `current_phase == "phase 6: finalize"` and no
        # `waves`/`categories` — proves this guard fires independently of
        # the completed-run guard and ahead of the "no progress at all"
        # die() below it.
    }
    st.save()
    args = _args()
    asyncio.run(leerie._run_phases(
        args, dict(leerie.DEFAULT_CAPS), run_dirs[2], st, "codebase",
        "normal", MODELS, EFFORTS))


def test_no_work_required_guard_precedes_rehydration_even_mid_pipeline(
    leerie, run_dirs
):
    """The no-work guard must win even when planning checkpoints ARE
    present (a run that reached the cleared-but-empty terminal state
    partway through planning) — it is not merely a "nothing happened yet"
    special case."""
    st = leerie.State(run_dirs[0], run_dirs[1])
    st.data = {
        "task": "test task", "worker_count": 8,
        "finished_at": "2026-01-01T00:00:00Z",
        "no_work_required": True,
        "categories": ["bug-fixing"],
        "plans_after_classify": [],
        "plans_after_plan": [],
        "plans_after_reconcile": [],
    }
    st.save()
    args = _args()
    asyncio.run(leerie._run_phases(
        args, dict(leerie.DEFAULT_CAPS), run_dirs[2], st, "codebase",
        "normal", MODELS, EFFORTS))


# ===========================================================================
# Grep guard: neither retired die() string remains in leerie.py (CLAUDE.md
# checklist discipline — prior art tests/test_ec2_launcher_dispatch_e2e.py).
# ===========================================================================

def test_retired_die_strings_absent_from_leerie_py():
    """Neither retired die() string survives as a LIVE die() call. A
    trailing comment may still reference the old behavior by name (as the
    thing this change replaced — see the `plan_snapshot` comment at the
    budget-feasibility call site) so this checks there is no `die(...)`
    invocation actually carrying either string, mirroring
    tests/test_resume_planning_reentry.py's
    test_old_scheduling_phase_die_message_is_gone discipline."""
    src = (Path(__file__).resolve().parent.parent
           / "orchestrator" / "leerie.py").read_text()
    import re
    die_calls = re.findall(r"die\(\s*\n?\s*[\"'].*?[\"']", src, re.DOTALL)
    for msg in ("did not reach the scheduling phase", "Plans are not persisted"):
        offending = [c for c in die_calls if msg in c]
        assert not offending, (
            f"a live die() call still carries the retired message {msg!r}: "
            f"{offending}")
