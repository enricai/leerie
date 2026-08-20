"""Shared pytest fixtures for the leerie test suite.

leerie.py is a single script (no package), so we load it once as a
module via importlib and expose it to every test via the `leerie`
fixture.
"""
from __future__ import annotations

import asyncio
import contextlib
import ctypes
import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LEERIE_PY = REPO_ROOT / "orchestrator" / "leerie.py"


def _run(coro):
    """Run an async coroutine synchronously (this repo does not use
    pytest-asyncio). Single owner for the byte-identical helper that was
    previously redefined in fifteen test files."""
    return asyncio.run(coro)


def run_bash(
    script: str,
    env: dict | None = None,
    *,
    cwd: Path | None = None,
    input: str | None = None,
) -> subprocess.CompletedProcess:
    """Run a bash script via `bash -c`, merging `env` onto the parent
    process environment. Single owner for the byte-identical `_run_bash`
    helper previously redefined across ~22 test files."""
    base_env = {k: v for k, v in os.environ.items()}
    if env:
        base_env.update(env)
    return subprocess.run(
        ["bash", "-c", script],
        env=base_env,
        cwd=str(cwd) if cwd is not None else None,
        input=input,
        capture_output=True,
        text=True,
    )


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


def _make_caps(leerie, **overrides) -> dict:
    """Shared DEFAULT_CAPS-derived caps builder for the recursive-decompose
    test family (§5½ (P1)). Callers layer on extra keys (e.g.
    `subfile_split_max_span`) via **overrides rather than every file forcing
    an identical cap set."""
    caps = {
        "max_total_workers": 200,
        "decompose_max_depth": leerie.DEFAULT_CAPS["decompose_max_depth"],
        "decompose_fit_threshold": leerie.DEFAULT_CAPS["decompose_fit_threshold"],
        "decompose_noprogress_rounds": leerie.DEFAULT_CAPS["decompose_noprogress_rounds"],
    }
    caps.update(overrides)
    return caps


def _fit_response(score: float) -> dict:
    """Shared stubbed fit_judge worker response for the recursive-decompose
    test family (§5½ (P1))."""
    return {
        "score": score,
        "rationale": f"score={score}",
        "diffuse": "" if score >= 0.70 else "too broad",
        "confidence": {
            "fit": 8.5, "basis": "test",
            "falsifiers_tested": ["x"], "contradictions_reconciled": [],
            "gap_to_close": {},
        },
    }


def _split_response(parent_id: str, n: int) -> dict:
    """Shared stubbed splitter worker response for the recursive-decompose
    test family (§5½ (P1))."""
    return {
        "children": [
            {
                "id": f"{parent_id}-{i + 1}",
                "title": f"child {i + 1}",
                "success_criteria_seed": f"criterion {i + 1}",
                "files_likely_touched": [f"f{i}.py"],
            }
            for i in range(n)
        ],
    }


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
def _no_real_planning_worktree(request, monkeypatch):
    """Keep `_ensure_planning_worktree` from shelling out to real git.

    It runs `scripts/planning-worktree.sh`, which does a real
    `git worktree add` rooted at `resolve_leerie_root()`. With
    `LEERIE_STATE_DIR` unset -- the default for a direct Python caller -- that
    resolves to `<repo>/.leerie`, so any test driving the real `_run_phases`
    created a FULL CHECKOUT OF THIS REPO inside this repo and left it
    registered in `.git/worktrees/`.

    The failure mode is silent in three ways at once, which is why this is a
    conftest-level guard rather than a stub in each driving test: `.leerie/*`
    is gitignored so `git status` stays clean; the directories survive the
    session; and the damage surfaces in an unrelated file, because a nested
    `.leerie/.../worktrees/planning/tests/...` copy does not match the
    `rel.startswith("tests/")` exclusion that scanners like
    `test_helper_naming_convention` use. Measured: two worktrees, 25 MB, and
    one red test with no visible connection to the change that caused it.

    Opt out with `@pytest.mark.real_planning_worktree` when the worktree
    mechanics are themselves the subject.
    """
    if request.node.get_closest_marker("real_planning_worktree"):
        return
    leerie = request.getfixturevalue("leerie")

    async def _stub(st):
        path = str(Path(getattr(st, "run_dir", ".")) / "worktrees"
                   / "planning")
        try:
            st.data["planning_worktree"] = path
            st.save()
        except Exception:
            pass
        return path

    monkeypatch.setattr(leerie, "_ensure_planning_worktree", _stub)


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
