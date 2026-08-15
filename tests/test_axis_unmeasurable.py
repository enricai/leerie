""""Could not measure" is a third state, distinct from RED, and it is not clean.

An exit code cannot distinguish "the command ran and failed" from "the command
never produced a verdict". Both causes of the latter are authored by leerie's own
environment rather than by the diff — the runner is absent from this tree, or the
container's limits killed the command — so both belong to one predicate,
`_axis_unmeasurable`.

Before this, only `_runner_missing` was consulted on BLT output, so a
resource-killed command was recorded RED and the judgment was left to the
workers: measured across a run corpus, **37 worker calls** reasoned about `OS
can't spawn worker thread: Resource temporarily unavailable (os error 11)` and
each decided for itself that it was environmental. DESIGN §12 puts that in code.

Separately, `_conformance_clean` never read `measured` at all, so an unmeasured
axis could be reported clean. One run shipped a PR whose entire test axis never
executed — its command did not exist on that tree — because `tests` was also in
`red_axes` from the baseline and the exclusion swallowed a second, different
unmeasurability.

See docs/POSTMORTEM-2026-08-14.md, F5 and F6.
"""
from __future__ import annotations

import inspect
import tokenize
import textwrap
import io
import ast

import pytest
from tests.source_strip import code_only as _code_only   # single owner; see that module


# The signatures measured in real runs, and what each must classify as.
# The negative half is as load-bearing as the positive: reading a genuine OOM as
# "unmeasurable" would hide exactly the failures the baseline exists to surface
# (docs/POSTMORTEM-2026-08-14.md, retraction R3 — the run whose base was red for
# a missing test database and a real heap OOM, both genuine).
_UNMEASURABLE = [
    ("thread 'main' panicked: OS can't spawn worker thread: Resource "
     "temporarily unavailable (os error 11)"),
    "bash: line 1: pytest: command not found",
    "./scripts/leerie-test-db.sh: No such file or directory",
    "fork: retry: Resource temporarily unavailable",
]

_GENUINELY_RED = [
    "[vitest-pool]: Worker forks emitted error",
    "Error: Worker exited unexpectedly",
    "Next.js build worker exited with code: null and signal: SIGABRT",
    "FATAL ERROR: Ineffective mark-compacts near heap limit",
    "Tests  2 failed | 10132 passed (10147)",
]


@pytest.mark.parametrize("text", _UNMEASURABLE)
def test_no_verdict_is_unmeasurable(leerie, text):
    assert leerie._axis_unmeasurable(text) is True, text


@pytest.mark.parametrize("text", _GENUINELY_RED)
def test_a_real_failure_stays_red(leerie, text):
    assert leerie._axis_unmeasurable(text) is False, (
        "this is a genuine failure and must stay RED — classifying it as "
        f"unmeasurable would hide it: {text!r}")


def test_the_predicate_unions_both_causes(leerie):
    """Anti-vacuity: it must be both, not one renamed.

    `_runner_missing` alone was the pre-fix behaviour, and it misses the
    resource case; `_is_fork_exhaustion` alone misses the absent runner.
    """
    resource = "OS can't spawn worker thread: Resource temporarily unavailable"
    absent = "bash: pytest: command not found"
    assert leerie._is_fork_exhaustion(resource) and not leerie._runner_missing(resource)
    assert leerie._runner_missing(absent) and not leerie._is_fork_exhaustion(absent)
    assert leerie._axis_unmeasurable(resource) and leerie._axis_unmeasurable(absent)


def test_measure_blt_classifies_on_full_output_not_the_display_summary(leerie):
    """The summary is truncated to 400 chars; classification must not be.

    A resource kill can be reported well before the end of a long build log —
    the build prints its own epilogue afterwards — so classifying on the
    truncated summary would miss it exactly when the output is long.
    """
    src = _code_only(inspect.getsource(leerie._measure_blt))
    assert "_axis_unmeasurable(full)" in src, (
        "classification must read the full output, not `summary`:\n" + src)
    assert "_axis_unmeasurable(summary)" not in src


class TestConformanceCleanReadsMeasured:
    """An axis that ran but was not measured must not read as clean."""

    def _axes(self, **over):
        base = {"ran": True, "measured": True, "passed": True}
        return {**base, **over}

    def test_unmeasured_axis_is_not_clean(self, leerie):
        res = {"tests": self._axes(measured=False, passed=None)}
        assert leerie._conformance_clean(res, None) is False

    def test_unmeasured_axis_is_not_clean_even_when_red_at_baseline(self, leerie):
        """The exact shape that shipped a PR with an unrun test axis.

        `tests` was red at baseline, so the red-axis exclusion returned clean
        without ever consulting `measured`. The measured check therefore has to
        run BEFORE that exclusion.
        """
        # `_baseline_red_axes` reads a `red_axes` LIST, not per-axis dicts.
        baseline = {"red_axes": ["tests"]}
        res = {"tests": self._axes(measured=False, passed=None)}
        assert leerie._conformance_clean(res, baseline) is False, (
            "a baseline-red axis must not launder a SECOND, different "
            "unmeasurability into a clean verdict")

    def test_a_measured_pass_is_still_clean(self, leerie):
        """Anti-vacuity: the fix must not make everything unclean."""
        assert leerie._conformance_clean({"tests": self._axes()}, None) is True

    def test_a_baseline_red_axis_is_still_excluded_when_measured(self, leerie):
        """The pre-existing delta-scoping must survive untouched."""
        baseline = {"red_axes": ["tests"]}
        res = {"tests": self._axes(passed=False)}
        assert leerie._conformance_clean(res, baseline) is True

    def test_an_axis_that_never_ran_is_ignored(self, leerie):
        """`ran: False` is "not applicable", not "unmeasured work"."""
        res = {"tests": {"ran": False, "measured": False, "passed": None}}
        assert leerie._conformance_clean(res, None) is True
