"""The Python half of the judgment worktree — `_ensure_planning_worktree` and
`_stage_worktree_extras` (DESIGN §12 *Judgment-worker isolation*).

`tests/test_planning_worktree_script.py` covers the shell script by
subprocess. This file covers the wrapper around it: parsing the path back out,
failing closed when the script fails, and staging the things `git worktree add`
does not carry because git only checks out TRACKED content.

Every test here carries `@pytest.mark.real_planning_worktree`, which opts out
of conftest's `_no_real_planning_worktree` stub — these are the tests whose
SUBJECT is the worktree mechanics. That makes them the one place in the suite
that runs a real `git worktree add`, so each one first `chdir`s into a
throwaway repo and points `LEERIE_STATE_DIR` at a throwaway state root. Without
both, `_run_script` runs with `cwd=os.getcwd()` and `resolve_leerie_root()`
falls back to `<repo>/.leerie` — which is exactly how a full checkout of leerie
ended up inside leerie, gitignored and invisible, until an unrelated scanner
went red on CI. `test_no_worktree_leaks_into_this_repo` is the standing proof
that the containment holds.
"""
from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest

from tests.conftest import run_git_repo_first

pytestmark = pytest.mark.real_planning_worktree


def _init(repo):
    repo.mkdir(parents=True, exist_ok=True)
    run_git_repo_first(repo, "init", "-q", ".")
    run_git_repo_first(repo, "config", "user.email", "t@t")
    run_git_repo_first(repo, "config", "user.name", "t")
    (repo / "tracked.txt").write_text("tracked\n")
    run_git_repo_first(repo, "add", "-A")
    run_git_repo_first(repo, "commit", "-qm", "init")
    return repo


class _St:
    """Only the attributes the two functions touch."""

    def __init__(self, repo, state_root, run_id="r1"):
        self.repo_root = repo
        self.run_id = run_id
        self.run_dir = state_root / "runs" / run_id
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.data: dict = {}
        self.saves = 0

    def save(self):
        self.saves += 1


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """A throwaway repo + state root, with the process parked inside the repo.

    Both halves are load-bearing — see the module docstring."""
    repo = _init(tmp_path / "repo")
    state = tmp_path / "state"
    monkeypatch.chdir(repo)
    monkeypatch.setenv("LEERIE_STATE_DIR", str(state))
    return repo, state, _St(repo, state)


def _run(leerie, st):
    return asyncio.run(leerie._ensure_planning_worktree(st))


class TestEnsurePlanningWorktree:
    def test_creates_the_worktree_and_records_the_path(self, leerie, env):
        repo, state, st = env
        path = _run(leerie, st)
        assert (state / "runs" / "r1" / "worktrees" / "planning").is_dir()
        assert st.data["planning_worktree"] == path
        assert st.saves >= 1, "the path must be persisted, not just returned"

    def test_returned_path_is_absolute_and_matches_the_checkout(
            self, leerie, env):
        repo, state, st = env
        path = _run(leerie, st)
        assert path.startswith("/")
        assert (run_git_repo_first(path, "rev-parse", "HEAD").stdout.strip()
                == run_git_repo_first(repo, "rev-parse", "HEAD").stdout.strip())

    def test_second_call_is_idempotent(self, leerie, env):
        """It is called twice per run — before phase_classify and again before
        the satisfied-probe sweep — so the second call must succeed against an
        existing worktree rather than failing on `already exists`."""
        repo, state, st = env
        first = _run(leerie, st)
        second = _run(leerie, st)
        assert first == second

    def test_script_failure_dies_and_does_not_fall_back(
            self, leerie, env, monkeypatch):
        """Fail closed. A silent fallback to the real checkout reinstates
        exactly the exposure this exists to remove."""
        repo, state, st = env

        async def failing(_name, *_a):
            return subprocess.CompletedProcess(
                args=[], returncode=1, stdout="", stderr="boom")

        monkeypatch.setattr(leerie, "_run_script", failing)
        with pytest.raises(SystemExit):
            _run(leerie, st)
        assert "planning_worktree" not in st.data, (
            "a failed creation must not leave a path behind for "
            "_judgment_cwd to hand to a worker")

    def test_die_message_is_actionable(self, leerie, env, monkeypatch):
        """It names both remedies. A die() an operator cannot act on is a
        die() they work around by disabling the check."""
        repo, state, st = env
        seen = {}

        async def failing(_name, *_a):
            return subprocess.CompletedProcess(
                args=[], returncode=1, stdout="", stderr="boom")

        def fake_die(msg, code=1):
            seen["msg"] = msg
            raise SystemExit(code)

        monkeypatch.setattr(leerie, "_run_script", failing)
        monkeypatch.setattr(leerie, "die", fake_die)
        with pytest.raises(SystemExit):
            _run(leerie, st)
        assert "cleanup.sh" in seen["msg"] and "prune" in seen["msg"]
        assert "boom" in seen["msg"], "git's own stderr must reach the operator"

    def test_unusable_path_output_dies(self, leerie, env, monkeypatch):
        """rc 0 with nothing parseable is still a failure — the caller would
        otherwise record an empty cwd and every worker would spawn in the
        orchestrator's own directory."""
        repo, state, st = env

        async def chatty(_name, *_a):
            return subprocess.CompletedProcess(
                args=[], returncode=0, stdout="something unexpected\n",
                stderr="")

        monkeypatch.setattr(leerie, "_run_script", chatty)
        with pytest.raises(SystemExit):
            _run(leerie, st)

    def test_staging_runs_after_the_reset(self, leerie, env):
        """Ordering, not presence: the script's `git clean -fd` would delete
        anything staged by a previous call, so staging must follow it. A file
        staged on call one must still be there after call two."""
        repo, state, st = env
        (repo / "brief.md").write_text("the spec\n")     # untracked
        st.data["task"] = "implement what brief.md says"
        _run(leerie, st)
        wt = state / "runs" / "r1" / "worktrees" / "planning"
        assert (wt / "brief.md").exists()
        _run(leerie, st)
        assert (wt / "brief.md").exists(), (
            "staging ran before the reset, so the second call wiped it")


class TestStageWorktreeExtras:
    def test_untracked_task_file_is_staged(self, leerie, env):
        """The measured case. On the run that motivated this feature the
        classifier's first action was reading a task document that
        `git status` reports as untracked — a worktree would not have
        contained the spec the run was about."""
        repo, state, st = env
        (repo / "brief.md").write_text("the spec\n")
        st.data["task"] = "do what brief.md says"
        path = _run(leerie, st)
        assert (repo / "brief.md").read_text() == "the spec\n"
        assert (Path(path) / "brief.md").read_text() == "the spec\n"

    def test_tracked_file_is_not_clobbered(self, leerie, env):
        """A tracked file is already in the worktree at the right revision.
        Copying the working-copy version over it would import uncommitted
        drift the run never agreed to plan against."""
        repo, state, st = env
        path = _run(leerie, st)
        wt_file = Path(path) / "tracked.txt"
        (repo / "tracked.txt").write_text("LOCAL DRIFT\n")
        st.data["task"] = "look at tracked.txt"
        _run(leerie, st)
        assert wt_file.read_text() == "tracked\n"

    def test_untracked_dot_claude_is_copied(self, leerie, env):
        """`seed-repo.sh` force-ships `.claude/` to /work precisely because
        repos gitignore it and workers need its hooks/agents/skills."""
        repo, state, st = env
        (repo / ".claude").mkdir()
        (repo / ".claude" / "settings.json").write_text("{}\n")
        path = _run(leerie, st)
        assert (Path(path) / ".claude" / "settings.json").exists()

    def test_absent_dot_claude_is_not_invented(self, leerie, env):
        repo, state, st = env
        path = _run(leerie, st)
        assert not (Path(path) / ".claude").exists()

    def test_paths_outside_the_repo_are_not_staged(self, leerie, env,
                                                   tmp_path):
        """Staging resolves against the repo root; anything else is not ours
        to copy into a tree the planner will reason about."""
        repo, state, st = env
        outsider = tmp_path / "outside.md"
        outsider.write_text("not mine\n")
        st.data["task"] = f"see {outsider}"
        path = _run(leerie, st)
        assert not (Path(path) / "outside.md").exists()
        assert not (Path(path) / outsider.name).exists()

    def test_is_best_effort_and_never_raises(self, leerie, env, monkeypatch):
        """Degraded context must not take down a run that would otherwise
        proceed — the worktree itself is already established by this point."""
        repo, state, st = env

        def boom(*_a, **_k):
            raise OSError("disk on fire")

        monkeypatch.setattr(leerie.shutil, "copy2", boom)
        monkeypatch.setattr(leerie.shutil, "copytree", boom)
        (repo / "brief.md").write_text("spec\n")
        (repo / ".claude").mkdir()
        (repo / ".claude" / "x.json").write_text("{}\n")
        st.data["task"] = "see brief.md"
        assert _run(leerie, st)          # returns a path, does not raise

    def test_no_task_text_is_a_no_op(self, leerie, env):
        """`_ensure_planning_worktree` runs before the fresh-run seed on some
        paths, so `task` may legitimately be absent."""
        repo, state, st = env
        st.data.pop("task", None)
        assert _run(leerie, st)


def test_no_worktree_leaks_into_this_repo(leerie):
    """Standing proof for the whole file: these are the only tests that run a
    real `git worktree add`, and none of them may register one against the
    leerie checkout. Measured once at 2 worktrees / 25 MB, gitignored and
    invisible to `git status`."""
    out = subprocess.run(["git", "worktree", "list", "--porcelain"],
                         capture_output=True, text=True, check=False).stdout
    assert ".leerie/runs" not in out, (
        f"a test registered a worktree inside this repo:\n{out}")
