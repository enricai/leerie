"""Tests for N6: detecting malformed tool-envelope errors
(``_detect_malformed_tool_envelope``) and forcing one additional
``_run_checked_loop`` round when they occur (``log_path=``).

Precedent: ``_emit_bash_axis_warnings`` / ``_count_orphaned_bg_axis``
(leerie.py) scan a per-worker JSONL log the same tolerant way.
"""
from __future__ import annotations

import asyncio
import json

import pytest


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _user_event(text: str, is_error: bool = True) -> str:
    """One JSONL line shaped like a per-worker log's tool_result event."""
    return json.dumps({
        "type": "user",
        "message": {
            "content": [{
                "type": "tool_result",
                "is_error": is_error,
                "content": text,
            }],
        },
    })


# --- _detect_malformed_tool_envelope --------------------------------------- #

def test_detects_malformed_envelope_text(leerie, tmp_path):
    log_path = tmp_path / "worker.log"
    log_path.write_text(
        _user_event(
            "InputValidationError: An unexpected parameter `Bash` was "
            "provided") + "\n")
    assert leerie._detect_malformed_tool_envelope(log_path) is True


def test_benign_tool_fail_does_not_trigger(leerie, tmp_path):
    """A deliberate absence probe (e.g. `ls` on a maybe-missing path) is a
    completely ordinary `is_error` tool-fail and must never be mistaken
    for the envelope-malformation shape."""
    log_path = tmp_path / "worker.log"
    log_path.write_text(
        _user_event("ls: cannot access 'maybe/missing/path': No such "
                     "file or directory") + "\n")
    assert leerie._detect_malformed_tool_envelope(log_path) is False


def test_missing_log_file_returns_false(leerie, tmp_path):
    assert leerie._detect_malformed_tool_envelope(
        tmp_path / "does-not-exist.log") is False


def test_successful_tool_result_is_not_scanned(leerie, tmp_path):
    """A non-error tool_result mentioning the marker text incidentally
    (e.g. a worker successfully greps for the phrase) must not trigger —
    only `is_error` blocks are inspected."""
    log_path = tmp_path / "worker.log"
    log_path.write_text(
        _user_event("found: 'An unexpected parameter' in docs/CHANGELOG.md",
                     is_error=False) + "\n")
    assert leerie._detect_malformed_tool_envelope(log_path) is False


def test_malformed_lines_are_skipped_not_fatal(leerie, tmp_path):
    log_path = tmp_path / "worker.log"
    log_path.write_text(
        "not json at all\n"
        + _user_event(
            "An unexpected parameter `Edit` was provided") + "\n")
    assert leerie._detect_malformed_tool_envelope(log_path) is True


# --- _run_checked_loop(log_path=...) forces one retry round ---------------- #

def test_checked_loop_retries_once_when_envelope_detected(leerie, tmp_path):
    log_path = tmp_path / "integrator-x.log"
    calls = []
    feedback_received = []

    async def invoke():
        calls.append(1)
        if len(calls) == 1:
            # First round: the worker's log carries the malformed-envelope
            # marker even though its own structured result is otherwise
            # clean (check() alone would find nothing).
            log_path.write_text(
                _user_event(
                    "An unexpected parameter `Bash` was provided") + "\n")
        else:
            # Second round recovers: no further malformed envelope, and
            # the loop must not force a third round.
            log_path.write_text(_user_event("ls: fine", is_error=False)
                                 + "\n")
        return {"status": "ready"}

    async def on_feedback(fb):
        feedback_received.append(fb)

    result, warnings = _run(leerie._run_checked_loop(
        invoke=invoke,
        check=lambda r: [],
        name="test",
        max_rounds=3,
        make_feedback_prompt=on_feedback,
        log_path=log_path,
    ))
    assert len(calls) == 2, (
        f"a malformed-envelope hit on round 0 must force exactly one "
        f"extra round: {len(calls)} invocations")
    assert result == {"status": "ready"}
    assert len(feedback_received) == 1
    assert "MALFORMED_TOOL_ENVELOPE" in feedback_received[0]
    assert any("MALFORMED_TOOL_ENVELOPE" in w for w in warnings)


def test_checked_loop_no_retry_when_envelope_absent(leerie, tmp_path):
    """Negative control: a clean log (only benign tool-fails, or none at
    all) must not force any extra round beyond what `check()` itself
    demands."""
    log_path = tmp_path / "integrator-y.log"
    log_path.write_text(
        _user_event("ls: cannot access 'x': No such file or directory")
        + "\n")
    calls = []

    async def invoke():
        calls.append(1)
        return {"status": "ready"}

    result, warnings = _run(leerie._run_checked_loop(
        invoke=invoke,
        check=lambda r: [],
        name="test",
        max_rounds=3,
        log_path=log_path,
    ))
    assert len(calls) == 1
    assert result == {"status": "ready"}
    assert not any("MALFORMED_TOOL_ENVELOPE" in w for w in warnings)
