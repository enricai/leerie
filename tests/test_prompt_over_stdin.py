"""Tests for feeding the worker prompt over stdin instead of argv.

Incident 2026-07-19 (see bugfix-001's subtask spec / the incident note
under `_task` in the run's plan): a 150,063-byte reconciler user prompt
raised `OSError: [Errno 7] Argument list too long` at `execve` time.
Linux's `MAX_ARG_STRLEN` (131,071 bytes = PAGE_SIZE*32) caps a single
argv element and is not raisable, independent of the larger aggregate
`ARG_MAX` — so no amount of trimming other flags would have helped.

Covers:
  - `build()` never puts the user prompt on argv, at any payload size
    (the argv-length property: no element can exceed MAX_ARG_STRLEN
    for a 150KB+ payload, because none of them carry it at all).
  - `_invoke` spawns with an already-readable temp FILE as stdin when
    `stdin_data` is given, and DEVNULL when it is not (smoke-test /
    direct-cmd callers unaffected). A file rather than a pipe because the
    CLI drops its stdin listener 3 s after spawn, which made a pipe a race
    against the orchestrator's event loop — measured 20/20 prompts lost
    under 400 ms bursts of synchronous work on that loop, 0/20 with a file
    (and 0/20 for a file at 10x CPU oversubscription). The file is staged
    before the spawn and unlinked after the call.
  - The full payload reaches a REAL child process with no deadlock, for a
    payload well over the 131,071-byte single-argv ceiling this fix exists
    to route around.
  - `claude_p` end-to-end (via a stubbed `_invoke`) sends a 150KB+
    prompt through as `stdin_data`, not as part of `cmd`.
  - The reconciler's size-gate retry prompt (`_build_size_retry_prompt`,
    which re-appends `original_user_prompt` verbatim on top of
    per-offender sections — strictly larger than the payload that
    already overflowed) clears the same argv-length property when fed
    into `claude_p` as `user_prompt`.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

import pytest

# Linux's per-argument ceiling (PAGE_SIZE * 32 on a 4KB-page kernel).
# Not a leerie constant — this is the external fact the fix routes
# around, measured directly in the incident's Colima VM (see the
# subtask's investigation_notes).
MAX_ARG_STRLEN = 131_071


# ---------------------------------------------------------------------------
# build(): the argv-length property
# ---------------------------------------------------------------------------

def _build_cmd(leerie, user_prompt: str, extra_user: str = "") -> list[str]:
    """Reconstruct claude_p's build() closure by calling claude_p with a
    stubbed _invoke that captures the cmd it was given, mirroring
    test_replay_capture.py's approach (build() is a local closure, not a
    module-level function, so it can't be imported directly)."""
    captured: dict = {}

    async def fake_invoke(cmd, cwd, timeout, sid, leerie_dir, verbosity,
                          stdin_data=None, **_kw):
        captured["cmd"] = list(cmd)
        captured["stdin_data"] = stdin_data
        return {"type": "result", "subtype": "success", "is_error": False,
                "result": "{}", "structured_output": {"categories": []}}

    import pathlib
    import types
    st = types.SimpleNamespace(
        path=pathlib.Path("/tmp/leerie-test-nonexistent/state.json"),
        run_dir=pathlib.Path("/tmp/leerie-test-nonexistent"),
        data={"verbosity": "quiet"}, run_id="r1",
        bump_workers=lambda *a, **k: None,
        add_telemetry=lambda *a, **k: None,
    )

    import unittest.mock as mock
    with mock.patch.object(leerie, "_invoke", fake_invoke), \
         mock.patch.object(leerie, "_capture_call", lambda *a, **k: None):
        asyncio.run(leerie.claude_p(
            user_prompt, "system prompt here",
            schema_key="classifier", cwd="/work",
            allowed_tools="Read", max_turns=40, autonomous=False,
            caps=dict(leerie.DEFAULT_CAPS), st=st, model="opus",
            sid="argv-test",
        ))
    return captured["cmd"], captured["stdin_data"]


def test_no_argv_element_exceeds_max_arg_strlen_for_150kb_prompt(leerie):
    """The load-bearing property: for a 150KB+ user_prompt (the incident's
    measured overflow size), no single argv element claude_p constructs
    exceeds MAX_ARG_STRLEN. Before the fix, the prompt itself was one
    argv element and blew past it by ~19KB on its own."""
    task = "x" * 51_142           # incident's measured task size
    subtask_views_shaped = "y" * 88_201  # incident's measured payload share
    user_prompt = task + subtask_views_shaped  # 139,343 bytes, still < 150,063
    # Pad to match the incident's exact overflow size.
    user_prompt = user_prompt + "z" * (150_063 - len(user_prompt))
    assert len(user_prompt.encode()) == 150_063

    cmd, stdin_data = _build_cmd(leerie, user_prompt)

    for i, elem in enumerate(cmd):
        assert len(elem.encode()) <= MAX_ARG_STRLEN, (
            f"argv element {i} ({elem[:80]!r}...) is "
            f"{len(elem.encode())} bytes, exceeding MAX_ARG_STRLEN "
            f"({MAX_ARG_STRLEN}) — a 150KB+ prompt must never land on argv")

    # And the prompt reached the child via stdin_data, not argv.
    assert stdin_data == user_prompt
    assert not any(user_prompt in elem for elem in cmd), (
        "the user prompt must not appear anywhere in argv")


def test_size_retry_prompt_stays_under_argv_ceiling(leerie):
    """`_build_size_retry_prompt` (the reconciler's size-gate retry
    builder, `orchestrator/leerie.py:14133`) re-appends
    `original_user_prompt` verbatim on top of per-offender sections —
    strictly larger than the input that already overflowed. It is fed
    into `claude_p` the same way any other `user_prompt` is (via
    `_spawn_reconciler`), so it must clear the same argv-length property
    as `test_no_argv_element_exceeds_max_arg_strlen_for_150kb_prompt`
    even though its payload is larger than the original prompt alone."""
    original_user_prompt = "z" * 150_063  # the incident's exact overflow size
    assert len(original_user_prompt.encode()) > MAX_ARG_STRLEN

    oversized = [{
        "id": "feat-100",
        "title": "Bundled foundation",
        "intent": "bundle 3 capabilities",
        "provides": ["cap-a", "cap-b", "cap-c"],
        "requires": [{"tag": "some-dep", "extent": "in_plan"}],
        "depends_on": ["feat-001"],
        "size": "large",
    }]
    size_retry_prompt = leerie._build_size_retry_prompt(
        oversized, original_user_prompt)
    # Sanity: the retry prompt is strictly larger than the original
    # prompt alone (the incident's core claim about this site).
    assert len(size_retry_prompt.encode()) > len(original_user_prompt.encode())

    cmd, stdin_data = _build_cmd(leerie, size_retry_prompt)

    for i, elem in enumerate(cmd):
        assert len(elem.encode()) <= MAX_ARG_STRLEN, (
            f"argv element {i} ({elem[:80]!r}...) is "
            f"{len(elem.encode())} bytes, exceeding MAX_ARG_STRLEN "
            f"({MAX_ARG_STRLEN}) — the size-retry prompt must never "
            "land on argv, even though it re-appends the original "
            "prompt on top of per-offender sections")

    assert stdin_data == size_retry_prompt
    assert not any(original_user_prompt in elem for elem in cmd), (
        "the re-appended original_user_prompt must not appear anywhere "
        "in argv")


def test_no_positional_prompt_after_dash_p(leerie):
    """A positional prompt silently wins over stdin with no error or
    warning (incident-verified). The positional must be ABSENT, not
    merely redundant — build() must emit `-p` with no value following
    it (the next element is always a flag)."""
    cmd, _ = _build_cmd(leerie, "small prompt")
    assert cmd[0] == "claude"
    assert cmd[1] == "-p"
    assert cmd[2].startswith("--"), (
        f"expected a flag immediately after -p, got positional {cmd[2]!r} — "
        "a positional prompt silently wins over stdin with no error")


def test_retry_note_reaches_stdin_not_argv(leerie):
    """The retry path (claude_p's schema-mismatch / no-result retry,
    which calls build(retry_note)) must concatenate retry_note into the
    stdin payload, not argv — same contract as the first attempt."""
    captured_stdins: list[str] = []

    async def fake_invoke(cmd, cwd, timeout, sid, leerie_dir, verbosity,
                          stdin_data=None, **_kw):
        captured_stdins.append(stdin_data)
        for elem in cmd:
            assert "ORIGINAL PROMPT" not in elem
            assert "RETRY NOTE" not in elem
        if len(captured_stdins) == 1:
            return {"type": "result", "is_error": False, "result": "{}",
                    "structured_output": None}  # forces a retry
        return {"type": "result", "is_error": False, "result": "{}",
                "structured_output": {"categories": []}}

    import types
    import pathlib
    st = types.SimpleNamespace(
        path=pathlib.Path("/tmp/leerie-test-nonexistent/state.json"),
        run_dir=pathlib.Path("/tmp/leerie-test-nonexistent"),
        data={"verbosity": "quiet"}, run_id="r1",
        bump_workers=lambda *a, **k: None,
        add_telemetry=lambda *a, **k: None,
    )

    import unittest.mock as mock
    with mock.patch.object(leerie, "_invoke", fake_invoke), \
         mock.patch.object(leerie, "_capture_call", lambda *a, **k: None):
        asyncio.run(leerie.claude_p(
            "ORIGINAL PROMPT", "sys",
            schema_key="classifier", cwd="/work",
            allowed_tools="Read", max_turns=40, autonomous=False,
            caps=dict(leerie.DEFAULT_CAPS), st=st, model="opus",
            sid="retry-test",
        ))

    assert len(captured_stdins) == 2
    assert captured_stdins[0] == "ORIGINAL PROMPT"
    assert "ORIGINAL PROMPT" in captured_stdins[1]
    assert "conforms exactly to the required schema" in captured_stdins[1]


# ---------------------------------------------------------------------------
# _invoke(): stdin=PIPE vs DEVNULL
# ---------------------------------------------------------------------------

_MOCK_PID_SENTINEL = 999_999_998


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
    def __init__(self, stdout_lines: list[str], returncode: int = 0,
                 with_stdin: bool = False):
        self.stdout = _MockStream(stdout_lines)
        self.stderr = _MockStream([])
        self.returncode = returncode
        self.pid = _MOCK_PID_SENTINEL
        self.stdin = _MockStdinWriter() if with_stdin else None

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


def test_invoke_passes_a_readable_file_when_stdin_data_given(
        leerie, leerie_dir, monkeypatch):
    """The prompt must reach the child as an ALREADY-READABLE file, not a
    pipe someone still has to write to.

    The CLI drops its stdin listener 3 s after spawn
    (`r6r(process.stdin,3000)`), so a pipe makes delivery a race against
    the orchestrator's event loop — measured 20/20 prompts lost under
    400 ms bursts of synchronous work on that loop. A regular file has the
    payload on disk before the child exists, so there is no writer left to
    schedule late and no deadline to lose.
    """
    captured: dict = {}

    async def fake(*cmd, **kwargs):
        handed = kwargs.get("stdin")
        captured["is_pipe"] = handed is asyncio.subprocess.PIPE
        captured["readable"] = hasattr(handed, "read")
        # Read it HERE, at spawn time. The payload must already be on disk
        # and positioned at byte 0 — that is the whole guarantee, and it is
        # only observable before `_invoke`'s cleanup closes the file.
        if captured["readable"]:
            handed.seek(0)
            captured["content"] = handed.read()
        events = [json.dumps({"type": "result", "subtype": "success",
                              "is_error": False})]
        return _MockProc(events, with_stdin=False)

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake)
    payload = "hello prompt"
    asyncio.run(leerie._invoke(
        ["claude", "-p"], cwd=str(leerie_dir.parent),
        timeout=60, sid="t-file", leerie_dir=leerie_dir,
        verbosity="quiet", stdin_data=payload))

    assert not captured["is_pipe"], (
        "_invoke must NOT hand the child a pipe — that reintroduces the "
        "race the file transport exists to remove")
    assert captured["readable"], "expected a readable file object as stdin"
    assert captured["content"] == payload.encode()


def test_invoke_still_uses_devnull_when_no_stdin_data(leerie, leerie_dir,
                                                       monkeypatch):
    """Direct-cmd callers with no prompt to feed (e.g. the preflight smoke
    test) keep the pre-fix DEVNULL behavior unchanged."""
    captured: dict = {}

    async def fake(*cmd, **kwargs):
        captured.update(kwargs)
        events = [json.dumps({"type": "result", "subtype": "success",
                              "is_error": False})]
        return _MockProc(events, with_stdin=False)

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake)
    asyncio.run(leerie._invoke(
        ["claude", "-p", "respond ok"], cwd=str(leerie_dir.parent),
        timeout=60, sid="t-devnull", leerie_dir=leerie_dir,
        verbosity="quiet"))

    assert captured.get("stdin") == asyncio.subprocess.DEVNULL


def test_full_payload_is_on_disk_before_the_child_exists(
        leerie, leerie_dir, monkeypatch):
    """The ENTIRE payload must be readable at the moment of spawn.

    A partial write would recreate the failure in a subtler form: the CLI
    starts on an incomplete prompt instead of dying visibly. Asserting at
    spawn time (inside the fake, before `_invoke` proceeds) is the point —
    checking afterwards cannot distinguish "written before exec" from
    "written during the run", which is the whole distinction here.
    """
    seen: dict = {}

    async def fake(*cmd, **kwargs):
        handed = kwargs.get("stdin")
        handed.seek(0)
        seen["at_spawn"] = handed.read()
        events = [json.dumps({"type": "result", "subtype": "success",
                              "is_error": False})]
        return _MockProc(events, with_stdin=False)

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake)
    payload = "p" * 150_063  # the incident's exact overflow size
    asyncio.run(leerie._invoke(
        ["claude", "-p"], cwd=str(leerie_dir.parent),
        timeout=60, sid="t-feed", leerie_dir=leerie_dir,
        verbosity="quiet", stdin_data=payload))

    assert seen["at_spawn"] == payload.encode()


def test_prompt_file_is_cleaned_up(leerie, leerie_dir, monkeypatch):
    """The staged file must not outlive the call — a leak here would
    accumulate one 100KB+ file per worker invocation across a run."""
    paths: list = []

    async def fake(*cmd, **kwargs):
        paths.append(kwargs["stdin"].name)
        events = [json.dumps({"type": "result", "subtype": "success",
                              "is_error": False})]
        return _MockProc(events, with_stdin=False)

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake)
    asyncio.run(leerie._invoke(
        ["claude", "-p"], cwd=str(leerie_dir.parent),
        timeout=60, sid="t-clean", leerie_dir=leerie_dir,
        verbosity="quiet", stdin_data="hello"))

    assert paths and not os.path.exists(paths[0]), (
        f"staged prompt file {paths[0]} survived the call")


# ---------------------------------------------------------------------------
# End-to-end against a REAL child process over a REAL OS pipe: proves no
# deadlock for a payload well over MAX_ARG_STRLEN, with a concurrent
# feeder (not write-then-read).
# ---------------------------------------------------------------------------

_ECHO_CHILD = (
    "import sys, json\n"
    "data = sys.stdin.buffer.read()\n"
    "print(json.dumps({'type': 'result', 'subtype': 'success', "
    "'is_error': False, 'result': str(len(data)), "
    "'structured_output': {'nbytes': len(data)}}), flush=True)\n"
)


def test_real_subprocess_150kb_stdin_no_deadlock(leerie, leerie_dir):
    """Spawns a REAL python3 child that reads all of stdin then emits one
    stream-json result event, fed a 150,063-byte payload (the incident's
    exact overflow size — well over MAX_ARG_STRLEN) through _invoke's
    real asyncio.create_subprocess_exec + concurrent feeder path. If the
    feeder/reader pairing deadlocked (e.g. write-then-await against a
    full OS pipe buffer while the child blocks writing its own stdout),
    this test would hang rather than fail cleanly — so it's bounded by
    pytest-timeout-independent asyncio.wait_for via _invoke's own
    `timeout` param.
    """
    payload = "q" * 150_063
    assert len(payload.encode()) > MAX_ARG_STRLEN, (
        "sanity: the whole point is a payload that could not have "
        "fit on argv in the first place")

    cmd = [sys.executable, "-c", _ECHO_CHILD]
    envelope = asyncio.run(leerie._invoke(
        cmd, cwd=str(leerie_dir.parent), timeout=30, sid="t-e2e",
        leerie_dir=leerie_dir, verbosity="quiet", stdin_data=payload))

    assert envelope["type"] == "result"
    assert envelope["is_error"] is False
    assert envelope["structured_output"]["nbytes"] == len(payload.encode())


def test_no_staged_file_leaks_when_the_spawn_fails(leerie, leerie_dir,
                                                    monkeypatch):
    """A failed spawn must not strand the staged prompt.

    The file is created BEFORE the try/finally that normally reaps it, and
    `create_subprocess_exec` is a fork — under the `pids.max` exhaustion
    this codebase handles elsewhere it raises EAGAIN, and every retry would
    strand another ~150KB file. Disk exhaustion has killed runs here (hence
    the `_disk_free_ratio` preflight), so leaking on the failure path feeds
    the very condition that produced the failure.
    """
    import glob
    import tempfile

    pattern = os.path.join(tempfile.gettempdir(), "leerie-prompt-*")
    before = set(glob.glob(pattern))

    async def boom(*cmd, **kwargs):
        raise OSError(11, "Resource temporarily unavailable")  # EAGAIN

    monkeypatch.setattr("asyncio.create_subprocess_exec", boom)
    with pytest.raises(OSError):
        asyncio.run(leerie._invoke(
            ["claude", "-p"], cwd=str(leerie_dir.parent),
            timeout=60, sid="t-leak", leerie_dir=leerie_dir,
            verbosity="quiet", stdin_data="x" * 150_000))

    assert not (set(glob.glob(pattern)) - before), (
        "a failed spawn stranded its staged prompt file")
