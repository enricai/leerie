"""The stores a view installs from are leerie's (DESIGN §6 *The stores a
view installs from are leerie's*).

pnpm hard-links a package's files from its store into `node_modules`. One
store was shared by leerie's installs and every worker's, across runs, so a
worker that edited a hard-linked file in place — same size, mtime put back,
which pnpm's integrity check does not look past — changed the store's own
copy, and the next committed view installed the edit (measured with real
pnpm 10.4.1 on barnacle). Leerie's commands now read only
`<state-root>/.stores/leerie/`, a worker only its own caches and its run's
worker pnpm store, and a tree leerie installs into gets links into leerie's
store only when it is a committed view.

The installer here is a stand-in that behaves like pnpm where it matters:
a content store at `npm_config_store_dir`, filled from a registry when a
package is missing, and files hard-linked from it into `node_modules`
unless `npm_config_package_import_method=copy`."""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(__file__))
from test_invoke_token_env import _patch_invoke_internals  # noqa: E402
from test_shipping_tree_is_committed import _git, _repo  # noqa: E402

GOOD = "export const ok = true;\n"
INSTALL = r'''
import os, shutil, sys
reg, store = sys.argv[1], os.environ["npm_config_store_dir"]
copy = os.environ.get("npm_config_package_import_method") == "copy"
os.makedirs(store, exist_ok=True)
for name in sorted(os.listdir(reg)):
    s = os.path.join(store, name)
    if not os.path.exists(s):
        shutil.copy(os.path.join(reg, name), s)
    d = os.path.join("node_modules", "dep", name)
    os.makedirs(os.path.dirname(d), exist_ok=True)
    if os.path.lexists(d):
        os.unlink(d)
    (shutil.copy if copy else os.link)(s, d)
'''


@pytest.fixture(autouse=True)
def _no_views(leerie):
    leerie._VIEWS.clear()
    yield
    leerie._VIEWS.clear()


def _world(leerie, tmp_path, monkeypatch, *, run: str = "") -> dict:
    """A run whose recipe installs `dep` from a registry, with leerie's
    stores set on the orchestrator's environment as `main()` sets them."""
    w = _repo(leerie, tmp_path / (run or "r1"), monkeypatch,
              ignore="node_modules/\n")
    reg = tmp_path / "registry"
    reg.mkdir(exist_ok=True)
    (reg / "index.js").write_text(GOOD)
    script = tmp_path / "install.py"
    script.write_text(INSTALL)
    state_root = tmp_path / "state-root"
    env: dict[str, str] = {}
    leerie._leerie_stores(env, state_root)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    w["st"].data["provision"] = {"recipe": [{
        "kind": "install", "working_dir": ".",
        "command": [sys.executable, str(script), str(reg)]}]}
    w.update(reg=reg, script=script, state_root=state_root)
    return w


def _worker_installs_and_poisons(leerie, w, sid: str) -> Path:
    """The worker's own install in its tree, then the attack: the
    hard-linked file edited in place, same size, mtime restored."""
    staging = w["staging"]
    env = leerie._worker_cache_env(os.environ, w["run_dir"])
    subprocess.run([sys.executable, str(w["script"]), str(w["reg"])],
                   cwd=staging, env=env, check=True)
    f = staging / "node_modules" / "dep" / "index.js"
    assert os.stat(f).st_nlink == 2           # linked from its store
    st = os.stat(f)
    bad = GOOD.replace("true", "TRUE")
    assert len(bad) == len(GOOD)
    fd = os.open(f, os.O_WRONLY)
    os.write(fd, bad.encode())
    os.close(fd)
    os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns))
    return Path(env["npm_config_store_dir"])


def _view_dep(leerie, w) -> str:
    tree = asyncio.run(leerie._measured_tree(
        w["st"], str(w["staging"]), w["caps"],
        log_path=w["run_dir"] / "logs" / "i.log", label="t"))
    return (Path(tree) / "node_modules" / "dep" / "index.js").read_text()


def test_a_poisoned_worker_store_does_not_reach_a_view(
        leerie, tmp_path, monkeypatch):
    w = _world(leerie, tmp_path, monkeypatch)
    wstore = _worker_installs_and_poisons(leerie, w, "feat-001")
    assert "TRUE" in (wstore / "index.js").read_text()     # store poisoned
    assert _view_dep(leerie, w) == GOOD
    lstore = Path(os.environ["npm_config_store_dir"])
    assert (lstore / "index.js").read_text() == GOOD
    assert not wstore.is_relative_to(lstore.parent)


def test_nor_a_view_of_a_later_run(leerie, tmp_path, monkeypatch):
    """Leerie's store outlives the run; nothing a worker of any run wrote is
    in it, and each run's workers start a store of their own."""
    w1 = _world(leerie, tmp_path, monkeypatch, run="r1")
    s1 = _worker_installs_and_poisons(leerie, w1, "feat-001")
    assert _view_dep(leerie, w1) == GOOD
    leerie._VIEWS.clear()
    w2 = _world(leerie, tmp_path, monkeypatch, run="r2")
    s2 = _worker_installs_and_poisons(leerie, w2, "feat-001")
    assert s1 != s2
    assert _view_dep(leerie, w2) == GOOD


def test_only_a_view_links_into_leeries_store(leerie, tmp_path,
                                              monkeypatch):
    """A tree a worker may run in (here staging, installed by leerie) gets
    copies, so no file in it is the store's."""
    w = _world(leerie, tmp_path, monkeypatch)
    asyncio.run(leerie._ensure_worktree_deps(
        str(w["staging"]), w["st"], w["caps"],
        log_path=w["run_dir"] / "logs" / "s.log", verbosity="quiet"))
    f = w["staging"] / "node_modules" / "dep" / "index.js"
    assert os.stat(f).st_nlink == 1
    view = Path(asyncio.run(leerie._measured_tree(
        w["st"], str(w["staging"]), w["caps"],
        log_path=w["run_dir"] / "logs" / "i.log", label="t")))
    store = Path(os.environ["npm_config_store_dir"]) / "index.js"
    assert os.stat(view / "node_modules/dep/index.js").st_ino == \
        os.stat(store).st_ino


def test_every_store_variable_is_split(leerie, tmp_path):
    root, run_dir = tmp_path / "root", tmp_path / "root" / "runs" / "r"
    lenv: dict[str, str] = {"GOCACHE": "/opt/go-cache"}
    leerie._leerie_stores(lenv, root)
    wenv = leerie._worker_cache_env(lenv, run_dir)
    leerie_side = root / ".stores" / "leerie"
    for var in leerie._STORE_VARS:
        if var == "GOCACHE":                    # baked into the image
            assert lenv[var] == wenv[var] == "/opt/go-cache"
            continue
        assert Path(lenv[var]).is_relative_to(leerie_side), var
        assert Path(wenv[var]).is_relative_to(run_dir / "workers"), var
    # One set per run, every worker's: the grant is that one directory.
    cache = run_dir / "workers" / "cache"
    assert Path(wenv["XDG_CACHE_HOME"]).is_relative_to(cache)
    for var in leerie._STORE_VARS:
        if var != "GOCACHE":
            assert Path(wenv[var]).is_relative_to(cache), var
    assert wenv["npm_config_store_dir"] == str(cache / "pnpm-store")


def test_invoke_gives_the_worker_its_own_stores(leerie, tmp_path,
                                                monkeypatch):
    captured: list = []
    _patch_invoke_internals(leerie, monkeypatch, captured)
    env: dict[str, str] = {}
    leerie._leerie_stores(env, tmp_path / "root")
    for k, v in env.items():
        monkeypatch.setenv(k, v)

    async def run():
        try:
            await leerie._invoke(["claude", "-p"], cwd=str(tmp_path),
                                 timeout=60, sid="feat-001",
                                 leerie_dir=tmp_path / "run",
                                 verbosity="quiet",
                                 grants=leerie.WorkerGrants(("/",), ()))
        except Exception:
            pass
    asyncio.run(run())
    wenv = captured[0]
    for var in leerie._STORE_VARS:
        assert wenv[var] != env[var], var
        assert Path(wenv[var]).is_relative_to(tmp_path / "run" / "workers")
