"""Review round 17: a self-ignoring `.gitignore` (DESIGN §8 *Every
measurement reads the committed view*).

A worker wrote an untracked `.gitignore` that ignored itself and a
`helper.py`. I1 read the tree as clean (git's own rules honour that file),
so every measurement ran with `helper.py` present while the commit that
shipped lacked it. Measurements of a worker tree now run in a detached
checkout of its branch tip, with only what that checkout's own ignore rules
(committed `.gitignore` files, leerie's settings) also ignore shared in from
the tree: the installed environment, not a worker's hidden file."""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))
from test_shipping_tree_is_committed import _git, _repo  # noqa: E402

HIDE = ".gitignore\nhelper.py\n"
NEEDS_HELPER = f"{sys.executable} -c 'import helper'"


@pytest.fixture(autouse=True)
def _no_views(leerie):
    leerie._VIEWS.clear()
    yield
    leerie._VIEWS.clear()


def _branch_tree(run) -> str:
    return _git(run["staging"], "rev-parse",
                f"refs/heads/leerie/runs/{run['st'].run_id}^{{tree}}")


def _measure(leerie, run, axes):
    return asyncio.run(leerie._measure_axes(
        str(run["staging"]), axes, run["st"], run["caps"],
        log_path=run["run_dir"] / "logs" / "m.log", verbosity="quiet",
        memo=False))


def test_a_file_hidden_by_an_uncommitted_gitignore_is_not_measured(
        leerie, tmp_path, monkeypatch):
    run = _repo(leerie, tmp_path, monkeypatch)
    staging = run["staging"]
    # The hostile worker: the hidden file and the rule hiding it, itself
    # hidden. git's own rules honour it, so I1 sees nothing to hold. (The
    # root `.gitignore` is tracked here, so the rule is a nested one, as
    # husky's `.husky/_/.gitignore` is.)
    (staging / "hide").mkdir()
    (staging / "hide" / "keep.txt").write_text("x\n")
    _git(staging, "add", "hide/keep.txt")
    _git(staging, "commit", "-qm", "a committed dir")
    (staging / "hide" / ".gitignore").write_text(HIDE)
    (staging / "hide" / "helper.py").write_text("X = 1\n")
    assert asyncio.run(leerie._tree_dirt(staging)) == []
    cmd = f"cd hide && {NEEDS_HELPER}"
    # In the tree itself the command passes: the measurement this replaces.
    assert subprocess.run(cmd, shell=True, cwd=staging).returncode == 0
    got = _measure(leerie, run, {"build": cmd})
    assert got["build"]["passed"] is False, got
    # The shipping tree is the branch's, and the hidden file is not in it.
    assert asyncio.run(leerie._view_tree_sha(run["st"], str(staging))) == \
        _branch_tree(run)
    assert "hide/helper.py" not in _git(
        staging, "ls-tree", "-r", "--name-only", "HEAD")


def test_a_failing_id_capture_reads_the_committed_view(
        leerie, tmp_path, monkeypatch):
    """Every test-ID run goes through `_capture_failing_test_ids`: a failing
    test only staging's worktree has is not reported."""
    from test_redrive_test_id_diff_refresh import CMD
    run = _repo(leerie, tmp_path, monkeypatch)
    staging = run["staging"]
    (staging / "tests" / "test_stray.py").write_text(
        "def test_stray():\n    assert False\n")
    got = asyncio.run(leerie._capture_failing_test_ids(
        str(staging), CMD, run["st"], run["caps"], label="x",
        log_path=run["run_dir"] / "logs" / "c.log", timeout=120))
    assert got is not None and got["ids"] == []
    assert (staging / "tests" / "test_stray.py").is_file()


def test_the_environment_is_shared_and_a_hidden_file_is_not(
        leerie, tmp_path, monkeypatch):
    run = _repo(leerie, tmp_path, monkeypatch,
                ignore="node_modules/\nbuild/\n")
    staging = run["staging"]
    (staging / "node_modules" / "dep").mkdir(parents=True)
    (staging / "node_modules" / "dep" / "index.js").write_text("x\n")
    (staging / "build").mkdir()
    (staging / "build" / "out.o").write_text("o")
    (staging / ".husky" / "_").mkdir(parents=True)
    (staging / ".husky" / "_" / ".gitignore").write_text("*\n")
    (staging / ".husky" / "_" / "h").write_text("#!/bin/sh\n")
    view = asyncio.run(leerie._committed_view(run["st"], str(staging)))
    assert view is not None and view != staging
    assert (view / "node_modules").is_symlink()
    assert (view / "node_modules" / "dep" / "index.js").is_file()
    assert (view / "build").is_symlink()        # a `dir/` rule matches
    assert not os.path.lexists(view / ".husky")
    assert (view / "mod.py").read_text() == "X = 2\n"
    # An install after the view was made is seen on the next call.
    (staging / "node_modules" / ".bin").mkdir()
    (staging / "build2").mkdir()                # ignored by nothing
    view2 = asyncio.run(leerie._committed_view(run["st"], str(staging)))
    assert view2 == view
    assert (view / "node_modules" / ".bin").is_dir()
    assert not os.path.lexists(view / "build2")


def test_the_view_follows_the_branch_tip_not_staging_head(
        leerie, tmp_path, monkeypatch):
    run = _repo(leerie, tmp_path, monkeypatch)
    staging, st = run["staging"], run["st"]
    tree = _branch_tree(run)
    _git(staging, "checkout", "-q", "--detach", "HEAD~1")
    view = asyncio.run(leerie._committed_view(st, str(staging)))
    assert (view / "mod.py").read_text() == "X = 2\n"
    assert asyncio.run(leerie._view_tree_sha(st, str(staging))) == tree
    assert asyncio.run(leerie._committed_tree_sha(str(staging))) != tree


def test_the_view_is_replaced_when_the_tip_moves(leerie, tmp_path,
                                                 monkeypatch):
    run = _repo(leerie, tmp_path, monkeypatch)
    staging, st = run["staging"], run["st"]
    view = asyncio.run(leerie._committed_view(st, str(staging)))
    (staging / "mod.py").write_text("X = 3\n")
    assert (view / "mod.py").read_text() == "X = 2\n"   # uncommitted: no
    _git(staging, "commit", "-qam", "three")
    again = asyncio.run(leerie._committed_view(st, str(staging)))
    assert (again / "mod.py").read_text() == "X = 3\n"
    assert leerie._VIEWS[os.path.realpath(staging)][0] == _git(
        staging, "rev-parse", "HEAD")


def test_a_tree_that_is_no_worker_tree_is_measured_as_it_is(
        leerie, tmp_path, monkeypatch):
    run = _repo(leerie, tmp_path, monkeypatch)
    assert asyncio.run(leerie._committed_view(
        run["st"], str(run["repo"]))) is None


def test_an_unreadable_branch_stops_the_run(leerie, tmp_path, monkeypatch,
                                            capsys):
    run = _repo(leerie, tmp_path, monkeypatch)
    staging, st = run["staging"], run["st"]
    _git(staging, "checkout", "-q", "--detach")
    _git(staging, "update-ref", "-d", f"refs/heads/leerie/runs/{st.run_id}")
    with pytest.raises(SystemExit):
        asyncio.run(leerie._committed_view(st, str(staging)))
    assert "committed view" in capsys.readouterr().err
