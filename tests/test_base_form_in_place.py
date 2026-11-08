"""An existing test's base form runs IN PLACE of its final form (DESIGN §8
*What an existing test pins on the base holds on the tree that ships*, *Where
the base form runs*).

#333 placed the base form beside the final form under a tagged name
(`a.test.ts` -> `a.leerie-base.test.ts`). A runner that builds a package or
a directory together cannot hold that second copy: a Go package declares each
test function once, a Ruby class reopened merges its methods, a JVM test
source set compiles both. Those files were listed as unchecked. Now the base
form replaces the final form at its own path in the held-out sandbox, and
every test-case file the base did not have is removed there, so the runner
sees the base's test-case files and the tree's code: one mechanism for every
runner.

No Go, Rust, Ruby or JVM toolchain is on the host these were written on
(`command -v go cargo ruby rspec java mvn gradle`: none), so nothing here
pretends to be one. What a package-compiling runner needs is pinned as the
property it rests on, with a real pytest: during the base-form run the
tree's test-case files are exactly the base's. The shim's own replacement and
removal are driven on a real directory; the sandbox itself cannot be built on
such a host (`isolation_error()`), so the suite's emulator stands in for it,
as it does for every held-out run. A real vitest, where one is installed
(`LEERIE_TEST_VITEST_DIR`, a directory holding `node_modules/.bin/vitest`),
runs a JS case with its snapshot. Only `claude_p` would be stubbed; nothing
here calls it.
"""
from __future__ import annotations

import asyncio
import importlib.util
import os
from pathlib import Path

import pytest

from tests.conftest import REPO_ROOT
from tests.conftest import run_git_cwd_first_stdout as _git
from tests.test_existing_behaviour_base_form import World, _caps
from tests.test_acceptance_gate import _st, _staging

SHIM = REPO_ROOT / "scripts" / "landlock_exec.py"


def _shim():
    spec = importlib.util.spec_from_file_location("lk_in_place", SHIM)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# --- the shim: written over a regular file, removed, never through a link ----

@pytest.fixture
def view(tmp_path):
    v = tmp_path / "view"
    (v / "tests").mkdir(parents=True)
    (v / "tests" / "test_a.py").write_text("final\n")
    (v / "tests" / "test_new.py").write_text("added\n")
    (tmp_path / "outside").mkdir()
    (tmp_path / "outside" / "test_a.py").write_text("outside\n")
    src = tmp_path / "base_a.py"
    src.write_text("base\n")
    return v, src


def test_the_shim_writes_the_base_form_over_the_final_one(view):
    v, src = view
    _shim().place([], str(v), over=[(str(src), "tests/test_a.py")],
                  remove=["tests/test_new.py"])
    assert (v / "tests" / "test_a.py").read_text() == "base\n"
    assert not (v / "tests" / "test_new.py").exists()


def test_the_shim_restores_a_deleted_file_and_removes_nothing_absent(view):
    v, src = view
    _shim().place([], str(v), over=[(str(src), "gone/deep/test_b.py")],
                  remove=["tests/test_never.py", "nodir/test_x.py"])
    assert (v / "gone" / "deep" / "test_b.py").read_text() == "base\n"
    assert sorted(p.name for p in (v / "tests").iterdir()) == [
        "test_a.py", "test_new.py"]
    assert not (v / "nodir").exists()


@pytest.mark.parametrize("how", ["file_link", "dir_link", "dir"])
@pytest.mark.parametrize("op", ["replace", "remove"])
def test_the_shim_never_writes_or_removes_through_a_link(view, tmp_path, how,
                                                         op):
    v, src = view
    sh = _shim()
    if how == "file_link":
        (v / "tests" / "test_a.py").unlink()
        (v / "tests" / "test_a.py").symlink_to(tmp_path / "outside"
                                               / "test_a.py")
        rel = "tests/test_a.py"
    elif how == "dir_link":
        (v / "lnk").symlink_to(tmp_path / "outside")
        rel = "lnk/test_a.py"
    else:
        (v / "tests" / "test_dir.py").mkdir()
        rel = "tests/test_dir.py"
    kw = ({"over": [(str(src), rel)]} if op == "replace"
          else {"remove": [rel]})
    with pytest.raises(sh.PlacementTaken):
        sh.place([], str(v), **kw)
    assert (tmp_path / "outside" / "test_a.py").read_text() == "outside\n"
    assert (v / "tests" / "test_new.py").exists()


def test_control_place_still_never_writes_over_a_file(view):
    """`--place` (a held-out set) is unchanged: never over the tree's own."""
    v, src = view
    sh = _shim()
    with pytest.raises(sh.PlacementTaken):
        sh.place([(str(src), "tests/test_a.py")], str(v))
    assert (v / "tests" / "test_a.py").read_text() == "final\n"


def test_the_shim_parses_replace_and_remove_only_in_a_sandbox():
    sh = _shim()
    o = sh._parse(["--sandbox", "--view", "/v", "--replace", "/s=a/b.py",
                   "--remove", "a/c.py", "--", "true"])
    assert o["replace"] == [("/s", "a/b.py")] and o["remove"] == ["a/c.py"]
    assert isinstance(sh._parse(["--replace", "/s=a", "--", "true"]), str)
    assert isinstance(sh._parse(["--remove", "a", "--", "true"]), str)


@pytest.mark.real_heldout_sandbox
def test_the_sandbox_argv_carries_replace_and_remove(leerie):
    """What `_confined_argv` hands the real shim, not the emulator."""
    sb = leerie.HeldOutSandbox(view="/v", replace=(("/s", "t/test_a.py"),),
                               remove=("t/test_new.py",))
    argv = leerie._confined_argv(
        leerie.WorkerGrants((), ()), ["true"], sandbox=sb, refusal_fd=None)
    i = argv.index("--replace")
    assert argv[i + 1] == "/s=t/test_a.py"
    assert argv[argv.index("--remove") + 1] == "t/test_new.py"


# --- the base's test-case files, and only those, during the run ---------------

REGISTRY = ("import pathlib\n\n"
            "def test_the_suite_is_these_files():\n"
            "    here = pathlib.Path(__file__).parent\n"
            "    assert sorted(p.name for p in here.glob('test_*.py')) == "
            "{names!r}\n")
BASE_NAMES = ["test_append.py", "test_cases.py", "test_export.py",
              "test_gone.py", "test_loose.py", "test_red.py",
              "test_registry.py"]


def _registry_world(leerie, tmp_path) -> World:
    w = World(leerie, tmp_path)
    w.commit({"tests/test_registry.py": REGISTRY.format(names=BASE_NAMES)},
             msg="a registry the base had")
    w.base = _git(w.staging, "rev-parse", "HEAD")
    w.st.data["base_sha"] = w.base
    return w


def test_a_test_case_file_the_base_did_not_have_is_not_there(
        leerie, tmp_path):
    """The run adds a test file and lists it in an existing one. Every test
    passes as committed. In base form the registry lists the base's files,
    and so must the tree it runs in, or every package-compiled runner (a Go
    package, a JVM test source set) would fail the base form for a file the
    base never had: a change the run did not make."""
    w = _registry_world(leerie, tmp_path)
    names = sorted(BASE_NAMES + ["test_zz_new.py"])
    w.commit({"tests/test_zz_new.py": "def test_new():\n    assert True\n",
              "tests/test_registry.py": REGISTRY.format(names=names)})
    rec = w.check()
    assert rec["files"] == ["tests/test_registry.py"]
    assert rec["changed"] == [] and rec["unmeasured"] == []
    # The new file is back as it was, in the tree that ships.
    assert (w.staging / "tests" / "test_zz_new.py").is_file()


def test_the_case_is_named_by_its_own_file_and_sized_by_it(leerie, tmp_path):
    """In place, the report names the file at its own path: B* reruns that
    file (sized by it), and the ID needs no translation."""
    w = World(leerie, tmp_path)
    w.commit({"lib.py": "def settle_rounds():\n    return 2\n\n"
                        "def spend():\n    return 5\n\n"
                        "def helper():\n    return 1\n",
              "tests/test_loose.py": "from lib import settle_rounds\n\n"
                                     "def test_rounds():\n"
                                     "    assert settle_rounds() >= 1\n"})
    rec = w.check()
    (row,) = rec["changed"]
    assert row["file"] == "tests/test_loose.py"
    assert row["id"].startswith("tests/test_loose.py :: ")


# --- a real vitest, where one is installed ------------------------------------

VITEST_DIR = os.environ.get("LEERIE_TEST_VITEST_DIR", "")
HAS_VITEST = bool(VITEST_DIR) and os.access(
    os.path.join(VITEST_DIR, "node_modules", ".bin", "vitest"), os.X_OK)

FMT = "export const fmt = (n: number): string => n.toFixed({d});\n"
CASE = ("import {{ expect, test }} from 'vitest';\n"
        "import {{ fmt }} from './fmt';\n\n"
        "test('formats two places', () => {{\n"
        "  expect(fmt(2)).toMatchSnapshot();\n}});\n{extra}")
SNAP = ("// Vitest Snapshot v1, https://vitest.dev/guide/snapshot.html\n\n"
        "exports[`formats two places 1`] = `\"{v}\"`;\n")


class JSWorld:
    """A TypeScript repository run by a real vitest (links to it committed
    in the tree's `node_modules`, so every checkout has it)."""

    def __init__(self, leerie, tmp_path):
        repo = tmp_path / "repo"
        (repo / "src" / "__snapshots__").mkdir(parents=True)
        _git(repo, "init", "-q", "-b", "main")
        _git(repo, "config", "user.email", "t@x")
        _git(repo, "config", "user.name", "t")
        # The package linked in, its directory the tree's own: vitest writes
        # its API token beneath the tree's `node_modules`.
        mods = Path(VITEST_DIR) / "node_modules"
        (repo / "node_modules" / ".bin").mkdir(parents=True)
        (repo / "node_modules" / "vitest").symlink_to(mods / "vitest")
        (repo / "node_modules" / ".bin" / "vitest").symlink_to(
            os.path.realpath(mods / ".bin" / "vitest"))
        (repo / "package.json").write_text('{"type": "module"}\n')
        (repo / ".leerie").mkdir()
        (repo / ".leerie" / "config.toml").write_text(
            'test = "npx vitest run"\n'
            'test_scoped = "npx vitest run {test_files}"\n')
        (repo / ".gitignore").write_text(
            ".leerie/\nnode_modules/.vite/\nnode_modules/.vitest/\n")
        (repo / "src" / "fmt.ts").write_text(FMT.format(d=2))
        (repo / "src" / "fmt.test.ts").write_text(CASE.format(extra=""))
        (repo / "src" / "__snapshots__" / "fmt.test.ts.snap").write_text(
            SNAP.format(v="2.00"))
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "base")
        self.base = _git(repo, "rev-parse", "HEAD")
        self.leerie = leerie
        self.st = _st(leerie, tmp_path, repo, self.base,
                      working_branch="main", base_sha=self.base)
        self.st.data["base_sha"] = self.base
        self.staging = _staging(self.st, repo)
        self.caps = _caps(leerie)

    def commit(self, files: dict[str, str]) -> None:
        for rel, text in files.items():
            (self.staging / rel).write_text(text)
        _git(self.staging, "add", "-A")
        _git(self.staging, "commit", "-qm", "round")

    def check(self) -> dict:
        return asyncio.run(self.leerie._base_form_regressions(
            self.st, self.caps, self.staging, self.base, label="t",
            log_path=self.st.run_dir / "logs" / "t.log"))


@pytest.mark.skipif(not HAS_VITEST, reason="no vitest installed "
                    "(LEERIE_TEST_VITEST_DIR)")
def test_a_real_vitest_case_runs_in_place_with_its_base_snapshot(
        leerie, tmp_path):
    """The run changes the format and rewrites the snapshot to match, and
    edits the test: in its final form everything passes. In place, with the
    base's snapshot, the case asserts the old value and fails."""
    w = JSWorld(leerie, tmp_path)
    w.commit({"src/fmt.ts": FMT.format(d=1),
              "src/fmt.test.ts": CASE.format(extra="// reworded\n"),
              "src/__snapshots__/fmt.test.ts.snap": SNAP.format(v="2.0")})
    rec = w.check()
    assert rec["files"] == ["src/fmt.test.ts"]
    (row,) = rec["changed"]
    assert row["file"] == "src/fmt.test.ts"
    assert "formats two places" in row["id"]
    assert (w.staging / "src" / "__snapshots__" / "fmt.test.ts.snap"
            ).read_text() == SNAP.format(v="2.0")


@pytest.mark.skipif(not HAS_VITEST, reason="no vitest installed "
                    "(LEERIE_TEST_VITEST_DIR)")
def test_a_real_vitest_case_whose_behaviour_held_is_no_change(
        leerie, tmp_path):
    w = JSWorld(leerie, tmp_path)
    w.commit({"src/fmt.test.ts": CASE.format(
        extra="\ntest('one place', () => {\n"
              "  expect(fmt(1)).toBe('1.00');\n});\n")})
    rec = w.check()
    assert rec["files"] == ["src/fmt.test.ts"]
    assert rec["changed"] == [] and rec["unmeasured"] == []
