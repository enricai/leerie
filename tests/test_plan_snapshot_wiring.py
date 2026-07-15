"""Source-coupling pins for the `plan_snapshot` diagnostic capture.

`plan_snapshot` exists for exactly one reason: to persist the scheduled plan
*before* the two gates that `die()` — `check_budget_feasibility` and
`validate_plan`. A run that trips either gate loses the planner / fit_judge /
splitter spend entirely otherwise, because `write_plan` never runs (observed:
a real run died at `validate_plan` and its `state.json` had no plan key at all).

That guarantee is pure ordering, so a refactor moving the `st.save()` below
either gate would void the feature with a green suite. Source-coupling is the
correct tier: driving `_run_phases` to a real die() is not something the suite
does. Mirrors `test_dep_capture_wiring.py`'s `inspect.getsource` approach and
the `phase_plan` "expansion loop precedes final logging" guard in
`test_phase_plan_recursion_wiring.py`.

Deliberately NOT added to `test_state_fields.py`: that module is generic by
design (it parses `STATE_FIELDS` and the spec table without naming any single
field), and hard-coding one field name there would break its shape.
"""
from __future__ import annotations

import inspect
import json
import re
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
IMPL_MD = REPO_ROOT / "docs" / "IMPLEMENTATION.md"


def _phases_src(leerie) -> str:
    return inspect.getsource(leerie._run_phases)


class TestSnapshotPrecedesTheDieGates:
    """The whole point of the field: written after schedule(), before the
    gates that can terminate the run."""

    def test_snapshot_is_written_in_run_phases(self, leerie):
        src = _phases_src(leerie)
        assert 'st.data["plan_snapshot"]' in src, (
            "_run_phases must capture the scheduled plan into "
            'st.data["plan_snapshot"] — without it a die() at '
            "check_budget_feasibility / validate_plan discards the entire "
            "planning spend (write_plan never runs)."
        )

    def test_snapshot_is_saved(self, leerie):
        """An assignment without st.save() never reaches disk, which is the
        only place a post-mortem can read it from."""
        src = _phases_src(leerie)
        idx = src.find('st.data["plan_snapshot"]')
        assert idx != -1
        after = src[idx:idx + 200]
        assert "st.save()" in after, (
            'st.data["plan_snapshot"] must be followed by st.save(); an '
            "in-memory assignment is lost on the die()."
        )

    def test_snapshot_follows_schedule(self, leerie):
        """It captures schedule()'s output, so it cannot precede the call."""
        src = _phases_src(leerie)
        sched = src.find("subtasks, waves = schedule(plans)")
        snap = src.find('st.data["plan_snapshot"]')
        assert sched != -1, "_run_phases must call schedule(plans)"
        assert snap != -1
        assert sched < snap, (
            "plan_snapshot must be captured AFTER schedule() returns — it "
            "records that call's subtasks/waves."
        )

    def test_snapshot_precedes_budget_feasibility_gate(self, leerie):
        """check_budget_feasibility die()s with EXIT_BUDGET_INFEASIBLE."""
        src = _phases_src(leerie)
        snap = src.find('st.data["plan_snapshot"]')
        gate = src.find("check_budget_feasibility(")
        assert gate != -1, "_run_phases must call check_budget_feasibility"
        assert snap < gate, (
            "plan_snapshot must be captured BEFORE check_budget_feasibility — "
            "that gate die()s, and the snapshot is what makes the discarded "
            "plan inspectable afterward."
        )

    def test_snapshot_precedes_validate_plan_gate(self, leerie):
        """validate_plan die()s — the gate that killed the reported run."""
        src = _phases_src(leerie)
        snap = src.find('st.data["plan_snapshot"]')
        gate = src.find("validate_plan(subtasks)")
        assert gate != -1, "_run_phases must call validate_plan(subtasks)"
        assert snap < gate, (
            "plan_snapshot must be captured BEFORE validate_plan — that gate "
            "die()s on a dangling depends_on, and without the snapshot the "
            "whole planning spend is unrecoverable (the reported failure)."
        )

    def test_snapshot_is_not_write_plan(self, leerie):
        """Deliberately not write_plan(): that also emits per-subtask spec
        files and seeds the execution scaffolding, which would make a failed
        run look half-executable. The snapshot must land strictly earlier.

        Matches the real call site, not the bare substring — `write_plan(`
        also appears in the surrounding comments that explain this ordering.
        """
        src = _phases_src(leerie)
        snap = src.find('st.data["plan_snapshot"]')
        wp = src.find("write_plan(leerie_dir, task, st, subtasks, waves)")
        assert wp != -1, "_run_phases must call write_plan with the real plan"
        assert snap < wp, (
            "plan_snapshot must precede write_plan — it is the cheap "
            "diagnostic capture, not a substitute for the real plan write."
        )


class TestSnapshotIsDeclaredAndDocumented:
    """The generic coupling test in test_state_fields.py enforces this for
    every field; pinned explicitly here so the intent survives a refactor of
    that module."""

    def test_declared_in_state_fields(self, leerie):
        assert "plan_snapshot" in leerie.STATE_FIELDS

    def test_documented_in_impl_field_table(self):
        text = IMPL_MD.read_text()
        assert re.search(r"^\|\s*`plan_snapshot`\s*\|", text, re.MULTILINE), (
            "plan_snapshot needs a row in IMPLEMENTATION.md §8's state.json "
            "field table (CLAUDE.md: update the spec in the same change)."
        )


class TestSnapshotRoundTrips:
    """Behavioral: the payload survives a real State.save() to disk. A
    snapshot that cannot be read back post-mortem is worthless."""

    def test_snapshot_survives_state_save(self, leerie):
        with tempfile.TemporaryDirectory() as d:
            st = leerie.State(leerie_root=Path(d) / ".leerie",
                              run_id="snapshot-roundtrip")
            st.run_dir.mkdir(parents=True, exist_ok=True)
            st.data["plan_snapshot"] = {
                "subtasks": {"config-003-1": {"id": "config-003-1"}},
                "waves": [["config-003-1"]],
            }
            st.save()

            on_disk = json.loads(st.path.read_text())
            assert "plan_snapshot" in on_disk
            assert on_disk["plan_snapshot"]["waves"] == [["config-003-1"]]
            assert on_disk["plan_snapshot"]["subtasks"]["config-003-1"] == {
                "id": "config-003-1"}
