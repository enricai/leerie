"""Review round 9 M1: leerie's own install, build and test commands leave
tracked files as they found them (DESIGN §6½ *Leerie's own commands leave
tracked files as they found them*).

Before: an install that rewrote a tracked lockfile in staging left
` M pnpm-lock.yaml` behind, and a clean run's PR listed it under "Needs
human verification" as "uncommitted edits to 1 tracked file(s) that do not
ship", with the final conformer warning that it "left 1 uncommitted
change(s)" it never made. The drivers use real git and real executables on
PATH that rewrite the tracked file the way `uv sync`, `bundle install` and
`next build` do."""
from __future__ import annotations

import asyncio
import os
import stat
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))
from test_redrive_test_id_diff_refresh import _git  # noqa: E402
from test_shipping_tree_is_committed import (  # noqa: E402
    _phase5, _quiet_conformer, _repo)

NODE_TEST = (
    "const fs=require('fs');fs.mkdirSync('coverage',{recursive:true});"
    "fs.writeFileSync('coverage/coverage-final.json','{}');"
    "fs.writeFileSync('junit.xml','<testsuites/>');"
    "const m=fs.readFileSync('mod.py','utf8');"
    "if(!/X = [12]/.test(m)){process.exit(1)}")


@pytest.fixture(autouse=True)
def _clear_deps_memo(leerie):
    leerie._DEPS_INSTALLED.clear()
    yield
    leerie._DEPS_INSTALLED.clear()


def _status(p) -> str:
    return subprocess.run(["git", "status", "--porcelain"], cwd=p,
                          capture_output=True, text=True).stdout


def _tool(tmp_path, monkeypatch, name: str, body: str) -> None:
    """An executable `name` on PATH running the shell `body` in its cwd."""
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    fake = bindir / name
    fake.write_text("#!/bin/sh\n" + body + "\n")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bindir}:{os.environ['PATH']}")


def _final_and_phase5(leerie, run):
    asyncio.run(leerie._run_final_conformance(
        run["run_dir"], run["st"], run["caps"], {"conformer": "sonnet"},
        {"conformer": "low"}))
    _phase5(leerie, run)
    st = run["st"]
    return (leerie._decide_outcome(st, any_commits=True),
            leerie._format_pr_end_state_section(st.data))


def _node_repo(leerie, tmp_path, monkeypatch, *, lock_rewrite: bool):
    node = subprocess.run(["sh", "-c", "command -v node"],
                          capture_output=True, text=True).stdout.strip()
    if not node:
        pytest.skip("node is not installed")
    run = _repo(leerie, tmp_path, monkeypatch, ignore="node_modules/\n")
    repo, staging = run["repo"], run["staging"]
    for wt in (repo, staging):
        (wt / "package.json").write_text(
            '{"name":"x","version":"1.0.0","private":true}\n')
        (wt / "pnpm-lock.yaml").write_text(
            "lockfileVersion: '9.0'\n\nimporters:\n\n  .: {}\n")
        _git(wt, "add", "package.json", "pnpm-lock.yaml")
        _git(wt, "commit", "-qm", "node")
    run["st"].data["conformance"]["_baseline"]["sha"] = _git(
        repo, "rev-parse", "HEAD")
    cmd = "node -e \"" + NODE_TEST + "\""
    monkeypatch.setattr(leerie, "resolve_blt", lambda root: {"test": cmd})
    recipe = leerie._lockfile_table_entries(repo)
    assert recipe and recipe[0]["command"][0] == "pnpm"
    run["st"].data["provision"] = {"recipe": recipe}
    # Shape 2 needs a pnpm too: an offline no-op that only creates deps.
    _tool(tmp_path, monkeypatch, "pnpm", "mkdir -p node_modules" + (
        "\nprintf '\\nsettings:\\n  autoInstallPeers: true\\n'"
        " >> pnpm-lock.yaml" if lock_rewrite else ""))
    run["st"].save()
    return run


# --------------------------------------------------------------------------
# The three clean-run shapes: done, with an EMPTY end-state section


def test_shape1_pytest_pycache_unignored(leerie, tmp_path, monkeypatch):
    run = _repo(leerie, tmp_path, monkeypatch)
    _quiet_conformer(leerie, monkeypatch)
    out, sec = _final_and_phase5(leerie, run)
    assert "?? " in _status(run["staging"])        # the precondition
    assert (out, sec) == ("done", "")


def test_shape2_vitest_shaped_coverage(leerie, tmp_path, monkeypatch):
    run = _node_repo(leerie, tmp_path, monkeypatch, lock_rewrite=False)
    _quiet_conformer(leerie, monkeypatch)
    out, sec = _final_and_phase5(leerie, run)
    assert "?? junit.xml" in _status(run["staging"])
    assert (out, sec) == ("done", "")


def test_shape3_an_install_that_rewrites_the_lockfile(
        leerie, tmp_path, monkeypatch):
    """The reviewer's driver: the install appends to the tracked
    `pnpm-lock.yaml`. Before: ` M pnpm-lock.yaml` in staging, the section
    listed it as edits that do not ship, and the conformer was blamed."""
    run = _node_repo(leerie, tmp_path, monkeypatch, lock_rewrite=True)
    _quiet_conformer(leerie, monkeypatch)
    out, sec = _final_and_phase5(leerie, run)
    status = _status(run["staging"])
    assert "pnpm-lock.yaml" not in status
    assert (run["staging"] / "node_modules").is_dir()   # deps stay
    assert (out, sec) == ("done", "")
    warnings = run["st"].data["conformance"]["_final"].get("warnings") or []
    assert not any("uncommitted change" in w for w in warnings), warnings


# --------------------------------------------------------------------------
# `_ensure_worktree_deps`: installs and builds that rewrite tracked files


def _tree(tmp_path, files: dict[str, str]):
    t = tmp_path / "t"
    t.mkdir()
    _git(t, "init", "-q", "-b", "main")
    _git(t, "config", "user.email", "t@t")
    _git(t, "config", "user.name", "t")
    for rel, body in files.items():
        (t / rel).write_text(body)
    _git(t, "add", "-A")
    _git(t, "commit", "-qm", "base")
    return t


class _St:
    def __init__(self, recipe):
        self.data = {"provision": {"recipe": recipe}}

    def save(self):
        pass


def _deps(leerie, tree, recipe):
    return asyncio.run(leerie._ensure_worktree_deps(
        str(tree), _St(recipe), {}, log_path=tree.parent / "i.log",
        verbosity="quiet"))


@pytest.mark.parametrize("tool,lock,args", [
    ("uv", "uv.lock", ["sync"]),
    ("bundle", "Gemfile.lock", ["install"]),
])
def test_an_install_that_relocks_is_put_back(leerie, tmp_path, monkeypatch,
                                             tool, lock, args):
    t = _tree(tmp_path, {lock: "version = 1\n"})
    _tool(tmp_path, monkeypatch, tool,
          f"printf 'revision = 2\\n' >> {lock}; mkdir -p .deps")
    assert _deps(leerie, t, [{"kind": "install",
                              "command": [tool, *args]}]) is True
    assert (t / lock).read_text() == "version = 1\n"
    assert (t / ".deps").is_dir()                  # deps stay
    assert " M " not in _status(t)


def test_a_build_that_rewrites_tsconfig_is_put_back(leerie, tmp_path,
                                                     monkeypatch):
    t = _tree(tmp_path, {"tsconfig.json": '{"a": 1}\n'})
    _tool(tmp_path, monkeypatch, "next",
          "printf '{\"a\": 1, \"plugins\": []}\\n' > tsconfig.json;"
          " printf 'x\\n' > next-env.d.ts")
    _deps(leerie, t, [{"kind": "build", "command": ["next", "build"]}])
    assert (t / "tsconfig.json").read_text() == '{"a": 1}\n'
    # Untracked output is not leerie's to touch.
    assert (t / "next-env.d.ts").is_file()


def test_a_workers_edit_to_the_same_file_survives(leerie, tmp_path,
                                                   monkeypatch):
    """Restored as it was just before the command — never to HEAD — so an
    edit a worker left is not lost; a worker's edit to another file and a
    deletion are untouched."""
    t = _tree(tmp_path, {"uv.lock": "version = 1\n", "a.py": "A = 1\n",
                         "gone.py": "G = 1\n"})
    (t / "uv.lock").write_text("version = 1\nworker = 1\n")
    (t / "a.py").write_text("A = 2\n")
    (t / "gone.py").unlink()
    _tool(tmp_path, monkeypatch, "uv", "printf 'revision = 2\\n' >> uv.lock")
    _deps(leerie, t, [{"kind": "install", "command": ["uv", "sync"]}])
    assert (t / "uv.lock").read_text() == "version = 1\nworker = 1\n"
    assert (t / "a.py").read_text() == "A = 2\n"
    assert not (t / "gone.py").exists()


def test_a_command_that_deletes_or_restores_a_file_is_undone(
        leerie, tmp_path, monkeypatch):
    t = _tree(tmp_path, {"keep.txt": "k\n", "back.txt": "b\n"})
    (t / "back.txt").unlink()                  # a worker deleted it
    _tool(tmp_path, monkeypatch, "uv",
          "rm keep.txt; printf 'b\\n' > back.txt")
    _deps(leerie, t, [{"kind": "install", "command": ["uv", "sync"]}])
    assert (t / "keep.txt").read_text() == "k\n"
    assert not (t / "back.txt").exists()


# --------------------------------------------------------------------------
# `_measure_axes`: a measured build axis that rewrites a tracked file


def test_a_measured_build_axis_puts_back_what_it_rewrote(leerie, tmp_path,
                                                         monkeypatch):
    t = _tree(tmp_path, {"tsconfig.json": '{"a": 1}\n'})
    st = _St([])
    st.data["blt_results"] = {}
    res = asyncio.run(leerie._measure_axes(
        str(t), {"build": "printf '{}\\n' > tsconfig.json"}, st, {},
        log_path=tmp_path / "m.log", verbosity="quiet", memo=False))
    assert res["build"]["passed"] is True
    assert (t / "tsconfig.json").read_text() == '{"a": 1}\n'
    assert _status(t) == ""


def test_the_snapshot_helpers_on_their_own(leerie, tmp_path):
    t = _tree(tmp_path, {"a": "1\n", "b": "2\n"})
    assert leerie._tracked_content(t) == {}
    (t / "a").write_text("3\n")
    (t / "b").unlink()
    snap = leerie._tracked_content(t)
    assert set(snap) == {"a", "b"} and snap["b"] is None
    assert leerie._changed_paths({}, snap) == ["a", "b"]
    assert leerie._changed_paths(snap, dict(snap)) == []
    assert leerie._tracked_content(tmp_path / "not-a-repo") is None
