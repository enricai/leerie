"""Tests for the telemetry surfacing + enrichment surface:

  - `_classify_failure_kind` taxonomy (Part 4): every branch, incl. the
    real-world is_error/parsed_ok anomaly and the {401,429,529} split.
  - `--list` cost column (Part 3): rendered from state.json telemetry,
    "—" for runs with no telemetry (orphans), right-aligned.
  - `compose_pr_body` cost line (Part 2): present when telemetry exists,
    omitted otherwise.
  - `report_run` (Part 5): per-call_type aggregation + memory peak,
    reconciling against state.json.telemetry; run-selection reuse.

Mirrors the module-load-via-conftest `leerie` fixture pattern.
"""
from __future__ import annotations

import json
from pathlib import Path


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _make_run(root: Path, run_id: str, state: dict,
              calls: list[dict] | None = None,
              memory: list[dict] | None = None) -> Path:
    """Create runs/<run_id>/ with state.json (+ optional calls/memory)."""
    run_dir = root / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "state.json").write_text(json.dumps(state))
    if calls is not None:
        (run_dir / "calls.ndjson").write_text(
            "".join(json.dumps(c) + "\n" for c in calls))
    if memory is not None:
        (run_dir / "memory.ndjson").write_text(
            "".join(json.dumps(m) + "\n" for m in memory))
    return run_dir


# ---------------------------------------------------------------------------
# Part 4 — _classify_failure_kind
# ---------------------------------------------------------------------------

def test_failure_kind_success_is_none(leerie):
    assert leerie._classify_failure_kind(
        {"is_error": False}, parsed_ok=True) is None


def test_failure_kind_schema_parse_failed(leerie):
    # The dominant real-world failure: worker returned, output didn't validate.
    assert leerie._classify_failure_kind(
        {"is_error": False}, parsed_ok=False) == "schema_parse_failed"


def test_failure_kind_api_error_split(leerie):
    assert leerie._classify_failure_kind(
        {"is_error": True, "api_error_status": 401}, False) == "api_error:auth"
    assert leerie._classify_failure_kind(
        {"is_error": True, "api_error_status": 429}, False) == "api_error:quota"
    assert leerie._classify_failure_kind(
        {"is_error": True, "api_error_status": 529}, False) \
        == "api_error:overload"


def test_failure_kind_api_error_bare(leerie):
    # is_error with a non-{401,429,529} numeric status stays bare "api_error".
    assert leerie._classify_failure_kind(
        {"is_error": True, "api_error_status": 500}, False) == "api_error"
    assert leerie._classify_failure_kind(
        {"is_error": True, "api_error_status": None}, False) == "api_error"


def test_failure_kind_is_error_wins_over_parsed_ok(leerie):
    # The real captured anomaly: is_error=True but structured_output present
    # (parsed_ok=True). is_error dominates → api_error, never None.
    assert leerie._classify_failure_kind(
        {"is_error": True}, parsed_ok=True) == "api_error"


def test_failure_kind_incomplete(leerie):
    # terminal_reason != completed, no is_error → incomplete (max-turns cutoff).
    assert leerie._classify_failure_kind(
        {"is_error": False, "terminal_reason": "max_turns"}, False) \
        == "incomplete"
    # completed terminal_reason with bad parse falls through to schema.
    assert leerie._classify_failure_kind(
        {"is_error": False, "terminal_reason": "completed"}, False) \
        == "schema_parse_failed"


# ---------------------------------------------------------------------------
# Part 3 — --list cost column
# ---------------------------------------------------------------------------

def test_list_runs_cost_column_header(leerie, tmp_path, capsys):
    _make_run(tmp_path, "feat-a-aaaaaa", {
        "started_at": "2026-05-26T10:00:00+00:00", "task": "x",
        "telemetry": {"calls": 3, "cost_usd": 12.5,
                      "input_tokens": 10, "output_tokens": 20}})
    leerie.list_runs(tmp_path)
    out = capsys.readouterr().out
    for col in ("run_id", "started_at", "status", "cost", "branch"):
        assert col in out


def test_list_runs_cost_value_rendered(leerie, tmp_path, capsys):
    _make_run(tmp_path, "feat-b-bbbbbb", {
        "started_at": "2026-05-26T10:00:00+00:00", "task": "x",
        "telemetry": {"calls": 3, "cost_usd": 107.25,
                      "input_tokens": 10, "output_tokens": 20}})
    leerie.list_runs(tmp_path)
    out = capsys.readouterr().out
    assert "$107.25" in out


def test_list_runs_no_telemetry_renders_dash(leerie, tmp_path, capsys):
    # A run with no telemetry block (e.g. pre-classify) shows "—", not $0.00.
    _make_run(tmp_path, "feat-c-cccccc", {
        "started_at": "2026-05-26T10:00:00+00:00", "task": "x"})
    leerie.list_runs(tmp_path)
    out = capsys.readouterr().out
    assert "—" in out
    assert "$" not in out


# ---------------------------------------------------------------------------
# Part 2 — compose_pr_body cost line
# ---------------------------------------------------------------------------

def test_pr_body_cost_line_present(leerie):
    body = leerie.compose_pr_body({
        "task": "t", "waves": [["a"]], "worker_count": 1,
        "telemetry": {"calls": 5, "cost_usd": 3.5,
                      "input_tokens": 100, "output_tokens": 200}},
        "run-xyz")
    assert "- Cost: $3.50" in body
    assert "5 calls" in body
    assert "100 in / 200 out tokens" in body


def test_pr_body_cost_line_absent_without_telemetry(leerie):
    body = leerie.compose_pr_body({
        "task": "t", "waves": [["a"]], "worker_count": 1}, "run-xyz")
    assert "- Cost:" not in body
    # And the body still renders the other run-summary fields.
    assert "- Workers:" in body


# ---------------------------------------------------------------------------
# Part 5 — report_run
# ---------------------------------------------------------------------------

_CALLS = [
    {"call_type": "implementer", "input_tokens": 100, "output_tokens": 200,
     "latency_ms": 1000, "success": True, "failure_kind": None},
    {"call_type": "implementer", "input_tokens": 50, "output_tokens": 80,
     "latency_ms": 3000, "success": False,
     "failure_kind": "schema_parse_failed"},
    {"call_type": "planner", "input_tokens": 300, "output_tokens": 400,
     "latency_ms": 2000, "success": True, "failure_kind": None},
]
_MEMORY = [
    {"ts": "t0", "rss_kb": 40000, "open_fds": 10, "thread_count": 1},
    {"ts": "t1", "rss_kb": 46000, "open_fds": 33, "thread_count": 2},
]
_STATE = {
    "started_at": "2026-06-30T03:35:40+00:00",
    "finished_at": "2026-06-30T05:35:40+00:00",
    "telemetry": {"calls": 3, "cost_usd": 42.0,
                  "input_tokens": 450, "output_tokens": 680},
}


def test_report_run_header_and_weight(leerie, tmp_path, capsys):
    _make_run(tmp_path, "rrr111", _STATE, calls=_CALLS, memory=_MEMORY)
    leerie.report_run(tmp_path, "rrr111")
    out = capsys.readouterr().out
    assert "rrr111" in out
    assert "$42.00" in out
    assert "450 in / 680 out tokens" in out
    assert "duration: 2h" in out


def test_report_run_per_call_type_breakdown(leerie, tmp_path, capsys):
    _make_run(tmp_path, "rrr222", _STATE, calls=_CALLS, memory=_MEMORY)
    leerie.report_run(tmp_path, "rrr222")
    out = capsys.readouterr().out
    # implementer: 2 calls, 150 in, 280 out, 1 failure; planner: 1 call.
    assert "implementer" in out and "planner" in out
    lines = [l for l in out.splitlines() if l.strip().startswith("implementer")]
    assert lines, "implementer row missing"
    # Sum reconciles: 2 impl + 1 planner = 3 = telemetry.calls.
    assert "failures by kind" in out
    assert "schema_parse_failed" in out


def test_report_run_memory_peak(leerie, tmp_path, capsys):
    _make_run(tmp_path, "rrr333", _STATE, calls=_CALLS, memory=_MEMORY)
    leerie.report_run(tmp_path, "rrr333")
    out = capsys.readouterr().out
    assert "46,000 KiB" in out
    assert "33 fds" in out


def test_report_run_missing_calls_is_graceful(leerie, tmp_path, capsys):
    _make_run(tmp_path, "rrr444", _STATE)  # no calls.ndjson / memory.ndjson
    leerie.report_run(tmp_path, "rrr444")
    out = capsys.readouterr().out
    assert "no calls.ndjson found" in out


def test_aggregate_calls_skips_malformed(leerie, tmp_path):
    p = tmp_path / "calls.ndjson"
    p.write_text('{"call_type":"x","input_tokens":5,"success":true}\n'
                 'not json\n'
                 '{"call_type":"x","input_tokens":7,"success":false,'
                 '"failure_kind":"api_error"}\n')
    agg = leerie._aggregate_calls(p)
    assert agg["x"]["calls"] == 2
    assert agg["x"]["input_tokens"] == 12
    assert agg["x"]["failures"] == 1
    assert agg["x"]["failure_kinds"] == {"api_error": 1}


def test_memory_peak_empty_returns_none(leerie, tmp_path):
    p = tmp_path / "memory.ndjson"
    p.write_text("")
    assert leerie._memory_peak(p) is None
    assert leerie._memory_peak(tmp_path / "absent.ndjson") is None
