"""A re-plan must re-run every planning phase upstream of the gate that
triggered it (DESIGN §5 *A re-plan invalidates every phase that already ran*).

The planning pipeline is `reconcile -> overlap-judge -> adherence-gate ->
coverage-gate`. A re-plan runs one planner per category in parallel with no
cross-category visibility, exactly like the first pass, so it reintroduces
both the vocabulary drift the reconciler resolved AND the cross-planner
surface collisions the overlap judge merged.

**Only `phase_adherence_gate` re-plans now.** `phase_planning_coverage_gate`
became advisory in PR #166 — it invokes its judge once, logs, and returns the
plan unchanged.

That matters for how this file shrank. The motivating incident, run
`19a70d96` (2026-08-01), was a COVERAGE-GATE re-plan: the overlap judge merged
8 subtasks down to 4 (`plans_after_overlap_judge` == 4), the coverage gate
re-planned, 8 came back with every duplicate restored
(`plans_after_coverage_gate` == 8), nothing re-detected them, and all 8
executed until the integration gate refused the merge — 4.7 hours and 164
workers spent on a plan the overlap judge had already rejected. Two subtasks
had performed the same `_gather_provision_fixtures` migration; one's
`files_likely_touched` was a strict subset of the other's.

**Its four guards are retired because that hazard is now structurally
impossible, not because it stopped mattering.** A gate that cannot re-plan
cannot invalidate an upstream phase. If the coverage gate's re-plan path is
ever restored, these guards must come back with it — the incident is a
property of re-planning from that pipeline position, not of the old code.
`tests/test_phase_planning_coverage_gate.py::test_a_coverage_gap_does_not_replan`
is the positive assertion that replaced them.

What remains is the adherence gate, which still re-plans and sits upstream:

- adherence gate re-plan -> reconcile + overlap judge. NOT the coverage gate,
  which is downstream and has not run yet.
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
        "straight to _schedule()")

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

def test_adherence_gate_does_not_rerun_the_downstream_coverage_gate(leerie):
    """Not an oversight — the coverage gate sits downstream and has not run
    yet, so it will see the re-planned output anyway. Re-running it here
    would double its spend for nothing."""
    calls = _replan_calls(leerie, leerie.phase_adherence_gate)
    assert "phase_planning_coverage_gate" not in calls


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
        def __init__(self):
            # judgment workers run in a disposable worktree
            # (DESIGN §12), never the user's checkout
            self.data = {"planning_worktree": "/tmp/leerie-test-wt"}
        def save(self): pass
        def bump_workers(self, caps): pass

    asyncio.run(leerie.phase_planning_coverage_gate(
        _duplicated_plan(), "task", _St(), dict(leerie.DEFAULT_CAPS), {}, {}))
    assert hits == {"reconcile": 0, "overlap": 0, "adherence": 0, "plan": 0}
