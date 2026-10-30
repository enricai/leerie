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


def _subtask_worktree(repo: Path, sid: str, run_id: str = RUN_ID) -> Path:
    out = subprocess.run(
        ["bash", str(REPO_ROOT / "scripts" / "new-worktree.sh"), sid, run_id],
        cwd=repo, capture_output=True, text=True, check=True)
    return Path(out.stdout.strip().splitlines()[-1])


def test_subtask_worktrees_a_sigkill_left_dirty_are_reset_on_resume(
        tmp_path, monkeypatch):
    """The same invariant for every subtask worktree the resume reuses:
    `new-worktree.sh` reattaches to a registered worktree as it is, so the
    killed implementer's half-made edits met the resumed subtask's first
    measurement and its next worker. Commits and ignored files stay; a
    worktree of another run is not touched."""
    repo, _staging = _repo(tmp_path, monkeypatch)
    wt = _subtask_worktree(repo, "feat-001")
    (wt / "done.py").write_text("x = 1\n")
    _git(wt, "add", "done.py")
    _git(wt, "commit", "-q", "-m", "committed half")
    tip = _git(wt, "rev-parse", "HEAD")
    (wt / "node_modules").mkdir()
    (wt / "node_modules" / "dep.js").write_text("dep\n")
    (wt / "f.txt").write_text("half done\n")              # killed mid-edit
    (wt / "scratch.py").write_text("y = 2\n")
    other_run = "20261029-other-run"
    subprocess.run(["bash", str(REPO_ROOT / "scripts" / "setup-run.sh"),
                    other_run, "main"], cwd=repo, check=True,
                   capture_output=True)
    other = _subtask_worktree(repo, "feat-001", other_run)
    (other / "f.txt").write_text("another run's live edit\n")
    _setup(repo)                                       # the resume's setup
    assert _git(wt, "status", "--porcelain", "--untracked-files=all") == ""
    assert _git(wt, "rev-parse", "HEAD") == tip        # commits kept
    assert (wt / "done.py").is_file()
    assert (wt / "node_modules" / "dep.js").is_file()  # ignored: kept
    assert (other / "f.txt").read_text() == "another run's live edit\n"
    # new-worktree.sh then reattaches to the clean worktree.
    assert _subtask_worktree(repo, "feat-001") == wt
    assert _git(wt, "status", "--porcelain", "--untracked-files=all") == ""


def test_a_read_only_directory_a_killed_test_left_does_not_stop_the_resume(
        tmp_path, monkeypatch):
    """`git clean` and `git reset --hard` fail with "Permission denied" in a
    read-only directory on every runtime, and setup-run.sh runs under
    `set -e`: every resume stopped. The directories are the orchestrator's
    own, so `leerie_reset_worktree` restores the write bit first."""
    repo, _staging = _repo(tmp_path, monkeypatch)
    wt = _subtask_worktree(repo, "feat-001")
    (wt / "pkg").mkdir()
    (wt / "pkg" / "a.txt").write_text("a\n")
    _git(wt, "add", "pkg")
    _git(wt, "commit", "-q", "-m", "pkg")
    (wt / "pkg" / "a.txt").write_text("half\n")
    (wt / "pkg" / "tmp").mkdir()
    (wt / "pkg" / "tmp" / "f").write_text("x\n")
    for d in (wt / "pkg" / "tmp", wt / "pkg"):
        d.chmod(0o555)
    try:
        _setup(repo)
        assert _git(wt, "status", "--porcelain",
                    "--untracked-files=all") == ""
        assert (wt / "pkg" / "a.txt").read_text() == "a\n"
    finally:
        subprocess.run(["chmod", "-R", "u+w", str(wt)], check=False)


def test_a_staging_a_worker_detached_is_back_on_the_run_branch(
        tmp_path, monkeypatch):
    """Review round 15 H2: `reset --hard HEAD` kept a detached HEAD, so the
    resume started on the worker's detached commit, not the run branch."""
    repo, staging = _repo(tmp_path, monkeypatch)
    branch = _git(staging, "symbolic-ref", "--short", "HEAD")
    tip = _git(staging, "rev-parse", "HEAD")
    _git(staging, "checkout", "-q", "--detach")
    (staging / "f.txt").write_text("worker fix\n")
    _git(staging, "commit", "-qam", "fix on detached head")
    _setup(repo)
    assert _git(staging, "symbolic-ref", "--short", "HEAD") == branch
    assert _git(staging, "rev-parse", "HEAD") == tip
    assert _git(staging, "status", "--porcelain",
                "--untracked-files=all") == ""


def test_a_detached_subtask_worktree_is_found_by_path_and_restored(
        tmp_path, monkeypatch):
    """Review round 15 H2: the reset loop picked subtask worktrees by the
    `branch` line of `git worktree list --porcelain`, which a detached one
    lacks, so its dirt survived the resume."""
    repo, _staging = _repo(tmp_path, monkeypatch)
    wt = _subtask_worktree(repo, "feat-001")
    (wt / "done.py").write_text("x = 1\n")
    _git(wt, "add", "done.py")
    _git(wt, "commit", "-q", "-m", "committed half")
    tip = _git(wt, "rev-parse", "HEAD")
    _git(wt, "checkout", "-q", "--detach", "HEAD~1")
    (wt / "f.txt").write_text("killed mid-edit\n")
    (wt / "scratch.py").write_text("y = 2\n")
    _setup(repo)
    assert _git(wt, "symbolic-ref", "--short", "HEAD") == \
        f"leerie/subtasks/{RUN_ID}/feat-001"
    assert _git(wt, "rev-parse", "HEAD") == tip
    assert (wt / "done.py").is_file()
    assert _git(wt, "status", "--porcelain", "--untracked-files=all") == ""
