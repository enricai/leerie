"""The base-paired confirm worktree (B*'s `confirm-base`) is kept across a
run's confirms and reused with its install in place, the same kept-tree
machinery the held-out evaluation tree uses (DESIGN §9 *The base confirm
worktree is kept across the run's confirms*; §8 *A view moves to the next tip
when the install's inputs are unchanged*).

Real git, the real scripts, the real `_confirm_against_base` (with
`reuse=True`) building and moving a worktree whose install is incremental, as
a package manager's is. Only `_capture_failing_test_ids` is stubbed — so no
Landlock sandbox and no worker runs — and it is stubbed to a *function of the
base tree's on-disk state*: a correctly reused install yields one verdict, a
corrupt or leaked tree another, so verdict-identity across confirms is a real
test of outcome-identity, not an assertion about it. The held-out eval tree's
own guarantees (install-key guard, `_install_intact` re-check, failed-install
not recorded) are pinned in `tests/test_eval_tree_reuse.py`; this file pins
the confirm wiring on top: the tree is kept across confirms (not torn down),
the base side always runs on a pristine, correctly-installed checkout, and
`reuse=False` keeps the old disposable behaviour for the other callers.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest

from tests.test_run_repo import _git, _setup_run, _St, _user_repo

pytestmark = pytest.mark.real_planning_worktree

# The install writes node_modules/dep/index.js == package.json, and counts its
# runs under its own output (node_modules/.count) — a cold run resets nothing
# but writes the dep; an incremental run over a kept install finds the dep in
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
    # The run repository must hold the base commit the confirm checks out.
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
    on-disk state and record what each base-side rerun saw. The base is a
    deterministically red tree for `t_base`; it passes `t_new` only when its
    install is correctly in place (dep == manifest) — a corrupt or leaked
    tree fails broadly (`t_new` too), as a real suite without its deps does.
    The final tree fails both, so `t_new` is a candidate the base decides."""
    obs: list[dict] = []

    async def _fake(tree, cmd, st, caps, *, label, log_path, timeout,
                    serial=False, files=None, db=False, place=(), replace=(),
                    remove=(), suite=None):
        treep = Path(tree)
        # The final side is staging; every other tree is a base worktree.
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


def _confirm(leerie, st, caps, base_sha, staging, *, worktree="confirm-base",
             reuse=True) -> dict:
    return asyncio.run(leerie._confirm_against_base(
        st, caps, base_sha=base_sha, final_tree=str(staging), cmd="pytest",
        candidates=["t_new"], id_files={"t_new": None}, label="final",
        log_path=st.run_dir / "confirm.log", worktree=worktree,
        deps_label="final-base", deps_log="confirm", run_prefix="final",
        confirm_label="final", say_unavailable=True, reuse=reuse))


def test_reuse_keeps_the_base_install_across_confirms(
        pin, leerie, env, observed):
    """Two confirms against the same base keep one worktree with one install:
    the dep is the same file, the costly reinstall never runs again
    (mtime unchanged, count incremental, not reset), and the verdict is
    identical — outcome-identity on the reused tree."""
    st, staging, base, caps = env
    wt = leerie._helper_worktree_path(st, "confirm-base")

    v1 = _confirm(leerie, st, caps, base, staging)
    assert v1["new"] == ["t_new"]              # base passes it, final fails it
    assert wt.is_dir()                         # kept, not torn down
    same = pin(_dep(wt))
    mtime1 = os.stat(_dep(wt)).st_mtime_ns
    count1 = _count(wt)

    v2 = _confirm(leerie, st, caps, base, staging)
    assert v2["new"] == ["t_new"]              # same verdict (outcome-identity)
    assert wt.is_dir()                         # still kept
    assert same()                              # same install, reused in place
    assert os.stat(_dep(wt)).st_mtime_ns == mtime1   # the reinstall didn't run
    assert _count(wt) == count1 + 1            # incremental, never a cold reset

    # Every base rerun, both confirms, saw a correctly installed base tree.
    assert observed and all(o["install_ok"] for o in observed)
    assert all(o["head"] == base for o in observed)


def test_a_changed_base_manifest_reinstalls(pin, leerie, env, observed):
    """A confirm against a different base whose manifest differs changes the
    install key, so the kept tree is remade and the deps installed afresh —
    the guard against reusing a stale install for a different base."""
    st, staging, base, caps = env
    wt = leerie._helper_worktree_path(st, "confirm-base")
    _confirm(leerie, st, caps, base, staging)
    same = pin(_dep(wt))

    # A second base commit with a different manifest the install reads.
    (staging / "package.json").write_text('{"name": "y"}\n')
    _git(staging, "add", "-A")
    _git(staging, "-c", "user.name=w", "-c", "user.email=w@w", "commit",
         "-qm", "bump")
    leerie._take_tree_branch(staging)
    base2 = _git(staging, "rev-parse", "HEAD")

    _confirm(leerie, st, caps, base2, staging)
    assert not same()                          # a fresh install, not reused
    assert _count(wt) == 1                      # cold: the counter reset
    assert _dep(wt).read_text() == '{"name": "y"}\n'


def test_no_confirm_state_leaks_into_the_next(leerie, env, observed):
    """State a prior confirm's base rerun could leave on the kept tree — an
    untracked report, an in-place edit of a tracked file — never reaches the
    next confirm: the move checks the tree out --detach --force and cleans it
    before reusing, so each base side runs on a pristine base checkout."""
    st, staging, base, caps = env
    wt = leerie._helper_worktree_path(st, "confirm-base")
    _confirm(leerie, st, caps, base, staging)

    # Simulate residue a prior base rerun left on the kept tree on disk.
    (wt / "junk.txt").write_text("a hidden test's cache")
    (wt / "src" / "a.txt").write_text("CLOBBERED")

    observed.clear()
    v = _confirm(leerie, st, caps, base, staging)
    assert v["new"] == ["t_new"]               # unchanged despite the residue
    # Every base rerun of the second confirm saw a pristine base checkout.
    assert observed and all(o["install_ok"] for o in observed)
    assert all(not o["junk"] for o in observed)         # untracked junk cleaned
    assert all(o["src"] == "base" for o in observed)    # tracked edit reset
    assert all(o["porcelain"] == "" for o in observed)  # nothing left over


def test_reuse_false_keeps_the_disposable_teardown(leerie, env, observed):
    """The remaining disposable caller (`base-form-base`, `reuse=False`)
    keeps the old behaviour: the worktree is torn down on exit and never
    entered into the kept-tree memos."""
    st, staging, base, caps = env
    wt = leerie._helper_worktree_path(st, "base-form-base")
    v = _confirm(leerie, st, caps, base, staging, worktree="base-form-base",
                 reuse=False)
    assert v["new"] == ["t_new"]
    assert not wt.exists()                              # removed on exit
    assert os.path.realpath(wt) not in leerie._KEPT_TREES
    assert os.path.realpath(wt) not in leerie._KEPT_INSTALLS


class _Stop(Exception):
    pass


def test_the_confirm_base_caller_opts_into_reuse(leerie, env, monkeypatch):
    """The production wiring: `_confirm_final_test_failures`'s base confirm
    passes `reuse=True` (and `worktree="confirm-base"`), so a run's confirms
    actually keep the tree. Driven through the real caller, capturing the
    kwargs the confirm is called with — not a source scan."""
    st, staging, base, caps = env
    captured: dict = {}

    async def _fake_cab(st_, caps_, **kw):
        captured.update(kw)
        raise _Stop

    async def _noop(*a, **k):
        return False

    monkeypatch.setattr(leerie, "_confirm_against_base", _fake_cab)
    monkeypatch.setattr(leerie, "_retry_failed_baseline", _noop)
    st.data["baseline_failing_ids"] = ["t_base"]
    st.data["conformance"] = {"_baseline": {"sha": base}}
    post = {"tests": {"measured": True, "command": "pytest",
                      "failing_ids": ["t_base", "t_new"],
                      "failing_id_files": {}}}
    with pytest.raises(_Stop):
        asyncio.run(leerie._confirm_final_test_failures(
            st, caps, str(staging), post,
            log_path=st.run_dir / "confirm.log", label="final",
            tree_sha="deadbeef"))
    assert captured.get("worktree") == "confirm-base"
    assert captured.get("reuse") is True
    assert captured.get("base_sha") == base       # the fixed base commit
