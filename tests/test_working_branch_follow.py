"""The run follows its recorded working branch, not whatever is checked out
(DESIGN §12 L4).

Once the checkout sentinel stopped halting on a mid-run change, a branch
switch in the real checkout became reachable without a human look: measured,
one halted run saw HEAD move onto a commit from ANOTHER run's branch (the
shape of an operator checking that branch out to test it). `setup-run.sh`
cut the run branch from the live HEAD and `planning-worktree.sh` mirrored
it, so the run would silently re-base onto the wrong branch.

Real git repos and the real scripts throughout. Each "follows the branch"
case is paired with the fallback it must not break (no argument → live HEAD)
and with a pull of the same branch, which must still be followed.
"""
from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path

import pytest

from tests.conftest import run_git_cwd_kw as _git

REPO_ROOT = Path(__file__).resolve().parent.parent
SETUP_RUN = REPO_ROOT / "scripts" / "setup-run.sh"
PLANNING_WT = REPO_ROOT / "scripts" / "planning-worktree.sh"


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git("init", "-q", "-b", "main", cwd=repo)
    _git("config", "user.email", "test@leerie.local", cwd=repo)
    _git("config", "user.name", "leerie-test", cwd=repo)
    _git("config", "commit.gpgsign", "false", cwd=repo)
    (repo / "file.txt").write_text("main\n")
    _git("add", "file.txt", cwd=repo)
    _git("commit", "-q", "-m", "main", cwd=repo)
    return repo


def _sha(repo: Path, ref: str) -> str:
    return _git("rev-parse", ref, cwd=repo).stdout.strip()


def _switch_to_other_branch(repo: Path) -> str:
    """The incident shape: the operator checks out a different branch
    carrying a commit `main` does not have. Returns that commit."""
    _git("checkout", "-q", "-b", "other", cwd=repo)
    (repo / "file.txt").write_text("other\n")
    _git("commit", "-q", "-am", "other", cwd=repo)
    return _sha(repo, "HEAD")


def _run(script: Path, repo: Path, state: Path, *args: str):
    env = dict(os.environ)
    env["LEERIE_STATE_DIR"] = str(state)
    return subprocess.run(["bash", str(script), "r1", *args], cwd=str(repo),
                          capture_output=True, text=True, check=False,
                          env=env)


# --- setup-run.sh -----------------------------------------------------------

class TestSetupRun:
    def test_branch_switch_does_not_rebase_the_run(self, tmp_path):
        repo = _make_repo(tmp_path)
        main_sha = _sha(repo, "main")
        other_sha = _switch_to_other_branch(repo)
        r = _run(SETUP_RUN, repo, tmp_path / "state", "main")
        assert r.returncode == 0, r.stderr
        run_sha = _sha(repo, "leerie/runs/r1")
        assert run_sha == main_sha
        assert run_sha != other_sha
        wb = tmp_path / "state" / "runs" / "r1" / "working-branch"
        assert wb.read_text().strip() == "main"

    def test_a_pull_of_the_working_branch_is_followed(self, tmp_path):
        repo = _make_repo(tmp_path)
        (repo / "file.txt").write_text("pulled\n")
        _git("commit", "-q", "-am", "pulled", cwd=repo)
        r = _run(SETUP_RUN, repo, tmp_path / "state", "main")
        assert r.returncode == 0, r.stderr
        assert _sha(repo, "leerie/runs/r1") == _sha(repo, "main")

    def test_no_argument_keeps_the_live_head(self, tmp_path):
        """Callers that pass nothing (collect-subtrees.sh, a detached
        start) keep today's behaviour."""
        repo = _make_repo(tmp_path)
        other_sha = _switch_to_other_branch(repo)
        r = _run(SETUP_RUN, repo, tmp_path / "state")
        assert r.returncode == 0, r.stderr
        assert _sha(repo, "leerie/runs/r1") == other_sha
        wb = tmp_path / "state" / "runs" / "r1" / "working-branch"
        assert wb.read_text().strip() == "other"

    @pytest.mark.parametrize("arg", ["HEAD", "no-such-branch"])
    def test_unusable_argument_falls_back_to_head(self, tmp_path, arg):
        repo = _make_repo(tmp_path)
        other_sha = _switch_to_other_branch(repo)
        r = _run(SETUP_RUN, repo, tmp_path / "state", arg)
        assert r.returncode == 0, r.stderr
        assert _sha(repo, "leerie/runs/r1") == other_sha


# --- planning-worktree.sh -----------------------------------------------------

def _planning_head(state: Path) -> str:
    wt = state / "runs" / "r1" / "worktrees" / "planning"
    return _git("rev-parse", "HEAD", cwd=wt).stdout.strip()


class TestPlanningWorktree:
    def test_branch_switch_does_not_move_later_judges(self, tmp_path):
        """First call (no branch yet) mirrors HEAD; a re-entry after the
        operator switched branches must still mirror the working branch."""
        repo = _make_repo(tmp_path)
        state = tmp_path / "state"
        main_sha = _sha(repo, "main")
        assert _run(PLANNING_WT, repo, state).returncode == 0
        _switch_to_other_branch(repo)
        r = _run(PLANNING_WT, repo, state, "main")
        assert r.returncode == 0, r.stderr
        assert _planning_head(state) == main_sha

    def test_a_pull_of_the_working_branch_is_followed(self, tmp_path):
        """Header reason 2: a sibling run's merged PR must be seen."""
        repo = _make_repo(tmp_path)
        state = tmp_path / "state"
        assert _run(PLANNING_WT, repo, state, "main").returncode == 0
        (repo / "file.txt").write_text("pulled\n")
        _git("commit", "-q", "-am", "pulled", cwd=repo)
        assert _run(PLANNING_WT, repo, state, "main").returncode == 0
        assert _planning_head(state) == _sha(repo, "main")

    def test_no_argument_keeps_the_live_head(self, tmp_path):
        repo = _make_repo(tmp_path)
        state = tmp_path / "state"
        other_sha = _switch_to_other_branch(repo)
        assert _run(PLANNING_WT, repo, state).returncode == 0
        assert _planning_head(state) == other_sha


# --- orchestrator wiring ----------------------------------------------------

class _St:
    def __init__(self, data):
        self.data = data
        self.run_id = "r1"

    def save(self):
        pass


class TestWiring:
    @pytest.mark.parametrize("wb, expected", [
        ("main", ["main"]),
        ("HEAD", []),
        ("", []),
        (None, []),
    ])
    def test_working_branch_arg(self, leerie, wb, expected):
        data = {} if wb is None else {"working_branch": wb}
        assert leerie._working_branch_arg(_St(data)) == expected

    @pytest.mark.real_planning_worktree
    def test_ensure_planning_worktree_passes_the_branch(
            self, leerie, monkeypatch, tmp_path):
        calls = []

        async def fake(name, *args):
            calls.append((name, args))
            return subprocess.CompletedProcess(
                [], 0, stdout=f"planning-worktree: {tmp_path}\n", stderr="")

        monkeypatch.setattr(leerie, "_run_script", fake)
        st = _St({"working_branch": "main"})
        st.run_dir = tmp_path
        asyncio.run(leerie._ensure_planning_worktree(st))
        assert calls == [("planning-worktree.sh", ("r1", "main"))]

    def test_phase_execute_passes_the_branch_to_setup_run(self, leerie):
        import inspect
        src = inspect.getsource(leerie.phase_execute)
        i = src.index('_run_script("setup-run.sh"')
        assert "*_working_branch_arg(st)" in src[i:i + 120]
