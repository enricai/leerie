"""Git `-z` path output is read in bytes and decoded as the filesystem does
(`_git_z_output`), never with `text=True` (review r2 L2: a non-UTF-8 file
name raised UnicodeDecodeError out of `_settle_by_execution`).

Substance: each reader, on a real repo holding a non-UTF-8 name, returns
names that open the files they came from. Structure: no `-z` git call in
leerie.py decodes as text."""
from __future__ import annotations

import ast
import os
import subprocess
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "orchestrator" / "leerie.py"
BAD = b"caf\xe9"


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True,
                          capture_output=True).stdout.decode(
                              errors="replace").strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "r"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    _git(r, "config", "user.email", "t@x")
    _git(r, "config", "user.name", "t")
    d = bytes(r) + b"/" + BAD
    os.mkdir(d)
    for name, body in ((b"/package-lock.json", b"{}"),
                       (b"/schema.prisma", b"datasource db {}\n"),
                       (b"/m.ts", b"export const x = 1;\n")):
        with open(d + name, "wb") as f:
            f.write(body)
    with open(d + b"/package.json", "wb") as f:
        f.write(b'{"dependencies": {"prisma": "5"}}')
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "base")
    with open(bytes(r) + b"/" + BAD + b".txt", "wb") as f:
        f.write(b"untracked")
    return r


def _opens(root: Path, rel: str) -> bool:
    return (root / rel).exists()


def test_untracked_files(leerie, repo):
    got = leerie._untracked_files(repo)
    assert got == {"caf\udce9.txt"} and _opens(repo, "caf\udce9.txt")


def test_paths_at_a_commit(leerie, repo):
    leerie._PATHS_AT.clear()
    got = leerie._git_paths_at(repo, _git(repo, "rev-parse", "HEAD"))
    assert "caf\udce9/m.ts" in got
    assert all(_opens(repo, p) for p in got)


def test_tree_sources(leerie, repo):
    src, allp = leerie._git_tree_sources(repo, "HEAD")
    assert "caf\udce9/m.ts" in src and "caf\udce9/m.ts" in allp


def test_node_lockfiles_and_prisma(leerie, repo):
    assert leerie._node_lockfile_paths(repo) == ["caf\udce9/package-lock.json"]
    got = leerie._prisma_project(repo)
    assert got is not None and _opens(repo, got["schema"])


def test_acceptance_new_files(leerie, repo):
    got = leerie._acceptance_new_files(repo, set(), [])
    assert got == ["caf\udce9.txt"]


def test_no_z_git_call_decodes_as_text():
    """Every `subprocess.run` of a git argv carrying `-z` goes through
    `_git_z_output` (bytes) instead of `text=True`/`encoding=`."""
    bad = []
    for n in ast.walk(ast.parse(SRC.read_text())):
        if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and isinstance(n.func.value, ast.Name)
                and n.func.value.id == "subprocess" and n.args):
            continue
        argv = n.args[0]
        words = [e.value for e in getattr(argv, "elts", [])
                 if isinstance(e, ast.Constant)]
        text = any(k.arg in ("text", "universal_newlines", "encoding")
                   and not (isinstance(k.value, ast.Constant)
                            and k.value.value in (False, None))
                   for k in n.keywords)
        if "git" in words[:1] and "-z" in words and text:
            bad.append(n.lineno)
    assert bad == []
