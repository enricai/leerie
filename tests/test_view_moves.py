"""A view moves to the next tip when the install's inputs are unchanged
(DESIGN §8 *A view moves to the next tip when the install's inputs are
unchanged*; review round 18 L1).

Real git, the real scripts, staging in its own tree repository, the real
`_measured_tree` running a recipe whose install is incremental, as a package
manager's is: it writes `node_modules/dep/index.js` from `package.json` only
when that differs, and counts its runs.
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

import pytest

from tests.test_run_repo import _git, _setup_run, _St, _user_repo

pytestmark = pytest.mark.real_planning_worktree

# Its run count is kept under its own output, `node_modules/.count`: the
# install is confined to the view and its measurement directory (DESIGN §6
# *Leerie's runs of the repository's commands are confined too*), and a
# moved view gets a fresh measurement directory, keeping only the install's
# output.
INSTALL = (
    "import os, pathlib, sys\n"
    "c = pathlib.Path('node_modules/.count')\n"
    "c.parent.mkdir(exist_ok=True)\n"
    "c.write_text(str(int(c.read_text()) + 1 if c.exists() else 1))\n"
    "want = open('package.json').read()\n"
    "p = pathlib.Path('node_modules/dep/index.js')\n"
    "if not p.exists() or p.read_text() != want:\n"
    "    p.parent.mkdir(parents=True, exist_ok=True)\n"
    "    p.write_text(want)\n"
    "if os.environ.get('FAIL_INSTALL'):\n"
    "    sys.exit(1)\n")
# The build writes `dist/out.txt` from the source, and, at the first tip
# only, a generated file under the install's own output.
BUILD = (
    "import os, pathlib\n"
    "src = open('src/a.txt').read()\n"
    "os.makedirs('dist', exist_ok=True)\n"
    "open('dist/out.txt', 'w').write(src)\n"
    "if src == '2':\n"
    "    os.makedirs('node_modules/.gen', exist_ok=True)\n"
    "    open('node_modules/.gen/out', 'w').write(src)\n")


def _count(view: Path) -> int:
    """How often the install ran in `view` over its own output."""
    return int((view / "node_modules" / ".count").read_text())


@pytest.fixture()
def env(leerie, tmp_path, monkeypatch):
    repo = _user_repo(tmp_path)
    (repo / "src").mkdir()
    (repo / "src" / "a.txt").write_text("1")
    (repo / "package.json").write_text('{"name": "x"}\n')
    (repo / ".gitignore").write_text("node_modules/\ndist/\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "app")
    state = tmp_path / "state"
    monkeypatch.chdir(repo)
    monkeypatch.setenv("LEERIE_STATE_DIR", str(state))
    monkeypatch.setattr(leerie, "_cgroup_create", lambda *a, **k: None)
    monkeypatch.delenv("FAIL_INSTALL", raising=False)
    st = _St(repo, state)
    st.data["provision"] = {"recipe": [
        {"kind": "install", "command": [sys.executable, "-c", INSTALL],
         "working_dir": "."},
        {"kind": "build", "command": [sys.executable, "-c", BUILD],
         "working_dir": "."}]}
    staging = _setup_run(leerie, st)
    caps = dict(leerie.DEFAULT_CAPS)
    caps["worker_timeout_sec"] = 60
    yield st, staging, caps, _count
    for d in (leerie._VIEWS, leerie._VIEW_INSTALLS, leerie._DEPS_KEPT,
              leerie._INSTALL_OUTPUTS):
        d.clear()


def _commit(leerie, staging: Path, files: dict[str, str]) -> str:
    for rel, text in files.items():
        (staging / rel).parent.mkdir(parents=True, exist_ok=True)
        (staging / rel).write_text(text)
    _git(staging, "add", "-A")
    _git(staging, "-c", "user.name=w", "-c", "user.email=w@w", "commit",
         "-qm", "work")
    leerie._take_tree_branch(staging)
    return _git(staging, "rev-parse", "HEAD")


def _measure(leerie, st, staging, caps) -> Path:
    return Path(asyncio.run(leerie._measured_tree(
        st, staging, caps, log_path=st.run_dir / "view.log", label="t")))


@pytest.fixture()
def hold():
    """`hold(path)` opens the file and keeps it open for the test. A file
    held open cannot have its inode freed, so no later file can be given the
    same number: an inode compared after a delete can be reused and prove
    nothing, but `_is_held(fd, path)` is true only if `path` is that very
    file. (A second hard link would hold it as well, but linking changes the
    file's ctime, which the install snapshot rightly reads as a change.)"""
    fds: list[int] = []

    def _hold(p: Path) -> int:
        fds.append(os.open(p, os.O_RDONLY))
        return fds[-1]
    yield _hold
    for fd in fds:
        os.close(fd)


def _is_held(fd: int, p: Path) -> bool:
    return os.path.samestat(os.fstat(fd), os.stat(p))


def test_a_new_tip_with_the_same_inputs_keeps_the_install(
        leerie, env, hold):
    """The view moves: the install's own output stays (the same file), the
    install runs again over it, the builds run again, and nothing else of
    the old tip — a measurement's report, a tool's cache under
    `node_modules`, a build's output, its measurement directory — is left;
    its stores (the install's result) are kept."""
    st, staging, caps, count = env
    a = _commit(leerie, staging, {"src/a.txt": "2"})
    v1 = _measure(leerie, st, staging, caps)
    assert _git(v1, "rev-parse", "HEAD") == a
    dep = v1 / "node_modules" / "dep" / "index.js"
    held = hold(dep)
    assert (v1 / "node_modules" / ".gen" / "out").read_text() == "2"
    # What measurements left at tip a.
    (v1 / "report.txt").write_text("old")
    (v1 / "dist" / "stale.txt").write_text("old")
    (v1 / "node_modules" / ".cache").mkdir()
    (v1 / "node_modules" / ".cache" / "x").write_text("old")
    poison = v1.parent.parent / "worktrees" / ".stores" / "tree-stores" / v1.name / "uv" / "old"
    poison.parent.mkdir(parents=True, exist_ok=True)
    poison.write_text("old")
    measured = leerie._measure_dir(st, v1)
    (measured / "reports" / "old").write_text("old")
    b = _commit(leerie, staging, {"src/a.txt": "3"})
    v2 = _measure(leerie, st, staging, caps)
    assert v2 == v1
    assert _git(v2, "rev-parse", "HEAD") == b
    assert _is_held(held, dep)           # the install kept
    assert count(v2) == 2                          # and run again over it
    assert (v2 / "dist" / "out.txt").read_text() == "3"
    for gone in ("report.txt", "dist/stale.txt", "node_modules/.cache",
                 "node_modules/.gen"):             # a build's is not kept
        assert not os.path.lexists(v2 / gone), gone
    assert poison.read_text() == "old"   # its stores stay: inputs unchanged
    assert leerie._measure_dir(st, v2) != measured  # and its measurement dir
    assert not (leerie._measure_dir(st, v2) / "reports" / "old").exists()
    assert _git(v2, "status", "--porcelain") == ""


def test_a_changed_install_input_makes_a_fresh_view(leerie, env, hold):
    st, staging, caps, _count = env
    _commit(leerie, staging, {"src/a.txt": "2"})
    v1 = _measure(leerie, st, staging, caps)
    held = hold(v1 / "node_modules" / "dep" / "index.js")
    c = _commit(leerie, staging, {"package.json": '{"name": "y"}\n'})
    v2 = _measure(leerie, st, staging, caps)
    assert _git(v2, "rev-parse", "HEAD") == c
    assert not _is_held(held, v2 / "node_modules/dep/index.js")
    assert (v2 / "node_modules/dep/index.js").read_text() == \
        '{"name": "y"}\n'


def test_an_install_a_measurement_changed_is_not_kept(leerie, env, hold):
    """An in-place edit of a file the install made, even one that keeps its
    size and puts its mtime back, sends the view back to a fresh one."""
    st, staging, caps, _count = env
    _commit(leerie, staging, {"src/a.txt": "2"})
    v1 = _measure(leerie, st, staging, caps)
    dep = v1 / "node_modules" / "dep" / "index.js"
    before = os.stat(dep)
    text = dep.read_text()
    with open(dep, "r+") as f:
        f.write("X" + text[1:])
    os.utime(dep, ns=(before.st_atime_ns, before.st_mtime_ns))
    held = hold(dep)
    _commit(leerie, staging, {"src/a.txt": "3"})
    v2 = _measure(leerie, st, staging, caps)
    assert not _is_held(held, v2 / "node_modules/dep/index.js")
    assert (v2 / "node_modules/dep/index.js").read_text() == text


# A store's file linked into the install's output, as pnpm links its
# content-addressable store into `node_modules` (and uv and bun their
# caches): one inode, two links.
STORE_LINKED = (
    "import os, pathlib\n"
    "s = pathlib.Path('node_modules/.store/dep.js')\n"
    "s.parent.mkdir(parents=True, exist_ok=True)\n"
    "s.exists() or s.write_text('linked')\n"
    "t = pathlib.Path('node_modules/linked/dep.js')\n"
    "t.parent.mkdir(parents=True, exist_ok=True)\n"
    "t.exists() or os.link(s, t)\n")


def _with_linked_install(st) -> None:
    st.data["provision"]["recipe"].insert(1, {
        "kind": "install", "command": [sys.executable, "-c", STORE_LINKED],
        "working_dir": "."})


def test_a_sibling_linking_the_same_store_file_keeps_the_install(
        leerie, env, hold, tmp_path):
    """Another tree's install linking the same store file (or its removal
    unlinking it) moves that file's change time: no change to this view,
    which still moves with its install kept."""
    st, staging, caps, _count = env
    _with_linked_install(st)
    _commit(leerie, staging, {"src/a.txt": "2"})
    v1 = _measure(leerie, st, staging, caps)
    linked = v1 / "node_modules" / "linked" / "dep.js"
    assert os.stat(linked).st_nlink == 2
    held = hold(linked)
    # A sibling's install, then its removal.
    os.link(linked, tmp_path / "sibling.js")
    os.unlink(tmp_path / "sibling.js")
    _commit(leerie, staging, {"src/a.txt": "3"})
    v2 = _measure(leerie, st, staging, caps)
    assert _is_held(held, v2 / "node_modules" / "linked" / "dep.js")
    assert _count(v2) == 2                    # moved, the install kept


def test_a_write_to_a_linked_store_file_is_not_kept(leerie, env, hold):
    """A file linked from a store is still told changed by a write that
    keeps its size: its modification time moves."""
    st, staging, caps, _count = env
    _with_linked_install(st)
    _commit(leerie, staging, {"src/a.txt": "2"})
    v1 = _measure(leerie, st, staging, caps)
    linked = v1 / "node_modules" / "linked" / "dep.js"
    held = hold(linked)
    time.sleep(0.01)
    with open(linked, "r+") as f:
        f.write("L")                          # same size, new time
    _commit(leerie, staging, {"src/a.txt": "3"})
    v2 = _measure(leerie, st, staging, caps)
    assert not _is_held(held, v2 / "node_modules" / "linked" / "dep.js")
    assert _count(v2) == 1                    # installed afresh


def test_a_file_made_with_one_link_that_gains_another_is_not_kept(
        leerie, env, hold, tmp_path):
    """A file the install made with one link is still known by its change
    time: a link added to it later is a change."""
    st, staging, caps, _count = env
    _commit(leerie, staging, {"src/a.txt": "2"})
    v1 = _measure(leerie, st, staging, caps)
    dep = v1 / "node_modules" / "dep" / "index.js"
    assert os.stat(dep).st_nlink == 1
    held = hold(dep)
    os.link(dep, tmp_path / "kept.js")
    _commit(leerie, staging, {"src/a.txt": "3"})
    v2 = _measure(leerie, st, staging, caps)
    assert not _is_held(held, v2 / "node_modules" / "dep" / "index.js")


def test_a_rewritten_dot_git_is_pointed_back(leerie, env, tmp_path):
    """A measurement that points the view's `.git` at another repository
    moves nothing there: the view is pointed back at its own."""
    st, staging, caps, _count = env
    _commit(leerie, staging, {"src/a.txt": "2"})
    v1 = _measure(leerie, st, staging, caps)
    other = tmp_path / "other"
    other.mkdir()
    _git(other, "init", "-q")
    (v1 / ".git").unlink()
    (v1 / ".git").write_text(f"gitdir: {other / '.git'}\n")
    b = _commit(leerie, staging, {"src/a.txt": "3"})
    v2 = _measure(leerie, st, staging, caps)
    assert _git(v2, "rev-parse", "HEAD") == b
    assert (v2 / "src" / "a.txt").read_text() == "3"
    assert _git(other, "rev-parse", "--verify", "-q", "HEAD",
                check=False) == ""
    admin = leerie._admin_dir_of(st.run_dir / "repo", v2)
    assert admin is not None
    assert (v2 / ".git").read_text() == f"gitdir: {admin}\n"


def test_a_move_that_cannot_read_the_commit_is_made_afresh(
        leerie, env, monkeypatch):
    """A move that cannot read the commit's tree is no move, and leaves a
    view git can still remove: the next measurement makes it afresh."""
    st, staging, caps, _count = env
    _commit(leerie, staging, {"src/a.txt": "2"})
    v1 = _measure(leerie, st, staging, caps)
    assert _count(v1) == 1
    monkeypatch.setattr(leerie, "_tree_entries", lambda *a, **k: None)
    b = _commit(leerie, staging, {"src/a.txt": "3"})
    v2 = _measure(leerie, st, staging, caps)
    assert _git(v2, "rev-parse", "HEAD") == b
    assert (v2 / "src" / "a.txt").read_text() == "3"
    assert _count(v2) == 1                    # installed afresh, not moved


def test_a_view_whose_submodule_path_holds_anything_is_made_afresh(
        leerie, env):
    """A submodule's path (a gitlink) is entered by neither the move's
    checkout nor its clean, so a view where anything is there is not moved
    but made afresh: the next commit's measurement never sees it. An empty
    one, as every checkout makes it, still moves."""
    st, staging, caps, _count = env
    sha = _git(staging, "rev-parse", "HEAD")
    _git(staging, "update-index", "--add", "--cacheinfo",
         f"160000,{sha},src/sub")
    _git(staging, "-c", "user.name=w", "-c", "user.email=w@w", "commit",
         "-qm", "a submodule")
    leerie._take_tree_branch(staging)
    v1 = _measure(leerie, st, staging, caps)
    assert _count(v1) == 1
    assert (v1 / "src" / "sub").is_dir()
    # Empty, as every checkout makes it: the view still moves.
    (staging / "src" / "c.txt").write_text("1")
    _git(staging, "add", "src/c.txt")
    _git(staging, "-c", "user.name=w", "-c", "user.email=w@w", "commit",
         "-qm", "more")
    leerie._take_tree_branch(staging)
    v1 = _measure(leerie, st, staging, caps)
    assert _count(v1) == 2                    # moved, the install kept
    (v1 / "src" / "sub" / "left.txt").write_text("x")
    (staging / "src" / "b.txt").write_text("1")
    _git(staging, "add", "src/b.txt")
    _git(staging, "-c", "user.name=w", "-c", "user.email=w@w", "commit",
         "-qm", "work")
    leerie._take_tree_branch(staging)
    v2 = _measure(leerie, st, staging, caps)
    assert (v2 / "src" / "b.txt").read_text() == "1"
    assert not os.path.lexists(v2 / "src" / "sub" / "left.txt")
    assert _count(v2) == 1                    # installed afresh, not moved


def test_a_failed_install_is_not_recorded(leerie, env, monkeypatch):
    st, staging, caps, _count = env
    monkeypatch.setenv("FAIL_INSTALL", "1")
    _commit(leerie, staging, {"src/a.txt": "2"})
    v1 = _measure(leerie, st, staging, caps)
    assert os.path.realpath(v1) not in leerie._VIEW_INSTALLS


# An install as uv's is: what it installs is a hard link of its own cache's
# copy, and its cache is one of the tree's own stores (`UV_CACHE_DIR`),
# which a moved view starts empty.
LINKED = (
    "import hashlib, os, pathlib\n"
    "want = open('package.json').read()\n"
    "cache = pathlib.Path(os.environ['UV_CACHE_DIR'],\n"
    "                     hashlib.sha256(want.encode()).hexdigest())\n"
    "if not cache.exists():\n"
    "    cache.parent.mkdir(parents=True, exist_ok=True)\n"
    "    cache.write_text(want)\n"
    "p = pathlib.Path('node_modules/dep/index.js')\n"
    "if not p.exists() or p.read_text() != want:\n"
    "    p.parent.mkdir(parents=True, exist_ok=True)\n"
    "    if p.exists():\n"
    "        p.unlink()\n"
    "    os.link(cache, p)\n")


def _stores_gone(leerie, st) -> None:
    """Wait for the removal leerie runs off the event loop of what a moved
    view's earlier stores held (`<run>/measure/.gone`)."""
    gone = st.run_dir / leerie.MEASURE_DIRNAME / ".gone"
    for _ in range(300):
        if not gone.exists():
            return
        time.sleep(0.1)
    raise AssertionError(f"{gone} still there")


def test_a_view_moved_once_moves_again_when_its_install_links_a_store(
        leerie, env, hold, monkeypatch):
    """The install's output is a link of a file in the tree's own stores.
    A move starts those stores empty and removes the old ones; that removal
    drops a link of the output, which moves its change time. The install's
    snapshot is taken after it, so the next tip with the same inputs moves
    the view again, and keeps the install."""
    st, staging, caps, _count = env
    # The removal outlasts the install, as a real store's does (click's uv
    # cache: the install, a no-op, ends first).
    real_rm = leerie._rmtree_owned

    def _slow_rm(path, *a, **k):
        if Path(path).name == ".gone":
            time.sleep(2)
        return real_rm(path, *a, **k)
    monkeypatch.setattr(leerie, "_rmtree_owned", _slow_rm)
    st.data["provision"]["recipe"] = [
        {"kind": "install", "command": [sys.executable, "-c", LINKED],
         "working_dir": "."}]
    _commit(leerie, staging, {"src/a.txt": "2"})
    v1 = _measure(leerie, st, staging, caps)
    dep = v1 / "node_modules" / "dep" / "index.js"
    assert os.stat(dep).st_nlink == 2          # linked from its store
    held = hold(dep)
    _commit(leerie, staging, {"src/a.txt": "3"})
    v2 = _measure(leerie, st, staging, caps)
    assert _is_held(held, v2 / "node_modules/dep/index.js")   # moved once
    _stores_gone(leerie, st)
    c = _commit(leerie, staging, {"src/a.txt": "4"})
    v3 = _measure(leerie, st, staging, caps)
    assert _git(v3, "rev-parse", "HEAD") == c
    assert _is_held(held, v3 / "node_modules/dep/index.js")   # and again
    # A changed input still makes a fresh view.
    _stores_gone(leerie, st)
    _commit(leerie, staging, {"package.json": '{"name": "z"}\n'})
    v4 = _measure(leerie, st, staging, caps)
    assert not _is_held(held, v4 / "node_modules/dep/index.js")
    assert (v4 / "node_modules/dep/index.js").read_text() == \
        '{"name": "z"}\n'


# An install as some package managers' is: it adds what its manifest names
# and never removes what the manifest no longer names. Its manifest's name
# is on no list.
INCREMENTAL = (
    "import pathlib\n"
    "for name in open('deps.manifest').read().split():\n"
    "    p = pathlib.Path('deps', name)\n"
    "    p.parent.mkdir(exist_ok=True)\n"
    "    p.write_text(name)\n")


def test_an_input_named_on_no_list_makes_a_fresh_view(leerie, env, hold):
    """The install read `deps.manifest`, so a tip that changes it gets a
    fresh view: a dependency the manifest dropped is not left there, as a
    moved view's incremental install would leave it."""
    st, staging, caps, _count = env
    st.data["provision"]["recipe"] = [
        {"kind": "install", "command": [sys.executable, "-c", INCREMENTAL],
         "working_dir": "."}]
    _commit(leerie, staging, {".gitignore": "deps/\n",
                              "deps.manifest": "kept\ndropped\n"})
    v1 = _measure(leerie, st, staging, caps)
    assert (v1 / "deps" / "dropped").is_file()
    held = hold(v1 / "deps" / "kept")
    c = _commit(leerie, staging, {"deps.manifest": "kept\n"})
    v2 = _measure(leerie, st, staging, caps)
    assert _git(v2, "rev-parse", "HEAD") == c
    assert not os.path.lexists(v2 / "deps" / "dropped")
    assert (v2 / "deps" / "kept").read_text() == "kept"
    assert not _is_held(held, v2 / "deps" / "kept")
    files, _opened = leerie._VIEW_INSTALLS[os.path.realpath(v2)][2]
    assert "deps.manifest" in files


class _LimitedLibc:
    """The real libc, but the kernel refuses `refused` (`inotify_init1`: no
    inotify instance left for this user, EMFILE; `inotify_add_watch`: the
    user's watches are spent, ENOSPC). Both are shared by every process of
    the user, a run's, another run's and an editor's alike."""

    def __init__(self, real, refused: str):
        self._real = real
        self._refused = refused

    def __getattr__(self, name):
        if name == self._refused:
            return lambda *a: -1
        return getattr(self._real, name)


@pytest.mark.parametrize("unseen", ["no_instance", "no_watch",
                                    "overflowed", "nothing_seen"])
def test_an_install_whose_reads_are_not_all_seen_is_not_recorded(
        leerie, env, hold, monkeypatch, unseen):
    """No inotify instance, a watch refused, an event lost, or an install
    seen opening nothing (as one whose tree the kernel reports by another
    name would be): the reads are not known, so no key is recorded and the
    next tip gets a fresh view."""
    st, staging, caps, _count = env
    if unseen in ("no_instance", "no_watch"):
        refused = {"no_instance": "inotify_init1",
                   "no_watch": "inotify_add_watch"}[unseen]
        real_cdll = leerie.ctypes.CDLL
        monkeypatch.setattr(
            leerie.ctypes, "CDLL",
            lambda *a, **k: _LimitedLibc(real_cdll(*a, **k), refused))
    elif unseen == "nothing_seen":
        real_watch = leerie._watch_reads

        def _blind(tree):
            w = real_watch(tree)
            return w and w._replace(dirs={})    # every event unattributed
        monkeypatch.setattr(leerie, "_watch_reads", _blind)
    else:
        real = leerie._drain_reads
        monkeypatch.setattr(leerie, "_drain_reads",
                            lambda w: real(w) and False)
    _commit(leerie, staging, {"src/a.txt": "2"})
    v1 = _measure(leerie, st, staging, caps)
    assert (v1 / "node_modules" / "dep" / "index.js").is_file()
    assert os.path.realpath(v1) not in leerie._VIEW_INSTALLS
    held = hold(v1 / "node_modules" / "dep" / "index.js")
    _commit(leerie, staging, {"src/a.txt": "3"})
    v2 = _measure(leerie, st, staging, caps)
    assert not _is_held(held, v2 / "node_modules/dep/index.js")


def test_the_install_key_is_what_the_install_read(leerie, env):
    """The key changes with a file read, a file appearing where one was
    read or in a directory read, and the recipe's install entries; not with
    any other file. It is read from the run repository, by commit."""
    st, staging, caps, _count = env
    a = _commit(leerie, staging, {"src/a.txt": "2"})
    v = _measure(leerie, st, staging, caps)
    files, opened = leerie._VIEW_INSTALLS[os.path.realpath(v)][2]
    assert "package.json" in files
    assert "src/a.txt" not in files    # the build read it, not the install
    b = _commit(leerie, staging, {"src/a.txt": "3"})
    repo = st.run_dir / "repo"
    recipe = st.data["provision"]["recipe"]
    reads = (frozenset({"cfg/lock.txt", "cfg/absent.txt"}),
             frozenset({"pkgs"}))
    base = _commit(leerie, staging, {"cfg/lock.txt": "1", "pkgs/a/m": "1",
                                     "other/x": "1"})
    same = [_commit(leerie, staging, {"other/x": "2"}),
            _commit(leerie, staging, {"other/y": "1"}),
            _commit(leerie, staging, {"pkgs/a/m": "2"})]
    moved = [_commit(leerie, staging, {"cfg/lock.txt": "2"}),
             _commit(leerie, staging, {"cfg/absent.txt": "1"}),
             _commit(leerie, staging, {"cfg/beside": "1"}),
             _commit(leerie, staging, {"pkgs/b/m": "1"})]

    def key(t, r=recipe, rd=reads):
        return leerie._install_key(repo, t, r, rd)
    k = key(base)
    # Each `same` commit adds to the one before; each `moved` one to the
    # last `same`, so each is compared with its own parent.
    assert [key(t) for t in same] == [k] * 3
    parents = [same[-1], *moved[:-1]]
    for parent, t in zip(parents, moved):
        assert key(t) != key(parent), t
    assert key(a, rd=(files, opened)) == key(b, rd=(files, opened))
    assert key(base) != key(base, [{"kind": "install",
                                    "command": ["other"]}])
    assert key(base) != key(base, [{**recipe[0], "env": {"X": "1"}},
                                   recipe[1]])
