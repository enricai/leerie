"""A data file's readers never displace a tree-reading guard (DESIGN §9).

A test whose imports reach no production file and that names no production
file at all (it lists the tree) is coupled to every change. That must still hold
when the change is a data file a reader names too, or the scoped run includes
the reader and drops the guard (barnacle #800, mode 1). These tests make the two
inputs disagree: a data-only change, and a data change plus a source change.
They drive the real `_coupled_test_files` and `_change_reach` over a real git
tree, with synthetic names."""
from __future__ import annotations

import pytest

from tests.conftest import run_git_out as _git

FILES = {
    "pkg/__init__.py": "",
    "pkg/widget.py": "W = 1\n",
    "data/table.json": '{"k": 1}\n',
    "docs/guide.md": "k\n",
    # Reads the data file by path, and imports source.
    "tests/test_reader.py": ('from pkg import widget\n'
                             'open("data/table.json").read()\n'),
    # Imports nothing, names no file: it walks the tracked tree.
    "tests/test_vocab_guard.py": ('import subprocess\n'
                                  'subprocess.run(["git", "ls-files"])\n'),
}


def _repo(tmp_path, change):
    root = tmp_path / "repo"
    for rel, text in FILES.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    _git(root, "init", "-q", "-b", "main")
    ident = ("-c", "user.email=t@t", "-c", "user.name=t")
    _git(root, *ident, "add", "-A")
    _git(root, *ident, "commit", "-qm", "base")
    for rel, text in change.items():
        (root / rel).write_text(text)
    _git(root, *ident, "commit", "-qam", "change")
    return root, _git(root, "rev-parse", "HEAD")


@pytest.mark.parametrize("changed", [
    ["data/table.json"],
    ["data/table.json", "pkg/widget.py"],
    ["docs/guide.md"],
])
def test_tree_guard_stays_coupled_beside_a_data_reader(leerie, tmp_path,
                                                       changed):
    edits = {p: FILES[p] + ("\n" if p.endswith((".md", ".py")) else "")
             for p in changed}
    edits = {p: (v if v != FILES[p] else v + " ") for p, v in edits.items()}
    root, head = _repo(tmp_path, edits)
    leerie._SOURCE_GRAPHS.clear()
    got = leerie._coupled_test_files(root, head, changed)
    assert got.get("tests/test_vocab_guard.py") == sorted(changed)
    if "data/table.json" in changed:
        assert "data/table.json" in got.get("tests/test_reader.py", [])
    reached, _unknown = leerie._change_reach(root, head, changed)
    assert "tests/test_vocab_guard.py" in reached
