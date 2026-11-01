"""Every install leerie runs on a repository's code reaches its inputs; what
it measures reaches nothing (DESIGN §6 *A confined process has its own
network*, *An install reaches its inputs*).

Review round 22's execution H3: once every confined process had its own
network namespace, the install a view runs was granted as a measurement
(`MEASURE_KIND`, no network), on the assumption that a view installs offline
from leerie's stores — which hold only what a store fetch put there, and
only pnpm, npm, yarn, bun, go and composer have a fetch. A pytest
repository's `pip install` failed name resolution in every view, nothing was
measured, and a real run ended `unconverged`. An install is now its own
kind, `INSTALL_KIND`: the measurement grant on disk, with egress.

Three layers here:
- the grant: `INSTALL_KIND` is `MEASURE_KIND` plus egress, nothing more;
- the consumers, executed: `_ensure_worktree_deps`, `_ci_prepare`/`_ci_run`
  and `_install_settles` hand an install an egress grant and a build or
  check a grant without one;
- a rule over `leerie.py`: every function that runs a recipe's `install`
  entry or a CI prerequisite names `INSTALL_KIND`, and nothing else does.

The network itself, with leerie's real egress proxy and a real `pip
install` from PyPI, needs a runtime that can isolate (the container under
the `leerie-container` profile, `--basetemp=/leerie-state/...`); it skips
elsewhere."""
from __future__ import annotations

import ast
import asyncio
import socket
from pathlib import Path

import pytest

from tests.test_blt_forbidden_gate import _st as _gate_st
from tests.test_blt_forbidden_gate import repo  # noqa: F401  (fixture)
from tests.test_confined_network import needs_isolation, proxy  # noqa: F401
from tests.test_worker_confinement import _St, run  # noqa: F401  (fixture)

SRC = Path(__file__).resolve().parents[1] / "orchestrator" / "leerie.py"


@pytest.fixture(autouse=True)
def _clear_deps_memo(leerie):
    leerie._DEPS_INSTALLED.clear()
    leerie._DEPS_FAILED.clear()
    yield
    leerie._DEPS_INSTALLED.clear()
    leerie._DEPS_FAILED.clear()


def _view(run) -> Path:
    return run["wts"][f"{run['run_dir'].name}-view-staging"]


# --- the grant ---------------------------------------------------------------


def test_an_install_is_a_measurement_with_egress_and_nothing_more(leerie,
                                                                  run):
    """Same paths, same env (so leerie's fetched stores stay read-only to
    it, and its own tree stores and the verified stores are written as a
    measurement's are); only `egress` differs."""
    st = _St(run["user"], run["run_dir"])
    for tree in (_view(run), run["wts"]["wt-a"]):
        m = leerie._measurement_grants(st, tree, leerie.MEASURE_KIND)
        i = leerie._measurement_grants(st, tree, leerie.INSTALL_KIND)
        assert (m.egress, i.egress) == (False, True)
        assert (i.rw, i.ro, i.env) == (m.rw, m.ro, m.env)
        stores = str(run["state_root"] / ".stores" / "leerie")
        assert stores in i.ro and stores not in i.rw


def test_an_unknown_kind_is_refused(leerie, run):
    st = _St(run["user"], run["run_dir"])
    with pytest.raises(ValueError):
        leerie._measurement_grants(st, _view(run), "network")


# --- the consumers, executed -------------------------------------------------


@pytest.fixture
def spawned(leerie, monkeypatch):
    """Every spawn `_run_streaming` makes, as (argv, egress), then run for
    real: `_run_contained` goes through it too."""
    real = leerie._run_streaming
    got: list[tuple[list[str], bool]] = []

    async def spy(cmd, **kw):
        got.append((list(cmd), kw["grants"].egress))
        return await real(cmd, **kw)
    monkeypatch.setattr(leerie, "_run_streaming", spy)
    return got


def _tagged(tag: str) -> list[str]:
    # `python3 -c pass <tag>`: a real, harmless command told apart by argv.
    return ["python3", "-c", "pass", tag]


def _egress_by_tag(got) -> dict[str, bool]:
    return {argv[-1]: egress for argv, egress in got
            if argv[:3] == ["python3", "-c", "pass"]}


def test_a_recipe_install_gets_egress_and_its_build_does_not(
        leerie, repo, spawned):  # noqa: F811
    st = _gate_st(leerie, repo)
    st.data["provision"] = {"recipe": [
        {"kind": "install", "command": _tagged("install-1")},
        {"kind": "build", "command": _tagged("build")},
        {"kind": "install", "command": _tagged("install-2")}]}
    ok = asyncio.run(leerie._ensure_worktree_deps(
        str(repo), st, dict(leerie.DEFAULT_CAPS),
        log_path=st.run_dir / "logs" / "deps.log", verbosity="quiet"))
    assert ok is True
    assert [a[-1] for a, _e in spawned] == ["install-1", "build",
                                            "install-2"]
    assert _egress_by_tag(spawned) == {"install-1": True, "build": False,
                                       "install-2": True}


def test_ci_parity_sets_up_with_egress_and_checks_without(
        leerie, repo, spawned):  # noqa: F811
    st = _gate_st(leerie, repo)
    st.data["provision"] = {"recipe": [
        {"kind": "install", "command": _tagged("recipe-install")}]}
    rec = {"id": "lint", "argv": _tagged("check"), "cwd": ".",
           "prerequisites": [{"argv": _tagged("prerequisite"),
                              "cwd": "."}]}
    caps = dict(leerie.DEFAULT_CAPS)

    async def go():
        await leerie._ci_prepare(repo, [rec], st, caps, "final")
        return await leerie._ci_run(rec["argv"], tree=repo, rec=rec, st=st,
                                    caps=caps, label="final-lint")
    out = asyncio.run(go())
    assert out["rc"] == 0
    assert _egress_by_tag(spawned) == {"recipe-install": True,
                                       "prerequisite": True, "check": False}


def test_an_install_gap_reinstalls_with_egress_and_coverage_has_none(
        leerie, repo, spawned):  # noqa: F811
    st = _gate_st(leerie, repo)
    st.data["provision"] = {"recipe": [
        {"kind": "install", "command": _tagged("settle-install")}]}
    caps = dict(leerie.DEFAULT_CAPS)
    assert asyncio.run(leerie._install_settles(repo, st=st, caps=caps,
                                               sid="feat-001")) is True
    out = asyncio.run(leerie._cov_contained(
        _tagged("coverage"), tree=repo, st=st, caps=caps, sid="feat-001",
        tag="t"))
    assert out["rc"] == 0
    assert _egress_by_tag(spawned) == {"settle-install": True,
                                       "coverage": False}


# --- the rule ----------------------------------------------------------------

_RUNNERS = {"_run_streaming", "_run_contained", "_ci_run", "_cov_contained"}


def _functions(tree: ast.Module):
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield n


def _calls_a_runner(fn: ast.AST) -> bool:
    return any(isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
               and c.func.id in _RUNNERS for c in ast.walk(fn))


def _reads_install_entries(fn: ast.AST) -> bool:
    """`<x>.get("kind") == "install"`: a provision recipe's install entry."""
    for c in ast.walk(fn):
        if (isinstance(c, ast.Compare) and len(c.ops) == 1
                and isinstance(c.ops[0], ast.Eq)
                and isinstance(c.left, ast.Call)
                and isinstance(c.left.func, ast.Attribute)
                and c.left.func.attr == "get" and c.left.args
                and isinstance(c.left.args[0], ast.Constant)
                and c.left.args[0].value == "kind"
                and isinstance(c.comparators[0], ast.Constant)
                and c.comparators[0].value == "install"):
            return True
    return False


def _reads_prerequisites(fn: ast.AST) -> bool:
    return any(isinstance(c, ast.Constant) and c.value == "prerequisites"
               for c in ast.walk(fn))


def _names(fn: ast.AST, name: str) -> bool:
    return any(isinstance(c, ast.Name) and c.id == name
               for c in ast.walk(fn))


def _install_runners(tree: ast.Module) -> dict[str, ast.AST]:
    """Every function that runs an install: it reads a recipe's install
    entries or a CI check's prerequisites, and spawns. Leerie's own store
    fetch reads install entries too, but runs only its own script-free
    fetch (`STORE_FETCH_KIND`), never the entry's command."""
    return {fn.name: fn for fn in _functions(tree)
            if _calls_a_runner(fn)
            and (_reads_install_entries(fn) or _reads_prerequisites(fn))
            and not _names(fn, "STORE_FETCH_KIND")}


def test_every_install_leerie_runs_uses_the_install_kind():
    tree = ast.parse(SRC.read_text())
    runners = _install_runners(tree)
    # Anti-vacuity: the three install sites the design names.
    assert {"_ensure_worktree_deps", "_ci_prepare",
            "_install_settles"} <= set(runners), sorted(runners)
    missing = sorted(n for n, fn in runners.items()
                     if not _names(fn, "INSTALL_KIND"))
    assert missing == [], (
        "an install leerie runs without INSTALL_KIND has no network and "
        "cannot reach its inputs (DESIGN §6 *An install reaches its "
        f"inputs*): {missing}")


def test_nothing_but_an_install_uses_the_install_kind():
    """The converse: a measurement, a held-out run or a check handed
    `INSTALL_KIND` would reach the network the committed view is measured
    without."""
    tree = ast.parse(SRC.read_text())
    users = {fn.name for fn in _functions(tree)
             if _names(fn, "INSTALL_KIND")}
    # `_measurement_grants` accepts it through `MEASUREMENT_KINDS`, a
    # module constant; no function names it but an install's.
    assert users == set(_install_runners(tree))


def test_the_rule_flags_an_install_without_the_kind():
    """The guard itself, on a synthetic module."""
    src = (
        "async def deps(recipe, st, tree):\n"
        "    for e in recipe:\n"
        "        if e.get('kind') == 'install':\n"
        "            await _run_streaming(e['command'],\n"
        "                grants=_measurement_grants(st, tree))\n"
        "async def prep(rec):\n"
        "    for p in rec.get('prerequisites') or []:\n"
        "        await _ci_run(p['argv'], kind=INSTALL_KIND)\n"
        "async def fetch(recipe, st, stage):\n"
        "    for e in recipe:\n"
        "        if e.get('kind') == 'install':\n"
        "            await _run_streaming(['pnpm', 'fetch'],\n"
        "                grants=_measurement_grants(st, stage,\n"
        "                                           STORE_FETCH_KIND))\n")
    runners = _install_runners(ast.parse(src))
    assert sorted(runners) == ["deps", "prep"]
    assert [n for n, fn in runners.items()
            if not _names(fn, "INSTALL_KIND")] == ["deps"]


# --- the network, in a runtime that can isolate ------------------------------


def _online() -> bool:
    try:
        socket.getaddrinfo("pypi.org", 443)
        return True
    except OSError:
        return False


PIN = "iniconfig==2.0.0"
PIP = ["python3", "-m", "pip", "--disable-pip-version-check",
       "--no-input"]


@needs_isolation
def test_a_pip_install_in_a_view_reaches_pypi_and_its_build_does_not(
        leerie, run, proxy, monkeypatch):  # noqa: F811
    """The review's shape: a pip repository's recipe in a committed view.
    Its install reaches PyPI through leerie's egress proxy; a `build` entry
    running the same download, and a plain measurement, reach nothing."""
    if not _online():
        pytest.skip("the runtime itself has no network to reach PyPI with")
    monkeypatch.setenv(leerie.ISOLATE_ENV, "1")
    view = _view(run)
    # Pre-existing and untracked, so the install's own outputs stay.
    (view / ".gitignore").write_text(".deps/\n.dl/\n.dl2/\n")
    st = _St(run["user"], run["run_dir"])
    st.data["provision"] = {"recipe": [
        {"kind": "install",
         "command": PIP + ["install", "--no-deps", "--target", ".deps",
                           PIN]},
        {"kind": "build",
         "command": PIP + ["download", "--no-deps", "-d", ".dl", PIN]}]}
    log_path = run["run_dir"] / "logs" / "view-install.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    ok = asyncio.run(leerie._ensure_worktree_deps(
        str(view), st, dict(leerie.DEFAULT_CAPS), log_path=log_path,
        verbosity="quiet"))
    text = log_path.read_text(errors="replace")
    assert ok is True, text
    assert (view / ".deps" / "iniconfig" / "__init__.py").is_file(), text
    assert "Successfully installed iniconfig" in text, text
    # pip makes the directory before it fails: what matters is no wheel.
    assert not list((view / ".dl").glob("*.whl")), text
    build = text.split("download --no-deps -d .dl", 1)[1]
    assert "No matching distribution found" in build, text
    # A measurement of the same view: no resolver, no route, no proxy.
    m_log = run["run_dir"] / "logs" / "view-measure.log"
    rc, tail = asyncio.run(leerie._run_streaming(
        PIP + ["download", "--no-deps", "-d", ".dl2", PIN],
        grants=leerie._measurement_grants(st, view), cwd=str(view),
        timeout=180, log_path=m_log, label="measure", verbosity="quiet"))
    assert rc != 0, tail
    assert not list((view / ".dl2").glob("*.whl")), tail
