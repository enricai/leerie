#!/usr/bin/env python3
"""Derive the per-worker-type duration distribution from a leerie state root.

N25's work order was explicit that the per-worker timeout *values* must come
from the observed distribution in the run corpus' `calls.ndjson`, not from a
guess: "a timeout set below a legitimate p99 converts a slow worker into a
failed one. The measurement is the work; the plumbing is trivial."

This is that measurement, kept as a script rather than a one-off so the
numbers in `tests/fixtures/worker_duration/summary.json` can be regenerated
against a larger corpus later:

    python3 scripts/measure/worker_durations.py ~/.leerie \\
        > tests/fixtures/worker_duration/summary.json

The raw `calls.ndjson` files are deliberately NOT committed — they contain
full prompt and response text for every worker invocation. Only the derived
per-worker aggregate is.

Reads the same two fields `_aggregate_calls` does (`call_type`,
`latency_ms`), so a schema change to the telemetry writer surfaces here too.
"""
from __future__ import annotations

import collections
import glob
import json
import math
import os
import sys

# `_invoke` bounds one attempt with `asyncio.wait_for(timeout=...)`, so a
# latency at or above the cap is a CENSORED observation (the worker was
# killed) rather than a measured duration. Reported so a future corpus with
# meaningful censoring cannot be mistaken for a clean one.
WORKER_TIMEOUT_SEC = 5400


def percentile(values: list[float], p: float) -> float:
    ordered = sorted(values)
    k = (len(ordered) - 1) * p
    lo, hi = math.floor(k), math.ceil(k)
    if lo == hi:
        return ordered[lo]
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo)


def collect(state_root: str) -> dict[str, list[float]]:
    by_type: dict[str, list[float]] = collections.defaultdict(list)
    pattern = os.path.join(state_root, "*", "runs", "*", "calls.ndjson")
    for path in glob.glob(pattern):
        with open(path, errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except ValueError:
                    continue  # a torn final line from a SIGKILLed run
                call_type = record.get("call_type")
                latency = record.get("latency_ms")
                if call_type and isinstance(latency, (int, float)) and latency > 0:
                    by_type[call_type].append(latency / 1000.0)
    return by_type


def summarize(by_type: dict[str, list[float]]) -> dict:
    workers = {}
    total = censored_total = 0
    for call_type, values in by_type.items():
        censored = sum(1 for v in values if v >= WORKER_TIMEOUT_SEC)
        total += len(values)
        censored_total += censored
        workers[call_type] = {
            "n": len(values),
            "p50": round(percentile(values, 0.50), 1),
            "p95": round(percentile(values, 0.95), 1),
            "p99": round(percentile(values, 0.99), 1),
            "max": round(max(values), 1),
            "censored_at_cap": censored,
        }
    return {
        "_comment": (
            "Derived per-worker-type duration distribution (seconds) from a "
            "real leerie state root. Regenerate with "
            "scripts/measure/worker_durations.py. Raw calls.ndjson is not "
            "committed: it contains full prompt/response text."
        ),
        "worker_timeout_sec_at_measurement": WORKER_TIMEOUT_SEC,
        "total_calls": total,
        "censored_calls": censored_total,
        "workers": dict(sorted(workers.items(), key=lambda kv: -kv[1]["n"])),
    }


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(f"usage: {argv[0]} <state-root>", file=sys.stderr)
        return 2
    by_type = collect(argv[1])
    if not by_type:
        print(f"no calls.ndjson found under {argv[1]}", file=sys.stderr)
        return 1
    print(json.dumps(summarize(by_type), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
