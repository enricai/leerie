"""`git worktree prune` must never reach a worktree leerie does not own.

A bare `git worktree prune` is repository-global and has **no grace period**:
the 3-month `gc.worktreePruneExpire` default applies to `git gc`, which calls
`git worktree prune --expire 3.months.ago`, while a bare prune drops every
registration whose directory is missing, immediately.

leerie's container bind-mounts the user's repository whole, so every container
shares the host's `.git`. A worktree the HOST registered at a path that does not
exist inside the container's mount namespace therefore looks stale to a bare
prune and is destroyed. `scripts/host-finalize.sh` creates exactly such a
worktree, at `/tmp/tmp.XXXX/rebase-<run-id>`. During one run's rebase window a
sibling run spawned three workers, each invoking `new-worktree.sh` and each
running a bare prune; the rebaser then reported its git metadata directory "has
vanished … without any destructive action on my part".

See docs/POSTMORTEM-2026-08-14.md, F19.

These tests run the real helper against real git repositories — the mechanism is
git's, so a stubbed one would prove nothing.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB = REPO_ROOT / "scripts" / "worktree-lib.sh"
_SCRIPTS_WITH_PRUNE = ("new-worktree.sh", "setup-run.sh", "cleanup.sh")


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=str(cwd), check=True,
                          capture_output=True, text=True).stdout


def _repo(tmp_path: Path) -> Path:
    d = tmp_path / "repo"
    d.mkdir()
    _git(d, "init", "-q", "-b", "main")
    _git(d, "config", "user.email", "t@leerie.local")
    _git(d, "config", "user.name", "leerie test")
    (d / "f.txt").write_text("x\n")
    _git(d, "add", "-A")
    _git(d, "commit", "-qm", "base")
    return d


def _registered(repo: Path) -> set[str]:
    out = _git(repo, "worktree", "list", "--porcelain")
    return {l[len("worktree "):] for l in out.splitlines()
            if l.startswith("worktree ")}


def _run_prune(repo: Path, root: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c",
         f'set -euo pipefail; . "{LIB}"; cd "{repo}"; '
         f'prune_leerie_worktrees "{root}"'],
        capture_output=True, text=True)


def _make_stale(repo: Path, path: Path, branch: str) -> None:
    """Register a worktree, then delete its directory — the stale shape."""
    path.parent.mkdir(parents=True, exist_ok=True)
    _git(repo, "worktree", "add", "-q", "-b", branch, str(path))
    subprocess.run(["rm", "-rf", str(path)], check=True)


def test_a_stale_leerie_worktree_is_pruned(tmp_path):
    repo = _repo(tmp_path)
    root = tmp_path / "state"
    wt = root / "runs" / "r1" / "worktrees" / "feat-001"
    _make_stale(repo, wt, "leerie/subtasks/r1/feat-001")
    assert str(wt.resolve()) in _registered(repo)

    r = _run_prune(repo, root)
    assert r.returncode == 0, r.stderr
    assert str(wt.resolve()) not in _registered(repo), (
        "a stale registration under leerie's own root must be dropped — that is "
        "the whole job the bare prune was doing")


def test_a_stale_worktree_OUTSIDE_the_root_survives(tmp_path):
    """The defect, directly: the host's rebase worktree must not be touched."""
    repo = _repo(tmp_path)
    root = tmp_path / "state"
    root.mkdir()
    host_wt = tmp_path / "tmp.HOSTXYZ" / "rebase-abc123"
    _make_stale(repo, host_wt, "leerie/runs/abc123")
    assert str(host_wt.resolve()) in _registered(repo)

    r = _run_prune(repo, root)
    assert r.returncode == 0, r.stderr
    assert str(host_wt.resolve()) in _registered(repo), (
        "a registration outside leerie's state root is not ours to prune; "
        "destroying it is what killed a live rebase mid-operation")


def test_a_bare_prune_would_have_destroyed_it(tmp_path):
    """Falsification control.

    Without this, the test above proves only that the fixture survives
    something — not that the scoping is what saves it.
    """
    repo = _repo(tmp_path)
    host_wt = tmp_path / "tmp.HOSTXYZ" / "rebase-abc123"
    _make_stale(repo, host_wt, "leerie/runs/abc123")
    assert str(host_wt.resolve()) in _registered(repo)

    _git(repo, "worktree", "prune")
    assert str(host_wt.resolve()) not in _registered(repo), (
        "if a bare prune no longer destroys this, git's behaviour changed and "
        "the scoping rationale needs re-deriving")


def test_a_LIVE_leerie_worktree_survives(tmp_path):
    """Dropping a live registration lost a completed subtask once.

    Run 488c42e5 lost `bugfix-009-2` after its implementer had committed,
    because a sibling's prune deregistered a worktree that was still in use.
    """
    repo = _repo(tmp_path)
    root = tmp_path / "state"
    wt = root / "runs" / "r1" / "worktrees" / "feat-002"
    wt.parent.mkdir(parents=True)
    _git(repo, "worktree", "add", "-q", "-b", "leerie/subtasks/r1/feat-002",
         str(wt))

    r = _run_prune(repo, root)
    assert r.returncode == 0, r.stderr
    assert str(wt.resolve()) in _registered(repo)
    assert wt.is_dir()


def test_prune_never_fails_its_caller(tmp_path):
    """All three call sites run under `set -e`; this is housekeeping."""
    repo = _repo(tmp_path)
    for root in (tmp_path / "does-not-exist", tmp_path):
        r = _run_prune(repo, root)
        assert r.returncode == 0, (root, r.stderr)


def test_outside_a_git_repo_is_a_silent_no_op(tmp_path):
    r = _run_prune(tmp_path, tmp_path)
    assert r.returncode == 0, r.stderr


@pytest.mark.parametrize("script", _SCRIPTS_WITH_PRUNE)
def test_no_script_runs_a_bare_prune(script):
    """The sweep: every call site uses the scoped helper.

    Comments are stripped first — the replacement comments necessarily name the
    construct they forbid, so a raw scan matches the prose explaining it.
    """
    src = (REPO_ROOT / "scripts" / script).read_text()
    code = "\n".join(l for l in src.splitlines()
                     if not l.lstrip().startswith("#"))
    assert not re.search(r"^\s*git worktree prune\b", code, re.M), (
        f"{script} still runs a repository-global prune; use "
        "prune_leerie_worktrees \"$LEERIE_ROOT\"")
    assert "prune_leerie_worktrees" in code, (
        f"{script} must still prune leerie's own stale registrations — "
        "removing the prune entirely reopens the orphaned-directory failure "
        "new-worktree.sh documents")


@pytest.mark.parametrize("script", _SCRIPTS_WITH_PRUNE)
def test_each_script_sources_the_lib(script):
    src = (REPO_ROOT / "scripts" / script).read_text()
    assert "worktree-lib.sh" in src, script
