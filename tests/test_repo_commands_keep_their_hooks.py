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
    def __init__(self):
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
        str(r), {"tests": CMD}, _St(), {}, log_path=tmp_path / "m.log",
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
    asyncio.run(leerie.run_proc(["git", "commit", "-q", "--allow-empty",
                                 "-m", "leerie"], cwd=str(r)))
    assert not (tmp_path / "fired").exists()


def test_every_repo_command_launch_goes_through_the_repo_env():
    """Derived: every function that gates a repository command through
    `_blt_shell_argv` launches it only through `_run_streaming` or
    `_run_contained` (which spawns through `_run_streaming`), or with an
    environment built by `_repo_command_env`; and `_run_streaming` builds
    its spawn's environment with it."""
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
    assert "env=_repo_command_env(env)" in stream_src
    assert "_run_streaming" in names(funcs["_run_contained"])
    raw = {"Popen", "run", "create_subprocess_exec", "check_output",
           "call", "check_call"}
    bad = []
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
    assert bad == []


def test_the_guard_flags_a_bypass():
    src = ("def f(argv):\n"
           "    argv = _blt_shell_argv(argv, [])\n"
           "    subprocess.Popen(argv)\n")
    fn = ast.parse(src).body[0]
    calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute)
             and n.func.attr == "Popen"]
    assert calls and not any(k.arg == "env" for k in calls[0].keywords)
