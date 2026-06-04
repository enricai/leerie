"""Tests for `discover_runs()` — enumerates `.leerie/runs/*/state.json`
for `--list` and `--resume` discovery.

Covers: empty repo, single run, multiple runs (sorted), bootstrap dir
skipped, malformed state.json skipped with warning, non-dict state.json
skipped.

Uses a `tmp_path` fixture for filesystem isolation — no mocking; this is
a pure-I/O function reading real files."""
from __future__ import annotations

import json
from pathlib import Path

import pytest


def _make_run(leerie_root: Path, run_id: str, state: dict) -> Path:
    run_dir = leerie_root / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "state.json").write_text(json.dumps(state))
    return run_dir


def test_discover_runs_empty_dir(leerie, tmp_path):
    """No `.leerie/runs/` directory → empty list, no error."""
    assert leerie.discover_runs(tmp_path) == []


def test_discover_runs_empty_runs_dir(leerie, tmp_path):
    """`.leerie/runs/` exists but has no children → empty list."""
    (tmp_path / "runs").mkdir()
    assert leerie.discover_runs(tmp_path) == []


def test_discover_runs_single_run(leerie, tmp_path):
    _make_run(tmp_path, "feat-foo-abc123",
              {"task": "do thing", "started_at": "2026-05-26T10:00:00+00:00"})
    runs = leerie.discover_runs(tmp_path)
    assert len(runs) == 1
    assert runs[0]["run_id"] == "feat-foo-abc123"
    assert runs[0]["task"] == "do thing"
    assert "path" in runs[0]


def test_discover_runs_multiple_runs_sorted_newest_first(leerie, tmp_path):
    """Newest by `started_at` sorts first, so `--list` shows most-recent at top."""
    _make_run(tmp_path, "feat-a-aaaaaa",
              {"task": "a", "started_at": "2026-05-26T10:00:00+00:00"})
    _make_run(tmp_path, "feat-b-bbbbbb",
              {"task": "b", "started_at": "2026-05-26T12:00:00+00:00"})
    _make_run(tmp_path, "feat-c-cccccc",
              {"task": "c", "started_at": "2026-05-26T11:00:00+00:00"})
    runs = leerie.discover_runs(tmp_path)
    assert [r["run_id"] for r in runs] == [
        "feat-b-bbbbbb", "feat-c-cccccc", "feat-a-aaaaaa"
    ]


def test_discover_runs_skips_bootstrap_dirs(leerie, tmp_path):
    """`_bootstrap-<hex>/` directories are pre-classify scratch space;
    they should never appear in discovery results."""
    _make_run(tmp_path, "_bootstrap-abc123",
              {"task": "in-progress", "started_at": "2026-05-26T13:00:00+00:00"})
    _make_run(tmp_path, "feat-foo-def456",
              {"task": "real", "started_at": "2026-05-26T10:00:00+00:00"})
    runs = leerie.discover_runs(tmp_path)
    assert len(runs) == 1
    assert runs[0]["run_id"] == "feat-foo-def456"


def test_discover_runs_skips_non_dirs(leerie, tmp_path):
    """A regular file in `runs/` is not a run; ignore it silently."""
    (tmp_path / "runs").mkdir()
    (tmp_path / "runs" / "stray-file").write_text("garbage")
    _make_run(tmp_path, "feat-foo-abc123",
              {"task": "x", "started_at": "2026-05-26T10:00:00+00:00"})
    runs = leerie.discover_runs(tmp_path)
    assert len(runs) == 1


def test_discover_runs_skips_empty_dirs(leerie, tmp_path):
    """A run directory missing both `state.json` AND `fly-machine.json`
    is genuinely empty — not surfaced. (Pre-Change-4 behavior: every
    dir without state.json was skipped, hiding orphan runs that died
    during seed_auth. Post-Change-4: orphans with fly-machine.json ARE
    surfaced; dirs with neither file remain skipped.)"""
    (tmp_path / "runs" / "feat-broken-xyz789").mkdir(parents=True)
    _make_run(tmp_path, "feat-foo-abc123",
              {"task": "x", "started_at": "2026-05-26T10:00:00+00:00"})
    runs = leerie.discover_runs(tmp_path)
    assert len(runs) == 1
    assert runs[0]["run_id"] == "feat-foo-abc123"


def _make_orphan(leerie_root: Path, run_id: str, fly: dict) -> Path:
    """Helper: create a run dir with ONLY fly-machine.json (no
    state.json) — the on-disk shape produced when seed_auth aborts
    before phase_classify completes."""
    run_dir = leerie_root / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "fly-machine.json").write_text(json.dumps(fly))
    return run_dir


def test_discover_runs_surfaces_orphan_with_fly_machine_json(leerie, tmp_path):
    """A run dir with fly-machine.json but no state.json is the
    pre-classify failure case (seed_auth aborted before the orchestrator
    wrote state.json). discover_runs must surface it so --list and
    --resume can reach it. Marked `_orphan=True` with started_at copied
    from the fly sidecar."""
    _make_orphan(tmp_path, "feat-seed-died-abc123", {
        "fly_app": "leerie",
        "fly_machine_id": "287061da360d78",
        "started_at": "2026-06-04T19:20:58+00:00",
        "run_id": "_bootstrap-bbd47f",
    })
    runs = leerie.discover_runs(tmp_path)
    assert len(runs) == 1
    assert runs[0]["run_id"] == "feat-seed-died-abc123"
    assert runs[0]["_orphan"] is True
    assert runs[0]["started_at"] == "2026-06-04T19:20:58+00:00"
    # path points at fly-machine.json (state.json doesn't exist).
    assert runs[0]["path"].endswith("/fly-machine.json")


def test_discover_runs_skips_bootstrap_orphans(leerie, tmp_path):
    """_bootstrap-* dirs are always pre-classify scratch; even when they
    have fly-machine.json (the launcher writes it before promotion to a
    real run id), they are not real runs and must not be surfaced via
    the orphan path. resolve_run_id's existing _bootstrap-* carve-out
    handles them when explicitly named via --run-id."""
    _make_orphan(tmp_path, "_bootstrap-abc123", {
        "fly_machine_id": "286d2d0b3e7798",
        "started_at": "2026-06-04T19:18:27+00:00",
    })
    _make_run(tmp_path, "feat-foo-def456",
              {"task": "real", "started_at": "2026-05-26T10:00:00+00:00"})
    runs = leerie.discover_runs(tmp_path)
    assert len(runs) == 1
    assert runs[0]["run_id"] == "feat-foo-def456"


def test_discover_runs_skips_malformed_fly_machine_json(leerie, tmp_path, capsys):
    """A fly-machine.json with invalid JSON is logged and skipped —
    matches the existing state.json error path. Other runs still
    surface."""
    run_dir = tmp_path / "runs" / "feat-broken-fly-xyz999"
    run_dir.mkdir(parents=True)
    (run_dir / "fly-machine.json").write_text("{not valid json")
    _make_run(tmp_path, "feat-foo-abc123",
              {"task": "x", "started_at": "2026-05-26T10:00:00+00:00"})
    runs = leerie.discover_runs(tmp_path)
    # The malformed orphan does NOT appear; the healthy run does.
    assert [r["run_id"] for r in runs] == ["feat-foo-abc123"]


def test_discover_runs_mixed_orphan_and_healthy(leerie, tmp_path):
    """Orphans and healthy runs coexist in --list — sorted by
    started_at descending like any other rows."""
    _make_orphan(tmp_path, "feat-died-aaa111", {
        "fly_machine_id": "machine-aaa",
        "started_at": "2026-06-04T19:20:00+00:00",  # newest
    })
    _make_run(tmp_path, "feat-old-bbb222", {
        "task": "y",
        "started_at": "2026-06-04T10:00:00+00:00",  # oldest
    })
    _make_run(tmp_path, "feat-mid-ccc333", {
        "task": "z",
        "started_at": "2026-06-04T15:00:00+00:00",
    })
    runs = leerie.discover_runs(tmp_path)
    assert [r["run_id"] for r in runs] == [
        "feat-died-aaa111",  # newest
        "feat-mid-ccc333",
        "feat-old-bbb222",
    ]
    # Only the orphan carries the marker.
    assert runs[0].get("_orphan") is True
    assert runs[1].get("_orphan") is None
    assert runs[2].get("_orphan") is None


def test_discover_runs_skips_malformed_json(leerie, tmp_path, capsys):
    """A state.json with invalid JSON triggers a warning log but doesn't
    raise — `--list` should still work in the presence of corrupted runs."""
    run_dir = tmp_path / "runs" / "feat-bad-xyz999"
    run_dir.mkdir(parents=True)
    (run_dir / "state.json").write_text("{not valid json")
    _make_run(tmp_path, "feat-foo-abc123",
              {"task": "x", "started_at": "2026-05-26T10:00:00+00:00"})
    runs = leerie.discover_runs(tmp_path)
    assert len(runs) == 1
    assert runs[0]["run_id"] == "feat-foo-abc123"


def test_discover_runs_skips_non_object_state(leerie, tmp_path):
    """state.json that contains valid JSON but not an object (array,
    string) is still useless to leerie; skip it."""
    run_dir = tmp_path / "runs" / "feat-array-xyz000"
    run_dir.mkdir(parents=True)
    (run_dir / "state.json").write_text('["this is", "an array"]')
    runs = leerie.discover_runs(tmp_path)
    assert runs == []


def test_discover_runs_handles_missing_started_at(leerie, tmp_path):
    """A run without `started_at` sorts last (treated as the empty string).
    Doesn't crash the sort."""
    _make_run(tmp_path, "feat-newer-aaa111",
              {"task": "a", "started_at": "2026-05-26T10:00:00+00:00"})
    _make_run(tmp_path, "feat-undated-bbb222",
              {"task": "b"})  # no started_at
    runs = leerie.discover_runs(tmp_path)
    assert len(runs) == 2
    # The one with started_at sorts first; undated sorts last.
    assert runs[0]["run_id"] == "feat-newer-aaa111"
    assert runs[1]["run_id"] == "feat-undated-bbb222"


def test_discover_runs_preserves_state_fields(leerie, tmp_path):
    """Discovered summary includes the full state.json contents, plus
    `run_id` and `path` overlay fields — callers (--list) need access to
    `categories`, `worker_count`, etc."""
    _make_run(tmp_path, "feat-foo-abc123", {
        "task": "x",
        "started_at": "2026-05-26T10:00:00+00:00",
        "finished_at": "2026-05-26T11:00:00+00:00",
        "categories": ["feature-implementation"],
        "worker_count": 17,
    })
    runs = leerie.discover_runs(tmp_path)
    assert runs[0]["finished_at"] == "2026-05-26T11:00:00+00:00"
    assert runs[0]["categories"] == ["feature-implementation"]
    assert runs[0]["worker_count"] == 17
