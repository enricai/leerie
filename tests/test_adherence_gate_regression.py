"""Regression fixture freezing the Check-2/Check-3 empirical calibration of
the instruction-adherence gate (DESIGN: instruction-adherence is
code-enforced, sibling to §12 "prompts advisory, code enforces").

The plan's validation work ran real opus judges against the real cruiselines
incident plan and a 21-run corpus (see the "Check 2"/"Check 3" sections of
this subtask's `_task`), and found: the two-stage composition
(`is_prescribed=true AND (floor violation OR low adherence)`) fires on the
incident and stays silent on ordinary goal-only tasks with 0 false
positives — but the *judge alone*, ungated by `is_prescribed`, false-
positived ~12% of ordinary runs. That composition is the load-bearing
result; this file freezes it as a deterministic, no-live-LLM fixture so a
future prompt/schema edit that regresses the calibration is caught
mechanically.

Distinct from tests/test_prescribed_cmd_coverage.py (floor in isolation,
synthetic cases) and tests/test_phase_adherence_gate.py (the phase's wiring
and control flow with ad hoc inline stubs). This file replays the specific
incident-shaped and legit-shaped fixtures via canned classifier/planner JSON
committed under tests/fixtures/adherence_gate/, driving both:
  1. the deterministic floor directly, and
  2. the full two-stage composition through phase_adherence_gate, with
     claude_p stubbed to return the fixture's recorded (already-empirically-
     validated) adherence_judge score — never a live claude_p call.

Fixtures are structured JSON only — no task prose is parsed by this file or
by the code under test; the classifier's `prescribed_procedure` extraction
and the planner's `runs_commands` extraction are themselves out of scope
here (that language-to-JSON step is exactly what a live classifier/planner
call would do, and is not re-validated by this offline fixture).
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "adherence_gate"


def _load_fixture(name: str) -> dict:
    with open(FIXTURES_DIR / f"{name}.json") as f:
        return json.load(f)


def _plan_from_fixture(fixture: dict) -> list[dict]:
    return [{
        "domain": "feature-implementation",
        "status": "ready",
        "subtasks": [
            {
                "id": s["id"],
                "title": s["title"],
                "intent": s["intent"],
                "success_criteria_seed": s["success_criteria_seed"],
                "runs_commands": list(s.get("runs_commands", [])),
                "files_likely_touched": [],
                "provides": [],
                "requires": [],
                "depends_on": [],
                "size": "small",
            }
            for s in fixture["plan_subtasks"]
        ],
    }]


def _minimal_state(leerie, tmp_path, fixture: dict, run_id: str) -> object:
    leerie_root = tmp_path / ".leerie"
    (leerie_root / "runs" / run_id).mkdir(parents=True)
    st = leerie.State(leerie_root, run_id)
    st.data = {
        "task": fixture["task"],
        "worker_count": 0,
        "prescribed_procedure": fixture["classifier_prescribed_procedure"],
    }
    st.save()
    return st


def _caps(leerie) -> dict:
    caps = dict(leerie.DEFAULT_CAPS)
    caps["judgment_check_rounds"] = 3
    return caps


MODELS = {"adherence_judge": "opus"}
EFFORTS = {"adherence_judge": "high"}


@pytest.fixture(params=["incident_plan", "legit_plan"])
def fixture_name(request) -> str:
    return request.param


# ===========================================================================
# 1. Deterministic floor over the canned fixture JSON (no claude_p at all)
# ===========================================================================

def test_floor_matches_expected_issue_count(leerie, fixture_name):
    fixture = _load_fixture(fixture_name)
    subtasks = fixture["plan_subtasks"]
    issues = leerie.check_prescribed_command_coverage(
        fixture["classifier_prescribed_procedure"], subtasks)
    assert len(issues) == fixture["expected_floor_issue_count"], (
        f"{fixture_name}: floor issue count regressed — expected "
        f"{fixture['expected_floor_issue_count']}, got {len(issues)}: "
        f"{issues}"
    )


def test_incident_floor_names_both_unrun_commands(leerie):
    fixture = _load_fixture("incident_plan")
    issues = leerie.check_prescribed_command_coverage(
        fixture["classifier_prescribed_procedure"], fixture["plan_subtasks"])
    assert any("recon browser" in i for i in issues)
    assert any("recon generate" in i for i in issues)


def test_legit_floor_is_silent(leerie):
    fixture = _load_fixture("legit_plan")
    issues = leerie.check_prescribed_command_coverage(
        fixture["classifier_prescribed_procedure"], fixture["plan_subtasks"])
    assert issues == []


# ===========================================================================
# 2. Composed two-stage gate decision, replayed through phase_adherence_gate
#    with claude_p stubbed to the fixture's recorded (empirically-validated)
#    adherence_judge score — no live LLM call.
# ===========================================================================

def test_incident_gate_fires(leerie, monkeypatch, tmp_path):
    """The incident fixture (is_prescribed=true, floor violates, judge
    score 2.5 < threshold) must drive the gate to re-plan; if the re-plan
    never produces a compliant plan, the gate must die() rather than
    silently let the incident-shaped plan through."""
    fixture = _load_fixture("incident_plan")
    assert fixture["expected_gate_fires"] is True
    st = _minimal_state(leerie, tmp_path, fixture, "test-adherence-incident")
    bad_plans = _plan_from_fixture(fixture)

    async def fake_claude_p(**kwargs):
        return dict(fixture["adherence_judge_result"])

    async def fake_phase_plan(task, st_, caps, models, efforts, replan_round=0):
        # A re-plan that still does not honor the prescribed procedure —
        # the incident's actual outcome. The gate must not silently accept
        # this; it should exhaust the retry budget and die().
        return bad_plans

    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    monkeypatch.setattr(leerie, "phase_plan", fake_phase_plan)

    with pytest.raises(SystemExit):
        asyncio.run(leerie.phase_adherence_gate(
            bad_plans, fixture["task"], st, _caps(leerie), MODELS, EFFORTS))


def test_legit_gate_stays_silent(leerie, monkeypatch, tmp_path):
    """The legit fixture (is_prescribed=false) must short-circuit before
    any claude_p call — the ~90% common case must never pay for or be
    at risk from the judge, and must never re-plan."""
    fixture = _load_fixture("legit_plan")
    assert fixture["expected_gate_fires"] is False
    st = _minimal_state(leerie, tmp_path, fixture, "test-adherence-legit")
    plans = _plan_from_fixture(fixture)

    async def fail_claude_p(**kwargs):
        pytest.fail(
            "claude_p must never be called for a goal-only "
            "(is_prescribed=false) plan — the two-stage gate's whole "
            "point is 0 false positives via short-circuiting before the "
            "judge runs"
        )

    async def fail_phase_plan(*args, **kwargs):
        pytest.fail("phase_plan must never be re-invoked on the legit control")

    monkeypatch.setattr(leerie, "claude_p", fail_claude_p)
    monkeypatch.setattr(leerie, "phase_plan", fail_phase_plan)

    result = asyncio.run(leerie.phase_adherence_gate(
        plans, fixture["task"], st, _caps(leerie), MODELS, EFFORTS))

    assert result is plans


def test_incident_vs_legit_judge_scores_are_cleanly_separated(leerie):
    """Freezes the raw Check-2 calibration numbers themselves (not just the
    fire/silent outcome) — a future recalibration that narrows the
    separation below the gate threshold should be visible in a diff here,
    not just in a pass/fail."""
    incident = _load_fixture("incident_plan")
    legit = _load_fixture("legit_plan")

    incident_scores = incident["adherence_judge_scores"]
    legit_scores = legit["adherence_judge_scores"]

    assert all(s < leerie._ADHERENCE_GATE_THRESHOLD for s in incident_scores)
    assert all(s >= leerie._ADHERENCE_GATE_THRESHOLD for s in legit_scores)
    assert max(incident_scores) < min(legit_scores), (
        "the incident and legit calibration scores must remain cleanly "
        "separated — any overlap means the two-stage gate threshold "
        "(_ADHERENCE_GATE_THRESHOLD) no longer discriminates the two "
        "corpus-validated cases"
    )
