"""F3: worktree creation is run-scoped, serialised repository-wide, and
reads git's worktree list without SIGPIPE (DESIGN §6 *Never a
repository-global git operation*, "Even leerie's own scope is one run").

Real git, real scripts. Each behaviour here was a run death before: a
state-root-wide prune racing a sibling run's add, the planning worktree
pruning after its orphan check, run names colliding in `.git/worktrees/`,
and `git worktree list | grep -q` reading a registered worktree as absent
once the list passes 4 KiB.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from tests.conftest import run_git_cwd_kw as _git

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "scripts"


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git("init", "-q", "-b", "main", cwd=repo)
    for k, v in (("user.email", "t@leerie.local"), ("user.name", "t"),
                 ("commit.gpgsign", "false")):
        _git("config", k, v, cwd=repo)
    (repo / "src.txt").write_text("v1\n")
    _git("add", "-A", cwd=repo)
    _git("commit", "-q", "-m", "init", cwd=repo)
    return repo


def _script(name: str, repo: Path, state: Path, *args: str,
            path_prefix: str | None = None) -> subprocess.CompletedProcess:
    env = {"PATH": "/usr/bin:/bin", "HOME": str(repo),
           "LEERIE_STATE_DIR": str(state)}
    if path_prefix:
        env["PATH"] = f"{path_prefix}:{env['PATH']}"
    return subprocess.run(["bash", str(SCRIPTS / name), *args],
                          cwd=str(repo), capture_output=True, text=True,
                          env=env, timeout=120)


def _admin_names(repo: Path) -> set[str]:
    d = repo / ".git" / "worktrees"
    return {p.name for p in d.iterdir()} if d.is_dir() else set()


def test_subtask_and_planning_worktrees_carry_the_run_prefix(tmp_path,
                                                             leerie):
    repo, state = _repo(tmp_path), tmp_path / "state"
    rid = "0123456789abcdef0123"
    _git("branch", f"leerie/runs/{rid}", "main", cwd=repo)
    r = _script("new-worktree.sh", repo, state, "bugfix-001", rid)
    assert r.returncode == 0, r.stderr
    wt = Path(r.stdout.strip().splitlines()[-1])
    assert wt.name == "0123456789ab-bugfix-001"
    assert wt == leerie._subtask_worktree_path(state / "runs" / rid,
                                               "bugfix-001").resolve()
    r = _script("planning-worktree.sh", repo, state, rid)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip().endswith("/worktrees/0123456789ab-planning")
    # The admin entries are named after the directories, so two runs'
    # `bugfix-001` no longer compete for one name.
    assert {"0123456789ab-bugfix-001", "0123456789ab-planning"} <= \
        _admin_names(repo)


def test_two_runs_same_sid_get_distinct_admin_entries(tmp_path):
    repo, state = _repo(tmp_path), tmp_path / "state"
    for rid in ("aaaa1111", "bbbb2222"):
        _git("branch", f"leerie/runs/{rid}", "main", cwd=repo)
        r = _script("new-worktree.sh", repo, state, "feat-001", rid)
        assert r.returncode == 0, r.stderr
    assert {"aaaa1111-feat-001", "bbbb2222-feat-001"} <= _admin_names(repo)


def test_a_prune_never_reaches_another_runs_registration(tmp_path):
    """Run-scoped prune: run B's stale registration survives run A's
    new-worktree.sh. A state-root-wide prune removes it."""
    repo, state = _repo(tmp_path), tmp_path / "state"
    for rid in ("runa", "runb"):
        _git("branch", f"leerie/runs/{rid}", "main", cwd=repo)
    r = _script("new-worktree.sh", repo, state, "x-001", "runb")
    assert r.returncode == 0, r.stderr
    wt_b = Path(r.stdout.strip().splitlines()[-1])
    shutil.rmtree(wt_b)          # B's registration is now stale (prunable)
    assert "runb-x-001" in _admin_names(repo)
    r = _script("new-worktree.sh", repo, state, "x-001", "runa")
    assert r.returncode == 0, r.stderr
    assert "runb-x-001" in _admin_names(repo), (
        "run A's prune deleted run B's registration")
    # ...and run B's own next call does clean it up.
    r = _script("new-worktree.sh", repo, state, "y-001", "runb")
    assert r.returncode == 0, r.stderr
    assert "runb-x-001" not in _admin_names(repo)


def test_planning_worktree_prunes_before_its_orphan_check(tmp_path):
    """A planning worktree whose `.git` link is gone is registered but
    prunable. Pruning AFTER the orphan check (the old order) let the check
    skip the directory, the prune then drop the registration, and `worktree
    add` hit the still-present directory: "already exists"."""
    repo, state = _repo(tmp_path), tmp_path / "state"
    r = _script("planning-worktree.sh", repo, state, "r1")
    assert r.returncode == 0, r.stderr
    wt = Path(r.stdout.strip().split("planning-worktree:", 1)[1].strip())
    (wt / ".git").unlink()
    (wt / "stale.txt").write_text("left\n")
    r = _script("planning-worktree.sh", repo, state, "r1")
    assert r.returncode == 0, r.stderr
    assert (wt / "src.txt").read_text() == "v1\n"
    assert not (wt / "stale.txt").exists()


def _pad_worktree_list(repo: Path, tmp_path: Path, n: int = 60) -> None:
    # git lists worktrees sorted by path; "z…" sorts after "state/".
    pad = tmp_path / ("z" * 60)
    for i in range(n):
        _git("worktree", "add", "-q", "--detach",
             str(pad / f"padding-worktree-with-a-long-name-{i:03d}"), "main",
             cwd=repo)


def test_registration_check_survives_a_worktree_list_past_4k(tmp_path):
    """The SIGPIPE case: with the subtask's worktree among the first entries
    of a >4 KiB list, `git worktree list | grep -q` under pipefail read it
    as unregistered, and the repair path then `rm -rf`ed a live worktree.
    The uncommitted file is the witness."""
    repo, state = _repo(tmp_path), tmp_path / "state"
    _git("branch", "leerie/runs/rsig", "main", cwd=repo)
    r = _script("new-worktree.sh", repo, state, "s-001", "rsig")
    assert r.returncode == 0, r.stderr
    wt = Path(r.stdout.strip().splitlines()[-1])
    (wt / "uncommitted.txt").write_text("in progress\n")
    _pad_worktree_list(repo, tmp_path)
    listing = _git("worktree", "list", "--porcelain", cwd=repo).stdout
    assert len(listing.encode()) > 8192
    assert listing.index(f"worktree {wt}") < 4096
    for _ in range(5):
        r = _script("new-worktree.sh", repo, state, "s-001", "rsig")
        assert r.returncode == 0, r.stderr
        assert (wt / "uncommitted.txt").exists(), (
            "a registered worktree read as absent and was deleted")


def test_no_registration_check_pipes_git_into_grep():
    """All eight registration checks read a captured list. A piped
    `git worktree list ... | grep` is the SIGPIPE shape."""
    import re
    offenders = []
    for name in ("setup-run.sh", "planning-worktree.sh", "new-worktree.sh"):
        code = "\n".join(ln for ln in (SCRIPTS / name).read_text()
                         .splitlines() if not ln.strip().startswith("#"))
        offenders += [f"{name}: {m.group(0)}" for m in re.finditer(
            r"git worktree list[^\n|]*\|\s*grep", code)]
        assert 'grep -qxF "worktree $' in code, name
    assert offenders == []


def _fake_git_reporting_live_entry(tmp_path: Path, name: str) -> Path:
    """A `git` wrapper whose `worktree prune -n -v` also reports `name` as
    prunable — the stale verdict a racing `worktree add` produces."""
    real = shutil.which("git")
    bindir = tmp_path / "fakebin"
    bindir.mkdir()
    (bindir / "git").write_text(
        "#!/bin/bash\n"
        'if [ "$1 $2 $3 $4" = "worktree prune -n -v" ]; then\n'
        f'  echo "Removing worktrees/{name}: gitdir file points to '
        'non-existent location" >&2\n'
        "  exit 0\n"
        "fi\n"
        f'exec {real} "$@"\n')
    (bindir / "git").chmod(0o755)
    return bindir


def test_the_shell_prune_rechecks_before_deleting(tmp_path):
    repo, state = _repo(tmp_path), tmp_path / "state"
    _git("branch", "leerie/runs/rchk", "main", cwd=repo)
    r = _script("new-worktree.sh", repo, state, "live-001", "rchk")
    assert r.returncode == 0, r.stderr
    fake = _fake_git_reporting_live_entry(tmp_path, "rchk-live-001")
    run_dir = state / "runs" / "rchk"
    p = subprocess.run(
        ["bash", "-c", f'. "{SCRIPTS / "worktree-lib.sh"}"; '
                       f'prune_leerie_worktrees "{run_dir}"'],
        cwd=str(repo), capture_output=True, text=True,
        env={"PATH": f"{fake}:/usr/bin:/bin", "HOME": str(repo)})
    assert p.returncode == 0, p.stderr
    assert "rchk-live-001" in _admin_names(repo), (
        "a live worktree's registration was deleted on a stale verdict")


def _run_repository(repo: Path, state: Path, rid: str) -> Path:
    """The run's own bare repository, `<run-dir>/repo` (DESIGN §6 *The run's
    own repository*), with its run branch: the registry the Python prune
    reads. Without one it prunes nothing (§6 *The explicit channel*)."""
    run_repo = state / "runs" / rid / "repo"
    run_repo.parent.mkdir(parents=True)
    _git("clone", "-q", "--bare", str(repo), str(run_repo), cwd=repo)
    _git("branch", f"leerie/runs/{rid}", "main", cwd=run_repo)
    return run_repo


def _bare_admin_names(run_repo: Path) -> set[str]:
    d = run_repo / "worktrees"
    return {p.name for p in d.iterdir()} if d.is_dir() else set()


def test_the_python_prune_rechecks_before_deleting(tmp_path, leerie,
                                                   monkeypatch):
    repo, state = _repo(tmp_path), tmp_path / "state"
    run_repo = _run_repository(repo, state, "rpy")
    r = _script("new-worktree.sh", run_repo, state, "live-001", "rpy")
    assert r.returncode == 0, r.stderr
    fake = _fake_git_reporting_live_entry(tmp_path, "rpy-live-001")
    monkeypatch.setenv("PATH", f"{fake}:{os.environ['PATH']}")
    leerie._prune_leerie_worktrees(state / "runs" / "rpy")
    assert "rpy-live-001" in _bare_admin_names(run_repo)
    # Control: once the worktree is really gone, the same prune removes it.
    shutil.rmtree(state / "runs" / "rpy" / "worktrees" / "rpy-live-001")
    leerie._prune_leerie_worktrees(state / "runs" / "rpy")
    assert "rpy-live-001" not in _bare_admin_names(run_repo)


def test_the_python_prune_skips_when_the_repo_lock_is_held(tmp_path, leerie,
                                                           monkeypatch):
    import fcntl
    repo, state = _repo(tmp_path), tmp_path / "state"
    run_repo = _run_repository(repo, state, "rlk")
    r = _script("new-worktree.sh", run_repo, state, "gone-001", "rlk")
    assert r.returncode == 0, r.stderr
    shutil.rmtree(Path(r.stdout.strip().splitlines()[-1]))
    monkeypatch.setattr(leerie, "_WORKTREE_LOCK_WAIT_SEC", 0.3)
    fd = os.open(str(run_repo / "leerie-worktree.lock"),
                 os.O_WRONLY | os.O_CREAT)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        leerie._prune_leerie_worktrees(state / "runs" / "rlk")
        assert "rlk-gone-001" in _bare_admin_names(run_repo), (
            "pruned without the repository lock")
    finally:
        os.close(fd)
    leerie._prune_leerie_worktrees(state / "runs" / "rlk")
    assert "rlk-gone-001" not in _bare_admin_names(run_repo)


def test_a_pre_prefix_worktree_is_moved_with_its_uncommitted_work(tmp_path):
    """A run started before the prefix has its worktree at
    `worktrees/<sid>` with the branch checked out there; resuming must move
    it, not fail "already used by worktree"."""
    repo, state = _repo(tmp_path), tmp_path / "state"
    rid = "legacyrun"
    _git("branch", f"leerie/runs/{rid}", "main", cwd=repo)
    old = state / "runs" / rid / "worktrees" / "feat-001"
    old.parent.mkdir(parents=True)
    _git("worktree", "add", "-q", "-b", f"leerie/subtasks/{rid}/feat-001",
         str(old), f"leerie/runs/{rid}", cwd=repo)
    (old / "wip.txt").write_text("uncommitted\n")
    r = _script("new-worktree.sh", repo, state, "feat-001", rid)
    assert r.returncode == 0, r.stderr
    wt = Path(r.stdout.strip().splitlines()[-1])
    assert wt.name == "legacyrun-feat-001"
    assert (wt / "wip.txt").read_text() == "uncommitted\n"
    assert not old.exists()
