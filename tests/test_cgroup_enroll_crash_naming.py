"""Tests for naming a cgroup-enroll failure as a probable contributing
cause when the SAME worker later crashes (DESIGN §6, incident
870cf82cdcd4c3df2c860b94c4608b63f4debf4f211c99cb1a2c5517c62cb9b4).

Before this fix, `_cgroup_enroll`'s rejection (e.g. broker
ProcessLookupError/ESRCH) and a later crash in the same worker's stream
(nonzero rc) were two separate, apparently-unrelated log lines with
nothing connecting them — making the incident's actual pairing (enroll
ESRCH immediately followed by the CLI's own 3s-stdin-timeout crash)
look like two unrelated events rather than one worker's story.

`_cgroup_enroll` now returns the broker's failure reason (str) instead
of a bare bool, and `_invoke` stashes it so the nonzero-rc crash message
names it as "possibly related" when present.

Reuses test_invoke_streaming.py's `_MockProc`/`_make_subprocess_exec_mock`
harness pattern (subprocess mocked, no real claude -p or broker).
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest


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

    async def read(self, n: int = -1) -> bytes:
        return b""


_MOCK_PID_SENTINEL = 999_999_998


class _MockProc:
    def __init__(self, stdout_lines: list[str], returncode: int = 0):
        self.stdout = _MockStream(stdout_lines)
        self.stderr = _MockStream([])
        self.returncode = returncode
        self.killed = False
        self.pid = _MOCK_PID_SENTINEL
        self.stdin = None

    def kill(self):
        self.killed = True

    async def wait(self):
        return self.returncode


def _make_subprocess_exec_mock(stdout_lines: list[str], returncode: int = 1):
    async def fake(*cmd, **kwargs):
        return _MockProc(stdout_lines, returncode)
    return fake


@pytest.fixture
def leerie_dir(tmp_path):
    cd = tmp_path / ".leerie"
    cd.mkdir()
    (cd / "logs").mkdir()
    return cd


# A truncated stream (no result event) + nonzero rc, matching the
# incident's exact shape: stderr carries the CLI's own timeout warning.
_NO_RESULT_EVENTS = [
    json.dumps({"type": "system", "subtype": "init", "model": "x"}),
]


def test_enroll_failure_named_in_crash_message_when_worker_also_crashes(
        leerie, leerie_dir, monkeypatch):
    """When cgroup enroll failed (broker rejected with ProcessLookupError)
    AND the worker later crashes with nonzero rc, the raised WorkerError's
    message must name the enroll failure as a probable contributing cause
    — not leave it silent while some other message names the crash."""
    monkeypatch.setattr("asyncio.create_subprocess_exec",
                        _make_subprocess_exec_mock(_NO_RESULT_EVENTS, returncode=1))
    monkeypatch.setattr(leerie, "_cgroup_create", lambda *a, **k: "run-w-sid")
    monkeypatch.setattr(
        leerie, "_cgroup_enroll",
        lambda *a, **k: "ERR ProcessLookupError: [Errno 3] No such process")

    with pytest.raises(leerie.WorkerError) as exc_info:
        asyncio.run(leerie._invoke(
            ["claude", "-p", "x"], cwd=str(leerie_dir.parent),
            timeout=60, sid="planner-documentation-s0", leerie_dir=leerie_dir,
            verbosity="stream",
            worker_memory_max_bytes=1 << 30, worker_pids_max=64,
            run_id="deadbeef1234"))

    msg = str(exc_info.value)
    assert "ProcessLookupError" in msg, (
        f"crash message must name the cgroup-enroll failure, got: {msg!r}")
    assert "possibly related" in msg


def test_no_enroll_note_when_enroll_succeeded(leerie, leerie_dir, monkeypatch):
    """Control: when enroll succeeded, a later crash's message must NOT
    fabricate an enroll-failure note."""
    monkeypatch.setattr("asyncio.create_subprocess_exec",
                        _make_subprocess_exec_mock(_NO_RESULT_EVENTS, returncode=1))
    monkeypatch.setattr(leerie, "_cgroup_create", lambda *a, **k: "run-w-sid")
    monkeypatch.setattr(leerie, "_cgroup_enroll", lambda *a, **k: None)

    with pytest.raises(leerie.WorkerError) as exc_info:
        asyncio.run(leerie._invoke(
            ["claude", "-p", "x"], cwd=str(leerie_dir.parent),
            timeout=60, sid="planner-documentation-s0", leerie_dir=leerie_dir,
            verbosity="stream",
            worker_memory_max_bytes=1 << 30, worker_pids_max=64,
            run_id="deadbeef1234"))

    msg = str(exc_info.value)
    assert "possibly related" not in msg
    assert "cgroup enroll" not in msg


def test_no_enroll_note_when_containment_disabled(leerie, leerie_dir, monkeypatch):
    """Control: when cgroup containment is off (no memory/pids caps
    passed), _cgroup_enroll is never called at all — the crash message
    must not reference cgroup enroll."""
    monkeypatch.setattr("asyncio.create_subprocess_exec",
                        _make_subprocess_exec_mock(_NO_RESULT_EVENTS, returncode=1))
    enroll_calls = []
    monkeypatch.setattr(leerie, "_cgroup_enroll",
                        lambda *a, **k: enroll_calls.append(a) or None)

    with pytest.raises(leerie.WorkerError) as exc_info:
        asyncio.run(leerie._invoke(
            ["claude", "-p", "x"], cwd=str(leerie_dir.parent),
            timeout=60, sid="planner-documentation-s0", leerie_dir=leerie_dir,
            verbosity="stream"))

    assert enroll_calls == []
    assert "possibly related" not in str(exc_info.value)


def test_cgroup_enroll_return_type_is_str_or_none(leerie, monkeypatch):
    """Direct unit pin: _cgroup_enroll's new contract is `str | None`
    (the failure reason, or None on success) — not the old bare bool."""
    class _FakeSock:
        def __init__(self, resp: str):
            self._resp = resp

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def settimeout(self, *a):
            pass

        def connect(self, *a):
            pass

        def sendall(self, *a):
            pass

        def recv(self, *a):
            return self._resp.encode()

    import socket as socket_mod

    monkeypatch.setattr(
        socket_mod, "socket",
        lambda *a, **k: _FakeSock("ERR ProcessLookupError: [Errno 3] No such process"))
    reason = leerie._cgroup_enroll("sid", 123)
    assert reason == "ERR ProcessLookupError: [Errno 3] No such process"

    monkeypatch.setattr(socket_mod, "socket", lambda *a, **k: _FakeSock("OK"))
    ok = leerie._cgroup_enroll("sid", 123)
    assert ok is None
