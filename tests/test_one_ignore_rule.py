"""Review round 17 H3: one ignore rule (DESIGN §8 *One ignore rule*).

I1 read "ignored" as the tree's own `.gitignore` files and I2 as git's
standard rules, so a `__pycache__/` only the developer's global ignore
covered — written by leerie's own test run — was kept by I2 and dirt to I1,
and the staging-clean assertion (since removed) stopped the run. The round's driver
(review-r17-proofs/exec/test_zz_r17e_i1_i2_ignore_rules_disagree), rewritten
to assert the fix; and a guard derived from the source that no listing of
untracked files exists outside the one rule, in Python or in the shell
leerie runs.
"""
from __future__ import annotations

import ast
import asyncio
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LEERIE_PY = ROOT / "orchestrator" / "leerie.py"
LIB = ROOT / "scripts" / "worktree-lib.sh"
RUN_ID = "r17ignore000"
BRANCH = f"leerie/runs/{RUN_ID}"


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


class _St:
    def __init__(self, user: Path, run_dir: Path):
        self.repo_root = user
        self.run_id = RUN_ID
        self.run_dir = run_dir
        self.data: dict = {"working_branch": "main"}

    def save(self) -> None:
        pass


@pytest.fixture()
def staging(leerie, tmp_path, monkeypatch):
    # The developer's global ignore, as the launcher mounts it.
    xdg = tmp_path / "xdg"
    (xdg / "git").mkdir(parents=True)
    (xdg / "git" / "ignore").write_text("__pycache__/\n")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "nogitconfig"))
    user = tmp_path / "user"
    (user / "pkg").mkdir(parents=True)
    _git(user, "init", "-q", "-b", "main")
    (user / "pkg" / "m.py").write_text("X = 1\n")
    (user / ".gitignore").write_text(".venv/\n")       # no __pycache__
    _git(user, "add", "-A")
    _git(user, "-c", "user.email=t@x", "-c", "user.name=t", "commit", "-qm",
         "base")
    run_dir = tmp_path / "runs" / RUN_ID
    run_dir.mkdir(parents=True)
    repo = leerie._ensure_run_repo(_St(user, run_dir))
    _git(repo, "branch", BRANCH, "main")
    stg = run_dir / "worktrees" / "staging"
    _git(repo, "worktree", "add", "-q", str(stg), BRANCH)
    return stg


def test_i1_and_i2_agree_on_leeries_own_test_output(leerie, staging):
    assert asyncio.run(leerie._tree_dirt(staging)) == []

    async def leerie_runs_the_test_axis() -> dict:
        async with leerie._leaves_tree_as_found(staging, "test", "r17e") \
                as rec:
            (staging / "pkg" / "__pycache__").mkdir()
            (staging / "pkg" / "__pycache__" / "m.cpython-313.pyc"
             ).write_bytes(b"\x00pyc")
            (staging / "junit.xml").write_text("<x/>\n")
        return rec

    rec = asyncio.run(leerie_runs_the_test_axis())
    # Ignored for both: I2 keeps it as environment, I1 does not read it.
    assert (staging / "pkg" / "__pycache__").is_dir()
    assert rec["removed"] == ["junit.xml"]           # not ignored: removed
    assert asyncio.run(leerie._tree_dirt(staging)) == []


def test_the_shell_listing_is_the_python_one(leerie, staging):
    (staging / "pkg" / "__pycache__").mkdir()
    (staging / "pkg" / "__pycache__" / "m.pyc").write_bytes(b"x")
    (staging / "new dir").mkdir()
    (staging / "new dir" / "f.txt").write_text("x\n")
    (staging / "loose.py").write_text("x\n")
    for kw, arg in (({}, []), ({"directory": True}, ["--directory"])):
        sh = subprocess.run(
            ["bash", "-c", '. "$1"; shift; leerie_untracked "$@"', "_",
             str(LIB), str(staging), *arg], capture_output=True)
        assert sh.returncode == 0, sh.stderr
        assert sorted(p for p in sh.stdout.decode().split("\0") if p) \
            == sorted(leerie._untracked_listing(staging, **kw))
    assert sorted(leerie._untracked_listing(staging)) == [
        "loose.py", "new dir/f.txt"]


# --- the derived guard: no other listing exists ----------------------------

_EXCLUDE_OPTS = ("--others", "--exclude-standard", "--exclude-from",
                 "--exclude-per-directory", "--ignored")


def _enclosing_function(tree: ast.AST) -> dict[int, str]:
    """line -> the name of the innermost function defining it."""
    owner: dict[int, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for line in range(node.lineno, (node.end_lineno or 0) + 1):
                # Inner functions overwrite outer ones (walk is BFS).
                owner[line] = node.name
    return owner


def _strings(node: ast.AST) -> list[str]:
    return [n.value for n in ast.walk(node)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)]


def test_python_lists_untracked_files_only_through_the_rule():
    """Every string that makes a git listing of untracked files — an
    `ls-files` untracked or exclude option — is in `_untracked_cmd`, and
    every argv that runs `git status --porcelain` says
    `--untracked-files=no` (status would list untracked files under its
    own reading of the ignore rules)."""
    src = LEERIE_PY.read_text()
    tree = ast.parse(src)
    owner = _enclosing_function(tree)
    stray = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                and node.value.startswith(_EXCLUDE_OPTS)
                and owner.get(node.lineno) != "_untracked_cmd"):
            stray.append((node.lineno, node.value))
    assert stray == []
    statuses = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.List, ast.Tuple, ast.Call)):
            elts = (node.elts if not isinstance(node, ast.Call)
                    else node.args)
            vals = [e.value for e in elts
                    if isinstance(e, ast.Constant)
                    and isinstance(e.value, str)]
            if "status" in vals and any(v.startswith("--porcelain")
                                        for v in vals):
                statuses.append((node.lineno,
                                 "--untracked-files=no" in vals))
    assert len(statuses) >= 6
    assert [ln for ln, ok in statuses if not ok] == []


def _sh_files() -> list[Path]:
    """worktree-lib.sh and every script that sources it: the shell leerie
    runs in a run's trees."""
    out = [LIB]
    for p in sorted((ROOT / "scripts").rglob("*.sh")):
        if p != LIB and re.search(r'^\s*(\.|source)\s+.*worktree-lib\.sh',
                                  p.read_text(), re.M):
            out.append(p)
    return out


def test_the_shell_lists_untracked_files_only_through_the_rule():
    files = _sh_files()
    assert {p.name for p in files} >= {
        "worktree-lib.sh", "setup-run.sh", "planning-worktree.sh",
        "collect-subtrees.sh"}
    stray = []
    for p in files:
        fn = ""
        for i, line in enumerate(p.read_text().splitlines(), 1):
            m = re.match(r"^([a-z_]+)\(\)\s*\{", line)
            if m:
                fn = m.group(1)
            code = line.split("#", 1)[0]
            if (re.search(r"--others|--exclude-standard|--exclude-per-directory"
                          r"|ls-files\s+(-\S+\s+)*-o\b", code)
                    and fn != "leerie_untracked"):
                stray.append(f"{p.name}:{i}")
            if (re.search(r"\bstatus\b.*--porcelain", code)
                    and "--untracked-files=no" not in code):
                stray.append(f"{p.name}:{i}")
    assert stray == []
