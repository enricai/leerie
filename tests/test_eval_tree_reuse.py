"""The held-out evaluation tree is kept across a run's evaluations and
moved to the next commit with its install kept, the way a committed view is
(DESIGN §8 *The evaluation tree is kept across a run's evaluations*; §6 *A
held-out evaluation runs alone* — *The world that dies is the overlay, not
the tree on disk*).

Real git, the real scripts, the real `_prepare_eval_tree` running a recipe
whose install is incremental, as a package manager's is: it writes
`node_modules/dep/index.js` from `package.json` only when that differs, and
counts its runs under its own output. Driving `_prepare_eval_tree` directly
(not the full `_evaluate_acceptance_sets`, whose set files run in a Landlock
sandbox no host test can build) exercises every line of the reuse decision:
the install-key guard, `_move_view`'s `_install_intact` re-check, and the
fresh-install fallback.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest

from tests.test_run_repo import _git, _setup_run, _St, _user_repo

pytestmark = pytest.mark.real_planning_worktree

# Its run count is kept under its own output, `node_modules/.count`.
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


def _count(tree: Path) -> int:
    return int((tree / "node_modules" / ".count").read_text())


@pytest.fixture()
def env(leerie, tmp_path, monkeypatch):
    repo = _user_repo(tmp_path)
    (repo / "src").mkdir()
    (repo / "src" / "a.txt").write_text("1")
    (repo / "package.json").write_text('{"name": "x"}\n')
    (repo / ".gitignore").write_text("node_modules/\n")
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
         "working_dir": "."}]}
    staging = _setup_run(leerie, st)
    caps = dict(leerie.DEFAULT_CAPS)
    caps["worker_timeout_sec"] = 60
    yield st, staging, caps
    for d in (leerie._VIEWS, leerie._VIEW_INSTALLS, leerie._KEPT_TREES,
              leerie._KEPT_INSTALLS, leerie._DEPS_KEPT,
              leerie._INSTALL_OUTPUTS):
        d.clear()


def _commit(leerie, staging: Path, files: dict[str, str]) -> str:
    """Commit `files` on staging, take its branch into the run repository
    (where `_prepare_eval_tree` reads the commit), and return the commit."""
    for rel, text in files.items():
        (staging / rel).parent.mkdir(parents=True, exist_ok=True)
        (staging / rel).write_text(text)
    _git(staging, "add", "-A")
    _git(staging, "-c", "user.name=w", "-c", "user.email=w@w", "commit",
         "-qm", "work")
    leerie._take_tree_branch(staging)
    return _git(staging, "rev-parse", "HEAD")


def _prepare(leerie, st, caps, tip: str) -> tuple[str, Path]:
    """Put the evaluation tree at `tip`; return (status, its path)."""
    repo = leerie._run_repo(st)
    wt = leerie._helper_worktree_path(st, "acceptance-eval")
    status = asyncio.run(leerie._prepare_eval_tree(
        st, caps, repo, wt, tip,
        log_path=st.run_dir / "acceptance-eval.log", label="t"))
    return status, wt


@pytest.fixture()
def hold():
    fds: list[int] = []

    def _hold(p: Path) -> int:
        fds.append(os.open(p, os.O_RDONLY))
        return fds[-1]
    yield _hold
    for fd in fds:
        os.close(fd)


def _is_held(fd: int, p: Path) -> bool:
    return os.path.samestat(os.fstat(fd), os.stat(p))


def test_reuse_keeps_the_install_across_evaluations(leerie, env, hold):
    """A later evaluation at a new commit with the same install inputs keeps
    the install on disk (the same file) and re-runs the recipe incrementally
    over it — never a cold reinstall, and the tree is never torn down."""
    st, staging, caps = env
    a = _commit(leerie, staging, {"src/a.txt": "2"})
    status, wt = _prepare(leerie, st, caps, a)
    assert status == "ok"
    assert _git(wt, "rev-parse", "HEAD") == a
    dep = wt / "node_modules" / "dep" / "index.js"
    held = hold(dep)
    dep_mtime = os.stat(dep).st_mtime_ns
    assert _count(wt) == 1
    # A later evaluation at a new commit (source changed, install inputs not).
    b = _commit(leerie, staging, {"src/a.txt": "3"})
    status, wt2 = _prepare(leerie, st, caps, b)
    assert status == "ok"
    assert wt2 == wt                               # same path, never retired
    assert wt2.is_dir()
    assert _git(wt2, "rev-parse", "HEAD") == b     # moved to the new commit
    assert _is_held(held, dep)                     # the install was kept
    assert _count(wt2) == 2                         # re-run incrementally
    # The kept dependency was found in place, not rewritten: the incremental
    # install did near-zero work — the saving this change exists for.
    assert os.stat(dep).st_mtime_ns == dep_mtime
    assert _git(wt2, "status", "--porcelain") == ""


def test_a_changed_install_input_reinstalls(leerie, env, hold):
    """A fix that touches a file the install reads (the manifest) changes the
    install key, so the evaluation tree is remade and the deps installed
    afresh — the guard that keeps a stale install from being reused."""
    st, staging, caps = env
    a = _commit(leerie, staging, {"src/a.txt": "2"})
    _status, wt = _prepare(leerie, st, caps, a)
    dep = wt / "node_modules" / "dep" / "index.js"
    held = hold(dep)
    c = _commit(leerie, staging, {"package.json": '{"name": "y"}\n'})
    status, wt2 = _prepare(leerie, st, caps, c)
    assert status == "ok"
    assert not _is_held(held, wt2 / "node_modules/dep/index.js")  # fresh file
    assert (wt2 / "node_modules/dep/index.js").read_text() == '{"name": "y"}\n'
    assert _count(wt2) == 1                         # a cold install, not kept


def test_a_mutated_on_disk_install_is_never_reused(leerie, env, hold):
    """I3, the load-bearing claim. The sandbox overlay keeps a held-out
    evaluation's writes off the tree's disk; this is the defence in depth for
    it. Even an in-place edit of a file the install made — one that keeps its
    size and puts its mtime back — sends the evaluation tree back to a fresh
    install, so nothing a write ever reached the on-disk deps with can be
    carried into the next evaluation's measured dependencies."""
    st, staging, caps = env
    a = _commit(leerie, staging, {"src/a.txt": "2"})
    _status, wt = _prepare(leerie, st, caps, a)
    dep = wt / "node_modules" / "dep" / "index.js"
    before = os.stat(dep)
    text = dep.read_text()
    with open(dep, "r+") as f:               # a held-out test's hypothetical
        f.write("X" + text[1:])              # write, had it reached disk
    os.utime(dep, ns=(before.st_atime_ns, before.st_mtime_ns))
    held = hold(dep)
    b = _commit(leerie, staging, {"src/a.txt": "3"})
    status, wt2 = _prepare(leerie, st, caps, b)
    assert status == "ok"
    assert not _is_held(held, wt2 / "node_modules/dep/index.js")
    assert (wt2 / "node_modules/dep/index.js").read_text() == text  # restored


def test_a_failed_install_is_not_recorded_and_reinstalls(
        leerie, env, monkeypatch):
    """A failed install is no reuse memo (so the next evaluation installs
    again) and the status says so, as a view's failed install does."""
    st, staging, caps = env
    a = _commit(leerie, staging, {"src/a.txt": "2"})
    monkeypatch.setenv("FAIL_INSTALL", "1")
    status, wt = _prepare(leerie, st, caps, a)
    assert status == "deps_failed"
    assert os.path.realpath(wt) not in leerie._KEPT_INSTALLS
    monkeypatch.delenv("FAIL_INSTALL", raising=False)
    status, wt2 = _prepare(leerie, st, caps, a)
    assert status == "ok"
    assert _count(wt2) >= 1


def test_the_tree_is_forgotten_when_torn_down(leerie, env):
    """`_kept_teardown` removes the tree and every reuse memo of it, so a
    tree remade at the path installs afresh (no stale `_KEPT_INSTALLS`)."""
    st, staging, caps = env
    a = _commit(leerie, staging, {"src/a.txt": "2"})
    _status, wt = _prepare(leerie, st, caps, a)
    key = os.path.realpath(wt)
    assert key in leerie._KEPT_INSTALLS and key in leerie._KEPT_TREES
    leerie._kept_teardown(leerie._run_repo(st), wt)
    assert not wt.exists()
    assert key not in leerie._KEPT_INSTALLS
    assert key not in leerie._KEPT_TREES
    assert key not in leerie._DEPS_INSTALLED
