"""No git command of leerie's own runs a repository hook; a worker's own git
keeps them (DESIGN §6½ *Hooks off*).

Review round 15 M3 turned hooks off per "hook-firing" subcommand; review
round 16 (claims H1, exec M1) measured that a path checkout fires
`post-checkout` and even `git status` fires `post-index-change`, and two
leerie paths in measured trees ran one with hooks on. So hooks are off for
every git process leerie starts, through the environment they all inherit
(`_git_hooks_off`), and only a worker's spawn takes the entry back out
(`_git_hooks_on` in `_invoke`). These run real git with every hook
installed."""
from __future__ import annotations

import ast
import asyncio
import inspect
import os
import re
import subprocess
from pathlib import Path

import pytest

from tests.conftest import REPO_ROOT

LEERIE = REPO_ROOT / "orchestrator" / "leerie.py"
# Every client-side hook git documents (githooks(5)) that a local command
# can fire.
HOOKS = ["applypatch-msg", "pre-applypatch", "post-applypatch",
         "pre-commit", "pre-merge-commit", "prepare-commit-msg",
         "commit-msg", "post-commit", "pre-rebase", "post-checkout",
         "post-merge", "pre-push", "reference-transaction", "pre-auto-gc",
         "post-rewrite", "post-index-change"]


def _git(cwd, *a, env=None):
    return subprocess.run(["git", *a], cwd=cwd, check=True, env=env,
                          capture_output=True, text=True).stdout.strip()


def _hooked_repo(tmp_path: Path) -> tuple[Path, Path]:
    """A repo whose shared config enables every hook (as husky's `prepare`
    does during leerie's install); each appends its name to `fired`."""
    r = tmp_path / "r"
    r.mkdir()
    clean = {k: v for k, v in os.environ.items()
             if not k.startswith(("GIT_CONFIG_", "LEERIE_GIT_HOOKS"))}
    _git(r, "init", "-q", "-b", "main", env=clean)
    _git(r, "config", "user.email", "t@x", env=clean)
    _git(r, "config", "user.name", "t", env=clean)
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    fired = tmp_path / "fired"
    for h in HOOKS:
        (hooks / h).write_text(f'#!/bin/sh\necho {h} >> "{fired}"\n')
        (hooks / h).chmod(0o755)
    (r / "snap.txt").write_text("v1\n")
    (r / "lock.json").write_text("{}\n")
    _git(r, "add", "-A", env=clean)
    _git(r, "commit", "-qm", "base", env=clean)
    _git(r, "config", "core.hooksPath", str(hooks), env=clean)
    if fired.exists():
        fired.unlink()
    return r, fired


@pytest.fixture
def hooks_off(leerie, monkeypatch):
    """`main()`'s first act, on a monkeypatched environment."""
    for k in list(os.environ):
        if k.startswith(("GIT_CONFIG_", "LEERIE_GIT_HOOKS")):
            monkeypatch.delenv(k)
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")          # the operator's own
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "color.ui")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "never")
    env = dict(os.environ)
    leerie._git_hooks_off(env)
    for k, v in env.items():
        monkeypatch.setenv(k, v)


def test_the_entry_round_trips_and_keeps_the_operators(leerie, hooks_off):
    env = dict(os.environ)
    assert env["GIT_CONFIG_COUNT"] == "2"
    assert (env["GIT_CONFIG_KEY_1"], env["GIT_CONFIG_VALUE_1"]) == (
        "core.hooksPath", "/dev/null")
    before = dict(env)
    leerie._git_hooks_off(env)                     # idempotent
    assert env == before
    back = leerie._git_hooks_on(env)
    assert back["GIT_CONFIG_COUNT"] == "1"
    assert back["GIT_CONFIG_KEY_0"] == "color.ui"
    assert not any(k.endswith("_1") and k.startswith("GIT_CONFIG_")
                   for k in back)
    assert "LEERIE_GIT_HOOKS_OFF" not in back


def test_the_i2_brackets_put_back_fires_no_hook(leerie, tmp_path, hooks_off):
    """The reviewer's r16 exec proof, turned: the bracket's tracked
    put-back (`git checkout HEAD -- <path>`) ran `post-checkout`, and a hook
    that rewrites the lockfile made the bracket die as leerie's own."""
    r, fired = _hooked_repo(tmp_path)
    hook = tmp_path / "hooks" / "post-checkout"
    hook.write_text(hook.read_text()
                    + 'printf "{\\"x\\":1}\\n" > lock.json\n')

    async def go():
        async with leerie._leaves_tree_as_found(r, "test run", "t"):
            (r / "snap.txt").write_text("v2\n")
    asyncio.run(go())
    assert (r / "snap.txt").read_text() == "v1\n"
    assert (r / "lock.json").read_text() == "{}\n"
    assert not fired.exists()


def test_every_git_form_leerie_uses_fires_no_hook(leerie, tmp_path,
                                                  hooks_off):
    """Under the entry, the forms measured to fire hooks fire none — the
    control without it shows each really does."""
    r, fired = _hooked_repo(tmp_path)
    forms = [["status", "--porcelain"], ["checkout", "--", "snap.txt"],
             ["checkout", "HEAD", "--", "snap.txt"],
             ["reset", "-q", "--hard", "HEAD"],
             ["update-ref", "refs/heads/x", "HEAD"],
             ["worktree", "add", "-q", str(tmp_path / "w"), "x"],
             ["commit", "-q", "--allow-empty", "-m", "c"]]
    for f in forms:
        (r / "snap.txt").write_text("v2\n")
        _git(r, *f)
    assert not fired.exists()
    clean = leerie._git_hooks_on(os.environ)
    (r / "snap.txt").write_text("v2\n")
    _git(r, "checkout", "--", "snap.txt", env=clean)
    assert "post-checkout" in fired.read_text()


def test_every_script_leerie_runs_turns_hooks_off():
    """Derived: every script `_run_script` names in leerie.py, and every one
    the subtree collection's payload runs, calls `leerie_git_hooks_off`."""
    src = LEERIE.read_text()
    names = set(re.findall(r'_run_script\(\s*"([\w.-]+\.sh)"', src))
    names |= set(re.findall(
        r'\$SCRIPTS/([\w.-]+\.sh)',
        (REPO_ROOT / "scripts" / "remote" / "collect-subtrees.sh").read_text()))
    names.discard("worktree-lib.sh")
    assert {"setup-run.sh", "integrate.sh", "new-worktree.sh"} <= names
    missing = [n for n in sorted(names)
               if not re.search(r"^leerie_git_hooks_off$",
                                (REPO_ROOT / "scripts" / n).read_text(),
                                re.M)]
    assert missing == []
    assert re.search(r"^leerie_git_hooks_off$",
                     (REPO_ROOT / "scripts" / "remote" /
                      "collect-subtrees.sh").read_text(), re.M)


def test_a_script_run_outside_the_orchestrator_fires_no_hook(leerie,
                                                             tmp_path):
    """integrate.sh's merge and setup-run/new-worktree's adds with no
    hooks-off entry in the caller's environment (the collection's case)."""
    r, fired = _hooked_repo(tmp_path)
    env = leerie._git_hooks_on(os.environ)
    env["LEERIE_STATE_DIR"] = str(tmp_path / "state")

    def sh(name, *a):
        return subprocess.run(["bash", str(REPO_ROOT / "scripts" / name),
                               *a], cwd=r, env=env, check=True,
                              capture_output=True, text=True).stdout
    sh("setup-run.sh", "run1", "main")
    wt = Path(sh("new-worktree.sh", "s1", "run1").strip().splitlines()[-1])
    (wt / "f.txt").write_text("f\n")
    subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "add", "f.txt"],
                   cwd=wt, check=True)
    subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "commit", "-qm",
                    "f"], cwd=wt, check=True)
    sh("integrate.sh", "s1", "run1")
    assert not fired.exists()


def test_main_turns_hooks_off_before_anything_spawns(leerie):
    """Structure, paired with the substance tests above and below: the
    call is `main()`'s second statement, ahead of `_become_subreaper` and
    every spawn."""
    body = ast.parse(inspect.getsource(leerie.main)).body[0].body
    calls = [ast.unparse(s) for s in body[:3]]
    assert calls[1] == "_git_hooks_off(os.environ)"


def test_a_worker_spawn_gets_the_hooks_back(leerie, tmp_path, monkeypatch,
                                            hooks_off):
    """The real `_invoke`, stopped at its spawn: the worker's environment
    has the operator's git config and not leerie's hooks-off entry."""
    seen: dict = {}

    async def stop(*argv, **kw):
        seen.update(kw)
        raise RuntimeError("stop at spawn")
    monkeypatch.setattr(leerie.asyncio, "create_subprocess_exec", stop)
    (tmp_path / "logs").mkdir()
    with pytest.raises(Exception):
        asyncio.run(leerie._invoke(["claude", "-p"], str(tmp_path), 10,
                                   "w", tmp_path, "quiet"))
    env = seen["env"]
    assert env["GIT_CONFIG_COUNT"] == "1"
    assert env["GIT_CONFIG_KEY_0"] == "color.ui"
    assert "LEERIE_GIT_HOOKS_OFF" not in env
    assert "core.hooksPath" not in env.values()
