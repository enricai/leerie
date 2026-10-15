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
import inspect
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
        wb = tmp_path / "state" / "runs" / "r1" / "working-branch"
        if arg == "HEAD":
            # No branch was recorded upstream: the live one is the answer.
            assert wb.read_text().strip() == "other"
            assert "warning" not in r.stderr
        else:
            # A recorded-but-vanished branch stays recorded, so this file
            # keeps agreeing with the orchestrator's working_branch, and the
            # fallback is announced rather than silent.
            assert wb.read_text().strip() == arg
            assert f"working branch '{arg}' not found" in r.stderr


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

    def test_vanished_branch_warns_and_uses_head(self, tmp_path):
        repo = _make_repo(tmp_path)
        state = tmp_path / "state"
        other_sha = _switch_to_other_branch(repo)
        r = _run(PLANNING_WT, repo, state, "gone")
        assert r.returncode == 0, r.stderr
        assert _planning_head(state) == other_sha
        assert "working branch 'gone' not found" in r.stderr


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
        src = inspect.getsource(leerie.phase_execute)
        i = src.index('_run_script("setup-run.sh"')
        assert "*_working_branch_arg(st)" in src[i:i + 120]


# --- satisfied-probe cache key ------------------------------------------------

class TestSatisfiedCacheKey:
    """`satisfied_probe_cache` verdicts are keyed on `base_sha`; the probe
    judges the planning worktree. The key must name that same tree, or a
    branch switch files verdicts under the wrong sha and a later advance of
    the working branch never invalidates them (DESIGN §6)."""

    def test_key_follows_the_working_branch_after_a_switch(
            self, leerie, tmp_path):
        repo = _make_repo(tmp_path)
        main_sha = _sha(repo, "main")
        other_sha = _switch_to_other_branch(repo)
        st = _St({"working_branch": "main"})
        key = asyncio.run(leerie._base_tree_sha(str(repo), st))
        assert key == main_sha
        assert key != other_sha

    @pytest.mark.parametrize("wb", [None, "HEAD", "no-such-branch"])
    def test_key_falls_back_to_head(self, leerie, tmp_path, wb):
        repo = _make_repo(tmp_path)
        other_sha = _switch_to_other_branch(repo)
        data = {} if wb is None else {"working_branch": wb}
        key = asyncio.run(leerie._base_tree_sha(str(repo), _St(data)))
        assert key == other_sha

    def test_key_equals_the_tree_the_probe_judges(self, leerie, tmp_path):
        """The agreement pin: whatever the real script mirrors, the key
        names. Discriminates against any independently derived key."""
        repo = _make_repo(tmp_path)
        state = tmp_path / "state"
        _switch_to_other_branch(repo)
        st = _St({"working_branch": "main"})
        r = _run(PLANNING_WT, repo, state, *leerie._working_branch_arg(st))
        assert r.returncode == 0, r.stderr
        key = asyncio.run(leerie._base_tree_sha(str(repo), st))
        assert key == _planning_head(state)

    def test_filter_uses_the_shared_key(self, leerie):
        src = inspect.getsource(leerie._filter_satisfied_subtasks)
        assert "base_sha = await _base_tree_sha(str(repo_root), st)" in src
        assert "_branch_head_sha(str(repo_root))" not in src

    def test_switch_then_advance_reprobes_through_the_real_filter(
            self, leerie, tmp_path, monkeypatch):
        """The incident the cache-key change exists for, end to end through
        `_filter_satisfied_subtasks`: a verdict cached at `main`'s tip, the
        operator switches to another branch, then `main` advances (a sibling
        PR merges) while HEAD stays put. A live-HEAD key would still match
        and trust the stale verdict; the base-tree key must re-probe once
        and re-file the verdict under `main`'s new tip."""
        repo = _make_repo(tmp_path)
        sha_a = _sha(repo, "main")
        _switch_to_other_branch(repo)
        st = leerie.State.__new__(leerie.State)
        st.repo_root = "/leerie-test-user-repo"
        st.run_id = "test-run-switch-then-advance"
        st.run_dir = tmp_path / "run"
        st.run_dir.mkdir()
        st.path = st.run_dir / "state.json"
        st.path.write_text("{}")
        st.data = {
            "planning_worktree": str(st.run_dir / "worktrees" / "planning"),
            "telemetry": {"calls": 0, "cost_usd": 0.0,
                          "input_tokens": 0, "output_tokens": 0},
            "verbosity": "quiet", "worker_count": 0,
            "working_branch": "main",
            "satisfied_probe_cache": {
                "feat-001": {"satisfied": True, "evidence": "at A",
                             "checked": ["file.txt"], "base_sha": sha_a}},
        }
        # main advances while the operator stays on `other`.
        _git("checkout", "-q", "main", cwd=repo)
        (repo / "file.txt").write_text("advanced\n")
        _git("commit", "-q", "-am", "advance", cwd=repo)
        sha_b = _sha(repo, "main")
        _git("checkout", "-q", "other", cwd=repo)
        head_sha = _sha(repo, "HEAD")
        assert head_sha not in (sha_a, sha_b)

        calls: list[str] = []

        async def fake_claude_p(*, user_prompt, sid, **_kw):
            calls.append(sid)
            return {"satisfied": False, "evidence": "not on the new tip"}

        monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
        plans = [{"domain": "d", "status": "ready",
                  "subtasks": [{"id": "feat-001", "title": "feat-001",
                                "success_criteria_seed": "done"}]}]
        res = asyncio.run(leerie._filter_satisfied_subtasks(
            plans, repo, st, {"max_parallel": 4, "max_total_workers": 999},
            {"satisfied_probe": "sonnet"}, {"satisfied_probe": None}))
        assert res is None
        assert calls == ["satisfied_probe-feat-001"]
        assert [s["id"] for s in plans[0]["subtasks"]] == ["feat-001"]
        assert st.data["satisfied_probe_cache"]["feat-001"]["base_sha"] == \
            sha_b


# --- task-reference staging ---------------------------------------------------

def _repo_with_switched_branch_deliverable(tmp_path: Path) -> Path:
    """`main` lacks `feature.py`; branch `other` tracks it and is checked
    out; `report.md` is the operator's untracked task document."""
    repo = _make_repo(tmp_path)
    _git("checkout", "-q", "-b", "other", cwd=repo)
    (repo / "feature.py").write_text("DELIVERED = True\n")
    _git("add", "feature.py", cwd=repo)
    _git("commit", "-q", "-m", "deliverable", cwd=repo)
    (repo / "report.md").write_text("bug report\n")
    return repo


class _TaskSt:
    def __init__(self, repo: Path, task: str) -> None:
        self.repo_root = repo
        self.data = {"task": task}


class TestStagingSkipsTrackedPaths:
    def test_switched_branch_deliverable_is_not_staged(self, leerie, tmp_path):
        """A file the task names that another branch tracks would show the
        satisfied-probe a deliverable the run's tree does not have."""
        repo = _repo_with_switched_branch_deliverable(tmp_path)
        state = tmp_path / "state"
        r = _run(PLANNING_WT, repo, state, "main")
        assert r.returncode == 0, r.stderr
        wt = state / "runs" / "r1" / "worktrees" / "planning"
        leerie._stage_worktree_extras(
            _TaskSt(repo, "fix per report.md and add feature.py"), str(wt))
        assert not (wt / "feature.py").exists()
        # Partner: the untracked task document IS still staged.
        assert (wt / "report.md").read_text() == "bug report\n"

    def test_tracked_in_checkout(self, leerie, tmp_path):
        repo = _repo_with_switched_branch_deliverable(tmp_path)
        assert leerie._tracked_in_checkout(repo, repo / "feature.py") is True
        assert leerie._tracked_in_checkout(repo, repo / "report.md") is False
        (repo / "pkg").mkdir()
        (repo / "pkg" / "m.py").write_text("x = 1\n")
        _git("add", "pkg/m.py", cwd=repo)
        assert leerie._tracked_in_checkout(repo, repo / "pkg") is True
        not_a_repo = tmp_path / "plain"
        not_a_repo.mkdir()
        assert leerie._tracked_in_checkout(
            not_a_repo, not_a_repo / "x.md") is False

    def test_acceptance_copy_applies_the_same_rule(self, leerie):
        src = inspect.getsource(leerie._write_acceptance_set)
        i = src.index("_glob_task_references(task, st.repo_root)")
        assert "_tracked_in_checkout(Path(st.repo_root), f)" in src[i:i + 700]
