"""No store is shared across runs or repositories, and every store is
writable by the run's own commands (DESIGN §6 *The stores a view installs
from are leerie's*).

Live evidence (user's `web` run 6f147a0c on 0.44.3, which pins
`pnpm@10.28.2`): every install leerie ran failed with `EROFS … symlink …
.stores/leerie/pnpm-store/v10/projects/<hash>` — pnpm 10.27 onward registers
every project it installs in its store, and leerie's shared store was
read-only to installs. These run real pnpm, through leerie's own view
install (`_measured_tree` → `_ensure_worktree_deps`) under the real Landlock
grant, with the stores `main()` sets (`_leerie_stores`): each pinned version
installs, it writes the run's store, and an in-place edit of an installed
file in one run never reaches another run's fresh install.

The pnpm builds come from the user's corepack cache; a host without one
skips those cases (CI)."""
from __future__ import annotations

import asyncio
import io
import json
import os
import shutil
import sys
import tarfile
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(__file__))
from test_shipping_tree_is_committed import _git, _repo  # noqa: E402
from test_worker_confinement import (  # noqa: E402,F401  (fixtures)
    _St, _grants, _probe, pytestmark, run)

from tests.conftest import REAL_HOME  # noqa: E402

GOOD = "module.exports = true;\n"
# The three generations whose store handling differs: 10.4.1 (no project
# registry), 10.28.2 (registers every project in `<store>/v10/projects/`,
# the live failure), 11 (a SQLite index opened read-write).
_PNPM = ("10.4.1", "10.28.2", "11.21.0")


def _pnpm_js(version: str) -> Path | None:
    d = Path(REAL_HOME) / ".cache" / "node" / "corepack" / "v1" / "pnpm" \
        / version / "dist"
    for name in ("pnpm.cjs", "pnpm.mjs"):
        if (d / name).is_file():
            return d / name
    return None


@pytest.fixture(autouse=True)
def _no_views(leerie):
    leerie._VIEWS.clear()
    leerie._DEPS_INSTALLED.clear()
    yield
    leerie._VIEWS.clear()
    leerie._DEPS_INSTALLED.clear()


def _dep_tarball() -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, text in (("package/package.json", json.dumps(
                {"name": "dep", "version": "1.0.0", "main": "index.js"})),
                           ("package/index.js", GOOD)):
            data = text.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _world(leerie, tmp_path, monkeypatch, pnpm: Path, name: str) -> dict:
    """A run whose recipe installs a project with one dependency, with the
    run's stores on the orchestrator's environment as `main()` sets them."""
    w = _repo(leerie, tmp_path / name, monkeypatch, ignore="node_modules/\n")
    staging = w["staging"]
    (staging / "dep-1.0.0.tgz").write_bytes(_dep_tarball())
    (staging / "package.json").write_text(json.dumps(
        {"name": "p", "version": "1.0.0",
         "dependencies": {"dep": "file:./dep-1.0.0.tgz"}}))
    _git(staging, "add", "-A")
    _git(staging, "commit", "-qm", "dep")
    env: dict[str, str] = {}
    leerie._leerie_stores(env, w["run_dir"])
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("CI", raising=False)
    w["st"].data["provision"] = {"recipe": [{
        "kind": "install", "working_dir": ".",
        "command": [shutil.which("node"), str(pnpm), "install", "--offline",
                    "--no-frozen-lockfile"]}]}
    w["store"] = Path(env["npm_config_store_dir"])
    return w


def _view(leerie, w) -> Path:
    return Path(asyncio.run(leerie._measured_tree(
        w["st"], str(w["staging"]), w["caps"],
        log_path=w["run_dir"] / "logs" / "i.log", label="t")))


def _installed(view: Path) -> Path:
    return view / "node_modules" / "dep" / "index.js"


def _log(w) -> str:
    p = w["run_dir"] / "logs" / "i.log"
    return p.read_text(errors="replace") if p.exists() else ""


@pytest.mark.parametrize("version", _PNPM)
def test_each_pnpm_installs_against_the_runs_store(leerie, tmp_path,
                                                    monkeypatch, version):
    pnpm = _pnpm_js(version)
    if pnpm is None or shutil.which("node") is None:
        pytest.skip(f"pnpm {version} not in the user's corepack cache")
    w = _world(leerie, tmp_path, monkeypatch, pnpm, "r1")
    view = _view(leerie, w)
    assert _installed(view).read_text() == GOOD, _log(w)[-3000:]
    # It wrote the run's own store, which lies in the run directory.
    assert w["store"].is_relative_to(w["run_dir"] / "worktrees" / ".stores" / "stores")
    assert any(p.is_file() for p in w["store"].rglob("*")), _log(w)[-3000:]
    if version == "10.28.2":
        # The write the shared read-only store refused.
        assert any((w["store"] / "v10" / "projects").iterdir())


def test_an_edit_in_one_runs_install_never_reaches_another_runs(
        leerie, tmp_path, monkeypatch):
    """The attack the shared store answered, across runs: an in-place,
    same-size, mtime-restored edit of an installed dependency file. Within
    its run it can reach the run's store (the rule DESIGN §6 deliberately
    gives up); the next run starts with a store of its own, so its fresh
    install is clean."""
    pnpm = next((p for p in map(_pnpm_js, _PNPM) if p is not None), None)
    if pnpm is None or shutil.which("node") is None:
        pytest.skip("no pnpm in the user's corepack cache")
    w1 = _world(leerie, tmp_path, monkeypatch, pnpm, "r1")
    f = _installed(_view(leerie, w1))
    assert f.read_text() == GOOD, _log(w1)[-3000:]
    st = os.stat(f)
    bad = GOOD.replace("true", "TRUE")
    assert len(bad) == len(GOOD)
    fd = os.open(f, os.O_WRONLY)
    os.write(fd, bad.encode())
    os.close(fd)
    os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns))
    leerie._VIEWS.clear()
    leerie._DEPS_INSTALLED.clear()
    w2 = _world(leerie, tmp_path, monkeypatch, pnpm, "r2")
    assert w1["store"] != w2["store"]
    assert _installed(_view(leerie, w2)).read_text() == GOOD, \
        _log(w2)[-3000:]
    s1 = {os.stat(p).st_ino for p in w1["store"].rglob("*") if p.is_file()}
    s2 = {os.stat(p).st_ino for p in w2["store"].rglob("*") if p.is_file()}
    assert s1 and s2 and not s1 & s2


def test_every_store_variable_is_the_runs_and_its_commands_write_it(
        leerie, run):
    """One mechanism for every manager: each `_STORE_VARS` variable outside
    the per-tree class names a directory of the run's own stores, and a
    command leerie runs in the run writes it under the real grant — npm's
    `_logs` in its cache included (the live run: npm "could not write" its
    logs to the read-only store) — while a worker's grant names none."""
    env: dict[str, str] = {}
    leerie._leerie_stores(env, run["run_dir"])
    stores = run["run_dir"] / "worktrees" / ".stores" / leerie.RUN_STORES_DIRNAME
    view = run["wts"][f"{run['run_dir'].name}-view-staging"]
    g = leerie._measurement_grants(_St(run["user"], run["run_dir"]), view)
    shared = sorted(set(leerie._STORE_VARS) - leerie._TREE_STORE_VARS)
    assert shared
    lines = []
    for var in shared:
        assert Path(env[var]).parent == stores, var
        lines.append(f"{var}|mkdir -p {env[var]}/_logs && "
                     f"echo x > {env[var]}/_logs/probe")
    got = _probe(leerie, g, "\n".join(lines), dict(g.env))
    assert all(got.values()), got
    wg = _grants(leerie, run, notes_id="wt-a")
    got = _probe(leerie, wg, "\n".join(lines), run["env"])
    assert not any(got.values()), got


def test_two_runs_never_share_a_store(leerie, tmp_path):
    a, b = {}, {}
    leerie._leerie_stores(a, tmp_path / "runs" / "r1")
    leerie._leerie_stores(b, tmp_path / "runs" / "r2")
    for var in leerie._STORE_VARS:
        assert a[var] != b[var], var
        assert Path(a[var]).is_relative_to(tmp_path / "runs" / "r1"), var
