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


def test_prune_must_not_orphan_the_dir_it_then_fails_on(tmp_path: Path) -> None:
    """ORDER PIN: `git worktree prune` must run BEFORE the orphan-dir check.

    Prune is not a passive cleanup — it is itself an operation that CREATES
    the orphaned state (registration dropped, directory left on disk) that
    the orphan check exists to repair. With the check first and prune second
    there was a window nothing covered: prune dropped the entry, the reuse
    grep then missed, and `worktree add` died on the directory prune had
    just orphaned.

    Run 488c42e5 (2026-08-05) lost `bugfix-009-2` exactly this way, AFTER its
    implementer had committed: a mechanical check drove the continuation
    path, this script ran a second time, and the wave refused to close with
    25 of 26 subtasks complete.

    This test reproduces the state prune leaves behind — a registration git
    considers stale plus a directory still on disk — and asserts the script
    recovers instead of dying. It fails if the two blocks are swapped back.
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

    # The implementer commits work — this is what must survive.
    (wt / "work.txt").write_text("implementer output\n")
    _git("add", "work.txt", cwd=wt)
    _git("commit", "-q", "-m", "subtask work", cwd=wt)
    sha = _git("rev-parse", "HEAD", cwd=wt).stdout.strip()

    # Make the entry PRUNABLE while leaving the directory on disk, so the
    # orphan is created by prune itself rather than pre-existing. Removing
    # the worktree's `.git` link file is what git checks: the admin entry's
    # gitdir target vanishes, so `git worktree prune` drops the entry — and
    # the populated directory stays.
    #
    # This distinction is the whole point. An orphan that ALREADY exists when
    # the script starts is handled by either ordering (the pre-existing
    # `test_orphaned_dir_without_registration_is_reclaimed` covers it). Only
    # an orphan that prune CREATES mid-script escapes a guard that ran first.
    admin = repo / ".git" / "worktrees" / "sub-001"
    assert admin.is_dir(), "expected a registration"
    (wt / ".git").unlink()
    assert wt.is_dir(), "the directory must remain — that is the orphan state"
    still_registered = subprocess.run(
        ["git", "worktree", "list", "--porcelain"], cwd=repo,
        capture_output=True, text=True).stdout
    assert str(wt) in still_registered, (
        "precondition: git must still consider it registered, so an "
        "orphan-check-first ordering skips the removal")

    r2 = _run_new_worktree(repo, "sub-001", "run-42")
    assert r2.returncode == 0, (
        "script died on the state prune itself produces: "
        f"{r2.stderr.strip()}")

    # THE LOAD-BEARING ASSERTION: the committed work is re-attached, not lost.
    wt2 = Path(r2.stdout.strip().splitlines()[-1])
    assert _git("rev-parse", "HEAD", cwd=wt2).stdout.strip() == sha, (
        "the retry did not re-attach to the branch — committed work lost")
    assert (wt2 / "work.txt").exists()


def test_prune_precedes_the_orphan_guard_in_source(tmp_path: Path) -> None:
    """Source-order guard. The behavioural test above can pass for the wrong
    reason if a future edit adds a second cleanup; this pins the ordering the
    fix actually depends on, ignoring comments (which discuss both)."""
    src = (Path(__file__).resolve().parent.parent
           / "scripts" / "new-worktree.sh").read_text()
    code = "\n".join(l for l in src.splitlines()
                     if not l.strip().startswith("#"))
    assert code.index("git worktree prune") < code.index('rm -rf "$WT"'), (
        "`git worktree prune` must precede the orphan-directory removal — "
        "prune is what creates the orphan the removal repairs")
    assert code.index('rm -rf "$WT"') < code.index("git worktree add"), (
        "the orphan removal must precede `worktree add`")

