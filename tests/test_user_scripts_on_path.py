"""Wherever leerie points `PYTHONUSERBASE`, `PATH` carries its `bin`, last
(DESIGN §6 *Which stores an install may share*).

pip installs a package's console scripts in `<user base>/bin`, which only
`PATH` finds; the image puts the default base's `~/.local/bin` last for
that. Leerie moves the base per tree (4430d4e9) and per worker side, so the
scripts were on no `PATH`: on a real run of a pytest repository, once its
install reached PyPI, pip installed pytest into the view's user base and
every test run read `pytest: command not found` — the base and every test
axis unmeasured.

Unit cases on `_with_user_scripts`; its three callers executed; a command
leerie runs in a run tree finding a script an install put in that tree's
user base (real Landlock, on the host); and, where isolation works, a real
`pip install` falling back to the user base, with the script it installs
then run by a measurement."""
from __future__ import annotations

import asyncio
import os
import socket
from pathlib import Path

import pytest

from tests.test_confined_network import needs_isolation, proxy  # noqa: F401
from tests.test_worker_confinement import _St, _abi, run  # noqa: F401


def _view(run) -> Path:
    return run["wts"][f"{run['run_dir'].name}-view-staging"]


# --- the helper --------------------------------------------------------------


def test_the_moved_base_s_scripts_go_last_in_place_of_the_old(leerie):
    env = {"PATH": "/usr/bin:/old/pyuser/bin:/home/leerie/.local/bin",
           "PYTHONUSERBASE": "/new/pyuser"}
    leerie._with_user_scripts(env, "/old/pyuser")
    assert env["PATH"].split(":") == ["/usr/bin", "/home/leerie/.local/bin",
                                      "/new/pyuser/bin"]
    # Again, with nothing moved: unchanged, never doubled.
    leerie._with_user_scripts(env, "/new/pyuser")
    assert env["PATH"].split(":")[-1] == "/new/pyuser/bin"
    assert env["PATH"].count("/new/pyuser/bin") == 1


def test_a_baked_base_and_a_pathless_env_are_left_alone(leerie):
    env = {"PATH": "/usr/bin", "PYTHONUSERBASE": "/opt/bake/pyuser"}
    leerie._with_user_scripts(env, None)
    assert env["PATH"] == "/usr/bin"
    env = {"PYTHONUSERBASE": "/new/pyuser"}
    leerie._with_user_scripts(env, None)
    assert "PATH" not in env


# --- its callers, executed ---------------------------------------------------


def test_every_caller_that_moves_the_base_moves_its_scripts(leerie, run,
                                                            tmp_path):
    base = {"PATH": "/usr/bin:/home/leerie/.local/bin"}
    stores = dict(base)
    leerie._leerie_stores(stores, run["state_root"])
    mine = stores["PYTHONUSERBASE"]
    assert stores["PATH"].split(":")[-1] == f"{mine}/bin"

    worker = leerie._worker_cache_env(stores, run["run_dir"])
    w = worker["PYTHONUSERBASE"]
    assert w != mine
    assert worker["PATH"].split(":") == ["/usr/bin", "/home/leerie/.local/bin",
                                         f"{w}/bin"]

    tree = leerie._tree_stores_for(stores, _view(run))
    t = tree["PYTHONUSERBASE"]
    assert t not in (mine, w) and Path(t).is_relative_to(
        run["run_dir"] / "tree-stores")
    assert tree["PATH"].split(":") == ["/usr/bin", "/home/leerie/.local/bin",
                                       f"{t}/bin"]


@pytest.mark.skipif(_abi() < 3, reason="the host kernel has no Landlock ABI 3")
def test_a_measurement_finds_a_script_its_install_put_in_the_user_base(
        leerie, run, monkeypatch):
    """Through `_run_streaming`, under real Landlock: an install-kind
    command writes a console script where pip's user scheme puts one, and a
    measurement of the same tree runs it by name."""
    monkeypatch.delenv("PYTHONUSERBASE", raising=False)
    st = _St(run["user"], run["run_dir"])
    view = _view(run)
    log = run["run_dir"] / "logs" / "user-scripts.log"
    log.parent.mkdir(parents=True, exist_ok=True)

    async def go():
        rc, tail = await leerie._run_streaming(
            ["bash", "-c", 'mkdir -p "$PYTHONUSERBASE/bin" && printf '
             '"#!/bin/sh\\necho SCRIPT-RAN\\n" > "$PYTHONUSERBASE/bin/'
             'leerie-fake-script" && chmod +x "$PYTHONUSERBASE/bin/'
             'leerie-fake-script"'],
            grants=leerie._measurement_grants(st, view, leerie.INSTALL_KIND),
            cwd=str(view), timeout=60, log_path=log, label="install",
            verbosity="quiet")
        assert rc == 0, tail
        return await leerie._run_streaming(
            ["leerie-fake-script"], grants=leerie._measurement_grants(
                st, view), cwd=str(view), timeout=60, log_path=log,
            label="measure", verbosity="quiet")
    rc, tail = asyncio.run(go())
    assert rc == 0 and "SCRIPT-RAN" in log.read_text(), log.read_text()


# --- a real pip user install, where isolation works --------------------------


def _online() -> bool:
    try:
        socket.getaddrinfo("pypi.org", 443)
        return True
    except OSError:
        return False


@needs_isolation
def test_a_pip_user_install_s_script_runs_in_the_measurement(
        leerie, run, proxy, monkeypatch):  # noqa: F811
    """The review's shape: pip, with the system site not writable, defaults
    to the user base, and the measurement then runs the script it put
    there by name."""
    if not _online():
        pytest.skip("the runtime itself has no network to reach PyPI with")
    monkeypatch.setenv(leerie.ISOLATE_ENV, "1")
    monkeypatch.delenv("PYTHONUSERBASE", raising=False)
    view = _view(run)
    st = _St(run["user"], run["run_dir"])
    st.data["provision"] = {"recipe": [
        {"kind": "install",
         "command": ["python3", "-m", "pip", "--disable-pip-version-check",
                     "install", "--break-system-packages", "--no-deps",
                     "pyflakes==3.2.0"]}]}
    log = run["run_dir"] / "logs" / "user-install.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    leerie._DEPS_INSTALLED.discard(os.path.realpath(view))
    ok = asyncio.run(leerie._ensure_worktree_deps(
        str(view), st, dict(leerie.DEFAULT_CAPS), log_path=log,
        verbosity="quiet"))
    text = log.read_text(errors="replace")
    assert ok is True, text
    assert "not on PATH" not in text, text
    rc, _tail = asyncio.run(leerie._run_streaming(
        ["pyflakes", "--version"], grants=leerie._measurement_grants(st, view),
        cwd=str(view), timeout=60, log_path=log, label="measure",
        verbosity="quiet"))
    assert rc == 0, log.read_text(errors="replace")
