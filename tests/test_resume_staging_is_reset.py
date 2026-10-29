"""Every orchestrator process starts on staging at its branch tip with
nothing uncommitted (DESIGN §8 *The final diff describes the tree that
ships*). An interrupted run's cleanup removes staging, but a SIGKILL skips
cleanup: the resume's `setup-run.sh` found the staging worktree still
registered and reused it as the killed worker left it, and the first
reader's `_assert_staging_clean` stopped the run. `setup-run.sh` now resets
a surviving staging — tracked edits, untracked files and a merge in
progress go; ignored files (installed dependencies) and the run branch
stay. Driven on the real script."""
from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

from tests.conftest import REPO_ROOT

RUN_ID = "20261029-sigkill-staging"


def _git(cwd: Path, *args: str, check: bool = True) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=check,
                          capture_output=True, text=True).stdout.strip()


def _setup(repo: Path) -> None:
    subprocess.run(["bash", str(REPO_ROOT / "scripts" / "setup-run.sh"),
                    RUN_ID, "main"], cwd=repo, check=True,
                   capture_output=True)


def _repo(tmp_path, monkeypatch) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "f.txt").write_text("base\n")
    (repo / ".gitignore").write_text(".leerie/\nnode_modules/\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    root = repo / ".leerie"
    monkeypatch.setenv("LEERIE_STATE_DIR", str(root))
    monkeypatch.chdir(repo)
    _setup(repo)
    return repo, root / "runs" / RUN_ID / "worktrees" / "staging"


def test_a_staging_a_sigkill_left_dirty_is_reset_on_resume(
        leerie, tmp_path, monkeypatch, capsys):
    repo, staging = _repo(tmp_path, monkeypatch)
    _git(staging, "commit", "-q", "--allow-empty", "-m", "integrated wave")
    tip = _git(staging, "rev-parse", "HEAD")
    (staging / "node_modules").mkdir()
    (staging / "node_modules" / "dep.js").write_text("dep\n")
    # What the killed worker left: a tracked edit and an untracked file.
    (staging / "f.txt").write_text("half done\n")
    (staging / "scratch.py").write_text("x = 1\n")
    assert _git(staging, "status", "--porcelain") != ""
    _setup(repo)                                   # the resume's setup
    assert _git(staging, "status", "--porcelain",
                "--untracked-files=all") == ""
    assert _git(staging, "rev-parse", "HEAD") == tip   # branch untouched
    assert (staging / "node_modules" / "dep.js").is_file()  # ignored: kept
    # The first reader no longer stops the run.
    assert asyncio.run(leerie._assert_staging_clean(staging, "x")) == _git(
        staging, "rev-parse", "HEAD^{tree}")


def test_a_merge_a_sigkill_left_in_progress_is_ended(leerie, tmp_path,
                                                     monkeypatch):
    repo, staging = _repo(tmp_path, monkeypatch)
    _git(repo, "checkout", "-q", "-b", "side")
    (repo / "f.txt").write_text("side\n")
    _git(repo, "commit", "-qam", "side")
    _git(repo, "checkout", "-q", "main")
    (staging / "f.txt").write_text("earlier\n")
    _git(staging, "commit", "-qam", "earlier integration")
    tip = _git(staging, "rev-parse", "HEAD")
    _git(staging, "merge", "side", check=False)
    (staging / "f.txt").write_text("half-resolved\n")
    assert _git(staging, "rev-parse", "-q", "--verify", "MERGE_HEAD",
                check=False)
    _setup(repo)
    assert not _git(staging, "rev-parse", "-q", "--verify", "MERGE_HEAD",
                    check=False)
    assert _git(staging, "rev-parse", "HEAD") == tip
    assert _git(staging, "status", "--porcelain",
                "--untracked-files=all") == ""


def test_a_fresh_run_and_a_clean_resume_are_untouched(tmp_path,
                                                      monkeypatch):
    repo, staging = _repo(tmp_path, monkeypatch)
    _git(staging, "commit", "-q", "--allow-empty", "-m", "wave")
    tip = _git(staging, "rev-parse", "HEAD")
    _setup(repo)
    assert _git(staging, "rev-parse", "HEAD") == tip
    assert _git(staging, "status", "--porcelain") == ""
