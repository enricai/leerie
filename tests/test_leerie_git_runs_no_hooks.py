"""Every git command of leerie's own that can fire a repository hook runs
with the hooks off (DESIGN §6½; review round 15 M3: husky's `post-merge`,
enabled by leerie's own install, wrote into staging on integrate.sh's
merge). Workers' own git commands keep the repository's hooks.

A derived source guard over `orchestrator/leerie.py` and the scripts that
act on a tree leerie measures, integrates or ships, paired with a
behavioural check that the flag really disables a hook."""
from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path

from tests.conftest import REPO_ROOT

LEERIE = REPO_ROOT / "orchestrator" / "leerie.py"
SCRIPTS = ["integrate.sh", "setup-run.sh", "new-worktree.sh",
           "planning-worktree.sh", "worktree-lib.sh", "host-finalize.sh"]
# The subcommands that run hooks (post-merge, post-checkout, pre-/post-
# commit, post-rewrite). `checkout -- <paths>` and `merge --abort` run none.
HOOKED = {"merge", "commit", "cherry-pick", "rebase", "am", "pull", "switch",
          "checkout", "worktree"}


def _tokens(elts) -> list:
    out = []
    for e in elts:
        if isinstance(e, ast.Constant) and isinstance(e.value, str):
            out.append(e.value)
        elif isinstance(e, ast.Starred):
            out.append("*" + ast.unparse(e.value))
        else:
            out.append(None)
    return out


def _hooked(toks: list) -> bool:
    i = 0
    while i < len(toks):
        t = toks[i]
        if t in ("-C", "-c"):
            i += 2
        elif t is None or t.startswith(("-", "*")):
            i += 1
        else:
            break
    if i >= len(toks) or toks[i] not in HOOKED:
        return False
    sub, rest = toks[i], toks[i + 1:]
    if sub == "worktree":
        return bool(rest) and rest[0] == "add"
    if sub == "checkout":
        return "--" not in rest
    if sub == "merge":
        return not (rest and rest[0] == "--abort")
    return True


def _python_offenders() -> list[str]:
    bad = []
    for n in ast.walk(ast.parse(LEERIE.read_text())):
        toks = None
        if (isinstance(n, ast.List) and n.elts
                and isinstance(n.elts[0], ast.Constant)
                and n.elts[0].value == "git"):
            toks = _tokens(n.elts[1:])
        elif (isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
              and n.func.id == "_git"):
            toks = _tokens(n.args)
        if toks is None or not _hooked(toks):
            continue
        if not any(t and ("GIT_NO_HOOKS" in t or "core.hooksPath" in t)
                   for t in toks):
            bad.append(f"leerie.py:{n.lineno}")
    return bad


_SHELL_HOOKED = re.compile(
    r"\bgit\b.*\b(merge|commit|cherry-pick|rebase|pull|switch"
    r"|worktree\s+add|checkout)\b")


def _shell_offenders() -> list[str]:
    bad = []
    for name in SCRIPTS:
        lines = (REPO_ROOT / "scripts" / name).read_text().splitlines()
        for i, line in enumerate(lines, 1):
            code = line.split("#", 1)[0] if line.lstrip().startswith(
                "#") else line
            if not code.strip() or code.lstrip().startswith(("echo",
                                                             "printf")):
                continue
            m = _SHELL_HOOKED.search(code)
            if not m or re.search(r"merge-base|checkout\s+--\s", code):
                continue
            if not re.search(r"core\.hooksPath|LEERIE_GIT_NO_HOOKS", code):
                bad.append(f"{name}:{i}")
    return bad


def test_every_hook_firing_git_command_of_leeries_runs_hooks_off():
    assert _python_offenders() == []
    assert _shell_offenders() == []


def test_the_guard_finds_what_it_must():
    """The converse: the scanners flag a hook-firing argv without the flag
    and pass one with it."""
    src = ("a = ['git', 'commit', '-m', 'x']\n"
           "b = ['git', *GIT_NO_HOOKS, 'commit', '-m', 'x']\n"
           "c = ['git', 'checkout', '--', 'p']\n"
           "d = ['git', '-C', p, 'worktree', 'add', w]\n")
    flagged = []
    for n in ast.walk(ast.parse(src)):
        if (isinstance(n, ast.List) and n.elts
                and isinstance(n.elts[0], ast.Constant)
                and n.elts[0].value == "git"):
            toks = _tokens(n.elts[1:])
            if _hooked(toks) and not any(
                    t and "GIT_NO_HOOKS" in t for t in toks):
                flagged.append(n.lineno)
    assert flagged == [1, 4]
    assert _SHELL_HOOKED.search('git merge --no-ff "$B"')
    assert not re.search(r"core\.hooksPath",
                         'git merge --no-ff "$B"')


def test_the_flag_really_turns_a_hook_off(tmp_path):
    def git(*a):
        return subprocess.run(["git", *a], cwd=tmp_path, check=True,
                              capture_output=True, text=True)
    git("init", "-q", "-b", "main")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    hooks = tmp_path / ".hooks"
    hooks.mkdir()
    for h in ("post-commit", "post-checkout"):
        (hooks / h).write_text(f"#!/bin/sh\necho x >> {tmp_path}/{h}.ran\n")
        (hooks / h).chmod(0o755)
    git("config", "core.hooksPath", str(hooks))
    git("-c", "core.hooksPath=/dev/null", "commit", "-q", "--allow-empty",
        "-m", "a")
    git("-c", "core.hooksPath=/dev/null", "checkout", "-q", "-b", "x")
    assert not list(tmp_path.glob("*.ran"))
    git("commit", "-q", "--allow-empty", "-m", "b")      # the control
    assert (tmp_path / "post-commit.ran").is_file()
