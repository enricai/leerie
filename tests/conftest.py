"""Shared pytest fixtures for the leerie test suite.

leerie.py is a single script (no package), so we load it once as a
module via importlib and expose it to every test via the `leerie`
fixture.
"""
from __future__ import annotations

import contextlib
import ctypes
import importlib.util
import os
import shutil
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LEERIE_PY = REPO_ROOT / "orchestrator" / "leerie.py"


@pytest.fixture(scope="session")
def leerie():
    """The leerie module loaded from orchestrator/leerie.py."""
    spec = importlib.util.spec_from_file_location("leerie", LEERIE_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _has_treesitter() -> bool:
    """True only if the installed tree-sitter stack can actually extract a
    symbol. Delegates to leerie's own `_tree_sitter_extraction_works()`
    functional probe (mere importability is insufficient — an
    installed-but-incompatible language-pack version imports fine yet extracts
    nothing). Shared here so extraction-dependent repo-map test modules gate
    on it without duplication."""
    spec = importlib.util.spec_from_file_location("_leerie_ts_probe", LEERIE_PY)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
        return mod._tree_sitter_extraction_works()
    except Exception:
        return False


# Evaluated once at collection; extraction-dependent repo-map test modules
# import this to skip cleanly on hosts without a working parser.
HAS_TREESITTER = _has_treesitter()


# Evaluated once at collection; test modules that exercise *host-side* bash
# import this to skip cleanly where `jq` is absent.
#
# Why a gate rather than adding jq to the image: the host/container split is
# deliberate. Host bash uses `jq` — the launcher hard-fails at preflight
# without it (`leerie`'s "jq not found on PATH" check, which tells you to
# `brew install jq`) — while code that runs *inside* the container uses
# python3, exactly as `scripts/remote/seed-auth.sh` documents: "python3 over
# jq because jq isn't in the leerie image (see Dockerfile)".
#
# The gated modules source scripts the host owns (`host-finalize.sh`,
# `provision.sh`'s `decide_teardown`, the launcher's finalize path) and stub
# `git`/`gh` on PATH but not `jq`, so they silently inherit it from whichever
# machine runs pytest. They pass on a dev host and in CI (both ship jq) and
# fail only inside `leerie:<version>` — where the scripts under test could
# never succeed anyway, since gh auth, ssh-agent, and Keychain are all
# host-side (DESIGN §6 *Finalization*).
#
# Do NOT "fix" a skip here by installing jq into the image: that buys a green
# tick, not working code, and erodes the boundary.
HAS_JQ = shutil.which("jq") is not None


def fake_claude_on_path(tmp_path: Path, monkeypatch) -> Path:
    """Put a stub `claude` binary on PATH and return it.

    `main()` gates on `shutil.which("claude")` and `die()`s (exit 1) long
    before anything a `main()`-driving test asserts on -- 87 lines before
    `State(...)` mints the run dir, and well before the top-level
    try/except. The binary is on a developer's PATH and NOT on the CI
    runner's, so a suite that drives the real `main()` without this passes
    locally and fails on CI with every expected exit code collapsed to
    `die()`'s 1. That is the trap CLAUDE.md records ("A local pass is not
    evidence until the host lacks `claude`"), and it is how 14 tests in
    `test_main_exception_arms.py` shipped red.

    Single owner on purpose: this lived in `test_main_cli_wiring.py` while
    `test_main_exception_arms.py` -- the same harness minus this one call --
    had no copy at all. Anything driving the real `main()` imports it from
    here rather than reproducing it.
    """
    bindir = tmp_path / "fakebin"
    bindir.mkdir(exist_ok=True)
    stub = bindir / "claude"
    stub.write_text("#!/bin/sh\necho '{}'\n")
    stub.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    return stub


# prctl option numbers, mirrored from leerie's own `_PR_SET_CHILD_SUBREAPER` /
# `_PR_GET_CHILD_SUBREAPER`. Duplicated as literals rather than imported
# because this fixture is autouse and must not force the session-scoped
# `leerie` module load onto every test in the suite;
# `tests/test_subreaper.py` carries a coupling guard that the two agree.
_PR_SET_CHILD_SUBREAPER = 36
_PR_GET_CHILD_SUBREAPER = 37


@contextlib.contextmanager
def child_subreaper_restored():
    """Restore this process's `PR_SET_CHILD_SUBREAPER` flag on exit.

    The mechanism behind the autouse fixture below, exposed as a plain
    context manager so a test can drive it without reaching into pytest's
    private fixture internals (which differ across pytest versions).

    Linux-only (`prctl` is a Linux syscall); a no-op everywhere else, and a
    no-op if the flag is unreadable or comes back unchanged.
    """
    if not sys.platform.startswith("linux"):
        yield
        return
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        before = ctypes.c_int(0)
        if libc.prctl(_PR_GET_CHILD_SUBREAPER, ctypes.byref(before),
                      0, 0, 0) != 0:
            yield
            return
    except OSError:
        yield
        return
    try:
        yield
    finally:
        after = ctypes.c_int(0)
        if (libc.prctl(_PR_GET_CHILD_SUBREAPER, ctypes.byref(after),
                       0, 0, 0) == 0
                and after.value != before.value):
            libc.prctl(_PR_SET_CHILD_SUBREAPER, before.value, 0, 0, 0)


@pytest.fixture(autouse=True)
def _restore_child_subreaper():
    """Put `PR_SET_CHILD_SUBREAPER` back after every test.

    `main()` calls `_become_subreaper()` as its SECOND statement, before
    argparse -- so every test that drives the real `main()` sets
    `prctl(PR_SET_CHILD_SUBREAPER, 1)` on the *pytest* process, and nothing
    ever cleared it. The flag then silently changes the meaning of process
    liveness for every later test in the same session: an orphaned
    grandchild that would normally reparent to PID 1 (which `wait()`s it, so
    the PID vanishes) instead reparents to pytest, which never reaps it. It
    lingers as a zombie -- and a zombie still owns its PID slot, so
    `os.kill(pid, 0)` succeeds and a liveness probe reports a process that
    was in fact killed.

    Measured: this turned three `tests/test_signal_cleanup.py` orphan-reaping
    assertions red on CI while `orchestrator/leerie.py` and that file were
    both byte-identical to main. Collection is alphabetical, so `test_main_*`
    poisons `test_signal_cleanup`; `tests/test_subreaper.py` sets the same
    flag and escaped only by sorting *after* it -- an accident of filename,
    not a fix. Hence a suite-wide autouse restore rather than a per-file one.
    """
    with child_subreaper_restored():
        yield
