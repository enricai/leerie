"""A test whose imports reach no production file is coupled by its text
(DESIGN §9 *A command the task forbids*).

Barnacle #800 (leerie 0.95.0): the task forbade `npm test`, so the scoped
replacement ran the 666 test files whose import closure reaches a changed
file. The run added a comment carrying a word the repository's vocabulary
guard forbids under `src/`. That guard lists the tree with `git ls-files` and
reads every file, and imports nothing of the product, so it was never among
the 666. The run shipped `done` and the PR's CI failed on it.

These run the real `_coupled_test_files` and `_measure_axes` (only the
command run itself stubbed) over a real git tree.
"""
from __future__ import annotations

import asyncio

from tests.conftest import run_git_out as _git
from tests.test_forbidden_commands import SUITE, _measure_st, _stub_measure

GUARD = ('import { execFileSync } from "node:child_process";\n'
         'import { readFileSync } from "node:fs";\n'
         'const files = execFileSync("git", ["ls-files", "src"]);\n')
E2E = ('import { spawnSync } from "node:child_process";\n'
       'spawnSync("npx", ["tsx", "src/scripts/gen.ts"]);\n')


def _repo(tmp_path, change: dict[str, str]):
    root = tmp_path / "repo"
    files = {
        ".leerie/config.toml": 'test = "pnpm run test"\n',
        "vitest.config.ts": "export default {};\n",
        "src/lib/rand.ts": "export const rand = () => 4;\n",
        "src/lib/rand.test.ts": 'import { rand } from "./rand";\n',
        "src/scripts/gen.ts": "export const gen = 1;\n",
        "src/other.ts": "export const o = 1;\n",
        "src/other.test.ts": 'import { o } from "./other";\n',
        # Imports only a test-support module, which imports nothing.
        "src/testing/fake.ts": "export const fake = 1;\n",
        "src/testing/fake.test.ts": 'import { fake } from "./fake";\n',
        "src/guard.test.ts": GUARD,
        "src/scripts/gen-e2e.test.ts": E2E,
        "pkg/__init__.py": "",
        "pkg/tool.py": "X = 1\n",
        "pkg/core.py": "Y = 1\n",
        "tests/test_core.py": "from pkg import core\n",
        "tests/test_tool_cli.py": ('import subprocess\n'
                                   'subprocess.run(["python", "pkg/tool.py"])\n'),
    }
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    _git(root, "init", "-q", "-b", "main")
    _git(root, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    _git(root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm",
         "base")
    base = _git(root, "rev-parse", "HEAD")
    for rel, text in change.items():
        (root / rel).write_text(text)
    _git(root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qam",
         "change")
    return root, base, _git(root, "rev-parse", "HEAD")


def test_a_test_naming_no_production_file_is_coupled_to_every_change(
        leerie, tmp_path):
    root, _b, head = _repo(tmp_path, {"src/other.ts": "export const o = 2;\n"})
    leerie._SOURCE_GRAPHS.clear()
    got = leerie._coupled_test_files(root, head, ["src/other.ts"])
    assert got == {"src/guard.test.ts": ["src/other.ts"],
                   "src/other.test.ts": ["src/other.ts"],
                   "src/testing/fake.test.ts": ["src/other.ts"]}


def test_a_test_naming_a_production_file_is_coupled_to_that_file_only(
        leerie, tmp_path):
    root, _b, head = _repo(tmp_path, {
        "src/scripts/gen.ts": "export const gen = 2;\n",
        "pkg/tool.py": "X = 2\n"})
    leerie._SOURCE_GRAPHS.clear()
    changed = ["src/scripts/gen.ts", "pkg/tool.py"]
    got = leerie._coupled_test_files(root, head, changed)
    assert got["src/scripts/gen-e2e.test.ts"] == ["src/scripts/gen.ts"]
    assert got["tests/test_tool_cli.py"] == ["pkg/tool.py"]
    assert got["src/guard.test.ts"] == sorted(changed)
    # Each names its own target: a change elsewhere does not reach it.
    leerie._SOURCE_GRAPHS.clear()
    got = leerie._coupled_test_files(root, head, ["src/lib/rand.ts"])
    assert "src/scripts/gen-e2e.test.ts" not in got
    assert "tests/test_tool_cli.py" not in got
    # An import-coupled test keeps its import coupling only.
    assert "src/other.test.ts" not in got and "tests/test_core.py" not in got
    assert got["src/lib/rand.test.ts"] == ["src/lib/rand.ts"]


def test_direct_coupling_is_imports_only(leerie, tmp_path):
    root, _b, head = _repo(tmp_path, {"src/other.ts": "export const o = 2;\n"})
    leerie._SOURCE_GRAPHS.clear()
    assert leerie._coupled_test_files(
        root, head, ["src/other.ts"], direct=True) == {
            "src/other.test.ts": ["src/other.ts"]}


def test_the_scoped_replacement_runs_the_guard(leerie, tmp_path, monkeypatch):
    """The consumer: a forbidden suite's replacement measures the guard."""
    root, base, _h = _repo(tmp_path, {"src/other.ts": "export const o = 2;\n"})
    leerie._SOURCE_GRAPHS.clear()
    ran = _stub_measure(leerie, monkeypatch)
    st = _measure_st(leerie, root, base, {"process_constraints": {
        "entries": SUITE}})
    res = asyncio.run(leerie._measure_axes(
        str(root), {"tests": "pnpm run test"}, st,
        dict(leerie.DEFAULT_CAPS), log_path=tmp_path / "l.log",
        verbosity="quiet", memo=False))
    (cmd,) = ran
    assert "src/guard.test.ts" in cmd.split()
    assert "src/scripts/gen-e2e.test.ts" not in cmd.split()
    assert "src/guard.test.ts" in res["tests"]["scoped_replacement"]["files"]


def test_a_test_naming_no_production_file_is_reached_by_every_change(
        leerie, tmp_path):
    """The narrowing (DESIGN §8 *A change's reach is never guessed*): such a
    test is never carried past a change, and never makes a path's own reach
    known."""
    root, _b, head = _repo(tmp_path, {"src/other.ts": "export const o = 2;\n"})
    leerie._SOURCE_GRAPHS.clear()
    reached, unknown = leerie._change_reach(root, head, ["src/other.ts"])
    assert reached == {"src/other.test.ts", "src/guard.test.ts",
                       "src/testing/fake.test.ts"}
    assert unknown == []
    reached, unknown = leerie._change_reach(root, head, ["src/scripts/gen.ts"])
    assert "src/scripts/gen-e2e.test.ts" in reached
    assert "src/guard.test.ts" in reached
    assert "tests/test_tool_cli.py" not in reached
    # A path no graph holds stays unknown, the guard notwithstanding.
    _r, unknown = leerie._change_reach(root, head, ["data.json"])
    assert unknown == ["data.json"]
