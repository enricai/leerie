"""A confined install writes the run's stores (DESIGN §6 *The stores a view
installs from are leerie's*, *corepack never floats*).

The grant posture the install depends on, under real Landlock: a measured
command writes the run's stores, its own tree's stores, and the
measurement's state and runtime dirs (where pnpm keeps its lock), and
nothing of another run; corepack is bounded; no package-manager setting is
added for a store's sake."""
from __future__ import annotations

import os
import sys
from pathlib import Path


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
    # The runtime directory holds sockets, so it is in the short private
    # temp directory (DESIGN §6 *A confined command's scratch is short and
    # private*), not the measurement directory.
    assert Path(env["XDG_RUNTIME_DIR"]) == leerie._private_tmp(own) / "run"
    # Both lie where the grant writes.
    for v in ("XDG_STATE_HOME", "XDG_RUNTIME_DIR"):
        assert Path(env[v]).is_dir()
        assert any(Path(env[v]).is_relative_to(p) for p in g.rw), v


def test_a_confined_install_writes_the_runs_stores_and_no_other_runs(
        leerie, run):
    view = run["wts"][f"{run['run_dir'].name}-view-staging"]
    env: dict[str, str] = {}
    leerie._leerie_stores(env, run["run_dir"])
    other = run["state_root"] / "runs" / "another-run" / "worktrees" / ".stores" / "stores" / "pnpm-store"
    other.mkdir(parents=True)
    (other / "pkg").write_text("stored\n")
    g = leerie._measurement_grants(_state(run), view, leerie.INSTALL_KIND)
    tree_store = run["run_dir"] / "worktrees" / ".stores" / "tree-stores" / view.name / "uv"
    genv = dict(g.env)
    got = _probe(leerie, g, f"""
write run store|echo x > {env['npm_config_store_dir']}/planted
write run pip cache|echo x > {env['PIP_CACHE_DIR']}/planted
write tree store|mkdir -p {tree_store} && echo x > {tree_store}/planted
write another run store|echo x > {other}/planted
read another run store|cat {other}/pkg
write state dir|echo x > {genv['XDG_STATE_HOME']}/lock
write runtime dir|echo x > {genv['XDG_RUNTIME_DIR']}/sock
""", genv)
    assert got["write run store"] and got["write run pip cache"]
    assert got["write tree store"]
    assert not got["write another run store"]
    assert not got["read another run store"]
    assert got["write state dir"] and got["write runtime dir"]


def test_corepack_is_bounded_and_never_floats(leerie):
    assert leerie._COREPACK_PIN_ENV["COREPACK_DEFAULT_TO_LATEST"] == "0"
    assert leerie._COREPACK_PIN_ENV["COREPACK_ENABLE_AUTO_PIN"] == "0"
    assert leerie._COREPACK_PIN_ENV["COREPACK_ENABLE_DOWNLOAD_PROMPT"] == "0"


def test_a_measured_pythons_user_base_is_the_homes(leerie, run):
    """pip falls back to the user site when the system one is not writable.
    That is the home's `~/.local` again (DESIGN §6 *Every confined process
    has its own home*, 4): no variable moves it, since a tree's install
    keeps it in the tree home layer. Real Landlock: Python's user base is
    the default one."""
    view = run["wts"][f"{run['run_dir'].name}-view-staging"]
    g = leerie._measurement_grants(_state(run), view)
    env = dict(os.environ)
    leerie._tree_store_env(env, view)
    env.update(dict(g.env))
    assert "PYTHONUSERBASE" not in env
    base = os.path.join(env["HOME"], ".local")
    got = _probe(leerie, g, f"""
user base is the homes|test "$(python3 -c 'import site; print(site.getuserbase())')" = {base}
""", env)
    assert got["user base is the homes"]


def test_no_measurement_carries_a_package_manager_setting(leerie, run):
    """The store is writable, so no kind sets a manager's own mode for it:
    the read-only store's `frozen-store`, `verify-deps-before-run=false` and
    copy import are gone (DESIGN §6 *pnpm's settings are spelled for the
    pnpm that reads them*)."""
    view = run["wts"][f"{run['run_dir'].name}-view-staging"]
    for kind in leerie.MEASUREMENT_KINDS:
        env = dict(leerie._measurement_grants(_state(run), view, kind).env)
        assert not [k for k in env if k.lower().startswith(
            ("npm_config_", "pnpm_config_", "uv_link"))], (kind, env)


def test_every_npm_spelled_setting_is_one_npm_or_pnpm_10_reads(leerie, run):
    """Every `npm_config_<k>` a measurement's environment carries is a key
    npm knows or one pnpm 10 reads: npm warns, once per npm, on any other
    (DESIGN §6 *pnpm's settings are spelled for the pnpm that reads
    them*). `NPM_KNOWN` and `PNPM10` are what npm 11.19.1's definitions and
    pnpm 10.4.1's/10.34.9's sources name (measured 2026-11-04)."""
    npm_known = {"cache", "script_shell"}
    pnpm10 = {"store_dir", "cache_dir"}
    view = run["wts"][f"{run['run_dir'].name}-view-staging"]
    for kind in leerie.MEASUREMENT_KINDS:
        # The orchestrator's own environment as `main()` makes it, then the
        # tree's stores and the grant's variables.
        env = {k: v for k, v in os.environ.items()
               if not k.lower().startswith(("npm_config_", "pnpm_config_"))}
        leerie._leerie_stores(env, run["run_dir"])
        leerie._tree_store_env(env, view)
        env.update(dict(leerie._measurement_grants(_state(run), view,
                                                   kind).env))
        keys = {k[len("npm_config_"):] for k in env
                if k.startswith("npm_config_")}
        assert keys <= npm_known | pnpm10, (kind, keys - npm_known - pnpm10)


def test_every_store_a_tree_installs_from_is_beneath_the_link_root(
        leerie, run):
    """DESIGN §6 *The write grants on one mount are one mount*: the grant's
    link root holds every store a tree installs from — the run's, the
    tree's own and both sides' worker caches — and none of the run's own
    records, so the one writable mount the shim makes there links a tree
    from its store and leaves `state.json` and the logs read-only."""
    view = run["wts"][f"{run['run_dir'].name}-view-staging"]
    g = leerie._measurement_grants(_state(run), view, leerie.INSTALL_KIND)
    link = Path(g.link)
    assert link == (run["run_dir"] / "worktrees").resolve()
    env: dict[str, str] = {}
    leerie._leerie_stores(env, run["run_dir"])
    leerie._tree_store_env(env, view)
    wenv = leerie._worker_cache_env({}, run["run_dir"])
    held = leerie._worker_cache_dir(run["run_dir"], "acceptance_writer")
    stores = ([env[v] for v in ("npm_config_store_dir", "UV_CACHE_DIR",
                                "PIP_CACHE_DIR")]
              + [wenv["npm_config_store_dir"], str(held)])
    for p in stores:
        assert Path(os.path.realpath(p)).is_relative_to(link), p
    for name in ("state.json", "logs", "run.json", "claude-config",
                 "workers", "acceptance"):
        assert not (run["run_dir"].resolve() / name).is_relative_to(link)
    # Every grant beneath the link root is a tree or a store of one.
    beneath = [p for p in g.rw if Path(p).is_relative_to(link)]
    assert beneath and all(
        Path(p) == view.resolve()
        or Path(p).is_relative_to(link / ".stores") for p in beneath), beneath
    wg = leerie._worker_grants(
        run_dir=run["run_dir"], state={}, schema_key="satisfied_probe",
        cwd=view, allowed_tools="Bash", env={})
    assert Path(wg.link) == link
    assert str(leerie._worker_cache_dir(run["run_dir"], "satisfied_probe")
               .resolve()) in [os.path.realpath(p) for p in wg.rw]
