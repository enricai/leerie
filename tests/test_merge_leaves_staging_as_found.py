"""Leerie's own merge leaves staging as it found it, apart from its product
(DESIGN §6½; review round 15 M3, whose proofs these are, turned to assert
the designed outcome). Driven on the real setup-run.sh, new-worktree.sh
and integrate.sh, through `_integrate_merge`.

1. A merged `.gitignore` change un-ignores output a build left in staging
   (kept by I2 as environment): it is neither committed nor ignored under
   the merged commit, so the merge's bracket removes it, and staging's
   install memo is dropped so the next measurement regenerates it.
2. A repository `post-merge` hook (husky's, enabled by leerie's own install)
   does not run on leerie's merge: hooks are off for leerie's git
   commands."""
from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path

from tests.conftest import worker_exit
from tests.conftest import REPO_ROOT

RUN_ID = "20261030-gitignore"


def _git(cwd, *a, check=True):
    return subprocess.run(["git", *a], cwd=cwd, check=check,
                          capture_output=True, text=True).stdout.strip()


def _sh(repo, name, *args):
    return subprocess.run(["bash", str(REPO_ROOT / "scripts" / name), *args],
                          cwd=repo, capture_output=True, text=True,
                          check=True)


def _world(leerie, tmp_path, monkeypatch, hooks_path=None):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    if hooks_path is not None:
        # The user's own setting, carried into the run's repository and so
        # part of leerie's copy of its settings (DESIGN §8 *The settings
        # are leerie's*): set on the run repository afterwards, I2's
        # bracket around the merge would put it back.
        _git(repo, "config", "core.hooksPath", str(hooks_path))
    (repo / "f.txt").write_text("base\n")
    (repo / ".gitignore").write_text(".leerie/\nnode_modules/\ngen/\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    root = repo / ".leerie"
    monkeypatch.setenv("LEERIE_STATE_DIR", str(root))
    monkeypatch.chdir(repo)
    st = leerie.State(root, RUN_ID)
    st.repo_root = repo
    st.data["working_branch"] = "main"
    # The scripts run in the run's own repository (DESIGN §6 *The run's own
    # repository*); `repo` below is that repository.
    run_repo = leerie._ensure_run_repo(st)
    _sh(run_repo, "setup-run.sh", RUN_ID, "main")
    staging = root / "runs" / RUN_ID / "worktrees" / "staging"
    wt = Path(_sh(run_repo, "new-worktree.sh", "feat-001",
                  RUN_ID).stdout.strip().splitlines()[-1])
    return run_repo, staging, wt, st


def test_output_a_merged_gitignore_exposes_is_removed(leerie, tmp_path,
                                                      monkeypatch):
    repo, staging, wt, st = _world(leerie, tmp_path, monkeypatch)
    (staging / "gen").mkdir()
    (staging / "gen" / "client.ts").write_text("// generated\n")
    (staging / "node_modules").mkdir()
    (staging / "node_modules" / "dep.js").write_text("dep\n")
    leerie._DEPS_INSTALLED.add(os.path.realpath(staging))
    (wt / ".gitignore").write_text(".leerie/\nnode_modules/\n")
    (wt / "gen").mkdir()
    (wt / "gen" / "index.ts").write_text("export {}\n")
    _git(wt, "add", "-A")
    _git(wt, "commit", "-qm", "track gen/")
    worker_exit(wt)   # a worker's exit takes its tree's branch
    try:
        proc = asyncio.run(leerie._integrate_merge(staging, st, "feat-001"))
        assert proc.returncode == 0
        assert not (staging / "gen" / "client.ts").exists()
        assert (staging / "gen" / "index.ts").is_file()       # the product
        assert (staging / "node_modules" / "dep.js").is_file()  # ignored
        assert os.path.realpath(staging) not in leerie._DEPS_INSTALLED
        assert asyncio.run(leerie._tree_dirt(staging)) == []
    finally:
        leerie._DEPS_INSTALLED.discard(os.path.realpath(staging))


def test_a_post_merge_hook_does_not_run_on_leeries_merge(leerie, tmp_path,
                                                         monkeypatch):
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    ran = tmp_path / "hook.ran"          # outside the tree: no bracket
    (hooks / "post-merge").write_text(   # can hide that the hook ran
        f"#!/bin/sh\necho synced > .last-sync\necho x >> {ran}\n")
    (hooks / "post-merge").chmod(0o755)
    repo, staging, wt, st = _world(leerie, tmp_path, monkeypatch,
                                   hooks_path=hooks)
    assert _git(repo, "config", "core.hooksPath") == str(hooks)
    (wt / "f.txt").write_text("feature\n")
    _git(wt, "commit", "-qam", "feature")
    worker_exit(wt)   # a worker's exit takes its tree's branch
    proc = asyncio.run(leerie._integrate_merge(staging, st, "feat-001"))
    assert proc.returncode == 0
    assert not ran.exists()
    assert not (staging / ".last-sync").exists()
    assert _git(staging, "status", "--porcelain",
                "--untracked-files=all") == ""
    # The control: the hook is real and fires on a merge that is not
    # leerie's.
    (wt / "g.txt").write_text("g\n")
    _git(wt, "add", "g.txt")
    _git(wt, "commit", "-qm", "g")
    worker_exit(wt)   # a worker's exit takes its tree's branch
    # By commit: staging's own repository holds only the run branch (DESIGN
    # §6 *A repository per committing tree (I6)*).
    subprocess.run(["git", "merge", "-q", "--no-ff", "-m", "m",
                    _git(wt, "rev-parse", "HEAD")],
                   cwd=staging, check=True, capture_output=True,
                   env=leerie._git_hooks_on(os.environ))
    assert (staging / ".last-sync").is_file() and ran.exists()
