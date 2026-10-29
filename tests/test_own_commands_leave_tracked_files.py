"""I2: leerie's own install, build, test and coverage commands leave a
worker's tree as they found it, apart from ignored files (DESIGN §6½
*Leerie's own commands leave a worker's tree as they found it*).

Review round 9 M1: an install that rewrote a tracked lockfile in staging
left ` M pnpm-lock.yaml` behind, and a clean run's PR listed it under
"Needs human verification" as "uncommitted edits to 1 tracked file(s) that
do not ship". Rounds 10 to 14: the untracked half — `__pycache__/`,
`coverage/`, `junit.xml` a test run leaves in a repository that does not
ignore them — was recorded instead of removed, and every record was driven
wrong in turn. Now a tracked file is put back and an untracked, not-ignored
file the command created is deleted. The drivers use real git and real
executables on PATH."""
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
    return subprocess.run(["git", "status", "--porcelain",
                           "--untracked-files=all"], cwd=p,
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
    # Leerie's own commands left nothing behind (I2), so the final
    # measurement is of the committed tree: the diff is stamped with it and
    # the record's final-tree axes cite it.
    assert _status(run["staging"]) == ""
    head_tree = _git(run["staging"], "rev-parse", "HEAD^{tree}")
    assert st.data["test_id_diff"]["tree_sha"] == head_tree
    assert st.data["conformance"]["_final"]["tree_sha"] == head_tree
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
# The four clean-run shapes: done, with an EMPTY end-state section


def test_shape1_pytest_pycache_unignored(leerie, tmp_path, monkeypatch,
                                         capsys):
    run = _repo(leerie, tmp_path, monkeypatch)
    _quiet_conformer(leerie, monkeypatch)
    out, sec = _final_and_phase5(leerie, run)
    # The precondition: leerie's test runs did create `__pycache__/`.
    assert "untracked file(s) its own tests created" in capsys.readouterr().out
    assert not list(run["staging"].rglob("__pycache__"))
    assert (out, sec) == ("done", "")


def test_shape2_vitest_shaped_coverage(leerie, tmp_path, monkeypatch, capsys):
    run = _node_repo(leerie, tmp_path, monkeypatch, lock_rewrite=False)
    _quiet_conformer(leerie, monkeypatch)
    out, sec = _final_and_phase5(leerie, run)
    logged = capsys.readouterr().out
    assert "untracked file(s) its own tests created" in logged
    assert "junit.xml" in logged
    assert not (run["staging"] / "junit.xml").exists()
    assert not (run["staging"] / "coverage").exists()
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


SNAP_TEST = (
    "import json, os, time\n"
    "P = os.path.join(os.path.dirname(__file__), 'snap.json')\n"
    "def test_snap():\n"
    "    with open(P, 'w') as f:\n"
    "        json.dump({'v': 1, 'at': time.time()}, f)\n"
    "    assert True\n")


def test_shape4_a_test_that_rewrites_a_tracked_snapshot(
        leerie, tmp_path, monkeypatch):
    """Round 10's fourth shape: a test rewrites the tracked
    `tests/snap.json` with a timestamp on every run."""
    run = _repo(leerie, tmp_path, monkeypatch)
    for wt in (run["repo"], run["staging"]):
        (wt / "tests" / "test_snap.py").write_text(SNAP_TEST)
        (wt / "tests" / "snap.json").write_text('{"v": 1, "at": 0}')
        _git(wt, "add", "tests")
        _git(wt, "commit", "-qm", "snap")
    run["st"].data["conformance"]["_baseline"]["sha"] = _git(
        run["repo"], "rev-parse", "HEAD")
    run["st"].save()
    _quiet_conformer(leerie, monkeypatch)
    out, sec = _final_and_phase5(leerie, run)
    assert " M tests/snap.json" not in _status(run["staging"])
    assert (out, sec) == ("done", "")


@pytest.mark.parametrize("worker_runs_tests", [False, True])
def test_a_conformer_rerunning_the_tests_ends_done_once_it_cleans_up(
        leerie, tmp_path, monkeypatch, worker_runs_tests):
    """Review round 11 M2's driver, rewritten for I1 + I2: shape 2, and the
    final conformer re-runs the repository's test command through Bash to
    check its work, changing nothing. Its `coverage/` and `junit.xml` are
    its own output now, never recorded as leerie's: the clean-tree gate
    re-asks it, and it deletes them (as real workers do). The round 11
    harm — `_final.tree_sha` and the diff's `tree_sha` None and the
    delivery judges' RECORD (`_orchestrator_results_digest`) empty for a
    tree that was exactly HEAD — cannot recur: both stamps are HEAD's tree.
    The control runs nothing and is never re-asked."""
    run = _node_repo(leerie, tmp_path, monkeypatch, lock_rewrite=False)
    _quiet_conformer(leerie, monkeypatch)
    staging, st = run["staging"], run["st"]
    cmd = leerie.resolve_blt(None)["test"]
    sids = []

    async def conformer(*a, **k):
        sids.append(k["sid"])
        if k["sid"].endswith("-clean1"):
            subprocess.run(["git", "clean", "-fdq"], cwd=staging, check=True)
        elif worker_runs_tests:
            subprocess.run(cmd, shell=True, cwd=staging, capture_output=True)
        return {}
    monkeypatch.setattr(leerie, "claude_p", conformer)
    monkeypatch.setattr(leerie, "_log_session_id", lambda p: "sess-1")
    out, sec = _final_and_phase5(leerie, run)     # asserts both tree_shas
    assert (out, sec) == ("done", "")
    assert sids == (["final-conformer-r0", "final-conformer-r0-clean1"]
                    if worker_runs_tests else ["final-conformer-r0"])
    digest = leerie._orchestrator_results_digest(st, staging)
    assert "[orchestrator final-tree tests]" in digest
    assert "[orchestrator B* failing-test diff]" in digest


# --------------------------------------------------------------------------
# `_ensure_worktree_deps`: installs and builds that rewrite tracked files


def _tree(tmp_path, files: dict[str, str]):
    t = tmp_path / "t"
    t.mkdir()
    _git(t, "init", "-q", "-b", "main")
    _git(t, "config", "user.email", "t@t")
    _git(t, "config", "user.name", "t")
    for rel, body in files.items():
        (t / rel).parent.mkdir(parents=True, exist_ok=True)
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
    t = _tree(tmp_path, {"tsconfig.json": '{"a": 1}\n',
                         ".gitignore": ".next/\n"})
    _tool(tmp_path, monkeypatch, "next",
          "printf '{\"a\": 1, \"plugins\": []}\\n' > tsconfig.json;"
          " printf 'x\\n' > next-env.d.ts; mkdir -p .next; touch .next/b")
    _deps(leerie, t, [{"kind": "build", "command": ["next", "build"]}])
    assert (t / "tsconfig.json").read_text() == '{"a": 1}\n'
    # I2: the unignored file it generated goes, the ignored build stays.
    assert not (t / "next-env.d.ts").exists()
    assert (t / ".next" / "b").is_file()
    assert _status(t) == ""


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


# --------------------------------------------------------------------------
# Round 10: the snapshot's own robustness, and the record of what leerie's
# commands left untracked


def test_only_the_snapshot_before_a_command_writes_blobs(leerie, tmp_path):
    """L1, the reviewer's driver in miniature: a wrapped command rewrote a
    tracked 5 MB fixture ten times and `.git/objects` grew by ~50 MB, every
    after-snapshot writing its blob. Only the before-snapshot writes now,
    and the bytes still come back."""
    t = _tree(tmp_path, {"fixture.db": "x" * 10})

    def objects():
        return sum(len(fs) for _d, _s, fs in os.walk(t / ".git" / "objects"))
    n0 = objects()

    async def run():
        for _ in range(5):
            async with leerie._leaves_tree_as_found(t, "test run", "t"):
                (t / "fixture.db").write_bytes(os.urandom(4096))
    asyncio.run(run())
    assert (t / "fixture.db").read_text() == "x" * 10 and _status(t) == ""
    assert objects() == n0             # HEAD's bytes, nothing written
    (t / "fixture.db").write_text("edited\n")      # a worker's edit
    asyncio.run(run())
    assert (t / "fixture.db").read_text() == "edited\n"
    assert objects() <= n0 + 5         # one blob per before-snapshot only


def _with_submodule(tmp_path):
    sub = tmp_path / "libsrc"
    sub.mkdir()
    for args in (["init", "-q", "-b", "main"],
                 ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        _git(sub, *args)
    (sub / "lib.c").write_text("int x;\n")
    _git(sub, "add", "-A")
    _git(sub, "commit", "-qm", "lib")
    t = _tree(tmp_path, {"uv.lock": "version = 1\n", "app.py": "A = 1\n"})
    _git(t, "-c", "protocol.file.allow=always", "submodule", "add", "-q",
         str(sub), "vendor/lib")
    _git(t, "commit", "-qm", "sub")
    (t / "vendor" / "lib" / "lib.c").write_text("int x; int y;\n")
    return t


def test_a_dirty_submodule_does_not_switch_the_snapshot_off(leerie,
                                                            tmp_path):
    """L2, the reviewer's driver: modified content inside a submodule made
    `git hash-object` fail on its directory and the whole snapshot None, so
    an install's relock was not put back. The submodule's own content is not this tree's edit;
    a moved submodule commit is listed as `gitlink:<sha>` and never put
    back."""
    t = _with_submodule(tmp_path)
    assert leerie._tracked_content(t) == {}

    async def install():
        async with leerie._leaves_tree_as_found(t, "install/build", "t"):
            (t / "uv.lock").write_text("version = 1\nrevision = 2\n")
    asyncio.run(install())
    assert (t / "uv.lock").read_text() == "version = 1\n"
    (t / "app.py").write_text("A = 2\n")
    sub = t / "vendor" / "lib"
    _git(sub, "commit", "-qam", "bump")              # the submodule moved
    bumped = _git(sub, "rev-parse", "HEAD")
    snap = leerie._tracked_content(t)
    assert snap["vendor/lib"] == "gitlink:" + bumped
    assert snap["app.py"] == _git(t, "hash-object", "app.py")


def test_a_put_back_never_moves_a_submodule(leerie, tmp_path):
    t = _with_submodule(tmp_path)
    sub = t / "vendor" / "lib"

    async def bump():
        async with leerie._leaves_tree_as_found(t, "install/build", "t"):
            _git(sub, "commit", "-qam", "bump")
    asyncio.run(bump())
    assert " M vendor/lib" in _status(t)             # left as the command did


@pytest.mark.parametrize("edited", [False, True])
def test_a_tracked_file_a_command_replaced_with_a_directory_is_put_back(
        leerie, tmp_path, edited):
    """Review round 11 suspicion 1, proven: `_tracked_content` ran `git -C
    <dir> rev-parse HEAD` on any tracked path that is now a directory, which
    in a plain directory answers with the superproject's HEAD — so a
    tracked file a command replaced with a directory was snapshotted as a
    submodule (`gitlink:<HEAD>`) and never put back. A worker's earlier
    edit of the file comes back too."""
    t = _tree(tmp_path, {"cfg": "a\n"})
    if edited:
        (t / "cfg").write_text("worker\n")

    async def cmd():
        async with leerie._leaves_tree_as_found(t, "build", "t"):
            (t / "cfg").unlink()
            (t / "cfg").mkdir()
            (t / "cfg" / "x").write_text("1")
            snap = leerie._tracked_content(t)
            assert snap == {"cfg": None}             # gone, not a gitlink
    asyncio.run(cmd())
    assert (t / "cfg").is_file()
    assert (t / "cfg").read_text() == ("worker\n" if edited else "a\n")
    assert _status(t) == (" M cfg\n" if edited else "")


def test_a_real_submodule_is_still_a_gitlink(leerie, tmp_path):
    t = _with_submodule(tmp_path)
    sub = t / "vendor" / "lib"
    _git(sub, "commit", "-qam", "bump")
    snap = leerie._tracked_content(t)
    assert snap["vendor/lib"] == "gitlink:" + _git(sub, "rev-parse", "HEAD")


def test_a_put_back_checks_out_a_bracketed_path_literally(leerie, tmp_path):
    """`git checkout HEAD -- app/[slug]/page.tsx` read the brackets as a
    glob and also reverted `app/s/page.tsx`, a worker's edit."""
    t = _tree(tmp_path, {"app/[slug]/page.tsx": "a\n",
                         "app/s/page.tsx": "b\n"})
    (t / "app" / "s" / "page.tsx").write_text("worker\n")

    async def cmd():
        async with leerie._leaves_tree_as_found(t, "build", "t"):
            (t / "app" / "[slug]" / "page.tsx").write_text("rewritten\n")
    asyncio.run(cmd())
    assert (t / "app" / "[slug]" / "page.tsx").read_text() == "a\n"
    assert (t / "app" / "s" / "page.tsx").read_text() == "worker\n"


# --------------------------------------------------------------------------
# I2, one regression per command family: a command that rewrites a tracked
# file, creates unignored files (one nested), creates an ignored file and
# rewrites a file a worker left untracked before it leaves `git status
# --porcelain --untracked-files=all` exactly as it found it. The ignored
# file stays (dependencies), and a file that was there before is never
# deleted.

BODY = ("printf 'changed\\n' > tracked.txt; mkdir -p out/deep; "
        "printf r > out/deep/report.xml; printf j > junit.xml; "
        "mkdir -p deps; printf d > deps/lib; printf w2 > worker.txt")


def _family_tree(tmp_path):
    t = _tree(tmp_path, {"tracked.txt": "orig\n", ".gitignore": "deps/\n"})
    (t / "worker.txt").write_text("w1")       # there before the command
    return t, _status(t)


def _assert_as_found(t, before: str, sub: str = "") -> None:
    assert before == "?? worker.txt\n"
    assert _status(t) == before
    base = t / sub
    assert (base / "tracked.txt").read_text() == "orig\n"
    assert not (base / "junit.xml").exists()
    assert not (base / "out").exists()            # its emptied dirs too
    assert (base / "deps" / "lib").read_text() == "d"     # ignored: stays
    assert (t / "worker.txt").exists()            # never deleted


def test_i2_an_install_leaves_the_tree_as_found(leerie, tmp_path,
                                               monkeypatch):
    t, before = _family_tree(tmp_path)
    _tool(tmp_path, monkeypatch, "fakeinst", BODY)
    assert _deps(leerie, t, [{"kind": "install",
                              "command": ["fakeinst"]}]) is True
    _assert_as_found(t, before)


@pytest.mark.parametrize("axis", ["build", "lint", "tests"])
def test_i2_a_measured_axis_leaves_the_tree_as_found(leerie, tmp_path,
                                                    axis):
    t, before = _family_tree(tmp_path)
    st = _St([])
    st.data["blt_results"] = {}
    res = asyncio.run(leerie._measure_axes(
        str(t), {axis: BODY}, st, {}, log_path=tmp_path / "m.log",
        verbosity="quiet", memo=False))
    assert res[axis]["passed"] is True
    _assert_as_found(t, before)


PY_TEST = (
    "import os, pathlib\n"
    "R = pathlib.Path(__file__).resolve().parent.parent\n"
    "def test_writes():\n"
    "    os.chdir(R)\n"
    "    os.system(" + repr(BODY) + ")\n"
    "    assert False\n")


def test_i2_a_test_id_run_leaves_the_tree_as_found(leerie, tmp_path,
                                                   monkeypatch):
    """`_capture_failing_test_ids`: the report run itself; its pytest also
    leaves `tests/__pycache__/`, unignored here."""
    monkeypatch.delenv("PYTHONDONTWRITEBYTECODE", raising=False)
    t, _ = _family_tree(tmp_path)
    (t / "tests").mkdir()
    (t / "tests" / "test_w.py").write_text(PY_TEST)
    _git(t, "add", "tests")
    _git(t, "commit", "-qm", "test")
    before = _status(t)
    st = leerie.State(tmp_path / "state", "r")
    st.data = {}
    got = asyncio.run(leerie._capture_failing_test_ids(
        str(t), "python3 -m pytest -q -p no:cacheprovider tests", st, {},
        label="ids", log_path=tmp_path / "ids.log", timeout=120))
    assert [i.rsplit(".", 1)[-1].rsplit("::", 1)[-1]
            for i in got["ids"]] == ["test_writes"]
    assert not (t / "tests" / "__pycache__").exists()
    _assert_as_found(t, before)


@pytest.mark.parametrize("sub", ["", "pkg"])
def test_i2_a_coverage_command_leaves_the_tree_as_found(leerie, tmp_path,
                                                       sub):
    """`_cov_contained`, in the worktree and in a recipe `working_dir`
    below it (the bracket covers the repository root)."""
    t, _ = _family_tree(tmp_path)
    if sub:
        (t / sub).mkdir()
        (t / sub / "tracked.txt").write_text("orig\n")
        (t / sub / ".gitignore").write_text("deps/\n")
        _git(t, "add", sub)
        _git(t, "commit", "-qm", "pkg")
    before = _status(t)
    st = leerie.State(tmp_path / "state", "r")
    st.data = {"verbosity": "quiet"}
    (st.run_dir / "logs").mkdir(parents=True, exist_ok=True)
    out = asyncio.run(leerie._cov_contained(
        ["sh", "-c", BODY.replace("worker.txt", "../worker.txt")
         if sub else BODY], tree=t / sub, st=st,
        caps=dict(leerie.DEFAULT_CAPS), sid="s", tag="t"))
    assert out["rc"] == 0
    _assert_as_found(t, before, sub)


# --------------------------------------------------------------------------
# Permission bits (DESIGN §6½): a test that makes a directory read-only and
# dies before restoring it. The files are the orchestrator's own on every
# runtime, so the bracket restores the write bit and still leaves the tree
# as it found it; what it truly cannot remove stops the run, named.

def test_i2_a_read_only_directory_does_not_keep_the_commands_output(
        leerie, tmp_path, monkeypatch):
    t, before = _family_tree(tmp_path)
    (t / "pkg").mkdir()
    (t / "pkg" / "mod.txt").write_text("orig\n")
    _git(t, "add", "pkg")
    _git(t, "commit", "-qm", "pkg")
    _tool(tmp_path, monkeypatch, "fakeinst",
          "mkdir -p out/locked && printf r > out/locked/f && "
          "chmod 0555 out/locked out && "
          "printf changed > pkg/mod.txt && chmod 0555 pkg")
    try:
        assert _deps(leerie, t, [{"kind": "install",
                                  "command": ["fakeinst"]}]) is True
        assert _status(t) == before
        assert not (t / "out").exists()
        assert (t / "pkg" / "mod.txt").read_text() == "orig\n"
    finally:
        subprocess.run(["chmod", "-R", "u+w", str(t)], check=False)


def test_i2_what_the_bracket_cannot_undo_stops_the_run_named(
        leerie, tmp_path, monkeypatch, capsys):
    t, _ = _family_tree(tmp_path)
    monkeypatch.setattr(leerie, "_remove_new_untracked",
                        lambda tree, before: [])

    async def cmd():
        async with leerie._leaves_tree_as_found(t, "test run", "t"):
            (t / "stuck.txt").write_text("x")
    with pytest.raises(SystemExit):
        asyncio.run(cmd())
    err = capsys.readouterr().err
    assert "leerie's own test run left 1 path(s)" in err
    assert "stuck.txt" in err
