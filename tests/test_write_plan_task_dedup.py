"""N6: `_write_plan` must not inline the full task text into every
per-subtask spec file.

`plan.json` already carries the task verbatim under its top-level `"task"`
key (written by the same call). Duplicating it into every
`subtasks/<id>.json` was measured to bloat implementer briefs by
90.8-97.8% on large task documents, spilling past the CLI's Read cap. No
prompt reads a `_task` field (grep across `prompts/` and `commands/`), so
the per-subtask copy has no consumer to break by pointing at plan.json
instead.
"""
from __future__ import annotations

import json
from pathlib import Path


def _minimal_state(leerie, tmp_path: Path, run_id: str):
    leerie_root = tmp_path / ".leerie"
    (leerie_root / "runs" / run_id).mkdir(parents=True)
    (leerie_root / "runs" / run_id / "subtasks").mkdir()
    st = leerie.State(leerie_root, run_id)
    st.data = {"task": "test", "worker_count": 0,
               "answers": {"source_of_truth": "codebase"}}
    st.save()
    return st


def test_subtask_spec_does_not_inline_the_task_text(leerie, tmp_path):
    st = _minimal_state(leerie, tmp_path, "test-write-plan-dedup1")
    task = "A" * 5000  # large enough that accidental substring inclusion is obvious
    subtasks = {
        "feat-001": {"id": "feat-001", "title": "x",
                     "success_criteria_seed": "y", "size": "small",
                     "provides": [], "requires": [], "depends_on": []},
    }
    leerie._write_plan(st.run_dir, task, st, subtasks, [["feat-001"]])
    spec_path = st.run_dir / "subtasks" / "feat-001.json"
    raw = spec_path.read_text()
    assert task not in raw, (
        "subtask spec must not embed the full task text verbatim; "
        "it should reference the task sidecar instead"
    )
    spec = json.loads(raw)
    assert "_task" not in spec
    assert "_task_ref" in spec
    assert spec["_task_ref"] == str(st.run_dir / "task.md")


def test_task_sidecar_holds_the_task_verbatim(leerie, tmp_path):
    """`_task_ref` must resolve to a file whose bytes ARE the task — a ref
    to a file that does not exist, or holds something else, is worse than
    the inlined copy it replaced."""
    st = _minimal_state(leerie, tmp_path, "test-write-plan-sidecar")
    task = "# Heading\n\nthe real task text\n"
    subtasks = {
        "feat-001": {"id": "feat-001", "title": "x",
                     "success_criteria_seed": "y", "size": "small",
                     "provides": [], "requires": [], "depends_on": []},
    }
    leerie._write_plan(st.run_dir, task, st, subtasks, [["feat-001"]])
    spec = json.loads(
        (st.run_dir / "subtasks" / "feat-001.json").read_text())
    ref = Path(spec["_task_ref"])
    assert ref.exists(), "_task_ref points at a file that was never written"
    assert ref.read_text() == task
    assert spec["_task_ref_bytes"] == len(task.encode())


def test_deref_target_is_smaller_than_plan_json(leerie, tmp_path):
    """The target the worker is told to read must be SMALLER than
    plan.json, not larger.

    This is the invariant the first version of the N6 fix violated:
    `_task_ref` pointed at plan.json, which is the task text PLUS every
    subtask body, so it was strictly bigger than any single brief it
    replaced — 125,047 B against a 110,400 B brief on the run that
    reproduced N6. That relocates the Read-cap failure instead of removing
    it, and no test caught it because the only size assertion bounded the
    BRIEF, one level too shallow."""
    st = _minimal_state(leerie, tmp_path, "test-write-plan-deref-size")
    task = "T" * 70_000
    subtasks = {
        f"feat-{i:03d}": {"id": f"feat-{i:03d}", "title": "x",
                          "success_criteria_seed": "y", "size": "small",
                          "provides": [], "requires": [], "depends_on": []}
        for i in range(1, 12)
    }
    leerie._write_plan(st.run_dir, task, st, subtasks,
                       [[sid] for sid in subtasks])
    spec = json.loads(
        (st.run_dir / "subtasks" / "feat-001.json").read_text())
    deref = Path(spec["_task_ref"]).stat().st_size
    plan_json = (st.run_dir / "plan.json").stat().st_size
    assert deref < plan_json, (
        f"the deref target ({deref} B) is not smaller than plan.json "
        f"({plan_json} B) — pointing there relocates the Read-cap failure")
    # And it is the task itself plus nothing: no JSON envelope, no escaping.
    assert deref == len(task.encode())


def test_plan_json_still_carries_the_full_task_text(leerie, tmp_path):
    """The task text is not lost — it lives once, in plan.json."""
    st = _minimal_state(leerie, tmp_path, "test-write-plan-dedup2")
    task = "the real task text"
    subtasks = {
        "feat-001": {"id": "feat-001", "title": "x",
                     "success_criteria_seed": "y", "size": "small",
                     "provides": [], "requires": [], "depends_on": []},
    }
    leerie._write_plan(st.run_dir, task, st, subtasks, [["feat-001"]])
    plan = json.loads((st.run_dir / "plan.json").read_text())
    assert plan["task"] == task


def test_large_task_document_keeps_subtask_brief_small(leerie, tmp_path):
    """A 70KB synthetic task document must not blow up per-subtask brief
    size — regression guard for the CLI's ~25k-token Read-cap spill
    failure this fix addresses."""
    st = _minimal_state(leerie, tmp_path, "test-write-plan-dedup3")
    task = "T" * 70_000
    subtasks = {
        f"feat-{i:03d}": {"id": f"feat-{i:03d}", "title": "x",
                           "success_criteria_seed": "y", "size": "small",
                           "provides": [], "requires": [], "depends_on": []}
        for i in range(1, 4)
    }
    leerie._write_plan(st.run_dir, task, st, subtasks,
                        [[sid] for sid in subtasks])
    for sid in subtasks:
        spec_path = st.run_dir / "subtasks" / f"{sid}.json"
        size = spec_path.stat().st_size
        # ~25k tokens at a conservative ~2.5 bytes/token is well over 60KB;
        # a healthy brief with no inlined task should stay a couple KB.
        assert size < 5_000, (
            f"{sid}.json is {size} bytes — the task text appears to still "
            "be inlined into the subtask brief"
        )
