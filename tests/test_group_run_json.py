"""Tests for group_id in run.json sidecars (DESIGN §20 run groups).

Verifies that _validate_run_json accepts group_id-bearing sidecars in
every push/pause/kill state, _write_run_json persists and preserves
group_id across incremental writes, and _derive_run_status produces the
correct status for group_id-tagged runs (local-stub member with no
fly_machine_id and fly-stub member with fly_machine_id).

These tests exercise the Python-layer run.json contract; bash-layer fan-out
and cross-state-dir verb dispatch are covered in test_group_launcher.py.
"""
from __future__ import annotations

import json

import pytest


GROUP_ID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"


def _base(**overrides) -> dict:
    """Minimal valid run.json with no terminal states."""
    base = {
        "run_id": "feat-grp-abc123",
        "branch": "leerie/runs/feat-grp-abc123",
        "working_branch": "main",
        "started_at": "2026-07-01T10:00:00+00:00",
        "task": "add /volumes endpoint",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# _validate_run_json accepts group_id in all states
# ---------------------------------------------------------------------------


class TestValidateRunJsonGroupId:
    """_validate_run_json must not reject group_id regardless of run state."""

    def test_accepts_group_id_in_progress(self, leerie) -> None:
        """group_id is accepted when no terminal state is set (local-stub member)."""
        leerie._validate_run_json(_base(group_id=GROUP_ID))

    def test_accepts_group_id_with_fly_machine_id(self, leerie) -> None:
        """group_id coexists with fly_machine_id (fly-stub member)."""
        leerie._validate_run_json(_base(
            group_id=GROUP_ID,
            fly_machine_id="fly-abc123",
        ))

    def test_accepts_group_id_paused(self, leerie) -> None:
        """group_id is accepted on a paused fly-stub member."""
        leerie._validate_run_json(_base(
            group_id=GROUP_ID,
            fly_machine_id="fly-abc123",
            paused_at="2026-07-01T11:00:00+00:00",
            pause_reason="worker-error",
        ))

    def test_accepts_group_id_pushed_no_pr(self, leerie) -> None:
        """group_id is accepted when pushed_at is set (done-pushed-no-pr)."""
        leerie._validate_run_json(_base(
            group_id=GROUP_ID,
            finished_at="2026-07-01T11:00:00+00:00",
            pushed_at="2026-07-01T11:00:05+00:00",
        ))

    def test_accepts_group_id_pushed_with_pr(self, leerie) -> None:
        """group_id is accepted on a fully pushed + PR'd run."""
        leerie._validate_run_json(_base(
            group_id=GROUP_ID,
            finished_at="2026-07-01T11:00:00+00:00",
            pushed_at="2026-07-01T11:00:05+00:00",
            pr_url="https://github.com/owner/repo/pull/42",
        ))

    def test_accepts_group_id_killed(self, leerie) -> None:
        """group_id is accepted on a killed fly-stub member."""
        leerie._validate_run_json(_base(
            group_id=GROUP_ID,
            fly_machine_id="fly-abc123",
            killed_at="2026-07-01T11:00:00+00:00",
        ))

    def test_accepts_group_id_push_failed(self, leerie) -> None:
        """group_id is accepted when push_error is set."""
        leerie._validate_run_json(_base(
            group_id=GROUP_ID,
            finished_at="2026-07-01T11:00:00+00:00",
            push_error="authentication failed",
        ))

    def test_group_id_is_passthrough(self, leerie) -> None:
        """group_id has no mutual-exclusion invariant; other unknown keys also pass."""
        leerie._validate_run_json(_base(
            group_id=GROUP_ID,
            extra_unknown_key="ignored",
        ))


# ---------------------------------------------------------------------------
# _write_run_json persists group_id correctly (round-trip)
# ---------------------------------------------------------------------------


class TestWriteRunJsonGroupId:
    """_write_run_json writes group_id and preserves it across incremental writes."""

    def test_group_id_written_to_disk(self, leerie, tmp_path) -> None:
        """group_id appears in the sidecar immediately after the first write."""
        leerie._write_run_json(
            tmp_path,
            run_id="feat-grp-abc123",
            branch="leerie/runs/feat-grp-abc123",
            working_branch="main",
            started_at="2026-07-01T10:00:00+00:00",
            task="add /volumes endpoint",
            group_id=GROUP_ID,
        )
        data = json.loads((tmp_path / "run.json").read_text())
        assert data["group_id"] == GROUP_ID

    def test_group_id_survives_push_update(self, leerie, tmp_path) -> None:
        """group_id is not clobbered by a subsequent incremental write."""
        leerie._write_run_json(
            tmp_path,
            run_id="feat-grp-abc123",
            branch="leerie/runs/feat-grp-abc123",
            working_branch="main",
            started_at="2026-07-01T10:00:00+00:00",
            task="add /volumes endpoint",
            group_id=GROUP_ID,
        )
        leerie._write_run_json(
            tmp_path,
            finished_at="2026-07-01T11:00:00+00:00",
            pushed_at="2026-07-01T11:00:05+00:00",
        )
        data = json.loads((tmp_path / "run.json").read_text())
        assert data["group_id"] == GROUP_ID
        assert data["pushed_at"] == "2026-07-01T11:00:05+00:00"

    def test_group_id_survives_pr_update(self, leerie, tmp_path) -> None:
        """group_id persists through push + PR write sequence."""
        leerie._write_run_json(
            tmp_path,
            run_id="feat-grp-abc123",
            branch="leerie/runs/feat-grp-abc123",
            working_branch="main",
            started_at="2026-07-01T10:00:00+00:00",
            task="add /volumes endpoint",
            group_id=GROUP_ID,
        )
        leerie._write_run_json(
            tmp_path,
            finished_at="2026-07-01T11:00:00+00:00",
            pushed_at="2026-07-01T11:00:05+00:00",
        )
        leerie._write_run_json(
            tmp_path,
            pr_url="https://github.com/owner/repo/pull/42",
        )
        data = json.loads((tmp_path / "run.json").read_text())
        assert data["group_id"] == GROUP_ID
        assert data["pr_url"] == "https://github.com/owner/repo/pull/42"

    def test_local_member_no_fly_machine_id(self, leerie, tmp_path) -> None:
        """A local-stub group member (no fly_machine_id) round-trips cleanly."""
        leerie._write_run_json(
            tmp_path,
            run_id="feat-grp-local",
            branch="leerie/runs/feat-grp-local",
            working_branch="main",
            started_at="2026-07-01T10:00:00+00:00",
            task="add disk dialog",
            group_id=GROUP_ID,
        )
        data = json.loads((tmp_path / "run.json").read_text())
        assert data["group_id"] == GROUP_ID
        assert "fly_machine_id" not in data

    def test_fly_member_with_fly_machine_id(self, leerie, tmp_path) -> None:
        """A fly-stub group member (with fly_machine_id) round-trips cleanly."""
        leerie._write_run_json(
            tmp_path,
            run_id="feat-grp-fly",
            branch="leerie/runs/feat-grp-fly",
            working_branch="main",
            started_at="2026-07-01T10:00:00+00:00",
            task="add disk dialog",
            group_id=GROUP_ID,
            fly_machine_id="fly-9876",
        )
        data = json.loads((tmp_path / "run.json").read_text())
        assert data["group_id"] == GROUP_ID
        assert data["fly_machine_id"] == "fly-9876"


# ---------------------------------------------------------------------------
# _derive_run_status is correct for group_id-tagged runs
# ---------------------------------------------------------------------------


class TestDeriveRunStatusGroupId:
    """_derive_run_status returns the correct status for group members."""

    def test_in_progress_local_member(self, leerie) -> None:
        """Local-stub member with no fly_machine_id and no terminal state is in-progress."""
        status = leerie._derive_run_status(
            run_json=_base(group_id=GROUP_ID),
            state_json=None,
        )
        assert status == "in-progress"

    def test_done_local_member(self, leerie) -> None:
        """Local-stub member with finished_at and no push is 'done'."""
        status = leerie._derive_run_status(
            run_json=_base(
                group_id=GROUP_ID,
                finished_at="2026-07-01T11:00:00+00:00",
            ),
            state_json={"waves": [["s-001"]], "completed_waves": 1},
        )
        assert status == "done"

    def test_done_pushed_pr_local_member(self, leerie) -> None:
        """Local-stub member with pr_url is 'done-pushed-pr'."""
        status = leerie._derive_run_status(
            run_json=_base(
                group_id=GROUP_ID,
                finished_at="2026-07-01T11:00:00+00:00",
                pushed_at="2026-07-01T11:00:05+00:00",
                pr_url="https://github.com/owner/repo/pull/42",
            ),
            state_json=None,
        )
        assert status == "done-pushed-pr"

    def test_paused_fly_member(self, leerie) -> None:
        """Fly-stub member with paused_at is 'paused'."""
        status = leerie._derive_run_status(
            run_json=_base(
                group_id=GROUP_ID,
                fly_machine_id="fly-9876",
                paused_at="2026-07-01T11:00:00+00:00",
                pause_reason="worker-error",
            ),
            state_json=None,
        )
        assert status == "paused"

    def test_killed_fly_member(self, leerie) -> None:
        """Fly-stub member with killed_at is 'killed'."""
        status = leerie._derive_run_status(
            run_json=_base(
                group_id=GROUP_ID,
                fly_machine_id="fly-9876",
                killed_at="2026-07-01T11:00:00+00:00",
            ),
            state_json=None,
        )
        assert status == "killed"

    def test_group_id_does_not_affect_status(self, leerie) -> None:
        """Presence of group_id alone does not alter status relative to a standalone run."""
        without_group = leerie._derive_run_status(
            run_json=_base(
                finished_at="2026-07-01T11:00:00+00:00",
                pushed_at="2026-07-01T11:00:05+00:00",
            ),
            state_json=None,
        )
        with_group = leerie._derive_run_status(
            run_json=_base(
                group_id=GROUP_ID,
                finished_at="2026-07-01T11:00:00+00:00",
                pushed_at="2026-07-01T11:00:05+00:00",
            ),
            state_json=None,
        )
        assert without_group == with_group == "done-pushed-no-pr"
