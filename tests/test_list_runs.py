"""Tests for `_list_runs()` — the `leerie list` rendering function.

Behavioral tests use `tmp_path` for filesystem isolation. The function
reads `.leerie/runs/*/state.json` (via _discover_runs) and overlays
`.leerie/runs/*/run.json` for status, then renders a sortable table
to stdout. Tests capture stdout via the `capsys` fixture.
"""
from __future__ import annotations

import json
from pathlib import Path


def _make_run(root: Path, run_id: str, state: dict,
              run_json: dict | None = None) -> None:
    rd = root / "runs" / run_id
    rd.mkdir(parents=True, exist_ok=True)
    (rd / "state.json").write_text(json.dumps(state))
    if run_json is not None:
        (rd / "run.json").write_text(json.dumps(run_json))


def test_list_runs_empty(leerie, tmp_path, capsys):
    leerie._list_runs(tmp_path)
    out = capsys.readouterr().out
    assert "no runs" in out


def test_list_runs_renders_table_header(leerie, tmp_path, capsys):
    _make_run(tmp_path, "feat-a-aaaaaa",
              {"started_at": "2026-05-26T10:00:00+00:00", "task": "x"})
    leerie._list_runs(tmp_path)
    out = capsys.readouterr().out
    # Header columns are present.
    for col in ("run_id", "started_at", "status", "branch"):
        assert col in out


def test_list_runs_in_progress_status(leerie, tmp_path, capsys):
    """A run with no run.json sidecar reads as in-progress."""
    _make_run(tmp_path, "feat-a-aaaaaa",
              {"started_at": "2026-05-26T10:00:00+00:00", "task": "x"})
    leerie._list_runs(tmp_path)
    out = capsys.readouterr().out
    assert "in-progress" in out
    assert "feat-a-aaaaaa" in out


def test_list_runs_done_pushed_pr_status(leerie, tmp_path, capsys):
    _make_run(tmp_path, "feat-a-aaaaaa",
              {"started_at": "2026-05-26T10:00:00+00:00", "task": "x"},
              run_json={
                  "finished_at": "2026-05-26T11:00:00+00:00",
                  "pushed_at": "2026-05-26T11:00:05+00:00",
                  "pr_url": "https://github.com/owner/repo/pull/1",
              })
    leerie._list_runs(tmp_path)
    out = capsys.readouterr().out
    assert "done-pushed-pr" in out


def test_list_runs_push_failed_status(leerie, tmp_path, capsys):
    _make_run(tmp_path, "fix-b-bbbbbb",
              {"started_at": "2026-05-26T10:00:00+00:00", "task": "y"},
              run_json={
                  "finished_at": "2026-05-26T11:00:00+00:00",
                  "push_error": "fatal: ...",
              })
    leerie._list_runs(tmp_path)
    out = capsys.readouterr().out
    assert "push-failed" in out


def test_list_runs_sorted_newest_first(leerie, tmp_path, capsys):
    """_discover_runs returns newest-first; _list_runs preserves that
    ordering so the table reads naturally."""
    _make_run(tmp_path, "feat-old-aaaaaa",
              {"started_at": "2026-05-26T10:00:00+00:00", "task": "x"})
    _make_run(tmp_path, "feat-new-bbbbbb",
              {"started_at": "2026-05-26T12:00:00+00:00", "task": "y"})
    leerie._list_runs(tmp_path)
    out = capsys.readouterr().out
    newest_pos = out.index("feat-new-bbbbbb")
    oldest_pos = out.index("feat-old-aaaaaa")
    assert newest_pos < oldest_pos


def test_list_runs_corrupt_sidecar(leerie, tmp_path, capsys):
    """A run.json that violates invariants is rendered as corrupt-sidecar."""
    _make_run(tmp_path, "feat-a-aaaaaa",
              {"started_at": "2026-05-26T10:00:00+00:00", "task": "x"},
              run_json={
                  "pushed_at": "2026-05-26T11:00:05+00:00",
                  "push_error": "violation: both set",
              })
    leerie._list_runs(tmp_path)
    out = capsys.readouterr().out
    assert "corrupt-sidecar" in out


def test_list_runs_malformed_run_json_treated_as_missing(leerie, tmp_path, capsys):
    """An unparseable run.json doesn't crash _list_runs; the run renders
    as in-progress (no sidecar info usable)."""
    rd = tmp_path / "runs" / "feat-broken-xyz999"
    rd.mkdir(parents=True)
    (rd / "state.json").write_text(json.dumps({
        "started_at": "2026-05-26T10:00:00+00:00", "task": "x",
    }))
    (rd / "run.json").write_text("{not valid json")
    leerie._list_runs(tmp_path)
    out = capsys.readouterr().out
    assert "feat-broken-xyz999" in out
    assert "in-progress" in out


def test_list_runs_renders_branch_from_run_json(leerie, tmp_path, capsys):
    _make_run(tmp_path, "feat-a-aaaaaa",
              {"started_at": "2026-05-26T10:00:00+00:00", "task": "x"},
              run_json={"branch": "leerie/runs/feat-a-aaaaaa"})
    leerie._list_runs(tmp_path)
    out = capsys.readouterr().out
    assert "leerie/runs/feat-a-aaaaaa" in out


def test_list_runs_falls_back_to_compute_run_branch(leerie, tmp_path, capsys):
    """If run.json is missing or has no `branch` field, _list_runs derives
    it from the run_id via _compute_run_branch."""
    _make_run(tmp_path, "feat-a-aaaaaa",
              {"started_at": "2026-05-26T10:00:00+00:00", "task": "x"})
    leerie._list_runs(tmp_path)
    out = capsys.readouterr().out
    assert "leerie/runs/feat-a-aaaaaa" in out


# --- --status filter (DESIGN §6 unified --list) ----------

def test_list_runs_status_filter_in_progress(leerie, tmp_path, capsys):
    """--list --status in-progress filters to running runs."""
    _make_run(tmp_path, "feat-running-aaaaa",
              {"started_at": "2026-05-29T10:00:00+00:00", "task": "x"})
    _make_run(tmp_path, "feat-paused-bbbbb",
              {"started_at": "2026-05-29T11:00:00+00:00", "task": "y"},
              run_json={
                  "paused_at": "2026-05-29T12:00:00+00:00",
                  "fly_machine_id": "mach-paused",
              })
    leerie._list_runs(tmp_path, status_filter="in-progress")
    out = capsys.readouterr().out
    assert "feat-running-aaaaa" in out
    assert "feat-paused-bbbbb" not in out


def test_list_runs_status_filter_killed_remote(leerie, tmp_path, capsys):
    """--list --status killed isolates killed runs."""
    _make_run(tmp_path, "feat-killed-aaaaa",
              {"started_at": "2026-05-29T10:00:00+00:00", "task": "x"},
              run_json={
                  "killed_at": "2026-05-29T11:00:00+00:00",
                  "fly_machine_id": "mach-killed",
              })
    _make_run(tmp_path, "feat-running-bbbbb",
              {"started_at": "2026-05-29T11:00:00+00:00", "task": "y"})
    leerie._list_runs(tmp_path, status_filter="killed")
    out = capsys.readouterr().out
    assert "feat-killed-aaaaa" in out
    assert "killed" in out
    assert "feat-running-bbbbb" not in out


def test_list_runs_status_filter_empty_match_message(leerie, tmp_path, capsys):
    """Filter that matches nothing prints a useful empty message."""
    _make_run(tmp_path, "feat-a-aaaaaa",
              {"started_at": "2026-05-29T10:00:00+00:00", "task": "x"})
    leerie._list_runs(tmp_path, status_filter="killed")
    out = capsys.readouterr().out
    assert "no runs" in out and "killed" in out


# --- Change 4: orphan dirs (seed_auth failure before state.json) --------

def _make_orphan(root: Path, run_id: str, fly: dict) -> None:
    rd = root / "runs" / run_id
    rd.mkdir(parents=True, exist_ok=True)
    (rd / "fly-machine.json").write_text(json.dumps(fly))


def test_list_runs_shows_orphan_with_seed_failed_status(leerie, tmp_path, capsys):
    """A run dir with only fly-machine.json must appear in --list with
    status `seed-failed`. This is the discoverability fix for the
    2026-06-04 incident hangs."""
    _make_orphan(tmp_path, "feat-died-aaa111", {
        "fly_machine_id": "287061da360d78",
        "started_at": "2026-06-04T19:20:58+00:00",
    })
    leerie._list_runs(tmp_path)
    out = capsys.readouterr().out
    assert "feat-died-aaa111" in out
    assert "seed-failed" in out
    # started_at from fly-machine.json renders.
    assert "2026-06-04T19:20:58" in out


def test_list_runs_status_filter_seed_failed(leerie, tmp_path, capsys):
    """--list --status seed-failed isolates orphan runs. This is the
    final piece of the discoverability fix: users with a mix of healthy
    and seed-failed runs can list just the broken ones."""
    _make_orphan(tmp_path, "feat-died-aaa111", {
        "fly_machine_id": "287061da360d78",
        "started_at": "2026-06-04T19:20:58+00:00",
    })
    _make_run(tmp_path, "feat-live-bbb222",
              {"started_at": "2026-06-04T20:00:00+00:00", "task": "y"})
    leerie._list_runs(tmp_path, status_filter="seed-failed")
    out = capsys.readouterr().out
    assert "feat-died-aaa111" in out
    assert "feat-live-bbb222" not in out
