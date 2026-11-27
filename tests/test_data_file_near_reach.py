"""Near tests and change reach see a test case reading a changed data file.

Drives the real `_near_test_files`, `_change_reach` and `_coupled_test_files`
over a real git tree (synthetic names): a test reading a changed data file,
also one importing production, is near and reached, the same rule as the
coupling; an unread data file reaches nothing and stays unknown.
"""
from __future__ import annotations

from tests.conftest import run_git_out as _git

FILES = {
    "pkg/__init__.py": "",
    "pkg/widget.py": "W = 1\n",
    "zeta/table.json": '{"k": 1}\n',
    "solo/orphan.json": '{"o": 1}\n',
    "tests/test_bare_reader.py": 'open("zeta/table.json").read()\n',
    "tests/test_import_reader.py": ('from pkg import widget\n'
                                    'open("zeta/table.json").read()\n'),
    "tests/test_plain.py": "from pkg import widget\n",
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


READERS = {"tests/test_bare_reader.py", "tests/test_import_reader.py"}


def test_near_includes_readers_of_changed_data(leerie, tmp_path):
    root, head = _repo(tmp_path, {"zeta/table.json": '{"k": 2}\n'})
    leerie._SOURCE_GRAPHS.clear()
    near = leerie._near_test_files(root, head, ["zeta/table.json"])
    assert READERS <= near
    assert "tests/test_plain.py" not in near


def test_reach_includes_readers_and_matches_coupling(leerie, tmp_path):
    root, head = _repo(tmp_path, {"zeta/table.json": '{"k": 2}\n'})
    leerie._SOURCE_GRAPHS.clear()
    reached, _unknown = leerie._change_reach(root, head, ["zeta/table.json"])
    coupled = leerie._coupled_test_files(root, head, ["zeta/table.json"])
    assert READERS <= reached
    assert READERS <= set(coupled)


def test_unread_data_file_reaches_nothing(leerie, tmp_path):
    root, head = _repo(tmp_path, {"solo/orphan.json": '{"o": 2}\n'})
    leerie._SOURCE_GRAPHS.clear()
    reached, unknown = leerie._change_reach(root, head, ["solo/orphan.json"])
    assert "solo/orphan.json" in unknown
    assert "tests/test_import_reader.py" not in reached
    near = leerie._near_test_files(root, head, ["solo/orphan.json"])
    assert "tests/test_import_reader.py" not in near
