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
import os
import pathlib
import subprocess
import types
import unittest.mock as mock

import pytest
from tests.conftest import fake_run_dir


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
        path=fake_run_dir() / "state.json",
        run_dir=fake_run_dir(),
        # claude_p derives the checkout write-denial from this
        # (_repo_write_denials) and the §12 cwd guard compares against it;
        # a stub without it silently disables both.
        repo_root=pathlib.Path("/leerie-test-user-repo"),
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


# --- F5: the staged file lives in the run directory (DESIGN §7 *The system
# prompt is staged in the run directory*) ------------------------------------

def test_staged_file_lives_in_the_run_dir_and_survives_a_tmp_wipe(
        leerie, tmp_path, monkeypatch):
    """The CLI reads the file at every spawn of the call. A /tmp cleared
    under a live call used to take it with it; the run directory is the
    state the run already depends on."""
    import tempfile
    fake_tmp = tmp_path / "tmp"
    fake_tmp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(fake_tmp))
    run_dir = tmp_path / "state" / "runs" / "r1"
    run_dir.mkdir(parents=True)
    path = leerie._write_system_prompt_file(run_dir, "SYSTEM")
    assert pathlib.Path(path).parent == run_dir / leerie.SYSPROMPT_DIRNAME
    # What a host cleanup does under a live call: /tmp emptied.
    for p in fake_tmp.iterdir():
        p.unlink()
    assert pathlib.Path(path).read_text() == "SYSTEM"


def test_falls_back_to_the_temp_dir_when_the_run_dir_cannot_hold_it(
        leerie, tmp_path):
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("")
    path = leerie._write_system_prompt_file(blocker, "SYSTEM")
    try:
        assert pathlib.Path(path).read_text() == "SYSTEM"
        assert not str(path).startswith(str(blocker))
    finally:
        os.unlink(path)


def test_claude_p_stages_in_the_run_dir_denies_reads_and_cleans_up(
        leerie, tmp_path, monkeypatch):
    """The real claude_p: the file it passes is under <run_dir>/sysprompt/,
    exists while the worker runs, is Read-denied to it, and is gone after."""
    run_dir = tmp_path / "runs" / "r1"
    run_dir.mkdir(parents=True)
    observed: dict = {}

    async def fake_invoke(cmd, cwd, timeout, sid, leerie_dir, verbosity,
                          stdin_data=None, **_kw):
        p = cmd[cmd.index("--append-system-prompt-file") + 1]
        observed["path"] = p
        observed["text"] = pathlib.Path(p).read_text()
        observed["deny"] = cmd[cmd.index("--disallowedTools") + 1]
        return {"type": "result", "subtype": "success", "is_error": False,
                "result": "{}", "structured_output": {"categories": []}}

    st = types.SimpleNamespace(
        path=run_dir / "state.json", run_dir=run_dir,
        repo_root=tmp_path / "repo", data={"verbosity": "quiet"},
        run_id="r1", bump_workers=lambda *a, **k: None,
        add_telemetry=lambda *a, **k: None)
    monkeypatch.setattr(leerie, "_invoke", fake_invoke)
    monkeypatch.setattr(leerie, "_capture_call", lambda *a, **k: None)
    monkeypatch.setattr(
        leerie, "_append_system_prompt_file_supported", lambda: True)
    asyncio.run(leerie.claude_p(
        "u", "THE SYSTEM PROMPT", schema_key="classifier",
        cwd=str(tmp_path / "planning"), allowed_tools="Read", max_turns=5,
        autonomous=False, caps=dict(leerie.DEFAULT_CAPS), st=st,
        model="sonnet", sid="t"))
    assert pathlib.Path(observed["path"]).parent == run_dir / "sysprompt"
    assert observed["text"] == "THE SYSTEM PROMPT"
    assert f"Read(/{run_dir}/sysprompt/**)" in observed["deny"].split(",")
    assert not pathlib.Path(observed["path"]).exists()


def _deny_for(leerie, tmp_path, monkeypatch, allowed_tools, autonomous):
    run_dir = tmp_path / "runs" / "r1"
    run_dir.mkdir(parents=True, exist_ok=True)
    observed: dict = {}

    async def fake_invoke(cmd, cwd, timeout, sid, leerie_dir, verbosity,
                          stdin_data=None, **_kw):
        observed["deny"] = cmd[cmd.index("--disallowedTools") + 1]
        return {"type": "result", "subtype": "success", "is_error": False,
                "result": "{}", "structured_output": {"categories": []}}

    st = types.SimpleNamespace(
        path=run_dir / "state.json", run_dir=run_dir,
        repo_root=tmp_path / "repo", data={"verbosity": "quiet"},
        run_id="r1", bump_workers=lambda *a, **k: None,
        add_telemetry=lambda *a, **k: None)
    monkeypatch.setattr(leerie, "_invoke", fake_invoke)
    monkeypatch.setattr(leerie, "_capture_call", lambda *a, **k: None)
    monkeypatch.setattr(
        leerie, "_append_system_prompt_file_supported", lambda: True)
    asyncio.run(leerie.claude_p(
        "u", "S", schema_key="classifier", cwd=str(tmp_path / "planning"),
        allowed_tools=allowed_tools, max_turns=5, autonomous=autonomous,
        caps=dict(leerie.DEFAULT_CAPS), st=st, model="sonnet", sid="t"))
    return observed["deny"].split(","), run_dir


@pytest.mark.parametrize("tools_name, autonomous, denied", [
    ("INSPECT_TOOLS", False, False),
    ("SATISFIED_PROBE_TOOLS", False, False),
    ("ACT_TOOLS", True, True),
    ("SMOKE_TOOLS", False, True),
])
def test_read_deny_only_for_workers_that_can_read_beyond_cwd(
        leerie, tmp_path, monkeypatch, tools_name, autonomous, denied):
    """DESIGN §7: a judgment worker held to the working-directory boundary
    carries no `Read()` rule. Any such rule makes the CLI refuse `cd <dir>;
    <read> <relative path>` compounds it cannot resolve. A worker that can
    read anywhere (acting, or a bare `Read` grant) keeps the deny."""
    deny, run_dir = _deny_for(leerie, tmp_path, monkeypatch,
                              getattr(leerie, tools_name), autonomous)
    reads = [d for d in deny if d.startswith("Read(")]
    assert reads == ([f"Read(/{run_dir}/sysprompt/**)"] if denied else [])


def test_judgment_tool_profiles_grant_no_blanket_read(leerie):
    """The boundary that replaces the deny for judgment workers holds only
    while they lack a bare Read/Grep/Glob grant, which the CLI honours on
    any path (measured live: `<run-dir>/state.json` read with it, refused
    without it)."""
    for tools in (leerie.INSPECT_TOOLS, leerie.SATISFIED_PROBE_TOOLS):
        names = {t.strip() for t in tools.split(",")}
        assert not names & {"Read", "Grep", "Glob"}
        assert not leerie._reads_beyond_cwd(tools, False)
    assert leerie._reads_beyond_cwd(leerie.ACT_TOOLS, True)
    assert leerie._reads_beyond_cwd("Glob,Bash(ls:*)", False)
