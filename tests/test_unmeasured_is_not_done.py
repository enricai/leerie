"""`done` rests on measurements that ran (DESIGN §6 *The run outcome is
decided once*; review round 19 execution).

In two measured runs every install failed and nothing was measured, and the
run ended `done`: the failing-test-ID diff stepped aside when the test axis
gave no verdict, and the decider read the absent diff as no new failure.
The decider now requires, when the repository has a test command, a diff of
exactly the tree that ships, and a verdict for every held-out set on it.
Driven through the real `_refresh_test_id_diff` and `_decide_outcome`, with
a real run repository, so the tree that ships is read as finalize reads it.
"""
from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest

from tests.conftest import run_git_cwd_first_stdout as _git

RUN_ID = "r19unmeas01"


class _St:
    def __init__(self, user: Path, run_dir: Path):
        self.repo_root = user
        self.run_dir = run_dir
        self.run_id = RUN_ID
        self.data: dict = {"working_branch": "main", "verbosity": "quiet",
                           "subtask_status": {"feat-001": "done"}}
        self.path = run_dir / "state.json"

    def save(self) -> None:
        pass


@pytest.fixture()
def st(leerie, tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "gitconfig"))
    user = tmp_path / "user"
    user.mkdir()
    _git(user, "init", "-q", "-b", "main")
    (user / ".leerie").mkdir()
    (user / ".leerie" / "config.toml").write_text('test = "true"\n')
    (user / "a.py").write_text("A = 1\n")
    _git(user, "add", "-A")
    _git(user, "-c", "user.email=t@x", "-c", "user.name=t", "commit", "-qm",
         "base")
    run_dir = tmp_path / "state" / "runs" / RUN_ID
    run_dir.mkdir(parents=True)
    (run_dir / "logs").mkdir()
    s = _St(user, run_dir)
    repo = leerie._ensure_run_repo(s)
    subprocess.run(["git", "--git-dir", str(repo), "branch",
                    f"leerie/runs/{RUN_ID}", "main"], check=True)
    monkeypatch.setattr(leerie, "_write_run_json", lambda *a, **k: None)
    return s


def _shipping(leerie, st) -> str:
    return leerie._shipping_tree(st)


def test_a_diff_of_the_tree_that_ships_is_done(leerie, st):
    st.data["test_id_diff"] = {"new": [], "tree_sha": _shipping(leerie, st)}
    assert leerie._decide_outcome(st, any_commits=True) == "done"


def test_no_diff_at_all_is_not_done(leerie, st):
    """The case the round measured: nothing measured, no diff recorded."""
    assert leerie._decide_outcome(st, any_commits=True) == "unconverged"
    assert any("not measured on the tree that ships" in r
               for r in st.data["outcome_reasons"])


def test_a_diff_of_another_tree_is_not_done(leerie, st):
    st.data["test_id_diff"] = {"new": [], "tree_sha": "0" * 40}
    assert leerie._decide_outcome(st, any_commits=True) == "unconverged"


def test_a_failed_install_on_the_tree_that_ships_is_not_done(
        leerie, st, monkeypatch):
    """Through the real refresh: the test axis on the tree that ships was
    measured without its dependencies, so the diff is not taken; the
    decider names why."""
    async def axes(tree, axes_, st_, caps, **kw):
        return {"tests": {"ran": True, "measured": False, "passed": None,
                          "command": "true", "deps_failed": True,
                          "summary": "install failed"}}
    monkeypatch.setattr(leerie, "_measure_axes", axes)

    async def no_retry(*a, **k):
        return False
    monkeypatch.setattr(leerie, "_retry_failed_baseline", no_retry)

    async def tree(st_, staging):
        return _shipping(leerie, st)
    monkeypatch.setattr(leerie, "_view_tree_sha", tree)
    asyncio.run(leerie._refresh_test_id_diff(
        st, {}, st.run_dir / "worktrees" / "staging",
        log_path=st.run_dir / "logs" / "t.log", label="t", log_prefix="t"))
    gap = st.data["shipping_tests_gap"]
    assert gap["tree_sha"] == _shipping(leerie, st)
    assert leerie._decide_outcome(st, any_commits=True) == "unconverged"
    assert any("dependency install failed" in r
               for r in st.data["outcome_reasons"])


def test_a_test_command_the_task_forbids_is_its_own_gap(leerie, st):
    st.data["shipping_tests_gap"] = {
        "tree_sha": _shipping(leerie, st), "why": "forbidden",
        "skip_kind": "forbidden_by_task"}
    assert leerie._decide_outcome(st, any_commits=True) == "done"


def test_failing_tests_the_base_could_not_rerun_are_not_done(leerie, st):
    st.data["test_id_diff"] = {"new": [], "no_verdict": ["t::x"],
                               "tree_sha": _shipping(leerie, st)}
    assert leerie._decide_outcome(st, any_commits=True) == "unconverged"
    assert any("could not be rerun on the base" in r
               for r in st.data["outcome_reasons"])


def test_an_unmeasured_held_out_set_is_not_done(leerie, st):
    st.data["test_id_diff"] = {"new": [], "tree_sha": _shipping(leerie, st)}
    st.data["acceptance"] = {
        "sets": [{"index": 0, "valid": True, "defect_files": ["t_a.py"],
                  "control_files": []}],
        "gate": {"final": [{"index": 0, "passed": False, "unmeasured": True,
                            "unmeasured_reason": "deps_failed"}]}}
    assert leerie._acceptance_unmeasured_lines(st.data)
    assert leerie._decide_outcome(st, any_commits=True) == "unconverged"
    assert any("held-out" in r for r in st.data["outcome_reasons"])


def test_a_repository_with_no_test_command_rests_on_no_test_run(
        leerie, st):
    (Path(st.repo_root) / ".leerie" / "config.toml").write_text(
        'test = ""\n')
    assert leerie._decide_outcome(st, any_commits=True) == "done"


def test_a_run_with_no_commits_ships_nothing_to_measure(leerie, st):
    assert leerie._decide_outcome(st, any_commits=False) == "no_work"
