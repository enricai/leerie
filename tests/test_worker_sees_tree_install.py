"""A worker started in a tree leerie installed for sees that install
(DESIGN §6 *A worker started in a tree leerie installed for runs on that
tree's stores*; review round 23 exec M1).

On a pip repository leerie's install of each falsifier lane put pytest in
that lane's own user base (`<run>/tree-stores/<lane>/pyuser`), while the
falsifier ran with `PYTHONUSERBASE` at its side's worker cache: 6 of 6
first samples answered "pytest isn't installed". The stores leerie's install
of a tree writes and the stores a worker in that tree reads were chosen by
two functions that never agreed on a tree.

The rule: for a tree leerie installed for, the environment `_invoke` gives a
worker there and the one `_run_streaming` gives leerie's commands there name
the same per-tree store for every `_TREE_STORE_VARS` variable, the grant
names the stores as it names the tree, and the worker's home has the tree
home layer on top (DESIGN §6 *Every confined process has its own home*, 4:
no variable moves a user base since). Then, executed where isolation works:
a worker's confined process runs by name a script an install put in
`~/.local/bin`, and a real
`pip install` of pytest followed by the worker running `pytest` and
`import pytest` (container, where isolation works)."""
from __future__ import annotations

import asyncio
import os
import socket
import subprocess
from pathlib import Path

import pytest

from tests.conftest import needs_landlock, skip_without_landlock

from tests.test_confined_network import needs_isolation, proxy  # noqa: F401
from tests.test_invoke_token_env import _patch_invoke_internals
from tests.test_worker_confinement import _St, run  # noqa: F401



@pytest.fixture()
def stores_env(leerie, run, monkeypatch):
    """Leerie's stores on the orchestrator's environment, as `main()` sets
    them, with no inherited user base."""
    monkeypatch.delenv("PYTHONUSERBASE", raising=False)
    env: dict[str, str] = {"PATH": os.environ.get("PATH", "/usr/bin:/bin")}
    leerie._leerie_stores(env, run["run_dir"])
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return env


@pytest.fixture()
def installed(leerie, monkeypatch):
    """Mark trees as installed for in this process (`_DEPS_INSTALLED`),
    undone at teardown."""
    marked: list[str] = []

    def mark(tree: Path) -> None:
        key = os.path.realpath(tree)
        leerie._DEPS_INSTALLED.add(key)
        marked.append(key)
    yield mark
    for key in marked:
        leerie._DEPS_INSTALLED.discard(key)


def _grants(leerie, run, tree: Path, schema_key: str, tools: str):
    return leerie._worker_grants(
        run_dir=run["run_dir"], state={}, schema_key=schema_key, cwd=tree,
        allowed_tools=tools, env=run["env"])


def _worker_env(leerie, run, tree: Path, grants) -> dict:
    """The environment `_invoke` actually spawns the worker's CLI with."""
    captured: list = []
    with pytest.MonkeyPatch.context() as mp:
        _patch_invoke_internals(leerie, mp, captured)
        _spawn(leerie, run, tree, grants)
    assert captured and captured[0] is not None
    return dict(captured[0])


def _spawn(leerie, run, tree: Path, grants) -> None:

    async def go():
        try:
            await leerie._invoke(["claude", "-p"], cwd=str(tree), timeout=60,
                                 sid="falsifier-i0-s0",
                                 leerie_dir=run["run_dir"], verbosity="quiet",
                                 grants=grants)
        except Exception:
            # The fake process ends with no result event; only the env the
            # spawn was given matters here.
            pass
    asyncio.run(go())


def _lane(run) -> Path:
    return run["wts"]["wt-a"]


# --- the rule ----------------------------------------------------------------


@pytest.mark.parametrize("schema_key,tools,access", [
    ("falsifier", "ACT_TOOLS", "rw"),
    ("acceptance_writer", "ACT_TOOLS", "rw"),
    ("surrogate_fixer", "ACT_TOOLS", "rw"),
    ("set_auditor", "SATISFIED_PROBE_TOOLS", "ro"),
])
def test_worker_and_install_name_the_same_tree_stores(
        leerie, run, stores_env, installed, monkeypatch, schema_key, tools,
        access):
    lane = _lane(run)
    installed(lane)
    stores = run["run_dir"] / "worktrees" / ".stores" / "tree-stores" / lane.name
    g = _grants(leerie, run, lane, schema_key, getattr(leerie, tools))
    real = os.path.realpath(stores)
    assert real in {os.path.realpath(p) for p in getattr(g, access)}
    other = "ro" if access == "rw" else "rw"
    assert real not in {os.path.realpath(p) for p in getattr(g, other)}

    wenv = _worker_env(leerie, run, lane, g)
    install_env = leerie._tree_stores_for(os.environ, lane)
    cache = leerie._granted_cache_dir(g, run["run_dir"])
    for var in leerie._TREE_STORE_VARS:
        # The install's value, and not the worker cache's: the two sources
        # disagree by construction.
        assert wenv[var] == install_env[var], var
        assert Path(wenv[var]).is_relative_to(stores), var
        assert not Path(wenv[var]).is_relative_to(cache), var
    # Every other store stays the worker's side cache, never leerie's.
    for var in (set(leerie._STORE_VARS) - leerie._TREE_STORE_VARS):
        assert Path(wenv[var]).is_relative_to(cache), var
    # No variable moves a user base (DESIGN §6 *Every confined process has
    # its own home*, 4): what the install kept beneath `~` is the tree home
    # layer, which the worker's own home has on top once it exists.
    assert "PYTHONUSERBASE" not in wenv
    layer = stores / leerie.TREE_HOME_NAME
    assert g.home_layers[:1] != (str(layer),)
    layer.mkdir(parents=True, exist_ok=True)
    g2 = _grants(leerie, run, lane, schema_key, getattr(leerie, tools))
    if g2.home:
        assert g2.home_layers[0] == str(layer)


def test_a_tree_leerie_never_installed_for_keeps_the_worker_cache(
        leerie, run, stores_env, monkeypatch):
    """A subtask's tree, which leerie measures only through its committed
    view: the worker's own installs land in its side's cache."""
    lane = _lane(run)
    assert os.path.realpath(lane) not in leerie._DEPS_INSTALLED
    g = _grants(leerie, run, lane, "implementer", leerie.ACT_TOOLS)
    stores = os.path.realpath(run["run_dir"] / "worktrees" / ".stores" / "tree-stores" / lane.name)
    assert stores not in {os.path.realpath(p) for p in (*g.rw, *g.ro)}
    wenv = _worker_env(leerie, run, lane, g)
    cache = leerie._granted_cache_dir(g, run["run_dir"])
    for var in leerie._TREE_STORE_VARS:
        assert Path(wenv[var]).is_relative_to(cache), var


# --- executed under the worker's grant -----------------------------------------


@needs_isolation
def test_a_worker_runs_a_script_leeries_install_put_in_the_tree(
        leerie, run, stores_env, installed, monkeypatch):
    """Leerie's install-kind command writes a console script where pip's
    user scheme puts one, `~/.local/bin`, which is the tree home layer
    (DESIGN §6 *Every confined process has its own home*, 4); the
    falsifier's own confined process, with the environment `_invoke` gives
    it, runs it by name in that tree, and the real home holds nothing."""
    monkeypatch.setenv(leerie.ISOLATE_ENV, "1")
    lane = _lane(run)
    st = _St(run["user"], run["run_dir"])
    log = run["run_dir"] / "logs" / "tree-install.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    rc, tail = asyncio.run(leerie._run_streaming(
        ["bash", "-c", 'mkdir -p "$HOME/.local/bin" && printf '
         '"#!/bin/sh\\necho SCRIPT-RAN\\n" > "$HOME/.local/bin/'
         'leerie-fake-runner" && chmod +x "$HOME/.local/bin/'
         'leerie-fake-runner"'],
        grants=leerie._measurement_grants(st, lane, leerie.INSTALL_KIND),
        cwd=str(lane), timeout=60, log_path=log, label="install",
        verbosity="quiet"))
    assert rc == 0, tail
    assert not (Path(os.environ["HOME"]) / ".local" / "bin"
                / "leerie-fake-runner").exists()
    installed(lane)
    g = _grants(leerie, run, lane, "falsifier", leerie.ACT_TOOLS)
    wenv = _worker_env(leerie, run, lane, g)
    wenv["PATH"] = f"{wenv['PATH']}:{os.environ['HOME']}/.local/bin"
    r = subprocess.run(leerie._confined_argv(g, ["leerie-fake-runner"]),
                       cwd=str(lane), env=wenv, capture_output=True,
                       text=True, timeout=60)
    assert r.returncode == 0 and "SCRIPT-RAN" in r.stdout, r.stderr


# --- a real pip install, where isolation works ---------------------------------


def _online() -> bool:
    try:
        socket.getaddrinfo("pypi.org", 443)
        return True
    except OSError:
        return False


@needs_isolation
def test_a_falsifier_runs_the_pytest_leeries_pip_install_put_in_its_lane(
        leerie, run, stores_env, proxy, monkeypatch):  # noqa: F811
    """The review's shape: a real `_ensure_worktree_deps` on a pip tree,
    pip defaulting to the tree's user base, then the falsifier's confined
    process running `pytest` by name and `import pytest`."""
    if not _online():
        pytest.skip("the runtime itself has no network to reach PyPI with")
    bare = {k: v for k, v in os.environ.items()
            if k not in ("PYTHONPATH", "PYTHONUSERBASE")}
    if subprocess.run(["python3", "-c", "import pytest"], env=bare,
                      capture_output=True).returncode == 0:
        pytest.skip("pytest is importable system-wide here: the probe "
                    "could not tell the tree's install from the image's")
    monkeypatch.setenv(leerie.ISOLATE_ENV, "1")
    # The test runner's own import path is no part of a run's environment:
    # pip would count what it finds there as installed.
    monkeypatch.delenv("PYTHONPATH", raising=False)
    lane = _lane(run)
    st = _St(run["user"], run["run_dir"])
    st.data["provision"] = {"recipe": [
        {"kind": "install",
         "command": ["python3", "-m", "pip", "--disable-pip-version-check",
                     "install", "--break-system-packages", "pytest==8.3.4"]}]}
    log = run["run_dir"] / "logs" / "lane-install.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    leerie._DEPS_INSTALLED.discard(os.path.realpath(lane))
    try:
        ok = asyncio.run(leerie._ensure_worktree_deps(
            str(lane), st, dict(leerie.DEFAULT_CAPS), log_path=log,
            verbosity="quiet"))
        text = log.read_text(errors="replace")
        assert ok is True, text
        g = _grants(leerie, run, lane, "falsifier", leerie.ACT_TOOLS)
        wenv = _worker_env(leerie, run, lane, g)
        for cmd in (["pytest", "--version"],
                    ["python3", "-c", "import pytest; print(pytest.__file__)"]):
            r = subprocess.run(leerie._confined_argv(g, cmd), cwd=str(lane),
                               env=wenv, capture_output=True, text=True,
                               timeout=120)
            assert r.returncode == 0, (cmd, r.stdout, r.stderr)
        assert str(run["run_dir"] / "worktrees" / ".stores" / "tree-stores" / lane.name) in r.stdout
    finally:
        leerie._DEPS_INSTALLED.discard(os.path.realpath(lane))
