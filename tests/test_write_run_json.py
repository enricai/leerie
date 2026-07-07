"""Tests for `_write_run_json()` — atomic per-run sidecar writer.

Covers:
- Writes to `run_dir/run.json` atomically (temp-file rename pattern).
- Merges fields with existing sidecar contents.
- Validates via `_validate_run_json` before writing (fails closed on
  invariant violations).
- Survives malformed pre-existing sidecar (treats as empty dict and
  writes a clean one on top).
- Handles `None` field values (writes JSON null, e.g., to clear a
  prior error).
"""
from __future__ import annotations

import json

import pytest


def test_writes_to_run_dir_run_json(leerie, tmp_path):
    """Basic write to a fresh run dir."""
    leerie._write_run_json(tmp_path, run_id="feat-foo-abc123",
                             branch="leerie/runs/feat-foo-abc123")
    sidecar = tmp_path / "run.json"
    assert sidecar.exists()
    data = json.loads(sidecar.read_text())
    assert data["run_id"] == "feat-foo-abc123"
    assert data["branch"] == "leerie/runs/feat-foo-abc123"


def test_merges_with_existing(leerie, tmp_path):
    """Calling _write_run_json twice merges the second call's fields
    into the first call's contents — used to update push/PR status
    incrementally without re-writing every field."""
    leerie._write_run_json(tmp_path, run_id="feat-foo-abc123",
                             branch="leerie/runs/feat-foo-abc123",
                             task="add stuff")
    leerie._write_run_json(tmp_path, pushed_at="2026-05-26T15:00:00+00:00")
    data = json.loads((tmp_path / "run.json").read_text())
    # Original fields preserved.
    assert data["run_id"] == "feat-foo-abc123"
    assert data["task"] == "add stuff"
    # New field added.
    assert data["pushed_at"] == "2026-05-26T15:00:00+00:00"


def test_overwrites_existing_field(leerie, tmp_path):
    """A field provided in a later call overrides the earlier value."""
    leerie._write_run_json(tmp_path, push_error="first failure")
    leerie._write_run_json(tmp_path, push_error=None,
                             pushed_at="2026-05-26T15:00:00+00:00")
    data = json.loads((tmp_path / "run.json").read_text())
    assert data["push_error"] is None
    assert data["pushed_at"] == "2026-05-26T15:00:00+00:00"


def test_writes_null_for_none_values(leerie, tmp_path):
    """None-valued fields are written as JSON null, not omitted.
    Used to clear a prior error explicitly."""
    leerie._write_run_json(tmp_path, push_error=None, pr_url=None)
    raw = (tmp_path / "run.json").read_text()
    # Substring match because JSON dumps may vary on whitespace.
    assert '"push_error": null' in raw
    assert '"pr_url": null' in raw


def test_atomic_write_via_temp_rename(leerie, tmp_path):
    """The write uses a `.tmp` temp file + rename so a partial write
    can't leave a half-written sidecar on disk. After the call, the
    temp file should not remain."""
    leerie._write_run_json(tmp_path, run_id="feat-foo-abc123")
    assert (tmp_path / "run.json").exists()
    assert not (tmp_path / "run.tmp").exists()


def test_rejects_invariant_violation(leerie, tmp_path):
    """A write that produces an invariant-violating sidecar raises
    rather than persisting bad state. Specifically: pr_url set without
    pushed_at."""
    with pytest.raises(ValueError, match="PR cannot succeed"):
        leerie._write_run_json(tmp_path, pr_url="https://gh.com/pr/1")
    # And nothing got written.
    assert not (tmp_path / "run.json").exists()


def test_rejects_violation_on_update(leerie, tmp_path):
    """If a merge would produce an invariant-violating sidecar, raise
    and leave the existing sidecar unchanged."""
    # Set a valid initial state.
    leerie._write_run_json(tmp_path, pushed_at="2026-05-26T15:00:00+00:00")
    # An update that would set both pushed_at and push_error → violation.
    with pytest.raises(ValueError, match="pushed_at and push_error"):
        leerie._write_run_json(tmp_path, push_error="conflict somehow")
    # Existing sidecar is intact.
    data = json.loads((tmp_path / "run.json").read_text())
    assert data["pushed_at"] == "2026-05-26T15:00:00+00:00"


def test_malformed_existing_is_recovered(leerie, tmp_path):
    """A pre-existing run.json with garbage contents is treated as
    empty (logged + replaced) rather than crashing — the alternative
    would be a permanent-broken state nobody can recover from."""
    (tmp_path / "run.json").write_text("{not valid json")
    leerie._write_run_json(tmp_path, run_id="recovered")
    data = json.loads((tmp_path / "run.json").read_text())
    assert data == {"run_id": "recovered"}


def test_non_object_existing_is_recovered(leerie, tmp_path):
    """An existing run.json that's a JSON array (not an object) is
    treated as empty."""
    (tmp_path / "run.json").write_text('["not", "an", "object"]')
    leerie._write_run_json(tmp_path, run_id="recovered")
    data = json.loads((tmp_path / "run.json").read_text())
    assert data == {"run_id": "recovered"}


# --- group_id (DESIGN §20 run groups) -----------------------------------------

def test_group_id_written_and_preserved(leerie, tmp_path):
    """group_id is written by _write_run_json and survives a subsequent merge.

    The orchestrator writes group_id into run.json at run-start
    (DESIGN §20); later incremental writes (push, PR) must not clobber it.
    This is the durable round-trip claim distinct from the pure-validation
    tests in test_run_json_invariants.py."""
    leerie._write_run_json(
        tmp_path,
        run_id="feat-foo-abc123",
        branch="leerie/runs/feat-foo-abc123",
        working_branch="main",
        started_at="2026-05-26T14:00:00+00:00",
        task="add /volumes endpoint",
        group_id="a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    )
    data = json.loads((tmp_path / "run.json").read_text())
    assert data["group_id"] == "a1b2c3d4-e5f6-7890-abcd-ef1234567890"

    # Simulate a subsequent merge (push update) — group_id must survive.
    leerie._write_run_json(tmp_path, pushed_at="2026-05-26T15:00:00+00:00")
    data = json.loads((tmp_path / "run.json").read_text())
    assert data["group_id"] == "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
    assert data["run_id"] == "feat-foo-abc123"
    assert data["pushed_at"] == "2026-05-26T15:00:00+00:00"


def test_full_lifecycle(leerie, tmp_path):
    """End-to-end: simulate the full sequence of writes during a run
    lifecycle — start, finalize, push success, PR success — and
    confirm the final sidecar matches the documented status."""
    # 1. Run starts.
    leerie._write_run_json(
        tmp_path,
        run_id="feat-foo-abc123",
        branch="leerie/runs/feat-foo-abc123",
        working_branch="main",
        started_at="2026-05-26T14:00:00+00:00",
        task="x",
    )
    # 2. Finalize completes.
    leerie._write_run_json(
        tmp_path, finished_at="2026-05-26T15:00:00+00:00")
    # 3. Push succeeds.
    leerie._write_run_json(
        tmp_path, pushed_at="2026-05-26T15:00:05+00:00", push_error=None)
    # 4. PR opens.
    leerie._write_run_json(
        tmp_path,
        pr_url="https://github.com/owner/repo/pull/123",
        pr_error=None,
    )
    data = json.loads((tmp_path / "run.json").read_text())
    assert data["run_id"] == "feat-foo-abc123"
    assert data["pushed_at"] == "2026-05-26T15:00:05+00:00"
    assert data["pr_url"] == "https://github.com/owner/repo/pull/123"
    assert data["push_error"] is None
    assert data["pr_error"] is None
