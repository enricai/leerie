"""Review round 9 M2 and L2, round 10 M1: a re-drive group's leftovers
commit takes what the group's implementer left (DESIGN §8 *A re-drive
group's leftovers are its implementer's*).

Round 9: `_redrive_commit_leftovers` ran `git add -u` (every tracked edit,
whenever made) plus every untracked file not listed before the group, so an
install churn that predated the group shipped and moved HEAD on a group
that changed nothing (M2b), and a file the implementer finished from an
untracked one the final conformer left was never committed (L2). Its fix
took an untracked file only when the implementer's Write/Edit tool calls
named it, and round 10 M1 drove what that dropped: a plain `mv` shipped the
deletion without the new file. Now every untracked file that appeared or
changed while the group ran is taken, less what leerie's own commands
produced; the tool calls are an extra source. Real git, real pytest, a real
per-worker log in the CLI's stream-json shape."""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))
from test_redrive_test_id_diff_refresh import _git  # noqa: E402


def _staging(tmp_path, monkeypatch, ignore=""):
    monkeypatch.delenv("PYTHONDONTWRITEBYTECODE", raising=False)
    s = tmp_path / "s"
    (s / "tests").mkdir(parents=True)
    _git(s, "init", "-q", "-b", "main")
    _git(s, "config", "user.email", "t@t")
    _git(s, "config", "user.name", "t")
    (s / "tests" / "test_a.py").write_text("def test_a():\n    assert 1\n")
    (s / "uv.lock").write_text("version = 1\n")
    (s / "mod.py").write_text("X = 1\n")
    if ignore:
        (s / ".gitignore").write_text(ignore)
    _git(s, "add", "-A")
    _git(s, "commit", "-qm", "base")
    # The re-drive's own earlier measurement: existing artifacts.
    _pytest(s)
    return s


def _pytest(s):
    subprocess.run([sys.executable, "-m", "pytest", "-q", "-p",
                    "no:cacheprovider", "tests"], cwd=s, capture_output=True)


def _log(tmp_path, s, *writes: tuple[str, str]):
    """A per-worker log holding one tool_use per (tool, path)."""
    lines = []
    for i, (tool, rel) in enumerate(writes):
        key = "notebook_path" if tool == "NotebookEdit" else "file_path"
        lines.append(json.dumps({"type": "assistant", "message": {
            "content": [{"type": "tool_use", "id": f"t{i}", "name": tool,
                         "input": {key: str(s / rel)}}]}}))
    p = tmp_path / "w.log"
    p.write_text("\n".join(lines) + "\n")
    return p


def _head(s):
    return _git(s, "rev-parse", "HEAD")


def _files_of_head(s):
    return sorted(_git(s, "show", "--name-only", "--format=",
                       "HEAD").split())


def _before(leerie, s):
    return leerie._tracked_content(s), leerie._untracked_signatures(s)


def _leftovers(leerie, s, before, written):
    return asyncio.run(leerie._redrive_commit_leftovers(
        s, "r1 g0", *before, written))


@pytest.mark.parametrize("ignore", ["__pycache__/\n", ""])
def test_a_pyc_from_the_implementers_own_test_run_follows_the_repos_ignore(
        leerie, tmp_path, monkeypatch, ignore):
    """M2a, the reviewer's driver: the implementer writes a test, runs
    pytest (a new `.pyc` appears) and commits only its test. A repository
    that ignores `__pycache__/` never sees the `.pyc`; one that does not
    gets it committed — what its own `git add -A` would do, since a shell
    command made it and no file name tells an artifact from a source file
    (DESIGN §8)."""
    s = _staging(tmp_path, monkeypatch, ignore)
    before = _before(leerie, s)
    (s / "tests" / "test_new.py").write_text("def test_n():\n    assert 1\n")
    _pytest(s)
    _git(s, "add", "tests/test_new.py")
    _git(s, "commit", "-qm", "implementer: add test")
    head = _head(s)
    written = leerie._worker_written_paths(
        _log(tmp_path, s, ("Write", "tests/test_new.py")), s)
    got = _leftovers(leerie, s, before, written)
    if ignore:
        assert got == [] and _head(s) == head
    else:
        assert got and all(f.endswith(".pyc") and "test_new" in f
                           for f in got)
        # The earlier measurement's `.pyc` predates the group: not taken.
        assert not any("test_a" in f for f in got)


def test_a_tracked_edit_that_predates_the_group_is_not_committed(
        leerie, tmp_path, monkeypatch):
    """M2b, the reviewer's driver: an earlier command left `uv.lock`
    edited and the implementer changes nothing. Before, it shipped and HEAD
    moved, so the group read `no_change: False`."""
    s = _staging(tmp_path, monkeypatch)
    (s / "uv.lock").write_text("version = 1\nrevision = 2\n")
    before = _before(leerie, s)
    head = _head(s)
    assert _leftovers(leerie, s, before, set()) == []
    assert _head(s) == head                       # no_change holds
    assert " M uv.lock" in subprocess.run(
        ["git", "status", "--porcelain"], cwd=s, capture_output=True,
        text=True).stdout                         # left as it was


def test_what_the_implementer_left_is_committed_and_only_that(
        leerie, tmp_path, monkeypatch):
    """The implementer edits a tracked file (and a pre-dirty one further),
    writes a new file, deletes one, and commits none of it; a command it ran
    left a `.pyc` the repository ignores. Every edit of its own ships."""
    s = _staging(tmp_path, monkeypatch, "__pycache__/\n")
    (s / "uv.lock").write_text("version = 1\nrevision = 2\n")
    before = _before(leerie, s)
    (s / "mod.py").write_text("X = 2\n")
    (s / "uv.lock").write_text("version = 1\nrevision = 3\n")
    (s / "tests" / "test_a.py").unlink()
    (s / "new.py").write_text("N = 1\n")
    (s / "tests" / "test_b.py").write_text("def test_b():\n    assert 1\n")
    _pytest(s)
    written = leerie._worker_written_paths(_log(
        tmp_path, s, ("Edit", "mod.py"), ("Write", "new.py"),
        ("Write", "tests/test_b.py")), s)
    got = _leftovers(leerie, s, before, written)
    assert got == ["mod.py", "new.py", "tests/test_a.py", "tests/test_b.py",
                   "uv.lock"]
    assert _files_of_head(s) == got


def test_a_file_the_implementer_finished_from_an_untracked_one_ships(
        leerie, tmp_path, monkeypatch):
    """L2: the final conformer left `helper.py` untracked; the group's
    implementer edits it and does not commit. Before, it was untracked
    before the group and so never committed — the shipping tree lacked a
    file the held-out remeasure had seen."""
    s = _staging(tmp_path, monkeypatch)
    (s / "helper.py").write_text("H = 0\n")       # the conformer's leftover
    before = _before(leerie, s)
    (s / "helper.py").write_text("H = 1\n")
    written = leerie._worker_written_paths(
        _log(tmp_path, s, ("Edit", "helper.py")), s)
    assert _leftovers(leerie, s, before, written) == ["helper.py"]
    assert _git(s, "show", "HEAD:helper.py") == "H = 1"


def test_a_bash_mv_ships_the_new_file_with_the_deletion(leerie, tmp_path,
                                                         monkeypatch):
    """Round 10 M1(a), the reviewer's driver: `mv util.py helpers.py`
    through Bash and an Edit of the import, nothing committed. Before, the
    commit held the deletion and the edit but not `helpers.py`, so a
    checkout of HEAD could not import `app` while staging still could."""
    s = _staging(tmp_path, monkeypatch, "__pycache__/\n")
    (s / "util.py").write_text("def f():\n    return 1\n")
    (s / "app.py").write_text("from util import f\n")
    _git(s, "add", "-A")
    _git(s, "commit", "-qm", "app")
    before = _before(leerie, s)
    os.rename(s / "util.py", s / "helpers.py")
    (s / "app.py").write_text("from helpers import f\n")
    written = leerie._worker_written_paths(
        _log(tmp_path, s, ("Edit", "app.py")), s)
    assert _leftovers(leerie, s, before, written) == [
        "app.py", "helpers.py", "util.py"]
    co = tmp_path / "co"
    _git(s, "worktree", "add", "-q", "--detach", str(co), "HEAD")
    assert subprocess.run([sys.executable, "-c", "import app"], cwd=co,
                          capture_output=True).returncode == 0


def test_a_heredoc_module_ships(leerie, tmp_path, monkeypatch):
    """Round 10 M1(b): `cat > extra.py` and an Edit importing it. Before,
    only `app.py` shipped and `extra.py` stayed untracked."""
    s = _staging(tmp_path, monkeypatch)
    (s / "app.py").write_text("A = 1\n")
    _git(s, "add", "-A")
    _git(s, "commit", "-qm", "app")
    before = _before(leerie, s)
    (s / "extra.py").write_text("Y = 1\n")
    (s / "app.py").write_text("from extra import Y\n")
    written = leerie._worker_written_paths(
        _log(tmp_path, s, ("Edit", "app.py")), s)
    assert _leftovers(leerie, s, before, written) == ["app.py", "extra.py"]


def test_what_leerie_produced_and_what_predates_the_group_stay(
        leerie, tmp_path, monkeypatch, capsys):
    """A report leerie's own wrapped command writes during the group is
    leerie's and never ships; a worker file that predates the group and
    that the group did not touch is not taken either — but it is logged,
    never dropped silently."""
    s = _staging(tmp_path, monkeypatch, "__pycache__/\n")
    (s / "notes.txt").write_text("the final conformer's\n")
    before = _before(leerie, s)
    (s / "new.py").write_text("N = 1\n")              # through Bash

    async def leerie_run():
        async with leerie._leaves_tracked_files(s, "test run", "t"):
            (s / "junit.xml").write_text("<testsuite/>")
    asyncio.run(leerie_run())
    assert _leftovers(leerie, s, before, set()) == ["new.py"]
    out = capsys.readouterr().out
    assert "left uncommitted: notes.txt" in out
    assert "junit.xml" not in out


def test_a_bracketed_path_is_a_literal_pathspec(leerie, tmp_path,
                                                monkeypatch):
    """`git add -- app/[slug]/page.tsx` read the brackets as a glob and also
    staged `app/s/page.tsx`, a file the group never touched."""
    s = _staging(tmp_path, monkeypatch)
    (s / "app" / "s").mkdir(parents=True)
    (s / "app" / "s" / "page.tsx").write_text("other\n")   # predates
    before = _before(leerie, s)
    (s / "app" / "[slug]").mkdir()
    (s / "app" / "[slug]" / "page.tsx").write_text("new\n")
    assert _leftovers(leerie, s, before, set()) == ["app/[slug]/page.tsx"]
    assert _files_of_head(s) == ["app/[slug]/page.tsx"]


def test_written_paths_come_only_from_write_tools_inside_the_tree(
        leerie, tmp_path):
    s = tmp_path / "s"
    (s / "sub").mkdir(parents=True)
    log = tmp_path / "w.log"
    events = [
        {"name": "Write", "input": {"file_path": str(s / "a.py")}},
        {"name": "Edit", "input": {"file_path": str(s / "sub" / "b.py")}},
        {"name": "MultiEdit", "input": {"file_path": "c.py"}},
        {"name": "NotebookEdit", "input": {"notebook_path": str(s / "n.ipynb")}},
        {"name": "Write", "input": {"file_path": "/etc/elsewhere"}},
        {"name": "Write", "input": {"file_path": str(s / ".." / "out.py")}},
        {"name": "Read", "input": {"file_path": str(s / "read.py")}},
        {"name": "Bash", "input": {"command": "touch made.py"}},
    ]
    log.write_text("\n".join(json.dumps({"message": {"content": [
        {"type": "tool_use", "id": str(i), **e}]}})
        for i, e in enumerate(events)) + "\nnot json\n")
    assert leerie._worker_written_paths(log, s) == {
        "a.py", os.path.join("sub", "b.py"), "c.py", "n.ipynb"}
    assert leerie._worker_written_paths(tmp_path / "none.log", s) == set()
