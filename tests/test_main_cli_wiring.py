"""Execution-level coverage for `main()`'s CLI construction and flag-resolution
wiring (lines ~32113-32810: argparse setup through the resolve_* wiring that
populates `caps`/`args` before `_orchestrate()` is reached).

Every other test touching `main()` in this suite drives it via
`inspect.getsource` — a structural pin that a call site exists — never by
actually invoking it (see CLAUDE.md's "A test that source-slices one function
cannot observe a property it asserts repo-wide" for why that matters). This
file is the execution counterpart for the argument-parsing/flag-resolution
region specifically: it runs `main()` for real against a temp git repo with a
stubbed `claude` binary on PATH, stopping the run at the `_orchestrate()`
boundary by stubbing that coroutine to a fast no-op — everything in this
region (argparse construction, --list-runs / --report short-circuits,
caps/args resolution) executes for real.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "config", "user.name", "t"], cwd=repo, check=True)
    (repo / "README.md").write_text("hi\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "init"], cwd=repo, check=True)


def _fake_claude_on_path(tmp_path: Path, monkeypatch) -> None:
    """A stub `claude` binary so `shutil.which('claude')` finds one."""
    bindir = tmp_path / "fakebin"
    bindir.mkdir(exist_ok=True)
    stub = bindir / "claude"
    stub.write_text("#!/bin/sh\necho '{}'\n")
    stub.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}:{__import__('os').environ['PATH']}")


class _ReachedOrchestrate(Exception):
    """Raised by the stubbed `_orchestrate` — reaching it means every line of
    main()'s CLI-construction / flag-resolution wiring ran without dying."""


def _drive_main(leerie, monkeypatch, tmp_path, *, extra_argv=()) -> None:
    # `leerie` is a session-scoped fixture (one module load for the whole
    # suite), and `_CURRENT_RUN_ID` is a module-level global `die()` reads
    # to annotate its message — leaking a prior test's run id into this
    # one's error text otherwise.
    monkeypatch.setattr(leerie, "_CURRENT_RUN_ID", None, raising=False)
    repo = tmp_path / "repo"
    _init_repo(repo)
    _fake_claude_on_path(tmp_path, monkeypatch)
    monkeypatch.chdir(repo)
    monkeypatch.setenv("LEERIE_STATE_DIR", str(tmp_path / "state"))

    async def _boom(*_a, **_k):
        raise _ReachedOrchestrate()

    monkeypatch.setattr(leerie, "_orchestrate", _boom)
    monkeypatch.setattr(
        sys, "argv",
        ["leerie", "--run-id", "run-main-cli", *extra_argv])

    with pytest.raises(_ReachedOrchestrate):
        leerie.main()


class TestMainReachesOrchestrateBoundary:
    """The whole argparse build + caps/args resolution chain in main() runs
    on a real invocation, for both a bare run and a run with several flags
    that exercise distinct resolve_* branches in the owned region."""

    def test_bare_invocation_reaches_orchestrate(self, leerie, monkeypatch,
                                                  tmp_path):
        _drive_main(leerie, monkeypatch, tmp_path)

    def test_flags_exercise_more_resolve_branches(self, leerie, monkeypatch,
                                                   tmp_path):
        _drive_main(
            leerie, monkeypatch, tmp_path,
            extra_argv=[
                "a task", "--max-workers", "5", "--max-parallel", "2",
                "--confidence-rounds", "3", "--worker-pids-max", "1024",
                "--worker-timeout", "600", "--strict-conformer",
                "--verbosity", "quiet", "--no-push",
                "--dangerously-skip-permissions",
                "--source-of-truth", "codebase",
            ])

    def test_dangerously_force_strict_output_conflicts_with_base_url(
            self, leerie, monkeypatch, tmp_path):
        """One of the die() branches inside the owned region (32863-32900):
        --dangerously-force-strict-output refuses when ANTHROPIC_BASE_URL is
        already set, rather than silently hijacking or ignoring it."""
        monkeypatch.setattr(leerie, "_CURRENT_RUN_ID", None, raising=False)
        repo = tmp_path / "repo"
        _init_repo(repo)
        _fake_claude_on_path(tmp_path, monkeypatch)
        monkeypatch.chdir(repo)
        monkeypatch.setenv("LEERIE_STATE_DIR", str(tmp_path / "state"))
        monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://example.invalid")
        monkeypatch.setattr(
            sys, "argv",
            ["leerie", "--run-id", "run-strict", "a task",
             "--dangerously-force-strict-output"])

        with pytest.raises(SystemExit):
            leerie.main()

    def test_bedrock_env_also_conflicts(self, leerie, monkeypatch, tmp_path):
        """Same guard, the other collision named at line ~32892: Bedrock env
        vars, not just an explicit ANTHROPIC_BASE_URL."""
        monkeypatch.setattr(leerie, "_CURRENT_RUN_ID", None, raising=False)
        repo = tmp_path / "repo"
        _init_repo(repo)
        _fake_claude_on_path(tmp_path, monkeypatch)
        monkeypatch.chdir(repo)
        monkeypatch.setenv("LEERIE_STATE_DIR", str(tmp_path / "state"))
        monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
        monkeypatch.setenv("CLAUDE_CODE_USE_BEDROCK", "1")
        monkeypatch.setattr(
            sys, "argv",
            ["leerie", "--run-id", "run-bedrock", "a task",
             "--dangerously-force-strict-output"])

        with pytest.raises(SystemExit):
            leerie.main()

    def test_missing_run_id_dies(self, leerie, monkeypatch, tmp_path):
        """`--run-id` is mandatory outside `resume` (line ~32684)."""
        monkeypatch.setattr(leerie, "_CURRENT_RUN_ID", None, raising=False)
        repo = tmp_path / "repo"
        _init_repo(repo)
        _fake_claude_on_path(tmp_path, monkeypatch)
        monkeypatch.chdir(repo)
        monkeypatch.setenv("LEERIE_STATE_DIR", str(tmp_path / "state"))
        monkeypatch.setattr(sys, "argv", ["leerie", "a task"])

        with pytest.raises(SystemExit):
            leerie.main()

    def test_claude_missing_from_path_dies(self, leerie, monkeypatch,
                                            tmp_path):
        """`shutil.which('claude')` failing dies before any git/state work
        (line ~32611)."""
        monkeypatch.setattr(leerie, "_CURRENT_RUN_ID", None, raising=False)
        repo = tmp_path / "repo"
        _init_repo(repo)
        monkeypatch.chdir(repo)
        monkeypatch.setenv("LEERIE_STATE_DIR", str(tmp_path / "state"))
        monkeypatch.setattr(leerie.shutil, "which", lambda *_a, **_k: None)
        monkeypatch.setattr(
            sys, "argv", ["leerie", "--run-id", "run-noclaude", "a task"])

        with pytest.raises(SystemExit):
            leerie.main()

    def test_conflicting_positional_and_flag_run_id_dies(
            self, leerie, monkeypatch, tmp_path):
        """`resume <id> --run-id <other-id>` disagreeing dies rather than
        silently picking one (lines ~32583-32586)."""
        monkeypatch.setattr(leerie, "_CURRENT_RUN_ID", None, raising=False)
        repo = tmp_path / "repo"
        _init_repo(repo)
        _fake_claude_on_path(tmp_path, monkeypatch)
        monkeypatch.chdir(repo)
        monkeypatch.setenv("LEERIE_STATE_DIR", str(tmp_path / "state"))
        monkeypatch.setattr(
            sys, "argv",
            ["leerie", "resume", "run-a", "--run-id", "run-b"])

        with pytest.raises(SystemExit):
            leerie.main()

    def test_second_orchestrator_on_same_run_dir_dies(
            self, leerie, monkeypatch, tmp_path):
        """A second `main()` invocation against a run id already `State`-held
        (flocked) surfaces StateLockedError and exits EXIT_LOCKED, rather
        than racing the first (lines ~32696-32711)."""
        monkeypatch.setattr(leerie, "_CURRENT_RUN_ID", None, raising=False)
        repo = tmp_path / "repo"
        _init_repo(repo)
        _fake_claude_on_path(tmp_path, monkeypatch)
        monkeypatch.chdir(repo)
        state_dir = tmp_path / "state"
        monkeypatch.setenv("LEERIE_STATE_DIR", str(state_dir))

        held = leerie.State(state_dir, "run-locked", repo_root=repo)
        try:
            monkeypatch.setattr(
                sys, "argv",
                ["leerie", "--run-id", "run-locked", "a task"])
            with pytest.raises(SystemExit) as exc_info:
                leerie.main()
            assert exc_info.value.code == leerie.EXIT_LOCKED
        finally:
            held.release_lock()


class TestMainListAndReportShortCircuits:
    """--list-runs / --report both return before reaching the owned region's
    claude/git preflight — pinned here as the negative control proving the
    _drive_main harness above is actually exercising the region rather than
    passing vacuously on every argv shape."""

    def test_list_short_circuits_before_claude_check(self, leerie,
                                                       monkeypatch, tmp_path):
        monkeypatch.setattr(leerie, "_CURRENT_RUN_ID", None, raising=False)
        repo = tmp_path / "repo"
        repo.mkdir()
        monkeypatch.chdir(repo)
        monkeypatch.setenv("LEERIE_STATE_DIR", str(tmp_path / "state"))
        # Deliberately no `claude` on PATH and no git repo at all — if
        # `list` reached the owned region's `shutil.which("claude")` guard
        # this would die(), proving the short-circuit fired first.
        monkeypatch.setattr(sys, "argv", ["leerie", "list"])
        leerie.main()

    def test_report_short_circuits_before_claude_check(self, leerie,
                                                         monkeypatch,
                                                         tmp_path):
        monkeypatch.setattr(leerie, "_CURRENT_RUN_ID", None, raising=False)
        repo = tmp_path / "repo"
        repo.mkdir()
        monkeypatch.chdir(repo)
        state_dir = tmp_path / "state"
        (state_dir / "runs" / "solo").mkdir(parents=True)
        (state_dir / "runs" / "solo" / "state.json").write_text(
            json.dumps({"run_id": "solo", "task": "t",
                        "started_at": "2026-01-01T00:00:00Z"}))
        (state_dir / "runs" / "solo" / "calls.ndjson").write_text("")
        monkeypatch.setenv("LEERIE_STATE_DIR", str(state_dir))
        monkeypatch.setattr(sys, "argv", ["leerie", "--report"])
        leerie.main()
