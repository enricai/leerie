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
from types import ModuleType

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


def _run(script: Path, repo: Path, state: Path,
         *args: str) -> subprocess.CompletedProcess:
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
    def __init__(self, data: dict) -> None:
        self.data = data
        self.run_id = "r1"

    def save(self) -> None:
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

        async def fake(name: str, *args: str) -> subprocess.CompletedProcess:
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
        """The incident end to end through `_filter_satisfied_subtasks`:
        the operator is on another branch when the probe runs, then `main`
        (the run's branch) advances while HEAD stays put. A live-HEAD key
        files the first verdict under the other branch's sha and, HEAD
        never having moved, trusts it on the second sweep — one probe. The
        base-tree key files it under `main`'s tip, sees that tip move, and
        re-probes — two."""
        repo = _make_repo(tmp_path)
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
        }
        calls: list[str] = []

        async def fake_claude_p(*, user_prompt: str, sid: str,
                                **_kw: object) -> dict:
            calls.append(sid)
            return {"satisfied": False, "evidence": "not yet"}

        monkeypatch.setattr(leerie, "claude_p", fake_claude_p)

        def sweep() -> None:
            plans = [{"domain": "d", "status": "ready",
                      "subtasks": [{"id": "feat-001", "title": "feat-001",
                                    "success_criteria_seed": "done"}]}]
            assert asyncio.run(leerie._filter_satisfied_subtasks(
                plans, repo, st,
                {"max_parallel": 4, "max_total_workers": 999},
                {"satisfied_probe": "sonnet"},
                {"satisfied_probe": None})) is None

        sweep()
        head_before = _sha(repo, "HEAD")
        # main advances (a sibling PR merges) while HEAD stays on `other`.
        _git("checkout", "-q", "main", cwd=repo)
        (repo / "file.txt").write_text("advanced\n")
        _git("commit", "-q", "-am", "advance", cwd=repo)
        _git("checkout", "-q", "other", cwd=repo)
        assert _sha(repo, "HEAD") == head_before
        sweep()
        assert calls == ["satisfied_probe-feat-001"] * 2
        assert st.data["satisfied_probe_cache"]["feat-001"]["base_sha"] == \
            _sha(repo, "main")


# --- branch shadowed by a same-named tag ------------------------------------

def _repo_with_tag_shadowed_branch(tmp_path: Path) -> tuple[Path, str]:
    """Branch `feat` with a tag `feat` on the same commit: `--abbrev-ref`
    records the branch as `heads/feat`. Then the operator switches to
    `other`. Returns the repo and `feat`'s sha."""
    repo = _make_repo(tmp_path)
    _git("checkout", "-q", "-b", "feat", cwd=repo)
    (repo / "file.txt").write_text("feat\n")
    _git("commit", "-q", "-am", "feat", cwd=repo)
    _git("tag", "feat", cwd=repo)
    recorded = _git("rev-parse", "--abbrev-ref", "HEAD", cwd=repo).stdout
    assert recorded.strip() == "heads/feat"
    feat_sha = _sha(repo, "refs/heads/feat")
    _switch_to_other_branch(repo)
    return repo, feat_sha


class TestTagShadowedBranch:
    def test_setup_run_follows_it(self, tmp_path):
        repo, feat_sha = _repo_with_tag_shadowed_branch(tmp_path)
        r = _run(SETUP_RUN, repo, tmp_path / "state", "heads/feat")
        assert r.returncode == 0, r.stderr
        assert "not found" not in r.stderr
        assert _sha(repo, "leerie/runs/r1") == feat_sha
        wb = tmp_path / "state" / "runs" / "r1" / "working-branch"
        # Recorded verbatim: `heads/feat` is the unambiguous revision the
        # diff base needs (`feat` alone would resolve to the tag).
        assert wb.read_text().strip() == "heads/feat"

    def test_planning_worktree_follows_it(self, tmp_path):
        repo, feat_sha = _repo_with_tag_shadowed_branch(tmp_path)
        state = tmp_path / "state"
        r = _run(PLANNING_WT, repo, state, "heads/feat")
        assert r.returncode == 0, r.stderr
        assert _planning_head(state) == feat_sha

    def test_base_tree_sha_follows_it(self, leerie, tmp_path):
        repo, feat_sha = _repo_with_tag_shadowed_branch(tmp_path)
        st = _St({"working_branch": "heads/feat"})
        assert asyncio.run(leerie._base_tree_sha(str(repo), st)) == feat_sha


# --- fallback warning is surfaced -------------------------------------------

class TestFallbackWarningSurfaced:
    @pytest.mark.real_planning_worktree
    def test_ensure_planning_worktree_logs_it(
            self, leerie, monkeypatch, tmp_path):
        async def fake(name: str, *args: str) -> subprocess.CompletedProcess:
            return subprocess.CompletedProcess(
                [], 0, stdout=f"planning-worktree: {tmp_path}\n",
                stderr="warning: working branch 'gone' not found; "
                       "using HEAD\n")

        logs: list[str] = []
        monkeypatch.setattr(leerie, "_run_script", fake)
        monkeypatch.setattr(leerie, "log", logs.append)
        st = _St({"working_branch": "gone"})
        st.run_dir = tmp_path
        st.repo_root = tmp_path
        asyncio.run(leerie._ensure_planning_worktree(st))
        assert any("working branch 'gone' not found" in m
                   and m.startswith("WARNING:") for m in logs)

    def test_ordinary_git_stderr_is_not_echoed(self, leerie, monkeypatch):
        """git prints progress such as `Preparing worktree` on stderr; only
        the scripts' own fallback line is surfaced."""
        logs: list[str] = []
        monkeypatch.setattr(leerie, "log", logs.append)
        leerie._log_working_branch_fallback(
            subprocess.CompletedProcess(
                [], 0, stdout="", stderr="Preparing worktree (detached)\n"),
            "phase 4")
        assert logs == []


# --- task-reference staging ---------------------------------------------------

def _repo_on_switched_branch(tmp_path: Path) -> Path:
    """`main` is the run's branch. Branch `other` commits `feature.py` and
    a `.claude/settings.json` that `main` lacks, and is checked out.
    `report.md` is the operator's untracked task document; `notes.md` is
    staged (`git add`) but never committed."""
    repo = _make_repo(tmp_path)
    _git("checkout", "-q", "-b", "other", cwd=repo)
    (repo / "feature.py").write_text("DELIVERED = True\n")
    (repo / ".claude").mkdir()
    (repo / ".claude" / "settings.json").write_text("{}\n")
    _git("add", "feature.py", ".claude/settings.json", cwd=repo)
    _git("commit", "-q", "-m", "other-branch content", cwd=repo)
    (repo / "report.md").write_text("bug report\n")
    (repo / "notes.md").write_text("spec notes\n")
    _git("add", "notes.md", cwd=repo)
    return repo


class _TaskSt:
    def __init__(self, repo: Path, task: str, wb: str = "main") -> None:
        self.repo_root = repo
        self.data = {"task": task, "working_branch": wb}


def _stage(leerie: ModuleType, tmp_path: Path, repo: Path,
           task: str) -> Path:
    state = tmp_path / "state"
    r = _run(PLANNING_WT, repo, state, "main")
    assert r.returncode == 0, r.stderr
    wt = state / "runs" / "r1" / "worktrees" / "planning"
    leerie._stage_worktree_extras(_TaskSt(repo, task), str(wt))
    return wt


class TestStagingSkipsSwitchedBranchContent:
    TASK = "fix per report.md and notes.md, then add feature.py"

    def test_switched_branch_deliverable_is_not_staged(
            self, leerie, tmp_path, monkeypatch):
        """It would show the satisfied-probe a deliverable the run's tree
        does not have — and the skip is announced, since the same rule
        would also withhold a spec the operator committed on a side
        branch."""
        repo = _repo_on_switched_branch(tmp_path)
        logs: list[str] = []
        monkeypatch.setattr(leerie, "log", logs.append)
        wt = _stage(leerie, tmp_path, repo, self.TASK)
        assert not (wt / "feature.py").exists()
        assert any(m.startswith("WARNING: planning worktree: not staging "
                                "feature.py") for m in logs)

    def test_task_documents_are_still_staged(self, leerie, tmp_path):
        """The untracked report and the staged-but-uncommitted notes are
        both the spec; losing either is the failure staging exists for."""
        repo = _repo_on_switched_branch(tmp_path)
        wt = _stage(leerie, tmp_path, repo, self.TASK)
        assert (wt / "report.md").read_text() == "bug report\n"
        assert (wt / "notes.md").read_text() == "spec notes\n"

    def test_switched_branch_claude_file_is_not_staged(self, leerie, tmp_path):
        repo = _repo_on_switched_branch(tmp_path)
        wt = _stage(leerie, tmp_path, repo, self.TASK)
        assert not (wt / ".claude" / "settings.json").exists()

    def test_directories_are_gated_file_by_file(self, leerie, tmp_path):
        """One switched-branch file among a task's globbed spec files, or
        inside `.claude/`, must not cost the untracked files beside it —
        the spec loss staging exists to prevent."""
        repo = _repo_on_switched_branch(tmp_path)
        (repo / "specs").mkdir()
        (repo / "specs" / "b.md").write_text("other-branch\n")
        _git("add", "specs/b.md", cwd=repo)
        _git("commit", "-q", "-m", "specs b", cwd=repo)
        (repo / "specs" / "a.md").write_text("untracked spec\n")
        (repo / ".claude" / "settings.local.json").write_text("{}\n")
        # Task references reach staging as files (a glob expands to each);
        # `.claude/` is the directory copy.
        wt = _stage(leerie, tmp_path, repo, "follow specs/*.md please")
        assert (wt / "specs" / "a.md").read_text() == "untracked spec\n"
        assert not (wt / "specs" / "b.md").exists()
        assert (wt / ".claude" / "settings.local.json").exists()
        assert not (wt / ".claude" / "settings.json").exists()

    def test_untracked_claude_dir_is_still_staged(self, leerie, tmp_path):
        repo = _make_repo(tmp_path)
        (repo / ".claude").mkdir()
        (repo / ".claude" / "settings.json").write_text("{}\n")
        wt = _stage(leerie, tmp_path, repo, "fix it")
        assert (wt / ".claude" / "settings.json").exists()

    def test_working_branch_commit_missing_from_worktree_is_staged(
            self, leerie, tmp_path):
        """A file committed on the run's own branch after the worktree was
        reset is the run's content, not a switched branch's."""
        repo = _make_repo(tmp_path)
        state = tmp_path / "state"
        assert _run(PLANNING_WT, repo, state, "main").returncode == 0
        (repo / "report.md").write_text("committed later\n")
        _git("add", "report.md", cwd=repo)
        _git("commit", "-q", "-m", "report", cwd=repo)
        wt = state / "runs" / "r1" / "worktrees" / "planning"
        leerie._stage_worktree_extras(_TaskSt(repo, "see report.md"), str(wt))
        assert (wt / "report.md").read_text() == "committed later\n"


class TestSwitchedBranchOnlyPaths:
    def test_sets(self, leerie, tmp_path):
        repo = _repo_on_switched_branch(tmp_path)
        f = leerie._switched_branch_only_paths
        assert f(repo, "feature.py", "refs/heads/main") == {"feature.py"}
        assert f(repo, ".claude", "refs/heads/main") == {
            ".claude/settings.json"}
        assert f(repo, "report.md", "refs/heads/main") == set()  # untracked
        assert f(repo, "notes.md", "refs/heads/main") == set()   # staged only
        assert f(repo, "file.txt", "refs/heads/main") == set()   # on both
        assert f(repo, "feature.py", None) == set()   # no branch to follow

    def test_glob_characters_match_literally(self, leerie, tmp_path):
        """`notes/run[1].md` must not match a committed `notes/run1.md`
        (a pathspec glob would)."""
        repo = _repo_on_switched_branch(tmp_path)
        (repo / "notes").mkdir()
        (repo / "notes" / "run1.md").write_text("x\n")
        _git("add", "notes/run1.md", cwd=repo)
        _git("commit", "-q", "-m", "run1", cwd=repo)
        (repo / "notes" / "run[1].md").write_text("y\n")
        f = leerie._switched_branch_only_paths
        assert f(repo, "notes/run1.md", "refs/heads/main") == {
            "notes/run1.md"}
        assert f(repo, "notes/run[1].md", "refs/heads/main") == set()

    def test_ls_tree_failure_answers_empty(
            self, leerie, tmp_path, monkeypatch):
        """Fails only the `ls-tree` calls, so the handler inside the helper
        is what is exercised; every other git call runs for real."""
        repo = _repo_on_switched_branch(tmp_path)
        real_run = subprocess.run

        def flaky(argv: list[str], *a: object,
                  **k: object) -> subprocess.CompletedProcess:
            if "ls-tree" in argv:
                raise OSError("no git")
            return real_run(argv, *a, **k)

        monkeypatch.setattr(leerie.subprocess, "run", flaky)
        assert leerie._switched_branch_only_paths(
            repo, "feature.py", "refs/heads/main") == set()


class TestUndecodableLsTree:
    def test_gives_empty_set_and_staging_continues(
            self, leerie, tmp_path, monkeypatch):
        """An undecodable `ls-tree` once raised out of the helper into
        staging's outer catch, abandoning every later task reference."""
        repo = _repo_on_switched_branch(tmp_path)
        real_run = subprocess.run

        def fake(argv: list[str], *a: object,
                 **k: object) -> subprocess.CompletedProcess:
            if "ls-tree" in argv:
                return real_run(["printf", "caf\\351\\0"],
                                capture_output=True,
                                text=k.get("text", False))
            return real_run(argv, *a, **k)

        monkeypatch.setattr(leerie.subprocess, "run", fake)
        assert leerie._switched_branch_only_paths(
            repo, "feature.py", "refs/heads/main") == set()
        wt = _stage(leerie, tmp_path, repo, "fix per report.md")
        assert (wt / "report.md").read_text() == "bug report\n"


class TestRefsHeadsForm:
    """`--abbrev-ref` prints `refs/heads/<name>` when even `heads/<name>`
    is ambiguous; all three lookups accept it."""

    def test_python_resolver(self, leerie, tmp_path):
        repo = _make_repo(tmp_path)
        st = _TaskSt(repo, "", "refs/heads/main")
        assert leerie._working_branch_ref(repo, st) == "refs/heads/main"

    @pytest.mark.parametrize("script", [SETUP_RUN, PLANNING_WT])
    def test_scripts(self, tmp_path, script):
        repo = _make_repo(tmp_path)
        main_sha = _sha(repo, "main")
        _switch_to_other_branch(repo)
        state = tmp_path / "state"
        r = _run(script, repo, state, "refs/heads/main")
        assert r.returncode == 0, r.stderr
        assert "not found" not in r.stderr
        got = (_sha(repo, "leerie/runs/r1") if script == SETUP_RUN
               else _planning_head(state))
        assert got == main_sha
