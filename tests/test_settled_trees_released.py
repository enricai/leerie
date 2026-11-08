"""A settled subtask's trees go when it settles (DESIGN §6 *A settled
subtask's trees go when it settles*).

A `complete` subtask's worktree, tree repository and committed view used to
stand until its whole wave was integrated: 14 views of 1.5 GB and seven
worker trees of 1.2 GB on the navegando run a70fb4a6. Now each goes as its
subtask settles, and whatever reads it later makes it again from the
branch in the run repository. Real git, through the real scripts and
helpers; nothing is stubbed but what a test names.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
from unittest import mock

import pytest

from tests.test_run_repo import RUN_ID, _git, _setup_run, _St, _subtask, \
    _user_repo
from tests.test_tree_repos import _commit

pytestmark = pytest.mark.real_planning_worktree

SUB = f"leerie/subtasks/{RUN_ID}/s1"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    repo = _user_repo(tmp_path)
    state = tmp_path / "state"
    monkeypatch.chdir(repo)
    monkeypatch.setenv("LEERIE_STATE_DIR", str(state))
    return repo, state, _St(repo, state)


@pytest.fixture(autouse=True)
def _no_views(leerie):
    leerie._VIEWS.clear()
    yield
    leerie._VIEWS.clear()


def _settled(leerie, st) -> tuple[Path, str]:
    """A subtask tree with one commit, taken as a worker's exit takes it."""
    _setup_run(leerie, st)
    wt = _subtask(leerie, st, "s1")
    head = _commit(wt, "w.txt")
    asyncio.run(leerie._take_after_worker(wt))
    return wt, head


def _registered(run_repo: Path) -> str:
    return _git(run_repo, "worktree", "list", "--porcelain")


def test_a_complete_subtasks_trees_go_when_it_settles(leerie, env):
    _repo, _state, st = env
    wt, head = _settled(leerie, st)
    view = asyncio.run(leerie._committed_view(st, wt))
    assert view is not None and view.is_dir()
    leerie._DEPS_INSTALLED.add(os.path.realpath(wt))

    asyncio.run(leerie._release_settled_trees(
        "s1", st.run_dir, {"status": "complete"}))

    assert not wt.exists()
    assert not (st.run_dir / "tree-repos" / wt.name).exists()
    assert not view.exists()
    assert str(view) not in _registered(st.run_dir / "repo")
    # A tree made again at its path installs afresh.
    assert os.path.realpath(wt) not in leerie._DEPS_INSTALLED
    # The work is the branch's, in the run repository.
    assert _git(st.run_dir / "repo", "rev-parse", SUB) == head


def test_a_blocked_subtask_keeps_its_tree_and_loses_its_view(leerie, env):
    _repo, _state, st = env
    wt, head = _settled(leerie, st)
    view = asyncio.run(leerie._committed_view(st, wt))

    asyncio.run(leerie._release_settled_trees(
        "s1", st.run_dir, {"status": "blocked"}))

    assert (wt / "w.txt").is_file()
    assert _git(wt, "rev-parse", "HEAD") == head
    assert not view.exists()


def test_a_released_subtasks_view_is_made_again_from_its_branch(leerie, env):
    _repo, _state, st = env
    wt, head = _settled(leerie, st)
    asyncio.run(leerie._release_settled_trees(
        "s1", st.run_dir, {"status": "complete"}))
    assert not wt.exists()

    assert asyncio.run(leerie._view_ref(st, wt)) == SUB
    view = asyncio.run(leerie._committed_view(st, wt))
    assert view is not None
    assert _git(view, "rev-parse", "HEAD") == head
    assert (view / "w.txt").read_text() == "x\n"


def test_a_re_drive_makes_the_released_tree_again(leerie, env):
    _repo, _state, st = env
    wt, head = _settled(leerie, st)
    asyncio.run(leerie._release_settled_trees(
        "s1", st.run_dir, {"status": "complete"}))

    again = _subtask(leerie, st, "s1")

    assert again == wt
    assert _git(wt, "rev-parse", "HEAD") == head
    assert _git(wt, "symbolic-ref", "--short", "HEAD") == SUB
    assert (st.run_dir / "tree-repos" / wt.name / "HEAD").is_file()


def test_a_coverage_evaluation_reads_the_branch_and_drops_its_view(
        leerie, env, monkeypatch):
    _repo, _state, st = env
    wt, head = _settled(leerie, st)
    asyncio.run(leerie._release_settled_trees(
        "s1", st.run_dir, {"status": "complete"}))
    view = leerie._helper_worktree_path(st, f"view-{wt.name}")
    seen: list[tuple[Path, str]] = []
    real_bin = leerie._vitest_coverage_bin

    def spy(tree):
        seen.append((Path(tree), _git(tree, "rev-parse", "HEAD")
                     if Path(tree).is_dir() else ""))
        return real_bin(tree)

    monkeypatch.setattr(leerie, "_vitest_coverage_bin", spy)

    rec = asyncio.run(leerie._evaluate_subtask_coverage(
        "s1", st, dict(leerie.DEFAULT_CAPS), st.run_dir,
        redrive_changed=set()))

    # Measured in a view made from the branch at the settled commit (this
    # repository has no vitest), not refused for want of a tree.
    assert seen == [(view, head)], seen
    assert rec["measured"] is False and "vitest" in rec["why"], rec
    assert not view.exists()

    missing = asyncio.run(leerie._evaluate_subtask_coverage(
        "s9", st, dict(leerie.DEFAULT_CAPS), st.run_dir,
        redrive_changed=set()))
    assert missing == {"measured": False, "why": "no subtask branch"}


def test_phase_execute_releases_each_subtask_before_the_next_settles(
        leerie, monkeypatch):
    """The wave loop releases a settled subtask's trees as it settles, not
    after the wave: with one slot, the first subtask is evaluated and
    released before the second starts, and both before integration."""
    st = mock.Mock()
    st.data = {"waves": [["a", "b"]], "completed_waves": 0,
               "subtask_status": {}, "integration_gate": {},
               "skip_base_baseline": True, "base_sha": "0" * 40}
    st.save = mock.Mock()
    st.repo_root = "/tmp/repo"
    st.path = "/tmp/state.json"
    st.run_dir = Path("/nonexistent-leerie-run-dir")
    caps = dict(leerie.DEFAULT_CAPS)
    caps["max_parallel"] = 1
    events: list[str] = []

    async def prune(sid, _dir):
        events.append(f"release {sid}")

    async def settle(sid, *_a, **_k):
        events.append(f"settle {sid}")
        return {"status": "complete", "intent": "x", "criteria_results": []}

    async def evaluate(sid, *_a, **_k):
        events.append(f"evaluate {sid}")
        return {"measured": False, "why": "stub"}

    async def integrate(wave, results, *_a, **_k):
        events.append("integrate")
        return [s for s in wave if results[s]["status"] == "complete"]

    monkeypatch.setattr(leerie, "_run_script", mock.AsyncMock(
        return_value=mock.Mock(returncode=0, stderr="")))
    monkeypatch.setattr(leerie, "_prune_leerie_worktrees", mock.Mock())
    monkeypatch.setattr(leerie, "_disk_free_ratio", lambda p: 0.9)
    monkeypatch.setattr(leerie, "_degrade_max_parallel_for_wave",
                        lambda max_parallel, demand: max_parallel)
    monkeypatch.setattr(leerie, "_scan_conflict_markers",
                        mock.AsyncMock(return_value=None))
    monkeypatch.setattr(leerie, "_prune_subtask_worktree", prune)
    monkeypatch.setattr(leerie, "_settle_subtask", settle)
    monkeypatch.setattr(leerie, "integrate_wave", integrate)
    monkeypatch.setattr(leerie, "_evaluate_subtask_coverage", evaluate)
    monkeypatch.setattr(leerie, "_assert_repo_unchanged", mock.AsyncMock())
    monkeypatch.setattr(leerie, "_ensure_run_repo_or_die", mock.AsyncMock())
    monkeypatch.setattr(leerie, "_refresh_run_repo_base_or_die",
                        mock.AsyncMock())
    monkeypatch.setattr(leerie, "_wave_sids_not_integrated",
                        mock.AsyncMock(return_value=[]))

    asyncio.run(leerie.phase_execute(Path("/nonexistent-leerie-run-dir"),
                                     st, caps, {}, {}))

    # Each subtask's coverage is evaluated once, in the view its settle
    # left, before its trees go; the coverage pass does not measure again.
    assert events[:7] == ["settle a", "evaluate a", "release a",
                          "settle b", "evaluate b", "release b",
                          "integrate"], events
