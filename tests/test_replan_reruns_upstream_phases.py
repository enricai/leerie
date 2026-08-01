"""A re-plan must re-run every planning phase upstream of the gate that
triggered it (DESIGN §5 *A re-plan invalidates every phase that already ran*).

The planning pipeline is `reconcile -> overlap-judge -> adherence-gate ->
coverage-gate`. The two later gates can reject a plan and re-drive
`phase_plan`. A re-plan runs one planner per category in parallel with no
cross-category visibility, exactly like the first pass, so it reintroduces both
the vocabulary drift the reconciler resolved AND the cross-planner surface
collisions the overlap judge merged.

Both gates re-ran `phase_reconcile` and neither re-ran `phase_overlap_judge`.
Run 19a70d96 (2026-08-01) is the incident: the judge merged 8 subtasks down to 4
(`plans_after_overlap_judge` == 4), the coverage gate re-planned, 8 came back
with every duplicate restored (`plans_after_coverage_gate` == 8), nothing
re-detected them, and all 8 executed until the integration gate refused the
merge — 4.7 hours and 164 workers spent on a plan the overlap judge had already
rejected. Two subtasks had performed the same `gather_provision_fixtures`
migration; one's `files_likely_touched` was a strict subset of the other's.

The repair is asymmetric because the gates sit at different pipeline positions,
and that asymmetry is the thing most likely to be "simplified" wrong later:

- adherence gate re-plan -> reconcile + overlap judge. NOT the coverage gate,
  which is downstream and has not run yet.
- coverage gate re-plan -> reconcile + overlap judge + adherence gate.
"""
from __future__ import annotations

import inspect
import re

import pytest


# --------------------------------------------------------------------- #
# Source-coupling pins — the asymmetry, and that it is deliberate
# --------------------------------------------------------------------- #

def _replan_calls(leerie, fn) -> list[str]:
    """Phases the gate awaits inside its own body, in source order."""
    return [m.group(1) for m in
            re.finditer(r'await (phase_\w+)\(', inspect.getsource(fn))]


def test_adherence_gate_reruns_reconcile_and_overlap_judge(leerie):
    calls = _replan_calls(leerie, leerie.phase_adherence_gate)
    assert "phase_plan" in calls, "the gate must actually re-plan"
    assert "phase_reconcile" in calls
    assert "phase_overlap_judge" in calls, (
        "a re-plan reintroduces the cross-planner surface collisions the "
        "overlap judge already merged; not re-running it hands duplicates "
        "straight to schedule()")


def test_adherence_gate_does_not_rerun_the_downstream_coverage_gate(leerie):
    """Not an oversight — the coverage gate sits downstream and has not run
    yet, so it will see the re-planned output anyway. Re-running it here
    would double its spend for nothing."""
    calls = _replan_calls(leerie, leerie.phase_adherence_gate)
    assert "phase_planning_coverage_gate" not in calls


def test_coverage_gate_reruns_all_three_upstream_phases(leerie):
    calls = _replan_calls(leerie, leerie.phase_planning_coverage_gate)
    assert "phase_plan" in calls
    for phase in ("phase_reconcile", "phase_overlap_judge",
                  "phase_adherence_gate"):
        assert phase in calls, (
            f"{phase} runs upstream of the coverage gate, so a re-plan here "
            "invalidates it")


def test_reruns_follow_the_replan_not_precede_it(leerie):
    """Ordering is load-bearing: re-running an upstream phase against the
    OLD plan would be a no-op that looks like a fix."""
    for fn in (leerie.phase_adherence_gate,
               leerie.phase_planning_coverage_gate):
        calls = _replan_calls(leerie, fn)
        i_plan = calls.index("phase_plan")
        for phase in calls[i_plan + 1:]:
            assert phase != "phase_plan"
        assert calls.index("phase_reconcile") > i_plan, fn.__name__
        assert calls.index("phase_overlap_judge") > i_plan, fn.__name__


def test_nesting_is_bounded_by_a_shared_round_budget(leerie):
    """Nesting the adherence gate inside the coverage gate's retry loop is
    bounded, not recursive: both loops cap at `judgment_check_rounds`, so
    the worst case is that product. If either grew an unbounded loop this
    would become a real recursion hazard."""
    for fn in (leerie.phase_adherence_gate,
               leerie.phase_planning_coverage_gate):
        src = inspect.getsource(fn)
        assert 'max_rounds=caps["judgment_check_rounds"]' in src, (
            f"{fn.__name__} must bound its re-plans by the shared round "
            "budget for the nesting to stay bounded")
    assert isinstance(leerie.DEFAULT_CAPS["judgment_check_rounds"], int)
    assert leerie.DEFAULT_CAPS["judgment_check_rounds"] >= 1


# --------------------------------------------------------------------- #
# Behavioural pin — the incident shape
# --------------------------------------------------------------------- #

def _sub(sid, provides, files):
    return {"id": sid, "title": sid, "intent": f"intent {sid}",
            "success_criteria_seed": "c", "runs_commands": [],
            "files_likely_touched": list(files), "provides": list(provides),
            "requires": [], "depends_on": [], "size": "small"}


def _duplicated_plan():
    """The 19a70d96 shape: two code planners independently producing the
    same subtask, one's file set a strict subset of the other's."""
    return [
        {"domain": "feature-implementation", "status": "ready", "subtasks": [
            _sub("feat-003", ["readme-llm-selection"],
                 ["orchestrator/leerie.py", "prompts/provision.md",
                  "docs/DESIGN.md", "tests/test_x.py"])]},
        {"domain": "refactoring", "status": "ready", "subtasks": [
            _sub("refactor-003", ["readme-worker-owns-selection"],
                 ["orchestrator/leerie.py", "prompts/provision.md",
                  "docs/DESIGN.md"])]},
    ]


def test_coverage_gate_replan_feeds_the_overlap_judge(leerie, monkeypatch):
    """The load-bearing behavioural check: whatever `phase_plan` returns on a
    re-plan must reach `phase_overlap_judge`, not go straight to the caller.

    Anti-vacuity: the stub judge asserts it was handed the RE-PLANNED plan
    (both duplicates present), not the original.
    """
    import asyncio

    seen: dict = {}
    rounds = {"n": 0}

    async def fake_plan(task, st, caps, models, efforts, replan_round=0):
        rounds["n"] += 1
        return _duplicated_plan()
    monkeypatch.setattr(leerie, "phase_plan", fake_plan)

    async def fake_reconcile(plans, *a, **kw):
        seen["reconcile"] = [s["id"] for p in plans
                             for s in p.get("subtasks", [])]
        return plans
    monkeypatch.setattr(leerie, "phase_reconcile", fake_reconcile)

    async def fake_overlap(plans, *a, **kw):
        seen["overlap"] = [s["id"] for p in plans
                           for s in p.get("subtasks", [])]
        # Merge the duplicate away, as the real judge did.
        return [plans[0]]
    monkeypatch.setattr(leerie, "phase_overlap_judge", fake_overlap)

    async def fake_adherence(plans, *a, **kw):
        seen["adherence"] = [s["id"] for p in plans
                             for s in p.get("subtasks", [])]
        return plans
    monkeypatch.setattr(leerie, "phase_adherence_gate", fake_adherence)

    # One round of gaps, then clean.
    calls = {"n": 0}

    async def fake_claude_p(**kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"plan_reviewed": True, "rationale": "r",
                    "coverage_gaps": [{
                        "kind": "missing_work",
                        "description": "site 5 is not covered by any subtask",
                        "concrete_evidence": "no subtask names _depunctuate"}]}
        return {"plan_reviewed": True, "rationale": "r", "coverage_gaps": []}
    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)

    class _St:
        def __init__(self): self.data = {}
        def save(self): pass
        def bump_workers(self, caps): pass

    out = asyncio.run(leerie.phase_planning_coverage_gate(
        _duplicated_plan(), "task", _St(), dict(leerie.DEFAULT_CAPS), {}, {}))

    assert rounds["n"] == 1, "the gate should have re-planned exactly once"
    assert seen.get("overlap") == ["feat-003", "refactor-003"], (
        "the overlap judge must be handed the re-planned plan with both "
        "duplicates present — this is the check that was missing")
    assert seen.get("adherence") == ["feat-003"], (
        "the adherence gate must run on the post-overlap-judge output, so "
        "the phases compose in pipeline order")
    surviving = [s["id"] for p in out for s in p.get("subtasks", [])]
    assert surviving == ["feat-003"], (
        "the judge's merge must survive to the gate's return value; before "
        "the fix the duplicate reached schedule() and executed")


def test_no_replan_means_no_extra_phase_calls(leerie, monkeypatch):
    """Anti-vacuity control: a clean plan must not pay for any of the
    re-runs. If this fires, the re-runs were hoisted out of the feedback
    callback and every run is now paying for them."""
    import asyncio

    hits = {"reconcile": 0, "overlap": 0, "adherence": 0, "plan": 0}

    def _counter(name):
        async def f(plans, *a, **kw):
            hits[name] += 1
            return plans
        return f

    async def fake_plan(*a, **kw):
        hits["plan"] += 1
        return _duplicated_plan()
    monkeypatch.setattr(leerie, "phase_plan", fake_plan)
    monkeypatch.setattr(leerie, "phase_reconcile", _counter("reconcile"))
    monkeypatch.setattr(leerie, "phase_overlap_judge", _counter("overlap"))
    monkeypatch.setattr(leerie, "phase_adherence_gate", _counter("adherence"))

    async def fake_claude_p(**kw):
        return {"plan_reviewed": True, "rationale": "r", "coverage_gaps": []}
    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)

    class _St:
        def __init__(self): self.data = {}
        def save(self): pass
        def bump_workers(self, caps): pass

    asyncio.run(leerie.phase_planning_coverage_gate(
        _duplicated_plan(), "task", _St(), dict(leerie.DEFAULT_CAPS), {}, {}))
    assert hits == {"reconcile": 0, "overlap": 0, "adherence": 0, "plan": 0}
