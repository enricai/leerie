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
from pathlib import Path

import pytest

from tests.test_run_repo import _git, _setup_run, _St, _user_repo

pytestmark = pytest.mark.real_planning_worktree

INSTALL = (
    "import os, pathlib, sys\n"
    "c = pathlib.Path(sys.argv[1])\n"
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
    count = tmp_path / "install.count"
    st.data["provision"] = {"recipe": [
        {"kind": "install", "command": [sys.executable, "-c", INSTALL,
                                        str(count)], "working_dir": "."},
        {"kind": "build", "command": [sys.executable, "-c", BUILD],
         "working_dir": "."}]}
    staging = _setup_run(leerie, st)
    caps = dict(leerie.DEFAULT_CAPS)
    caps["worker_timeout_sec"] = 60
    yield st, staging, caps, count
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


def _ino(p: Path) -> int:
    return os.lstat(p).st_ino


def test_a_new_tip_with_the_same_inputs_keeps_the_install(leerie, env):
    """The view moves: the install's own output stays (same inode), the
    install runs again over it, the builds run again, and nothing else of
    the old tip — a measurement's report, a tool's cache under
    `node_modules`, a build's output — is left."""
    st, staging, caps, count = env
    a = _commit(leerie, staging, {"src/a.txt": "2"})
    v1 = _measure(leerie, st, staging, caps)
    assert _git(v1, "rev-parse", "HEAD") == a
    dep = v1 / "node_modules" / "dep" / "index.js"
    ino = _ino(dep)
    assert (v1 / "node_modules" / ".gen" / "out").read_text() == "2"
    # What measurements left at tip a.
    (v1 / "report.txt").write_text("old")
    (v1 / "dist" / "stale.txt").write_text("old")
    (v1 / "node_modules" / ".cache").mkdir()
    (v1 / "node_modules" / ".cache" / "x").write_text("old")
    b = _commit(leerie, staging, {"src/a.txt": "3"})
    v2 = _measure(leerie, st, staging, caps)
    assert v2 == v1
    assert _git(v2, "rev-parse", "HEAD") == b
    assert _ino(dep) == ino                        # the install kept
    assert int(count.read_text()) == 2             # and run again over it
    assert (v2 / "dist" / "out.txt").read_text() == "3"
    for gone in ("report.txt", "dist/stale.txt", "node_modules/.cache",
                 "node_modules/.gen"):             # a build's is not kept
        assert not os.path.lexists(v2 / gone), gone
    assert _git(v2, "status", "--porcelain") == ""


def test_a_changed_install_input_makes_a_fresh_view(leerie, env):
    st, staging, caps, _count = env
    _commit(leerie, staging, {"src/a.txt": "2"})
    v1 = _measure(leerie, st, staging, caps)
    ino = _ino(v1 / "node_modules")
    c = _commit(leerie, staging, {"package.json": '{"name": "y"}\n'})
    v2 = _measure(leerie, st, staging, caps)
    assert _git(v2, "rev-parse", "HEAD") == c
    assert _ino(v2 / "node_modules") != ino
    assert (v2 / "node_modules/dep/index.js").read_text() == \
        '{"name": "y"}\n'


def test_an_install_a_measurement_changed_is_not_kept(leerie, env):
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
    nm = _ino(v1 / "node_modules")
    _commit(leerie, staging, {"src/a.txt": "3"})
    v2 = _measure(leerie, st, staging, caps)
    assert _ino(v2 / "node_modules") != nm
    assert (v2 / "node_modules/dep/index.js").read_text() == text


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
    admin = leerie._view_admin_dir(st.run_dir / "repo", v2)
    assert admin is not None
    assert (v2 / ".git").read_text() == f"gitdir: {admin}\n"


def test_a_failed_install_is_not_recorded(leerie, env, monkeypatch):
    st, staging, caps, _count = env
    monkeypatch.setenv("FAIL_INSTALL", "1")
    _commit(leerie, staging, {"src/a.txt": "2"})
    v1 = _measure(leerie, st, staging, caps)
    assert os.path.realpath(v1) not in leerie._VIEW_INSTALLS


def test_the_install_key_reads_the_inputs_by_commit(leerie, env):
    """The key changes with an input, with the recipe's install entries and
    with a named copy input, and not with any other file; it is read from
    the run repository."""
    st, staging, _caps, _count = env
    repo = st.run_dir / "repo"
    recipe = st.data["provision"]["recipe"]
    a = _commit(leerie, staging, {"src/a.txt": "2"})
    b = _commit(leerie, staging, {"src/a.txt": "3"})
    c = _commit(leerie, staging, {"patches/dep.patch": "p"})
    d = _commit(leerie, staging, {"notes.md": "n"})
    e = _commit(leerie, staging, {"pkg/b/package.json": "{}"})

    def key(t, r=recipe, ci=()):
        return leerie._install_key(repo, t, r, list(ci))
    assert key(a) == key(b) != key(c) == key(d) != key(e)
    assert key(a) != key(a, [{"kind": "install", "command": ["other"]}])
    assert key(a) != key(a, [{**recipe[0], "env": {"X": "1"}}, recipe[1]])
    assert key(a) != key(a, ci=["src/a.txt"])
