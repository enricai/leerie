"""The base-form check's base-paired confirm worktree (`base-form-base`) is
kept across a run's base-form confirms and reused with its install in place,
the same kept-tree machinery B*'s `confirm-base` worktree, the re-drive's
`redrive-base` worktree and the held-out evaluation tree use (DESIGN §9 *The
base-form check's base confirm worktree is kept too*; §8 *A view moves to the
next tip when the install's inputs are unchanged*).

Driven through the real `_base_form_regressions` (which passes `reuse=True`)
and the real `_confirm_against_base`, over a real git repository whose base had
a test-case file the run's commit modified — so the real `_base_form_files` and
`_base_form_place` run and a candidate reaches `_confirm_against_base`. Only
`_capture_failing_test_ids` is stubbed — so no Landlock sandbox and no worker
runs — and it is stubbed to a *function of the base tree's on-disk state*, so
verdict-identity across confirms is a real test of outcome-identity.

`base-form-base` has both base-commit shapes the mechanism already covers: its
`base_sha` is the run's fixed base for the existing-behaviour and
base-form-fixed callers (like B*'s `confirm-base`) and a round's `pre_sha` for
the acceptance ratchet (like the re-drive's `redrive-base`). This file pins,
on top of the shared kept-tree guarantees (install-key guard and
`_install_intact` re-check in `tests/test_eval_tree_reuse.py`, the confirm
wiring in `tests/test_confirm_base_reuse.py`, the moved-base property in
`tests/test_redrive_base_reuse.py`), that `_base_form_regressions` opts into
reuse and keeps the worktree with an identical verdict across confirms, for
both a fixed and a moved `before`.
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
# place and does near-zero work. (Same shape as tests/test_redrive_base_reuse.)
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

# A test-case file the base pins; `_base_form_case` recognises `tests/test_*.py`.
PIN_BASE = "def test_pin():\n    assert True  # base form\n"
PIN_FINAL = "def test_pin():\n    assert True  # final form\n"


def _count(tree: Path) -> int:
    return int((tree / "node_modules" / ".count").read_text())


def _dep(tree: Path) -> Path:
    return tree / "node_modules" / "dep" / "index.js"


@pytest.fixture()
def env(leerie, tmp_path, monkeypatch):
    repo = _user_repo(tmp_path)
    (repo / "tests").mkdir()
    (repo / "tests" / "test_pin.py").write_text(PIN_BASE)
    (repo / "code.py").write_text("# v0\n")
    (repo / "package.json").write_text('{"name": "x"}\n')
    (repo / ".gitignore").write_text("node_modules/\n.leerie/\n")
    (repo / ".leerie").mkdir()
    (repo / ".leerie" / "config.toml").write_text(
        'test = "python3 -m pytest -q tests"\n'
        'test_scoped = "python3 -m pytest -q {test_files}"\n')
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "app")
    base = _git(repo, "rev-parse", "HEAD")
    state = tmp_path / "state"
    monkeypatch.chdir(repo)
    monkeypatch.setenv("LEERIE_STATE_DIR", str(state))
    monkeypatch.setattr(leerie, "_cgroup_create", lambda *a, **k: None)
    st = _St(repo, state)
    st.data["base_sha"] = base               # _diff_base reads this
    st.data["provision"] = {"recipe": [
        {"kind": "install", "command": [sys.executable, "-c", INSTALL],
         "working_dir": "."}]}
    staging = _setup_run(leerie, st)
    (st.run_dir / "logs").mkdir(parents=True, exist_ok=True)
    # The run's commit modifies the test-case file, so the base-form check has
    # a file and a candidate; the run repository must hold the commit the
    # confirm checks out.
    after = _commit(leerie, staging, "tests/test_pin.py", PIN_FINAL, "fix")
    caps = dict(leerie.DEFAULT_CAPS)
    caps["worker_timeout_sec"] = 60
    yield st, staging, base, after, caps
    for d in (leerie._VIEWS, leerie._VIEW_INSTALLS, leerie._KEPT_TREES,
              leerie._KEPT_INSTALLS, leerie._DEPS_KEPT,
              leerie._INSTALL_OUTPUTS):
        d.clear()


@pytest.fixture()
def observed(leerie, monkeypatch):
    """Stub `_capture_failing_test_ids` as a function of the base tree's
    on-disk state and record what each base-side rerun saw. The base passes
    the pinned case only when its install is correctly in place (dep ==
    manifest) — a corrupt or leaked tree fails broadly, as a real suite
    without its deps does. The run's base form fails on staging (the final
    tree), so it is a candidate the base decides."""
    obs: list[dict] = []

    async def _fake(tree, cmd, st, caps, *, label, log_path, timeout,
                    serial=False, files=None, db=False, place=(), replace=(),
                    remove=(), suite=None):
        treep = Path(tree)
        if os.path.realpath(tree).rstrip("/").endswith("staging"):
            # The base form fails on the tree that ships: both IDs are
            # candidates the base side then confirms.
            return {"ids": ["t_pin", "t_was_red"], "passed": [], "files": {},
                    "messages": {}}
        dep = _dep(treep)
        manifest = (treep / "package.json").read_text()
        install_ok = dep.exists() and dep.read_text() == manifest
        obs.append({
            "install_ok": install_ok,
            "dep_ino": os.stat(dep).st_ino if dep.exists() else None,
            "pin": ((treep / "tests" / "test_pin.py").read_text()
                    if (treep / "tests" / "test_pin.py").exists() else None),
            "junk": (treep / "junk.txt").exists(),
            "head": _git(treep, "rev-parse", "HEAD"),
            "porcelain": _git(treep, "status", "--porcelain"),
        })
        # t_pin passes on the base when the install is intact (→ `changed`);
        # t_was_red was already red on the base (→ pre_existing, filtered).
        ids = ["t_was_red"] if install_ok else ["t_pin", "t_was_red"]
        passed = ["t_pin"] if install_ok else []
        return {"ids": ids, "passed": passed, "files": {}, "messages": {}}

    monkeypatch.setattr(leerie, "_capture_failing_test_ids", _fake)
    return obs


def _commit(leerie, staging, path, text, msg) -> str:
    p = staging / path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    _git(staging, "add", "-A")
    _git(staging, "-c", "user.name=w", "-c", "user.email=w@w", "commit",
         "-qm", msg)
    leerie._take_tree_branch(staging)
    return _git(staging, "rev-parse", "HEAD")


def _baseform(leerie, st, caps, staging, before, **kw) -> dict:
    """One base-form confirm through the real caller."""
    return asyncio.run(leerie._base_form_regressions(
        st, caps, staging, before, label="t",
        log_path=st.run_dir / "logs" / "t.log", **kw))


def _changed(rec: dict) -> list[str]:
    return sorted(r["id"] for r in rec["changed"])


def test_reuse_keeps_the_base_install_across_base_form_confirms(
        pin, leerie, env, observed):
    """Two base-form confirms against the same base keep one worktree with one
    install: the dep is the same file, the costly reinstall never runs again
    (mtime unchanged, count incremental, not reset), and the verdict is
    identical — outcome-identity on the reused tree."""
    st, staging, base, after, caps = env
    wt = leerie._helper_worktree_path(st, "base-form-base")

    r1 = _baseform(leerie, st, caps, staging, base)
    assert _changed(r1) == ["t_pin"]           # base passes it, final fails it
    assert wt.is_dir()                         # kept, not torn down
    same = pin(_dep(wt))
    mtime1 = os.stat(_dep(wt)).st_mtime_ns
    count1 = _count(wt)

    r2 = _baseform(leerie, st, caps, staging, base)
    assert _changed(r2) == ["t_pin"]           # same verdict (outcome-identity)
    assert wt.is_dir()                         # still kept
    assert same()                              # same install, reused in place
    assert os.stat(_dep(wt)).st_mtime_ns == mtime1   # the reinstall didn't run
    assert _count(wt) == count1 + 1            # incremental, never a cold reset

    assert observed and all(o["install_ok"] for o in observed)
    assert all(o["head"] == base for o in observed)


def test_a_moved_before_with_unchanged_inputs_reuses(
        pin, leerie, env, observed):
    """The acceptance-ratchet shape: the confirm's base is a round's start
    commit (`before`), which moves as groups commit, not the run's fixed base.
    When its install inputs are unchanged the kept install is reused — the move
    just checks the tree out at the new commit (reach_of narrows to the files
    the round reaches, as the ratchet caller passes)."""
    st, staging, base, after, caps = env
    wt = leerie._helper_worktree_path(st, "base-form-base")

    r1 = _baseform(leerie, st, caps, staging, base)
    assert _changed(r1) == ["t_pin"]
    same = pin(_dep(wt))
    count1 = _count(wt)

    # A round start between base and the run's commit: existing code changed
    # (its name unchanged, so no read directory's tracked names move),
    # package.json (the install input) byte-identical; a later commit modifies
    # the pinned test so `start..after` still reaches it.
    start = _commit(leerie, staging, "code.py", "# v1\n", "advance")
    after2 = _commit(leerie, staging, "tests/test_pin.py",
                     PIN_FINAL + "# r2\n", "refix")
    assert start != base and after2 != start

    observed.clear()
    r2 = _baseform(leerie, st, caps, staging, start, reach_of=start)
    assert _changed(r2) == ["t_pin"]           # outcome-identity on moved base
    assert same()                              # same install, reused in place
    assert _count(wt) == count1 + 1            # incremental, not a cold reset
    # The base side ran on the moved start's checkout with a correct install.
    assert observed and all(o["install_ok"] for o in observed)
    assert all(o["head"] == start for o in observed)


def test_a_moved_before_with_a_changed_manifest_reinstalls(
        pin, leerie, env, observed):
    """The install-key guard still holds for a moved base: a start commit
    whose manifest the install reads changed is a different key, so the kept
    tree is remade and the deps installed afresh — reuse never serves a stale
    install for a base whose inputs differ."""
    st, staging, base, after, caps = env
    wt = leerie._helper_worktree_path(st, "base-form-base")
    _baseform(leerie, st, caps, staging, base)
    same = pin(_dep(wt))

    # The round start changed the manifest; a later commit modifies the pinned
    # test so `start..after` still reaches it.
    start = _commit(leerie, staging, "package.json", '{"name": "y"}\n', "bump")
    _commit(leerie, staging, "tests/test_pin.py", PIN_FINAL + "# r2\n", "refix")

    _baseform(leerie, st, caps, staging, start, reach_of=start)
    assert not same()                          # a fresh install, not reused
    assert _count(wt) == 1                      # cold: the counter reset
    assert _dep(wt).read_text() == '{"name": "y"}\n'


def test_no_cross_confirm_state_leaks_into_the_next(leerie, env, observed):
    """State a prior confirm's base rerun could leave on the kept tree — an
    untracked report, an in-place edit of a tracked file — never reaches the
    next confirm: the move checks the tree out --detach --force and cleans it
    before reusing, so each base side runs on a pristine checkout."""
    st, staging, base, after, caps = env
    wt = leerie._helper_worktree_path(st, "base-form-base")
    _baseform(leerie, st, caps, staging, base)

    # Residue a prior base rerun left on the kept tree on disk.
    (wt / "junk.txt").write_text("a hidden test's cache")
    (wt / "tests" / "test_pin.py").write_text("CLOBBERED\n")

    observed.clear()
    r = _baseform(leerie, st, caps, staging, base)
    assert _changed(r) == ["t_pin"]            # unchanged despite the residue
    assert observed and all(o["install_ok"] for o in observed)
    assert all(not o["junk"] for o in observed)         # untracked junk cleaned
    assert all(o["pin"] == PIN_BASE for o in observed)  # tracked edit reset
    assert all(o["porcelain"] == "" for o in observed)  # nothing left over


class _Stop(Exception):
    pass


def test_the_base_form_caller_opts_into_reuse(leerie, env, monkeypatch):
    """The production wiring: `_base_form_regressions` passes `reuse=True` (and
    `worktree="base-form-base"`, `base_sha=before`) to `_confirm_against_base`,
    so a run's base-form confirms keep the tree. Driven through the real
    caller, capturing the kwargs the confirm is called with — not a source
    scan."""
    st, staging, base, after, caps = env
    captured: dict = {}

    async def _fake_first(tree, cmd, st_, caps_, **kw):
        # The staging base-form run that produces the candidate the confirm
        # then decides.
        return {"ids": ["t_pin"], "passed": [], "files": {}, "messages": {}}

    async def _fake_cab(st_, caps_, **kw):
        captured.update(kw)
        raise _Stop

    monkeypatch.setattr(leerie, "_capture_failing_test_ids", _fake_first)
    monkeypatch.setattr(leerie, "_confirm_against_base", _fake_cab)
    with pytest.raises(_Stop):
        _baseform(leerie, st, caps, staging, base)
    assert captured.get("worktree") == "base-form-base"
    assert captured.get("reuse") is True
    assert captured.get("base_sha") == base
