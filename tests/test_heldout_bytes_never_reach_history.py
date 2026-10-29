"""Review round 11 H1: no byte derived from a staged held-out set reaches
history (DESIGN §8 *No byte derived from a staged set reaches history*).

The reviewer's driver: a re-drive implementer runs the staged held-out test
as its note tells it to, in a repository that does not ignore
`__pycache__/`; pytest compiles the hidden test, and the leftovers commit
took `tests/__pycache__/test_hidden_accept.cpython-*.pyc`, whose constants
carry the hidden assertion message into `git show`. The void check compared
staged file NAMES only, so the group stood. Real git and real pytest."""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(__file__))
from test_delivery_redrive import DEFECT, World  # noqa: E402
from test_redrive_test_id_diff_refresh import _git  # noqa: E402

SECRET = "SECRET_HELDOUT_EXPECTATION"
HIDDEN = ("from mod import f\n\ndef test_secret_case():\n"
          f"    assert f() == 2, '{SECRET}'\n")


def _repo(tmp_path, monkeypatch):
    monkeypatch.delenv("PYTHONDONTWRITEBYTECODE", raising=False)
    s = tmp_path / "s"
    (s / "tests").mkdir(parents=True)
    _git(s, "init", "-q", "-b", "main")
    _git(s, "config", "user.email", "t@t")
    _git(s, "config", "user.name", "t")
    (s / "mod.py").write_text("def f():\n    return 1\n")
    (s / "tests" / "test_a.py").write_text("def test_a():\n    assert 1\n")
    _git(s, "add", "-A")
    _git(s, "commit", "-qm", "base")
    return s


def _held(tmp_path, rel="tests/test_hidden_accept.py"):
    held = tmp_path / "held"
    (held / os.path.dirname(rel)).mkdir(parents=True, exist_ok=True)
    (held / rel).write_text(HIDDEN)
    return {"index": 0, "dir": str(held), "defect_files": [rel],
            "cases": {rel: ["test_secret_case"]}}


def _pytest(s, *args):
    subprocess.run([sys.executable, "-m", "pytest", "-q", "-p",
                    "no:cacheprovider", *args], cwd=s, capture_output=True,
                   env={**os.environ, "PYTHONPATH": str(s)})


def _history_bytes(s, since):
    """Every blob of every commit since `since`, concatenated."""
    out = b""
    for c in _git(s, "rev-list", f"{since}..HEAD").split():
        for p in _git(s, "show", "--name-only", "--format=", c).split():
            out += subprocess.run(["git", "-C", str(s), "show", f"{c}:{p}"],
                                  capture_output=True).stdout
    return out


def test_the_reviewers_driver_ships_no_bytecode_of_the_hidden_test(
        leerie, tmp_path, monkeypatch):
    s = _repo(tmp_path, monkeypatch)
    base = _git(s, "rev-parse", "HEAD")
    set_ = _held(tmp_path)
    staged = leerie._redrive_stage(s, [set_])
    assert staged
    markers = leerie._heldout_markers(s, staged, {0: set_}, base)
    before = (leerie._tracked_content(s), leerie._untracked_signatures(s))
    (s / "mod.py").write_text("def f():\n    return 2\n")
    _pytest(s, "tests/test_hidden_accept.py")
    # The staged file's bytecode is ignored while the group runs, so the
    # implementer's own `git add -A` cannot take it either.
    assert "test_hidden_accept" not in _git(s, "status", "--porcelain",
                                            "--untracked-files=all")
    _git(s, "add", "-A")
    _git(s, "commit", "-qm", "fix")
    swept: list[str] = []
    assert leerie._redrive_unstage(s, staged, swept=swept) == []
    assert any("test_hidden_accept" in p and p.endswith(".pyc")
               for p in swept)
    got = asyncio.run(leerie._redrive_commit_leftovers(
        s, "r1 g0", *before, set(), markers=markers))
    assert not any("test_hidden_accept" in p for p in got)
    assert SECRET.encode() not in _history_bytes(s, base)
    assert not list((s / "tests").rglob("test_hidden_accept*"))
    assert leerie._history_heldout_hits(s, base, _git(s, "rev-parse", "HEAD"),
                                        staged, markers) == []


def test_a_file_holding_the_sets_bytes_is_removed_not_committed(
        leerie, tmp_path, monkeypatch):
    """Rule 2: a report the self-check wrote outside the staged directory,
    naming the hidden case and its message (a junit file, a runner's
    node-ID cache), is removed before the leftovers commit; the
    implementer's own new source beside it still ships."""
    s = _repo(tmp_path, monkeypatch)
    base = _git(s, "rev-parse", "HEAD")
    set_ = _held(tmp_path)
    staged = leerie._redrive_stage(s, [set_])
    markers = leerie._heldout_markers(s, staged, {0: set_}, base)
    before = (leerie._tracked_content(s), leerie._untracked_signatures(s))
    (s / "report.xml").write_text(
        f"<testcase name='test_secret_case'><failure>{SECRET}</failure>")
    (s / "nodeids").write_text("tests/test_hidden_accept.py::x\n")
    (s / "helper.py").write_text("H = 1\n")
    leerie._redrive_unstage(s, staged)
    withheld: dict = {}
    got = asyncio.run(leerie._redrive_commit_leftovers(
        s, "r1 g0", *before, set(), markers=markers, withheld=withheld))
    assert got == ["helper.py"]
    assert sorted(withheld["heldout"]) == ["nodeids", "report.xml"]
    assert not (s / "report.xml").exists() and not (s / "nodeids").exists()


def test_markers_already_in_the_base_are_not_distinctive(leerie, tmp_path,
                                                         monkeypatch):
    s = _repo(tmp_path, monkeypatch)
    (s / "mod.py").write_text("ERR = 'shared message text'\n"
                              "def f():\n    return 1\n")
    _git(s, "commit", "-qam", "shared")
    base = _git(s, "rev-parse", "HEAD")
    held = tmp_path / "held"
    (held / "tests").mkdir(parents=True)
    (held / "tests" / "test_h.py").write_text(
        "def test_only_here():\n    assert 'shared message text'\n"
        f"    assert 1, '{SECRET}'\n    assert 'short'\n")
    set_ = {"index": 0, "dir": str(held), "defect_files": ["tests/test_h.py"]}
    markers = leerie._heldout_markers(
        s, [{"set_index": 0, "dir": str(held), "files": ["tests/test_h.py"],
             "made": []}], {0: set_}, base)
    assert SECRET.encode() in markers
    assert b"test_only_here" in markers and b"tests/test_h.py" in markers
    assert b"shared message text" not in markers
    assert b"short" not in markers


def test_every_commit_is_read_not_the_net_diff(leerie, tmp_path,
                                               monkeypatch):
    """Rule 3: a file that copies the hidden message, committed and then
    deleted, is still in the history; an unrelated commit is not a hit."""
    s = _repo(tmp_path, monkeypatch)
    base = _git(s, "rev-parse", "HEAD")
    set_ = _held(tmp_path)
    staged = leerie._redrive_stage(s, [set_])
    markers = leerie._heldout_markers(s, staged, {0: set_}, base)
    (s / "mod.py").write_text("def f():\n    return 2\n")
    _git(s, "add", "mod.py")
    _git(s, "commit", "-qm", "fix")
    head = _git(s, "rev-parse", "HEAD")
    assert leerie._history_heldout_hits(s, base, head, staged, markers) == []
    (s / "notes.md").write_text(f"expects {SECRET}\n")
    _git(s, "add", "notes.md")
    _git(s, "commit", "-qm", "copy")
    _git(s, "rm", "-q", "notes.md")
    _git(s, "commit", "-qm", "gone")
    assert leerie._history_heldout_hits(
        s, base, _git(s, "rev-parse", "HEAD"), staged, markers) == [
            "notes.md"]
    leerie._redrive_unstage(s, staged)


def test_a_directory_staging_created_is_staged_material_whole(
        leerie, tmp_path, monkeypatch):
    """Rule 1: everything that appears under a directory staging had to
    create is ignored while the group runs and removed with it."""
    s = _repo(tmp_path, monkeypatch)
    set_ = _held(tmp_path, rel="acc/deep/test_hidden_accept.py")
    staged = leerie._redrive_stage(s, [set_])
    assert staged[0]["made"] == ["acc"]
    (s / "acc" / "deep" / "conftest_copy.py").write_text("X = 1\n")
    (s / "acc" / "other.txt").write_text("y\n")
    assert _git(s, "status", "--porcelain", "--untracked-files=all") == ""
    swept: list[str] = []
    leerie._redrive_unstage(s, staged, swept=swept)
    assert sorted(swept) == ["acc/deep/conftest_copy.py", "acc/other.txt"]
    assert not (s / "acc").exists()
    assert _git(s, "status", "--porcelain", "--untracked-files=all") == ""


def test_a_group_whose_commit_carries_the_sets_bytes_is_voided(
        leerie, tmp_path, monkeypatch):
    """End to end: the implementer fixes the defect and also commits a
    file quoting the hidden expectation, under a name that is no staged
    path. Before, `committed_staged` (names only) was empty and the group
    stood; now it is voided and rolled back, and the next group's honest
    fix is kept."""
    w = World(leerie, tmp_path)
    secret = "SECRET_EXPECTATION_XYZ"
    sdir = w.sets[0]["dir"]
    with open(os.path.join(sdir, DEFECT), "w") as f:
        f.write("import { a } from './a';\n"
                f"it('x', () => expect(a).toBe('{secret}'));\n")
    w.install(monkeypatch)
    start = _git(w.staging, "rev-parse", "HEAD")

    def leak(w):
        (w.staging / "src" / "notes.ts").write_text(f"// {secret}\n")
        w.write("export const a = 1; // FIXED\n")
        _git(w.staging, "add", "src/notes.ts")
        _git(w.staging, "commit", "-qm", "notes")

    def honest(w):
        w.write("export const a = 1; // FIXED\n")
    w.edits = [leak, honest]
    w.run()
    groups = [g for r in w.st.data["delivery_redrive"]["rounds"]
              for g in r["groups"] if "pre_sha" in g]
    assert groups[0]["committed_staged"] == ["src/notes.ts"]
    assert groups[0]["rolled_back"]
    assert secret.encode() not in _history_bytes(w.staging, start)
    assert "FIXED" in w.a_text("HEAD")
