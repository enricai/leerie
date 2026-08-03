"""Behavioral test for scripts/new-worktree.sh idempotency.

Verifies that new-worktree.sh correctly reuses an existing worktree
when called a second time with the same subtask-id and run-id.
Without the WT-canonicalization fix, the reuse check fails when
LEERIE_STATE_DIR is unset (WT is relative, git worktree list
outputs absolute paths), causing the second call to crash with
'fatal: ... already exists'.
"""
import os
import shutil
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "new-worktree.sh"


def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(cwd),
        capture_output=True, text=True, check=False,
    )


def _run_new_worktree(cwd: Path, sid: str, run_id: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.pop("LEERIE_STATE_DIR", None)
    return subprocess.run(
        ["bash", str(SCRIPT), sid, run_id],
        cwd=str(cwd), capture_output=True, text=True, check=False, env=env,
    )


def test_reuse_worktree_when_state_dir_unset(tmp_path: Path) -> None:
    """Second call reuses existing worktree instead of crashing."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git("init", "-q", "-b", "main", cwd=repo)
    _git("config", "user.email", "test@leerie.local", cwd=repo)
    _git("config", "user.name", "leerie-test", cwd=repo)
    _git("config", "commit.gpgsign", "false", cwd=repo)
    (repo / "file.txt").write_text("initial\n")
    _git("add", "file.txt", cwd=repo)
    _git("commit", "-q", "-m", "initial", cwd=repo)
    _git("branch", "leerie/runs/run-42", "main", cwd=repo)

    r1 = _run_new_worktree(repo, "sub-001", "run-42")
    assert r1.returncode == 0, f"first call failed: {r1.stderr}"
    wt1 = r1.stdout.strip().splitlines()[-1]
    assert Path(wt1).is_absolute()
    assert Path(wt1).is_dir()

    r2 = _run_new_worktree(repo, "sub-001", "run-42")
    assert r2.returncode == 0, f"second call failed (reuse path broken): {r2.stderr}"
    wt2 = r2.stdout.strip().splitlines()[-1]
    assert wt2 == wt1


def test_orphaned_dir_without_registration_is_reclaimed(tmp_path: Path) -> None:
    """An unregistered-but-present worktree dir must not crash the retry.

    The failure this pins: a partial cleanup deregisters the worktree but
    leaves the directory on disk. `git worktree add` then refuses with
    "fatal: '<path>' already exists" and `_run_implementer` raises, killing
    the whole run. Neither `git worktree prune` (only drops entries whose
    dir is *gone*) nor `--force` (overrides branch-checked-out and
    path-*missing*, not path-present) recovers it — only removing the
    orphaned directory does.

    The load-bearing assertion is the last one: the subtask branch's commit
    must survive, since the whole point of the continuation path is to keep
    work the implementer already committed.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _git("init", "-q", "-b", "main", cwd=repo)
    _git("config", "user.email", "test@leerie.local", cwd=repo)
    _git("config", "user.name", "leerie-test", cwd=repo)
    _git("config", "commit.gpgsign", "false", cwd=repo)
    (repo / "file.txt").write_text("initial\n")
    _git("add", "file.txt", cwd=repo)
    _git("commit", "-q", "-m", "initial", cwd=repo)
    _git("branch", "leerie/runs/run-42", "main", cwd=repo)

    r1 = _run_new_worktree(repo, "sub-001", "run-42")
    assert r1.returncode == 0, f"first call failed: {r1.stderr}"
    wt = Path(r1.stdout.strip().splitlines()[-1])

    # The implementer commits real work in its worktree.
    (wt / "work.txt").write_text("implementer output\n")
    _git("add", "work.txt", cwd=wt)
    _git("commit", "-q", "-m", "subtask work", cwd=wt)
    branch = "leerie/subtasks/run-42/sub-001"
    tip = _git("rev-parse", branch, cwd=repo).stdout.strip()
    assert tip, "precondition: the subtask branch should carry a commit"

    # Partial cleanup: git's admin entry is gone, the directory survives.
    admin = repo / ".git" / "worktrees" / "sub-001"
    assert admin.is_dir(), "precondition: worktree admin entry should exist"
    shutil.rmtree(admin)
    assert wt.is_dir(), "precondition: the orphaned dir must still be present"
    listing = _git("worktree", "list", "--porcelain", cwd=repo).stdout
    assert f"worktree {wt}" not in listing, "precondition: must be unregistered"

    # The continuation retry must recover rather than crash.
    r2 = _run_new_worktree(repo, "sub-001", "run-42")
    assert r2.returncode == 0, (
        "retry over an orphaned worktree dir failed "
        f"(this is the crash that kills the run): {r2.stderr}")
    assert Path(r2.stdout.strip().splitlines()[-1]) == wt

    assert _git("rev-parse", branch, cwd=repo).stdout.strip() == tip, (
        "the subtask branch tip must survive — reclaiming the directory "
        "must never discard commits the implementer already made")


def test_reuse_worktree_when_state_dir_set(tmp_path: Path) -> None:
    """Reuse also works when LEERIE_STATE_DIR is an absolute path."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git("init", "-q", "-b", "main", cwd=repo)
    _git("config", "user.email", "test@leerie.local", cwd=repo)
    _git("config", "user.name", "leerie-test", cwd=repo)
    _git("config", "commit.gpgsign", "false", cwd=repo)
    (repo / "file.txt").write_text("initial\n")
    _git("add", "file.txt", cwd=repo)
    _git("commit", "-q", "-m", "initial", cwd=repo)
    _git("branch", "leerie/runs/run-42", "main", cwd=repo)

    state_dir = tmp_path / "state"
    env = dict(os.environ)
    env["LEERIE_STATE_DIR"] = str(state_dir)

    def _run(sid: str, run_id: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["bash", str(SCRIPT), sid, run_id],
            cwd=str(repo), capture_output=True, text=True, check=False, env=env,
        )

    r1 = _run("sub-001", "run-42")
    assert r1.returncode == 0, f"first call failed: {r1.stderr}"
    wt1 = r1.stdout.strip().splitlines()[-1]
    assert Path(wt1).is_absolute()

    r2 = _run("sub-001", "run-42")
    assert r2.returncode == 0, f"second call failed (reuse path broken): {r2.stderr}"
    wt2 = r2.stdout.strip().splitlines()[-1]
    assert wt2 == wt1
