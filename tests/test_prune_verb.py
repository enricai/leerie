"""`leerie prune` reclaims state that nothing else reaps.

Measured on one repo after three weeks: **1.5 GB** across 71 run dirs and
23,158 repo-map-cache entries, plus **64 stale `leerie/subtasks/*` branches**
left in the user checkout — while leerie's own preflight dies on low disk
headroom and tells the operator to prune by hand.

See docs/POSTMORTEM-2026-08-14.md, F22.

The verb is dry-run by default: it deletes run directories that may hold the
only record of a paid-for run, so the safe mode is the one you get without
asking for it.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = REPO_ROOT / "leerie"
OLD = time.time() - 60 * 86400


def _run_dir(root: Path, run_id: str, *, old: bool, **fields) -> Path:
    d = root / "runs" / run_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "run.json").write_text(json.dumps({"run_id": run_id, **fields}))
    (d / "state.json").write_text("{}")
    if old:
        os.utime(d, (OLD, OLD))
    return d


def _git(cwd: Path, *args: str):
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                          text=True, check=False)


def _repo(tmp_path: Path) -> Path:
    d = tmp_path / "repo"
    d.mkdir()
    _git(d, "init", "-q", "-b", "main")
    _git(d, "config", "user.email", "t@leerie.local")
    _git(d, "config", "user.name", "t")
    (d / "f").write_text("x")
    _git(d, "add", "-A")
    _git(d, "commit", "-qm", "base")
    return d


def _prune(root: Path, repo: Path, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    # LEERIE_STATE_DIR is the knob the launcher RESOLVES from;
    # LEERIE_STATE_HOST_DIR is its output. Setting only the latter leaves the
    # resolver unsatisfied and the launcher dies before reaching any verb.
    env["LEERIE_STATE_DIR"] = str(root)
    env["LEERIE_STATE_HOST_DIR"] = str(root)
    env["USER_REPO"] = str(repo)
    return subprocess.run(
        ["bash", str(LAUNCHER), "prune", *args],
        capture_output=True, text=True, env=env, cwd=str(repo))


def test_dry_run_is_the_default(tmp_path):
    root = tmp_path / "state"
    d = _run_dir(root, "old-done", old=True, finished_at="t")
    r = _prune(root, _repo(tmp_path))
    assert r.returncode == 0, r.stderr
    assert "dry-run" in r.stdout
    assert d.is_dir(), "a dry run must not delete anything"


def test_apply_removes_a_terminal_old_run(tmp_path):
    root = tmp_path / "state"
    d = _run_dir(root, "old-done", old=True, finished_at="t")
    r = _prune(root, _repo(tmp_path), "--apply")
    assert r.returncode == 0, r.stderr
    assert not d.exists()


@pytest.mark.parametrize("fields", [
    {},                        # never finished — in flight
    {"paused_at": "t"},        # resumable
])
def test_a_non_terminal_run_survives_regardless_of_age(tmp_path, fields):
    """A paused or in-flight run is resumable; age says nothing about it."""
    root = tmp_path / "state"
    d = _run_dir(root, "old-live", old=True, **fields)
    assert _prune(root, _repo(tmp_path), "--apply").returncode == 0
    assert d.is_dir()


def test_a_recent_terminal_run_survives(tmp_path):
    root = tmp_path / "state"
    d = _run_dir(root, "new-done", old=False, finished_at="t")
    assert _prune(root, _repo(tmp_path), "--apply").returncode == 0
    assert d.is_dir()


def test_older_than_is_honoured(tmp_path):
    root = tmp_path / "state"
    d = _run_dir(root, "old-done", old=True, finished_at="t")
    assert _prune(root, _repo(tmp_path), "--older-than", "90", "--apply").returncode == 0
    assert d.is_dir(), "60 days old must survive a 90-day cutoff"


def test_stale_cache_entries_are_reclaimed(tmp_path):
    root = tmp_path / "state"
    (root / "repo-map-cache").mkdir(parents=True)
    old = root / "repo-map-cache" / "a"
    new = root / "repo-map-cache" / "b"
    old.write_text("x"); new.write_text("y")
    os.utime(old, (OLD, OLD))
    assert _prune(root, _repo(tmp_path), "--apply").returncode == 0
    assert not old.exists() and new.exists()


def test_orphaned_subtask_branches_are_removed(tmp_path):
    """Scoped to leerie/subtasks/<run-id>/* whose run dir is gone."""
    root = tmp_path / "state"
    repo = _repo(tmp_path)
    _run_dir(root, "live-run", old=False)
    _git(repo, "branch", "leerie/subtasks/dead-run/feat-001")
    _git(repo, "branch", "leerie/subtasks/live-run/feat-001")
    _git(repo, "branch", "my-own-work")
    assert _prune(root, repo, "--apply").returncode == 0
    out = _git(repo, "branch", "--format=%(refname:short)").stdout.split()
    assert "leerie/subtasks/dead-run/feat-001" not in out
    assert "leerie/subtasks/live-run/feat-001" in out, (
        "a branch belonging to a live run must survive")
    assert "my-own-work" in out, "a user branch is never in scope"


def _unmerged_branch(repo: Path, name: str) -> None:
    """A subtask branch carrying a commit that exists nowhere else.

    This is the shape that matters. A branch created at the base tip holds no
    work and is safe to delete; the destructive case is one an implementer has
    committed to, which is the only copy of that work once
    `_cleanup_on_abnormal_exit(full_purge=False)` has removed its worktree.
    """
    _git(repo, "branch", name)
    _git(repo, "checkout", "-q", name)
    (repo / "impl.txt").write_text("implementer work\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "implementer commit")
    _git(repo, "checkout", "-q", "main")


class TestBranchReapingNeedsPositiveEvidence:
    """Absence is not evidence of death.

    The rule this replaces inferred "orphaned" from "no run dir in THIS state
    root", which is silent about every run the root never owned. Reproduced: a
    paused run whose state lived under one `--state-dir`, pruned from another
    (both pass the `.owner` check when they belong to the same repo), lost the
    only copy of its implementer commits.

    Guarding that on `runs.is_dir()` did not help — the orchestrator creates
    `runs/` unconditionally and nothing removes it, so on any host that has run
    leerie once the guard could never fire again.
    """

    def test_a_run_in_another_state_root_keeps_its_work(self, tmp_path):
        """The reproduced case, verbatim."""
        rootA = tmp_path / "rootA"          # pruned from here
        rootB = tmp_path / "rootB"          # where the live run's state lives
        _run_dir(rootA, "old-done", old=True, finished_at="t")
        _run_dir(rootB, "LIVE", old=False, paused_at="t")
        repo = _repo(tmp_path)
        _unmerged_branch(repo, "leerie/subtasks/LIVE/feat-001")

        assert _prune(rootA, repo, "--apply").returncode == 0
        out = _git(repo, "branch", "--format=%(refname:short)").stdout.split()
        assert "leerie/subtasks/LIVE/feat-001" in out, (
            "a branch holding unmerged implementer commits must survive a "
            "prune run from a state root that never owned it")

    def test_no_runs_dir_keeps_unmerged_work(self, tmp_path):
        root = tmp_path / "state"
        (root / "repo-map-cache").mkdir(parents=True)   # root exists, runs/ absent
        repo = _repo(tmp_path)
        _unmerged_branch(repo, "leerie/subtasks/run-a/feat-001")
        assert _prune(root, repo, "--apply").returncode == 0
        out = _git(repo, "branch", "--format=%(refname:short)").stdout.split()
        assert "leerie/subtasks/run-a/feat-001" in out

    def test_it_reports_what_it_kept(self, tmp_path):
        """Silence would be worse than the old behaviour: an operator running
        `prune` to reclaim disk needs to know a branch was spared and why."""
        root = tmp_path / "state"
        (root / "runs").mkdir(parents=True)
        repo = _repo(tmp_path)
        _unmerged_branch(repo, "leerie/subtasks/run-a/feat-001")
        r = _prune(root, repo, "--apply")
        assert "kept 1 subtask branch(es) with unmerged commits" in r.stdout

    def test_a_merged_orphan_is_still_reaped(self, tmp_path):
        """Anti-vacuity: requiring evidence must not disable reaping.

        A branch with nothing on it beyond the base holds no work, `git branch
        -d` accepts it, and it goes — which is what F22's 64 stale branches
        were.
        """
        root = tmp_path / "state"
        (root / "runs").mkdir(parents=True)
        repo = _repo(tmp_path)
        _git(repo, "branch", "leerie/subtasks/dead-run/feat-001")
        assert _prune(root, repo, "--apply").returncode == 0
        out = _git(repo, "branch", "--format=%(refname:short)").stdout.split()
        assert "leerie/subtasks/dead-run/feat-001" not in out

    def test_a_run_dir_this_prune_removed_is_force_deleted(self, tmp_path):
        """The one case where `-D` is right: this prune established the run is
        terminal and old, so even unmerged commits on its branches are spent."""
        root = tmp_path / "state"
        _run_dir(root, "gone", old=True, finished_at="t")
        repo = _repo(tmp_path)
        _unmerged_branch(repo, "leerie/subtasks/gone/feat-001")
        assert _prune(root, repo, "--apply").returncode == 0
        out = _git(repo, "branch", "--format=%(refname:short)").stdout.split()
        assert "leerie/subtasks/gone/feat-001" not in out

    def test_a_live_runs_branch_is_never_touched(self, tmp_path):
        root = tmp_path / "state"
        _run_dir(root, "live-run", old=False)
        repo = _repo(tmp_path)
        _unmerged_branch(repo, "leerie/subtasks/live-run/feat-001")
        assert _prune(root, repo, "--apply").returncode == 0
        out = _git(repo, "branch", "--format=%(refname:short)").stdout.split()
        assert "leerie/subtasks/live-run/feat-001" in out


def test_apply_removes_a_killed_run(tmp_path):
    """`killed_at` is the second terminal key in the predicate; only
    `finished_at` was exercised on the deletion path."""
    root = tmp_path / "state"
    d = _run_dir(root, "old-killed", old=True, killed_at="t")
    assert _prune(root, _repo(tmp_path), "--apply").returncode == 0
    assert not d.exists()


def test_older_than_accepts_the_equals_form(tmp_path):
    """Both spellings are implemented in the arg loop; only the space-separated
    one was tested."""
    root = tmp_path / "state"
    d = _run_dir(root, "old-done", old=True, finished_at="t")
    assert _prune(root, _repo(tmp_path), "--older-than=90", "--apply").returncode == 0
    assert d.is_dir(), "60 days old must survive a 90-day cutoff"


def test_rejects_a_non_numeric_cutoff(tmp_path):
    r = _prune(tmp_path / "state", _repo(tmp_path), "--older-than", "soon")
    assert r.returncode == 1 and "whole number" in r.stderr


def test_help_exits_clean(tmp_path):
    r = _prune(tmp_path / "state", _repo(tmp_path), "--help")
    assert r.returncode == 0 and "Usage: leerie prune" in r.stderr


def test_reports_reclaimed_size(tmp_path):
    root = tmp_path / "state"
    d = _run_dir(root, "old-done", old=True, finished_at="t")
    (d / "big.log").write_text("x" * 200_000)
    os.utime(d, (OLD, OLD))
    r = _prune(root, _repo(tmp_path))
    assert "MiB" in r.stdout


def _integrated_branch(repo: Path, run_id: str, sid: str) -> None:
    """A subtask branch whose work is merged into its RUN branch.

    This is the shape `git branch -d` cannot recognise: `-d` checks
    merged-into-HEAD, and a subtask branch is merged into `leerie/runs/<id>`,
    never into `main`. So a fully integrated, long-pushed branch is refused
    exactly like one holding unique work.
    """
    br = f"leerie/subtasks/{run_id}/{sid}"
    _git(repo, "branch", f"leerie/runs/{run_id}")
    _git(repo, "checkout", "-q", "-b", br, "main")
    (repo / f"{sid}.txt").write_text("work\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", f"{sid} work")
    _git(repo, "checkout", "-q", f"leerie/runs/{run_id}")
    _git(repo, "merge", "-q", "--no-ff", "-m", "integrate", br)
    _git(repo, "checkout", "-q", "main")


class TestStaleRegistrationsDoNotBlockReaping:
    """Removing a run dir orphans its worktree REGISTRATION.

    `shutil.rmtree` deletes `<run>/worktrees/<sid>/` but leaves
    `.git/worktrees/<sid>` behind, and git then refuses `git branch -D` with
    "cannot delete branch … used by worktree at …". Every branch of every run
    prune removed therefore survived — and the rc=1 was reported as "unmerged
    commits", which `-D` never returns. Reproduced end to end before the fix.
    """

    def test_a_removed_runs_branch_is_actually_reaped(self, tmp_path):
        root = tmp_path / "state"
        _run_dir(root, "RUN1", old=True, finished_at="t")
        repo = _repo(tmp_path)
        _git(repo, "worktree", "add", "-q",
             str(root / "runs" / "RUN1" / "worktrees" / "feat-001"),
             "-b", "leerie/subtasks/RUN1/feat-001")
        os.utime(root / "runs" / "RUN1", (OLD, OLD))
        assert _prune(root, repo, "--apply").returncode == 0
        out = _git(repo, "branch", "--format=%(refname:short)").stdout.split()
        assert "leerie/subtasks/RUN1/feat-001" not in out, (
            "the run dir was removed, so its branch must go too — a leftover "
            "worktree registration is not a reason to keep it")

    def test_a_blocked_delete_is_not_reported_as_unmerged(self, tmp_path):
        """`-D` never refuses for unmergedness, so that message was a lie."""
        root = tmp_path / "state"
        _run_dir(root, "RUN1", old=True, finished_at="t")
        repo = _repo(tmp_path)
        _git(repo, "worktree", "add", "-q",
             str(root / "runs" / "RUN1" / "worktrees" / "feat-001"),
             "-b", "leerie/subtasks/RUN1/feat-001")
        os.utime(root / "runs" / "RUN1", (OLD, OLD))
        r = _prune(root, repo, "--apply")
        assert "with unmerged commits" not in r.stdout, r.stdout


class TestIntegratedBranchesAreReclaimed:
    """The tier that keeps the feature useful.

    Without it, `-d` refuses every real subtask branch and prune reclaims only
    zero-commit ones — F22's 64 stale branches would be unreclaimable.
    """

    def test_an_integrated_branch_of_an_unknown_run_is_reaped(self, tmp_path):
        root = tmp_path / "state"
        (root / "runs").mkdir(parents=True)          # this root never knew GHOST
        repo = _repo(tmp_path)
        _integrated_branch(repo, "GHOST", "done")
        assert _prune(root, repo, "--apply").returncode == 0
        out = _git(repo, "branch", "--format=%(refname:short)").stdout.split()
        assert "leerie/subtasks/GHOST/done" not in out, (
            "its commits are reachable from leerie/runs/GHOST, so the branch "
            "is redundant — that is positive evidence, not absence")

    def test_unique_work_beside_it_still_survives(self, tmp_path):
        """Anti-vacuity: the tier must not reap by run-id alone."""
        root = tmp_path / "state"
        (root / "runs").mkdir(parents=True)
        repo = _repo(tmp_path)
        _integrated_branch(repo, "GHOST", "done")
        _unmerged_branch(repo, "leerie/subtasks/GHOST/pending")
        assert _prune(root, repo, "--apply").returncode == 0
        out = _git(repo, "branch", "--format=%(refname:short)").stdout.split()
        assert "leerie/subtasks/GHOST/pending" in out
        assert "leerie/subtasks/GHOST/done" not in out


def test_dry_run_and_apply_agree(tmp_path):
    """The default mode must predict the outcome it is previewing.

    It appended every candidate without probing mergedness, so it said "would
    remove 3" where apply removed 1 and kept 2 — and never printed the `kept`
    line at all, which is the one thing an operator needs before choosing
    `--apply`.
    """
    root = tmp_path / "state"
    (root / "runs").mkdir(parents=True)
    repo = _repo(tmp_path)
    _integrated_branch(repo, "GHOST", "done")
    _unmerged_branch(repo, "leerie/subtasks/GHOST/pending")

    dry = _prune(root, repo)
    n_dry = int(re.search(r"would remove (\d+) orphaned", dry.stdout).group(1))
    kept_dry = re.search(r"kept (\d+) subtask", dry.stdout)

    applied = _prune(root, repo, "--apply")
    n_app = int(re.search(r"removed (\d+) orphaned", applied.stdout).group(1))
    kept_app = re.search(r"kept (\d+) subtask", applied.stdout)

    assert n_dry == n_app, f"dry-run said {n_dry}, apply did {n_app}"
    assert (kept_dry is None) == (kept_app is None)
    if kept_dry:
        assert kept_dry.group(1) == kept_app.group(1)
