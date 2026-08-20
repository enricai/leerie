"""Tests for check_merge_committed() — the post-integrator guard against
an integrator that claims 'resolved' but left the worktree mid-merge.

Uses real git repos under tmp_path because the function reads
MERGE_HEAD and the staged index via `git rev-parse` / `git diff --cached`.
"""
from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

from tests.conftest import run_git_cwd_kw as _git


def _git_noraise(*args, cwd):
    """Like `_git`, but never raises -- for calls whose non-zero exit is
    the thing under test (e.g. a merge left mid-conflict)."""
    return subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=False,
    )


def _make_repo(path: Path):
    """Initialize a git repo with a committed initial file. Returns path."""
    path.mkdir(parents=True, exist_ok=True)
    _git("init", "-q", "-b", "main", cwd=path)
    _git("config", "user.email", "test@leerie.local", cwd=path)
    _git("config", "user.name", "leerie test", cwd=path)
    _git("config", "commit.gpgsign", "false", cwd=path)
    (path / "file.txt").write_text("initial\n")
    _git("add", "file.txt", cwd=path)
    _git("commit", "-q", "-m", "initial", cwd=path)
    return path


def test_clean_worktree_returns_none(leerie, tmp_path):
    """A worktree with no MERGE_HEAD and no staged changes returns None."""
    repo = _make_repo(tmp_path / "repo")
    assert asyncio.run(leerie.check_merge_committed(repo)) is None


def test_merge_head_present_returns_mid_merge_error(leerie, tmp_path):
    """A worktree mid-merge with MERGE_HEAD set returns the mid-merge error."""
    repo = _make_repo(tmp_path / "repo")

    # Create branch B with a conflicting change.
    _git("checkout", "-q", "-b", "branch-b", cwd=repo)
    (repo / "file.txt").write_text("branch-b content\n")
    _git("commit", "-q", "-am", "branch-b change", cwd=repo)

    # Back on main, make a conflicting change.
    _git("checkout", "-q", "main", cwd=repo)
    (repo / "file.txt").write_text("main content\n")
    _git("commit", "-q", "-am", "main change", cwd=repo)

    # Attempt to merge branch-b → conflict, MERGE_HEAD is set.
    merge = _git_noraise("merge", "--no-commit", "branch-b", cwd=repo)
    assert merge.returncode != 0, "expected merge conflict"
    assert (repo / ".git" / "MERGE_HEAD").exists()

    err = asyncio.run(leerie.check_merge_committed(repo))
    assert err is not None
    assert "MERGE_HEAD" in err
    assert "mid-merge" in err


def test_staged_uncommitted_returns_staged_error(leerie, tmp_path):
    """A worktree with staged-but-uncommitted changes (and no MERGE_HEAD)
    returns the staged-uncommitted error."""
    repo = _make_repo(tmp_path / "repo")
    (repo / "file.txt").write_text("modified\n")
    _git("add", "file.txt", cwd=repo)
    # No commit — so changes are staged but uncommitted.

    err = asyncio.run(leerie.check_merge_committed(repo))
    assert err is not None
    assert "staged but uncommitted" in err
    # And we want to be sure it's NOT mistaken for a mid-merge case:
    assert "MERGE_HEAD" not in err


def test_unstaged_changes_only_returns_none(leerie, tmp_path):
    """Unstaged working-tree changes alone are not the integrator's
    failure mode this guard catches (only staged-but-uncommitted is).
    Confirm the guard does not false-positive on unstaged changes."""
    repo = _make_repo(tmp_path / "repo")
    (repo / "file.txt").write_text("modified but unstaged\n")
    # No `git add` — change is in the working tree only.
    assert asyncio.run(leerie.check_merge_committed(repo)) is None


def test_completed_merge_returns_none(leerie, tmp_path):
    """After a successful, fully-committed merge, MERGE_HEAD is gone
    and the index is clean — check_merge_committed returns None."""
    repo = _make_repo(tmp_path / "repo")

    _git("checkout", "-q", "-b", "branch-b", cwd=repo)
    (repo / "other.txt").write_text("from branch-b\n")
    _git("add", "other.txt", cwd=repo)
    _git("commit", "-q", "-m", "add other.txt", cwd=repo)

    _git("checkout", "-q", "main", cwd=repo)
    # Non-conflicting merge — completes cleanly with a merge commit.
    merge = _git("merge", "--no-ff", "-m", "merge branch-b", "branch-b", cwd=repo)
    assert merge.returncode == 0, f"merge failed unexpectedly: {merge.stderr}"
    assert not (repo / ".git" / "MERGE_HEAD").exists()

    assert asyncio.run(leerie.check_merge_committed(repo)) is None
