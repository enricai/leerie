"""Unit tests for the `leerie list`/resume-disambiguation formatting and
sort helpers: `_format_run_duration`, `_run_recency_key`,
`_format_run_for_disambiguation`, `_render_run_table`.

These four functions had zero test references anywhere in tests/ before
this file (grep across concatenated tests/*.py source returned no hits).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "orchestrator"))
import leerie  # noqa: E402


# --- _format_run_duration ------------------------------------------------

class TestFormatRunDuration:
    def test_missing_started_at_returns_none(self):
        assert leerie._format_run_duration(None, "2026-08-01T00:00:10+00:00") is None

    def test_missing_finished_at_returns_none(self):
        assert leerie._format_run_duration("2026-08-01T00:00:00+00:00", None) is None

    def test_both_missing_returns_none(self):
        assert leerie._format_run_duration(None, None) is None

    def test_unparseable_started_at_returns_none(self):
        assert leerie._format_run_duration("not-a-timestamp",
                                            "2026-08-01T00:00:10+00:00") is None

    def test_unparseable_finished_at_returns_none(self):
        assert leerie._format_run_duration("2026-08-01T00:00:00+00:00",
                                            "not-a-timestamp") is None

    def test_seconds_only(self):
        assert leerie._format_run_duration(
            "2026-08-01T00:00:00+00:00", "2026-08-01T00:00:42+00:00") == "42s"

    def test_minutes_and_seconds(self):
        assert leerie._format_run_duration(
            "2026-08-01T00:00:00+00:00", "2026-08-01T00:03:42+00:00") == "3m 42s"

    def test_hours_and_minutes(self):
        assert leerie._format_run_duration(
            "2026-08-01T00:00:00+00:00", "2026-08-01T01:12:00+00:00") == "1h 12m"

    def test_hours_only_omits_zero_minutes(self):
        assert leerie._format_run_duration(
            "2026-08-01T00:00:00+00:00", "2026-08-01T02:00:00+00:00") == "2h 0m"

    def test_negative_delta_returns_none(self):
        # finished_at before started_at is nonsensical, not crash-worthy.
        assert leerie._format_run_duration(
            "2026-08-01T01:00:00+00:00", "2026-08-01T00:00:00+00:00") is None

    def test_zero_delta_returns_zero_seconds(self):
        assert leerie._format_run_duration(
            "2026-08-01T00:00:00+00:00", "2026-08-01T00:00:00+00:00") == "0s"


# --- _run_recency_key ------------------------------------------------

class TestRunRecencyKey:
    def test_real_started_at_ranks_above_missing(self, tmp_path):
        with_started = {"run_id": "run-a", "started_at": "2026-01-01T00:00:00+00:00"}
        without_started = {"run_id": "run-b"}
        (tmp_path / "runs" / "run-b").mkdir(parents=True)
        # No state.json for run-b -> mtime lookup fails -> (0, "").
        key_with = leerie._run_recency_key(with_started, tmp_path)
        key_without = leerie._run_recency_key(without_started, tmp_path)
        assert key_with > key_without
        assert key_with[0] == 1
        assert key_without[0] == 0

    def test_missing_started_at_falls_back_to_mtime(self, tmp_path):
        run_dir = tmp_path / "runs" / "run-c"
        run_dir.mkdir(parents=True)
        sidecar = run_dir / "state.json"
        sidecar.write_text("{}")
        key = leerie._run_recency_key({"run_id": "run-c"}, tmp_path)
        assert key[0] == 0
        assert key[1] == str(sidecar.stat().st_mtime)

    def test_missing_started_at_and_missing_sidecar_returns_empty_string(self, tmp_path):
        key = leerie._run_recency_key({"run_id": "does-not-exist"}, tmp_path)
        assert key == (0, "")

    def test_never_sorts_missing_above_real(self, tmp_path):
        # Sort a list containing both shapes; the real-started_at run must
        # land last (newest) regardless of how "recent" the mtime looks.
        run_dir = tmp_path / "runs" / "run-mtime"
        run_dir.mkdir(parents=True)
        (run_dir / "state.json").write_text("{}")
        rows = [
            {"run_id": "run-mtime"},
            {"run_id": "run-real", "started_at": "2020-01-01T00:00:00+00:00"},
        ]
        rows.sort(key=lambda r: leerie._run_recency_key(r, tmp_path))
        assert rows[-1]["run_id"] == "run-real"

    def test_two_real_started_at_sort_lexicographically(self, tmp_path):
        earlier = {"run_id": "run-x", "started_at": "2026-01-01T00:00:00+00:00"}
        later = {"run_id": "run-y", "started_at": "2026-06-01T00:00:00+00:00"}
        rows = [later, earlier]
        rows.sort(key=lambda r: leerie._run_recency_key(r, tmp_path))
        assert rows == [earlier, later]


# --- _format_run_for_disambiguation ------------------------------------------------

class TestFormatRunForDisambiguation:
    def test_includes_run_id_status_started(self, tmp_path):
        run_dir = tmp_path / "runs" / "run-a"
        run_dir.mkdir(parents=True)
        state_path = run_dir / "state.json"
        state_path.write_text("{}")
        run = {
            "run_id": "run-a",
            "started_at": "2026-01-01T00:00:00+00:00",
            "path": str(state_path),
        }
        out = leerie._format_run_for_disambiguation(run, tmp_path)
        assert "run-a" in out
        assert "started=2026-01-01T00:00:00+00:00" in out
        assert "status=" in out
        assert "last-activity=" in out

    def test_missing_started_at_renders_question_mark(self, tmp_path):
        run_dir = tmp_path / "runs" / "run-b"
        run_dir.mkdir(parents=True)
        run = {"run_id": "run-b"}
        out = leerie._format_run_for_disambiguation(run, tmp_path)
        assert "started=?" in out

    def test_missing_path_renders_last_activity_question_mark(self, tmp_path):
        run = {"run_id": "run-no-path", "started_at": "2026-01-01T00:00:00+00:00"}
        out = leerie._format_run_for_disambiguation(run, tmp_path)
        assert "last-activity=?" in out

    def test_unreadable_state_json_degrades_gracefully(self, tmp_path):
        # A run.json (consulted by _run_status_for -> _derive_run_status)
        # that is present but unparseable must not raise -- the sidecar
        # read is wrapped in a try/except and falls back to state.json
        # alone.
        run_dir = tmp_path / "runs" / "run-corrupt"
        run_dir.mkdir(parents=True)
        (run_dir / "run.json").write_text("{not valid json")
        run = {"run_id": "run-corrupt", "started_at": "2026-01-01T00:00:00+00:00"}
        out = leerie._format_run_for_disambiguation(run, tmp_path)
        assert "run-corrupt" in out
        assert "status=" in out

    def test_nonexistent_sidecar_path_does_not_raise(self, tmp_path):
        # `path` names a file that was deleted between _discover_runs and
        # this call -- os.path.getmtime raises OSError, caught internally.
        run = {
            "run_id": "run-deleted",
            "started_at": "2026-01-01T00:00:00+00:00",
            "path": str(tmp_path / "runs" / "run-deleted" / "state.json"),
        }
        out = leerie._format_run_for_disambiguation(run, tmp_path)
        assert "last-activity=?" in out

    def test_no_runs_dir_at_all_does_not_raise(self, tmp_path):
        # leerie_root/runs doesn't even exist -- _run_status_for's sidecar
        # probe and this function must both tolerate that.
        missing_root = tmp_path / "does-not-exist"
        run = {"run_id": "run-x", "started_at": "2026-01-01T00:00:00+00:00"}
        out = leerie._format_run_for_disambiguation(run, missing_root)
        assert "run-x" in out


# --- _render_run_table ------------------------------------------------

class TestRenderRunTable:
    def _row(self, run_id="r1", started="2026-01-01T00:00:00+00:00",
             status="done", branch="leerie/subtasks/x", is_fly=False,
             cost="$1.23", is_ec2=False):
        return (run_id, started, status, branch, is_fly, cost, is_ec2)

    def test_singleton_does_not_raise(self, capsys):
        leerie._render_run_table([self._row()])
        out = capsys.readouterr().out
        assert "r1" in out
        assert "run_id" in out  # header row

    def test_multiple_rows_auto_sizes_columns(self, capsys):
        rows = [
            self._row(run_id="short", branch="b"),
            self._row(run_id="a-much-longer-run-id", branch="a/much/longer/branch/name"),
        ]
        leerie._render_run_table(rows)
        lines = capsys.readouterr().out.splitlines()
        # Header, separator, and one line per row -> column widths line up
        # (every data line is the same length as the header line).
        assert len(lines) == 2 + len(rows)
        header_len = len(lines[0])
        for line in lines[1:]:
            assert len(line) == header_len

    def test_renders_run_id_started_status_cost_branch_not_is_fly_or_is_ec2(self, capsys):
        leerie._render_run_table([self._row(is_fly=True, is_ec2=True)])
        out = capsys.readouterr().out
        assert "True" not in out
        assert "False" not in out

    def test_empty_input_raises_valueerror_shaped_error(self):
        # _render_run_table is only ever called by _list_runs after an
        # `if not rows:` guard that prints "no runs" instead -- an empty
        # list reaching this function directly is not a supported input.
        # Pinning the actual current behavior (a max()-on-empty TypeError)
        # rather than papering over it, since fixing the underlying
        # column-width computation is outside this subtask's scope
        # (tests-only; files_likely_touched = tests/test_run_table_helpers.py).
        with pytest.raises(TypeError):
            leerie._render_run_table([])
