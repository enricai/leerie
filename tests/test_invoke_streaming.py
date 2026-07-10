"""Tests for _invoke()'s line-by-line streaming behavior.

These tests mock `asyncio.create_subprocess_exec` rather than spawn a
real subprocess (per CLAUDE.md, claude_p is not exercised live in unit
tests — the worker invocation path is end-to-end tier). The mock
yields a pre-recorded stream of events shaped like real
`claude -p --output-format stream-json --verbose` output.

What we pin here:
  - The final `result` event is returned as the envelope (same shape
    consumers already parse).
  - Per-worker log file is always written, regardless of verbosity.
  - Inline summaries are emitted to leerie's log() per verbosity.
  - If no `result` event arrives, WorkerError is raised.
"""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import pytest


# ----- minimal mock for asyncio.subprocess.Process --------------------------

class _MockStream:
    """A mock asyncio stream that yields pre-set lines, then EOF."""
    def __init__(self, lines: list[str]):
        self._lines = [(l + "\n").encode() for l in lines]
        self._idx = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._idx >= len(self._lines):
            raise StopAsyncIteration
        line = self._lines[self._idx]
        self._idx += 1
        return line

    async def read(self, n: int = -1) -> bytes:
        # Used by the stderr drain path. Return empty bytes immediately
        # since the mock has no stderr.
        return b""


_MOCK_PID_SENTINEL = 999_999_999


class _MockProc:
    """A mock asyncio.subprocess.Process.

    `pid` is a large positive sentinel so that `os.killpg(pid, sig)`
    (which `_terminate_proc_tree` uses on exception paths) raises
    `ProcessLookupError` cleanly on POSIX systems — the helper
    swallows that. A real-looking PID could collide with a live
    process group on the test host; `0` is unsafe (means "current
    process group"); a negative number can be interpreted as a kill
    target on some kernels. A large unowned positive number is the
    safe choice."""
    def __init__(self, stdout_lines: list[str], returncode: int = 0):
        self.stdout = _MockStream(stdout_lines)
        self.stderr = _MockStream([])
        self.returncode = returncode
        self.killed = False
        self.pid = _MOCK_PID_SENTINEL

    def kill(self):
        self.killed = True

    async def wait(self):
        return self.returncode

    async def communicate(self):
        # not used by streaming path, but kept for completeness
        out = b""
        async for chunk in self.stdout:
            out += chunk
        return out, b""


def _make_subprocess_exec_mock(stdout_lines: list[str], returncode: int = 0):
    async def fake(*cmd, **kwargs):
        return _MockProc(stdout_lines, returncode)
    return fake


@pytest.fixture
def leerie_dir(tmp_path):
    cd = tmp_path / ".leerie"
    cd.mkdir()
    (cd / "logs").mkdir()
    return cd


# ----- envelope return ------------------------------------------------------

def test_invoke_returns_final_result_event(leerie, leerie_dir, monkeypatch):
    """The final type='result' event is the envelope, returned to the
    caller. Same shape as the pre-streaming json mode."""
    events = [
        json.dumps({"type": "system", "subtype": "init",
                    "model": "claude-opus-4-7"}),
        json.dumps({"type": "result", "subtype": "success",
                    "num_turns": 1, "total_cost_usd": 0.01,
                    "structured_output": {"ok": True},
                    "is_error": False}),
    ]
    monkeypatch.setattr("asyncio.create_subprocess_exec",
                        _make_subprocess_exec_mock(events))
    result = asyncio.run(leerie._invoke(
        ["claude", "-p", "x"], cwd=str(leerie_dir.parent),
        timeout=60, sid="t1", leerie_dir=leerie_dir,
        verbosity="stream"))
    assert result["type"] == "result"
    assert result["subtype"] == "success"
    assert result["structured_output"] == {"ok": True}


def test_invoke_ignores_task_notification_result_event(leerie, leerie_dir,
                                                        monkeypatch):
    """`claude -p` can emit additional `result` events with
    `origin: {"kind": "task-notification"}` after the worker's real
    result, when backgrounded Bash subprocesses finish and the CLI
    wakes the worker up to acknowledge them. Those events carry no
    `structured_output`. Envelope capture must skip them; otherwise
    the caller sees `structured_output: None` and misclassifies a
    successful worker as failed. Multiple task-notification tails
    must all be ignored — the filter is not one-shot."""
    events = [
        json.dumps({"type": "result", "subtype": "success",
                    "num_turns": 21, "total_cost_usd": 0.34,
                    "structured_output": {"status": "complete"},
                    "is_error": False}),
        json.dumps({"type": "result", "subtype": "success",
                    "num_turns": 1, "total_cost_usd": 0.36,
                    "is_error": False,
                    "origin": {"kind": "task-notification"}}),
        json.dumps({"type": "result", "subtype": "success",
                    "num_turns": 2, "total_cost_usd": 0.38,
                    "is_error": False,
                    "origin": {"kind": "task-notification"}}),
    ]
    monkeypatch.setattr("asyncio.create_subprocess_exec",
                        _make_subprocess_exec_mock(events))
    result = asyncio.run(leerie._invoke(
        ["claude", "-p", "x"], cwd=str(leerie_dir.parent),
        timeout=60, sid="t-tn", leerie_dir=leerie_dir,
        verbosity="stream"))
    assert result["num_turns"] == 21
    assert result["structured_output"] == {"status": "complete"}
    assert "origin" not in result


def test_invoke_raises_when_no_result_event(leerie, leerie_dir, monkeypatch):
    """A worker that exits without emitting any result event (e.g. the
    process died mid-stream) raises WorkerError — same error class
    leerie's existing retry path already handles."""
    events = [
        json.dumps({"type": "system", "subtype": "init",
                    "model": "x"}),
        # No result event — stream ends.
    ]
    monkeypatch.setattr("asyncio.create_subprocess_exec",
                        _make_subprocess_exec_mock(events))
    with pytest.raises(leerie.WorkerError):
        asyncio.run(leerie._invoke(
            ["claude", "-p", "x"], cwd=str(leerie_dir.parent),
            timeout=60, sid="t2", leerie_dir=leerie_dir,
            verbosity="stream"))


# ----- out-of-credits truncation → pause-and-surface -----------------------

# A real out-of-credits kill (navegando run 60c68e71…): the exhaustion
# reason is `overageDisabledReason:"out_of_credits"` while `status` is
# still the benign "allowed". The latch keys on `overageDisabledReason`
# being an exhaustion reason — NOT on `overageStatus:"rejected"`, which is
# a standing state for orgs with overage disabled (see
# `_ORG_LEVEL_DISABLED_EVENT` below).
_OVERAGE_BLOCKED_EVENT = {
    "type": "rate_limit_event",
    "rate_limit_info": {
        "status": "allowed", "resetsAt": 1783446600,
        "rateLimitType": "five_hour", "overageStatus": "rejected",
        "overageDisabledReason": "out_of_credits", "isUsingOverage": False,
    },
}

# The false-positive shape (navegando run f77f7456…): an org that has
# extra-usage (overage) turned OFF at the org level emits this in EVERY
# rate_limit_event — `overageStatus:"rejected"` with
# `overageDisabledReason:"org_level_disabled"` and `status:"allowed"`. It
# is NOT credit exhaustion (plenty of base quota remains); it must not arm
# the out-of-credits latch. 96/96 events in that run carried this exact
# shape, yet workers succeeded — the ones that truncated for unrelated
# reasons were being misreported as out-of-credits before the fix.
_ORG_LEVEL_DISABLED_EVENT = {
    "type": "rate_limit_event",
    "rate_limit_info": {
        "status": "allowed", "resetsAt": 1783620600,
        "rateLimitType": "five_hour", "overageStatus": "rejected",
        "overageDisabledReason": "org_level_disabled", "isUsingOverage": False,
    },
}


def test_invoke_overage_block_plus_truncation_raises_ratelimited(
        leerie, leerie_dir, monkeypatch):
    """The observed crash: a genuine out-of-credits exhaustion event, then a
    truncated assistant turn and NO result event — the CLI was killed the
    moment credits ran out. This must raise RateLimitedExit with
    out_of_credits=True (routing into main()'s pause-and-surface path), NOT
    a bare WorkerError that bypasses the auth/quota backoff and die()s the
    run non-resumably.

    reset_at is None: the kill left no result envelope carrying a parseable
    resetsAt. out_of_credits=True tells main() to pause-and-surface (exit
    EXIT_LOCKED with a --resume hint) rather than auto-resume — out-of-credits
    has no reset clock."""
    events = [
        json.dumps({"type": "system", "subtype": "init", "model": "opus"}),
        json.dumps(_OVERAGE_BLOCKED_EVENT),
        json.dumps({"type": "assistant", "message": {"content": [
            {"type": "text", "text": "I'll analyze the rename"}]}}),
        # Stream truncates — no result event.
    ]
    monkeypatch.setattr("asyncio.create_subprocess_exec",
                        _make_subprocess_exec_mock(events, returncode=1))
    with pytest.raises(leerie.RateLimitedExit) as ei:
        asyncio.run(leerie._invoke(
            ["claude", "-p", "x"], cwd=str(leerie_dir.parent),
            timeout=60, sid="reconciler", leerie_dir=leerie_dir,
            verbosity="stream"))
    assert ei.value.reset_at is None
    assert ei.value.out_of_credits is True
    assert "out of credits" in ei.value.raw_message


def test_invoke_org_level_disabled_truncation_raises_workererror(
        leerie, leerie_dir, monkeypatch):
    """Regression pin for the reported false positive: an org with overage
    disabled at the org level emits `overageStatus:"rejected"` /
    `overageDisabledReason:"org_level_disabled"` in every rate_limit_event.
    That is NOT credit exhaustion. When a worker stream then truncates for
    an unrelated reason (no result event), _invoke must raise a plain
    WorkerError (the ordinary retryable/advisory path) — NOT a
    RateLimitedExit that would misroute the run into an out-of-credits
    pause."""
    events = [
        json.dumps({"type": "system", "subtype": "init", "model": "opus"}),
        json.dumps(_ORG_LEVEL_DISABLED_EVENT),
        json.dumps({"type": "assistant", "message": {"content": [
            {"type": "text", "text": "Checking quote usage"}]}}),
        # Stream truncates — no result event, for an unrelated reason.
    ]
    monkeypatch.setattr("asyncio.create_subprocess_exec",
                        _make_subprocess_exec_mock(events, returncode=1))
    with pytest.raises(leerie.WorkerError):
        asyncio.run(leerie._invoke(
            ["claude", "-p", "x"], cwd=str(leerie_dir.parent),
            timeout=60, sid="final-conformer-r0", leerie_dir=leerie_dir,
            verbosity="stream"))


def test_invoke_overage_block_with_result_returns_envelope(
        leerie, leerie_dir, monkeypatch):
    """Control for the corpus runs that carried an exhaustion overage event
    yet SUCCEEDED: when a result event DOES arrive, the overage warning is
    benign and _invoke returns the envelope normally — no RateLimitedExit.
    This is why the fix keys on the *coincidence* of exhaustion AND a
    missing result event, not on the event alone."""
    events = [
        json.dumps({"type": "system", "subtype": "init", "model": "opus"}),
        json.dumps(_OVERAGE_BLOCKED_EVENT),
        json.dumps({"type": "result", "subtype": "success",
                    "num_turns": 3, "total_cost_usd": 0.05,
                    "structured_output": {"ok": True}, "is_error": False}),
    ]
    monkeypatch.setattr("asyncio.create_subprocess_exec",
                        _make_subprocess_exec_mock(events))
    result = asyncio.run(leerie._invoke(
        ["claude", "-p", "x"], cwd=str(leerie_dir.parent),
        timeout=60, sid="pr-writer", leerie_dir=leerie_dir,
        verbosity="stream"))
    assert result["type"] == "result"
    assert result["structured_output"] == {"ok": True}


# ----- per-worker log file --------------------------------------------------

def test_log_file_written_at_stream(leerie, leerie_dir, monkeypatch):
    events = [
        json.dumps({"type": "system", "subtype": "init", "model": "m"}),
        json.dumps({"type": "assistant", "message": {"content": [
            {"type": "text", "text": "hi"}]}}),
        json.dumps({"type": "result", "subtype": "success",
                    "num_turns": 1, "is_error": False}),
    ]
    monkeypatch.setattr("asyncio.create_subprocess_exec",
                        _make_subprocess_exec_mock(events))
    asyncio.run(leerie._invoke(
        ["claude", "-p", "x"], cwd=str(leerie_dir.parent),
        timeout=60, sid="t3", leerie_dir=leerie_dir,
        verbosity="stream"))
    log_text = (leerie_dir / "logs" / "t3.log").read_text()
    # All 3 events appear in the file.
    assert "system/init" in log_text
    assert "assistant" in log_text
    assert "result/success" in log_text


def test_log_file_written_at_quiet(leerie, leerie_dir, monkeypatch):
    """The per-worker file is written REGARDLESS of verbosity — even
    at quiet, the audit trail is preserved. Verbosity gates only the
    inline output."""
    events = [
        json.dumps({"type": "system", "subtype": "init", "model": "m"}),
        json.dumps({"type": "result", "subtype": "success",
                    "num_turns": 1, "is_error": False}),
    ]
    monkeypatch.setattr("asyncio.create_subprocess_exec",
                        _make_subprocess_exec_mock(events))
    asyncio.run(leerie._invoke(
        ["claude", "-p", "x"], cwd=str(leerie_dir.parent),
        timeout=60, sid="t4", leerie_dir=leerie_dir,
        verbosity="quiet"))
    log_text = (leerie_dir / "logs" / "t4.log").read_text()
    assert "system/init" in log_text
    assert "result/success" in log_text


def test_log_file_records_non_json_lines(leerie, leerie_dir, monkeypatch):
    """If a line of stdout isn't valid JSON (rare; defensive), the raw
    line goes to the file with a 'non-json-line' header. Stream
    progresses past it."""
    events = [
        "not valid json at all",
        json.dumps({"type": "result", "subtype": "success",
                    "num_turns": 1, "is_error": False}),
    ]
    monkeypatch.setattr("asyncio.create_subprocess_exec",
                        _make_subprocess_exec_mock(events))
    asyncio.run(leerie._invoke(
        ["claude", "-p", "x"], cwd=str(leerie_dir.parent),
        timeout=60, sid="t5", leerie_dir=leerie_dir,
        verbosity="stream"))
    log_text = (leerie_dir / "logs" / "t5.log").read_text()
    assert "non-json-line" in log_text
    assert "not valid json at all" in log_text


# ----- inline summaries (verbosity-gated) ----------------------------------

def test_inline_summaries_emitted_at_stream(leerie, leerie_dir,
                                            monkeypatch, capsys):
    events = [
        json.dumps({"type": "system", "subtype": "init",
                    "model": "opus"}),
        json.dumps({"type": "result", "subtype": "success",
                    "num_turns": 2, "total_cost_usd": 0.05,
                    "is_error": False}),
    ]
    monkeypatch.setattr("asyncio.create_subprocess_exec",
                        _make_subprocess_exec_mock(events))
    asyncio.run(leerie._invoke(
        ["claude", "-p", "x"], cwd=str(leerie_dir.parent),
        timeout=60, sid="t6", leerie_dir=leerie_dir,
        verbosity="stream"))
    out = capsys.readouterr().out
    # Two summary lines: starting + done.
    assert "[t6] starting" in out
    assert "[t6] done" in out


def test_no_inline_summaries_at_quiet_for_success(leerie, leerie_dir,
                                                   monkeypatch, capsys):
    """At quiet, successful events produce no inline output. Per-worker
    file is still written (see test_log_file_written_at_quiet)."""
    events = [
        json.dumps({"type": "system", "subtype": "init", "model": "m"}),
        json.dumps({"type": "assistant", "message": {"content": [
            {"type": "text", "text": "ok"}]}}),
        json.dumps({"type": "result", "subtype": "success",
                    "num_turns": 1, "is_error": False}),
    ]
    monkeypatch.setattr("asyncio.create_subprocess_exec",
                        _make_subprocess_exec_mock(events))
    asyncio.run(leerie._invoke(
        ["claude", "-p", "x"], cwd=str(leerie_dir.parent),
        timeout=60, sid="t7", leerie_dir=leerie_dir,
        verbosity="quiet"))
    out = capsys.readouterr().out
    # None of the individual events should produce a [t7] line.
    assert "[t7]" not in out


def test_multi_line_summary_each_line_has_timestamp(leerie, leerie_dir,
                                                     monkeypatch, capsys):
    """A multi-line summary (multi-line text block, or multiple
    tool_use blocks in one event) must produce one log() call per
    line so each line gets its own ISO-8601 [leerie] prefix.

    Earlier behavior returned a \\n-joined string and called log()
    once, which prepended the timestamp only to the first line —
    lines 2+ visually disconnected from the orchestrator's
    timestamped stream. In a parallel run, untimestamped lines from
    one worker could be misread as belonging to a different worker."""
    events = [
        json.dumps({"type": "assistant", "message": {"content": [
            {"type": "text", "text": "first paragraph\n"
                                     "second paragraph\n"
                                     "third paragraph"}]}}),
        json.dumps({"type": "result", "subtype": "success",
                    "num_turns": 1, "is_error": False}),
    ]
    monkeypatch.setattr("asyncio.create_subprocess_exec",
                        _make_subprocess_exec_mock(events))
    asyncio.run(leerie._invoke(
        ["claude", "-p", "x"], cwd=str(leerie_dir.parent),
        timeout=60, sid="t-multi", leerie_dir=leerie_dir,
        verbosity="stream"))
    out = capsys.readouterr().out
    # Each text line is on its own output line, each prefixed with
    # the ISO-8601 second-precision local-tz [leerie] timestamp.
    line_prefix_re = re.compile(
        r"^\s*\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2} "
        r"\[leerie\]")
    paragraphs = ["first paragraph", "second paragraph", "third paragraph"]
    for para in paragraphs:
        # Find the line containing this paragraph and assert it has
        # the leerie prefix.
        matching = [l for l in out.split("\n") if para in l]
        assert matching, f"missing line for {para!r}; got: {out!r}"
        for l in matching:
            assert line_prefix_re.match(l), (
                f"line {l!r} lacks ISO-8601 [leerie] prefix — the "
                "log() call per line guarantee broke")


def test_worker_failure_surfaces_even_at_quiet(leerie, leerie_dir,
                                                monkeypatch, capsys):
    """Errors emit at every level (clig.dev). A result event with
    is_error=true must produce a summary even at quiet."""
    events = [
        json.dumps({"type": "result", "subtype": "error_max_turns",
                    "num_turns": 5, "is_error": True}),
    ]
    monkeypatch.setattr("asyncio.create_subprocess_exec",
                        _make_subprocess_exec_mock(events))
    asyncio.run(leerie._invoke(
        ["claude", "-p", "x"], cwd=str(leerie_dir.parent),
        timeout=60, sid="t8", leerie_dir=leerie_dir,
        verbosity="quiet"))
    out = capsys.readouterr().out
    assert "[t8] worker failed" in out


# ----- P10-1: live-flush property (per-worker log file is line-buffered) ---

def test_log_file_opened_line_buffered(leerie, leerie_dir, monkeypatch):
    """The per-worker log file MUST be opened line-buffered (buffering=1)
    so a user running `tail -f .leerie/logs/<sid>.log` sees events as
    they happen, not when the file closes at worker end. Default Python
    text-mode buffering would batch writes into ~8KB chunks and only
    flush on close — silently defeating the live-progress property the
    streaming feature exists to provide.

    Tested by spying on Path.open to capture the keyword arguments. Set
    up before `_invoke` runs; assert `buffering=1` was passed."""
    open_calls: list[dict] = []
    real_open = type(leerie_dir).open  # pathlib.Path.open

    def spy_open(self, *args, **kwargs):
        # Spy on every Path.open and capture the path along with the
        # kwargs so we can filter to the per-worker log open after.
        open_calls.append({"path": str(self), "args": args,
                           "kwargs": kwargs})
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(type(leerie_dir), "open", spy_open)
    events = [json.dumps({"type": "result", "subtype": "success",
                          "num_turns": 1, "is_error": False})]
    monkeypatch.setattr("asyncio.create_subprocess_exec",
                        _make_subprocess_exec_mock(events))
    asyncio.run(leerie._invoke(
        ["claude", "-p", "x"], cwd=str(leerie_dir.parent),
        timeout=60, sid="t-flush", leerie_dir=leerie_dir,
        verbosity="quiet"))
    # The log file open call must have buffering=1.
    log_opens = [c for c in open_calls if c["path"].endswith("t-flush.log")]
    assert log_opens, ("expected at least one t-flush.log open "
                       f"intercepted; got: {open_calls!r}")
    for call in log_opens:
        assert call["kwargs"].get("buffering") == 1, (
            "per-worker log file MUST be opened with buffering=1 (line-"
            "buffered) so `tail -f` shows live progress. Without this, "
            "Python's default text-mode buffering batches writes until "
            f"the file closes. Got: {call!r}")


# ----- P10-2: StreamReader limit raised to handle large JSON events --------

class _OverlimitStream:
    """A mock stream that yields one valid event, then raises
    ValueError on the next iteration — simulating asyncio's
    StreamReader hitting the line limit mid-stream (which is what
    happens when a worker emits a line >10 MiB)."""
    def __init__(self, first_line: str):
        self._yielded = False
        self._line = (first_line + "\n").encode()

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._yielded:
            self._yielded = True
            return self._line
        raise ValueError(
            "Separator is not found, and chunk exceed the limit")

    async def read(self, n: int = -1) -> bytes:
        return b""


def test_value_error_from_line_limit_becomes_worker_error(leerie,
                                                          leerie_dir,
                                                          monkeypatch):
    """When a worker emits a line larger than the StreamReader limit,
    `async for proc.stdout` raises `ValueError("Separator is not
    found...")`. Without explicit handling this propagates through
    claude_p's retry loop and surfaces as a Python traceback. The
    Pass-12 fix wraps the `async for` in a try/except that converts
    the ValueError into a WorkerError — same exception class
    leerie's retry / blocked-subtask paths already handle.

    Mock the stream so the second iteration raises ValueError; assert
    _invoke raises WorkerError (not ValueError) and the message names
    the buffer limit so a user can recognize the failure mode."""
    class _OverlimitProc:
        def __init__(self):
            self.stdout = _OverlimitStream(json.dumps({
                "type": "system", "subtype": "init", "model": "m"}))
            self.stderr = _MockStream([])
            self.returncode = 1
            self.pid = _MOCK_PID_SENTINEL

        def kill(self):
            pass

        async def wait(self):
            return self.returncode

    async def fake(*cmd, **kwargs):
        return _OverlimitProc()

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake)
    with pytest.raises(leerie.WorkerError) as exc_info:
        asyncio.run(leerie._invoke(
            ["claude", "-p", "x"], cwd=str(leerie_dir.parent),
            timeout=60, sid="t-overlimit", leerie_dir=leerie_dir,
            verbosity="quiet"))
    msg = str(exc_info.value)
    # The error message must name the limit so a user diagnosing
    # the failure can act on it (not a generic "worker failed").
    assert "10 MiB" in msg or "buffer limit" in msg, msg
    # ValueError must NOT leak through — explicitly check that the
    # caller doesn't see a ValueError. (pytest.raises only catches
    # the exception type asked for; if the code actually raises
    # ValueError, this test would fail BEFORE getting here. The
    # explicit assert is redundant but documents the contract.)
    assert not isinstance(exc_info.value, ValueError), (
        "ValueError leaked through — should have been converted to "
        "WorkerError. See Pass-12 audit.")


def test_progress_prefix_shows_wave_and_activity(leerie, leerie_dir,
                                                  monkeypatch, capsys):
    """When progress is passed to _invoke as a callable returning the
    (running, in_conformer, done, wave_idx, wave_total) tuple, every
    inline summary line is prefixed with the activity prefix before the
    worker tag. The callable is invoked per event so sibling workers
    finishing mid-run bump the count for still-running workers — the
    prefix is not a spawn-time snapshot."""
    events = [
        json.dumps({"type": "system", "subtype": "init", "model": "opus"}),
        json.dumps({"type": "result", "subtype": "success",
                    "num_turns": 1, "is_error": False}),
    ]
    monkeypatch.setattr("asyncio.create_subprocess_exec",
                        _make_subprocess_exec_mock(events))
    # 9 running, 0 in_conformer, 3 done, wave 2 of 3
    asyncio.run(leerie._invoke(
        ["claude", "-p", "x"], cwd=str(leerie_dir.parent),
        timeout=60, sid="t-prog", leerie_dir=leerie_dir,
        verbosity="stream", progress=lambda: (9, 0, 3, 2, 3)))
    out = capsys.readouterr().out
    assert "[wave 2 of 3 · running 9 subtasks · 3 subtasks done]" in out
    assert "[t-prog] starting" in out


def test_progress_prefix_recomputes_per_event(leerie, leerie_dir,
                                                monkeypatch, capsys):
    """The progress callable is invoked per event, not once at spawn.
    Pin this directly: a counter whose `done` rises across events must
    show up as a monotonically rising `N subtasks done` segment."""
    events = [
        json.dumps({"type": "system", "subtype": "init", "model": "opus"}),
        json.dumps({"type": "assistant", "message": {"content": [
            {"type": "text", "text": "step 1"}]}}),
        json.dumps({"type": "assistant", "message": {"content": [
            {"type": "text", "text": "step 2"}]}}),
        json.dumps({"type": "result", "subtype": "success",
                    "num_turns": 1, "is_error": False}),
    ]
    monkeypatch.setattr("asyncio.create_subprocess_exec",
                        _make_subprocess_exec_mock(events))
    counter = {"done": 0}

    def get_progress() -> tuple[int, int, int, int, int]:
        counter["done"] += 1
        # (running, in_conformer, done, wave_idx, wave_total)
        return 14 - counter["done"], 0, counter["done"], 1, 3

    asyncio.run(leerie._invoke(
        ["claude", "-p", "x"], cwd=str(leerie_dir.parent),
        timeout=60, sid="t-fresh", leerie_dir=leerie_dir,
        verbosity="stream", progress=get_progress))
    out = capsys.readouterr().out
    assert "1 subtask done" in out
    assert "2 subtasks done" in out
    assert "3 subtasks done" in out
    assert "wave 1 of 3" in out


def test_create_subprocess_exec_uses_high_limit(leerie, leerie_dir,
                                                  monkeypatch):
    """asyncio's StreamReader defaults to 64KB per line. A single JSON
    event from `claude -p --output-format stream-json` can plausibly
    exceed that — the implementer's `structured_output` tool_use carries
    the full worker payload (criteria results with multi-KB evidence
    strings, falsifier arrays, etc.). Without a higher limit, a large
    event raises LimitOverrunError mid-stream and the worker run dies
    with no useful diagnostic.

    Pin that create_subprocess_exec is called with a limit well above
    the default."""
    captured_kwargs: dict = {}

    async def spy(*cmd, **kwargs):
        captured_kwargs.update(kwargs)
        # Return a minimal valid stream so _invoke completes normally.
        events = [json.dumps({"type": "result", "subtype": "success",
                              "num_turns": 1, "is_error": False})]
        return _MockProc(events)

    monkeypatch.setattr("asyncio.create_subprocess_exec", spy)
    asyncio.run(leerie._invoke(
        ["claude", "-p", "x"], cwd=str(leerie_dir.parent),
        timeout=60, sid="t-limit", leerie_dir=leerie_dir,
        verbosity="quiet"))

    limit = captured_kwargs.get("limit")
    assert limit is not None, (
        "create_subprocess_exec must be called with an explicit `limit` "
        "kwarg to override asyncio's 64KB default — a single JSON event "
        "can exceed 64KB. See Pass-10 audit P10-2.")
    # 1 MB is the floor for "well above the default"; we shipped 10 MB.
    assert limit >= 1_000_000, (
        f"limit={limit} is too close to asyncio's 64KB default. A "
        "large structured_output event would still crash _read_stream "
        "with LimitOverrunError. The shipped value is 10 MB.")


# ----- PID-exhaustion detection → early WorkerError (DESIGN §6) -------------

def _errored_tool_result(text: str = "Exit code 1") -> str:
    """One stream line: a user/tool_result with is_error=true. Under PID
    exhaustion the worker emits a run of these (every shell fails)."""
    return json.dumps({"type": "user", "message": {"content": [
        {"type": "tool_result", "is_error": True, "content": text,
         "tool_use_id": "tu"}]}})


def _asst_bash():
    """An assistant turn requesting a Bash call — the kind of event that
    ALWAYS sits between two tool-results in a real stream. The detector
    must NOT let these reset its window (the v0.9.37 bug)."""
    return json.dumps({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "Bash", "id": "t", "input": {"command": "x"}}]}})


def _ok_tool_result(text: str = "ok"):
    return json.dumps({"type": "user", "message": {"content": [
        {"type": "tool_result", "is_error": False, "content": text,
         "tool_use_id": "tu"}]}})


def _enable_fake_cgroup(leerie, monkeypatch, stat_triple):
    """Make _invoke believe a worker cgroup exists (so cgroup_sid is set)
    and have _cgroup_stat report `stat_triple`."""
    monkeypatch.setattr(leerie, "_cgroup_create", lambda sid, mem, pids: sid)
    monkeypatch.setattr(leerie, "_cgroup_enroll", lambda sid, pid: True)
    monkeypatch.setattr(leerie, "_cgroup_destroy", lambda sid: None)
    monkeypatch.setattr(leerie, "_cgroup_stat", lambda sid: stat_triple)


def test_invoke_pid_exhaustion_realistic_interleaved_stream(
        leerie, leerie_dir, monkeypatch):
    """THE regression test for the v0.9.37 gap. In a real stream a
    tool-result is ALWAYS followed by the model's assistant turn (and
    system/rate_limit events) before the next tool-result — the events are
    never adjacent. The old *consecutive* counter reset on every one of
    those in-between events and so could never exceed 1, never firing. The
    window counts only tool-result outcomes, so an exhausted worker is
    caught despite the interleaving. Pattern mirrors the captured
    config-003 log: err, <assistant>, err, <assistant>, err ..."""
    events = [json.dumps({"type": "system", "subtype": "init", "model": "x"})]
    for _ in range(4):
        events.append(_errored_tool_result())
        events.append(_asst_bash())          # the interleaved reset-bait
        events.append(json.dumps({"type": "system"}))
    events.append(json.dumps({"type": "result", "subtype": "success",
                              "structured_output": {"ok": True},
                              "is_error": False}))
    _enable_fake_cgroup(leerie, monkeypatch, (256, 256, 0))  # at cap
    monkeypatch.setattr("asyncio.create_subprocess_exec",
                        _make_subprocess_exec_mock(events))
    with pytest.raises(leerie.WorkerError) as ei:
        asyncio.run(leerie._invoke(
            ["claude", "-p", "x"], cwd=str(leerie_dir.parent),
            timeout=60, sid="cfg-003", leerie_dir=leerie_dir,
            verbosity="stream",
            worker_memory_max_bytes=1 << 30, worker_pids_max=256))
    assert "exhausted its PID cgroup" in str(ei.value)


def test_invoke_pid_exhaustion_raises_early(leerie, leerie_dir, monkeypatch):
    """A worker at its pids.max cap (current >= max) that emits several
    consecutive errored tool-results is terminated early with WorkerError,
    instead of streaming to a (never-arriving) result event."""
    events = [
        json.dumps({"type": "system", "subtype": "init", "model": "x"}),
        _errored_tool_result(),   # 1
        _errored_tool_result(),   # 2
        _errored_tool_result(),   # 3 -> probe fires, current>=max -> raise
        json.dumps({"type": "result", "subtype": "success",
                    "structured_output": {"ok": True}, "is_error": False}),
    ]
    _enable_fake_cgroup(leerie, monkeypatch, (256, 256, 0))
    monkeypatch.setattr("asyncio.create_subprocess_exec",
                        _make_subprocess_exec_mock(events))
    with pytest.raises(leerie.WorkerError) as ei:
        asyncio.run(leerie._invoke(
            ["claude", "-p", "x"], cwd=str(leerie_dir.parent),
            timeout=60, sid="cfg-002", leerie_dir=leerie_dir,
            verbosity="stream",
            worker_memory_max_bytes=1 << 30, worker_pids_max=256))
    assert "exhausted its PID cgroup" in str(ei.value)


def test_invoke_pid_exhaustion_via_climbing_denials(leerie, leerie_dir,
                                                    monkeypatch):
    """Even when current < max, a climbing pids.events.max (fresh fork
    denials since the FIRST probe) confirms exhaustion. The probe fires
    once per errored result past the threshold; probe #1 (3rd error)
    latches the baseline, probe #2 (4th error) sees the counter climbed
    and raises. _cgroup_stat is shared with the _DescendantTracker poll
    loop, which probes it on every iteration and interposes an
    indeterminate number of calls between the detector's two probes; a
    monotonically climbing denial counter guarantees probe #2 exceeds
    whatever value probe #1 latched as the baseline (at_cap stays False
    since 200 < 256, so the raise comes via denials_climbing)."""
    events = [
        json.dumps({"type": "system", "subtype": "init", "model": "x"}),
        _errored_tool_result(),   # 1
        _errored_tool_result(),   # 2
        _errored_tool_result(),   # 3 -> probe #1: baseline latches
        _errored_tool_result(),   # 4 -> probe #2: counter climbed -> raise
        json.dumps({"type": "result", "subtype": "success",
                    "structured_output": {"ok": True}, "is_error": False}),
    ]
    # _cgroup_stat is shared with the _DescendantTracker poll loop, which
    # probes it on every iteration and interposes an indeterminate number of
    # calls between the detector's two probes. A monotonically climbing
    # ev_max guarantees the detector's 2nd probe exceeds whatever its 1st
    # probe latched as the baseline, whatever that value was — the tracker's
    # calls only advance it further. current=200 < max=256 keeps at_cap
    # False (raise is via denials_climbing) and pressure (200/256 = 0.78)
    # stays below _PID_REAP_HIGH_WATER (0.90) so reaping never arms.
    denials = [0]
    def _climbing_stat(sid):
        denials[0] += 1
        return (200, 256, denials[0])
    _enable_fake_cgroup(leerie, monkeypatch, None)
    monkeypatch.setattr(leerie, "_cgroup_stat", _climbing_stat)
    monkeypatch.setattr("asyncio.create_subprocess_exec",
                        _make_subprocess_exec_mock(events))
    with pytest.raises(leerie.WorkerError):
        asyncio.run(leerie._invoke(
            ["claude", "-p", "x"], cwd=str(leerie_dir.parent),
            timeout=60, sid="cfg-002", leerie_dir=leerie_dir,
            verbosity="stream",
            worker_memory_max_bytes=1 << 30, worker_pids_max=256))


def test_invoke_ordinary_failures_do_not_trigger(leerie, leerie_dir,
                                                  monkeypatch):
    """A worker with failing commands but a healthy cgroup (current far
    below max, no denials) must NOT be terminated — it streams to its
    result event normally. Guards against killing a worker whose tests
    merely fail."""
    events = [
        json.dumps({"type": "system", "subtype": "init", "model": "x"}),
        _errored_tool_result("FAILED test_a"),
        _errored_tool_result("FAILED test_b"),
        _errored_tool_result("FAILED test_c"),
        _errored_tool_result("FAILED test_d"),
        json.dumps({"type": "result", "subtype": "success",
                    "structured_output": {"ok": True}, "is_error": False}),
    ]
    _enable_fake_cgroup(leerie, monkeypatch, (12, 256, 0))  # healthy
    monkeypatch.setattr("asyncio.create_subprocess_exec",
                        _make_subprocess_exec_mock(events))
    result = asyncio.run(leerie._invoke(
        ["claude", "-p", "x"], cwd=str(leerie_dir.parent),
        timeout=60, sid="cfg-002", leerie_dir=leerie_dir,
        verbosity="stream",
        worker_memory_max_bytes=1 << 30, worker_pids_max=256))
    assert result["structured_output"] == {"ok": True}


def test_invoke_sparse_errors_below_window_threshold_do_not_trigger(
        leerie, leerie_dir, monkeypatch):
    """Errors that stay sparse — successes keep pushing them out of the
    window so it never holds ≥3 at once — must NOT trigger, even with an
    exhausted-looking stat stub. Window=6: the pattern err, ok, ok, err,
    ok, ok, err, ok, ok never accumulates 3 errors in any 6-wide window.
    This is the window analogue of "one failing test shouldn't fire."""
    events = [json.dumps({"type": "system", "subtype": "init", "model": "x"})]
    for _ in range(4):
        events.append(_errored_tool_result())
        events.append(_ok_tool_result())
        events.append(_ok_tool_result())
    events.append(json.dumps({"type": "result", "subtype": "success",
                              "structured_output": {"ok": True},
                              "is_error": False}))
    # Stat would confirm exhaustion IF probed — proving the window gate
    # (not the cgroup read) is what holds detection back here.
    _enable_fake_cgroup(leerie, monkeypatch, (256, 256, 99))
    monkeypatch.setattr("asyncio.create_subprocess_exec",
                        _make_subprocess_exec_mock(events))
    result = asyncio.run(leerie._invoke(
        ["claude", "-p", "x"], cwd=str(leerie_dir.parent),
        timeout=60, sid="cfg-002", leerie_dir=leerie_dir,
        verbosity="stream",
        worker_memory_max_bytes=1 << 30, worker_pids_max=256))
    assert result["structured_output"] == {"ok": True}


def test_invoke_stat_none_never_false_detects(leerie, leerie_dir, monkeypatch):
    """When containment is off (_cgroup_stat returns None), the detector is
    inert even under a long run of errors — no early kill."""
    events = [
        json.dumps({"type": "system", "subtype": "init", "model": "x"}),
        _errored_tool_result(), _errored_tool_result(),
        _errored_tool_result(), _errored_tool_result(),
        json.dumps({"type": "result", "subtype": "success",
                    "structured_output": {"ok": True}, "is_error": False}),
    ]
    _enable_fake_cgroup(leerie, monkeypatch, None)
    monkeypatch.setattr("asyncio.create_subprocess_exec",
                        _make_subprocess_exec_mock(events))
    result = asyncio.run(leerie._invoke(
        ["claude", "-p", "x"], cwd=str(leerie_dir.parent),
        timeout=60, sid="cfg-002", leerie_dir=leerie_dir,
        verbosity="stream",
        worker_memory_max_bytes=1 << 30, worker_pids_max=256))
    assert result["structured_output"] == {"ok": True}
