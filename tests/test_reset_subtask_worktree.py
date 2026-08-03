"""Tests for `_reset_subtask_worktree` — the inter-retry cleanup helper that
removes a subtask's leftover worktree directory and branch so the next
attempt's `new-worktree.sh` reaches its "fresh subtask" path.

Without this reset, a corrective retry after a "complete with no commits"
failure hits `fatal: a branch ... already exists`, the WorkerError escapes
_settle_subtask, and _gather_or_cancel takes down the whole wave.
"""
from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path


def _git(*args, cwd):
    return subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=False,
    )


def _init_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git("init", "-q", "-b", "main", cwd=path)
    _git("config", "user.email", "test@leerie.local", cwd=path)
    _git("config", "user.name", "leerie test", cwd=path)
    _git("config", "commit.gpgsign", "false", cwd=path)
    (path / "file.txt").write_text("initial\n")
    _git("add", "file.txt", cwd=path)
    _git("commit", "-q", "-m", "initial", cwd=path)
    return path


def test_noop_when_neither_worktree_nor_branch_exists(leerie, tmp_path, monkeypatch):
    """Idempotent: when the worktree and branch are already absent the helper
    returns cleanly. This is the steady-state on a fresh subtask."""
    repo = _init_repo(tmp_path / "repo")
    monkeypatch.chdir(repo)
    leerie_dir = repo / ".leerie" / "runs" / "run-id"
    (leerie_dir / "worktrees").mkdir(parents=True)

    # Should not raise.
    asyncio.run(leerie._reset_subtask_worktree("sid-x", leerie_dir, "run-id"))

    # No worktree dir was created; no branch was created.
    assert not (leerie_dir / "worktrees" / "sid-x").exists()
    branches = _git("branch", "--list", "leerie/subtasks/run-id/sid-x", cwd=repo)
    assert branches.stdout.strip() == ""


def test_removes_existing_worktree_and_branch(leerie, tmp_path, monkeypatch):
    """After `new-worktree.sh`-equivalent setup, the helper drops both the
    worktree registration and the branch ref."""
    repo = _init_repo(tmp_path / "repo")
    monkeypatch.chdir(repo)
    leerie_dir = repo / ".leerie" / "runs" / "run-id"
    wt_dir = leerie_dir / "worktrees" / "sid-x"
    wt_dir.parent.mkdir(parents=True)

    # Set up the run branch (parent of the subtask branch) and the subtask
    # worktree the way scripts/new-worktree.sh would.
    _git("branch", "leerie/runs/run-id", "main", cwd=repo)
    r = _git("worktree", "add", str(wt_dir), "-b",
             "leerie/subtasks/run-id/sid-x", "leerie/runs/run-id", cwd=repo)
    assert r.returncode == 0, r.stderr
    assert wt_dir.exists()
    show = _git("show-ref", "--verify", "--quiet",
                "refs/heads/leerie/subtasks/run-id/sid-x", cwd=repo)
    assert show.returncode == 0

    asyncio.run(leerie._reset_subtask_worktree("sid-x", leerie_dir, "run-id"))

    assert not wt_dir.exists()
    show = _git("show-ref", "--verify", "--quiet",
                "refs/heads/leerie/subtasks/run-id/sid-x", cwd=repo)
    assert show.returncode != 0


def test_resets_so_worktree_add_b_succeeds_on_retry(leerie, tmp_path, monkeypatch):
    """The end-to-end shape this helper exists to enable: after reset, a
    fresh `git worktree add -b <branch>` succeeds where it would otherwise
    fail with `fatal: a branch ... already exists`. Mirrors the live
    container repro that motivated Fix B."""
    repo = _init_repo(tmp_path / "repo")
    monkeypatch.chdir(repo)
    leerie_dir = repo / ".leerie" / "runs" / "run-id"
    wt_dir = leerie_dir / "worktrees" / "sid-x"
    wt_dir.parent.mkdir(parents=True)
    _git("branch", "leerie/runs/run-id", "main", cwd=repo)

    # Attempt #1: succeeds.
    r1 = _git("worktree", "add", str(wt_dir), "-b",
              "leerie/subtasks/run-id/sid-x", "leerie/runs/run-id", cwd=repo)
    assert r1.returncode == 0

    # Attempt #2 without reset: fails because branch already exists.
    r2 = _git("worktree", "add", str(wt_dir) + "-again", "-b",
              "leerie/subtasks/run-id/sid-x", "leerie/runs/run-id", cwd=repo)
    assert r2.returncode != 0
    assert "already exists" in r2.stderr

    # Reset → attempt #3 succeeds (a fresh "new-worktree.sh" pass).
    asyncio.run(leerie._reset_subtask_worktree("sid-x", leerie_dir, "run-id"))
    r3 = _git("worktree", "add", str(wt_dir), "-b",
              "leerie/subtasks/run-id/sid-x", "leerie/runs/run-id", cwd=repo)
    assert r3.returncode == 0, r3.stderr
