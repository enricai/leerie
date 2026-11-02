"""Review round 17: a self-ignoring `.gitignore` (DESIGN §8 *Every
measurement reads the committed view*).

A worker wrote an untracked `.gitignore` that ignored itself and a
`helper.py`. I1 read the tree as clean (git's own rules honour that file),
so every measurement ran with `helper.py` present while the commit that
shipped lacked it. Measurements of a worker tree now run in a detached
checkout of its branch tip, and nothing is shared in from the worker's
tree: the dependencies are leerie's install from the committed lockfile
and the build outputs leerie's build from the committed sources, so a
dependency patched in place or a stale build is not measured either."""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path

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


# The recipe: an "install" that materialises the committed lockfile into
# node_modules, and a "build" that derives build/out.txt from the sources.
INSTALL = [sys.executable, "-c",
           "import os, shutil; os.makedirs('node_modules/dep', exist_ok=True);"
           " shutil.copy('lock.txt', 'node_modules/dep/index.js')"]
BUILD = [sys.executable, "-c",
         "import os; os.makedirs('build', exist_ok=True);"
         " open('build/out.txt', 'w').write(open('mod.py').read())"]


def _provisioned(leerie, tmp_path, monkeypatch) -> dict:
    run = _repo(leerie, tmp_path, monkeypatch,
                ignore="node_modules/\nbuild/\n")
    staging = run["staging"]
    (staging / "lock.txt").write_text("dep 1.0\n")
    _git(staging, "add", "lock.txt")
    _git(staging, "commit", "-qm", "lockfile")
    run["st"].data["provision"] = {"recipe": [
        {"kind": "install", "command": INSTALL, "working_dir": "."},
        {"kind": "build", "command": BUILD, "working_dir": "."}]}
    # The worker's own install and build, then its in-place edits.
    for argv in (INSTALL, BUILD):
        subprocess.run(argv, cwd=staging, check=True)
    return run


def test_a_dependency_patched_in_place_is_not_measured(
        leerie, tmp_path, monkeypatch):
    """A worker patches `node_modules/<lib>` to make its tests pass. The
    view's dependencies are leerie's install from the committed lockfile,
    so the patch is not what is measured."""
    run = _provisioned(leerie, tmp_path, monkeypatch)
    staging = run["staging"]
    (staging / "node_modules" / "dep" / "index.js").write_text("patched\n")
    assert asyncio.run(leerie._tree_dirt(staging)) == []
    cmd = (f"{sys.executable} -c \"assert open('node_modules/dep/index.js')"
           f".read() == 'patched\\n'\"")
    assert subprocess.run(cmd, shell=True, cwd=staging).returncode == 0
    got = _measure(leerie, run, {"test": cmd})
    assert got["test"]["passed"] is False, got
    view = leerie._VIEWS[os.path.realpath(staging)][1]
    nm = view / "node_modules"
    assert not nm.is_symlink()
    assert (nm / "dep" / "index.js").read_text() == "dep 1.0\n"


def test_a_stale_build_output_is_not_measured(leerie, tmp_path,
                                              monkeypatch):
    """The worker built before its last commit: its `build/` describes the
    old source. The view's build output is made from the commit."""
    run = _provisioned(leerie, tmp_path, monkeypatch)
    staging = run["staging"]
    (staging / "mod.py").write_text("X = 7\n")
    _git(staging, "commit", "-qam", "seven")       # built before: stale
    assert (staging / "build" / "out.txt").read_text() == "X = 2\n"
    cmd = (f"{sys.executable} -c \"assert open('build/out.txt').read()"
           f" == 'X = 7\\n'\"")
    got = _measure(leerie, run, {"build": cmd})
    assert got["build"]["passed"] is True, got
    view = leerie._VIEWS[os.path.realpath(staging)][1]
    assert not (view / "build").is_symlink()


def test_a_new_tip_gets_a_fresh_install(leerie, tmp_path, monkeypatch):
    """A replaced view is installed again: the install memo is dropped with
    it, so a changed lockfile is what the next measurement reads."""
    run = _provisioned(leerie, tmp_path, monkeypatch)
    staging, st = run["staging"], run["st"]
    tree = asyncio.run(leerie._measured_tree(
        st, str(staging), run["caps"], log_path=run["run_dir"] / "i.log",
        label="t"))
    assert (Path(tree) / "node_modules/dep/index.js").read_text() == \
        "dep 1.0\n"
    (staging / "lock.txt").write_text("dep 2.0\n")
    _git(staging, "commit", "-qam", "bump")
    again = asyncio.run(leerie._measured_tree(
        st, str(staging), run["caps"], log_path=run["run_dir"] / "i.log",
        label="t"))
    assert again == tree
    assert (Path(again) / "node_modules/dep/index.js").read_text() == \
        "dep 2.0\n"
    # Nothing in the worker's tree was installed or rebuilt by leerie.
    assert (staging / "node_modules/dep/index.js").read_text() == \
        "dep 1.0\n"


def test_a_hidden_ignored_directory_is_not_in_the_view(
        leerie, tmp_path, monkeypatch):
    run = _repo(leerie, tmp_path, monkeypatch)
    staging = run["staging"]
    (staging / ".husky" / "_").mkdir(parents=True)
    (staging / ".husky" / "_" / ".gitignore").write_text("*\n")
    (staging / ".husky" / "_" / "h").write_text("#!/bin/sh\n")
    view = asyncio.run(leerie._committed_view(run["st"], str(staging)))
    assert view is not None and view != staging
    assert not os.path.lexists(view / ".husky")
    assert (view / "mod.py").read_text() == "X = 2\n"


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
