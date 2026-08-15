"""`leerie prune` must attribute worktree registrations correctly, and must
never reap a run that is still executing.

The deregistration added in `e21bb13` compared a **host** path against what
the **container** wrote. That is wrong in two directions at once, and every
test that certified it used a host-path fixture, so none of them could see
either:

- it is a **no-op in the only runtime that produces the defect** — subtask
  worktrees are created inside the container, where the state root is
  bind-mounted at `/leerie-state`, so `.git/worktrees/<n>/gitdir` holds
  `/leerie-state/runs/<id>/...` and the host-side comparison never matches;
- where the comparison **does** match it removes a protection and destroys
  committed work — the stale registration was the accidental thing making
  `git branch -D` fail on a live run's worktree.

These tests build the fixtures the existing ones cannot: a container-spelling
`gitdir`, a live run holding the run-directory flock, a registration orphaned
by something other than this pass, and the two torn/relative `gitdir` shapes.

Each has a recorded control against the shipped code — see the commit message
for the A/B — because a test that passes on both the defect and the fix is not
evidence of either.

See docs/POSTMORTEM-2026-08-14.md, F19/F22.
"""
from __future__ import annotations

import fcntl
import json
import os
import subprocess
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = REPO_ROOT / "leerie"
OLD = time.time() - 60 * 86400

RID = "run-aaa"


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                          text=True, check=False,
                          env={**os.environ, "LC_ALL": "C", "LANGUAGE": ""})


def _repo(tmp_path: Path) -> Path:
    d = tmp_path / "repo"
    d.mkdir()
    assert _git(d, "init", "-q", "-b", "main").returncode == 0
    # Isolate from the operator's global git config: a global
    # `commit.gpgsign` or `core.hooksPath` otherwise makes every commit below
    # fail silently (`check=False`), and an `assert "<branch>" not in out`
    # then passes because the branch was never created.
    for k, v in (("user.email", "t@leerie.local"), ("user.name", "t"),
                 ("commit.gpgsign", "false"), ("core.hooksPath", "/dev/null")):
        assert _git(d, "config", k, v).returncode == 0
    (d / "f").write_text("x")
    assert _git(d, "add", "-A").returncode == 0
    assert _git(d, "commit", "-qm", "base").returncode == 0
    return d


def _subtask_worktree(repo: Path, root: Path, *, integrated: bool) -> Path:
    """A run dir with one subtask worktree holding a unique commit.

    Preconditions are asserted, not assumed. Every `assert "<branch>" not in
    out` in this area passes trivially if setup silently failed, and `_git`
    runs with `check=False`.
    """
    wt = root / "runs" / RID / "worktrees" / "feat-001"
    branch = f"leerie/subtasks/{RID}/feat-001"
    assert _git(repo, "worktree", "add", "-q", "-b", branch,
                str(wt)).returncode == 0
    (wt / "only-copy").write_text("unique")
    assert _git(wt, "add", "-A").returncode == 0
    assert _git(wt, "commit", "-qm", "the only copy").returncode == 0
    assert _git(repo, "branch", "-q", "-f", f"leerie/runs/{RID}",
                branch if integrated else "main").returncode == 0
    assert branch in _branches(repo), "fixture did not create the branch"
    return wt


def _branches(repo: Path) -> list[str]:
    return _git(repo, "for-each-ref", "--format=%(refname:short)",
                "refs/heads/leerie/subtasks").stdout.split()


def _entry(repo: Path) -> Path:
    """The `.git/worktrees/<name>` registration for the subtask worktree."""
    entries = [p for p in (repo / ".git" / "worktrees").iterdir()
               if p.name.startswith("feat-001")]
    assert len(entries) == 1, entries
    return entries[0]


def _terminal(root: Path, **fields) -> Path:
    d = root / "runs" / RID
    d.mkdir(parents=True, exist_ok=True)
    # A stale `finished_at` from an earlier die(): nothing in the launcher or
    # the orchestrator ever clears it, so it persists across every resume.
    (d / "run.json").write_text(json.dumps({"run_id": RID,
                                            "finished_at": "t", **fields}))
    os.utime(d, (OLD, OLD))
    return d


def _prune(root: Path, repo: Path, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["LEERIE_STATE_DIR"] = str(root)
    env["LEERIE_STATE_HOST_DIR"] = str(root)
    env["USER_REPO"] = str(repo)
    r = subprocess.run(["bash", str(LAUNCHER), "prune", *args],
                       capture_output=True, text=True, env=env, cwd=str(repo))
    assert r.returncode == 0, r.stderr
    return r


# ===========================================================================
# D1 — the container spelling
# ===========================================================================

def test_a_container_written_gitdir_is_attributed(tmp_path):
    """The shape production actually writes.

    Control: on the shipped code this prints `removed 0 orphaned subtask
    branch(es)` plus `could not delete a subtask branch: ... used by worktree
    at '/leerie-state/runs/run-aaa/worktrees/feat-001'`, and the branch
    survives — the deregistration never fired.
    """
    root, repo = tmp_path / "state", _repo(tmp_path)
    _subtask_worktree(repo, root, integrated=True)
    _entry(repo).joinpath("gitdir").write_text(
        f"/leerie-state/runs/{RID}/worktrees/feat-001/.git\n")
    _terminal(root)

    out = _prune(root, repo, "--apply").stdout
    assert "could not delete" not in out, out
    assert not _branches(repo), "the branch was not reaped"


def test_a_host_written_gitdir_still_works(tmp_path):
    """Regression control: translating the container prefix must not break
    the spelling the tests were originally written against."""
    root, repo = tmp_path / "state", _repo(tmp_path)
    _subtask_worktree(repo, root, integrated=True)
    _terminal(root)
    assert "could not delete" not in _prune(root, repo, "--apply").stdout
    assert not _branches(repo)


# ===========================================================================
# D2/D5 — liveness beats a timestamp
# ===========================================================================

def test_a_live_run_survives_a_stale_finished_at(tmp_path):
    """The data-loss case, reproduced.

    `finished_at` is never cleared, so a run that die()d once reads as
    terminal forever; only `mtime < cutoff` stood in the way, and
    `--older-than 0` is accepted. Control: on the shipped code this fixture
    loses **both** the branch and the run directory, recoverable only from
    the reflog.
    """
    root, repo = tmp_path / "state", _repo(tmp_path)
    _subtask_worktree(repo, root, integrated=True)
    d = _terminal(root)

    # What State.__init__ holds for the life of the orchestrator. flock is an
    # inode lock and the container bind-mounts this directory, so a host-side
    # probe sees a container-side holder.
    fd = os.open(d, os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        out = _prune(root, repo, "--older-than", "0", "--apply").stdout
    finally:
        os.close(fd)

    assert d.is_dir(), "a live run's directory was deleted"
    assert _branches(repo), "a live run's only copy of its work was deleted"
    assert "removed 0 terminal run dir(s)" in out


def test_the_flock_being_free_is_what_permits_reaping(tmp_path):
    """Anti-vacuity for the test above.

    Without this, "the run survived" could equally mean the liveness gate
    refuses everything — a prune that reaps nothing passes every safety
    assertion ever written for it.
    """
    root, repo = tmp_path / "state", _repo(tmp_path)
    _subtask_worktree(repo, root, integrated=True)
    d = _terminal(root)
    _prune(root, repo, "--older-than", "0", "--apply")
    assert not d.exists(), "the same fixture unlocked must be reaped"


# ===========================================================================
# D3 — orphaned by anything, not only by this pass
# ===========================================================================

def test_a_registration_orphaned_by_an_earlier_pass_is_reaped(tmp_path):
    """Deregistering only what this pass removed leaves a branch unreapable
    forever: a directory removed by an earlier prune, by cleanup.sh or by
    hand leaves a registration no later prune would ever consult.

    Control: on the shipped code the branch survives with `could not delete`.
    """
    root, repo = tmp_path / "state", _repo(tmp_path)
    _subtask_worktree(repo, root, integrated=True)
    import shutil
    shutil.rmtree(root / "runs" / RID)          # gone before this prune ran

    out = _prune(root, repo, "--apply").stdout
    assert "could not delete" not in out, out
    assert not _branches(repo)


def test_a_registration_outside_the_state_root_is_never_touched(tmp_path):
    """The scoping guarantee the docstring makes. Widening attribution from
    "under the dirs this pass removed" to "orphaned anywhere" must not widen
    it past the state root and into the operator's own worktrees."""
    root, repo = tmp_path / "state", _repo(tmp_path)
    _subtask_worktree(repo, root, integrated=True)
    mine = tmp_path / "my-own-worktree"
    assert _git(repo, "worktree", "add", "-q", "-b", "mine",
                str(mine)).returncode == 0
    import shutil
    shutil.rmtree(mine)                          # orphaned, but not ours
    _terminal(root)

    _prune(root, repo, "--apply")
    assert (repo / ".git" / "worktrees" / "my-own-worktree").is_dir(), (
        "prune deregistered a worktree it does not own")


# ===========================================================================
# D4 — torn and relative `gitdir`
# ===========================================================================

@pytest.mark.parametrize("content", ["", "   \n"])
def test_an_empty_gitdir_is_unattributable(tmp_path, content):
    """`Path("").resolve()` returns the process cwd, so a torn `gitdir`
    attributes a registration to whatever directory prune ran from — the exact
    accident the scoping docstring forbids.

    Honest scope: this passes on the shipped code too, because there the
    accident additionally required the cwd to sit under the run dir, and
    prune runs with cwd=repo. It is a precondition for the *widened*
    attribution below — which asks about every orphaned registration, not
    only this pass's — not a reproduction of a live defect."""
    root, repo = tmp_path / "state", _repo(tmp_path)
    _subtask_worktree(repo, root, integrated=True)
    entry = _entry(repo)
    entry.joinpath("gitdir").write_text(content)
    _terminal(root)

    _prune(root, repo, "--apply")
    assert entry.is_dir(), (
        "an unattributable registration was deregistered anyway")


def test_a_relative_gitdir_is_resolved_against_the_entry(tmp_path):
    """git >= 2.48 with `worktree.useRelativePaths=true` stores the path
    relative to the ENTRY directory. Resolving it against the process cwd
    instead makes attribution silently fail."""
    root, repo = tmp_path / "state", _repo(tmp_path)
    wt = _subtask_worktree(repo, root, integrated=True)
    entry = _entry(repo)
    entry.joinpath("gitdir").write_text(
        os.path.relpath(wt / ".git", entry) + "\n")
    _terminal(root)

    out = _prune(root, repo, "--apply").stdout
    assert "could not delete" not in out, out
    assert not _branches(repo)


def test_a_locked_worktree_is_never_deregistered(tmp_path):
    """git's own prune honours `locked`; so must we."""
    root, repo = tmp_path / "state", _repo(tmp_path)
    _subtask_worktree(repo, root, integrated=True)
    entry = _entry(repo)
    entry.joinpath("locked").write_text("held\n")
    import shutil
    shutil.rmtree(root / "runs" / RID)

    _prune(root, repo, "--apply")
    assert entry.is_dir(), "a locked registration was deregistered"
