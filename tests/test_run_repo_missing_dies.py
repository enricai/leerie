"""A missing run repository is leerie's own bug, never "no evidence"
(DESIGN §6 *The run's own repository*; review round 17 H1).

Every helper worktree is added in the run's repository (`<run-dir>/repo`),
which `_run_phases` makes before any. Round 17 found two harnesses
building a State without it: their gates did not fail, they degraded — CI
parity recorded every check `no_base_worktree`, the acceptance evaluation
"no evidence either way" — so a run whose repository vanished would ship
with two gates silently empty. `_worktree_add_detached` now dies naming
the missing repository. Each test drives the real path to that die.
"""
from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest


def _git(cwd: Path, *a: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), *a], check=True,
                          capture_output=True, text=True).stdout.strip()


def _state_without_run_repo(leerie, tmp_path):
    repo = tmp_path / "user"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q",
         "--allow-empty", "-m", "base")
    st = leerie.State(tmp_path / "state", "run-x", repo_root=repo)
    st.data = {"task": "x", "verbosity": "quiet"}
    st.save()
    assert leerie._run_repo_of(st.run_dir) is None
    return st, _git(repo, "rev-parse", "HEAD")


def _dies(fn, capsys) -> str:
    with pytest.raises(SystemExit):
        fn()
    return capsys.readouterr().err


def test_a_helper_worktree_add_dies_without_the_run_repository(
        leerie, tmp_path, capsys):
    st, head = _state_without_run_repo(leerie, tmp_path)
    err = _dies(lambda: leerie._worktree_add_detached(
        st, leerie._helper_worktree_path(st, "x"), head), capsys)
    assert "the run's own repository" in err and "missing" in err


def test_ci_parity_dies_instead_of_no_base_worktree(leerie, tmp_path,
                                                    capsys):
    st, head = _state_without_run_repo(leerie, tmp_path)
    err = _dies(lambda: asyncio.run(leerie._ci_worktree(st, "ci-base", head)),
                capsys)
    assert "missing" in err


def test_acceptance_evaluation_dies_instead_of_no_evidence(leerie, tmp_path,
                                                           capsys):
    st, head = _state_without_run_repo(leerie, tmp_path)
    sets = [{"index": 1, "dir": str(tmp_path), "defect_files": [],
             "control_files": [], "support_files": []}]
    err = _dies(lambda: asyncio.run(leerie._evaluate_acceptance_sets(
        st, dict(leerie.DEFAULT_CAPS), str(st.repo_root), sets, "gate")),
        capsys)
    assert "missing" in err and "no evidence" not in err
