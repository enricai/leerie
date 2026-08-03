"""Tests for `_write_plan()`'s `preconditions` surface (DESIGN §5
`requires.extent`).

`_write_plan` reads `st.data["external_preconditions"]` (populated by
`phase_reconcile`) and writes it as the `preconditions` field in
`plan.json` alongside `{task, waves, subtasks}`. These tests pin the
read/write contract so a future refactor of either side can't silently
drop the surface — without it, planner-declared out-of-graph
prerequisites would be collected then never displayed.
"""
from __future__ import annotations

import json
from pathlib import Path


def _minimal_state(leerie, tmp_path: Path):
    leerie_root = tmp_path / ".leerie"
    run_id = "test-write-plan-bbb222"
    (leerie_root / "runs" / run_id).mkdir(parents=True)
    (leerie_root / "runs" / run_id / "subtasks").mkdir()
    st = leerie.State(leerie_root, run_id)
    st.data = {"task": "test", "worker_count": 0,
               "answers": {"source_of_truth": "codebase"}}
    st.save()
    return st


def test_write_plan_includes_preconditions_section(leerie, tmp_path):
    st = _minimal_state(leerie, tmp_path)
    st.data["external_preconditions"] = [
        {
            "tag": "dynamo-table-in-region",
            "reasons": [{"sid": "feat-007",
                         "reason": "provisioned by api-services CDK stack"}],
            "originating_subtasks": ["feat-007"],
        }
    ]
    st.save()
    subtasks = {
        "feat-001": {"id": "feat-001", "title": "x",
                     "success_criteria_seed": "y", "size": "small",
                     "provides": [], "requires": [], "depends_on": []},
    }
    leerie._write_plan(st.run_dir, "the task", st, subtasks, [["feat-001"]])
    plan = json.loads((st.run_dir / "plan.json").read_text())
    assert "preconditions" in plan
    assert len(plan["preconditions"]) == 1
    assert plan["preconditions"][0]["tag"] == "dynamo-table-in-region"
    assert plan["preconditions"][0]["originating_subtasks"] == ["feat-007"]


def test_write_plan_preconditions_empty_when_none_declared(leerie, tmp_path):
    """When no planner declared any `extent: external` entry,
    `preconditions` is an empty array — present but empty. The shape
    must be stable so downstream consumers can read the field
    unconditionally."""
    st = _minimal_state(leerie, tmp_path)
    # No external_preconditions key set on st.data.
    subtasks = {
        "feat-001": {"id": "feat-001", "title": "x",
                     "success_criteria_seed": "y", "size": "small",
                     "provides": [], "requires": [], "depends_on": []},
    }
    leerie._write_plan(st.run_dir, "the task", st, subtasks, [["feat-001"]])
    plan = json.loads((st.run_dir / "plan.json").read_text())
    assert plan["preconditions"] == []
