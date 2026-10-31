"""A repository per committing tree (DESIGN §6 *A repository per committing
tree (I6)*).

Each committing tree — staging, each subtask tree — is a worktree of its
own bare repository `<run-dir>/tree-repos/<tree>`, which borrows the run
repository's objects and holds one ref. A worker writes only that
repository; leerie alone moves the run repository's refs, by taking a
tree's branch back. Real git, through the real scripts and helpers.
"""
from __future__ import annotations

import asyncio
import os
import stat
import subprocess
from pathlib import Path

import pytest

from tests.test_run_repo import RUN_ID, _git, _setup_run, _St, _subtask, \
    _user_repo

pytestmark = pytest.mark.real_planning_worktree

RUN_BRANCH = f"leerie/runs/{RUN_ID}"
SUB = f"leerie/subtasks/{RUN_ID}/s1"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    repo = _user_repo(tmp_path)
    state = tmp_path / "state"
    monkeypatch.chdir(repo)
    monkeypatch.setenv("LEERIE_STATE_DIR", str(state))
    return repo, state, _St(repo, state)


def _refs(gitdir: Path) -> list[str]:
    return _git(gitdir, "for-each-ref", "--format=%(refname)").split()


def _commit(wt: Path, name: str, text: str = "x\n") -> str:
    (wt / name).write_text(text)
    _git(wt, "add", "-A")
    _git(wt, "-c", "user.name=w", "-c", "user.email=w@w", "commit", "-qm",
         f"add {name}")
    return _git(wt, "rev-parse", "HEAD")


def _chmod_tree(path: Path, writable: bool) -> None:
    for d, dirs, files in os.walk(path):
        for n in [d, *(os.path.join(d, f) for f in files)]:
            m = os.stat(n).st_mode
            os.chmod(n, (m | stat.S_IWUSR) if writable
                     else (m & ~stat.S_IWUSR & ~stat.S_IWGRP & ~stat.S_IWOTH))


def test_each_committing_tree_has_its_own_repository(leerie, env):
    """Staging and a subtask tree are worktrees of their own bare
    repositories: the run repository's objects through the alternates, its
    settings copied, one ref, nothing copied."""
    _repo, _state, st = env
    staging = _setup_run(leerie, st)
    wt = _subtask(leerie, st, "s1")
    run_repo = st.run_dir / "repo"
    for tree, name, branch in ((staging, "staging", RUN_BRANCH),
                               (wt, wt.name, SUB)):
        tr = st.run_dir / "tree-repos" / name
        assert Path(_git(tree, "rev-parse", "--path-format=absolute",
                         "--git-common-dir")).resolve() == tr.resolve()
        assert (tr / "objects" / "info" / "alternates").read_text().strip() \
            == str((run_repo / "objects").resolve())
        assert _refs(tr) == [f"refs/heads/{branch}"]
        assert (tr / "config").read_bytes() == \
            (st.run_dir / "repo-settings" / "config").read_bytes()
        assert _git(tr, "count-objects").startswith("0 objects")
    run_list = _git(run_repo, "worktree", "list", "--porcelain")
    assert f"worktree {staging.resolve()}" not in run_list
    assert f"worktree {wt.resolve()}" not in run_list
    # The run repository never prunes while the run lives.
    for key, value in (("gc.auto", "0"), ("gc.pruneExpire", "never"),
                       ("maintenance.auto", "false")):
        assert _git(run_repo, "config", key) == value


def test_a_confined_worker_commits_but_cannot_move_another_branch(
        leerie, env):
    """The grant I5 gives a committing worker: its tree and its tree
    repository, the run repository read-only. A commit works; moving the
    run branch or a sibling's in the run repository is refused, and
    moving its own repository's copy changes nothing leerie reads."""
    _repo, _state, st = env
    _setup_run(leerie, st)
    wt = _subtask(leerie, st, "s1")
    _subtask(leerie, st, "s2")
    run_repo = st.run_dir / "repo"
    before = {r: _git(run_repo, "rev-parse", r) for r in _refs(run_repo)}
    _chmod_tree(run_repo, writable=False)
    try:
        head = _commit(wt, "w.txt")
        sibling = f"refs/heads/leerie/subtasks/{RUN_ID}/s2"
        for ref in (f"refs/heads/{RUN_BRANCH}", sibling):
            r = subprocess.run(["git", "--git-dir", str(run_repo),
                                "update-ref", ref, head],
                               capture_output=True, text=True)
            assert r.returncode != 0
        _git(wt, "update-ref", f"refs/heads/{RUN_BRANCH}", head)
    finally:
        _chmod_tree(run_repo, writable=True)
    assert {r: _git(run_repo, "rev-parse", r) for r in _refs(run_repo)} \
        == before


def test_leerie_takes_exactly_the_trees_own_branch(leerie, env):
    """A take moves the tree's branch and nothing else: refs a worker made
    in its own repository, a run-branch copy it moved, and the branch its
    HEAD is on never reach the run repository."""
    _repo, _state, st = env
    _setup_run(leerie, st)
    wt = _subtask(leerie, st, "s1")
    run_repo = st.run_dir / "repo"
    run_tip = _git(run_repo, "rev-parse", RUN_BRANCH)
    head = _commit(wt, "w.txt")
    _git(wt, "update-ref", "refs/heads/evil", head)
    _git(wt, "update-ref", f"refs/heads/{RUN_BRANCH}", head)
    _git(wt, "checkout", "-qb", "elsewhere")
    _commit(wt, "other.txt")
    assert leerie._take_tree_branch(wt) == head
    assert _git(run_repo, "rev-parse", SUB) == head
    assert _git(run_repo, "rev-parse", RUN_BRANCH) == run_tip
    assert "refs/heads/evil" not in _refs(run_repo)
    assert "refs/heads/elsewhere" not in _refs(run_repo)


def test_every_worker_exit_takes_its_trees_branch(leerie, env, monkeypatch):
    """The take is in the one spawn path: a worker that committed and then
    failed still has its commit in the run repository."""
    _repo, _state, st = env
    _setup_run(leerie, st)
    wt = _subtask(leerie, st, "s1")
    made: dict = {}

    async def fake_invoke(cmd, cwd, *a, **kw):
        made["sha"] = _commit(Path(cwd), "w.txt")
        raise leerie.WorkerError("the worker died after committing")
    monkeypatch.setattr(leerie, "_invoke", fake_invoke)
    with pytest.raises(leerie.WorkerError):
        asyncio.run(leerie._invoke_admitted(["claude"], str(wt), 30, "s1",
                                            st.run_dir, "quiet",
                                            grants=None))
    assert _git(st.run_dir / "repo", "rev-parse", SUB) == made["sha"]


def test_integration_merges_from_the_run_repository_and_takes_staging(
        leerie, env):
    _repo, _state, st = env
    staging = _setup_run(leerie, st)
    wt = _subtask(leerie, st, "s1")
    head = _commit(wt, "s1.txt")
    leerie._take_tree_branch(wt)
    pre = _git(staging, "rev-parse", "HEAD")
    proc = asyncio.run(leerie._integrate_merge(staging, st, "s1"))
    assert proc.returncode == 0, proc.stderr
    run_repo = st.run_dir / "repo"
    merged = _git(run_repo, "rev-parse", RUN_BRANCH)
    assert merged == _git(staging, "rev-parse", "HEAD") != pre
    assert _git(run_repo, "merge-base", "--is-ancestor", head, merged,
                check=False) == ""
    assert asyncio.run(leerie._ancestor_codes(
        staging, [f"refs/heads/{SUB}"], "HEAD")) == [0]
    # A rollback through the one restore reaches the run repository.
    assert asyncio.run(leerie._restore_tree(
        staging, pre, where="test", branch=RUN_BRANCH))
    assert _git(run_repo, "rev-parse", RUN_BRANCH) == pre


def test_a_resume_takes_what_a_killed_worker_committed(leerie, env):
    """A SIGKILL skips the take; the resume's setup takes every surviving
    tree's branch before it resets the tree."""
    _repo, _state, st = env
    _setup_run(leerie, st)
    wt = _subtask(leerie, st, "s1")
    head = _commit(wt, "w.txt")
    (wt / "half-done.txt").write_text("uncommitted\n")
    assert _git(st.run_dir / "repo", "rev-parse", SUB) != head
    _setup_run(leerie, st)
    assert _git(st.run_dir / "repo", "rev-parse", SUB) == head
    assert not (wt / "half-done.txt").exists()


def test_a_judge_checks_out_a_synthetic_merge_from_the_run_repository(
        leerie, env):
    _repo, _state, st = env
    staging = _setup_run(leerie, st)
    head = _git(staging, "rev-parse", "HEAD")
    p1 = head
    wt = _subtask(leerie, st, "s1")
    p2 = _commit(wt, "s1.txt")
    leerie._take_tree_branch(wt)
    sha = asyncio.run(leerie._synthetic_merge(staging, head, p1, p2, head))
    assert sha
    assert _git(st.run_dir / "repo", "cat-file", "-t", sha) == "commit"

    async def judge():
        async with leerie._judge_checkout(st, staging, "integration-judge-s1",
                                          rev=sha) as c:
            return _git(c, "rev-parse", "HEAD"), Path(_git(
                c, "rev-parse", "--path-format=absolute",
                "--git-common-dir")).resolve()
    got, common = asyncio.run(judge())
    assert got == sha and common == (st.run_dir / "repo").resolve()


def test_a_tree_made_in_the_run_repository_is_made_again(leerie, env):
    """A subtask tree registered in the run repository itself (a run made
    before tree repositories) is remade from a tree repository, its branch
    and commits kept."""
    _repo, _state, st = env
    _setup_run(leerie, st)
    run_repo = st.run_dir / "repo"
    _git(run_repo, "branch", SUB, RUN_BRANCH)
    old = st.run_dir / "worktrees" / leerie._worktree_dirname(RUN_ID, "s1")
    _git(run_repo, "worktree", "add", "-q", str(old), SUB)
    head = _commit(old, "w.txt")
    wt = _subtask(leerie, st, "s1")
    assert wt.resolve() == old.resolve()
    assert Path(_git(wt, "rev-parse", "--path-format=absolute",
                     "--git-common-dir")).name == wt.name
    assert _git(wt, "rev-parse", "HEAD") == head
    assert f"worktree {old}" not in _git(run_repo, "worktree", "list",
                                         "--porcelain")


def test_settings_of_a_tree_repository_are_checked_and_restored(leerie, env):
    """I1's settings check reads a tree repository against the run's copy:
    a worker's change to its own repository's config is seen and put
    back."""
    _repo, _state, st = env
    _setup_run(leerie, st)
    wt = _subtask(leerie, st, "s1")
    assert leerie._settings_dirt(wt) == []
    _git(wt, "config", "core.fsmonitor", "true")
    assert leerie._settings_dirt(wt)
    assert leerie._restore_settings(wt) == ["config"]
    assert leerie._settings_dirt(wt) == []


def test_pruning_a_tree_takes_it_and_removes_its_repository(leerie, env):
    _repo, _state, st = env
    _setup_run(leerie, st)
    wt = _subtask(leerie, st, "s1")
    head = _commit(wt, "w.txt")
    asyncio.run(leerie._prune_subtask_worktree("s1", st.run_dir))
    assert not wt.exists()
    assert not (st.run_dir / "tree-repos" / wt.name).exists()
    assert _git(st.run_dir / "repo", "rev-parse", SUB) == head


def test_a_judge_reads_a_detached_head_staging_was_left_on(leerie, env):
    """A take moves staging's branch only; a commit a worker left on a
    detached HEAD is in staging's own repository alone. The judge's checkout
    of staging's HEAD, a checkout of the run repository, brings it first."""
    _repo, _state, st = env
    staging = _setup_run(leerie, st)
    _git(staging, "checkout", "-q", "--detach")
    head = _commit(staging, "detached.txt")
    leerie._take_tree_branch(staging)
    run_repo = st.run_dir / "repo"
    assert subprocess.run(["git", "--git-dir", str(run_repo), "cat-file",
                           "-e", head], capture_output=True).returncode != 0

    async def judge():
        async with leerie._judge_checkout(st, staging, "delivery-judge") as c:
            return _git(c, "rev-parse", "HEAD"), (c / "detached.txt").is_file()
    assert asyncio.run(judge()) == (head, True)
