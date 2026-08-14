"""Input-token accounting must include the cached input.

The API reports input across THREE fields: `input_tokens` is only the uncached
remainder, with `cache_creation_input_tokens` and `cache_read_input_tokens`
carrying the rest. leerie read the first alone, which produced run weights like
`24,281 in / 194,508 out` for 53 agentic workers — about 460 input tokens per
call, which no worker that reads a repository can possibly use.

`cost_usd` was unaffected, since it comes from the CLI's own `total_cost_usd`.
That is why this went unnoticed for so long: the number operators watch was
right while the tokens printed beside it were wrong by orders of magnitude.

See docs/POSTMORTEM-2026-08-14.md, F21.
"""
from __future__ import annotations

import inspect

import pytest


def test_all_three_input_fields_are_counted(leerie):
    usage = {
        "input_tokens": 460,
        "cache_creation_input_tokens": 12_000,
        "cache_read_input_tokens": 180_000,
        "output_tokens": 3_600,
    }
    assert leerie._usage_input_tokens(usage) == 192_460


def test_the_uncached_field_alone_is_not_the_answer(leerie):
    """Anti-vacuity: pins that the fix is the SUM, not a rename."""
    usage = {"input_tokens": 460, "cache_read_input_tokens": 180_000}
    assert leerie._usage_input_tokens(usage) != 460


@pytest.mark.parametrize("usage", [{}, None, [], "x", {"output_tokens": 5}])
def test_degenerate_usage_is_zero_not_a_crash(leerie, usage):
    """Telemetry must never be able to kill a run."""
    assert leerie._usage_input_tokens(usage) == 0


def test_realistic_call_has_input_exceeding_output(leerie):
    """The sanity property the old numbers violated.

    An agentic worker reads far more than it writes. A run whose reported input
    is a fraction of its output is not a surprising run — it is a broken
    measurement.
    """
    usage = {"input_tokens": 400, "cache_read_input_tokens": 150_000,
             "output_tokens": 4_000}
    assert leerie._usage_input_tokens(usage) > usage["output_tokens"]


def test_accumulator_uses_the_helper(leerie):
    src = inspect.getsource(leerie._accumulate_telemetry)
    assert "_usage_input_tokens(usage)" in src
    assert 'usage.get("input_tokens")' not in src, (
        "the accumulator must not read the uncached field directly")


def test_per_call_capture_uses_the_helper(leerie):
    """The per-call record and the run total must agree.

    `leerie report` breaks the run-weight line down per worker type; if the two
    read different fields the breakdown contradicts the total it explains.
    """
    src = inspect.getsource(leerie.claude_p)
    assert "_usage_input_tokens(_usage)" in src
    assert '"input_tokens": int(_usage.get("input_tokens") or 0)' not in src


def test_accumulated_totals_agree_with_per_call_records(leerie):
    """End to end over the two code paths, on one envelope."""
    envelope = {
        "total_cost_usd": 1.5,
        "usage": {"input_tokens": 100, "cache_creation_input_tokens": 200,
                  "cache_read_input_tokens": 300, "output_tokens": 50},
    }
    data: dict = {}
    leerie._accumulate_telemetry(data, envelope)
    leerie._accumulate_telemetry(data, envelope)
    tel = data["telemetry"]
    assert tel["calls"] == 2
    assert tel["input_tokens"] == 2 * leerie._usage_input_tokens(
        envelope["usage"])
    assert tel["output_tokens"] == 100
    assert tel["cost_usd"] == pytest.approx(3.0)
