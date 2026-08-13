"""`_ensure_worktree_deps` — apply the provision recipe to a worktree once.

Extracted from `_capture_conformance_baseline` (DESIGN §6½): deps live only in
worktrees, so a tree nothing has installed into cannot run the suite at all.

The memo is module-level (`_DEPS_INSTALLED`) rather than run state, because it
records a per-process filesystem fact. That makes it leak across tests exactly
the way `_active_admissions` does, and conftest's `leerie` fixture is
session-scoped — so this file resets it around every test. Without that, the
once-per-worktree assertions below are order-dependent and would also poison
unrelated files that drive the baseline.
"""
from __future__ import annotations

import asyncio
import subprocess
import types

import pytest


@pytest.fixture(autouse=True)
def _clear_deps_memo(leerie):
    leerie._DEPS_INSTALLED.clear()
    yield
    leerie._DEPS_INSTALLED.clear()


@pytest.fixture
def st():
    return types.SimpleNamespace(
        data={"provision": {"recipe": [
            {"kind": "install", "command": ["pnpm", "install"]},
        ]}},
        save=lambda: None,
    )


@pytest.fixture
def streaming(leerie, monkeypatch):
    calls = []

    def install(exc=None):
        async def _fake(cmd, **kwargs):
            calls.append((cmd, kwargs))
            if exc is not None:
                raise exc
            return (0, "ok")
        monkeypatch.setattr(leerie, "_run_streaming", _fake)
        return calls
    return install


def _go(leerie, tree, st, caps=None):
    return asyncio.run(leerie._ensure_worktree_deps(
        str(tree), st, caps or {}, log_path=None, verbosity="quiet"))


# --------------------------------------------------------------------------
# Once per worktree
# --------------------------------------------------------------------------

def test_installs_on_first_call(leerie, st, streaming, tmp_path):
    calls = streaming()
    _go(leerie, tmp_path, st)
    assert [c[0] for c in calls] == [["pnpm", "install"]]


def test_second_call_on_the_same_tree_installs_nothing(leerie, st, streaming,
                                                       tmp_path):
    """THE LOAD-BEARING ASSERTION. Measured on the motivating run, 263
    installs ran across 161 worker logs — ~2.8 per worktree. A memo that
    returns without re-running is the entire point, and only a call-count
    assertion catches an implementation that re-installs anyway."""
    calls = streaming()
    _go(leerie, tmp_path, st)
    _go(leerie, tmp_path, st)
    assert len(calls) == 1


def test_a_different_tree_does_install(leerie, st, streaming, tmp_path):
    """ANTI-VACUITY PARTNER: without this, the test above passes against an
    implementation that never installs at all."""
    calls = streaming()
    _go(leerie, tmp_path / "a", st)
    _go(leerie, tmp_path / "b", st)
    assert len(calls) == 2


def test_memo_is_keyed_on_the_resolved_path(leerie, st, streaming, tmp_path):
    """`/x` and `/x/./` are the same worktree. Keying on the raw string
    would install twice for the same tree."""
    calls = streaming()
    _go(leerie, tmp_path, st)
    _go(leerie, str(tmp_path) + "/.", st)
    assert len(calls) == 1


# --------------------------------------------------------------------------
# Which recipe entries run
# --------------------------------------------------------------------------

def test_only_install_and_build_kinds_run(leerie, streaming, tmp_path):
    calls = streaming()
    st = types.SimpleNamespace(data={"provision": {"recipe": [
        {"kind": "install", "command": ["pnpm", "install"]},
        {"kind": "build", "command": ["pnpm", "build"]},
        {"kind": "verify", "command": ["pnpm", "test"]},
        {"kind": "install"},                       # no command
    ]}}, save=lambda: None)
    _go(leerie, tmp_path, st)
    assert [c[0] for c in calls] == [["pnpm", "install"], ["pnpm", "build"]]


def test_empty_or_absent_recipe_is_a_no_op(leerie, streaming, tmp_path):
    calls = streaming()
    _go(leerie, tmp_path, types.SimpleNamespace(data={}, save=lambda: None))
    assert calls == []


def test_working_dir_is_resolved_under_the_tree(leerie, streaming, tmp_path):
    calls = streaming()
    st = types.SimpleNamespace(data={"provision": {"recipe": [
        {"kind": "install", "command": ["pnpm", "i"], "working_dir": "web"},
    ]}}, save=lambda: None)
    _go(leerie, tmp_path, st)
    assert calls[0][1]["cwd"] == str(tmp_path / "web")


def test_entry_env_layers_over_the_process_env(leerie, streaming, tmp_path):
    """`_run_streaming(env=None)` means 'inherit everything', so an explicit
    dict must still carry PATH — replacing rather than layering would strip
    it and every command would fail to resolve."""
    calls = streaming()
    st = types.SimpleNamespace(data={"provision": {"recipe": [
        {"kind": "install", "command": ["pnpm", "i"],
         "env": {"UV_THREADPOOL_SIZE": "4"}},
    ]}}, save=lambda: None)
    _go(leerie, tmp_path, st)
    env = calls[0][1]["env"]
    assert env["UV_THREADPOOL_SIZE"] == "4"
    assert "PATH" in env, "entry env must layer over os.environ, not replace it"


# --------------------------------------------------------------------------
# Non-fatal
# --------------------------------------------------------------------------

@pytest.mark.parametrize("exc", [
    subprocess.TimeoutExpired(cmd="pnpm install", timeout=1),
    OSError("disk full"),
])
def test_a_failing_install_never_raises(leerie, st, streaming, tmp_path, exc):
    """A failed install is deliberately left to surface as whatever the
    subsequent BLT command reports — that is the more useful signal, and it
    is already classified (`_runner_missing` demotes a missing runner to
    'could not measure' rather than RED)."""
    streaming(exc)
    _go(leerie, tmp_path, st)   # must not raise


def test_a_failing_install_still_marks_the_tree_done(leerie, st, streaming,
                                                     tmp_path):
    """Retrying a broken install once per measurement would multiply the
    cost this function exists to remove."""
    calls = streaming(OSError("boom"))
    _go(leerie, tmp_path, st)
    _go(leerie, tmp_path, st)
    assert len(calls) == 1
