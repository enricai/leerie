"""A tree's stores are the tree's, whoever writes it (DESIGN §6 *A tree's
stores are the tree's, whoever writes it*).

Navegando b94226a8 (0.71.0), `staging`: the final conformer ran `pnpm
install --frozen-lockfile` there on the fixers' store, and leerie's install
of the browser suite's prerequisites before a re-drive group
(`_ci_prepare(staging, ..., "redrive")`) ran four hours later on the run's;
all three attempts ended `ERR_PNPM_ABORTED_REMOVE_MODULES_DIR_NO_TTY`. The
writer chose the stores, not the tree: a worker's spawn put every store
variable at its side's cache, leerie's commands at the run's stores.

The two-writer tests are the lane's fixture made a repository test: two
writers install into one tree a worker writes (staging, a tree of its own
repository), the worker with the environment `_invoke` really spawns it
with and under its real grant, leerie through `_ensure_worktree_deps` (the
first step of `_ci_prepare`) under its real measurement grant, in both
orders, with pnpm (which records its store in the tree's install output)
and with npm (which does not). Both installs must succeed, and every store
variable must name the same directory, the side's cache. On the code before
this change pnpm's second install refused and npm's environments differed.

They need Landlock (ABI 3; CI's kernel has it) and node with npm. pnpm
10.4.1 comes from the user's corepack cache, else from the registry
through npm, as CI's own typescript step fetches, copied into staging.
Its departures from a run are each the same for both writers and none a
store variable: no isolation (this host and CI cannot unshare a user
namespace; `tests/test_confined_network.py`'s `needs_isolation`); pnpm
run as `node pnpm.cjs` from that copy rather than through corepack, and
both installers with `--offline` (pnpm also `--no-frozen-lockfile`); `CI`
unset; and the installers' update check off
(`npm_config_update_notifier=false`), which with no route to the registry
outlived the install (900 s, measured in the lane's fixture)."""
from __future__ import annotations

import ast
import asyncio
import inspect
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.test_run_stores import GOOD, _dep_tarball
from tests.test_worker_confinement import (  # noqa: F401  (fixtures)
    _St, _grants, pytestmark, run)
from tests.test_worker_sees_tree_install import _worker_env

from tests.conftest import REAL_HOME

PNPM_VERSION = "10.4.1"


@pytest.fixture(autouse=True)
def _fresh_records(leerie):
    leerie._DEPS_INSTALLED.clear()
    leerie._CLONE_CACHES.clear()
    yield
    leerie._DEPS_INSTALLED.clear()
    leerie._CLONE_CACHES.clear()


@pytest.fixture()
def stores_env(leerie, run, monkeypatch):
    """The run's stores on the orchestrator's environment, as `main()` sets
    them."""
    env: dict[str, str] = {}
    leerie._leerie_stores(env, run["run_dir"])
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return env


def _side(leerie, run, name: str) -> Path:
    return leerie._stores_home(run["run_dir"]) / name


def _clone(leerie, run, name: str, writer: str | None) -> Path:
    st = _St(run["user"], run["run_dir"])
    head = subprocess.run(["git", "-C", str(run["user"]), "rev-parse",
                           "HEAD"], capture_output=True, text=True,
                          check=True).stdout.strip()
    wt = leerie._helper_worktree_path(st, name)
    assert leerie._acceptance_writer_checkout(st, wt, head,
                                              writer=writer) == "clone"
    return wt


# --- which side a tree is ------------------------------------------------------


def test_a_tree_of_its_own_repository_is_the_fixers(leerie, run):
    for name in ("staging", "wt-a"):
        assert leerie._tree_cache(run["wts"][name]) == _side(
            leerie, run, leerie.WORKER_CACHE_DIR)
        # A path inside the tree is the tree's.
        assert leerie._tree_cache(run["wts"][name] / "sub") == _side(
            leerie, run, leerie.WORKER_CACHE_DIR)


def test_a_tree_no_worker_writes_is_leeries(leerie, run, tmp_path):
    from tests.test_worker_confinement import RUN_ID
    assert leerie._tree_cache(run["wts"][f"{RUN_ID}-view-staging"]) is None
    assert leerie._tree_cache(tmp_path) is None
    assert leerie._tree_cache(None) is None


@pytest.mark.parametrize("writer,cache", [
    ("falsifier", "HELD_OUT_CACHE_DIR"),
    ("acceptance_writer", "HELD_OUT_CACHE_DIR"),
    ("surrogate_fixer", "WORKER_CACHE_DIR"),
])
def test_a_clone_made_for_a_worker_is_that_workers_side(leerie, run, writer,
                                                        cache):
    wt = _clone(leerie, run, f"clone-{writer}", writer)
    want = _side(leerie, run, getattr(leerie, cache))
    assert want == leerie._worker_cache_dir(run["run_dir"], writer)
    assert leerie._tree_cache(wt) == want
    # Removed, its record goes with it: a tree made again at that path for
    # leerie alone is leerie's.
    leerie._remove_acceptance_checkout(_St(run["user"], run["run_dir"]), wt)
    assert leerie._tree_cache(wt) is None
    again = _clone(leerie, run, f"clone-{writer}", None)
    assert again == wt and leerie._tree_cache(wt) is None


def test_a_worker_cannot_make_its_tree_leeries(leerie, run, stores_env):
    """The side comes from leerie's record and layout, never from what the
    tree's worker writes: its `.git` removed, a `.git` planted deeper down,
    its tree repository's contents emptied."""
    fixers = _side(leerie, run, leerie.WORKER_CACHE_DIR)
    staging = run["wts"]["staging"]
    lane = _clone(leerie, run, "falsifier-lane0", "falsifier")
    held = _side(leerie, run, leerie.HELD_OUT_CACHE_DIR)
    deep = staging / "pkg" / "worktrees" / "x"
    (deep / ".git").mkdir(parents=True)
    assert leerie._tree_cache(deep) == fixers
    assert leerie._tree_stores_dir(deep)[0] == staging
    # A worker's tree, and what leerie measures from it: a planted
    # `worktrees`, `tree-repos` or `repo` beneath a path a command may run in
    # (a recipe's `working_dir`) moves nothing.
    from tests.test_worker_confinement import RUN_ID
    view = run["wts"][f"{RUN_ID}-view-staging"]
    for tree in (staging, view):
        a = tree / "tools"
        (a / "worktrees" / "cli").mkdir(parents=True)
        (a / "tree-repos" / "cli").mkdir(parents=True)
        (a / "repo" / "objects").mkdir(parents=True)
        (a / "repo" / "HEAD").write_text("ref: refs/heads/main\n")
        cwd = a / "worktrees" / "cli"
        assert leerie._tree_stores_dir(cwd)[0] == tree
        env = leerie._tree_stores_for(os.environ, cwd)
        for var in leerie._STORE_VARS:
            assert not Path(env[var]).is_relative_to(tree), var
    assert leerie._tree_cache(view / "tools" / "worktrees" / "cli") is None
    for tree in (staging, lane):
        git = tree / ".git"
        if git.is_dir() and not git.is_symlink():
            shutil.rmtree(git)
        else:
            git.unlink()
    tr = run["run_dir"] / "tree-repos" / "staging"
    for child in tr.iterdir():
        shutil.rmtree(child) if child.is_dir() else child.unlink()
    assert leerie._tree_cache(staging) == fixers
    assert leerie._tree_cache(lane) == held
    env = leerie._tree_stores_for(os.environ, staging)
    assert Path(env["npm_config_store_dir"]) == fixers / "pnpm-store"


def test_a_run_inside_a_checkout_under_worktrees_finds_its_own_tree(
        leerie, tmp_path):
    """A state root inside a checkout whose parent directory is named
    `worktrees` (`<repo>/.claude/worktrees/<name>/.leerie`): the run's own
    tree is found, never the checkout around it."""
    outer = tmp_path / "worktrees" / "co"
    (outer / ".git").mkdir(parents=True)
    run_dir = outer / ".leerie" / "runs" / "r1"
    subprocess.run(["git", "init", "-q", "--bare", str(run_dir / "repo")],
                   check=True)
    staging = run_dir / "worktrees" / "staging"
    (staging / ".git").mkdir(parents=True)
    (run_dir / leerie.TREE_REPOS_NAME / "staging").mkdir(parents=True)
    view = run_dir / "worktrees" / "r1-view-staging"
    (view / ".git").mkdir(parents=True)
    assert leerie._tree_stores_dir(staging / "src")[0] == staging
    assert leerie._tree_cache(staging) == (
        run_dir / "worktrees" / ".stores" / leerie.WORKER_CACHE_DIR)
    assert leerie._tree_stores_dir(view / "src") == (
        view, run_dir / "worktrees" / ".stores" / "tree-stores" / view.name)
    assert leerie._tree_cache(view) is None


def test_an_ast_script_runs_on_its_trees_stores(leerie, run, stores_env,
                                                monkeypatch):
    """`_ts_script_run` is a command leerie runs in a tree: in staging its
    store variables are the fixers' cache its measurement grant names."""
    st = _St(run["user"], run["run_dir"])
    staging = run["wts"]["staging"]
    grants = leerie._measurement_grants(st, staging)
    monkeypatch.setattr(leerie, "_exec_lock_held", lambda: True)
    r = leerie._ts_script_run(["env", "-0"], tree=staging, grants=grants,
                              timeout=60)
    assert r.returncode == 0, r.stderr
    got = dict(item.split(b"=", 1) for item in r.stdout.split(b"\0")
               if b"=" in item)
    cache = _side(leerie, run, leerie.WORKER_CACHE_DIR)
    for var in leerie._STORE_VARS:
        assert Path(got[var.encode()].decode()) == (
            cache / leerie._STORE_VARS[var]), var


def test_every_checkout_names_who_writes_it(leerie):
    """`writer` has no default, and each call names the worker the checkout
    is made for: leerie's own confirm and flip checkouts none."""
    for fn in (leerie._acceptance_writer_checkout,
               leerie._falsifier_checkout, leerie._falsifier_kept_checkout):
        p = inspect.signature(fn).parameters["writer"]
        assert p.kind is p.KEYWORD_ONLY and p.default is p.empty
    tree = ast.parse(inspect.getsource(leerie))
    got: dict[str, set] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        args = [*node.args]
        name = getattr(node.func, "id", getattr(node.func, "attr", ""))
        if name == "to_thread" and args:
            name = getattr(args[0], "id", "")
            args = args[1:]
        if name not in ("_acceptance_writer_checkout", "_falsifier_checkout",
                        "_falsifier_kept_checkout"):
            continue
        kw = {k.arg: k.value for k in node.keywords}
        assert "writer" in kw, ast.unparse(node)
        label = ast.unparse(args[1]) if len(args) > 1 else "?"
        got.setdefault(ast.unparse(kw["writer"]), set()).add(label)
    # The kept tree's rebuild passes `writer` on (it has a default there,
    # for the trees no worker writes): every writer's checkout it makes
    # names its writer at the call.
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call)
                and getattr(node.func, "id", "") ==
                "_reuse_or_rebuild_kept_tree"):
            kw = {k.arg for k in node.keywords}
            if "writer_checkout" in kw:
                assert "writer" in kw, ast.unparse(node)
    assert got == {
        "'acceptance_writer'": {"wt"},
        "'surrogate_fixer'": {"wt"},
        "'falsifier'": {"f'falsifier-lane{n}'"},
        "None": {"'falsifier-confirm'", "f'flip-{side}'"},
        "writer": {"wt"},
    }, got


# --- one choice, for leerie's commands and for the worker ---------------------


def test_every_store_in_a_tree_a_worker_writes_is_its_sides(leerie, run,
                                                           stores_env):
    staging = run["wts"]["staging"]
    cache = _side(leerie, run, leerie.WORKER_CACHE_DIR)
    env = leerie._tree_stores_for(os.environ, staging)
    for var in leerie._STORE_VARS:
        assert Path(env[var]) == cache / leerie._STORE_VARS[var], var
    for k in leerie._PNPM_SETTINGS:
        assert env[f"pnpm_config_{k}"] == env[f"npm_config_{k}"]
    # Not the cache's `XDG_CACHE_HOME`: a measurement's is its own temp.
    assert env.get("XDG_CACHE_HOME") == os.environ.get("XDG_CACHE_HOME")
    g = _grants(leerie, run, cwd=staging)
    wenv = _worker_env(leerie, run, staging, g)
    for var in leerie._STORE_VARS:
        assert wenv[var] == env[var], var


def test_a_tree_no_worker_writes_keeps_the_runs_stores(leerie, run,
                                                       stores_env):
    from tests.test_worker_confinement import RUN_ID
    view = run["wts"][f"{RUN_ID}-view-staging"]
    env = leerie._tree_stores_for(os.environ, view)
    tree = leerie._stores_home(run["run_dir"]) / leerie.TREE_STORES_DIRNAME \
        / view.name
    for var in leerie._STORE_VARS:
        if var in (leerie._TREE_STORE_VARS | leerie._KEYED_STORE_VARS):
            assert Path(env[var]).parent == tree, var
        else:
            assert env[var] == stores_env[var], var


def test_the_measurement_grant_names_the_trees_stores(leerie, run,
                                                     stores_env):
    """In a tree a worker writes, the side's cache in place of the run's
    stores; in a tree no worker writes, the run's stores and no cache."""
    from tests.test_worker_confinement import RUN_ID
    st = _St(run["user"], run["run_dir"])
    home = leerie._stores_home(run["run_dir"])
    run_stores = os.path.realpath(home / leerie.RUN_STORES_DIRNAME)
    fixers = os.path.realpath(home / leerie.WORKER_CACHE_DIR)
    held = os.path.realpath(home / leerie.HELD_OUT_CACHE_DIR)
    lane = _clone(leerie, run, "falsifier-lane0", "falsifier")
    for tree, want in ((run["wts"]["staging"], fixers), (lane, held),
                       (run["wts"][f"{RUN_ID}-view-staging"], run_stores)):
        for kind in (leerie.INSTALL_KIND, leerie.MEASURE_KIND):
            rw = {os.path.realpath(p)
                  for p in leerie._measurement_grants(st, tree, kind).rw}
            assert want in rw, (tree.name, kind)
            assert not ({run_stores, fixers, held} - {want}) & rw, (
                tree.name, kind)


# --- two writers, one tree, real installers under the real grants ------------


def _node() -> tuple[str, str]:
    node, npm = shutil.which("node"), shutil.which("npm")
    if not node or not npm:
        pytest.skip("node and npm are not on PATH here; CI (ubuntu-latest) "
                    "has both and runs these")
    return node, npm


@pytest.fixture(scope="module")
def pnpm_cli(tmp_path_factory) -> Path:
    """pnpm's package directory, `dist/pnpm.cjs` in it: the user's corepack
    cache's, else fetched from the registry through npm."""
    cached = (Path(REAL_HOME) / ".cache" / "node" / "corepack" / "v1"
              / "pnpm" / PNPM_VERSION)
    if (cached / "dist" / "pnpm.cjs").is_file():
        return cached
    _node_, npm = _node()
    d = tmp_path_factory.mktemp("pnpm")
    r = subprocess.run(
        [npm, "install", "--no-save", "--no-audit", "--no-fund",
         "--prefix", str(d), f"pnpm@{PNPM_VERSION}"],
        env={**os.environ, "npm_config_cache": str(d / "npm-cache")},
        capture_output=True, text=True, timeout=300)
    got = d / "node_modules" / "pnpm"
    if r.returncode != 0 or not (got / "dist" / "pnpm.cjs").is_file():
        pytest.skip(f"pnpm {PNPM_VERSION} could not be fetched from the "
                    "registry here; CI reaches it, as its typescript step "
                    f"does: {r.stderr[-300:]}")
    return got


def _command(installer: str, node: str, npm: str, pnpm: Path | None,
             dump: Path) -> list[str]:
    cmd = ([node, str(pnpm), "install", "--offline", "--no-frozen-lockfile"]
           if installer == "pnpm" else
           [npm, "install", "--offline", "--no-audit", "--no-fund"])
    # The environment the installer ran with, then the install, bounded
    # well below the per-test cap: on the old code pnpm waited on a prompt.
    return ["bash", "-c", 'env -0 > "$1"; shift; exec timeout 60 "$@"', "_",
            str(dump), *cmd]


def _read_env(dump: Path) -> dict[str, str]:
    out = {}
    for item in dump.read_bytes().split(b"\0"):
        k, sep, v = item.decode(errors="replace").partition("=")
        if sep:
            out[k] = v
    return out


def _store_keys(leerie) -> set[str]:
    return set(leerie._STORE_VARS) | {f"pnpm_config_{k}"
                                       for k in leerie._PNPM_SETTINGS}


@pytest.mark.parametrize("installer", ["pnpm", "npm"])
@pytest.mark.parametrize("first", ["worker", "leerie"])
def test_two_writers_of_one_tree_install_on_one_store_set(
        leerie, run, stores_env, monkeypatch, request, installer, first):
    node, npm = _node()
    monkeypatch.delenv(leerie.ISOLATE_ENV, raising=False)
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setenv("npm_config_update_notifier", "false")
    monkeypatch.setenv("pnpm_config_update_notifier", "false")
    staging = run["wts"]["staging"]
    (staging / "dep-1.0.0.tgz").write_bytes(_dep_tarball())
    (staging / "package.json").write_text(json.dumps(
        {"name": "p", "version": "1.0.0",
         "dependencies": {"dep": "file:./dep-1.0.0.tgz"}}))
    (staging / ".gitignore").write_text(
        "node_modules/\n.env-*\n.pnpm-cli/\n")
    pnpm = None
    if installer == "pnpm":
        # Into the tree, the one path both writers' grants read wherever the
        # test runs: on CI the state root and pytest's temporary directories
        # both lie beneath `/tmp`, which no grant reads (`_reads_outside`),
        # and there is no corepack cache.
        shutil.copytree(request.getfixturevalue("pnpm_cli"),
                        staging / ".pnpm-cli")
        pnpm = staging / ".pnpm-cli" / "dist" / "pnpm.cjs"
    dumps = {who: staging / f".env-{who}" for who in ("worker", "leerie")}
    st = _St(run["user"], run["run_dir"])
    st.data["provision"] = {"recipe": [{
        "kind": "install", "working_dir": ".",
        "command": _command(installer, node, npm, pnpm,
                            dumps["leerie"])}]}
    log = run["run_dir"] / "logs" / "install.log"
    log.parent.mkdir(parents=True, exist_ok=True)

    def by_leerie() -> tuple[bool, str]:
        ok = asyncio.run(leerie._ensure_worktree_deps(
            str(staging), st, dict(leerie.DEFAULT_CAPS), log_path=log,
            verbosity="quiet"))
        return ok, log.read_text(errors="replace")[-3000:]

    def by_worker() -> tuple[bool, str]:
        g = _grants(leerie, run, cwd=staging)
        wenv = _worker_env(leerie, run, staging, g)
        r = subprocess.run(
            leerie._confined_argv(g, _command(installer, node, npm, pnpm,
                                              dumps["worker"])),
            cwd=str(staging), env=wenv, stdin=subprocess.DEVNULL,
            capture_output=True, text=True, timeout=120)
        return r.returncode == 0, (r.stdout + r.stderr)[-3000:]

    order = (by_worker, by_leerie) if first == "worker" else (by_leerie,
                                                              by_worker)
    for step in order:
        ok, out = step()
        assert ok, f"{step.__name__}: {out}"
    assert (staging / "node_modules" / "dep" / "index.js").read_text() == GOOD
    envs = {who: _read_env(p) for who, p in dumps.items()}
    cache = os.path.realpath(_side(leerie, run, leerie.WORKER_CACHE_DIR))
    for key in _store_keys(leerie):
        assert envs["worker"].get(key) == envs["leerie"].get(key), key
        assert os.path.realpath(envs["leerie"][key]).startswith(
            cache + os.sep), key
    if installer == "pnpm":
        record = (staging / "node_modules" / ".modules.yaml").read_text()
        assert f"storeDir: {cache}{os.sep}pnpm-store" in record, record
