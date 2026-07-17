"""Behavioral test for scripts/setup-run.sh staging-worktree idempotency.

Verifies that setup-run.sh re-creates (or reuses) the staging worktree on a
--resume instead of dying with "fatal: '...' already exists".

Two failures are pinned, both measured against the real script before the fix:

1. Absolute LEERIE_STATE_DIR (the container case, `/leerie-state`). The old
   guard `grep -q "worktree .*/${STAGING_WT}$"` expanded to a double slash and
   never matched, so `git worktree add` ran unconditionally. Every --resume
   over a surviving staging dir died in phase 4; the *second* identical resume
   then succeeded, because the first one's die() unwound into
   `_cleanup_on_abnormal_exit`, which removed the directory as a side effect.

2. An orphaned staging dir (git admin entry gone, directory on disk) — what a
   SIGKILLed run leaves behind on Fly, where `machine stop` means cleanup never
   runs. Neither `git worktree prune` nor `git worktree add --force` recovers
   this; only removing the directory does.

There is deliberately NO test for the LEERIE_STATE_DIR-unset (relative) case:
it passes against the *unfixed* script and so proves nothing. With a relative
$STAGING_WT the old pattern's `.*/` accidentally absorbs the leading
directories and the unescaped `.` in `.leerie` matches literally — the guard
worked by luck. Canonicalizing is still correct there (git resolves symlinks, a
relative path does not; see new-worktree.sh:20-23), but it cannot carry a
regression test. Structural cover for that path lives in
test_setup_run_script_paths.py instead.

Modeled on tests/test_new_worktree_idempotency.py — the sibling script that hit
this same class of bug first.
"""
import os
import shutil
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "setup-run.sh"


def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(cwd),
        capture_output=True, text=True, check=False,
    )


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git("init", "-q", "-b", "main", cwd=repo)
    _git("config", "user.email", "test@leerie.local", cwd=repo)
    _git("config", "user.name", "leerie-test", cwd=repo)
    _git("config", "commit.gpgsign", "false", cwd=repo)
    (repo / "file.txt").write_text("initial\n")
    _git("add", "file.txt", cwd=repo)
    _git("commit", "-q", "-m", "initial", cwd=repo)
    return repo


def _run_setup(repo: Path, run_id: str, state_dir: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["LEERIE_STATE_DIR"] = str(state_dir)
    return subprocess.run(
        ["bash", str(SCRIPT), run_id],
        cwd=str(repo), capture_output=True, text=True, check=False, env=env,
    )


def _staging_path(res: subprocess.CompletedProcess) -> str | None:
    for line in res.stdout.splitlines():
        if line.startswith("staging-worktree: "):
            return line.split(": ", 1)[1]
    return None


def test_second_call_reuses_staging_when_state_dir_absolute(tmp_path: Path) -> None:
    """A second identical call must reuse the staging worktree, not crash.

    This is the reported bug: every `--resume` inside the container (where
    LEERIE_STATE_DIR=/leerie-state) failed on the first attempt and succeeded
    on the second. Against the unfixed script this assertion fails 128 != 0.
    """
    repo = _make_repo(tmp_path)
    state = tmp_path / "state"

    r1 = _run_setup(repo, "run-42", state)
    assert r1.returncode == 0, f"first call failed: {r1.stderr}"
    wt1 = _staging_path(r1)
    assert wt1 is not None and Path(wt1).is_dir()

    r2 = _run_setup(repo, "run-42", state)
    assert r2.returncode == 0, f"second call failed (reuse path broken): {r2.stderr}"
    assert _staging_path(r2) == wt1


def test_orphaned_staging_dir_is_reclaimed(tmp_path: Path) -> None:
    """An unregistered-but-present staging dir must not crash the resume.

    Simulates the SIGKILL survivor: git's admin entry is gone, the directory
    remains. The load-bearing assertion is the last one — the run branch's
    integration commits must survive, since reclaiming the directory exists
    precisely to let a resume re-attach to waves already completed.
    """
    repo = _make_repo(tmp_path)
    state = tmp_path / "state"

    r1 = _run_setup(repo, "run-42", state)
    assert r1.returncode == 0, f"first call failed: {r1.stderr}"
    wt = Path(_staging_path(r1))

    # A completed wave lands an integration commit on the run branch.
    (wt / "wave.txt").write_text("integrated wave 1\n")
    _git("add", "wave.txt", cwd=wt)
    _git("commit", "-q", "-m", "leerie: integrate wave 1", cwd=wt)
    branch = "leerie/runs/run-42"
    tip = _git("rev-parse", branch, cwd=repo).stdout.strip()
    assert tip, "precondition: the run branch should carry a commit"

    # The SIGKILL survivor: admin entry gone, directory still on disk.
    git_dir = Path(_git("rev-parse", "--absolute-git-dir", cwd=repo).stdout.strip())
    admin = git_dir / "worktrees" / "staging"
    assert admin.is_dir(), "precondition: worktree admin entry should exist"
    shutil.rmtree(admin)
    assert wt.is_dir(), "precondition: the orphaned dir must still be present"
    listing = _git("worktree", "list", "--porcelain", cwd=repo).stdout
    assert f"worktree {wt}" not in listing, "precondition: must be unregistered"

    r2 = _run_setup(repo, "run-42", state)
    assert r2.returncode == 0, (
        "resume over an orphaned staging dir failed "
        f"(this is the phase-4 crash): {r2.stderr}")

    assert _git("rev-parse", branch, cwd=repo).stdout.strip() == tip, (
        "the run branch tip must survive — reclaiming the staging directory "
        "must never discard integration commits from completed waves")
