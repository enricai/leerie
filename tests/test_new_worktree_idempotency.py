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
    # The prune is now scoped to leerie's own registrations
    # (prune_leerie_worktrees), because a repository-global prune destroys
    # host-side worktrees the container cannot see. The ORDERING invariant this
    # test pins is unchanged.
    assert code.index("prune_leerie_worktrees") < code.index('rm -rf "$WT"'), (
        "the prune must precede the orphan-directory removal — "
        "prune is what creates the orphan the removal repairs")
    assert code.index('rm -rf "$WT"') < code.index("git worktree add"), (
        "the orphan removal must precede `worktree add`")


# --- repair-and-retry: one test per measured `worktree add` failure mode --- #
#
# The pre-checks are CHECK-THEN-ACT against a repo a dozen sibling subtasks
# mutate concurrently, so the add must verify its own outcome. These drive the
# REAL script against a REAL repo, one per mode enumerated against git 2.53.

def _repo(tmp_path: Path) -> Path:
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
    return repo


def test_mode_M1_path_exists_unregistered_is_repaired(tmp_path: Path) -> None:
    """M1: `fatal: '<path>' already exists` — the incident's own error.

    Repair is removing the orphaned directory. The branch is untouched, so the
    retry re-attaches to work an earlier attempt committed."""
    repo = _repo(tmp_path)
    r1 = _run_new_worktree(repo, "sub-001", "run-42")
    wt = Path(r1.stdout.strip().splitlines()[-1])
    (wt / "work.txt").write_text("committed work\n")
    _git("add", "work.txt", cwd=wt)
    _git("commit", "-q", "-m", "work", cwd=wt)
    sha = _git("rev-parse", "HEAD", cwd=wt).stdout.strip()

    # Deregister but leave the populated directory: the M1 state, however it
    # arises (prune, partial cleanup, or a race the evidence cannot name).
    shutil.rmtree(repo / ".git" / "worktrees" / "sub-001")
    assert wt.is_dir()

    r2 = _run_new_worktree(repo, "sub-001", "run-42")
    assert r2.returncode == 0, f"M1 not repaired: {r2.stderr.strip()}"
    wt2 = Path(r2.stdout.strip().splitlines()[-1])
    assert _git("rev-parse", "HEAD", cwd=wt2).stdout.strip() == sha, (
        "committed work lost — the repair must re-attach, not restart")


def test_mode_M3_registered_but_directory_gone_is_repaired(
        tmp_path: Path) -> None:
    """M3: `use 'add -f' to override, or 'prune' or 'remove' to clear`.
    Repair is `git worktree prune`."""
    repo = _repo(tmp_path)
    r1 = _run_new_worktree(repo, "sub-001", "run-42")
    wt = Path(r1.stdout.strip().splitlines()[-1])
    shutil.rmtree(wt)          # directory gone, admin entry left behind
    assert (repo / ".git" / "worktrees" / "sub-001").is_dir()

    r2 = _run_new_worktree(repo, "sub-001", "run-42")
    assert r2.returncode == 0, f"M3 not repaired: {r2.stderr.strip()}"
    assert Path(r2.stdout.strip().splitlines()[-1]).is_dir()


def test_mode_M2_branch_held_elsewhere_is_surfaced_not_forced(
        tmp_path: Path) -> None:
    """M2: the branch is checked out at ANOTHER path.

    Forcing would steal a live sibling's branch, so the script must fail
    loudly instead. Safe to fail: `worktree_setup` is retryable, so the
    subtask retries rather than dying."""
    repo = _repo(tmp_path)
    branch = "leerie/subtasks/run-42/sub-001"
    _git("branch", branch, "leerie/runs/run-42", cwd=repo)
    other = repo / "held-elsewhere"
    _git("worktree", "add", "-q", str(other), branch, cwd=repo)

    r = _run_new_worktree(repo, "sub-001", "run-42")

    assert r.returncode != 0, "must not silently steal a live branch"
    assert other.is_dir(), "the sibling's worktree must be untouched"
    assert _git("rev-parse", "--abbrev-ref", "HEAD",
                cwd=other).stdout.strip() == branch


def test_mode_M4_healthy_worktree_is_reused_with_no_add(
        tmp_path: Path) -> None:
    """ANTI-VACUITY: the repair path must not fire on the healthy case.
    A script that always removed and re-added would pass M1/M3 while
    destroying uncommitted work on every ordinary call."""
    repo = _repo(tmp_path)
    r1 = _run_new_worktree(repo, "sub-001", "run-42")
    wt = Path(r1.stdout.strip().splitlines()[-1])
    (wt / "uncommitted.txt").write_text("in progress\n")

    r2 = _run_new_worktree(repo, "sub-001", "run-42")

    assert r2.returncode == 0
    assert (wt / "uncommitted.txt").exists(), (
        "the healthy reuse path removed the worktree — uncommitted work lost")


def test_race_between_check_and_add_is_repaired(tmp_path: Path) -> None:
    """THE TEST FOR THE REPAIR ITSELF.

    M1/M3 above are handled by the PRE-CHECKS (the orphan guard and the
    prune), so they pass even with the repair removed — verified by mutation.
    The repair only earns its place when the state changes BETWEEN the check
    and the `add` it selected, which is precisely the window a dozen sibling
    subtasks create and precisely what a sequential test cannot produce
    naturally.

    So inject it: a `git` shim that creates the destination directory on the
    FIRST `worktree add` (a sibling landing in the window) and then gets out
    of the way. Without repair the script dies on `already exists`; with it,
    the failure is re-derived, the orphan removed, and the retry succeeds.
    """
    repo = _repo(tmp_path)
    real_git = subprocess.run(["bash", "-lc", "command -v git"],
                              capture_output=True, text=True).stdout.strip()
    assert real_git, "need a real git on PATH"

    shim_dir = tmp_path / "shim"
    shim_dir.mkdir()
    sentinel = tmp_path / "raced"
    (shim_dir / "git").write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "worktree" ] && [ "$2" = "add" ] && '
        f'[ ! -e "{sentinel}" ]; then\n'
        f'  : > "{sentinel}"\n'
        # $3 is the destination path for `worktree add <path> …`
        '  mkdir -p "$3" 2>/dev/null || true\n'
        '  : > "$3/.racer" 2>/dev/null || true\n'
        "fi\n"
        f'exec {real_git} "$@"\n')
    (shim_dir / "git").chmod(0o755)

    env = dict(os.environ, PATH=f"{shim_dir}:{os.environ['PATH']}")
    r = subprocess.run(
        ["bash", str(Path(__file__).resolve().parent.parent
                     / "scripts" / "new-worktree.sh"), "sub-001", "run-42"],
        cwd=repo, capture_output=True, text=True, env=env)

    assert sentinel.exists(), (
        "the shim never fired — the race was not injected and this test "
        "would pass vacuously")
    assert r.returncode == 0, (
        f"the check-then-act race was not repaired: {r.stderr.strip()}")
    assert Path(r.stdout.strip().splitlines()[-1]).is_dir()

