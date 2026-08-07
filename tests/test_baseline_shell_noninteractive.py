"""bugfix-001: `_capture_conformance_baseline` must invoke build/lint/test
commands via a non-login shell (`["bash", "-c", cmd]`), not a login shell
(`["bash", "-lc", cmd]`).

Root cause (PENDING_ISSUES.md N8): mise is put on PATH only via the Docker
image's `ENV PATH=...` (Dockerfile:288). A login shell sources
~/.bash_profile (or /etc/profile), which on a typical setup *assigns*
(rather than appends to) PATH, discarding the inherited Docker-ENV value —
so a mise-managed runner (pnpm, npx, ...) silently resolves to "not found"
during the baseline capture even though it is genuinely on PATH for every
other command path in the module, all of which already use `["bash", "-c"]`
via `run_proc`/`_run_streaming` call sites elsewhere.
"""
from __future__ import annotations

import inspect
import os
import stat
import subprocess
import sys
import types


# --- source-coupling: the call site itself -------------------------------

def test_capture_baseline_uses_non_login_shell(leerie):
    src = inspect.getsource(leerie._capture_conformance_baseline)
    assert '"-lc"' not in src, (
        "_capture_conformance_baseline must not invoke commands via a "
        "login shell (bash -lc) — a login shell sources ~/.bash_profile / "
        "/etc/profile, which discards the Docker-image-ENV PATH mise "
        "needs to stay resolvable."
    )
    assert '["bash", "-c", cmd]' in src, (
        "_capture_conformance_baseline must invoke commands via "
        '["bash", "-c", cmd], matching every other command path in the '
        "module."
    )


def test_no_login_shell_invocation_anywhere_in_module(leerie):
    src = inspect.getsource(leerie)
    assert '"-lc"' not in src, (
        "No call site in orchestrator/leerie.py should use a login shell "
        "(bash -lc) — it silently discards the mise PATH set only via "
        "Docker ENV."
    )


# --- mechanism proof: a real subprocess reproduces the PATH loss ---------

def _make_fixture(tmp_path):
    """Build a HOME whose .bash_profile resets PATH (reproducing a typical
    login-shell profile chain that does not re-add mise) and a directory
    containing a runner that is only reachable via the *inherited* PATH
    (standing in for the Docker-ENV-injected mise shim dir)."""
    home = tmp_path / "home"
    home.mkdir()
    (home / ".bash_profile").write_text("export PATH=/usr/bin:/bin\n")

    mise_dir = tmp_path / "mise-shims"
    mise_dir.mkdir()
    runner = mise_dir / "fake-mise-runner"
    runner.write_text("#!/bin/sh\necho ok\n")
    runner.chmod(runner.stat().st_mode | stat.S_IEXEC)

    env = dict(os.environ)
    env["HOME"] = str(home)
    env["PATH"] = f"{mise_dir}:{env.get('PATH', '')}"
    return env


def test_login_shell_loses_the_mise_path_a_plain_shell_keeps(tmp_path):
    """Ground-truth reproduction, independent of the leerie module: the
    exact mechanism the bugfix targets. Skips itself if the host's own
    default shell config already re-adds a full PATH (so the fixture can't
    demonstrate the loss) rather than false-failing on an unrelated host
    quirk."""
    env = _make_fixture(tmp_path)

    login = subprocess.run(
        ["bash", "-lc", "fake-mise-runner"], env=env,
        capture_output=True, text=True)
    plain = subprocess.run(
        ["bash", "-c", "fake-mise-runner"], env=env,
        capture_output=True, text=True)

    assert plain.returncode == 0 and plain.stdout.strip() == "ok", (
        "sanity: a non-login shell must resolve the runner via the "
        "inherited PATH"
    )
    if login.returncode == 0:
        import pytest
        pytest.skip(
            "host's login-shell profile chain does not reset PATH — "
            "fixture cannot demonstrate the PATH loss on this host")
    assert login.returncode != 0, (
        "a login shell should have lost the mise-shim PATH entry via "
        "~/.bash_profile, reproducing the bug this fix addresses"
    )


# --- end-to-end: _capture_conformance_baseline resolves the runner -------

def _st(tmp_path, repo_root):
    run_dir = tmp_path / "run"
    (run_dir / "logs").mkdir(parents=True)
    st = types.SimpleNamespace(
        run_dir=run_dir,
        repo_root=repo_root,
        data={},
    )
    st.save = lambda: None
    return st


def test_capture_baseline_resolves_mise_only_via_env(leerie, tmp_path, monkeypatch):
    """End-to-end: with mise on PATH only through the (Docker-ENV-style)
    inherited environment, and a login-shell profile that would otherwise
    reset PATH, _capture_conformance_baseline's real ["bash", "-c", cmd]
    invocation still resolves the runner. This is the behavior a
    ["bash", "-lc", cmd] call would break."""
    env = _make_fixture(tmp_path)
    monkeypatch.setenv("PATH", env["PATH"])
    monkeypatch.setenv("HOME", env["HOME"])

    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    leerie_dir = tmp_path / "leerie_dir"
    staging = leerie_dir / "worktrees" / "staging"
    staging.mkdir(parents=True)

    monkeypatch.setattr(
        leerie, "resolve_blt",
        lambda repo_root: {"build": "", "lint": "", "test": "fake-mise-runner"})
    monkeypatch.setattr(leerie, "_write_run_json", lambda *a, **k: None)

    st = _st(tmp_path, repo_root)

    import asyncio
    asyncio.run(leerie._capture_conformance_baseline(
        leerie_dir, st, {"worker_timeout_sec": 30}))

    baseline = st.data["conformance"]["_baseline"]
    tests_axis = baseline["axes"]["tests"]
    assert tests_axis["ran"] is True
    assert tests_axis["measured"] is True, (
        f"the runner should have resolved via the inherited PATH; got "
        f"summary={tests_axis.get('summary')!r}"
    )
    assert tests_axis["passed"] is True
