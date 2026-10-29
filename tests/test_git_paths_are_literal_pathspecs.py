"""Review round 11 L1: every git invocation in leerie.py that passes a path
after `--` passes it as a literal pathspec (DESIGN §6½ *Leerie's own
commands leave a worker's tree as they found it*: "every path goes to git
as a literal pathspec").

Without `--literal-pathspecs`, git reads `app/[slug]/page.tsx` as a glob
that also matches `app/s/page.tsx`. Round 11 drove two of the remaining
sites: `_try_json_key_merge`'s `git add -- cfg/[ab]/x.json` also staged an
untracked `cfg/a/x.json` into the merge commit, and `_changed_line_numbers
(…, "app/[slug]/p.ts")` returned a line of `app/s/p.ts`, a file the path
does not name. The guard is a source scan so a new call site cannot skip
the rule; the substance tests below drive the two sites with real git."""
from __future__ import annotations

import ast
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
from test_redrive_test_id_diff_refresh import _git  # noqa: E402

SRC = Path(__file__).resolve().parent.parent / "orchestrator" / "leerie.py"
# Wrappers whose positional args after the first are git's argv.
GIT_WRAPPERS = {"_redrive_git", "_git_text", "_git_out"}
# Call sites whose pathspecs are deliberately not literal, by enclosing
# function, each with its reason. Anything else that passes a non-constant
# path after `--` must say `--literal-pathspecs`.
DELIBERATE = {
    "_node_lockfile_paths": "explicit `:(glob)` magic",
    "_prisma_project": "explicit `:(glob)` magic",
    "_d1_untouched_verdict": "chooses `:(glob)` per path itself",
    "_verify_delivery_cite": "`ABSENT:<pathspec>` is a git pathspec by "
                             "contract (prompts/delivery_judge.md)",
    "_conflicted_area_tests": "`git grep -- *.py` globs on purpose",
}


def _git_argvs(tree: ast.AST):
    """(enclosing function, line, argv elements) of every git invocation."""
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for node in ast.walk(fn):
            if isinstance(node, ast.List) and node.elts and isinstance(
                    node.elts[0], ast.Constant) and node.elts[0].value == "git":
                yield fn.name, node.lineno, node.elts
            elif (isinstance(node, ast.Call)
                  and isinstance(node.func, ast.Name)
                  and node.func.id in GIT_WRAPPERS and node.args):
                yield fn.name, node.lineno, node.args[1:]


def _offenders(source: str) -> list[str]:
    tree = ast.parse(source)
    seen: set[tuple[str, int]] = set()
    out = []
    for fn, line, elts in _git_argvs(tree):
        consts = [e.value if isinstance(e, ast.Constant) else None
                  for e in elts]
        if "--" not in consts or (fn, line) in seen:
            continue
        seen.add((fn, line))
        after = elts[consts.index("--") + 1:]
        if all(isinstance(e, ast.Constant) for e in after):
            continue            # `checkout -- .`: nothing variable
        if "--literal-pathspecs" in consts or fn in DELIBERATE:
            continue
        out.append(f"{fn}:{line}")
    return sorted(out)


def test_every_variable_path_after_dashdash_is_literal():
    assert _offenders(SRC.read_text()) == []


def test_the_scan_catches_a_glob_path_and_reads_wrappers():
    """The guard's own falsification: a list-literal call, a wrapper call
    and an f-string path are each caught; a constant `.` and a call that
    says `--literal-pathspecs` are not."""
    src = (
        "def a(p):\n    run(['git', 'add', '--', p])\n"
        "def b(c, p):\n    _git_text(c, 'diff', 'x', '--', p)\n"
        "def c(t, p):\n    run(['git', '-C', t, 'diff', '--', f'{p}/x'])\n"
        "def d():\n    run(['git', 'checkout', '--', '.'])\n"
        "def e(p):\n    run(['git', '--literal-pathspecs', 'add', '--', p])\n")
    assert _offenders(src) == ["a:2", "b:4", "c:6"]


def test_every_deliberate_site_still_exists():
    """An allowlist entry for a function that no longer passes a variable
    path is dead weight that would hide a future offender."""
    tree = ast.parse(SRC.read_text())
    names = {fn for fn, _l, elts in _git_argvs(tree)
             if "--" in [e.value if isinstance(e, ast.Constant) else None
                         for e in elts]}
    assert set(DELIBERATE) <= names


def _init(s):
    s.mkdir(parents=True)
    _git(s, "init", "-q", "-b", "main")
    _git(s, "config", "user.email", "t@t")
    _git(s, "config", "user.name", "t")


def test_json_key_merge_commits_only_the_conflicted_path(leerie, tmp_path):
    """The reviewer's driver: before, the merge commit also held the
    untracked `cfg/a/x.json` the bracket glob matched."""
    s = tmp_path / "s"
    _init(s)
    f = s / "cfg" / "[ab]" / "x.json"
    f.parent.mkdir(parents=True)
    f.write_text(json.dumps({"k": 1}, indent=2) + "\n")
    _git(s, "add", "-A")
    _git(s, "commit", "-qm", "base")
    _git(s, "checkout", "-qb", "other")
    f.write_text(json.dumps({"k": 1, "o": 2}, indent=2) + "\n")
    _git(s, "commit", "-qam", "other")
    _git(s, "checkout", "-q", "main")
    f.write_text(json.dumps({"k": 1, "m": 3}, indent=2) + "\n")
    _git(s, "commit", "-qam", "main")
    (s / "cfg" / "a").mkdir()
    (s / "cfg" / "a" / "x.json").write_text('{"leftover": true}\n')
    subprocess.run(["git", "merge", "other"], cwd=s, capture_output=True)
    got = asyncio.run(leerie._try_json_key_merge(s, "st1"))
    assert got == ["cfg/[ab]/x.json"]
    files = _git(s, "show", "--name-only", "--format=", "-m",
                 "--first-parent", "HEAD").split()
    assert files == ["cfg/[ab]/x.json"]
    assert json.loads(_git(s, "show", "HEAD:cfg/[ab]/x.json")) == {
        "k": 1, "o": 2, "m": 3}
    assert "?? cfg/a/" in _git(s, "status", "--porcelain")


def test_changed_line_numbers_read_only_the_named_file(leerie, tmp_path):
    """Before: `[41]`, a line of `app/s/p.ts`, for an untouched
    five-line `app/[slug]/p.ts`."""
    s = tmp_path / "s"
    _init(s)
    (s / "app" / "[slug]").mkdir(parents=True)
    (s / "app" / "s").mkdir(parents=True)
    (s / "app" / "[slug]" / "p.ts").write_text("a\n" * 5)
    (s / "app" / "s" / "p.ts").write_text("a\n" * 50)
    _git(s, "add", "-A")
    _git(s, "commit", "-qm", "base")
    base = _git(s, "rev-parse", "HEAD")
    (s / "app" / "s" / "p.ts").write_text("a\n" * 40 + "b\n" + "a\n" * 9)
    (s / "app" / "[slug]" / "p.ts").write_text("a\n" * 2 + "c\n" + "a\n" * 2)
    _git(s, "commit", "-qam", "touch both")
    assert asyncio.run(leerie._changed_line_numbers(
        s, base, "app/[slug]/p.ts")) == [3]
