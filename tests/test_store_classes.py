"""Which stores an install may share (DESIGN §6 *Which stores an install
may share*).

Every installer the recipe can name was measured in a container: does any
command that writes its store run committed code, and does the store refuse
an edited entry on reuse? The answers put each store variable in one of
three classes — filled only by a no-code fetch and read-only to installs,
shared because the manager verifies every entry, or one set per tree. These
tests pin the routing; the measurements are in DESIGN."""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(__file__))
from test_shipping_tree_is_committed import _git, _repo  # noqa: E402


def test_every_store_variable_has_exactly_one_class(leerie):
    classes = (leerie._TREE_STORE_VARS, leerie._VERIFIED_STORE_VARS,
               leerie._FETCHED_STORE_VARS)
    for var in leerie._STORE_VARS:
        assert sum(var in c for c in classes) == 1, var
    for c in classes:
        assert c <= set(leerie._STORE_VARS)


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
    root = run / "tree-stores" / t.name
    for var in leerie._TREE_STORE_VARS - {"GOCACHE"}:
        assert env[var] == str(root / leerie._STORE_VARS[var]), var
        assert Path(env[var]).is_dir()
    assert env["GOCACHE"] == "/opt/go-cache"            # baked: kept
    assert env["pnpm_config_cache_dir"] == env["npm_config_cache_dir"]
    other = {"UV_CACHE_DIR": "/shared/uv"}
    leerie._tree_store_env(other, tmp_path)               # no run tree
    assert other == {"UV_CACHE_DIR": "/shared/uv"}


def test_run_streaming_runs_a_repo_command_with_its_trees_stores(
        leerie, tmp_path):
    run, t = _tree(tmp_path)
    log = tmp_path / "l.log"
    out = tmp_path / "env.json"
    rc, _ = asyncio.run(leerie._run_streaming(
        [sys.executable, "-c",
         f"import json,os; json.dump(dict(os.environ), open({str(out)!r},'w'))"],
        cwd=str(t), env=None, timeout=60, log_path=log, label="t",
        verbosity="quiet"))
    assert rc == 0
    got = json.loads(out.read_text())
    assert got["UV_CACHE_DIR"] == str(run / "tree-stores" / t.name / "uv")


# Files that configure a manager to run code or fetch from elsewhere.
COMMITTED_CODE = frozenset({".pnpmfile.cjs", ".npmrc", ".yarnrc.yml",
                            ".yarnrc", "bunfig.toml", "auth.json"})


def _fake(bindir: Path, name: str, rec: Path) -> None:
    f = bindir / name
    f.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        f"json.dump({{'argv': sys.argv[1:], 'cwd': sorted(os.listdir('.')),"
        f" 'env': {{k: v for k, v in os.environ.items()"
        f" if 'SCRIPT' in k.upper()}}}}, open({str(rec)!r}, 'w'))\n")
    f.chmod(0o755)


@pytest.mark.parametrize("mgr", ["pnpm", "npm", "yarn", "bun", "go",
                                 "composer"])
def test_each_fetch_runs_its_no_code_argv_on_data_files_only(
        leerie, tmp_path, monkeypatch, mgr):
    spec = leerie._STORE_FETCH[mgr]
    w = _repo(leerie, tmp_path, monkeypatch, ignore="node_modules/\n")
    staging = w["staging"]
    for name in spec.stage:
        if name == "patches":
            (staging / name).mkdir()
            (staging / name / "x.patch").write_text("")
        else:
            (staging / name).write_text("{}\n")
    for committed_code in COMMITTED_CODE:
        (staging / committed_code).write_text("x\n")
    _git(staging, "add", "-A")
    _git(staging, "commit", "-qm", "manifests")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    rec = tmp_path / "rec.json"
    _fake(bindir, spec.argv[0], rec)
    monkeypatch.setenv("PATH", f"{bindir}:{os.environ['PATH']}")
    w["st"].data["provision"] = {"recipe": [{
        "kind": "install", "working_dir": ".",
        "command": [spec.argv[0], "install"]}]}
    asyncio.run(leerie._populate_leerie_store(
        str(staging), w["st"], w["caps"],
        log_path=w["run_dir"] / "logs" / "f.log",
        label_prefix="t", log_prefix="t"))
    got = json.loads(rec.read_text())
    assert got["argv"] == list(spec.argv[1:])
    # Independent of the table: a file that configures or runs code is never
    # staged, whatever `_STORE_FETCH` lists.
    assert not set(got["cwd"]) & COMMITTED_CODE
    assert set(got["cwd"]) == set(spec.stage) - COMMITTED_CODE
    for k, v in spec.env:
        assert got["env"].get(k) == v


def test_a_bun_install_copies_from_its_cache(leerie, tmp_path, monkeypatch):
    w = _repo(leerie, tmp_path, monkeypatch, ignore="node_modules/\n")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    rec = tmp_path / "rec.json"
    _fake(bindir, "bun", rec)
    monkeypatch.setenv("PATH", f"{bindir}:{os.environ['PATH']}")
    for cmd, want in ((["bun", "install", "--frozen-lockfile"],
                       ["install", "--frozen-lockfile", "--backend=copyfile"]),
                      (["bun", "install", "--backend=symlink"],
                       ["install", "--backend=symlink"])):
        leerie._DEPS_INSTALLED.clear()
        w["st"].data["provision"] = {"recipe": [{
            "kind": "install", "working_dir": ".", "command": cmd}]}
        asyncio.run(leerie._ensure_worktree_deps(
            str(w["staging"]), w["st"], w["caps"],
            log_path=w["run_dir"] / "logs" / "b.log", verbosity="quiet"))
        assert json.loads(rec.read_text())["argv"] == want


def test_a_replaced_view_takes_its_tree_stores_with_it(leerie, tmp_path,
                                                       monkeypatch):
    leerie._VIEWS.clear()
    w = _repo(leerie, tmp_path, monkeypatch)
    staging, st = w["staging"], w["st"]
    view = asyncio.run(leerie._committed_view(st, str(staging)))
    stores = view.parent.parent / "tree-stores" / view.name
    leerie._tree_store_env({}, view)
    assert stores.is_dir()
    (stores / "uv" / "planted").write_text("x")
    _git(staging, "commit", "-qm", "next", "--allow-empty")
    asyncio.run(leerie._committed_view(st, str(staging)))
    assert not (stores / "uv" / "planted").exists()
    leerie._VIEWS.clear()
