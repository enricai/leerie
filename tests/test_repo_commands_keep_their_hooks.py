"""The repository's own commands run in the environment the repository
expects (DESIGN §6½ *Hooks off*; review round 17 M4).

Leerie's hooks-off entry is git's command scope, which no repository config
overrides. Inherited by the repository's test suite, it also turned hooks
off in repositories the code under test makes for itself: a suite that
asserts a hook fires in its own temporary repository was green outside
leerie and red in every leerie measurement. Leerie's own git keeps the
entry; every command of the repository's is launched through
`_run_streaming` (or the self-check's spawn), both built with
`_repo_command_env`. Driven with the environment `main()` builds."""
from __future__ import annotations

import ast
import asyncio
import os
import subprocess
from pathlib import Path

import pytest

from tests.conftest import REPO_ROOT

# A test command that creates a temporary repository, installs a
# pre-commit hook in it and asserts the hook ran.
SUITE = r"""
import os, subprocess, tempfile, pathlib

def test_my_hook_fires():
    d = pathlib.Path(tempfile.mkdtemp())
    def git(*a):
        subprocess.run(["git", "-C", str(d), *a], check=True,
                       capture_output=True)
    git("init", "-q")
    git("config", "user.email", "t@x")
    git("config", "user.name", "t")
    hook = d / ".git" / "hooks" / "pre-commit"
    hook.write_text(f"#!/bin/sh\ntouch '{d}/hook-ran'\n")
    hook.chmod(0o755)
    git("commit", "-q", "--allow-empty", "-m", "probe")
    assert (d / "hook-ran").exists()
"""
CMD = "python3 -m pytest -q -p no:cacheprovider tests"


@pytest.fixture
def leerie_env(leerie, monkeypatch):
    """`main()`'s environment: leerie's hooks-off entry in os.environ."""
    for k in list(os.environ):
        if k.startswith(("GIT_CONFIG_", "LEERIE_GIT_HOOKS")):
            monkeypatch.delenv(k)
    env: dict[str, str] = {}
    leerie._git_hooks_off(env)
    for k, v in env.items():
        monkeypatch.setenv(k, v)


def _repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    (r / "tests").mkdir(parents=True)
    (r / "tests" / "test_hooks.py").write_text(SUITE)
    for a in (["init", "-q", "-b", "main"], ["config", "user.email", "t@x"],
              ["config", "user.name", "t"], ["add", "-A"],
              ["commit", "-qm", "base"]):
        subprocess.run(["git", *a], cwd=r, check=True, capture_output=True)
    return r


class _St:
    """A run with a run directory: every repository command is confined to
    its measurement grant (DESIGN §6 *Leerie's runs of the repository's
    commands are confined too*), which lives there."""
    def __init__(self, run_dir: Path):
        self.run_dir = run_dir
        self.run_id = run_dir.name
        self.data = {"provision": {"recipe": []}, "blt_results": {}}

    def save(self):
        pass


def test_the_suite_passes_through_a_measured_axis(leerie, tmp_path,
                                                  leerie_env):
    r = _repo(tmp_path)
    # The control: the same suite, run as leerie's own git would be (the
    # entry inherited), is red — the measurement must not see that.
    bad = subprocess.run(CMD.split(), cwd=r, capture_output=True)
    assert bad.returncode != 0
    res = asyncio.run(leerie._measure_axes(
        str(r), {"tests": CMD}, _St(tmp_path / "state" / "runs" / "r1"),
        {}, log_path=tmp_path / "m.log",
        verbosity="quiet", memo=False))
    assert res["tests"]["passed"] is True, res["tests"]


def test_the_suite_passes_through_an_acceptance_run(leerie, tmp_path,
                                                    leerie_env):
    from tests.test_acceptance_unmeasured_pr import _caps, _eval_st
    r = _repo(tmp_path)
    (r / ".leerie").mkdir()
    (r / ".leerie" / "config.toml").write_text(
        'test = "python3 -m pytest -q -p no:cacheprovider"\n')
    st = _eval_st(leerie, tmp_path, r)
    v = asyncio.run(leerie._run_acceptance_file(
        st, _caps(leerie), str(r), "tests/test_hooks.py",
        st.run_dir / "logs" / "a.log", "a"))
    assert v is True


def test_leeries_own_git_still_runs_hooks_off(leerie, tmp_path, leerie_env):
    """The converse: the entry stays on leerie's own git."""
    r = _repo(tmp_path)
    hooks = r / ".git" / "hooks"
    (hooks / "post-commit").write_text(
        f"#!/bin/sh\ntouch {tmp_path}/fired\n")
    (hooks / "post-commit").chmod(0o755)
    asyncio.run(leerie._run_proc(["git", "commit", "-q", "--allow-empty",
                                 "-m", "leerie"], cwd=str(r)))
    assert not (tmp_path / "fired").exists()


def test_every_repo_command_launch_goes_through_the_repo_env():
    """Derived: every function that gates a repository command through
    `_blt_shell_argv` launches it only through `_run_streaming` or
    `_run_contained` (which spawns through `_run_streaming`), or with an
    environment built by `_repo_command_env`; and `_run_streaming` builds
    its spawn's environment with it. And every such spawn is confined
    (DESIGN §6 *Leerie's runs of the repository's commands are confined
    too*): `_run_streaming` takes `grants` keyword-only with no default and
    starts only `_confined_argv(grants, …)`; every call of `_run_streaming`,
    `_run_contained` and `_measure_blt` passes `grants=`; the one raw spawn
    left is the self-check's, which a confined worker's shell starts and
    so runs inside that worker's grants."""
    tree = ast.parse((REPO_ROOT / "orchestrator" / "leerie.py").read_text())
    funcs = {f.name: f for f in ast.walk(tree)
             if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))}

    def names(fn):
        out = set()
        for n in ast.walk(fn):
            if isinstance(n, ast.Call):
                f = n.func
                out.add(f.attr if isinstance(f, ast.Attribute) else
                        getattr(f, "id", None))
        return out
    stream_src = ast.unparse(funcs["_run_streaming"])
    assert "env={**_repo_command_env(env), **dict(grants.env)}" in stream_src
    assert "_run_streaming" in names(funcs["_run_contained"])
    for prim in ("_run_streaming", "_run_contained", "_measure_blt"):
        a = funcs[prim].args
        kw = dict(zip([x.arg for x in a.kwonlyargs], a.kw_defaults))
        assert "grants" in kw and kw["grants"] is None, prim
    # The spawn is the confined argv, and only it.
    spawns = [n for n in ast.walk(funcs["_run_streaming"])
              if isinstance(n, ast.Call)
              and getattr(n.func, "attr", None) == "create_subprocess_exec"]
    assert len(spawns) == 1
    # Under the execution lock held shared (DESIGN §6 *A held-out
    # evaluation runs alone*), in the caller's sandbox when it gives one.
    assert ("spawn_cmd = _confined_argv(grants, list(cmd), sandbox=sandbox,"
            in stream_src)
    assert "async with _exec_lock().shared():" in stream_src
    assert ast.unparse(spawns[0].args[0]) == "*spawn_cmd"
    unpassed = [(name, n.lineno) for name, fn in funcs.items()
                for n in ast.walk(fn) if isinstance(n, ast.Call)
                and getattr(n.func, "id", None) in (
                    "_run_streaming", "_run_contained", "_measure_blt")
                and not any(k.arg == "grants" for k in n.keywords)]
    assert unpassed == []
    raw = {"Popen", "run", "create_subprocess_exec", "check_output",
           "call", "check_call"}
    bad = []
    raw_ok: set[str] = set()
    for name, fn in funcs.items():
        if "_blt_shell_argv" not in names(fn) or name == "_blt_shell_argv":
            continue
        for n in ast.walk(fn):
            if not isinstance(n, ast.Call):
                continue
            f = n.func
            attr = f.attr if isinstance(f, ast.Attribute) else None
            if attr not in raw:
                continue
            argv = n.args[0] if n.args else None
            literal_git = (isinstance(argv, ast.List) and argv.elts
                           and isinstance(argv.elts[0], ast.Constant)
                           and argv.elts[0].value in ("git", "tar"))
            env = next((k.value for k in n.keywords if k.arg == "env"),
                       None)
            env_ok = env is not None and "_repo_command_env" in ast.unparse(
                funcs[name]) and isinstance(env, ast.Name)
            if not literal_git and not env_ok:
                bad.append((name, n.lineno))
            elif env_ok and not literal_git:
                raw_ok.add(name)
    assert bad == []
    assert raw_ok == {"selfcheck_main"}


def test_the_guard_flags_a_bypass():
    src = ("def f(argv):\n"
           "    argv = _blt_shell_argv(argv, [])\n"
           "    subprocess.Popen(argv)\n")
    fn = ast.parse(src).body[0]
    calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute)
             and n.func.attr == "Popen"]
    assert calls and not any(k.arg == "env" for k in calls[0].keywords)


def test_leeries_scripts_that_load_tree_code_are_confined():
    """Derived: `node` (the AST scripts load the tree's own `typescript`)
    and `mise` (it reads the repository's configuration) are never spawned
    directly — only through `_ts_script_run`, `_run_streaming` or
    `_confined_argv` (DESIGN §6 *Leerie's runs of the repository's commands
    are confined too*)."""
    tree = ast.parse((REPO_ROOT / "orchestrator" / "leerie.py").read_text())
    spawners = {"Popen", "run", "create_subprocess_exec", "check_output",
                "call", "check_call", "_run_proc"}
    bad = []
    for n in ast.walk(tree):
        if not isinstance(n, ast.Call) or not n.args:
            continue
        f = n.func
        name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id",
                                                                    None)
        argv = n.args[0]
        if (name in spawners and isinstance(argv, ast.List) and argv.elts
                and isinstance(argv.elts[0], ast.Constant)
                and argv.elts[0].value in ("node", "mise")):
            bad.append(n.lineno)
    assert bad == []
    funcs = {f.name: f for f in ast.walk(tree)
             if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))}
    assert "_confined_argv(grants, argv, refusal_fd=refusal_fd)" in ast.unparse(
        funcs["_ts_script_run"])
