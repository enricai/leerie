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


class TestBranchReapingFailsClosed:
    """`live` is populated ONLY by the loop over `<root>/runs`. Without that
    directory it stays empty, every `leerie/subtasks/*` ref reads as orphaned,
    and `--apply` force-deletes all of them — including branches of runs that
    are executing right now, with unmerged work.

    Reached by a mistyped or unset `LEERIE_STATE_DIR`, a renamed state root, or
    a first run on a fresh install. `test_stale_cache_entries_are_reclaimed`
    already drives this exact path; it passes only because its fixture repo has
    no leerie branches to lose.
    """

    def test_no_runs_dir_reaps_no_branches(self, tmp_path):
        root = tmp_path / "state"
        (root / "repo-map-cache").mkdir(parents=True)   # root exists, runs/ does not
        repo = _repo(tmp_path)
        _git(repo, "branch", "leerie/subtasks/run-a/feat-001")
        _git(repo, "branch", "leerie/subtasks/run-b/feat-002")
        r = _prune(root, repo, "--apply")
        assert r.returncode == 0, r.stderr
        out = _git(repo, "branch", "--format=%(refname:short)").stdout.split()
        assert "leerie/subtasks/run-a/feat-001" in out
        assert "leerie/subtasks/run-b/feat-002" in out

    def test_it_says_why_it_skipped(self, tmp_path):
        root = tmp_path / "state"
        (root / "repo-map-cache").mkdir(parents=True)
        r = _prune(root, _repo(tmp_path), "--apply")
        assert "skipped branch reaping" in r.stdout

    def test_a_present_runs_dir_still_reaps(self, tmp_path):
        """Anti-vacuity: failing closed must not disable reaping outright.

        `runs/` exists and lists no live run, so the orphan really is an
        orphan and must still go.
        """
        root = tmp_path / "state"
        (root / "runs").mkdir(parents=True)
        repo = _repo(tmp_path)
        _git(repo, "branch", "leerie/subtasks/dead-run/feat-001")
        assert _prune(root, repo, "--apply").returncode == 0
        out = _git(repo, "branch", "--format=%(refname:short)").stdout.split()
        assert "leerie/subtasks/dead-run/feat-001" not in out


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
