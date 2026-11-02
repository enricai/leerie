"""Partially blocked plans are recorded; an external prerequisite stops the
run before execution (DESIGN §8 *A partially blocked plan is recorded, and
an external prerequisite stops the run*; IMPLEMENTATION §5½ *Blocked
domains*).

Before: `_schedule` dropped a blocked domain with a log line and no State
record, and a domain blocked on an unmerged prerequisite task let the ready
domains execute a partial change. Bench P3: the 4 domains blocked on
unmerged prerequisite tasks typed `external_prerequisite` 8/8; the
non-external cases planned `ready` 6/6.

The helpers are executed on values; the wiring drives the REAL `_run_phases`
from a `plans_after_plan` checkpoint with every phase stubbed.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from tests.test_resume_planning_reentry import (
    EFFORTS, MODELS, _StopAtExecute, _args, _caps, _plan, _stub_common,
    _subtask)


def _blocked(domain: str, kind: str | None, resource: str = "",
             basis: str = "the gap") -> dict:
    p = {"domain": domain, "status": "blocked", "subtasks": [],
         "confidence": {"task_understanding": 5, "decomposition_quality": 5,
                        "basis": basis}}
    if kind is not None:
        p["blocker_kind"] = kind
    if resource:
        p["external_resource"] = resource
    return p


def test_rows_carry_the_typed_blocker(leerie):
    plans = [_plan("bug-fixing", _subtask("bugfix-001")),
             _blocked("feature-implementation", "external_precondition",
                      "Task 03 merged to main"),
             _blocked("infrastructure", "evidence_gap", basis="which env?"),
             _blocked("testing", None)]
    rows = leerie._blocked_domain_rows(plans)
    assert [(r["domain"], r["blocker_kind"], r["external_resource"])
            for r in rows] == [
        ("feature-implementation", "external_precondition",
         "Task 03 merged to main"),
        ("infrastructure", "evidence_gap", ""),
        ("testing", "", "")]
    assert "which env?" in rows[1]["gap"]


def test_an_unknown_kind_never_reads_as_external(leerie):
    (row,) = leerie._blocked_domain_rows([_blocked("x", "external")])
    assert row["blocker_kind"] == ""


def _state(leerie, tmp_path, data=None):
    root = tmp_path / ".leerie"
    run_id = "domain-blocked-aaa111"
    (root / "runs" / run_id).mkdir(parents=True)
    st = leerie.State(root, run_id)
    st.data = dict(data or {})
    st.save()
    return st


def test_settle_records_an_empty_list_when_nothing_is_blocked(leerie,
                                                              tmp_path):
    st = _state(leerie, tmp_path, {"domain_blocked": [{"stale": 1}]})
    assert leerie._settle_blocked_domains(
        st, [_plan("bug-fixing", _subtask("bugfix-001"))]) == []
    assert st.data["domain_blocked"] == []


def test_an_evidence_gap_is_recorded_and_proceeds(leerie, tmp_path):
    st = _state(leerie, tmp_path)
    rows = leerie._settle_blocked_domains(
        st, [_plan("bug-fixing", _subtask("bugfix-001")),
             _blocked("infrastructure", "evidence_gap")])
    assert [r["domain"] for r in rows] == ["infrastructure"]
    assert st.data["domain_blocked"] == rows


def test_an_external_prerequisite_stops_and_is_shown(leerie, tmp_path,
                                                     capsys):
    st = _state(leerie, tmp_path)
    with pytest.raises(SystemExit) as exc:
        leerie._settle_blocked_domains(
            st, [_plan("bug-fixing", _subtask("bugfix-001")),
                 _blocked("feature-implementation", "external_precondition",
                          "Task 05 merged to main"),
                 _blocked("infrastructure", "evidence_gap")])
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "feature-implementation: Task 05 merged to main" in err
    assert "Nothing was executed" in err
    # Recorded BEFORE the stop, both rows, so state.json shows the user why.
    on_disk = json.loads(st.path.read_text())
    assert [r["blocker_kind"] for r in on_disk["domain_blocked"]] == [
        "external_precondition", "evidence_gap"]


# === wiring: the real _run_phases ==========================================

def _drive(leerie, monkeypatch, tmp_path, plans):
    calls: dict = {}
    _stub_common(leerie, monkeypatch, calls)
    root = tmp_path / ".leerie-state"
    run_id = "domain-blocked-wiring-aaa111"
    run_dir = root / "runs" / run_id
    (run_dir / "subtasks").mkdir(parents=True)
    st = leerie.State(root, run_id)
    st.data = {"task": "t", "worker_count": 3,
               "categories": ["bug-fixing", "feature-implementation"],
               "plans_after_classify": [], "plans_after_plan": plans}
    st.save()
    return calls, st, run_dir


def test_run_phases_stops_before_any_later_phase(leerie, monkeypatch,
                                                 tmp_path):
    plans = [_plan("bug-fixing", _subtask("bugfix-001")),
             _blocked("feature-implementation", "external_precondition",
                      "Task 03 merged to main")]
    calls, st, run_dir = _drive(leerie, monkeypatch, tmp_path, plans)
    with pytest.raises(SystemExit):
        asyncio.run(leerie._run_phases(
            _args(), _caps(leerie), run_dir, st, "codebase", "normal",
            MODELS, EFFORTS))
    assert "phase_reconcile" not in calls, \
        "nothing after planning may run once an external block is known"
    assert "plan_snapshot" not in st.data
    assert st.data["domain_blocked"][0]["external_resource"] == \
        "Task 03 merged to main"


def test_run_phases_records_a_partial_evidence_gap_and_schedules(
        leerie, monkeypatch, tmp_path):
    plans = [_plan("bug-fixing", _subtask("bugfix-001")),
             _blocked("feature-implementation", "evidence_gap")]
    calls, st, run_dir = _drive(leerie, monkeypatch, tmp_path, plans)
    with pytest.raises(_StopAtExecute):
        asyncio.run(leerie._run_phases(
            _args(), _caps(leerie), run_dir, st, "codebase", "normal",
            MODELS, EFFORTS))
    assert [r["domain"] for r in st.data["domain_blocked"]] == [
        "feature-implementation"]
    assert list(st.data["plan_snapshot"]["subtasks"]) == ["bugfix-001"]


def test_a_later_replan_that_blocks_externally_also_stops(
        leerie, monkeypatch, tmp_path):
    """The second call site, immediately before `_schedule()`: a gate's
    re-plan can produce the block after the post-plan check passed."""
    plans = [_plan("bug-fixing", _subtask("bugfix-001"))]
    calls, st, run_dir = _drive(leerie, monkeypatch, tmp_path, plans)

    async def _coverage_gate(plans_, task, st_, caps, models, efforts):
        return plans_ + [_blocked("testing", "external_precondition",
                                  "a staging account")]
    monkeypatch.setattr(leerie, "phase_planning_coverage_gate",
                        _coverage_gate)
    with pytest.raises(SystemExit):
        asyncio.run(leerie._run_phases(
            _args(), _caps(leerie), run_dir, st, "codebase", "normal",
            MODELS, EFFORTS))
    assert "plan_snapshot" not in st.data
    assert st.data["domain_blocked"][0]["domain"] == "testing"


def test_planner_schema_requires_the_typed_blocker(leerie):
    jsonschema = pytest.importorskip("jsonschema")
    schema = leerie.SCHEMAS["planner"]
    assert "blocker_kind" in schema["required"]
    ok = {"domain": "d", "status": "blocked", "subtasks": [],
          "blocker_kind": "external_precondition",
          "external_resource": "Task 03 merged"}
    jsonschema.validate(ok, schema)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({**ok, "blocker_kind": "external_prerequisite"},
                            schema)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({k: v for k, v in ok.items()
                             if k != "blocker_kind"}, schema)
