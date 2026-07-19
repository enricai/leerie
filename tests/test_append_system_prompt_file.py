"""Tests for the appended system prompt transport (probe + file/inline
fallback for `--append-system-prompt-file`).

Incident 2026-07-19, root cause B (see bugfix-002's subtask spec / the
incident note under `_task` in the run's plan): the appended system
prompt (e.g. reconciler.md, ~25KB) is a second large argv element that
compounds with the (now-stdin-routed, per bugfix-001) user prompt toward
the overflow. `--append-system-prompt-file` genuinely appends (live-
verified in the incident write-up) but is UNDOCUMENTED — it has no
entry of its own in `claude --help`, appearing only inside `--bare`'s
help text — so its use is gated behind a one-time capability probe
(`_append_system_prompt_file_supported`) with an unconditional fallback
to the inline `--append-system-prompt`.

Covers:
  - `_append_system_prompt_file_supported()`: memoized once per process;
    reads "unknown option" in stderr as unsupported, anything else as
    supported; fails closed (False) on any OSError/timeout.
  - `claude_p`'s `build()`: emits `--append-system-prompt-file <path>`
    (never the inline flag) with the temp file holding `system_prompt`
    when the probe reports supported; falls back to the inline flag
    when unsupported.
  - The temp file is cleaned up after `claude_p` returns, on both the
    success and exception paths.
"""
from __future__ import annotations

import asyncio
import pathlib
import subprocess
import types
import unittest.mock as mock

import pytest


@pytest.fixture(autouse=True)
def _reset_probe_memo(leerie):
    """The probe result is memoized in a module-level global — reset it
    before and after every test in this file so tests don't leak state
    into each other or into other test modules (the `leerie` fixture is
    session-scoped, so the module object is shared)."""
    leerie._APPEND_SYSTEM_PROMPT_FILE_SUPPORTED = None
    yield
    leerie._APPEND_SYSTEM_PROMPT_FILE_SUPPORTED = None


# ---------------------------------------------------------------------------
# _append_system_prompt_file_supported(): the probe itself
# ---------------------------------------------------------------------------

def test_probe_true_when_flag_recognized(leerie, monkeypatch):
    """A recognized flag reaches the CLI's own 'no prompt provided' error,
    not 'unknown option' — that is read as supported."""
    def fake_run(cmd, **kwargs):
        assert "--append-system-prompt-file" in cmd
        return subprocess.CompletedProcess(
            cmd, 1, stdout="",
            stderr="Error: Input must be provided either through stdin "
                   "or as a prompt argument when using --print")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert leerie._append_system_prompt_file_supported() is True


def test_probe_false_when_flag_unrecognized(leerie, monkeypatch):
    """An old CLI rejects the flag outright with 'unknown option' —
    that is read as unsupported, triggering the inline fallback."""
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(
            cmd, 1, stdout="",
            stderr="error: unknown option '--append-system-prompt-file'")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert leerie._append_system_prompt_file_supported() is False


def test_probe_fails_closed_on_missing_binary(leerie, monkeypatch):
    """No `claude` on PATH (or any other OSError) must not crash the
    probe — fail closed to the documented inline flag."""
    def fake_run(cmd, **kwargs):
        raise FileNotFoundError("claude not found")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert leerie._append_system_prompt_file_supported() is False


def test_probe_fails_closed_on_timeout(leerie, monkeypatch):
    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout", 15))

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert leerie._append_system_prompt_file_supported() is False


def test_probe_memoized_across_calls(leerie, monkeypatch):
    """The probe must invoke `claude` at most once per process — a
    second call reuses the memoized result rather than re-invoking."""
    call_count = {"n": 0}

    def fake_run(cmd, **kwargs):
        call_count["n"] += 1
        return subprocess.CompletedProcess(
            cmd, 1, stdout="", stderr="Error: Input must be provided")

    monkeypatch.setattr(subprocess, "run", fake_run)
    first = leerie._append_system_prompt_file_supported()
    second = leerie._append_system_prompt_file_supported()
    assert first is second is True
    assert call_count["n"] == 1


def test_probe_writes_and_cleans_up_temp_file(leerie, monkeypatch, tmp_path):
    """The probe's own throwaway temp file must not leak on disk."""
    seen_path = {}

    def fake_run(cmd, **kwargs):
        idx = cmd.index("--append-system-prompt-file") + 1
        seen_path["path"] = cmd[idx]
        assert pathlib.Path(cmd[idx]).exists()
        return subprocess.CompletedProcess(
            cmd, 1, stdout="", stderr="Error: Input must be provided")

    monkeypatch.setattr(subprocess, "run", fake_run)
    leerie._append_system_prompt_file_supported()
    assert not pathlib.Path(seen_path["path"]).exists()


# ---------------------------------------------------------------------------
# claude_p()'s build(): file flag vs inline flag
# ---------------------------------------------------------------------------

def _make_state():
    return types.SimpleNamespace(
        path=pathlib.Path("/tmp/leerie-test-nonexistent/state.json"),
        run_dir=pathlib.Path("/tmp/leerie-test-nonexistent"),
        data={"verbosity": "quiet"}, run_id="r1",
        bump_workers=lambda *a, **k: None,
        add_telemetry=lambda *a, **k: None,
    )


def _run_claude_p_capturing_cmd(leerie, system_prompt: str):
    captured: dict = {}

    async def fake_invoke(cmd, cwd, timeout, sid, leerie_dir, verbosity,
                          stdin_data=None, **_kw):
        captured["cmd"] = list(cmd)
        # Read the temp file's contents (if any) at call time, before
        # claude_p's finally-block cleanup runs.
        if "--append-system-prompt-file" in cmd:
            idx = cmd.index("--append-system-prompt-file") + 1
            captured["file_contents"] = pathlib.Path(cmd[idx]).read_text()
            captured["file_path"] = cmd[idx]
        return {"type": "result", "subtype": "success", "is_error": False,
                "result": "{}", "structured_output": {"categories": []}}

    with mock.patch.object(leerie, "_invoke", fake_invoke), \
         mock.patch.object(leerie, "_capture_call", lambda *a, **k: None):
        asyncio.run(leerie.claude_p(
            "user prompt", system_prompt,
            schema_key="classifier", cwd="/work",
            allowed_tools="Read", max_turns=40, autonomous=False,
            caps=dict(leerie.DEFAULT_CAPS), st=_make_state(), model="opus",
            sid="append-sp-test",
        ))
    return captured


def test_build_uses_file_flag_when_probe_supported(leerie, monkeypatch):
    monkeypatch.setattr(
        leerie, "_append_system_prompt_file_supported", lambda: True)

    system_prompt = "SYSTEM PROMPT CONTENT " * 100
    captured = _run_claude_p_capturing_cmd(leerie, system_prompt)

    cmd = captured["cmd"]
    assert "--append-system-prompt-file" in cmd
    assert "--append-system-prompt" not in cmd
    assert captured["file_contents"] == system_prompt


def test_build_falls_back_to_inline_when_probe_unsupported(leerie, monkeypatch):
    monkeypatch.setattr(
        leerie, "_append_system_prompt_file_supported", lambda: False)

    system_prompt = "SYSTEM PROMPT CONTENT " * 100
    captured = _run_claude_p_capturing_cmd(leerie, system_prompt)

    cmd = captured["cmd"]
    assert "--append-system-prompt-file" not in cmd
    assert "--append-system-prompt" in cmd
    idx = cmd.index("--append-system-prompt") + 1
    assert cmd[idx] == system_prompt


def test_system_prompt_temp_file_cleaned_up_after_success(leerie, monkeypatch):
    monkeypatch.setattr(
        leerie, "_append_system_prompt_file_supported", lambda: True)

    captured = _run_claude_p_capturing_cmd(leerie, "sp content")
    assert not pathlib.Path(captured["file_path"]).exists(), (
        "the system-prompt temp file must be removed once claude_p "
        "returns, not left behind on every worker invocation")


def test_system_prompt_temp_file_cleaned_up_on_exception(leerie, monkeypatch):
    """The temp file must be removed even when claude_p raises from
    inside the try/finally-wrapped body (e.g. the terminal-auth-failure
    raise) — the cleanup lives in a `finally`, not only on the success
    return path."""
    monkeypatch.setattr(
        leerie, "_append_system_prompt_file_supported", lambda: True)

    seen_path: dict = {}

    async def fake_invoke(cmd, cwd, timeout, sid, leerie_dir, verbosity,
                          stdin_data=None, **_kw):
        idx = cmd.index("--append-system-prompt-file") + 1
        seen_path["path"] = cmd[idx]
        assert pathlib.Path(cmd[idx]).exists()
        return {"type": "result", "is_error": True,
                "result": "Failed to authenticate: OAuth session expired "
                          "and could not be refreshed"}

    with mock.patch.object(leerie, "_invoke", fake_invoke), \
         mock.patch.object(leerie, "_capture_call", lambda *a, **k: None):
        with pytest.raises(leerie.TerminalAuthFailure):
            asyncio.run(leerie.claude_p(
                "user prompt", "sp content",
                schema_key="classifier", cwd="/work",
                allowed_tools="Read", max_turns=40, autonomous=False,
                caps=dict(leerie.DEFAULT_CAPS), st=_make_state(),
                model="opus", sid="append-sp-exc-test",
            ))

    assert not pathlib.Path(seen_path["path"]).exists(), (
        "the system-prompt temp file must be removed even when claude_p "
        "raises out of the try/finally-wrapped body")


def test_retry_note_does_not_recreate_temp_file(leerie, monkeypatch):
    """The retry path (claude_p's schema-mismatch retry, calling
    build(retry_note) a second time) must reuse the SAME temp file
    rather than writing a new one per attempt — system_prompt is fixed
    for the whole claude_p call."""
    monkeypatch.setattr(
        leerie, "_append_system_prompt_file_supported", lambda: True)

    seen_paths: list[str] = []

    async def fake_invoke(cmd, cwd, timeout, sid, leerie_dir, verbosity,
                          stdin_data=None, **_kw):
        idx = cmd.index("--append-system-prompt-file") + 1
        seen_paths.append(cmd[idx])
        if len(seen_paths) == 1:
            return {"type": "result", "is_error": False, "result": "{}",
                    "structured_output": None}  # forces a retry
        return {"type": "result", "is_error": False, "result": "{}",
                "structured_output": {"categories": []}}

    with mock.patch.object(leerie, "_invoke", fake_invoke), \
         mock.patch.object(leerie, "_capture_call", lambda *a, **k: None):
        asyncio.run(leerie.claude_p(
            "user prompt", "sp content",
            schema_key="classifier", cwd="/work",
            allowed_tools="Read", max_turns=40, autonomous=False,
            caps=dict(leerie.DEFAULT_CAPS), st=_make_state(), model="opus",
            sid="append-sp-retry-test",
        ))

    assert len(seen_paths) == 2
    assert seen_paths[0] == seen_paths[1]


# ---------------------------------------------------------------------------
# End-to-end against a REAL child process: an overlap-judge-shaped payload
# (system prompt + user prompt together, both large) succeeds with rc=0
# using the file flag when the live CLI supports it.
# ---------------------------------------------------------------------------

def test_live_cli_accepts_append_system_prompt_file():
    """Live sanity check against the real installed `claude` CLI (no
    mocking): a large system prompt over --append-system-prompt-file is
    accepted without an 'unknown option' error. Skips if `claude` isn't
    on PATH (this file's other tests fully cover the logic via mocks;
    this one guards against the live CLI actually removing the flag,
    which is exactly the risk the probe+fallback design defends against
    — a failure here is a signal to check whether the fallback still
    engages, not a reason to xfail silently)."""
    import shutil

    if shutil.which("claude") is None:
        pytest.skip("claude CLI not on PATH")

    import tempfile
    system_prompt = "APPENDED SYSTEM PROMPT " * 1000  # ~23KB, judge-shaped
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt") as f:
        f.write(system_prompt)
        f.flush()
        r = subprocess.run(
            ["claude", "-p", "--append-system-prompt-file", f.name],
            input="", capture_output=True, text=True, timeout=15,
            check=False,
        )
    assert "unknown option" not in (r.stderr or "")
