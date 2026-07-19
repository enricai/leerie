"""Incident-shaped end-to-end regression pin for the 2026-07-19 argv
E2BIG crash (see the `_task` note under this repo's leerie run for
subtask test-002 / bugfix-001 / bugfix-002).

A 150,063-byte reconciler user prompt (task 51,142B + subtask_views
88,201B, plus padding to the incident's exact measured overflow size)
raised `OSError: [Errno 7] Argument list too long` at `execve` time,
because it was a single argv element exceeding Linux's `MAX_ARG_STRLEN`
(131,071 bytes = PAGE_SIZE * 32 — a per-argument ceiling, not raisable,
and distinct from the larger aggregate `ARG_MAX`).

`test_prompt_over_stdin.py` and `test_append_system_prompt_file.py`
already pin each half of the fix (stdin transport, and the
`--append-system-prompt-file` probe + inline fallback) in isolation.
This file is the "conceptually dominant" incident-shaped regression the
planner scoped as its own subtask: one suite that replays the full
150,063-byte reconciler-shaped payload — together with a ~25KB
reconciler.md-shaped appended system prompt and the inline JSON
schema — through `claude_p`'s real `build()` closure and asserts on the
combined, end-to-end transport contract the incident write-up demands:

  - no positional prompt anywhere on argv, at any payload size
  - the prompt reaches the child via stdin, not argv
  - `--append-system-prompt-file` is used when the probe reports
    supported, with the inline `--append-system-prompt` fallback path
    exercised separately
  - the schema stays inline (`--json-schema`, never a `@file` form)
  - a stubbed spawn fed the full payload does not raise E2BIG and does
    not deadlock (bounded via a concurrent feeder)

Does not cover the coverage-gate (root cause A) or dep_capture budget
sites — those are separate subtasks.
"""
from __future__ import annotations

import asyncio
import json
import pathlib
import types
import unittest.mock as mock

import pytest

# Linux's per-argument ceiling (PAGE_SIZE * 32 on a 4KB-page kernel).
# Not a leerie constant — the external fact the fix routes around,
# measured directly in the incident's Colima VM.
MAX_ARG_STRLEN = 131_071

# The incident's exact measured shape (see the `_task` note's "Root
# cause B" table): task embedded whole (51,142B) + subtask_views at
# indent=2 (88,201B), padded to the measured total overflow (150,063B).
_INCIDENT_TASK_BYTES = 51_142
_INCIDENT_SUBTASK_VIEWS_BYTES = 88_201
_INCIDENT_TOTAL_BYTES = 150_063

# reconciler.md is ~25KB (the incident's measured appended-system-prompt
# size that compounds with the user prompt toward the ceiling).
_RECONCILER_SYSTEM_PROMPT_BYTES = 25_128


def _incident_shaped_user_prompt() -> str:
    task = "T" * _INCIDENT_TASK_BYTES
    subtask_views = "V" * _INCIDENT_SUBTASK_VIEWS_BYTES
    prompt = task + subtask_views
    prompt += "P" * (_INCIDENT_TOTAL_BYTES - len(prompt))
    assert len(prompt.encode()) == _INCIDENT_TOTAL_BYTES
    return prompt


def _incident_shaped_system_prompt() -> str:
    return "S" * _RECONCILER_SYSTEM_PROMPT_BYTES


def _make_state():
    return types.SimpleNamespace(
        path=pathlib.Path("/tmp/leerie-test-nonexistent/state.json"),
        run_dir=pathlib.Path("/tmp/leerie-test-nonexistent"),
        data={"verbosity": "quiet"}, run_id="r1",
        bump_workers=lambda *a, **k: None,
        add_telemetry=lambda *a, **k: None,
    )


def _run_claude_p_capturing(leerie, user_prompt: str, system_prompt: str):
    """Drive claude_p's real build() closure through a stubbed _invoke,
    capturing the constructed argv and stdin payload — mirrors
    test_no_result_event_retry.py's stubbed-_invoke pattern (no live
    `claude` binary needed for the command-vector assertions)."""
    captured: dict = {}

    async def fake_invoke(cmd, cwd, timeout, sid, leerie_dir, verbosity,
                          stdin_data=None, **_kw):
        captured["cmd"] = list(cmd)
        captured["stdin_data"] = stdin_data
        if "--append-system-prompt-file" in cmd:
            idx = cmd.index("--append-system-prompt-file") + 1
            captured["system_prompt_file"] = cmd[idx]
            captured["system_prompt_file_contents"] = (
                pathlib.Path(cmd[idx]).read_text())
        return {"type": "result", "subtype": "success", "is_error": False,
                "result": "{}", "structured_output": {"categories": []}}

    with mock.patch.object(leerie, "_invoke", fake_invoke), \
         mock.patch.object(leerie, "_capture_call", lambda *a, **k: None):
        asyncio.run(leerie.claude_p(
            user_prompt, system_prompt,
            schema_key="reconciler", cwd="/work",
            allowed_tools="Read", max_turns=40, autonomous=False,
            caps=dict(leerie.DEFAULT_CAPS), st=_make_state(), model="opus",
            sid="incident-2026-07-19",
        ))
    return captured


# ---------------------------------------------------------------------------
# The full incident-shaped payload through the real build() closure
# ---------------------------------------------------------------------------

class TestIncidentShapedTransport:
    """Replays the exact 150,063-byte reconciler payload (+ ~25KB
    appended system prompt) that crashed the real run, and asserts the
    combined transport contract holds end to end."""

    def test_no_positional_prompt_on_argv_at_incident_scale(self, leerie,
                                                             monkeypatch):
        monkeypatch.setattr(
            leerie, "_append_system_prompt_file_supported", lambda: True)
        user_prompt = _incident_shaped_user_prompt()
        system_prompt = _incident_shaped_system_prompt()

        captured = _run_claude_p_capturing(leerie, user_prompt, system_prompt)
        cmd = captured["cmd"]

        assert cmd[0] == "claude"
        assert cmd[1] == "-p"
        assert cmd[2].startswith("--"), (
            f"expected a flag immediately after -p, got positional "
            f"{cmd[2]!r} — a positional prompt silently wins over stdin "
            "with no error (incident-verified)")
        assert not any(user_prompt in elem for elem in cmd), (
            "the incident-shaped user prompt must not appear anywhere "
            "on argv")

    def test_no_argv_element_exceeds_max_arg_strlen(self, leerie,
                                                     monkeypatch):
        """The load-bearing property: with the full incident payload
        (150,063B user prompt + ~25KB system prompt + inline schema),
        no single argv element claude_p constructs exceeds
        MAX_ARG_STRLEN. Before the fix, the user prompt alone was one
        argv element and overflowed it by ~19KB; the appended system
        prompt was a second, smaller-but-still-large element."""
        monkeypatch.setattr(
            leerie, "_append_system_prompt_file_supported", lambda: True)
        user_prompt = _incident_shaped_user_prompt()
        system_prompt = _incident_shaped_system_prompt()

        captured = _run_claude_p_capturing(leerie, user_prompt, system_prompt)

        for i, elem in enumerate(captured["cmd"]):
            assert len(elem.encode()) <= MAX_ARG_STRLEN, (
                f"argv element {i} ({elem[:80]!r}...) is "
                f"{len(elem.encode())} bytes, exceeding MAX_ARG_STRLEN "
                f"({MAX_ARG_STRLEN}) at incident scale")

    def test_prompt_reaches_child_via_stdin(self, leerie, monkeypatch):
        monkeypatch.setattr(
            leerie, "_append_system_prompt_file_supported", lambda: True)
        user_prompt = _incident_shaped_user_prompt()
        system_prompt = _incident_shaped_system_prompt()

        captured = _run_claude_p_capturing(leerie, user_prompt, system_prompt)
        assert captured["stdin_data"] == user_prompt

    def test_append_system_prompt_file_used_when_probe_supported(
            self, leerie, monkeypatch):
        monkeypatch.setattr(
            leerie, "_append_system_prompt_file_supported", lambda: True)
        user_prompt = _incident_shaped_user_prompt()
        system_prompt = _incident_shaped_system_prompt()

        captured = _run_claude_p_capturing(leerie, user_prompt, system_prompt)
        cmd = captured["cmd"]

        assert "--append-system-prompt-file" in cmd
        assert "--append-system-prompt" not in cmd
        assert captured["system_prompt_file_contents"] == system_prompt
        for i, elem in enumerate(cmd):
            assert len(elem.encode()) <= MAX_ARG_STRLEN, (
                f"argv element {i} is {len(elem.encode())} bytes even "
                "though the system prompt was routed to a file, not argv")

    def test_inline_fallback_used_when_probe_unsupported(self, leerie,
                                                          monkeypatch):
        """When the undocumented --append-system-prompt-file flag isn't
        recognized by the installed CLI, the fallback still carries the
        full ~25KB reconciler-shaped system prompt inline on argv — that
        element alone stays well under MAX_ARG_STRLEN, so the fallback
        path is safe even though it differs from the file-flag path."""
        monkeypatch.setattr(
            leerie, "_append_system_prompt_file_supported", lambda: False)
        user_prompt = _incident_shaped_user_prompt()
        system_prompt = _incident_shaped_system_prompt()

        captured = _run_claude_p_capturing(leerie, user_prompt, system_prompt)
        cmd = captured["cmd"]

        assert "--append-system-prompt-file" not in cmd
        assert "--append-system-prompt" in cmd
        idx = cmd.index("--append-system-prompt") + 1
        assert cmd[idx] == system_prompt
        assert len(system_prompt.encode()) <= MAX_ARG_STRLEN
        # The user prompt still must not be on argv even though the
        # system prompt fell back to the inline flag.
        assert captured["stdin_data"] == user_prompt
        assert not any(user_prompt in elem for elem in cmd)

    def test_schema_stays_inline(self, leerie, monkeypatch):
        """No --json-schema-file / no @file form: the schema is bounded
        (SCHEMAS is a static dict) and the live CLI has no file-based
        schema flag, so it always travels as --json-schema <inline
        json>."""
        monkeypatch.setattr(
            leerie, "_append_system_prompt_file_supported", lambda: True)
        user_prompt = _incident_shaped_user_prompt()
        system_prompt = _incident_shaped_system_prompt()

        captured = _run_claude_p_capturing(leerie, user_prompt, system_prompt)
        cmd = captured["cmd"]

        assert "--json-schema" in cmd
        assert "--json-schema-file" not in cmd
        assert not any(elem.startswith("@") for elem in cmd)
        idx = cmd.index("--json-schema") + 1
        parsed = json.loads(cmd[idx])
        assert parsed == leerie.SCHEMAS["reconciler"]


# ---------------------------------------------------------------------------
# Stubbed spawn: the incident payload must not raise E2BIG and must not
# deadlock, exercised through _invoke's real asyncio spawn + concurrent
# feeder against a stubbed asyncio.create_subprocess_exec (no live
# `claude` binary required).
# ---------------------------------------------------------------------------

class _MockStream:
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


class _MockStdinWriter:
    def __init__(self):
        self.written = b""
        self.closed = False

    def write(self, data: bytes):
        self.written += data

    async def drain(self):
        pass

    def close(self):
        self.closed = True


class _MockProc:
    def __init__(self, stdout_lines, returncode: int = 0):
        self.stdout = _MockStream(stdout_lines)
        self.stderr = _MockStream([])
        self.returncode = returncode
        self.pid = 999_999_997
        self.stdin = _MockStdinWriter()

    def kill(self):
        pass

    async def wait(self):
        return self.returncode


@pytest.fixture
def leerie_dir(tmp_path):
    cd = tmp_path / ".leerie"
    cd.mkdir()
    (cd / "logs").mkdir()
    return cd


def test_stubbed_spawn_does_not_raise_e2big_at_incident_scale(
        leerie, leerie_dir, monkeypatch):
    """A stubbed asyncio.create_subprocess_exec receiving the full
    150,063-byte incident payload over stdin_data must complete
    normally — no OSError/E2BIG (which, pre-fix, would have come from
    the real execve call inside create_subprocess_exec when the prompt
    was still a positional argv element)."""
    user_prompt = _incident_shaped_user_prompt()

    async def fake(*cmd, **kwargs):
        for elem in cmd:
            assert len(str(elem).encode()) <= MAX_ARG_STRLEN
        events = [json.dumps({"type": "result", "subtype": "success",
                              "is_error": False, "result": "{}"})]
        return _MockProc(events)

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake)

    envelope = asyncio.run(leerie._invoke(
        ["claude", "-p", "--append-system-prompt-file", "/tmp/sp.txt",
         "--json-schema", "{}"],
        cwd=str(leerie_dir.parent), timeout=30, sid="incident-stub",
        leerie_dir=leerie_dir, verbosity="quiet", stdin_data=user_prompt))

    assert envelope["type"] == "result"
    assert envelope["is_error"] is False


def test_stubbed_spawn_feeds_full_payload_without_deadlock(
        leerie, leerie_dir, monkeypatch):
    """The concurrent feeder writes the entire incident-scale payload
    and closes stdin (EOF) rather than blocking on a write-then-read
    ordering — asserted by inspecting the mock's captured stdin."""
    user_prompt = _incident_shaped_user_prompt()
    proc_holder: dict = {}

    async def fake(*cmd, **kwargs):
        events = [json.dumps({"type": "result", "subtype": "success",
                              "is_error": False, "result": "{}"})]
        proc = _MockProc(events)
        proc_holder["proc"] = proc
        return proc

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake)

    asyncio.run(leerie._invoke(
        ["claude", "-p"], cwd=str(leerie_dir.parent), timeout=30,
        sid="incident-feed", leerie_dir=leerie_dir, verbosity="quiet",
        stdin_data=user_prompt))

    proc = proc_holder["proc"]
    assert proc.stdin.written == user_prompt.encode()
    assert proc.stdin.closed is True


def test_real_subprocess_incident_payload_no_deadlock(leerie, leerie_dir):
    """End-to-end against a REAL child process over a REAL OS pipe: the
    exact 150,063-byte incident payload is delivered and read back
    without hanging, bounded by _invoke's own asyncio.wait_for timeout
    rather than relying on pytest-level enforcement — if the concurrent
    feeder/reader pairing deadlocked, this test would hang instead of
    failing cleanly."""
    import sys

    user_prompt = _incident_shaped_user_prompt()
    echo_child = (
        "import sys, json\n"
        "data = sys.stdin.buffer.read()\n"
        "print(json.dumps({'type': 'result', 'subtype': 'success', "
        "'is_error': False, 'result': str(len(data)), "
        "'structured_output': {'nbytes': len(data)}}), flush=True)\n"
    )
    cmd = [sys.executable, "-c", echo_child]

    envelope = asyncio.run(leerie._invoke(
        cmd, cwd=str(leerie_dir.parent), timeout=30, sid="incident-e2e",
        leerie_dir=leerie_dir, verbosity="quiet", stdin_data=user_prompt))

    assert envelope["type"] == "result"
    assert envelope["is_error"] is False
    assert envelope["structured_output"]["nbytes"] == len(
        user_prompt.encode())
