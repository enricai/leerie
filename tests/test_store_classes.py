"""Which stores an install may share (DESIGN §6 *Which stores an install
may share*).

Every store variable is the run's own; the per-tree class holds one tree's
environment or build, one set per tree. These tests pin the routing, and
that a recipe's install runs as the recipe names it."""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(__file__))
from test_shipping_tree_is_committed import _git, _repo  # noqa: E402

from tests.conftest import measured_tmp_files  # noqa: E402


def test_the_per_tree_class_is_a_subset_of_the_store_variables(leerie):
    assert (leerie._TREE_STORE_VARS | leerie._KEYED_STORE_VARS) <= set(leerie._STORE_VARS)


def _tree(tmp_path) -> tuple[Path, Path]:
    run = tmp_path / "runs" / "r1"
    t = run / "worktrees" / "r1-view-staging"
    (t / "sub").mkdir(parents=True)
    (t / ".git").write_text("gitdir: x\n")
    return run, t


def test_a_command_in_a_run_tree_gets_that_trees_own_stores(leerie,
                                                            tmp_path):
    run, t = _tree(tmp_path)
    # A value an image chose, beneath /opt/ where a bake used to put
    # dependencies, is moved like any other: the image holds no dependency
    # (DESIGN §6½ *The repository image holds no dependency*).
    env = {"GOCACHE": "/opt/go-cache", "BUNDLE_PATH": "/opt/bundle",
           "UV_CACHE_DIR": "/shared/uv"}
    leerie._tree_store_env(env, t / "sub")
    root = run / "worktrees" / ".stores" / "tree-stores" / t.name
    for var in (leerie._TREE_STORE_VARS | leerie._KEYED_STORE_VARS):
        assert env[var] == str(root / leerie._STORE_VARS[var]), var
        assert Path(env[var]).is_dir()
    assert env["pnpm_config_cache_dir"] == env["npm_config_cache_dir"]
    other = {"UV_CACHE_DIR": "/shared/uv"}
    leerie._tree_store_env(other, tmp_path)               # no run tree
    assert other == {"UV_CACHE_DIR": "/shared/uv"}


def test_a_trees_own_stores_are_made_private(leerie, tmp_path):
    """Every directory leerie makes for a measurement is 0700 (DESIGN §6 *A
    confined command's scratch is short and private*), each store under
    `tree-stores/<tree>/` too — not only that directory. Under umask 0 a
    plain `mkdir` would leave them 0777."""
    run, t = _tree(tmp_path)
    old = os.umask(0)
    try:
        env: dict[str, str] = {}
        leerie._tree_store_env(env, t)
    finally:
        os.umask(old)
    root = run / "worktrees" / ".stores" / "tree-stores" / t.name
    made = [root.parent, root,
            *(Path(env[v]) for v in (leerie._TREE_STORE_VARS | leerie._KEYED_STORE_VARS))]
    for d in made:
        assert d.is_dir(), d
        assert (d.stat().st_mode & 0o777) == 0o700, (d, oct(d.stat().st_mode))


def test_run_streaming_runs_a_repo_command_with_its_trees_stores(
        leerie, tmp_path):
    run, t = _tree(tmp_path)
    log = tmp_path / "l.log"
    out = tmp_path / "env.json"
    # Granted everything: this pins the store routing, not the grant.
    rc, _ = asyncio.run(leerie._run_streaming(
        [sys.executable, "-c",
         f"import json,os; json.dump(dict(os.environ), open({str(out)!r},'w'))"],
        grants=leerie.WorkerGrants(("/",), ()),
        cwd=str(t), env=None, timeout=60, log_path=log, label="t",
        verbosity="quiet"))
    assert rc == 0
    got = json.loads(out.read_text())
    assert got["UV_CACHE_DIR"] == str(run / "worktrees" / ".stores" / "tree-stores" / t.name / "uv")


def test_an_install_into_an_image_path_lands_in_the_trees_store(
        leerie, tmp_path):
    """A command whose environment arrives with `BUNDLE_PATH=/opt/bundle`
    (what a 0.65.0 bake set) writes its install into the tree's own,
    writable store, not into the image's read-only path; a variable that is
    no store (`VIRTUAL_ENV`) is passed through as it came (DESIGN §6½ *The
    repository image holds no dependency*)."""
    run, t = _tree(tmp_path)
    log = tmp_path / "l.log"
    incoming = {**os.environ, "BUNDLE_PATH": "/opt/bundle",
                "VIRTUAL_ENV": "/opt/venv"}
    script = ("import os,pathlib\n"
              "d=pathlib.Path(os.environ['BUNDLE_PATH'])/'gems'\n"
              "d.mkdir(parents=True)\n"
              "(d/'rack.gemspec').write_text(os.environ['VIRTUAL_ENV'])\n")
    rc, tail = asyncio.run(leerie._run_streaming(
        [sys.executable, "-c", script],
        grants=leerie.WorkerGrants(("/",), ()),
        cwd=str(t), env=incoming, timeout=60, log_path=log, label="t",
        verbosity="quiet"))
    assert rc == 0, tail
    gem = (run / "worktrees" / ".stores" / "tree-stores" / t.name / "bundle"
           / "gems" / "rack.gemspec")
    assert gem.read_text() == "/opt/venv"


def _fake(bindir: Path, name: str, rec: Path) -> None:
    """A stand-in manager that records its argv, cwd listing and script
    settings as `rec.name` in its own temp directory: it runs confined, and
    writes nothing beside its tree (DESIGN §6 *Leerie's runs of the
    repository's commands are confined too*)."""
    f = bindir / name
    f.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        f"json.dump({{'argv': sys.argv[1:], 'cwd': sorted(os.listdir('.')),"
        f" 'env': {{k: v for k, v in os.environ.items()"
        f" if 'SCRIPT' in k.upper()}}}},"
        f" open(os.path.join(os.environ['TMPDIR'], {rec.name!r}), 'w'))\n")
    f.chmod(0o755)


def _recorded(st, rec: Path) -> dict:
    """The newest record `_fake` left in one of the run's measurements."""
    got = sorted(measured_tmp_files(st.run_dir, rec.name),
                 key=lambda f: f.stat().st_mtime_ns)
    assert got, rec.name
    return json.loads(got[-1].read_text())


@pytest.mark.parametrize("cmd", [["bun", "install", "--frozen-lockfile"],
                                 ["pnpm", "install", "--frozen-lockfile"]])
def test_a_recipe_install_runs_as_the_recipe_names_it(leerie, tmp_path,
                                                      monkeypatch, cmd):
    """No manager-specific flag or setting is added: the read-only store's
    bun `--backend=copyfile` and copy import are gone (DESIGN §6 *The
    stores a view installs from are leerie's*)."""
    w = _repo(leerie, tmp_path, monkeypatch, ignore="node_modules/\n")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    rec = tmp_path / "rec.json"
    _fake(bindir, cmd[0], rec)
    monkeypatch.setenv("PATH", f"{bindir}:{os.environ['PATH']}")
    leerie._DEPS_INSTALLED.clear()
    w["st"].data["provision"] = {"recipe": [{
        "kind": "install", "working_dir": ".", "command": cmd}]}
    asyncio.run(leerie._ensure_worktree_deps(
        str(w["staging"]), w["st"], w["caps"],
        log_path=w["run_dir"] / "logs" / "b.log", verbosity="quiet"))
    assert _recorded(w["st"], rec)["argv"] == cmd[1:]


def test_a_replaced_view_takes_its_tree_stores_with_it(leerie, tmp_path,
                                                       monkeypatch):
    leerie._VIEWS.clear()
    w = _repo(leerie, tmp_path, monkeypatch)
    staging, st = w["staging"], w["st"]
    view = asyncio.run(leerie._committed_view(st, str(staging)))
    stores = view.parent.parent / "worktrees" / ".stores" / "tree-stores" / view.name
    leerie._tree_store_env({}, view)
    assert stores.is_dir()
    (stores / "uv" / "planted").write_text("x")
    _git(staging, "commit", "-qm", "next", "--allow-empty")
    asyncio.run(leerie._committed_view(st, str(staging)))
    assert not (stores / "uv" / "planted").exists()
    leerie._VIEWS.clear()


def test_trees_with_equal_install_keys_share_the_keyed_store(leerie, tmp_path):
    run, t = _tree(tmp_path)
    t2 = run / "worktrees" / "r1-view-other"
    (t2).mkdir(parents=True)
    (t2 / ".git").write_text("gitdir: x\n")
    t3 = run / "worktrees" / "r1-view-third"
    t3.mkdir(parents=True)
    (t3 / ".git").write_text("gitdir: x\n")
    assert "BUNDLE_USER_CACHE" in leerie._STORE_VARS
    try:
        for tree, key in ((t, "k1"), (t2, "k1"), (t3, "k2")):
            leerie._TREE_INSTALL_KEYS[os.path.realpath(tree)] = key
        envs = []
        for tree in (t, t2, t3):
            e: dict[str, str] = {}
            leerie._tree_store_env(e, tree)
            envs.append(e["BUNDLE_PATH"])
    finally:
        for tree in (t, t2, t3):
            leerie._TREE_INSTALL_KEYS.pop(os.path.realpath(tree), None)
    assert envs[0] == envs[1] != envs[2]


def _git_tree(run: Path, name: str, files: dict[str, str]) -> Path:
    t = run / "worktrees" / name
    t.mkdir(parents=True)
    _git(t, "init", "-q")
    for rel, text in files.items():
        (t / rel).write_text(text)
    _git(t, "add", "-A")
    _git(t, "-c", "user.name=w", "-c", "user.email=w@w", "commit", "-qm", "c")
    return t


def test_trees_with_unequal_install_keys_never_share_and_a_keyless_tree_is_its_own(
        leerie, tmp_path):
    """Equal keys share one store under `keyed/<key>`; a different key gets
    another; a tree with no recorded install gets its per-tree store, never
    a keyed one."""
    run, a = _tree(tmp_path)
    b, c = (run / "worktrees" / n for n in ("r1-view-b", "r1-view-c"))
    for d in (b, c):
        d.mkdir(parents=True)
        (d / ".git").write_text("gitdir: x\n")
    leerie._TREE_INSTALL_KEYS[os.path.realpath(a)] = "k1"
    leerie._TREE_INSTALL_KEYS[os.path.realpath(b)] = "k2"
    try:
        got = {}
        for tree in (a, b, c):
            e: dict[str, str] = {}
            leerie._tree_store_env(e, tree)
            got[tree] = e["BUNDLE_PATH"]
    finally:
        leerie._TREE_INSTALL_KEYS.clear()
    stores = run / "worktrees" / ".stores"
    assert got[a] == str(stores / "stores" / "keyed" / "k1" / "bundle")
    assert got[b] == str(stores / "stores" / "keyed" / "k2" / "bundle")
    assert got[c] == str(stores / "tree-stores" / c.name / "bundle")
    # The non-keyed per-tree stores stay per tree for every tree.
    for tree in (a, b):
        e = {}
        leerie._tree_store_env(e, tree)
        assert e["UV_CACHE_DIR"] == str(stores / "tree-stores" / tree.name / "uv")


def test_a_worker_tree_sees_the_keyed_store_of_its_install_inputs(
        leerie, tmp_path, monkeypatch):
    """A tree leerie did not install for (a subtask's worktree) is given the
    keyed store existing for its own install inputs (`_visible_keyed_stores`),
    and only when its inputs equal the recorded ones."""
    run = tmp_path / "runs" / "r1"
    files = {"Gemfile": "gem 'rack'\n"}
    installed = _git_tree(run, "r1-view-installed", files)
    same = _git_tree(run, "r1-view-same", files)
    other = _git_tree(run, "r1-view-other", {"Gemfile": "gem 'rails'\n"})
    for t in (installed, same, other):
        monkeypatch.setattr(leerie, "_tree_git_args",
                            lambda tree: ["-C", str(tree)])
    reads = ({"Gemfile"}, set())
    recipe = [{"kind": "install", "working_dir": ".",
               "command": ["bundle", "install"]}]
    head = _git(installed, "rev-parse", "HEAD")
    key = leerie._install_key(installed, head, recipe, reads)
    assert key
    store = leerie._keyed_store(run, key, "BUNDLE_PATH")
    store.mkdir(parents=True)
    monkeypatch.setattr(leerie, "_KEYED_INPUTS", {key: (recipe, reads)})
    monkeypatch.setattr(leerie, "_DEPS_INSTALLED", {})
    assert leerie._visible_keyed_stores(same) == {"BUNDLE_PATH": store}
    assert leerie._visible_keyed_stores(other) == {}        # inputs differ
    monkeypatch.setattr(leerie, "_DEPS_INSTALLED",
                        {os.path.realpath(same): True})
    assert leerie._visible_keyed_stores(same) == {}         # installed: own
    assert leerie._visible_keyed_stores(tmp_path) == {}     # no run tree


def test_a_content_key_reads_no_link_the_tree_holds(leerie, tmp_path):
    """A tree's root is confined code's to shape: a link at a declaration
    file's name, to a file outside the tree, moves no key, and a regular
    file at that name does (DESIGN §6 *Leerie never reads through a link a
    repository holds*)."""
    outside = tmp_path / "outside"
    outside.write_text("secret\n")
    t = tmp_path / "tree"
    t.mkdir()
    (t / "Gemfile").write_text("gem 'rack'\n")
    base = leerie._content_install_key(t)
    assert base
    (t / "Gemfile.lock").symlink_to(outside)
    assert leerie._content_install_key(t) == base
    outside.write_text("changed\n")
    assert leerie._content_install_key(t) == base
    (t / "Gemfile.lock").unlink()
    (t / "Gemfile.lock").write_text("secret\n")
    assert leerie._content_install_key(t) != base


def _installed_tree(leerie, tmp_path) -> tuple[Path, Path, Path]:
    run, t = _tree(tmp_path)
    src = (run / "worktrees" / ".stores" / "tree-stores" / t.name
           / leerie._STORE_VARS["BUNDLE_PATH"])
    src.mkdir(parents=True)
    (src / "gem").write_text("installed\n")
    return run, t, src


def test_an_adopted_install_moves_into_the_keyed_store(leerie, tmp_path):
    run, t, src = _installed_tree(leerie, tmp_path)
    dst = leerie._keyed_store(run, "k1", "BUNDLE_PATH")
    dst.mkdir(parents=True)                           # empty: replaced
    leerie._adopt_install_store(str(t), "k1")
    assert (dst / "gem").read_text() == "installed\n"
    assert src.is_dir() and not any(src.iterdir())    # made again, empty


def test_an_adopted_install_is_never_moved_through_a_link(leerie, tmp_path):
    """A confined install writes the run's stores: a link it left at the
    keyed store, or on the way to it, never carries the tree's install out
    of the run (DESIGN §6 *Leerie never writes through a path confined code
    shaped*)."""
    run, t, src = _installed_tree(leerie, tmp_path)
    away = tmp_path / "away"
    away.mkdir()
    dst = leerie._keyed_store(run, "k1", "BUNDLE_PATH")
    dst.parent.mkdir(parents=True)
    dst.symlink_to(away)                              # at the destination
    leerie._adopt_install_store(str(t), "k1")
    assert not any(away.iterdir())
    assert not dst.is_symlink() and (dst / "gem").is_file()

    run, t, src = _installed_tree(leerie, tmp_path / "b")
    dst = leerie._keyed_store(run, "k2", "BUNDLE_PATH")
    dst.parent.parent.mkdir(parents=True)
    dst.parent.symlink_to(away)                       # on the way
    leerie._adopt_install_store(str(t), "k2")
    assert not any(away.iterdir())
    # Leerie's own layout: the link is replaced by the directory it names.
    assert not dst.parent.is_symlink() and (dst / "gem").is_file()
