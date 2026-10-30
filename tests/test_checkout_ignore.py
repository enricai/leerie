"""Files leerie places in a checkout are git-ignored in THAT checkout only
(DESIGN §6 *Ignores are per checkout*).

A linked worktree's `info/exclude` is the repository's shared one: `git
rev-parse --git-path info/exclude` resolves to the common dir. The
falsifier's `_git_exclude` appended its leads and the task's referenced
files there and never removed them, so the report the user had written
(`docs/bug-report.md`, untracked) vanished from their own `git status`
for good. These tests are the review's driver (`exclude_leak.sh`) on real
git, in a linked worktree, plus the helper's refusals. (The delivery
re-drive no longer places anything in its checkout:
tests/test_redrive_selfcheck_out_of_tree.py.)
"""
from __future__ import annotations

import asyncio
import ast
import inspect
import subprocess
import types
from pathlib import Path

import pytest


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], check=True,
                          capture_output=True, text=True).stdout


def _user_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "user"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "a.txt").write_text("x\n")
    (repo / "src").mkdir()
    (repo / "src" / "a.ts").write_text("export const a = 1;\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    return repo


def _shared_exclude(repo: Path) -> str:
    ex = Path(_git(repo, "rev-parse", "--git-path", "info/exclude").strip())
    ex = ex if ex.is_absolute() else repo / ex
    return ex.read_text() if ex.is_file() else ""


def _linked(repo: Path, name: str) -> Path:
    wt = repo / ".leerie" / "runs" / "r" / "worktrees" / name
    _git(repo, "worktree", "add", "-q", "--detach", str(wt), "HEAD")
    return wt


def test_falsifier_leads_leave_the_users_checkout_alone(leerie, tmp_path):
    """The driver: the task names an untracked report; after the falsifier
    checkout is gone the user still sees it, and the shared exclude file
    is byte-for-byte what it was."""
    repo = _user_repo(tmp_path)
    (repo / ".gitignore").write_text(".leerie/\n")
    (repo / "docs").mkdir()
    (repo / "docs" / "bug-report.md").write_text("repro steps\n")
    before_status = _git(repo, "status", "--porcelain")
    before_ex = _shared_exclude(repo)
    assert "docs/" in before_status
    wt = _linked(repo, "falsifier")
    sdir = tmp_path / "set1"
    (sdir / "src").mkdir(parents=True)
    (sdir / "src" / "a.test.ts").write_text("t\n")
    st = types.SimpleNamespace(repo_root=repo)
    ctx, refs = leerie._falsifier_place_leads(
        st, wt, [{"valid": True, "dir": str(sdir), "index": 1,
                  "defect_files": ["src/a.test.ts"]}],
        "Fix the bug described in docs/bug-report.md")
    assert refs == ["docs/bug-report.md"]
    assert ctx == [f"{leerie.FALSIFIER_LEADS_DIR}/acceptance/set-1/"
                   "src/a.test.ts"]
    # Inside the checkout both are ignored...
    assert _git(wt, "status", "--porcelain", "--untracked-files=all") == ""
    # ...and survive the per-sample `clean -fd`.
    asyncio.run(leerie._restore_tree(
        wt, _git(wt, "rev-parse", "HEAD").strip(), where="t",
        branch=None))
    assert (wt / "docs" / "bug-report.md").is_file()
    # A second placement (the next sample) is idempotent.
    leerie._falsifier_place_leads(st, wt, [], "see docs/bug-report.md")
    assert (wt / "docs" / ".gitignore").read_text().count(
        "/bug-report.md") == 1
    _git(repo, "worktree", "remove", "--force", str(wt))
    assert _shared_exclude(repo) == before_ex
    assert _git(repo, "status", "--porcelain") == before_status


def test_a_directory_with_its_own_gitignore_is_never_edited(leerie,
                                                           tmp_path):
    repo = _user_repo(tmp_path)
    (repo / "src" / ".gitignore").write_text("*.log\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "gi")
    staging = _linked(repo, "staging")
    # Ignoring it would mean editing the tracked file: refused, untouched.
    assert leerie._checkout_ignore(staging, ["src/x.ts"]) == ["src/x.ts"]
    assert (staging / "src" / ".gitignore").read_text() == "*.log\n"
    assert leerie._checkout_ignore(staging, ["tests/b.ts"]) == []
    assert (staging / "tests" / ".gitignore").read_text().splitlines()[
        :1] == [leerie._CHECKOUT_IGNORE_TAG]


def test_names_are_literal_patterns(leerie, tmp_path):
    """A placed file's name is matched literally, never as a glob: `[id]`
    must not ignore the sibling `i.test.ts`."""
    repo = _user_repo(tmp_path)
    (repo / "src" / "[id].test.ts").write_text("t\n")
    (repo / "src" / "i.test.ts").write_text("t\n")
    assert leerie._checkout_ignore(repo, ["src/[id].test.ts"]) == []
    assert _git(repo, "status", "--porcelain",
                "--untracked-files=all").split() == ["??", "src/i.test.ts"]


def test_no_code_writes_the_shared_exclude_file(leerie):
    """Structural guard paired with the behavioural tests above: the only
    function that resolves `info/exclude` is the legacy path the resume
    uses to REMOVE an earlier version's block, and nothing appends to it."""
    tree = ast.parse(inspect.getsource(leerie))
    owners = sorted({
        f.name for f in ast.walk(tree)
        if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))
        for n in ast.walk(f)
        if isinstance(n, ast.Constant) and n.value == "info/exclude"})
    assert owners == ["_redrive_exclude_path"]
    assert not hasattr(leerie, "_git_exclude")
    clear = inspect.getsource(leerie._redrive_exclude_clear)
    assert '.open("a")' not in clear and "if not skip" in clear


@pytest.mark.parametrize("name", ["a b ", "x*y", "q?.ts", "back\\slash"])
def test_escaped_names_still_match(leerie, tmp_path, name):
    repo = _user_repo(tmp_path)
    (repo / "src" / name).write_text("t\n")
    leerie._checkout_ignore(repo, ["src/" + name])
    assert _git(repo, "status", "--porcelain",
                "--untracked-files=all") == ""
