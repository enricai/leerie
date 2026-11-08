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
    assert leerie._TREE_STORE_VARS <= set(leerie._STORE_VARS)


def _tree(tmp_path) -> tuple[Path, Path]:
    run = tmp_path / "runs" / "r1"
    t = run / "worktrees" / "r1-view-staging"
    (t / "sub").mkdir(parents=True)
    (t / ".git").write_text("gitdir: x\n")
    return run, t


def test_a_command_in_a_run_tree_gets_that_trees_own_stores(leerie,
                                                            tmp_path):
    run, t = _tree(tmp_path)
    env = {"GOCACHE": "/opt/go-cache", "UV_CACHE_DIR": "/shared/uv"}
    leerie._tree_store_env(env, t / "sub")
    root = run / "worktrees" / ".stores" / "tree-stores" / t.name
    for var in leerie._TREE_STORE_VARS - {"GOCACHE"}:
        assert env[var] == str(root / leerie._STORE_VARS[var]), var
        assert Path(env[var]).is_dir()
    assert env["GOCACHE"] == "/opt/go-cache"            # baked: kept
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
            *(Path(env[v]) for v in leerie._TREE_STORE_VARS)]
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
