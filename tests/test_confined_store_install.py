"""A confined install reads leerie's fetched store and pins pnpm's version
(DESIGN §6 *A measurement pins pnpm's version*).

The grant posture the install depends on, under real Landlock: a measured
command reads a fetched store but cannot write it, writes a verified store
and its own tree's stores, and writes the measurement's state and runtime
dirs (where pnpm keeps its lock and db). Plus the version pin: corepack is
bounded, and leerie seeds a known-good pnpm only for a tree that pins
none."""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(__file__))
from test_worker_confinement import (  # noqa: F401,E402  (fixtures)
    _St, _probe, pytestmark, run)


def _state(run) -> "_St":
    return _St(run["user"], run["run_dir"])


def test_the_measure_env_gives_pnpm_writable_state_and_runtime(leerie, run):
    view = run["wts"][f"{run['run_dir'].name}-view-staging"]
    g = leerie._measurement_grants(_state(run), view)
    own = leerie._measure_dir(_state(run), view)
    env = dict(g.env)
    assert Path(env["XDG_STATE_HOME"]) == own / "state"
    assert Path(env["XDG_RUNTIME_DIR"]) == own / "run"
    # Both lie in the measurement dir, which the grant writes.
    for v in ("XDG_STATE_HOME", "XDG_RUNTIME_DIR"):
        assert Path(env[v]).is_relative_to(own)
        assert str(own) in g.rw or any(
            Path(env[v]).is_relative_to(p) for p in g.rw)


def test_a_confined_install_reads_the_store_but_cannot_write_it(
        leerie, run, monkeypatch, tmp_path):
    view = run["wts"][f"{run['run_dir'].name}-view-staging"]
    stores = run["state_root"] / ".stores" / "leerie"
    (stores / "pnpm-store").mkdir(parents=True)
    (stores / "pnpm-store" / "pkg").write_text("stored\n")
    verified = tmp_path / "verified-pip"
    verified.mkdir()
    monkeypatch.setenv("PIP_CACHE_DIR", str(verified))
    g = leerie._measurement_grants(_state(run), view)
    tree_store = run["run_dir"] / "tree-stores" / view.name / "uv"
    env = dict(g.env)
    got = _probe(leerie, g, f"""
read fetched store|cat {stores}/pnpm-store/pkg
write fetched store|echo x > {stores}/pnpm-store/planted
write verified store|echo x > {verified}/planted
write tree store|mkdir -p {tree_store} && echo x > {tree_store}/planted
write state dir|echo x > {env['XDG_STATE_HOME']}/lock
write runtime dir|echo x > {env['XDG_RUNTIME_DIR']}/sock
""", env)
    assert got["read fetched store"]
    assert not got["write fetched store"]        # read-only: the store's safety
    assert got["write verified store"]
    assert got["write tree store"]
    assert got["write state dir"] and got["write runtime dir"]


def test_corepack_is_bounded_and_never_floats(leerie):
    assert leerie._COREPACK_PIN_ENV["COREPACK_DEFAULT_TO_LATEST"] == "0"
    assert leerie._COREPACK_PIN_ENV["COREPACK_ENABLE_AUTO_PIN"] == "0"
    assert leerie._COREPACK_PIN_ENV["COREPACK_ENABLE_DOWNLOAD_PROMPT"] == "0"
    # Never "latest": a real version that reads the current lockfiles.
    assert leerie._PNPM_DEFAULT_PIN[0].isdigit()


def test_reads_package_manager(leerie, tmp_path):
    (tmp_path / "with").mkdir()
    (tmp_path / "with" / "package.json").write_text(
        json.dumps({"packageManager": "pnpm@10.4.1"}))
    (tmp_path / "without").mkdir()
    (tmp_path / "without" / "package.json").write_text(json.dumps({}))
    assert leerie._reads_package_manager(tmp_path, "with")
    assert not leerie._reads_package_manager(tmp_path, "without")
    assert not leerie._reads_package_manager(tmp_path, "missing")


def _repo_with_recipe(leerie, tmp_path, monkeypatch, *, package_manager):
    from test_shipping_tree_is_committed import _git, _repo
    w = _repo(leerie, tmp_path, monkeypatch, ignore="node_modules/\n")
    staging = w["staging"]
    (staging / "pnpm-lock.yaml").write_text("lockfileVersion: '9.0'\n")
    pj = {"name": "p"}
    if package_manager:
        pj["packageManager"] = "pnpm@10.4.1"
    (staging / "package.json").write_text(json.dumps(pj))
    _git(staging, "add", "-A")
    _git(staging, "commit", "-qm", "lock")
    w["st"].data["provision"] = {"recipe": [{
        "kind": "install", "working_dir": ".", "command": ["pnpm", "fetch"]}]}
    return w


@pytest.mark.parametrize("package_manager,seeds", [(False, True),
                                                   (True, False)])
def test_the_pin_is_seeded_only_when_the_tree_pins_none(
        leerie, tmp_path, monkeypatch, package_manager, seeds):
    w = _repo_with_recipe(leerie, tmp_path, monkeypatch,
                          package_manager=package_manager)
    calls: list = []

    async def fake_seed(st, stage, log_path, log_prefix):
        calls.append(True)

    async def fake_stream(*a, **k):
        return (0, "")
    monkeypatch.setattr(leerie, "_seed_pnpm_pin", fake_seed)
    monkeypatch.setattr(leerie, "_run_streaming", fake_stream)
    asyncio.run(leerie._populate_leerie_store(
        str(w["staging"]), w["st"], w["caps"],
        log_path=w["run_dir"] / "logs" / "f.log",
        label_prefix="t", log_prefix="t"))
    assert bool(calls) is seeds
