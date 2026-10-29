"""Review round 9 M2 and L2: a re-drive group's leftovers commit takes
only what the group's implementer left (DESIGN §8 *Delivery re-drive*).

Before: `_redrive_commit_leftovers` ran `git add -u` (every tracked edit,
whenever made) plus every untracked file not listed before the group. A
new `.pyc` from the implementer's own pytest run shipped (M2a), an install
churn that predated the group shipped and moved HEAD on a group that
changed nothing (M2b), and a file the implementer finished from an
untracked one the final conformer left was never committed (L2). Real git,
real pytest, a real per-worker log in the CLI's stream-json shape."""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(__file__))
from test_redrive_test_id_diff_refresh import _git  # noqa: E402


def _staging(tmp_path, monkeypatch):
    monkeypatch.delenv("PYTHONDONTWRITEBYTECODE", raising=False)
    s = tmp_path / "s"
    (s / "tests").mkdir(parents=True)
    _git(s, "init", "-q", "-b", "main")
    _git(s, "config", "user.email", "t@t")
    _git(s, "config", "user.name", "t")
    (s / "tests" / "test_a.py").write_text("def test_a():\n    assert 1\n")
    (s / "uv.lock").write_text("version = 1\n")
    (s / "mod.py").write_text("X = 1\n")
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


def _leftovers(leerie, s, before, written):
    return asyncio.run(leerie._redrive_commit_leftovers(
        s, "r1 g0", before, written))


def test_a_pyc_from_the_implementers_own_test_run_is_not_committed(
        leerie, tmp_path, monkeypatch):
    """M2a, the reviewer's driver: the implementer writes a test, runs
    pytest (a new `.pyc` appears, listed file by file) and commits only its
    test. Before, the `.pyc` shipped in a leftovers commit."""
    s = _staging(tmp_path, monkeypatch)
    before = leerie._tracked_content(s)
    (s / "tests" / "test_new.py").write_text("def test_n():\n    assert 1\n")
    _pytest(s)
    assert any(f.endswith(".pyc") and "test_new" in f
               for f in leerie._untracked_files(s))      # the precondition
    _git(s, "add", "tests/test_new.py")
    _git(s, "commit", "-qm", "implementer: add test")
    head = _head(s)
    written = leerie._worker_written_paths(
        _log(tmp_path, s, ("Write", "tests/test_new.py")), s)
    assert _leftovers(leerie, s, before, written) == []
    assert _head(s) == head


def test_a_tracked_edit_that_predates_the_group_is_not_committed(
        leerie, tmp_path, monkeypatch):
    """M2b, the reviewer's driver: an earlier command left `uv.lock`
    edited and the implementer changes nothing. Before, it shipped and HEAD
    moved, so the group read `no_change: False`."""
    s = _staging(tmp_path, monkeypatch)
    (s / "uv.lock").write_text("version = 1\nrevision = 2\n")
    before = leerie._tracked_content(s)
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
    left a `.pyc`. Every edit of its own ships; the `.pyc` does not."""
    s = _staging(tmp_path, monkeypatch)
    (s / "uv.lock").write_text("version = 1\nrevision = 2\n")
    before = leerie._tracked_content(s)
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
    assert not any(f.endswith(".pyc") for f in _files_of_head(s))


def test_a_file_the_implementer_finished_from_an_untracked_one_ships(
        leerie, tmp_path, monkeypatch):
    """L2: the final conformer left `helper.py` untracked; the group's
    implementer edits it and does not commit. Before, it was untracked
    before the group and so never committed — the shipping tree lacked a
    file the held-out remeasure had seen."""
    s = _staging(tmp_path, monkeypatch)
    (s / "helper.py").write_text("H = 0\n")       # the conformer's leftover
    before = leerie._tracked_content(s)
    (s / "helper.py").write_text("H = 1\n")
    written = leerie._worker_written_paths(
        _log(tmp_path, s, ("Edit", "helper.py")), s)
    assert _leftovers(leerie, s, before, written) == ["helper.py"]
    assert _git(s, "show", "HEAD:helper.py") == "H = 1"


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
