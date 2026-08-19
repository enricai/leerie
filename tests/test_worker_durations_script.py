"""Tests for scripts/measure/worker_durations.py.

The script has its own set of small pure functions (`percentile`, `collect`,
`summarize`, `main`) with zero direct coverage elsewhere --
tests/test_worker_duration_distribution.py exercises orchestrator/leerie.py's
`_aggregate_calls` against a committed fixture via an independently
reimplemented parser, and never imports this script. This file imports the
script directly and exercises each of its four functions.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_SCRIPT_PATH = (Path(__file__).resolve().parent.parent
                / "scripts" / "measure" / "worker_durations.py")


@pytest.fixture
def wd():
    spec = importlib.util.spec_from_file_location("worker_durations", _SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestPercentile:
    def test_single_value(self, wd):
        assert wd.percentile([5.0], 0.5) == 5.0
        assert wd.percentile([5.0], 0.99) == 5.0

    def test_exact_index_lo_equals_hi(self, wd):
        # 5 values, p50 -> k = 4*0.5 = 2.0, lo == hi == 2 (0-indexed, sorted[2])
        values = [1.0, 2.0, 3.0, 4.0, 5.0]
        assert wd.percentile(values, 0.5) == 3.0

    def test_interpolation_between_two_values(self, wd):
        # 2 values, p50 -> k = 1*0.5 = 0.5, lo=0, hi=1 -> interpolate
        values = [10.0, 20.0]
        assert wd.percentile(values, 0.5) == pytest.approx(15.0)

    def test_unsorted_input_is_sorted_first(self, wd):
        values = [5.0, 1.0, 3.0, 2.0, 4.0]
        assert wd.percentile(values, 0.5) == 3.0

    def test_p99_of_known_dataset(self, wd):
        values = list(range(1, 101))  # 1..100
        # k = 99 * 0.99 = 98.01, lo=98, hi=99 -> ordered[98]=99, ordered[99]=100
        result = wd.percentile([float(v) for v in values], 0.99)
        assert result == pytest.approx(99.0 + (100.0 - 99.0) * 0.01)


def _write_calls(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        for r in records:
            f.write(r if isinstance(r, str) else json.dumps(r))
            f.write("\n")


class TestCollect:
    def test_glob_pattern_finds_nested_calls_ndjson(self, wd, tmp_path):
        state_root = tmp_path / "state"
        p1 = state_root / "repoA" / "runs" / "run1" / "calls.ndjson"
        p2 = state_root / "repoB" / "runs" / "run2" / "calls.ndjson"
        _write_calls(p1, [{"call_type": "implementer", "latency_ms": 1000}])
        _write_calls(p2, [{"call_type": "implementer", "latency_ms": 2000}])

        by_type = wd.collect(str(state_root))
        assert by_type["implementer"] == pytest.approx([1.0, 2.0], abs=1e-6) or \
            sorted(by_type["implementer"]) == pytest.approx([1.0, 2.0], abs=1e-6)

    def test_glob_ignores_files_outside_expected_shape(self, wd, tmp_path):
        state_root = tmp_path / "state"
        # Not under */runs/*/calls.ndjson
        stray = state_root / "calls.ndjson"
        _write_calls(stray, [{"call_type": "implementer", "latency_ms": 1000}])
        by_type = wd.collect(str(state_root))
        assert by_type == {}

    def test_tolerates_malformed_json_line(self, wd, tmp_path):
        state_root = tmp_path / "state"
        p = state_root / "repo" / "runs" / "run1" / "calls.ndjson"
        _write_calls(p, [
            {"call_type": "conformer", "latency_ms": 500},
            '{"call_type": "conformer", "latency_ms": 900',  # torn / truncated
            "",  # blank line
            {"call_type": "conformer", "latency_ms": 1500},
        ])
        by_type = wd.collect(str(state_root))
        assert sorted(by_type["conformer"]) == pytest.approx([0.5, 1.5])

    def test_filters_non_positive_and_non_numeric_latency(self, wd, tmp_path):
        state_root = tmp_path / "state"
        p = state_root / "repo" / "runs" / "run1" / "calls.ndjson"
        _write_calls(p, [
            {"call_type": "planner", "latency_ms": 0},
            {"call_type": "planner", "latency_ms": -100},
            {"call_type": "planner", "latency_ms": "not-a-number"},
            {"call_type": "planner", "latency_ms": None},
            {"call_type": "planner", "latency_ms": 3000},
            {"call_type": None, "latency_ms": 3000},  # no call_type
            {"latency_ms": 3000},  # missing call_type entirely
        ])
        by_type = wd.collect(str(state_root))
        assert list(by_type.keys()) == ["planner"]
        assert by_type["planner"] == pytest.approx([3.0])


class TestSummarize:
    def test_fields_present_and_correct_shape(self, wd):
        by_type = {"implementer": [float(v) for v in range(1, 101)]}
        result = wd.summarize(by_type)
        entry = result["workers"]["implementer"]
        assert entry["n"] == 100
        assert entry["p50"] == pytest.approx(wd.percentile(by_type["implementer"], 0.50), abs=0.1)
        assert entry["p95"] == pytest.approx(wd.percentile(by_type["implementer"], 0.95), abs=0.1)
        assert entry["p99"] == pytest.approx(wd.percentile(by_type["implementer"], 0.99), abs=0.1)
        assert entry["max"] == 100.0
        assert entry["censored_at_cap"] == 0
        assert result["total_calls"] == 100
        assert result["censored_calls"] == 0
        assert result["worker_timeout_sec_at_measurement"] == wd.WORKER_TIMEOUT_SEC

    def test_censored_count_against_worker_timeout_sec(self, wd):
        cap = wd.WORKER_TIMEOUT_SEC
        by_type = {
            "conformer": [1.0, 2.0, float(cap), float(cap) + 500.0],
        }
        result = wd.summarize(by_type)
        entry = result["workers"]["conformer"]
        assert entry["censored_at_cap"] == 2
        assert result["censored_calls"] == 2
        assert result["total_calls"] == 4

    def test_descending_by_n_sort_order(self, wd):
        by_type = {
            "small": [1.0, 2.0],
            "large": [1.0, 2.0, 3.0, 4.0, 5.0],
            "medium": [1.0, 2.0, 3.0],
        }
        result = wd.summarize(by_type)
        assert list(result["workers"].keys()) == ["large", "medium", "small"]


class TestMain:
    def test_wrong_argv_count_returns_2(self, wd, capsys):
        rc = wd.main(["prog"])
        assert rc == 2
        captured = capsys.readouterr()
        assert "usage" in captured.err

        rc = wd.main(["prog", "a", "b"])
        assert rc == 2

    def test_empty_state_root_returns_1(self, wd, tmp_path, capsys):
        rc = wd.main(["prog", str(tmp_path / "empty")])
        assert rc == 1
        captured = capsys.readouterr()
        assert "no calls.ndjson found" in captured.err

    def test_populated_state_root_returns_0_and_prints_json(self, wd, tmp_path, capsys):
        state_root = tmp_path / "state"
        p = state_root / "repo" / "runs" / "run1" / "calls.ndjson"
        _write_calls(p, [
            {"call_type": "implementer", "latency_ms": 1000},
            {"call_type": "implementer", "latency_ms": 2000},
        ])
        rc = wd.main(["prog", str(state_root)])
        assert rc == 0
        captured = capsys.readouterr()
        payload = json.loads(captured.out)
        assert payload["total_calls"] == 2
        assert "implementer" in payload["workers"]
