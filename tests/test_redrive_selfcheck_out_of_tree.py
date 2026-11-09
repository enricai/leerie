"""I3: a held-out set never enters a tree a worker commits from (DESIGN §8
*A held-out set never enters a tree a worker commits from*).

Root cause RC3: the re-drive's self-check staged the shown sets in
staging, the tree the implementer commits from, and every rule that then
had to remove what a runner made of them drew the next review's
counter-example. The self-check is now a command leerie provides
(`scripts/selfcheck.py` behind `bin/leerie-selfcheck`) that runs the sets
against a copy of the worker's working tree, outside it.

Covered, on real git with the real command and a real runner (pytest):
the verdict follows the worker's uncommitted edits; nothing is written to
the worker's tree; ignored paths are shared, but never one a set file is
placed under; a set path the tree already has is refused; git run by a
test resolves no enclosing repository; a subset by file name and a
test-name filter; and, after a whole re-drive whose implementer runs the
command with a copy of the set lying in its tree, no set byte in the
repository's object store.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(__file__))
from test_delivery_redrive import World  # noqa: E402
from test_worker_confinement import run  # noqa: E402,F401  (fixture)
import functools
from tests.conftest import run_git_out  # noqa: E402

MARK = "SETBYTES_7f3c_UNIQUE_MARKER"


_git = functools.partial(run_git_out, strip=False)


def _fixture(tmp_path: Path) -> tuple[Path, types.SimpleNamespace, list]:
    """A pytest repo whose state root sits inside it (`.leerie/`, ignored —
    the default layout outside the container), an ignored helper package
    the set imports, an ignored `gen/` directory, and one shown set: a
    defect file, a control file, and a test under `gen/`."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    (repo / ".gitignore").write_text(".leerie/runs/\nvendored/\ngen/\n")
    (repo / ".leerie").mkdir()
    (repo / ".leerie" / "config.toml").write_text(
        'test = "python3 -m pytest -q -p no:cacheprovider"\n')
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    (repo / "vendored").mkdir()
    (repo / "vendored" / "__init__.py").write_text("ONE = 1\n")
    (repo / "gen").mkdir()
    (repo / "gen" / "built.txt").write_text("generated\n")
    sdir = tmp_path / "set1"
    (sdir / "tests").mkdir(parents=True)
    (sdir / "gen").mkdir()
    (sdir / "tests" / "test_add.py").write_text(
        f"# {MARK}\nfrom calc import add\nfrom vendored import ONE\n\n"
        "def test_adds():\n    assert add(2, ONE) == 3\n\n"
        "def test_zero():\n    assert add(0, 0) == 0\n")
    (sdir / "tests" / "test_repo.py").write_text(
        "import subprocess\n\n"
        "def test_no_enclosing_repository():\n"
        "    r = subprocess.run(['git', 'rev-parse', '--show-toplevel'],\n"
        "                       capture_output=True, text=True)\n"
        "    assert r.returncode != 0, r.stdout\n")
    (sdir / "gen" / "test_gen.py").write_text(
        "def test_gen():\n    assert True\n")
    st = types.SimpleNamespace(repo_root=repo,
                               run_dir=repo / ".leerie" / "runs" / "r1",
                               data={})
    sets = [{"index": 1, "dir": str(sdir),
             "defect_files": ["tests/test_add.py"],
             "control_files": ["tests/test_repo.py", "gen/test_gen.py"],
             "support_files": []}]
    return repo, st, sets


def _run(cmd: str, *args: str, cwd: Path) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items()
           if k not in ("GIT_DIR", "GIT_WORK_TREE")}
    return subprocess.run([cmd, *args], cwd=cwd, capture_output=True,
                          text=True, env=env, timeout=300)


def test_the_real_command_checks_the_working_tree_from_outside_it(
        leerie, tmp_path):
    repo, st, sets = _fixture(tmp_path)
    sc = leerie._selfcheck_prepare(st, dict(leerie.DEFAULT_CAPS), repo, sets,
                                   "delivery-redrive-r1-g0")
    # The shown source is readable outside the tree, and nothing is in it.
    assert (Path(sc["src"]) / "set-1" / "tests" / "test_add.py").is_file()
    assert sc["name_flag"] == "-k"

    r = _run(sc["command"], cwd=repo)
    assert r.returncode == 1, r.stdout          # the defect still fails
    assert "1 failed" in r.stdout and "SELF-CHECK FAILED" in r.stdout
    # The worker's uncommitted fix is what the copy runs.
    (repo / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    r = _run(sc["command"], cwd=repo)
    assert r.returncode == 0, r.stdout
    # All four ran: the ignored package was shared, `gen/` was a real
    # directory in the copy, git found no repository from the copy.
    assert "4 passed" in r.stdout, r.stdout

    # Part of the set: one file by name, then a name filter.
    r = _run(sc["command"], "test_add.py", cwd=repo)
    assert r.returncode == 0 and "2 passed" in r.stdout, r.stdout
    r = _run(sc["command"], "-t", "zero", cwd=repo)
    assert r.returncode == 0 and "1 passed" in r.stdout, r.stdout

    # Nothing was written to the worker's tree: the only change is its own.
    # No set file and no byte of one anywhere in the worker's tree, ignored
    # paths included (the run directory sits inside it here, as it does
    # outside the container, and holds the shown source by design).
    for dp, dn, fn in os.walk(repo):
        dn[:] = [d for d in dn if d not in (".git", ".leerie")]
        for f in fn:
            assert not f.startswith(("test_add", "test_repo", "test_gen")), (
                dp, f)
            assert MARK.encode() not in Path(dp, f).read_bytes(), (dp, f)
    assert _git(repo, "status", "--porcelain",
                "--untracked-files=all") == " M calc.py\n"
    assert not (repo / "gen" / "test_gen.py").exists()
    assert not (repo / "tests").exists()
    assert sorted(os.listdir(repo / "gen")) == ["built.txt"]
    # The copies are gone; the call log stays.
    assert [p.name for p in Path(sc["dir"], "run").iterdir()] \
        == ["calls.ndjson"]

    # A set path the tree already has is refused, never overwritten.
    (repo / "tests").mkdir()
    (repo / "tests" / "test_add.py").write_text("mine\n")
    r = _run(sc["command"], cwd=repo)
    assert r.returncode == 3 and "tests/test_add.py" in r.stdout, r.stdout
    assert (repo / "tests" / "test_add.py").read_text() == "mine\n"

    calls = leerie._selfcheck_calls(sc)
    assert [c["rc"] for c in calls] == [1, 0, 0, 0, 3]
    assert calls[2]["subset"] == ["tests/test_add.py"]
    assert calls[3]["name_filter"] == "zero"
    leerie._selfcheck_cleanup(sc)
    assert not Path(sc["dir"]).exists()


@pytest.mark.parametrize("args,want", [
    ([], ([], None, [])),
    (["test_add.py"], (["tests/test_add.py"], None, [])),
    (["a/test_same.py"], (["x/a/test_same.py"], None, [])),   # a suffix
    (["x/a/test_same.py"], (["x/a/test_same.py"], None, [])),
    (["./tests/test_add.py", "-t", "x y"],
     (["tests/test_add.py"], "x y", [])),
    (["--testNamePattern=adds", "-x"], ([], "adds", ["-x"])),
    (["-k", "a or b", "--maxfail=1"], ([], "a or b", ["--maxfail=1"])),
    # Bench I3-3's rule: what names no set test file passes through.
    (["util.py", "-t"], ([], None, ["util.py", "-t"])),
    (["test_same.py"], "matches more than one set file")])
def test_the_arguments_select_part_of_the_set(leerie, args, want):
    """Path, file name or unique path suffix (FROZEN3's selection): the
    file name `test_same.py` names two set files, the suffix
    `a/test_same.py` only one."""
    run = ["tests/test_add.py", "x/a/test_same.py", "b/test_same.py"]
    got = leerie._selfcheck_args(args, run)
    if isinstance(want, str):
        assert isinstance(got, str) and want in got, got
    else:
        assert got == want


def test_a_signal_still_deletes_the_copy(leerie, tmp_path):
    """Clean-up runs on every exit: a SIGTERM to the command mid-run (a
    shell timeout, a cancelled call) unwinds through it."""
    import signal
    import time
    repo, st, sets = _fixture(tmp_path)
    (repo / "slow.sh").write_text("sleep 60\n")      # a runner that hangs
    (repo / ".leerie" / "config.toml").write_text(
        'test_files_scoped = "bash slow.sh {test_files}"\n')
    sc = leerie._selfcheck_prepare(st, dict(leerie.DEFAULT_CAPS), repo, sets,
                                   "g")
    snap_root = leerie._selfcheck_copies(st.run_dir, "g")
    p = subprocess.Popen([sc["command"]], cwd=repo, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True)
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline and not (
            snap_root.is_dir() and any(snap_root.iterdir())
            and any(Path(d).joinpath("tests", "test_add.py").exists()
                    for d in snap_root.iterdir())):
        time.sleep(0.1)
    assert any(snap_root.iterdir()), "the copy was never made"
    time.sleep(0.5)
    p.send_signal(signal.SIGTERM)
    out, _ = p.communicate(timeout=30)
    assert p.returncode == 128 + signal.SIGTERM, out
    # The copy is gone; the call log stays.
    assert list(snap_root.iterdir()) == []
    assert [p.name for p in Path(sc["dir"], "run").iterdir()] \
        == ["calls.ndjson"]
    assert [c["rc"] for c in leerie._selfcheck_calls(sc)] == [99]


def _all_objects(repo: Path) -> bytes:
    """Every object in the repository's store, reachable or not."""
    ids = subprocess.run(
        ["git", "-C", str(repo), "cat-file", "--batch-all-objects",
         "--batch-check=%(objectname)"], capture_output=True, text=True,
        check=True).stdout.split()
    return subprocess.run(
        ["git", "-C", str(repo), "cat-file", "--batch"],
        input="\n".join(ids).encode() + b"\n", capture_output=True,
        check=True).stdout


def test_no_set_byte_enters_the_object_store_after_a_redrive(
        leerie, tmp_path, monkeypatch):
    """The whole re-drive, its implementer stubbed to do what one does: copy
    the shown set's source into its tree to read it, run the real self-check
    command (which reaches the copy, the sets and the runner), fix, delete
    its copy, commit. Afterwards no byte of a shown or a held-back set is
    in any object of the repository, reachable or not."""
    w = World(leerie, tmp_path, n_sets=4)
    held_mark = MARK + "_HELD"
    for s_ in w.sets:
        for rel in s_["defect_files"] + s_["control_files"]:
            f = Path(s_["dir"]) / rel
            mark = held_mark if rel == "src/h.test.ts" else MARK
            f.write_text(f.read_text() + f"// {mark}\n")
    w.install(monkeypatch)
    ran: list[subprocess.CompletedProcess] = []
    real = w.L.claude_p

    async def worker(**kw):
        path = (kw.get("env_extra") or {}).get("PATH", "")
        if path and "-clean" not in kw["sid"]:
            cmd = os.path.join(path.split(os.pathsep)[0], "leerie-selfcheck")
            src = next(Path(w.st.run_dir, "selfcheck").glob(
                "*/sets/set-*/src/a.test.ts"))
            mine = w.staging / "src" / "copy_of_set.ts"
            mine.write_text(src.read_text())
            ran.append(_run(cmd, cwd=w.staging))
            mine.unlink()
        return await real(**kw)
    monkeypatch.setattr(w.L, "claude_p", worker)
    w.edits = [lambda w: w.write("export const a = 1; // FIXED\n"),
               lambda w: w.write("export const a = 1; // FIXED GENERAL\n")]
    w.run()
    assert ran and all("SELF-CHECK" in r.stdout for r in ran), ran
    assert w.st.data["delivery_redrive"]["done"]
    blob = _all_objects(w.repo)
    assert MARK.encode() not in blob
    assert held_mark.encode() not in blob


def test_the_copy_reads_a_dash_led_name_as_a_file(leerie, tmp_path):
    """The copy's `tar` takes the tree's file names on stdin, NUL-separated
    (`--null`): a name that looks like a `tar` option is copied as a file
    and runs nothing — the property tests/test_blt_forbidden_gate.py
    relies on in counting `tar` among the programs leerie itself chose."""
    tree = tmp_path / "t"
    tree.mkdir()
    _git(tree, "init", "-q")
    names = ["--checkpoint=1", "--checkpoint-action=exec=touch PWNED",
             "ok.txt"]
    for n in names:
        (tree / n).write_text("x\n")
    snap = tmp_path / "snap"
    snap.mkdir()
    assert leerie._selfcheck_snapshot(str(tree), snap) == 3
    assert sorted(os.listdir(snap)) == sorted(names)
    assert not (tree / "PWNED").exists() and not (snap / "PWNED").exists()



# --- the copy is a tree of its own (DESIGN §8 *The self-check's copy is a
# tree of its own*) ----------------------------------------------------------
#
# navegando a70fb4a6 (lane SR): the copy shared staging's ignored paths as
# symlinks into staging, and Turbopack refused to start the app's server in
# it ("Symlink [project]/node_modules is invalid, it points out of the
# filesystem root"), so a probe run through the browser suite failed on the
# fix as on the defect. The set test below checks what such a tool checks:
# that nothing in its directory resolves outside it.

_ROOT_TEST = '''\
import os


def test_nothing_here_resolves_outside_this_tree():
    root = os.path.realpath(os.getcwd())
    out = []
    for dp, dn, fn in os.walk(root):
        for n in dn + fn:
            p = os.path.join(dp, n)
            r = os.path.realpath(p)
            if r != root and not r.startswith(root + os.sep):
                out.append((os.path.relpath(p, root), r))
    assert out == []


def test_the_ignored_package_is_this_trees_and_its_link_too():
    assert not os.path.islink("vendored")
    assert os.path.isdir("vendored/deep")
    assert os.readlink("vendored/alias.py") == "__init__.py"
    from vendored import ONE
    from vendored.alias import ONE as SAME
    assert ONE == SAME == 1
    # A file the copy makes in a shared directory is the copy's alone.
    with open("vendored/made_by_the_run.txt", "w") as f:
        f.write("x")
'''


def _root_fixture(tmp_path: Path):
    repo, st, sets = _fixture(tmp_path)
    (repo / "vendored" / "deep").mkdir()
    (repo / "vendored" / "deep" / "data.txt").write_text("deep\n")
    os.symlink("__init__.py", repo / "vendored" / "alias.py")
    tdir = Path(sets[0]["dir"]) / "tests"
    (tdir / "test_root.py").write_text(_ROOT_TEST)
    sets[0]["control_files"].append("tests/test_root.py")
    return repo, st, sets


def test_the_copy_holds_no_link_out_of_itself(leerie, tmp_path):
    repo, st, sets = _root_fixture(tmp_path)
    (repo / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    sc = leerie._selfcheck_prepare(st, dict(leerie.DEFAULT_CAPS), repo, sets,
                                   "delivery-redrive-r1-g0")
    r = _run(sc["command"], cwd=repo)
    assert r.returncode == 0, r.stdout
    assert "6 passed" in r.stdout, r.stdout
    # The tree is as it was: the copy's new file never reached it, and its
    # link is still its own.
    assert sorted(os.listdir(repo / "vendored")) == [
        "__init__.py", "alias.py", "deep"]
    assert os.readlink(repo / "vendored" / "alias.py") == "__init__.py"
    # Every file shared was linked, none copied, and the copy is gone.
    call = leerie._selfcheck_calls(sc)[0]
    rec = [json.loads(ln) for ln in Path(
        sc["dir"], "run", "calls.ndjson").read_text().splitlines()][0]
    assert call["rc"] == 0
    assert rec["linked"] == 2 and rec["copied"] == 0, rec
    assert rec["shared"] == 1, rec        # `vendored/`; `gen/` holds a set
    assert list(leerie._selfcheck_copies(st.run_dir, "delivery-redrive-r1-g0"
                                         ).iterdir()) == []
    leerie._selfcheck_cleanup(sc)
    assert not leerie._selfcheck_copies(st.run_dir,
                                        "delivery-redrive-r1-g0").exists()


def test_a_shared_file_is_the_trees_own_inode(leerie, tmp_path):
    """Linked, not copied: nothing installed is duplicated per call."""
    repo, _st, _sets = _fixture(tmp_path)
    snap = tmp_path / "snap"
    snap.mkdir()
    got = leerie._share_ignored_paths(repo, snap)
    assert "vendored" in got["paths"] and got["copied"] == 0
    for rel in ("vendored/__init__.py", "gen/built.txt"):
        assert (snap / rel).stat().st_ino == (repo / rel).stat().st_ino
    assert not (snap / "vendored").is_symlink()


def test_a_file_that_cannot_be_linked_is_copied(leerie, tmp_path,
                                                 monkeypatch):
    """Where the filesystem refuses a link (two mounts: `EXDEV`), the copy
    still holds the file's bytes, and says it copied them."""
    import errno as _errno
    repo, _st, _sets = _fixture(tmp_path)
    snap = tmp_path / "snap"
    snap.mkdir()

    def refuse(*_a, **_k):
        raise OSError(_errno.EXDEV, "Invalid cross-device link")
    monkeypatch.setattr(leerie.os, "link", refuse)
    got = leerie._share_ignored_paths(repo, snap)
    assert got["linked"] == 0 and got["copied"] == 2, got
    assert (snap / "vendored" / "__init__.py").read_text() == "ONE = 1\n"
    assert (snap / "vendored" / "__init__.py").stat().st_ino != \
        (repo / "vendored" / "__init__.py").stat().st_ino


def test_sharing_follows_no_link_the_tree_holds(leerie, tmp_path):
    """A link in an ignored path stays that link: the file it names
    elsewhere is never linked or copied into the copy."""
    repo, _st, _sets = _fixture(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret\n")
    os.symlink(outside, repo / "vendored" / "elsewhere")
    snap = tmp_path / "snap"
    snap.mkdir()
    leerie._share_ignored_paths(repo, snap)
    assert os.readlink(snap / "vendored" / "elsewhere") == str(outside)
    assert (outside / "secret.txt").stat().st_nlink == 1


def test_an_ignored_path_holding_the_copy_is_not_shared(leerie, tmp_path):
    """The run directory inside the tree (outside the container) holds the
    copy: sharing it would copy the copy into itself."""
    repo, st, _sets = _fixture(tmp_path)
    snap = leerie._selfcheck_copies(st.run_dir, "g") / "sc-1"
    snap.mkdir(parents=True)
    # The run directory holds the run's files, so git lists it.
    (st.run_dir / "state.json").write_text("{}\n")
    assert ".leerie/runs/" in leerie._untracked_listing(
        repo, which="ignored", directory=True)
    got = leerie._share_ignored_paths(repo, snap)
    assert ".leerie/runs" not in got["paths"]
    assert not (snap / ".leerie").exists()


def test_the_implementer_writes_its_copies_beside_the_tree(leerie, run):
    """The copies are granted beneath the link root, with the tree they
    copy, so the shim binds both as one mount and a hard link between them
    is no `EXDEV` (DESIGN §6 *The write grants on one mount are one
    mount*)."""
    from test_worker_confinement import _grants
    sc_dir = run["run_dir"] / "selfcheck" / "delivery-redrive-r1-g0"
    g = _grants(leerie, run, cwd=run["wts"]["staging"], selfcheck_dir=sc_dir)
    copies = leerie._selfcheck_copies(run["run_dir"].resolve(),
                                      "delivery-redrive-r1-g0")
    assert copies.is_dir() and str(copies) in g.rw
    link = Path(g.link)
    assert copies.is_relative_to(link)
    assert run["wts"]["staging"].resolve().is_relative_to(link)
    assert str(run["wts"]["staging"].resolve()) in g.rw
