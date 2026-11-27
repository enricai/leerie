"""A test case reading a changed data file is coupled to it (DESIGN §9).

Contract: a test coupled to a change includes every test case that reads a
changed file, whatever its kind and whatever it imports. These drive the real
`_coupled_test_files`, `_near_test_files`, `_change_reach` and `_measure_axes`
(only the command run itself stubbed) over a real git tree, in synthetic names.
"""
from __future__ import annotations

import asyncio

from tests.conftest import run_git_out as _git
from tests.test_forbidden_commands import SUITE, _measure_st, _stub_measure

FILES = {
    ".leerie/config.toml": 'test = "pnpm run test"\n',
    "vitest.config.ts": "export default {};\n",
    "src/widget.ts": "export const w = 1;\n",
    "pkg/__init__.py": "",
    "pkg/widget.py": "W = 1\n",
    "zeta/table.json": '{"k": 1}\n',
    "zeta/rules.yaml": "k: 1\n",
    "zeta/notes.txt": "k\n",
    # (a) imports source AND reads the data file by path (JS)
    "src/by-path.test.ts": ('import { w } from "./widget";\n'
                            'readFileSync("zeta/table.json");\n'),
    # (a) Python, imports source, reads yaml by path
    "tests/test_by_path.py": ('from pkg import widget\n'
                              'open("zeta/rules.yaml").read()\n'),
    # (b) imports nothing, reads plain text by path
    "tests/test_bare_reader.py": 'open("zeta/notes.txt").read()\n',
    # (c) directory segment plus a computed name
    "src/by-dir.test.ts": ('import { w } from "./widget";\n'
                           'readFileSync(join("zeta", name));\n'),
    # (d) imports source, never mentions the data
    "src/plain.test.ts": 'import { w } from "./widget";\n',
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
    base = _git(root, "rev-parse", "HEAD")
    for rel, text in change.items():
        (root / rel).write_text(text)
    _git(root, *ident, "commit", "-qam", "change")
    return root, base, _git(root, "rev-parse", "HEAD")


def test_json_reader_is_coupled_by_path_and_by_directory(leerie, tmp_path):
    root, _b, head = _repo(tmp_path, {"zeta/table.json": '{"k": 2}\n'})
    leerie._SOURCE_GRAPHS.clear()
    got = leerie._coupled_test_files(root, head, ["zeta/table.json"])
    assert got["src/by-path.test.ts"] == ["zeta/table.json"]
    assert got["src/by-dir.test.ts"] == ["zeta/table.json"]
    assert "src/plain.test.ts" not in got
    assert "tests/test_plain.py" not in got


def test_yaml_reader_python_importing_source_is_coupled(leerie, tmp_path):
    root, _b, head = _repo(tmp_path, {"zeta/rules.yaml": "k: 2\n"})
    leerie._SOURCE_GRAPHS.clear()
    got = leerie._coupled_test_files(root, head, ["zeta/rules.yaml"])
    assert got["tests/test_by_path.py"] == ["zeta/rules.yaml"]
    assert "tests/test_plain.py" not in got
    assert "src/plain.test.ts" not in got


def test_reader_importing_nothing_is_coupled_to_plain_text(leerie, tmp_path):
    root, _b, head = _repo(tmp_path, {"zeta/notes.txt": "j\n"})
    leerie._SOURCE_GRAPHS.clear()
    got = leerie._coupled_test_files(root, head, ["zeta/notes.txt"])
    assert got["tests/test_bare_reader.py"] == ["zeta/notes.txt"]
    assert "src/plain.test.ts" not in got


def test_direct_coupling_is_imports_only(leerie, tmp_path):
    root, _b, head = _repo(tmp_path, {"zeta/table.json": '{"k": 2}\n'})
    leerie._SOURCE_GRAPHS.clear()
    assert leerie._coupled_test_files(
        root, head, ["zeta/table.json"], direct=True) == {}


def test_source_change_coupling_is_unchanged(leerie, tmp_path):
    root, _b, head = _repo(tmp_path, {"src/widget.ts": "export const w = 2;\n"})
    leerie._SOURCE_GRAPHS.clear()
    got = leerie._coupled_test_files(root, head, ["src/widget.ts"])
    assert got["src/by-path.test.ts"] == ["src/widget.ts"]
    assert got["src/plain.test.ts"] == ["src/widget.ts"]
    assert "tests/test_plain.py" not in got
    leerie._SOURCE_GRAPHS.clear()
    assert leerie._coupled_test_files(
        root, head, ["src/widget.ts"], direct=True) == {
            "src/by-dir.test.ts": ["src/widget.ts"],
            "src/by-path.test.ts": ["src/widget.ts"],
            "src/plain.test.ts": ["src/widget.ts"]}


def test_near_tests_include_the_reader(leerie, tmp_path):
    root, _b, head = _repo(tmp_path, {"zeta/table.json": '{"k": 2}\n'})
    leerie._SOURCE_GRAPHS.clear()
    near = leerie._near_test_files(root, head, ["zeta/table.json"])
    assert {"src/by-path.test.ts", "src/by-dir.test.ts"} <= near
    assert "src/plain.test.ts" not in near


def test_change_reach_holds_the_reader_but_the_path_stays_unknown(
        leerie, tmp_path):
    root, _b, head = _repo(tmp_path, {"zeta/table.json": '{"k": 2}\n'})
    leerie._SOURCE_GRAPHS.clear()
    reached, unknown = leerie._change_reach(root, head, ["zeta/table.json"])
    assert {"src/by-path.test.ts", "src/by-dir.test.ts"} <= reached
    assert "src/plain.test.ts" not in reached
    assert unknown == ["zeta/table.json"]


def test_scoped_replacement_includes_the_reader(leerie, tmp_path, monkeypatch):
    root, base, _h = _repo(tmp_path, {"zeta/table.json": '{"k": 2}\n'})
    leerie._SOURCE_GRAPHS.clear()
    ran = _stub_measure(leerie, monkeypatch)
    st = _measure_st(leerie, root, base, {"process_constraints": {
        "entries": SUITE}})
    res = asyncio.run(leerie._measure_axes(
        str(root), {"tests": "pnpm run test"}, st,
        dict(leerie.DEFAULT_CAPS), log_path=tmp_path / "l.log",
        verbosity="quiet", memo=False))
    (cmd,) = ran
    assert "src/by-path.test.ts" in cmd.split()
    assert "src/by-dir.test.ts" in cmd.split()
    assert "src/plain.test.ts" not in cmd.split()
    assert "src/by-path.test.ts" in res["tests"]["scoped_replacement"]["files"]


def test_fixture_under_a_test_dir_and_a_deleted_data_file_are_coupled(
        leerie, tmp_path):
    files = dict(FILES)
    files["tests/fixtures/msgs.json"] = "{}\n"
    files["zeta/old.json"] = "{}\n"
    files["src/reads.test.ts"] = ('import { w } from "./widget";\n'
                                  'readFileSync("tests/fixtures/msgs.json");\n'
                                  'readFileSync("zeta/old.json");\n')
    root = tmp_path / "repo2"
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    ident = ("-c", "user.email=t@t", "-c", "user.name=t")
    _git(root, "init", "-q", "-b", "main")
    _git(root, *ident, "add", "-A")
    _git(root, *ident, "commit", "-qm", "base")
    (root / "tests/fixtures/msgs.json").write_text('{"a": 1}\n')
    (root / "zeta/old.json").unlink()
    _git(root, *ident, "commit", "-qam", "change")
    head = _git(root, "rev-parse", "HEAD")
    leerie._SOURCE_GRAPHS.clear()
    for path in ("tests/fixtures/msgs.json", "zeta/old.json"):
        got = leerie._coupled_test_files(root, head, [path])
        assert "src/reads.test.ts" in got, path
