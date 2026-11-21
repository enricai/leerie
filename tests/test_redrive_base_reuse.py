"""The delivery re-drive's base-paired confirm worktree (`redrive-base`) is
kept across a run's re-drive confirms and reused with its install in place,
the same kept-tree machinery B*'s `confirm-base` worktree and the held-out
evaluation tree use (DESIGN §9 *The delivery re-drive's base confirm worktree
is kept too*; §8 *A view moves to the next tip when the install's inputs are
unchanged*).

Driven through the real `_redrive_new_failures` (which passes `reuse=True`)
and the real `_confirm_against_base`, building and moving a worktree whose
install is incremental, as a package manager's is. Only
`_capture_failing_test_ids` is stubbed — so no Landlock sandbox and no worker
runs — and it is stubbed to a *function of the base tree's on-disk state*, so
verdict-identity across confirms is a real test of outcome-identity.

The redrive-base worktree differs from B*'s `confirm-base` in one way: its
base is each group's or round's start commit (`pre_sha`), which moves as groups
commit, not the run's fixed base. So this file pins, on top of the shared
kept-tree guarantees (install-key guard and `_install_intact` re-check in
`tests/test_eval_tree_reuse.py`, the confirm wiring in
`tests/test_confirm_base_reuse.py`), the redrive-specific property: a confirm
against a *moved* `pre_sha` whose install inputs are unchanged still reuses the
install (the move checks it out at the new commit), and one whose manifest
changed reinstalls.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest

from tests.test_run_repo import _git, _setup_run, _St, _user_repo

pytestmark = pytest.mark.real_planning_worktree

# The install writes node_modules/dep/index.js == package.json and counts its
# runs under its own output (node_modules/.count): a cold run resets the count
# and rewrites the dep; an incremental run over a kept install finds the dep in
# place and does near-zero work.
INSTALL = (
    "import pathlib\n"
    "c = pathlib.Path('node_modules/.count')\n"
    "c.parent.mkdir(exist_ok=True)\n"
    "c.write_text(str(int(c.read_text()) + 1 if c.exists() else 1))\n"
    "want = open('package.json').read()\n"
    "p = pathlib.Path('node_modules/dep/index.js')\n"
    "if not p.exists() or p.read_text() != want:\n"
    "    p.parent.mkdir(parents=True, exist_ok=True)\n"
    "    p.write_text(want)\n")


def _count(tree: Path) -> int:
    return int((tree / "node_modules" / ".count").read_text())


def _dep(tree: Path) -> Path:
    return tree / "node_modules" / "dep" / "index.js"


@pytest.fixture()
def env(leerie, tmp_path, monkeypatch):
    repo = _user_repo(tmp_path)
    (repo / "src").mkdir()
    (repo / "src" / "a.txt").write_text("base")
    (repo / "package.json").write_text('{"name": "x"}\n')
    (repo / ".gitignore").write_text("node_modules/\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "app")
    base = _git(repo, "rev-parse", "HEAD")
    state = tmp_path / "state"
    monkeypatch.chdir(repo)
    monkeypatch.setenv("LEERIE_STATE_DIR", str(state))
    monkeypatch.setattr(leerie, "_cgroup_create", lambda *a, **k: None)
    st = _St(repo, state)
    st.data["provision"] = {"recipe": [
        {"kind": "install", "command": [sys.executable, "-c", INSTALL],
         "working_dir": "."}]}
    staging = _setup_run(leerie, st)
    (st.run_dir / "logs").mkdir(parents=True, exist_ok=True)
    # The run repository must hold the commit the confirm checks out.
    leerie._take_tree_branch(staging)
    caps = dict(leerie.DEFAULT_CAPS)
    caps["worker_timeout_sec"] = 60
    yield st, staging, base, caps
    for d in (leerie._VIEWS, leerie._VIEW_INSTALLS, leerie._KEPT_TREES,
              leerie._KEPT_INSTALLS, leerie._DEPS_KEPT,
              leerie._INSTALL_OUTPUTS):
        d.clear()


@pytest.fixture()
def observed(leerie, monkeypatch):
    """Stub `_capture_failing_test_ids` as a function of the base tree's
    on-disk state and record what each base-side rerun saw. The base passes
    `t_new` only when its install is correctly in place (dep == manifest) — a
    corrupt or leaked tree fails broadly, as a real suite without its deps
    does. The final tree fails `t_new`, so it is a candidate the base
    decides."""
    obs: list[dict] = []

    async def _fake(tree, cmd, st, caps, *, label, log_path, timeout,
                    serial=False, files=None, db=False, place=(), replace=(),
                    remove=(), suite=None):
        treep = Path(tree)
        if not os.path.realpath(tree).rstrip("/").endswith("staging"):
            dep = _dep(treep)
            manifest = (treep / "package.json").read_text()
            install_ok = dep.exists() and dep.read_text() == manifest
            src = treep / "src" / "a.txt"
            obs.append({
                "install_ok": install_ok,
                "dep_ino": os.stat(dep).st_ino if dep.exists() else None,
                "src": src.read_text() if src.exists() else None,
                "junk": (treep / "junk.txt").exists(),
                "head": _git(treep, "rev-parse", "HEAD"),
                "porcelain": _git(treep, "status", "--porcelain"),
            })
            ids = ["t_base"] if install_ok else ["t_base", "t_new"]
            passed = ["t_new"] if install_ok else []
            return {"ids": ids, "passed": passed, "files": {}, "messages": {}}
        return {"ids": ["t_base", "t_new"], "passed": [], "files": {},
                "messages": {}}

    monkeypatch.setattr(leerie, "_capture_failing_test_ids", _fake)
    return obs


def _redrive(leerie, st, caps, pre_sha, staging) -> dict:
    """One delivery re-drive confirm through the real caller: `t_new` fails
    after the group (final) and not before (base)."""
    before = {"ids": ["t_base"]}
    after = {"ids": ["t_base", "t_new"], "files": {"t_new": None},
             "command": "pytest"}
    return asyncio.run(leerie._redrive_new_failures(
        st, caps, staging, pre_sha, before, after, "redrive-r1g0"))


def _commit(leerie, staging, path, text, msg) -> str:
    (staging / path).write_text(text)
    _git(staging, "add", "-A")
    _git(staging, "-c", "user.name=w", "-c", "user.email=w@w", "commit",
         "-qm", msg)
    leerie._take_tree_branch(staging)
    return _git(staging, "rev-parse", "HEAD")


def test_reuse_keeps_the_base_install_across_redrive_confirms(
        leerie, env, observed):
    """Two re-drive confirms against the same start commit keep one worktree
    with one install: the dep is the same file, the costly reinstall never
    runs again (mtime unchanged, count incremental, not reset), and the
    verdict is identical — outcome-identity on the reused tree."""
    st, staging, base, caps = env
    wt = leerie._helper_worktree_path(st, "redrive-base")

    v1 = _redrive(leerie, st, caps, base, staging)
    assert v1["new"] == ["t_new"]              # base passes it, final fails it
    assert wt.is_dir()                         # kept, not torn down
    ino1 = os.stat(_dep(wt)).st_ino
    mtime1 = os.stat(_dep(wt)).st_mtime_ns
    count1 = _count(wt)

    v2 = _redrive(leerie, st, caps, base, staging)
    assert v2["new"] == ["t_new"]              # same verdict (outcome-identity)
    assert wt.is_dir()                         # still kept
    assert os.stat(_dep(wt)).st_ino == ino1    # same install, reused in place
    assert os.stat(_dep(wt)).st_mtime_ns == mtime1   # the reinstall didn't run
    assert _count(wt) == count1 + 1            # incremental, never a cold reset

    assert observed and all(o["install_ok"] for o in observed)
    assert all(o["head"] == base for o in observed)


def test_a_moved_pre_sha_with_unchanged_inputs_reuses(leerie, env, observed):
    """The redrive-specific case: a later group starts at a *different* commit
    (a prior group committed) but the install inputs are unchanged, so the
    kept install is reused — the move just checks the tree out at the new
    commit. The base side runs on the new commit's code with a correct
    install, and the verdict is identical (outcome-identity across a moved
    base)."""
    st, staging, base, caps = env
    wt = leerie._helper_worktree_path(st, "redrive-base")

    v1 = _redrive(leerie, st, caps, base, staging)
    assert v1["new"] == ["t_new"]
    ino1 = os.stat(_dep(wt)).st_ino
    count1 = _count(wt)

    # A second start commit: code changed, package.json (the install input)
    # byte-identical.
    base2 = _commit(leerie, staging, "src/a.txt", "base2", "advance")
    assert base2 != base

    observed.clear()
    v2 = _redrive(leerie, st, caps, base2, staging)
    assert v2["new"] == ["t_new"]              # outcome-identity on moved base
    assert os.stat(_dep(wt)).st_ino == ino1    # same install, reused in place
    assert _count(wt) == count1 + 1            # incremental, not a cold reset
    # The base side ran on the new commit's checkout with a correct install.
    assert observed and all(o["install_ok"] for o in observed)
    assert all(o["head"] == base2 for o in observed)
    assert all(o["src"] == "base2" for o in observed)


def test_a_moved_pre_sha_with_a_changed_manifest_reinstalls(
        leerie, env, observed):
    """The install-key guard still holds for a moved base: a start commit
    whose manifest the install reads changed is a different key, so the kept
    tree is remade and the deps installed afresh — reuse never serves a stale
    install for a base whose inputs differ."""
    st, staging, base, caps = env
    wt = leerie._helper_worktree_path(st, "redrive-base")
    _redrive(leerie, st, caps, base, staging)
    ino1 = os.stat(_dep(wt)).st_ino

    base2 = _commit(leerie, staging, "package.json", '{"name": "y"}\n', "bump")

    _redrive(leerie, st, caps, base2, staging)
    assert os.stat(_dep(wt)).st_ino != ino1    # a fresh install, not reused
    assert _count(wt) == 1                      # cold: the counter reset
    assert _dep(wt).read_text() == '{"name": "y"}\n'


def test_no_cross_redrive_state_leaks_into_the_next(leerie, env, observed):
    """State a prior confirm's base rerun could leave on the kept tree — an
    untracked report, an in-place edit of a tracked file — never reaches the
    next confirm: the move checks the tree out --detach --force and cleans it
    before reusing, so each base side runs on a pristine checkout."""
    st, staging, base, caps = env
    wt = leerie._helper_worktree_path(st, "redrive-base")
    _redrive(leerie, st, caps, base, staging)

    # Residue a prior base rerun left on the kept tree on disk.
    (wt / "junk.txt").write_text("a hidden test's cache")
    (wt / "src" / "a.txt").write_text("CLOBBERED")

    observed.clear()
    v = _redrive(leerie, st, caps, base, staging)
    assert v["new"] == ["t_new"]               # unchanged despite the residue
    assert observed and all(o["install_ok"] for o in observed)
    assert all(not o["junk"] for o in observed)         # untracked junk cleaned
    assert all(o["src"] == "base" for o in observed)    # tracked edit reset
    assert all(o["porcelain"] == "" for o in observed)  # nothing left over


class _Stop(Exception):
    pass


def test_the_redrive_caller_opts_into_reuse(leerie, env, monkeypatch):
    """The production wiring: `_redrive_new_failures` passes `reuse=True` (and
    `worktree="redrive-base"`) to `_confirm_against_base`, so a run's re-drive
    confirms keep the tree. Driven through the real caller, capturing the
    kwargs the confirm is called with — not a source scan."""
    st, staging, base, caps = env
    captured: dict = {}

    async def _fake_cab(st_, caps_, **kw):
        captured.update(kw)
        raise _Stop

    monkeypatch.setattr(leerie, "_confirm_against_base", _fake_cab)
    before = {"ids": ["t_base"]}
    after = {"ids": ["t_base", "t_new"], "files": {"t_new": None},
             "command": "pytest"}
    with pytest.raises(_Stop):
        asyncio.run(leerie._redrive_new_failures(
            st, caps, staging, base, before, after, "redrive-r1g0"))
    assert captured.get("worktree") == "redrive-base"
    assert captured.get("reuse") is True
    assert captured.get("base_sha") == base
