"""Tests for _validate_result() — cross-field invariant checks on
worker results.

Returns `(failure_kind, message)` when the result is missing a required
mechanical-precondition field for its status branch, None otherwise.
The `failure_kind` is the structured discriminator `_retryable_failure`
dispatches on (see `_RETRYABLE_FAILURE_KINDS`). Per DESIGN §8 the
criteria file is informational and the `complete` branch no longer
gates on `criteria_results` shape or content — those tests cover that
loosening.
"""
from __future__ import annotations


# --- complete status -------------------------------------------------------
# Per DESIGN §8 the §8 confidence gate is the only load-bearing signal;
# the criteria file is informational (DESIGN §9). `complete` is accepted
# regardless of what `criteria_results` carries.

def test_complete_with_empty_criteria_results_returns_none(leerie):
    """Empty criteria_results no longer rejects `complete`."""
    assert leerie._validate_result(
        {"status": "complete", "criteria_results": []}) is None


def test_complete_with_missing_criteria_results_returns_none(leerie):
    """Missing criteria_results no longer rejects `complete`."""
    assert leerie._validate_result({"status": "complete"}) is None


def test_complete_with_all_met_criteria_returns_none(leerie):
    assert leerie._validate_result({
        "status": "complete",
        "criteria_results": [
            {"criterion": "tests pass", "met": True, "evidence": "ran them"},
            {"criterion": "no regressions", "met": True, "evidence": "verified"},
        ],
    }) is None


def test_complete_with_failing_criteria_returns_none(leerie):
    """`met:false` entries are recorded as warnings but do not reject
    `complete` (DESIGN §8 — confidence gate is the only load-bearing
    signal)."""
    assert leerie._validate_result({
        "status": "complete",
        "criteria_results": [
            {"criterion": "tests pass", "met": True, "evidence": "ok"},
            {"criterion": "no regressions", "met": False, "evidence": "broke X"},
        ],
    }) is None


# --- incomplete-handoff status ---------------------------------------------

def test_incomplete_handoff_without_checkpoint_path_returns_error(leerie):
    err = leerie._validate_result({"status": "incomplete-handoff"})
    assert err is not None
    assert err[0] == "broken"
    assert "checkpoint_path" in err[1]


def test_incomplete_handoff_with_null_checkpoint_path_returns_error(leerie):
    err = leerie._validate_result(
        {"status": "incomplete-handoff", "checkpoint_path": None}
    )
    assert err is not None
    assert err[0] == "broken"
    assert "checkpoint_path" in err[1]


def test_incomplete_handoff_with_nonexistent_checkpoint_returns_error(leerie, tmp_path):
    """The missing-checkpoint case is `empty_handoff` (retryable): the
    Claude Code session-limit no-op and the --max-turns-with-no-checkpoint
    cases both land here, and a fresh worker can plausibly do better.
    See `_RETRYABLE_FAILURE_KINDS`."""
    err = leerie._validate_result(
        {"status": "incomplete-handoff",
         "checkpoint_path": str(tmp_path / "nonexistent.md")}
    )
    assert err is not None
    assert err[0] == "empty_handoff"
    assert "does not exist" in err[1]


def test_incomplete_handoff_with_existing_checkpoint_returns_none(leerie, tmp_path):
    cp = tmp_path / "checkpoint.md"
    cp.write_text("# checkpoint\n")
    assert leerie._validate_result(
        {"status": "incomplete-handoff", "checkpoint_path": str(cp)}
    ) is None


# --- blocked status --------------------------------------------------------

def test_blocked_without_blocker_returns_error(leerie):
    err = leerie._validate_result({"status": "blocked"})
    assert err is not None
    assert err[0] == "broken"
    assert "blocker" in err[1]


def test_blocked_with_empty_blocker_returns_error(leerie):
    err = leerie._validate_result({"status": "blocked", "blocker": "   "})
    assert err is not None
    assert err[0] == "broken"
    assert "blocker" in err[1]


def test_blocked_with_blocker_returns_none(leerie):
    assert leerie._validate_result(
        {"status": "blocked", "blocker": "missing API key XYZ"}
    ) is None


# --- failed status ---------------------------------------------------------
# A `failed` result must carry a non-empty summary (the worker's diagnosis).
# The prompt requires it; the code enforces it per DESIGN §12.

def test_failed_with_empty_summary_returns_error(leerie):
    for res in (
        {"status": "failed"},
        {"status": "failed", "summary": ""},
        {"status": "failed", "summary": "   "},
    ):
        err = leerie._validate_result(res)
        assert err is not None
        assert err[0] == "broken"


def test_failed_with_summary_returns_none(leerie):
    assert leerie._validate_result(
        {"status": "failed", "summary": "tests still red after 5 iterations"}
    ) is None
