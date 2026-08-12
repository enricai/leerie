"""N25: the per-worker-type duration distribution, and the table derived from it.

The work order made this measurement-first: "the *values* must come from the
observed per-worker-type duration distribution in the run corpus'
`calls.ndjson`. A timeout below a legitimate p99 turns a slow worker into a
failed one." Until that distribution existed, no table was allowed to ship.

It exists now. `tests/fixtures/worker_duration/summary.json` is the derived
aggregate of a real state root -- 15,951 calls across 21 worker types from
153 runs -- produced by `scripts/measure/worker_durations.py`. The raw
`calls.ndjson` is deliberately not committed: it carries full prompt and
response text for every invocation.

The small `sample_calls.ndjson` fixture is retained for ONE purpose: proving
this file's parser reads the same fields as the live `_aggregate_calls`, so
the two cannot drift. It is NOT evidence about production timings, and the
two ordering assertions that used to be here ("planner is slower than
fit_judge", "implementer is slowest") were removed: with 10 hand-written
rows per type they verified the fixture author's own ordering, which the
real corpus now supplies for free.

**Why the p99s can be trusted.** `_invoke` bounds one attempt with
`wait_for(timeout=...)`, so a latency at the cap is a killed worker rather
than a measured duration -- a right-censored observation. Exactly 1 of
15,951 calls sits at or above the 5400 s cap (0.006%), so the distribution
is effectively uncensored and its tail is real.
"""
from __future__ import annotations

import inspect
import json
import math
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


SUMMARY = Path(__file__).parent / "fixtures" / "worker_duration" / "summary.json"


def _summary() -> dict:
    """Load the committed measurement.

    Explicit existence check rather than letting `read_text` raise: this
    fixture is the one artifact of the N25 measurement, and if it is ever
    missing the cause is almost certainly that it was left untracked while
    the tests that consume it were committed — a failure that passes locally
    and only appears in CI, where a fresh clone has tracked files only. A
    bare `FileNotFoundError` names the path but not that cause.

    Deliberately not a `pytest.skip`: skipping would make the whole
    derivation guard silently vacuous, which is exactly the failure mode
    this file exists to prevent.
    """
    assert SUMMARY.exists(), (
        f"{SUMMARY} is missing. It is a committed fixture — regenerate with "
        "`python3 scripts/measure/worker_durations.py <state-root> > "
        f"{SUMMARY}` and make sure it is `git add`ed; if this only fails in "
        "CI, it was left untracked.")
    return json.loads(SUMMARY.read_text())


class TestMeasuredCorpusSummary:
    """The committed measurement itself. These are the numbers the table
    below is derived from, so a regenerated summary that changes them is a
    visible diff rather than a silent re-derivation."""

    def test_summary_is_a_real_corpus_not_a_toy(self):
        data = _summary()
        assert data["total_calls"] > 10_000, (
            "the committed summary is too small to support a p99 -- "
            "regenerate it against a real state root")
        assert len(data["workers"]) >= 15

    def test_censoring_is_negligible(self):
        """The load-bearing precondition. If a meaningful share of calls sat
        at the cap, the p99s would be lower bounds on a truncated
        distribution and deriving timeouts from them would be circular."""
        data = _summary()
        ratio = data["censored_calls"] / data["total_calls"]
        assert ratio < 0.01, (
            f"{ratio:.2%} of calls are censored at the "
            f"{data['worker_timeout_sec_at_measurement']}s cap -- the p99s "
            "are truncated and must not be used to derive timeouts")

    def test_percentiles_are_ordered_for_every_worker(self):
        for worker, row in _summary()["workers"].items():
            assert row["p50"] <= row["p95"] <= row["p99"] <= row["max"], (
                f"{worker}: percentiles out of order in the committed summary")


class TestTimeoutTableIsDerivedFromTheMeasurement:
    """Every entry must be reproducible from the committed distribution.

    This is what stops the table from drifting back into invented numbers:
    the rule (`ceil(p99 * 3)`, clamped) is executed against the measured
    p99s, and each shipped value must match.
    """

    def test_every_entry_matches_ceil_p99_times_three(self, leerie):
        workers = _summary()["workers"]
        cap = leerie.DEFAULT_CAPS["worker_timeout_sec"]
        floor = leerie._WORKER_TIMEOUT_FLOOR_SEC
        mismatches = []
        for worker, shipped in leerie.TIMEOUT_DEFAULT_PER_WORKER.items():
            assert worker in workers, (
                f"{worker} has a timeout entry but no measured distribution")
            row = workers[worker]
            expected = min(cap, max(floor,
                                    math.ceil(row["p99"] * 3),
                                    math.ceil(row["max"] * 1.2)))
            if shipped != expected:
                mismatches.append(
                    f"{worker}: shipped {shipped}s, rule gives {expected}s "
                    f"(p99 {workers[worker]['p99']}s)")
        assert not mismatches, (
            "timeout entries no longer follow the derivation rule "
            "min(cap, max(floor, ceil(p99*3), ceil(max*1.2))):\n  "
            + "\n  ".join(mismatches))

    def test_no_entry_is_below_its_measured_MAX(self, leerie):
        """The work order's explicit failure mode, in its sharpest form.

        Asserting against the p99 is too weak: `planner`'s p99*3 is 5,091s
        while its observed maximum is 5,247.6s, so a p99-only rule produces
        a ceiling that would have killed a run present in the very corpus it
        was derived from. Every shipped ceiling must clear the slowest call
        actually observed for that worker.
        """
        workers = _summary()["workers"]
        for worker, shipped in leerie.TIMEOUT_DEFAULT_PER_WORKER.items():
            assert shipped > workers[worker]["max"], (
                f"{worker}'s {shipped}s ceiling is at or below its slowest "
                f"observed call ({workers[worker]['max']}s) -- that call "
                "would now be killed instead of completing")

    def test_planner_is_excluded_because_of_the_max_guard(self, leerie):
        """Regression pin for the rule's sharpest edge.

        planner is the worker where p99*3 and the observed max disagree; if
        the max term is ever dropped, planner reappears in the table at
        5,091s and silently starts killing its own slowest runs.
        """
        workers = _summary()["workers"]
        p99_only = math.ceil(workers["planner"]["p99"] * 3)
        assert p99_only < workers["planner"]["max"], (
            "planner's p99*3 no longer sits under its observed max -- "
            "re-derive whether the max guard is still load-bearing")
        assert "planner" not in leerie.TIMEOUT_DEFAULT_PER_WORKER

    def test_slowest_workers_are_absent_and_keep_the_global_cap(self, leerie):
        """conformer/implementer/planner exceed the cap after the x3
        multiplier, so they are omitted rather than listed at 5400 -- an
        explicit entry equal to the default is noise that drifts."""
        for worker in ("conformer", "implementer", "planner"):
            assert worker not in leerie.TIMEOUT_DEFAULT_PER_WORKER


class TestResolveWorkerTimeout:
    def test_table_entry_wins_for_a_listed_worker(self, leerie):
        caps = dict(leerie.DEFAULT_CAPS)
        assert leerie.resolve_worker_timeout("classifier", caps) == 1236

    def test_unlisted_worker_falls_through_to_the_global_cap(self, leerie):
        caps = dict(leerie.DEFAULT_CAPS)
        assert (leerie.resolve_worker_timeout("implementer", caps)
                == caps["worker_timeout_sec"])
        assert (leerie.resolve_worker_timeout("a_worker_invented_tomorrow", caps)
                == caps["worker_timeout_sec"])

    def test_an_explicit_global_bypasses_the_table_in_both_directions(self, leerie):
        """The escape hatch. An explicitly-set global wins outright.

        Load-bearing that it wins rather than clamps: the table is derived
        from ONE host's corpus, so the operator reaching for the flag is
        precisely the one whose worker is being killed at a ceiling derived
        on a faster machine. `min(table, global)` would leave them no way up
        -- raising the global would change nothing for any listed worker.
        """
        raised = dict(leerie.DEFAULT_CAPS, worker_timeout_sec=9000)
        assert leerie.resolve_worker_timeout("classifier", raised) == 9000, (
            "raising the global did not lift a table-listed worker -- the "
            "escape hatch cannot reach the case that needs it")
        assert leerie.resolve_worker_timeout("implementer", raised) == 9000

        lowered = dict(leerie.DEFAULT_CAPS, worker_timeout_sec=300)
        assert leerie.resolve_worker_timeout("classifier", lowered) == 300
        assert leerie.resolve_worker_timeout("implementer", lowered) == 300

    def test_the_table_applies_when_no_explicit_global_is_set(self, leerie):
        """Anti-vacuity partner: with the default global untouched the table
        must still lower the ceiling, or the bypass above has silently
        disabled the whole feature."""
        caps = dict(leerie.DEFAULT_CAPS)
        assert leerie.resolve_worker_timeout("classifier", caps) == 1236
        assert (leerie.resolve_worker_timeout("implementer", caps)
                == leerie.DEFAULT_CAPS["worker_timeout_sec"])

    def test_missing_cap_key_falls_back_to_the_default(self, leerie):
        assert (leerie.resolve_worker_timeout("implementer", {})
                == leerie.DEFAULT_CAPS["worker_timeout_sec"])

    def test_global_override_chain_exists_and_reaches_caps(self, leerie):
        """`args.worker_timeout` must be READ, not merely parsed.

        `tests/test_no_dead_resolutions.py` exists because this repo shipped
        `args.X = resolve_Y(...)` assignments nothing consumed. Here the
        failure mode is the mirror image: a flag parsed into `args` that
        never reaches `caps`, leaving the documented escape hatch inert.
        """
        import inspect
        src = inspect.getsource(leerie.main)
        assert 'caps["worker_timeout_sec"] = resolve_worker_timeout_sec(' in src, (
            "the --worker-timeout resolution never reaches caps, so the flag "
            "is documented but inert")
        assert "args.worker_timeout" in src

    def test_resolver_precedence_cli_over_env_over_file(self, leerie, tmp_path,
                                                        monkeypatch):
        monkeypatch.setenv(leerie.WORKER_TIMEOUT_ENV, "1200")
        (tmp_path / leerie.WORKER_TIMEOUT_FILE).write_text(
            "worker_timeout_sec = 1500\n")
        assert leerie.resolve_worker_timeout_sec(tmp_path, 900) == 900
        assert leerie.resolve_worker_timeout_sec(tmp_path, None) == 1200
        monkeypatch.delenv(leerie.WORKER_TIMEOUT_ENV)
        assert leerie.resolve_worker_timeout_sec(tmp_path, None) == 1500

    def test_resolver_falls_back_to_the_default(self, leerie, tmp_path):
        assert (leerie.resolve_worker_timeout_sec(tmp_path, None)
                == leerie.DEFAULT_CAPS["worker_timeout_sec"])

    def test_claude_p_uses_the_resolver_not_the_raw_cap(self, leerie):
        """Wiring pin: the table is inert unless the spawn path consults it."""
        src = inspect.getsource(leerie.claude_p)
        assert "resolve_worker_timeout(schema_key, caps)" in src
        assert 'timeout = caps["worker_timeout_sec"]' not in src
