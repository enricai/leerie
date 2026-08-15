"""A subtask settled without its implementer must still be integrable.

`integrate_wave` filters on one thing — `results[sid]["status"] != "complete"`
(`orchestrator/leerie.py`). It does not ask whether a branch exists. So a
subtask settled `complete` by the pre-spawn provider-subset probe, which runs
*before* `_run_implementer` and therefore before `new-worktree.sh` has created
`leerie/subtasks/<run-id>/<sid>`, still reaches `integrate.sh` — which exits 2
on a missing branch, and `integrate_wave` turns rc 2 into a `die()`. Every
probe hit would have killed the run.

The post-execution rescue never had this problem because its implementer ran:
the branch exists carrying zero commits, and `git merge --no-ff` of a
zero-commit branch is a clean no-op. DESIGN §8 called the two paths "the same
HEAD probe" — true of the probe, false of the surrounding state, and that gap
was the bug.

These tests are behavioural against real git and the real `integrate.sh`,
deliberately, because the defect shipped past a file of thirteen
source-substring assertions in `tests/test_provider_subset_pre_spawn_probe.py`.
Source inspection cannot see a missing branch.
"""
from __future__ import annotations

import asyncio
import inspect
import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
INTEGRATE = REPO_ROOT / "scripts" / "integrate.sh"
RUN_ID = "run-abc123"


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                          text=True, check=False)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A checkout with a run branch and its staging worktree, as
    `setup-run.sh` would leave it just before a wave runs."""
    d = tmp_path / "repo"
    d.mkdir()
    _git(d, "init", "-q", "-b", "main")
    _git(d, "config", "user.email", "t@leerie.local")
    _git(d, "config", "user.name", "t")
    (d / "f").write_text("base\n")
    _git(d, "add", "-A")
    _git(d, "commit", "-qm", "base")
    _git(d, "branch", f"leerie/runs/{RUN_ID}")
    staging = d / ".leerie" / "runs" / RUN_ID / "worktrees" / "staging"
    staging.parent.mkdir(parents=True)
    _git(d, "worktree", "add", "-q", str(staging), f"leerie/runs/{RUN_ID}")
    return d


def _integrate(repo: Path, sid: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["LEERIE_STATE_DIR"] = ".leerie"
    return subprocess.run(["bash", str(INTEGRATE), sid, RUN_ID],
                          cwd=str(repo), capture_output=True, text=True,
                          env=env, check=False)


class TestTheProductionShape:
    def test_a_branchless_subtask_dies_at_rc_2(self, repo):
        """The defect itself, reproduced. This is what a pre-spawn settle
        looked like before the fix, and it is the anti-vacuity control for the
        test below: without it, that test could pass because `integrate.sh`
        tolerates anything."""
        r = _integrate(repo, "test-001")
        assert r.returncode == 2
        assert "does not exist" in r.stderr

    def test_an_empty_branch_at_the_run_tip_integrates_cleanly(self, repo):
        """The shape the fix produces — and the shape the post-execution
        rescue already had."""
        _git(repo, "branch", f"leerie/subtasks/{RUN_ID}/test-001",
             f"leerie/runs/{RUN_ID}")
        r = _integrate(repo, "test-001")
        assert r.returncode == 0, r.stderr
        assert "integrated: test-001" in r.stdout

    def test_integrating_it_changes_no_files(self, repo):
        """A zero-commit branch must contribute nothing — the settle claims the
        deliverable is already on the run branch."""
        before = _git(repo, "rev-parse", f"leerie/runs/{RUN_ID}^{{tree}}").stdout
        _git(repo, "branch", f"leerie/subtasks/{RUN_ID}/test-001",
             f"leerie/runs/{RUN_ID}")
        assert _integrate(repo, "test-001").returncode == 0
        after = _git(repo, "rev-parse", f"leerie/runs/{RUN_ID}^{{tree}}").stdout
        assert before == after


class TestTheHelper:
    def test_it_creates_the_branch_at_the_run_tip(self, leerie, repo):
        assert asyncio.run(leerie._create_empty_subtask_branch(
            str(repo), RUN_ID, "test-001")) is True
        tip = _git(repo, "rev-parse",
                   f"leerie/subtasks/{RUN_ID}/test-001").stdout.strip()
        run_tip = _git(repo, "rev-parse", f"leerie/runs/{RUN_ID}").stdout.strip()
        assert tip and tip == run_tip

    def test_the_branch_it_makes_is_integrable(self, leerie, repo):
        """Composes the two halves: the helper's output must be exactly what
        `integrate.sh` accepts. Asserting the branch exists is not the same as
        asserting the run survives."""
        assert asyncio.run(leerie._create_empty_subtask_branch(
            str(repo), RUN_ID, "test-001")) is True
        assert _integrate(repo, "test-001").returncode == 0

    def test_an_existing_branch_is_never_repointed(self, leerie, repo):
        """Idempotent for resume. By the time `_settle_subtask` is re-entered
        the branch may carry commits, and moving it would discard them."""
        branch = f"leerie/subtasks/{RUN_ID}/test-001"
        _git(repo, "branch", branch, f"leerie/runs/{RUN_ID}")
        wt = repo / "wt"
        _git(repo, "worktree", "add", "-q", str(wt), branch)
        (wt / "new.txt").write_text("real work\n")
        _git(wt, "add", "-A")
        _git(wt, "commit", "-qm", "implementer work")
        before = _git(repo, "rev-parse", branch).stdout.strip()

        assert asyncio.run(leerie._create_empty_subtask_branch(
            str(repo), RUN_ID, "test-001")) is True
        assert _git(repo, "rev-parse", branch).stdout.strip() == before

    def test_it_reports_failure_rather_than_raising(self, leerie, tmp_path):
        """A non-repo, or a run branch that does not exist. The caller falls
        through to the implementer on False, so this must be a return value,
        not an exception."""
        d = tmp_path / "not-a-repo"
        d.mkdir()
        assert asyncio.run(leerie._create_empty_subtask_branch(
            str(d), RUN_ID, "test-001")) is False


def _pre_spawn_block(leerie) -> str:
    """The pre-spawn block, sliced BY STRUCTURE — flag read to the implementer
    loop. A fixed character window is the trap this repo has now hit three
    times; the sibling file already resolves it this way."""
    src = inspect.getsource(leerie._settle_subtask)
    i = src.index('st.data.get("provider_subset_sids")')
    return src[i:src.index("\n    while True:", i)]



class TestTheCallerWiring:
    def test_the_settle_is_gated_on_the_branch(self, leerie):
        """Source-coupling, because the alternative — settling anyway — is the
        original defect. The behavioural half is the composition test above."""
        window = _pre_spawn_block(leerie)
        assert "_create_empty_subtask_branch(" in window
        create = window.index("_create_empty_subtask_branch(")
        settle = window.index("_settle_already_satisfied(")
        assert create < settle, (
            "the branch must exist before the subtask is settled complete")

    def test_a_failed_branch_creation_falls_through_to_the_implementer(
            self, leerie):
        window = _pre_spawn_block(leerie)
        assert "if await _create_empty_subtask_branch(" in window, (
            "the settle must be conditional on branch creation succeeding — "
            "a settle integration cannot merge is worse than the spend")
