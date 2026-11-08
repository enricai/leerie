"""The stores a view installs from are leerie's (DESIGN §6 *The stores a
view installs from are leerie's*): no store is shared across runs or
repositories.

pnpm hard-links a package's files from its store into `node_modules`, so an
in-place edit of a linked file — same size, mtime put back, which pnpm's
integrity check does not look past — changes the store's own copy, and the
next install from that store links the edit (measured with real pnpm 10.4.1
on barnacle). Leerie's commands use the run's own stores
(`<run-dir>/stores/`), a worker its side's caches: such an edit reaches at
most its own run, and never another run's install. Within a run it can
reach a later install — the rule DESIGN §6 deliberately gives up.

The installer here is a stand-in that behaves like pnpm where it matters:
a content store at `npm_config_store_dir`, filled from a registry when a
package is missing, and files hard-linked from it into `node_modules`.
`tests/test_run_stores.py` runs the real pnpm."""
from __future__ import annotations

import asyncio
import json
import shutil
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(__file__))
from test_invoke_token_env import _patch_invoke_internals  # noqa: E402
from test_shipping_tree_is_committed import _git, _repo  # noqa: E402


GOOD = "export const ok = true;\n"
# A stand-in for pnpm's install: fill the store from a registry, link each
# package into node_modules (hardlink, or a separate inode under any of
# pnpm's non-link import methods), then — when POISON is set, standing in
# for a committed `postinstall` — edit the linked file in place, same size.
INSTALL = r'''
import os, shutil, sys
reg, store = sys.argv[1], os.environ["npm_config_store_dir"]
sep = False
os.makedirs(store, exist_ok=True)
for name in sorted(os.listdir(reg)):
    s = os.path.join(store, name)
    if not os.path.exists(s):
        shutil.copy(os.path.join(reg, name), s)
    d = os.path.join("node_modules", "dep", name)
    os.makedirs(os.path.dirname(d), exist_ok=True)
    if os.path.lexists(d):
        os.unlink(d)
    (shutil.copy if sep else os.link)(s, d)
if os.environ.get("POISON"):
    f = os.path.join("node_modules", "dep", "index.js")
    os.chmod(f, 0o644)
    text = open(f).read()
    open(f, "w").write(text.replace("true", "PWND"))
'''


@pytest.fixture(autouse=True)
def _no_views(leerie):
    leerie._VIEWS.clear()
    yield
    leerie._VIEWS.clear()


def _world(leerie, tmp_path, monkeypatch, *, run: str = "") -> dict:
    """A run whose recipe installs `dep` from a registry, with the run's
    stores set on the orchestrator's environment as `main()` sets them."""
    w = _repo(leerie, tmp_path / (run or "r1"), monkeypatch,
              ignore="node_modules/\n")
    reg = tmp_path / "registry"
    reg.mkdir(exist_ok=True)
    (reg / "index.js").write_text(GOOD)
    script = tmp_path / "install.py"
    script.write_text(INSTALL)
    env: dict[str, str] = {}
    leerie._leerie_stores(env, w["run_dir"])
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    w["st"].data["provision"] = {"recipe": [{
        "kind": "install", "working_dir": ".",
        "command": [sys.executable, str(script), str(reg)]}]}
    w.update(reg=reg, script=script)
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


def test_an_edit_in_one_runs_view_never_reaches_another_runs(
        leerie, tmp_path, monkeypatch):
    """A committed `postinstall` (the stand-in's POISON) edits the linked
    file in place. It reaches its own run's store — the same-run rule given
    up — and no other run: the next run's view installs a clean copy from a
    store of its own."""
    w1 = _world(leerie, tmp_path, monkeypatch, run="r1")
    w1["st"].data["provision"]["recipe"][0].setdefault(
        "env", {})["POISON"] = "1"
    assert "PWND" in _view_dep(leerie, w1)
    s1 = Path(os.environ["npm_config_store_dir"])
    assert "PWND" in (s1 / "index.js").read_text()     # its run's store
    leerie._VIEWS.clear()
    leerie._DEPS_INSTALLED.clear()
    w2 = _world(leerie, tmp_path, monkeypatch, run="r2")
    s2 = Path(os.environ["npm_config_store_dir"])
    assert s1 != s2
    assert s2.is_relative_to(w2["run_dir"])
    assert _view_dep(leerie, w2) == GOOD
    assert (s2 / "index.js").read_text() == GOOD


def test_every_store_variable_is_split(leerie, tmp_path):
    run_dir = tmp_path / "root" / "runs" / "r"
    lenv: dict[str, str] = {"GOCACHE": "/opt/go-cache"}
    leerie._leerie_stores(lenv, run_dir)
    wenv = leerie._worker_cache_env(lenv, run_dir)
    leerie_side = run_dir / "worktrees" / ".stores" / "stores"
    for var in leerie._STORE_VARS:
        if var == "GOCACHE":                    # baked into the image
            assert lenv[var] == wenv[var] == "/opt/go-cache"
            continue
        assert Path(lenv[var]).is_relative_to(leerie_side), var
        assert Path(wenv[var]).is_relative_to(run_dir / "worktrees" / ".stores" / "cache"), var
    # One set per run, every worker's: the grant is that one directory.
    cache = run_dir / "worktrees" / ".stores" / "cache"
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
    leerie._leerie_stores(env, tmp_path / "run")
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
        assert Path(wenv[var]).is_relative_to(
            tmp_path / "run" / "worktrees" / ".stores" / "cache")


_PNPM_VERSIONS = ("10.4.1", "11.21.0")


def _pnpm_get(tmp_path, version: str, env: dict, key: str) -> str:
    """`pnpm config get <key>` as pnpm `version` reads it under `env`, the
    version chosen by `packageManager` through the user's corepack cache."""
    from tests.conftest import REAL_HOME
    d = tmp_path / f"pnpm-{version}"
    d.mkdir(exist_ok=True)
    (d / "package.json").write_text(json.dumps(
        {"name": "p", "packageManager": f"pnpm@{version}"}))
    run_env = {**env, "COREPACK_ENABLE_DOWNLOAD_PROMPT": "0",
               "COREPACK_HOME": str(Path(REAL_HOME) / ".cache" / "node"
                                    / "corepack")}
    r = subprocess.run(["pnpm", "config", "get", key], cwd=str(d),
                       env=run_env, capture_output=True, text=True,
                       timeout=120)
    return r.stdout.strip()


def _pnpm_cached(version: str) -> bool:
    from tests.conftest import REAL_HOME
    return (shutil.which("pnpm") is not None and (
        Path(REAL_HOME) / ".cache" / "node" / "corepack" / "v1" / "pnpm"
        / version).is_dir())


@pytest.mark.parametrize("version", _PNPM_VERSIONS)
def test_pnpm_reads_every_store_setting_on_both_sides(leerie, tmp_path,
                                                       version):
    """Both pnpm generations read leerie's settings and a worker's
    (review round 18 exec H2): pnpm 10 only `npm_config_*`, pnpm 11 only
    `pnpm_config_*`, so each is set under both spellings."""
    if not _pnpm_cached(version):
        pytest.skip(f"pnpm {version} not in the user's corepack cache")
    run_dir = tmp_path / "state-root" / "runs" / "r1"
    lenv = dict(os.environ)
    leerie._leerie_stores(lenv, run_dir)
    wenv = leerie._worker_cache_env(lenv, run_dir)
    assert _pnpm_get(tmp_path, version, lenv, "store-dir") == \
        lenv["npm_config_store_dir"]
    assert _pnpm_get(tmp_path, version, wenv, "store-dir") == \
        wenv["npm_config_store_dir"]
    assert _pnpm_get(tmp_path, version, wenv, "store-dir") != \
        lenv["npm_config_store_dir"]
