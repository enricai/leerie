"""Leerie reads a ref-less clone through a git directory it owns (DESIGN §6
*Leerie's git never takes a tree's repository from the tree*).

The acceptance writer's, the surrogate fixer's and the falsifier's checkouts
are ref-less clones of the run repository. A clone keeps its repository in
its own `.git`, inside the tree its confined worker writes, so that worker
can rewrite the clone's config, hooks and attributes. Leerie's git on the
clone runs unconfined, so it must not read them. `_ref_less_clone` makes
`<run>/clone-gits/<name>` — the run repository's objects, an index of the
clone's commit, written by leerie alone — and every leerie read of the
clone, in Python and in the shell, goes through it."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from tests.conftest import REPO_ROOT
from tests.conftest import run_git_cwd_first_stdout as _git

LIB = REPO_ROOT / "scripts" / "worktree-lib.sh"
RUN_ID = "r19rl00001"


class _St:
    def __init__(self, user: Path, run_dir: Path):
        self.repo_root = user
        self.run_dir = run_dir
        self.run_id = run_dir.name
        self.data: dict = {"working_branch": "main"}

    def save(self) -> None:
        pass


@pytest.fixture()
def clone(leerie, tmp_path, monkeypatch):
    """A run repository and a ref-less clone of its `main` in the run's
    `worktrees/`, as `_acceptance_writer_checkout` makes one; then what its
    confined worker may do: edit a tracked file, add one, and give the
    clone's own `.git` a config whose programs record that they ran."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "gitconfig"))
    user = tmp_path / "user"
    user.mkdir()
    _git(user, "init", "-q", "-b", "main")
    (user / "a.txt").write_text("base\n")
    _git(user, "add", "-A")
    _git(user, "-c", "user.email=t@x", "-c", "user.name=t", "commit", "-qm",
         "base")
    run_dir = tmp_path / "state" / "runs" / RUN_ID
    run_dir.mkdir(parents=True)
    repo = leerie._ensure_run_repo(_St(user, run_dir))
    head = _git(repo, "rev-parse", "main")
    wt = run_dir / "worktrees" / f"{RUN_ID[:12]}-acceptance-w1"
    assert leerie._ref_less_clone(repo, wt, head) == ""
    # The worker's result, which leerie must still see.
    (wt / "a.txt").write_text("edited by the worker\n")
    (wt / "new_test.py").write_text("def test_x(): pass\n")
    (wt / ".gitattributes").write_text("* filter=x diff=x\n")
    # What its confined worker could leave in the clone's own repository.
    marker = tmp_path / "ran"
    prog = tmp_path / "prog.sh"
    prog.write_text(f"#!/bin/sh\necho ran >> '{marker}'\ncat\n")
    prog.chmod(0o755)
    hooks = wt / ".git" / "planted-hooks"
    hooks.mkdir()
    for h in ("post-index-change", "post-checkout", "reference-transaction"):
        (hooks / h).write_text(f"#!/bin/sh\necho ran >> '{marker}'\n")
        (hooks / h).chmod(0o755)
    for k, v in (("core.fsmonitor", str(prog)),
                 ("filter.x.clean", str(prog)),
                 ("filter.x.smudge", str(prog)),
                 ("filter.x.process", str(prog)),
                 ("diff.x.textconv", str(prog)),
                 ("core.hooksPath", str(hooks))):
        subprocess.run(["git", "--git-dir", str(wt / ".git"), "config", k, v],
                       check=True)
    (wt / ".git" / "info").mkdir(exist_ok=True)
    (wt / ".git" / "info" / "attributes").write_text("* filter=x diff=x\n")
    return {"wt": wt, "run_dir": run_dir, "repo": repo, "marker": marker,
            "head": head}


def test_the_clone_is_located_at_leeries_git_dir(leerie, clone):
    loc = leerie._run_tree_location(clone["wt"])
    shadow = leerie._clone_git_dir(clone["wt"])
    assert loc and Path(loc[1]) == shadow
    assert shadow.is_relative_to(clone["run_dir"] / "clone-gits")
    assert not Path(loc[1]).is_relative_to(clone["wt"])


def test_leeries_reads_of_the_clone_run_nothing_it_planted(leerie, clone):
    wt = clone["wt"]
    status = subprocess.run(
        ["git", *leerie._tree_git_args(wt), "status", "--porcelain",
         "--untracked-files=no"], capture_output=True, text=True, check=True)
    diff = subprocess.run(
        ["git", *leerie._tree_git_args(wt), "diff", "HEAD"],
        capture_output=True, text=True, check=True)
    made = leerie._untracked_listing(wt)
    assert "a.txt" in status.stdout                 # the edit, seen
    assert "edited by the worker" in diff.stdout
    assert "new_test.py" in (made or [])
    assert not clone["marker"].exists(), clone["marker"].read_text()


def test_the_shells_reads_of_the_clone_run_nothing_it_planted(clone):
    r = subprocess.run(
        ["bash", "-c", '. "$1"; leerie_tgit "$2" status --porcelain', "_",
         str(LIB), str(clone["wt"])], capture_output=True, text=True,
        env={**os.environ})
    assert r.returncode == 0, r.stderr
    assert "a.txt" in r.stdout
    assert not clone["marker"].exists()


def test_a_clone_without_leeries_git_dir_is_refused(leerie, clone):
    """No fallback to the clone's own `.git`: git fails, nothing runs."""
    import shutil
    shutil.rmtree(leerie._clone_git_dir(clone["wt"]))
    assert leerie._run_tree_location(clone["wt"]) is False
    r = subprocess.run(["git", *leerie._tree_git_args(clone["wt"]),
                        "status", "--porcelain"], capture_output=True,
                       text=True)
    assert r.returncode != 0
    assert not clone["marker"].exists()


def test_removing_the_checkout_removes_leeries_git_dir(leerie, clone):
    st = _St(clone["run_dir"], clone["run_dir"])
    shadow = leerie._clone_git_dir(clone["wt"])
    assert shadow.is_dir()
    leerie._remove_acceptance_checkout(st, clone["wt"])
    assert not shadow.exists() and not clone["wt"].exists()
