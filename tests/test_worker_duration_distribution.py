"""N25 measurement-first deliverable: per-worker-type duration distribution.

This test computes p50/p95/p99 wall-clock latency per `call_type` from a
committed sample `calls.ndjson` fixture (tests/fixtures/worker_duration/
sample_calls.ndjson), shape-matched to the real field names `_aggregate_calls`
already reads (orchestrator/leerie.py:4324-4427: `call_type`, `latency_ms`,
`success`, `failure_kind`, `input_tokens`, `output_tokens`).

This subtask does NOT add a `WORKER_TIMEOUT_PER_WORKER` table or any new
`DEFAULT_CAPS` entry -- grepping the codebase confirms no such table exists
yet. It exists solely to deliver the measurement the work order's N25 item
requires before any timeout table is written ("picking timeout values
without it is guessing"). The 10-row-per-call_type fixture here is a small,
synthetic, shape-matched sample -- it is NOT a substitute for measuring the
real multi-hundred-run production calls.ndjson corpus. A p99 computed from
10 points is not a legitimate p99 (it degenerates to the max of the sample);
this fixture only proves the *computation* reproduces the ordering already
observed live (planner/implementer calls running materially longer than
fit_judge calls), not that its numeric percentile values are production-grade
inputs to a real timeout table.
"""
from __future__ import annotations

import json
import statistics
from pathlib import Path

FIXTURE = Path(__file__).parent / "fixtures" / "worker_duration" / "sample_calls.ndjson"


def _load_latencies_by_call_type(calls_path: Path) -> dict[str, list[int]]:
    """Parse a calls.ndjson file into {call_type: [latency_ms, ...]}.

    Reads the same fields `_aggregate_calls` (orchestrator/leerie.py:4324)
    reads -- `call_type` and `latency_ms` -- so this fixture and computation
    stay compatible with the real aggregation logic rather than drifting
    from it. Malformed lines are skipped, matching `_aggregate_calls`'s own
    tolerance.
    """
    out: dict[str, list[int]] = {}
    for line in calls_path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        ct = rec.get("call_type") or "(unknown)"
        latency = rec.get("latency_ms")
        if latency is None:
            continue
        out.setdefault(ct, []).append(int(latency))
    return out


def _percentiles(values: list[int]) -> dict[str, float]:
    """p50/p95/p99 via statistics.quantiles(n=100, method='inclusive')."""
    ordered = sorted(values)
    q = statistics.quantiles(ordered, n=100, method="inclusive")
    return {"p50": q[49], "p95": q[94], "p99": q[98]}


class TestAggregateCallsFieldCompatibility:
    """The fixture and this file's parser must read the fields the live
    `_aggregate_calls` reads, so both stay compatible with the same
    calls.ndjson shape rather than drifting into two parsers."""

    def test_fixture_parses_with_real_aggregate_calls(self, leerie):
        agg = leerie._aggregate_calls(FIXTURE)
        assert agg, "the fixture produced no aggregation via the real _aggregate_calls"
        assert "planner" in agg
        assert "fit_judge" in agg
        assert agg["planner"]["calls"] == 10
        assert agg["fit_judge"]["calls"] == 10

    def test_avg_latency_matches_between_the_two_parsers(self, leerie):
        agg = leerie._aggregate_calls(FIXTURE)
        by_type = _load_latencies_by_call_type(FIXTURE)
        for ct, values in by_type.items():
            avg_here = sum(values) / len(values)
            avg_real = agg[ct]["latency_ms_sum"] / agg[ct]["calls"]
            assert avg_here == avg_real, (
                f"{ct}: this file's parser and _aggregate_calls disagree on "
                f"average latency ({avg_here} vs {avg_real})"
            )


class TestPercentileComputation:
    def test_percentiles_computed_per_call_type(self):
        by_type = _load_latencies_by_call_type(FIXTURE)
        assert set(by_type) == {"planner", "fit_judge", "implementer", "classifier"}
        for ct, values in by_type.items():
            pct = _percentiles(values)
            assert pct["p50"] <= pct["p95"] <= pct["p99"]
            assert pct["p99"] <= max(values)
            assert pct["p50"] >= min(values)

    def test_planner_runs_materially_longer_than_fit_judge(self):
        """The work order's live observation: planner calls average
        materially longer than fit_judge calls. Reproduced here on the
        fixture at every percentile, not just the mean."""
        by_type = _load_latencies_by_call_type(FIXTURE)
        planner_pct = _percentiles(by_type["planner"])
        fit_judge_pct = _percentiles(by_type["fit_judge"])
        for key in ("p50", "p95", "p99"):
            assert planner_pct[key] > fit_judge_pct[key] * 5, (
                f"expected planner {key} to run materially longer than "
                f"fit_judge {key}; got planner={planner_pct[key]} "
                f"fit_judge={fit_judge_pct[key]}"
            )

    def test_implementer_is_the_slowest_worker_type_measured(self):
        """implementer (the acting worker that writes code) should have the
        highest p50/p95/p99 among the four measured call_types here,
        matching the intuition that acting workers run longer than
        judgment workers."""
        by_type = _load_latencies_by_call_type(FIXTURE)
        all_pcts = {ct: _percentiles(vals) for ct, vals in by_type.items()}
        for key in ("p50", "p95", "p99"):
            assert all_pcts["implementer"][key] == max(
                p[key] for p in all_pcts.values()
            ), f"implementer is not the slowest call_type at {key}"


class TestNoTimeoutTableIntroduced:
    """This subtask is measurement-only. No WORKER_TIMEOUT_PER_WORKER-shaped
    table or new DEFAULT_CAPS entry should exist as a result of this work."""

    def test_no_worker_timeout_table_exists(self, leerie):
        assert not hasattr(leerie, "WORKER_TIMEOUT_PER_WORKER"), (
            "WORKER_TIMEOUT_PER_WORKER was introduced by this measurement-only "
            "subtask; N25's deliverable here is strictly the distribution "
            "measurement, not the timeout table itself"
        )

    def test_default_caps_carries_no_worker_duration_percentile_keys(self, leerie):
        caps = leerie.DEFAULT_CAPS
        for k in caps:
            assert "timeout_per_worker" not in k.lower(), (
                f"DEFAULT_CAPS[{k!r}] looks like a per-worker timeout table; "
                "this subtask must not add one"
            )
