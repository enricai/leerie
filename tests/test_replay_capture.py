"""Tests for replay_capture() — the primitive for judge and heal-loop replays.

Covers:
  - Arguments passed to claude_p match the captured record's fields.
  - override_system_prompt replaces system_prompt end-to-end.
  - A replay call does not write to any calls.ndjson (no capture pollution).
  - Return value is (envelope, structured_output) 2-tuple.
  - replay_capture is importable from the leerie module.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest


# ---------------------------------------------------------------------------
# fixtures / helpers
# ---------------------------------------------------------------------------

_GOOD_ENVELOPE = {
    "type": "result",
    "subtype": "success",
    "num_turns": 2,
    "total_cost_usd": 0.005,
    "is_error": False,
    "terminal_reason": "completed",
    "result": '{"categories": ["bug-fixing"]}',
    "structured_output": {"categories": ["bug-fixing"]},
    "usage": {"input_tokens": 200, "output_tokens": 50},
}

_CAPTURE_RECORD = {
    "call_id": "aaaabbbb-cccc-dddd-eeee-ffffffffffff",
    "run_id": "fix-some-bug-abc123",
    "call_type": "classifier",
    "model": "opus",
    "system_prompt": "You are the original classifier system prompt.",
    "user_content": "TASK:\nFix the login bug.\n\nClassify it.",
    "response_content": '{"categories": ["bug-fixing"]}',
    "parsed_ok": True,
    "input_tokens": 200,
    "output_tokens": 50,
    "latency_ms": 1234,
    "success": True,
    "ts": "2026-01-01T00:00:00.000Z",
}


def _stub_invoke(leerie, monkeypatch, envelope=_GOOD_ENVELOPE):
    """Patch leerie._invoke to return envelope; return captured call_args list."""
    captured = []

    async def fake_invoke(cmd, cwd, timeout, sid, leerie_dir, verbosity,
                          progress=None, stdin_data=None, **_kw):
        captured.append({"cmd": cmd, "cwd": cwd, "stdin_data": stdin_data})
        return envelope

    monkeypatch.setattr(leerie, "_invoke", fake_invoke)
    return captured


# ---------------------------------------------------------------------------
# Criterion 1: args match capture fields
# ---------------------------------------------------------------------------

def test_args_match_capture_fields(leerie, tmp_path, monkeypatch):
    """replay_capture passes system_prompt, user_content, call_type→schema_key,
    and model from the capture record through to claude_p / _invoke.

    The appended-system-prompt flag (--append-system-prompt vs. the
    probe-gated --append-system-prompt-file, see
    tests/test_append_system_prompt_file.py) is pinned to the inline
    form here via monkeypatch, so this test's assertions are independent
    of whether the live `claude` CLI on the test host happens to support
    the undocumented file flag."""
    monkeypatch.setattr(
        leerie, "_append_system_prompt_file_supported", lambda: False)
    collected_cmd: list[list[str]] = []
    collected_stdin: list[str | None] = []

    async def fake_invoke(cmd, cwd, timeout, sid, leerie_dir, verbosity,
                          progress=None, stdin_data=None, **_kw):
        collected_cmd.append(list(cmd))
        collected_stdin.append(stdin_data)
        return _GOOD_ENVELOPE

    monkeypatch.setattr(leerie, "_invoke", fake_invoke)

    asyncio.run(leerie.replay_capture(_CAPTURE_RECORD))

    assert collected_cmd, "fake_invoke was never called"
    cmd = collected_cmd[0]

    # user_content is fed over stdin, not argv (see build()'s comment on
    # why the prompt was moved off a positional argv element).
    assert cmd[0] == "claude"
    assert cmd[1] == "-p"
    assert "--append-system-prompt" == cmd[2], (
        "no positional prompt element should follow -p; got: "
        f"{cmd[:4]!r}")
    user_arg = collected_stdin[0]
    assert user_arg is not None
    assert "Fix the login bug" in user_arg, (
        f"user_content not in stdin_data: {user_arg!r}")

    # system_prompt is passed via --append-system-prompt
    assert "--append-system-prompt" in cmd
    sys_idx = cmd.index("--append-system-prompt")
    assert cmd[sys_idx + 1] == "You are the original classifier system prompt."

    # model is passed via --model
    assert "--model" in cmd
    model_idx = cmd.index("--model")
    assert cmd[model_idx + 1] == "opus"

    # schema_key → --json-schema must embed the classifier schema
    assert "--json-schema" in cmd
    schema_idx = cmd.index("--json-schema")
    schema_str = cmd[schema_idx + 1]
    schema = json.loads(schema_str)
    # classifier schema has "categories" property
    assert "categories" in schema.get("properties", {}), (
        f"schema_key 'classifier' not reflected in --json-schema: {schema_str}")


# ---------------------------------------------------------------------------
# Criterion 2: override_system_prompt is plumbed through
# ---------------------------------------------------------------------------

def test_override_system_prompt(leerie, tmp_path, monkeypatch):
    """When override_system_prompt is supplied, it replaces the captured
    system_prompt in the invocation. Pinned to the inline
    --append-system-prompt flag (see test_args_match_capture_fields'
    docstring for why)."""
    monkeypatch.setattr(
        leerie, "_append_system_prompt_file_supported", lambda: False)
    collected_cmd: list[list[str]] = []

    async def fake_invoke(cmd, cwd, timeout, sid, leerie_dir, verbosity,
                          progress=None, **_kw):
        collected_cmd.append(list(cmd))
        return _GOOD_ENVELOPE

    monkeypatch.setattr(leerie, "_invoke", fake_invoke)

    override = "PATCHED: use a different classifier strategy."
    asyncio.run(leerie.replay_capture(
        _CAPTURE_RECORD,
        override_system_prompt=override,
    ))

    assert collected_cmd, "fake_invoke was never called"
    cmd = collected_cmd[0]

    assert "--append-system-prompt" in cmd
    sys_idx = cmd.index("--append-system-prompt")
    actual_sys = cmd[sys_idx + 1]
    assert actual_sys == override, (
        f"override_system_prompt not plumbed through; got: {actual_sys!r}")
    # Original prompt must NOT appear
    assert "You are the original classifier system prompt." not in actual_sys


# ---------------------------------------------------------------------------
# Criterion 3: no calls.ndjson written (no capture pollution)
# ---------------------------------------------------------------------------

def test_replay_does_not_pollute_captures(leerie, tmp_path, monkeypatch):
    """replay_capture must not write to any calls.ndjson file — replays must
    not pollute the captures stream."""

    async def fake_invoke(cmd, cwd, timeout, sid, leerie_dir, verbosity,
                          progress=None, **_kw):
        return _GOOD_ENVELOPE

    monkeypatch.setattr(leerie, "_invoke", fake_invoke)

    # Run replay with cwd set to tmp_path so if any files are written they
    # land there where we can detect them.
    asyncio.run(leerie.replay_capture(
        _CAPTURE_RECORD,
        cwd=str(tmp_path),
    ))

    # No calls.ndjson anywhere under tmp_path
    ndjson_files = list(tmp_path.rglob("calls.ndjson"))
    assert not ndjson_files, (
        f"calls.ndjson was written during replay: {ndjson_files}")


def test_replay_does_not_modify_existing_capture_file(leerie, tmp_path,
                                                       monkeypatch):
    """If a calls.ndjson already exists (from a prior live run), replay must
    leave it unmodified."""
    existing = tmp_path / "calls.ndjson"
    original_content = '{"call_id":"existing"}\n'
    existing.write_text(original_content)

    async def fake_invoke(cmd, cwd, timeout, sid, leerie_dir, verbosity,
                          progress=None, **_kw):
        return _GOOD_ENVELOPE

    monkeypatch.setattr(leerie, "_invoke", fake_invoke)

    asyncio.run(leerie.replay_capture(
        _CAPTURE_RECORD,
        cwd=str(tmp_path),
    ))

    assert existing.read_text() == original_content, (
        "replay_capture modified an existing calls.ndjson")


# ---------------------------------------------------------------------------
# Criterion 4: return value shape
# ---------------------------------------------------------------------------

def test_return_value_shape(leerie, tmp_path, monkeypatch):
    """replay_capture returns a 2-tuple (envelope, structured_output)."""

    async def fake_invoke(cmd, cwd, timeout, sid, leerie_dir, verbosity,
                          progress=None, **_kw):
        return _GOOD_ENVELOPE

    monkeypatch.setattr(leerie, "_invoke", fake_invoke)

    result = asyncio.run(leerie.replay_capture(_CAPTURE_RECORD))

    assert isinstance(result, tuple), f"Expected tuple, got {type(result)}"
    assert len(result) == 2, f"Expected 2-tuple, got {len(result)}-tuple"

    envelope, structured_output = result
    assert isinstance(envelope, dict), "First element (envelope) must be a dict"
    assert isinstance(structured_output, dict), (
        "Second element (structured_output) must be a dict")

    # structured_output matches the envelope's structured_output field
    assert structured_output == _GOOD_ENVELOPE["structured_output"]

    # envelope has the expected keys from the fake invocation
    assert envelope.get("type") == "result"
    assert envelope.get("is_error") is False


# ---------------------------------------------------------------------------
# Criterion 5: importable from leerie module
# ---------------------------------------------------------------------------

def test_replay_capture_importable(leerie):
    """replay_capture must be a top-level name in the leerie module."""
    assert hasattr(leerie, "replay_capture"), (
        "replay_capture is not defined in orchestrator/leerie.py")
    assert callable(leerie.replay_capture), (
        "replay_capture is not callable")
