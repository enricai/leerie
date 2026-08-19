"""Tests for `_prune_subtask_worktree` (N31) — the post-integration cleanup
helper that removes a subtask's worktree directory (including node_modules
etc.) once its branch has been merged into staging, WITHOUT deleting the
branch itself. Distinct from `_reset_subtask_worktree`, which removes both
the worktree AND the branch to enable a corrective retry.
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


def test_noop_when_worktree_absent(leerie, tmp_path, monkeypatch):
    """Idempotent: no worktree to prune is not an error."""
    repo = _init_repo(tmp_path / "repo")
    monkeypatch.chdir(repo)
    leerie_dir = repo / ".leerie" / "runs" / "run-id"
    (leerie_dir / "worktrees").mkdir(parents=True)

    asyncio.run(leerie._prune_subtask_worktree("sid-x", leerie_dir))

    assert not (leerie_dir / "worktrees" / "sid-x").exists()


def test_removes_worktree_dir_but_keeps_branch(leerie, tmp_path, monkeypatch):
    """The core contract: worktree directory (and node_modules-style content
    within it) is gone, but the branch ref survives for finalize/PR use."""
    repo = _init_repo(tmp_path / "repo")
    monkeypatch.chdir(repo)
    leerie_dir = repo / ".leerie" / "runs" / "run-id"
    wt_dir = leerie_dir / "worktrees" / "sid-x"
    wt_dir.parent.mkdir(parents=True)

    _git("branch", "leerie/runs/run-id", "main", cwd=repo)
    r = _git("worktree", "add", str(wt_dir), "-b",
             "leerie/subtasks/run-id/sid-x", "leerie/runs/run-id", cwd=repo)
    assert r.returncode == 0, r.stderr
    # Simulate a bulky dependency directory left in the worktree.
    (wt_dir / "node_modules").mkdir()
    (wt_dir / "node_modules" / "dep.js").write_text("module.exports = {};\n")
    assert wt_dir.exists()

    asyncio.run(leerie._prune_subtask_worktree("sid-x", leerie_dir))

    assert not wt_dir.exists()
    show = _git("show-ref", "--verify", "--quiet",
                "refs/heads/leerie/subtasks/run-id/sid-x", cwd=repo)
    assert show.returncode == 0, "branch must survive pruning"


def test_prune_does_not_touch_other_sids_worktree(leerie, tmp_path, monkeypatch):
    """Scoped to exactly one sid — a sibling wave member's worktree is
    untouched."""
    repo = _init_repo(tmp_path / "repo")
    monkeypatch.chdir(repo)
    leerie_dir = repo / ".leerie" / "runs" / "run-id"
    leerie_dir.joinpath("worktrees").mkdir(parents=True)
    _git("branch", "leerie/runs/run-id", "main", cwd=repo)

    wt_a = leerie_dir / "worktrees" / "sid-a"
    wt_b = leerie_dir / "worktrees" / "sid-b"
    for sid, wt in (("sid-a", wt_a), ("sid-b", wt_b)):
        r = _git("worktree", "add", str(wt), "-b",
                 f"leerie/subtasks/run-id/{sid}", "leerie/runs/run-id", cwd=repo)
        assert r.returncode == 0, r.stderr

    asyncio.run(leerie._prune_subtask_worktree("sid-a", leerie_dir))

    assert not wt_a.exists()
    assert wt_b.exists()


def test_prune_falls_back_to_rmtree_when_git_leaves_dir_behind(
        leerie, tmp_path, monkeypatch):
    """`git worktree remove` fails (unregistered dir) but the directory
    still exists — the helper must fall back to a direct `shutil.rmtree`
    rather than leaving it behind. Mirrors the same fallback in
    `_cleanup_on_abnormal_exit`."""
    repo = _init_repo(tmp_path / "repo")
    monkeypatch.chdir(repo)
    leerie_dir = repo / ".leerie" / "runs" / "run-id"
    wt_dir = leerie_dir / "worktrees" / "sid-x"
    wt_dir.mkdir(parents=True)
    (wt_dir / "leftover.txt").write_text("stray\n")
    # Never registered with git, so `git worktree remove --force` returns
    # nonzero and the directory survives that call.
    r = _git("worktree", "remove", "--force", str(wt_dir), cwd=repo)
    assert r.returncode != 0
    assert wt_dir.exists()

    asyncio.run(leerie._prune_subtask_worktree("sid-x", leerie_dir))

    assert not wt_dir.exists(), (
        "an unregistered worktree directory must still be removed via the "
        "shutil.rmtree fallback")


def test_prune_then_reset_of_sibling_still_works(leerie, tmp_path, monkeypatch):
    """After a prune, a blocked/failed sibling sid's own worktree can still
    be reset via `_reset_subtask_worktree` independently — proves the two
    helpers don't interfere via shared state (e.g. `git worktree prune`
    calls)."""
    repo = _init_repo(tmp_path / "repo")
    monkeypatch.chdir(repo)
    leerie_dir = repo / ".leerie" / "runs" / "run-id"
    leerie_dir.joinpath("worktrees").mkdir(parents=True)
    _git("branch", "leerie/runs/run-id", "main", cwd=repo)

    wt_ok = leerie_dir / "worktrees" / "sid-ok"
    wt_blocked = leerie_dir / "worktrees" / "sid-blocked"
    for sid, wt in (("sid-ok", wt_ok), ("sid-blocked", wt_blocked)):
        r = _git("worktree", "add", str(wt), "-b",
                 f"leerie/subtasks/run-id/{sid}", "leerie/runs/run-id", cwd=repo)
        assert r.returncode == 0, r.stderr

    asyncio.run(leerie._prune_subtask_worktree("sid-ok", leerie_dir))
    assert not wt_ok.exists()
    assert wt_blocked.exists()

    asyncio.run(leerie._reset_subtask_worktree("sid-blocked", leerie_dir, "run-id"))
    assert not wt_blocked.exists()
    show = _git("show-ref", "--verify", "--quiet",
                "refs/heads/leerie/subtasks/run-id/sid-blocked", cwd=repo)
    assert show.returncode != 0
