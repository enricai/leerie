"""Behavioral tests: setup-run.sh must reject a repo with a bare 'leerie' branch.

A pre-existing branch named exactly 'leerie' occupies the ref path that
leerie's namespaced branches (leerie/runs/*, leerie/subtasks/*) need as a
directory in git's loose ref store. Without this guard, the first
`git branch leerie/runs/<id>` crashes with:

  fatal: cannot lock ref 'refs/heads/leerie/runs/<id>':
    'refs/heads/leerie' exists;
    cannot create 'refs/heads/leerie/runs/<id>'

Companion to test_branch_namespaces_dont_collide.py (which covers the
internal runs/ vs subtasks/ collision).
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from tests.conftest import init_git_repo

REPO_ROOT = Path(__file__).resolve().parent.parent
SETUP_RUN_SH = REPO_ROOT / "scripts" / "setup-run.sh"


def test_setup_run_rejects_preexisting_leerie_branch(tmp_path):
    """A branch named 'leerie' must cause setup-run.sh to fail before
    attempting to create leerie/runs/<id>."""
    repo = init_git_repo(tmp_path / "repo")
    subprocess.run(["git", "branch", "leerie"], cwd=repo, check=True)
    r = subprocess.run(
        [str(SETUP_RUN_SH), "test-run-aaaaaa"],
        cwd=repo, capture_output=True, text=True, check=False,
    )
    assert r.returncode != 0, (
        f"setup-run.sh should fail when 'leerie' branch exists; "
        f"got rc={r.returncode}, stdout={r.stdout!r}"
    )
    assert "leerie" in r.stderr.lower()


def test_setup_run_succeeds_without_leerie_branch(tmp_path):
    """Baseline: setup-run.sh works when no conflicting branch exists."""
    repo = init_git_repo(tmp_path / "repo")
    r = subprocess.run(
        [str(SETUP_RUN_SH), "test-run-bbbbbb"],
        cwd=repo, capture_output=True, text=True, check=False,
    )
    assert r.returncode == 0, (
        f"setup-run.sh should succeed; got rc={r.returncode}, "
        f"stderr={r.stderr!r}"
    )


def test_setup_run_succeeds_with_leerie_subdir_branch(tmp_path):
    """A branch named 'leerie/foo' is NOT a conflict — it means 'leerie'
    is already a directory in the ref store, which is what we need."""
    repo = init_git_repo(tmp_path / "repo")
    subprocess.run(["git", "branch", "leerie/foo"], cwd=repo, check=True)
    r = subprocess.run(
        [str(SETUP_RUN_SH), "test-run-cccccc"],
        cwd=repo, capture_output=True, text=True, check=False,
    )
    assert r.returncode == 0, (
        f"setup-run.sh should succeed when 'leerie/foo' exists; "
        f"got rc={r.returncode}, stderr={r.stderr!r}"
    )
