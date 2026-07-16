"""Tests for the silent-worker observability additions to _invoke():

  - A1: spawn heartbeat log line emitted immediately after the worker
    subprocess is created (before the await on stdout blocks).
  - A2 + A4: idle watchdog that warns when no stdout events arrive for
    `caps['worker_idle_warn_sec']` seconds, flushing the stderr tail
    when available.
  - A3: stdin=DEVNULL passed to create_subprocess_exec so a worker
    inherits no TTY from the orchestrator's `nerdctl run -it` container.
  - A5: LEERIE_WORKER_DEBUG=1 in the orchestrator env injects DEBUG=* and
    ANTHROPIC_LOG=debug into the worker subprocess env.

Plus the D1 plumbing test:

  - claude_p threads its resolved `caps["worker_idle_warn_sec"]` value
    through to _invoke's `idle_warn_sec` kwarg so per-run overrides
    (CLI / env / leerie.toml) take effect rather than being silently
    ignored in favor of DEFAULT_CAPS.

All tests mock asyncio.create_subprocess_exec so no real `claude -p`
binary is required (matching test_invoke_streaming.py's pattern).
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest


_MOCK_PID_SENTINEL = 999_999_999


class _DelayedStream:
    """Asyncio stream mock that sleeps `pre_delay_sec` before yielding
    its first line, then yields each subsequent line after
    `inter_delay_sec` more seconds. EOF when exhausted.

    Used to simulate a `claude -p` worker that goes silent for an
    arbitrary amount of time before emitting any output."""
    def __init__(self, lines: list[str], pre_delay_sec: float = 0.0,
                 inter_delay_sec: float = 0.0):
        self._lines = [(l + "\n").encode() for l in lines]
        self._idx = 0
        self._pre = pre_delay_sec
        self._inter = inter_delay_sec
        self._first = True

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._idx >= len(self._lines):
            raise StopAsyncIteration
        if self._first:
            self._first = False
            if self._pre > 0:
                await asyncio.sleep(self._pre)
        else:
            if self._inter > 0:
                await asyncio.sleep(self._inter)
        line = self._lines[self._idx]
        self._idx += 1
        return line

    async def read(self, n: int = -1) -> bytes:
        return b""


class _StderrStream:
    """Asyncio stderr mock that emits a single bytes payload (optionally
    after a delay) then EOF. Used to verify the watchdog's stderr-tail
    flushing and live-streaming surfacing of CLI-internal debug output.

    `_drain_stderr` iterates the stream with `async for raw in
    proc.stderr` (line-by-line), so the mock yields its payload via
    `__anext__` as a single line followed by EOF."""
    def __init__(self, payload: bytes = b"", delay_sec: float = 0.0):
        # Ensure payload terminates with a newline so the async-for
        # iteration treats it as a complete line.
        if payload and not payload.endswith(b"\n"):
            payload = payload + b"\n"
        self._payload = payload
        self._delay = delay_sec
        self._yielded = False

    async def read(self, n: int = -1) -> bytes:
        # Kept for compatibility with any code path that still uses
        # chunked reads on stderr.
        if self._yielded:
            return b""
        self._yielded = True
        if self._delay > 0:
            await asyncio.sleep(self._delay)
        return self._payload

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._yielded or not self._payload:
            raise StopAsyncIteration
        self._yielded = True
        if self._delay > 0:
            await asyncio.sleep(self._delay)
        return self._payload


class _DelayedProc:
    def __init__(self, stdout_lines: list[str],
                 pre_delay_sec: float = 0.0,
                 inter_delay_sec: float = 0.0,
                 stderr_payload: bytes = b"",
                 stderr_delay_sec: float = 0.0,
                 returncode: int = 0):
        self.stdout = _DelayedStream(stdout_lines, pre_delay_sec,
                                     inter_delay_sec)
        self.stderr = _StderrStream(stderr_payload, stderr_delay_sec)
        self.returncode = returncode
        self.pid = _MOCK_PID_SENTINEL

    def kill(self):
        pass

    async def wait(self):
        # Match the wall-clock the stream consumer needs so proc.wait()
        # doesn't return before _read_stream has finished. The real
        # `_invoke` gathers _read_stream, _drain_stderr, and proc.wait()
        # — all three must complete for the gather to resolve.
        # Sleep until our stream is exhausted, then return.
        while self.stdout._idx < len(self.stdout._lines):
            await asyncio.sleep(0.01)
        return self.returncode


@pytest.fixture
def leerie_dir(tmp_path):
    cd = tmp_path / ".leerie"
    cd.mkdir()
    (cd / "logs").mkdir()
    return cd


# ---- A3: stdin=DEVNULL --------------------------------------------------

def test_create_subprocess_exec_passes_stdin_devnull(leerie, leerie_dir,
                                                      monkeypatch):
    """The worker must never inherit the orchestrator's stdin. Inside a
    `nerdctl run -it` container the orchestrator's stdin is /dev/pts/0
    (a real TTY); a CLI that branches on isatty() would block forever
    waiting for input. Closing stdin via DEVNULL eliminates that class
    of hang and is the principled shape for a non-interactive worker."""
    captured: dict = {}

    async def fake(*cmd, **kwargs):
        captured.update(kwargs)
        events = [json.dumps({"type": "result", "subtype": "success",
                              "num_turns": 1, "is_error": False})]
        return _DelayedProc(events)

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake)
    asyncio.run(leerie._invoke(
        ["claude", "-p", "x"], cwd=str(leerie_dir.parent),
        timeout=60, sid="t-stdin", leerie_dir=leerie_dir,
        verbosity="quiet"))

    assert captured.get("stdin") == asyncio.subprocess.DEVNULL, (
        "_invoke must pass stdin=asyncio.subprocess.DEVNULL so workers "
        "do not inherit the orchestrator's TTY stdin. See the silent-"
        "hang failure analysis (50-minute stalls in phase 2 / 2½) for "
        "context."
    )


# ---- A1: spawn heartbeat ------------------------------------------------

def test_spawn_heartbeat_logged_at_normal_verbosity(leerie, leerie_dir,
                                                     monkeypatch, capsys):
    """Immediately after create_subprocess_exec returns, _invoke must
    log a `[<sid>] spawned (pid=…)` line. Without this, the user sees
    nothing between the phase header and the worker's first event — and
    if the worker hangs before emitting any event (the silent-hang
    failure class), the user gets zero feedback for up to 90 minutes.

    The spawn heartbeat is operational chatter, not error-class, so it
    is suppressed at `quiet` (see the verbosity contract). At any other
    level the line is required."""
    events = [json.dumps({"type": "result", "subtype": "success",
                          "num_turns": 1, "is_error": False})]

    async def fake(*cmd, **kwargs):
        return _DelayedProc(events)

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake)
    asyncio.run(leerie._invoke(
        ["claude", "-p", "x"], cwd=str(leerie_dir.parent),
        timeout=60, sid="t-spawn", leerie_dir=leerie_dir,
        verbosity="normal"))

    out = capsys.readouterr().out
    assert f"[t-spawn] spawned (pid={_MOCK_PID_SENTINEL})" in out, (
        "expected spawn-heartbeat line; got:\n" + out
    )


def test_spawn_heartbeat_suppressed_at_quiet(leerie, leerie_dir,
                                              monkeypatch, capsys):
    """At quiet, the spawn heartbeat is suppressed — quiet emits phase
    boundaries + errors only. The watchdog warning (a degraded-state
    signal) still fires at quiet; only the operational spawn line is
    gated."""
    events = [json.dumps({"type": "result", "subtype": "success",
                          "num_turns": 1, "is_error": False})]

    async def fake(*cmd, **kwargs):
        return _DelayedProc(events)

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake)
    asyncio.run(leerie._invoke(
        ["claude", "-p", "x"], cwd=str(leerie_dir.parent),
        timeout=60, sid="t-spawn-q", leerie_dir=leerie_dir,
        verbosity="quiet"))

    out = capsys.readouterr().out
    assert "spawned (pid=" not in out, (
        "expected spawn heartbeat to be suppressed at quiet; got:\n"
        + out
    )


# ---- A2 + A4: idle watchdog --------------------------------------------

def test_idle_watchdog_warns_on_silent_worker(leerie, leerie_dir,
                                                monkeypatch, capsys):
    """The watchdog fires when no stdout event arrives within
    `worker_idle_warn_sec`. We simulate a worker that goes silent for
    a controllable interval before finally emitting its result, then
    assert at least one `no stdout events in` warning appeared."""
    events = [json.dumps({"type": "result", "subtype": "success",
                          "num_turns": 1, "is_error": False})]

    async def fake(*cmd, **kwargs):
        # 1.0s of silence before the first (and only) event.
        return _DelayedProc(events, pre_delay_sec=1.0)

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake)
    # Patch DEFAULT_CAPS so the watchdog fires after 0.3s of silence,
    # well below the simulated 1.0s gap. Restore on test exit.
    monkeypatch.setitem(leerie.DEFAULT_CAPS, "worker_idle_warn_sec", 0.3)
    asyncio.run(leerie._invoke(
        ["claude", "-p", "x"], cwd=str(leerie_dir.parent),
        timeout=10, sid="t-idle", leerie_dir=leerie_dir,
        verbosity="quiet"))

    out = capsys.readouterr().out
    assert "[t-idle] no stdout events in" in out, (
        "expected watchdog warning; got:\n" + out
    )


def test_idle_watchdog_does_not_fire_on_active_worker(leerie, leerie_dir,
                                                       monkeypatch,
                                                       capsys):
    """A worker that emits events steadily must NOT trigger a watchdog
    warning. The watchdog measures *silence*, not total elapsed time."""
    events = [
        json.dumps({"type": "system", "subtype": "init", "model": "m"}),
        json.dumps({"type": "assistant", "message": {"content": [
            {"type": "text", "text": "hi"}]}}),
        json.dumps({"type": "result", "subtype": "success",
                    "num_turns": 1, "is_error": False}),
    ]

    async def fake(*cmd, **kwargs):
        # All events flow instantly; gather should complete before the
        # watchdog ever wakes. warn_sec=10.0 gives ~200x headroom over
        # the ~50ms a 3-event stream takes in this mock — protects
        # against soft flakes under heavy CI load while adding no real
        # wall-clock to the test (asyncio.run returns as soon as the
        # gather resolves).
        return _DelayedProc(events)

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake)
    monkeypatch.setitem(leerie.DEFAULT_CAPS, "worker_idle_warn_sec", 10.0)
    asyncio.run(leerie._invoke(
        ["claude", "-p", "x"], cwd=str(leerie_dir.parent),
        timeout=30, sid="t-active", leerie_dir=leerie_dir,
        verbosity="quiet"))

    out = capsys.readouterr().out
    assert "no stdout events in" not in out, (
        "watchdog should not fire on a healthy worker; got:\n" + out
    )


def test_idle_watchdog_cancels_cleanly_on_success(leerie, leerie_dir,
                                                    monkeypatch):
    """The watchdog must be cancelled on every exit path so it never
    outlives the worker. If the try/finally lifecycle were missing, the
    task would leak and asyncio.run() would either hang waiting on the
    watchdog's `await asyncio.sleep(...)` or warn about a never-awaited
    coroutine on shutdown. This test verifies _invoke completes
    cleanly within a tight budget."""
    events = [json.dumps({"type": "result", "subtype": "success",
                          "num_turns": 1, "is_error": False})]

    async def fake(*cmd, **kwargs):
        return _DelayedProc(events)

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake)
    monkeypatch.setitem(leerie.DEFAULT_CAPS, "worker_idle_warn_sec", 30.0)

    async def runner():
        # Must complete inside the timeout — if the watchdog leaks,
        # asyncio.wait_for() will time out.
        return await asyncio.wait_for(leerie._invoke(
            ["claude", "-p", "x"], cwd=str(leerie_dir.parent),
            timeout=10, sid="t-cancel", leerie_dir=leerie_dir,
            verbosity="quiet"), timeout=5.0)

    result = asyncio.run(runner())
    assert result["subtype"] == "success"


def test_idle_watchdog_includes_stderr_tail(leerie, leerie_dir,
                                              monkeypatch, capsys):
    """When the worker is writing to stderr but not stdout (the most
    likely shape of a credential-refresh hang or a `--debug` CLI under
    LEERIE_WORKER_DEBUG=1), the watchdog must surface the stderr tail
    alongside the silence warning so the user has something
    actionable."""
    events = [json.dumps({"type": "result", "subtype": "success",
                          "num_turns": 1, "is_error": False})]
    stderr_payload = b"DEBUG anthropic: retrying token refresh (attempt 3)\n"

    async def fake(*cmd, **kwargs):
        return _DelayedProc(events, pre_delay_sec=1.0,
                            stderr_payload=stderr_payload)

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake)
    monkeypatch.setitem(leerie.DEFAULT_CAPS, "worker_idle_warn_sec", 0.3)
    asyncio.run(leerie._invoke(
        ["claude", "-p", "x"], cwd=str(leerie_dir.parent),
        timeout=10, sid="t-tail", leerie_dir=leerie_dir,
        verbosity="quiet"))

    out = capsys.readouterr().out
    assert "[t-tail] no stdout events in" in out
    # The stderr payload appears in some watchdog warning, truncated to
    # the last 400 chars and repr-quoted.
    assert "retrying token refresh" in out, (
        "expected the stderr tail to be flushed in the watchdog "
        f"warning; got:\n{out}"
    )


# ---- A5: LEERIE_WORKER_DEBUG env passthrough -----------------------------

def test_leerie_worker_debug_injects_env(leerie, leerie_dir, monkeypatch):
    """When LEERIE_WORKER_DEBUG is set in the orchestrator's environment,
    the worker subprocess must inherit DEBUG=* and ANTHROPIC_LOG=debug
    so its internal state surfaces (via stderr, which the watchdog
    flushes). This is the diagnostic toggle for the next silent-hang
    reproduction — without it, B's root cause stays opaque."""
    captured: dict = {}

    async def fake(*cmd, **kwargs):
        captured.update(kwargs)
        events = [json.dumps({"type": "result", "subtype": "success",
                              "num_turns": 1, "is_error": False})]
        return _DelayedProc(events)

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake)
    monkeypatch.setenv("LEERIE_WORKER_DEBUG", "1")
    asyncio.run(leerie._invoke(
        ["claude", "-p", "x"], cwd=str(leerie_dir.parent),
        timeout=60, sid="t-debug", leerie_dir=leerie_dir,
        verbosity="quiet"))

    env = captured.get("env")
    assert env is not None, (
        "expected env= to be passed when LEERIE_WORKER_DEBUG is set"
    )
    assert env.get("DEBUG") == "*", (
        f"expected DEBUG=* in worker env; got {env.get('DEBUG')!r}"
    )
    assert env.get("ANTHROPIC_LOG") == "debug", (
        f"expected ANTHROPIC_LOG=debug in worker env; got "
        f"{env.get('ANTHROPIC_LOG')!r}"
    )


def test_no_env_override_when_leerie_worker_debug_unset(leerie, leerie_dir,
                                                       monkeypatch):
    """When LEERIE_WORKER_DEBUG is NOT set, env= must be None so the
    worker simply inherits the parent environment (preserving the
    OAuth token, mise PATH, etc.). Passing a partial env dict would
    break the worker by stripping required variables."""
    captured: dict = {}

    async def fake(*cmd, **kwargs):
        captured.update(kwargs)
        events = [json.dumps({"type": "result", "subtype": "success",
                              "num_turns": 1, "is_error": False})]
        return _DelayedProc(events)

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake)
    monkeypatch.delenv("LEERIE_WORKER_DEBUG", raising=False)
    asyncio.run(leerie._invoke(
        ["claude", "-p", "x"], cwd=str(leerie_dir.parent),
        timeout=60, sid="t-no-debug", leerie_dir=leerie_dir,
        verbosity="quiet"))

    assert captured.get("env") is None, (
        "without LEERIE_WORKER_DEBUG, env= must be None so the worker "
        "inherits the orchestrator's full environment (token, PATH, "
        "etc.). Passing a partial dict would strip required vars."
    )


# ---- DEFAULT_CAPS cap registration --------------------------------------

def test_worker_idle_warn_sec_in_default_caps(leerie):
    """The new cap must be present in DEFAULT_CAPS and a positive
    number. This is the structural test that prevents drift between
    code and IMPLEMENTATION.md's caps table."""
    assert "worker_idle_warn_sec" in leerie.DEFAULT_CAPS, (
        "DEFAULT_CAPS must declare worker_idle_warn_sec — "
        "claude_p falls back to DEFAULT_CAPS when the cap is absent."
    )
    v = leerie.DEFAULT_CAPS["worker_idle_warn_sec"]
    assert isinstance(v, (int, float)) and v > 0, (
        f"worker_idle_warn_sec must be a positive number; got {v!r}"
    )


# ---- D1: per-run override threads from caps through to _invoke ---------

def _make_state(leerie, run_dir: Path):
    """Minimal State-alike enough for claude_p to record telemetry —
    mirrors the helper in test_capture_call.py."""
    st = leerie.State.__new__(leerie.State)
    st.run_id = "test-run-d1"
    st.run_dir = run_dir
    st.path = run_dir / "state.json"
    st.data = {"telemetry": {"calls": 0, "cost_usd": 0.0,
                             "input_tokens": 0, "output_tokens": 0},
               "verbosity": "quiet"}
    run_dir.mkdir(parents=True, exist_ok=True)
    st.path.write_text("{}")
    return st


_OK_ENVELOPE = {
    "type": "result",
    "subtype": "success",
    "num_turns": 1,
    "total_cost_usd": 0.001,
    "is_error": False,
    "terminal_reason": "completed",
    "result": '{"ok": true}',
    "structured_output": {"ok": True},
    "usage": {"input_tokens": 10, "output_tokens": 2},
}


def test_idle_warn_sec_threads_from_caps_through_claude_p(
        leerie, tmp_path, monkeypatch):
    """Regression test for the D1 defect: `claude_p` must pass its
    resolved `caps["worker_idle_warn_sec"]` value down to `_invoke` as
    the `idle_warn_sec` kwarg. Otherwise the watchdog silently falls
    back to `DEFAULT_CAPS["worker_idle_warn_sec"]` and any per-run
    override (CLI / env / leerie.toml) is ignored.

    The watchdog itself runs inside `_invoke`; here we don't need to
    trigger it — we just spy on the kwargs `claude_p` passes to
    `_invoke` and confirm the override value (not the DEFAULT_CAPS
    default) is what arrives."""
    captured: dict = {}

    async def spy_invoke(*args, **kwargs):
        captured.update(kwargs)
        return _OK_ENVELOPE

    monkeypatch.setattr(leerie, "_invoke", spy_invoke)
    monkeypatch.setattr(leerie.State, "bump_workers",
                        lambda self, caps: None)

    run_dir = tmp_path / "runs" / "test-run-d1"
    st = _make_state(leerie, run_dir)
    # The override value MUST be visibly different from
    # DEFAULT_CAPS["worker_idle_warn_sec"] so the assertion is
    # discriminating — a stale read of DEFAULT_CAPS would surface as
    # the wrong value here.
    override_value = 42
    assert override_value != leerie.DEFAULT_CAPS["worker_idle_warn_sec"], (
        "test setup error: pick an override value that differs from "
        "the default so the assertion can distinguish them"
    )
    caps = {
        "worker_timeout_sec": 60,
        "max_total_workers": 99,
        "worker_idle_warn_sec": override_value,
    }

    asyncio.run(leerie.claude_p(
        user_prompt="x",
        system_prompt="y",
        schema_key="classifier",
        cwd=str(run_dir),
        allowed_tools="Read",
        max_turns=10,
        autonomous=False,
        caps=caps,
        st=st,
        model="sonnet",
        sid="t-d1",
    ))

    assert captured.get("idle_warn_sec") == override_value, (
        f"claude_p must pass caps['worker_idle_warn_sec']={override_value} "
        f"to _invoke as the idle_warn_sec kwarg so per-run overrides "
        f"take effect; got idle_warn_sec={captured.get('idle_warn_sec')!r}. "
        f"This is the D1 regression — if the watchdog reads DEFAULT_CAPS "
        f"directly, any CLI / env / leerie.toml override is silently "
        f"ignored."
    )


def test_idle_warn_sec_falls_back_to_default_when_cap_absent(
        leerie, tmp_path, monkeypatch):
    """If a caller passes a caps dict that doesn't carry
    `worker_idle_warn_sec`, `claude_p` falls back to the DEFAULT_CAPS
    value rather than crashing. This is the safety hatch for older
    state files or programmatic callers that don't know about the new
    cap key."""
    captured: dict = {}

    async def spy_invoke(*args, **kwargs):
        captured.update(kwargs)
        return _OK_ENVELOPE

    monkeypatch.setattr(leerie, "_invoke", spy_invoke)
    monkeypatch.setattr(leerie.State, "bump_workers",
                        lambda self, caps: None)

    run_dir = tmp_path / "runs" / "test-run-d1b"
    st = _make_state(leerie, run_dir)
    # caps deliberately omits worker_idle_warn_sec.
    caps = {"worker_timeout_sec": 60, "max_total_workers": 99}

    asyncio.run(leerie.claude_p(
        user_prompt="x",
        system_prompt="y",
        schema_key="classifier",
        cwd=str(run_dir),
        allowed_tools="Read",
        max_turns=10,
        autonomous=False,
        caps=caps,
        st=st,
        model="sonnet",
        sid="t-d1b",
    ))

    assert captured.get("idle_warn_sec") == \
        leerie.DEFAULT_CAPS["worker_idle_warn_sec"], (
        "when caps lacks worker_idle_warn_sec, claude_p must pass the "
        "DEFAULT_CAPS value to _invoke (not None, not zero); got "
        f"idle_warn_sec={captured.get('idle_warn_sec')!r}"
    )


# ---- Live stderr streaming (Fix 2) ------------------------------------

def test_stderr_written_to_log_file_live(leerie, leerie_dir, monkeypatch):
    """Each stderr line must be written to the per-sid log file with a
    `[ts] stderr` header, regardless of verbosity. This is the property
    that lets `tail -f .leerie/logs/<sid>.log` show stderr as it arrives,
    instead of (today) buffering silently until the worker exits or the
    watchdog fires."""
    events = [json.dumps({"type": "result", "subtype": "success",
                          "num_turns": 1, "is_error": False})]
    stderr_payload = b"Claude configuration file not found at: /home/leerie/.claude.json"

    async def fake(*cmd, **kwargs):
        return _DelayedProc(events, stderr_payload=stderr_payload)

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake)
    asyncio.run(leerie._invoke(
        ["claude", "-p", "x"], cwd=str(leerie_dir.parent),
        timeout=60, sid="t-stderr-file", leerie_dir=leerie_dir,
        verbosity="quiet"))

    log_text = (leerie_dir / "logs" / "t-stderr-file.log").read_text()
    assert "stderr" in log_text, (
        "per-sid log file must contain a `stderr` header for each "
        "stderr line; got log text:\n" + log_text)
    assert "Claude configuration file not found" in log_text, (
        "per-sid log file must contain the stderr payload verbatim; "
        "got log text:\n" + log_text)


def test_stderr_echoed_to_orchestrator_at_stream(leerie, leerie_dir,
                                                  monkeypatch, capsys):
    """At `stream` verbosity, each stderr line must be echoed to the
    orchestrator log as `[<sid>] stderr: <line>` so the user sees worker
    failures live instead of waiting for the 300s watchdog or
    process exit."""
    events = [json.dumps({"type": "result", "subtype": "success",
                          "num_turns": 1, "is_error": False})]
    stderr_payload = b"DEBUG anthropic: retrying token refresh (attempt 3)"

    async def fake(*cmd, **kwargs):
        return _DelayedProc(events, stderr_payload=stderr_payload)

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake)
    asyncio.run(leerie._invoke(
        ["claude", "-p", "x"], cwd=str(leerie_dir.parent),
        timeout=60, sid="t-stderr-stream", leerie_dir=leerie_dir,
        verbosity="stream"))

    out = capsys.readouterr().out
    assert "[t-stderr-stream] stderr: " in out, (
        "expected `[<sid>] stderr: ...` echo at stream verbosity; "
        "got:\n" + out)
    assert "retrying token refresh" in out, (
        "expected the stderr payload in the orchestrator echo; got:\n"
        + out)


def test_stderr_not_echoed_at_quiet(leerie, leerie_dir, monkeypatch, capsys):
    """At `quiet` verbosity, stderr lines must NOT appear in the
    orchestrator's stdout. The per-sid log file still gets them — that
    invariant is covered by `test_stderr_written_to_log_file_live`."""
    events = [json.dumps({"type": "result", "subtype": "success",
                          "num_turns": 1, "is_error": False})]
    stderr_payload = b"DEBUG anthropic: chatty progress message"

    async def fake(*cmd, **kwargs):
        return _DelayedProc(events, stderr_payload=stderr_payload)

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake)
    asyncio.run(leerie._invoke(
        ["claude", "-p", "x"], cwd=str(leerie_dir.parent),
        timeout=60, sid="t-stderr-quiet", leerie_dir=leerie_dir,
        verbosity="quiet"))

    out = capsys.readouterr().out
    assert "stderr:" not in out, (
        "stderr must not echo to the orchestrator at `quiet` verbosity; "
        f"got:\n{out}")
    # File still gets it.
    log_text = (leerie_dir / "logs" / "t-stderr-quiet.log").read_text()
    assert "chatty progress message" in log_text, (
        "per-sid log file must still capture stderr even at quiet; got "
        f"log text:\n{log_text}")


def test_stderr_activity_resets_idle_watchdog(leerie, leerie_dir,
                                               monkeypatch, capsys):
    """A worker that emits stderr but no stdout is `recovery-loop`
    shaped — the worker is alive, talking, just stuck before its first
    stream-json event. Stderr activity must refresh `last_event_at` so
    the idle watchdog does NOT falsely fire on it. Without this, the
    watchdog would spam warnings every `worker_idle_warn_sec` even
    though the worker is clearly emitting output."""
    events = [json.dumps({"type": "result", "subtype": "success",
                          "num_turns": 1, "is_error": False})]
    stderr_payload = b"DEBUG continuous output"

    async def fake(*cmd, **kwargs):
        # stderr arrives quickly; stdout (the result event) is delayed
        # long enough that the watchdog WOULD fire if stderr weren't
        # counted as liveness.
        return _DelayedProc(events,
                            pre_delay_sec=0.5,
                            stderr_payload=stderr_payload,
                            stderr_delay_sec=0.1)

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake)
    # Watchdog window: 0.3s. stderr arrives at 0.1s; stdout at 0.5s.
    # Without the nonlocal refresh, the watchdog would wake at 0.3s
    # with a 0.3s-since-spawn gap and warn. With the refresh, the
    # 0.1s stderr arrival resets the clock so the watchdog wake at
    # 0.4s sees only 0.3s of silence — under the 0.3s threshold by
    # the tightest margin; bump warn to 0.4 to be safe.
    monkeypatch.setitem(leerie.DEFAULT_CAPS, "worker_idle_warn_sec", 0.4)
    asyncio.run(leerie._invoke(
        ["claude", "-p", "x"], cwd=str(leerie_dir.parent),
        timeout=10, sid="t-stderr-liveness", leerie_dir=leerie_dir,
        verbosity="quiet"))

    out = capsys.readouterr().out
    assert "no stdout events in" not in out, (
        "watchdog should not fire when stderr is arriving; got:\n"
        + out)


def test_stderr_chunks_preserved_for_no_result_envelope(leerie, leerie_dir,
                                                         monkeypatch):
    """The exit-time no-envelope path decodes the full `stderr_chunks`
    buffer and includes it in the message it surfaces. Live streaming
    must NOT consume the buffer — it has to keep appending so the
    operator still sees why the worker died.

    The carrier changed (this path now returns a synthetic envelope
    rather than raising WorkerError, so claude_p's retry loop can see
    it), but the property under test is unchanged: stderr survives."""
    stderr_payload = b"FATAL: authentication failed for tenant Z"

    async def fake(*cmd, **kwargs):
        return _DelayedProc([], stderr_payload=stderr_payload)

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake)
    envelope = asyncio.run(leerie._invoke(
        ["claude", "-p", "x"], cwd=str(leerie_dir.parent),
        timeout=60, sid="t-stderr-exiterr", leerie_dir=leerie_dir,
        verbosity="quiet"))
    msg = str(envelope.get("result"))
    assert "authentication failed for tenant Z" in msg, (
        "the no-result envelope must still surface the full stderr "
        f"content (stderr_chunks not consumed by live streaming); "
        f"got: {msg!r}")
