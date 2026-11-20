"""A held-out set is validated in a tree leerie made at the own start, never
in the writer's checkout (DESIGN §8 *Every measurement reads the committed
view*, *The validity tree is the run's own start*): the writer's files are
read out of its checkout once and placed in each run's sandbox: the files
the set carries and its declared files, nothing else the writer left.

Real git and real pytest, the held-out sandbox through conftest's emulator
(its placement contract, not its isolation); only `claude_p` is stubbed.
"""
from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path

import pytest

from tests.conftest import run_git_cwd_first_stdout as _git
from tests.test_acceptance_gate import (_IMPORT_DEFECT, CONTROL_TEST,
                                        DEFECT_TEST, EFFORTS, MODELS,
                                        NONDISCRIMINATING, _caps,
                                        _import_writer, _repo, _st,
                                        _writer_stub)

# A control that passes only where a file under `.venv/` sits beside the
# repository root: a provisioned environment's path, which a set never
# carries (`_acceptance_is_cache_path`).
CONTROL_NEEDS_VENV_FILE = (
    "import pathlib\n\n"
    "def test_flag_there():\n"
    "    root = pathlib.Path(__file__).resolve().parent.parent\n"
    "    assert (root / '.venv' / 'flag').read_text() == 'on'\n")


@pytest.fixture(autouse=True)
def _clear_deps_memo(leerie, monkeypatch):
    leerie._DEPS_INSTALLED.clear()
    monkeypatch.setattr(leerie, "_host_cpu_psi", lambda *a, **k: 0.0)
    yield
    leerie._DEPS_INSTALLED.clear()


def _write(leerie, st):
    acc = asyncio.run(leerie.phase_acceptance_write(
        st.data["task"], st, _caps(leerie, 1), MODELS, EFFORTS))
    return acc


def test_a_file_the_set_does_not_carry_does_not_reach_validation(
        leerie, tmp_path, monkeypatch):
    """The writer leaves `.venv/flag` in its checkout and a control that
    needs it. Validated in the checkout, the control passed there and the
    set was valid — and then failed everywhere else, since the flag does not
    travel. Validated in leerie's tree with only the carried files placed,
    the control fails on the base and the set is discarded."""
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head)
    _writer_stub(leerie, monkeypatch, {1: {
        "acc/test_defect_sum.py": ("defect", DEFECT_TEST, ["sums"]),
        ".venv/flag": ("control", "on", ["not a test"]),
        "acc/test_control_flag.py": ("control", CONTROL_NEEDS_VENV_FILE,
                                     ["flag"]),
    }})
    # `.venv/flag` is declared as a file too, so drop it from the
    # declaration: it is left in the tree, not offered as a test.
    real = leerie.claude_p

    async def writer(**kw):
        res = await real(**kw)
        res["files"] = [f for f in res["files"]
                        if not f["path"].startswith(".venv/")]
        return res
    monkeypatch.setattr(leerie, "claude_p", writer)

    acc = _write(leerie, st)
    assert acc["sets"] == []
    (d,) = acc["discarded"]
    assert d["reason"] == ("control file acc/test_control_flag.py does not "
                           "pass on base")


def test_validation_runs_in_leerie_s_tree_with_the_set_placed(
        leerie, tmp_path, monkeypatch):
    """Every validation run names a tree that is not the writer's checkout,
    at the own start even when the user's branch has moved on, and places
    the set's files from leerie's own copy of them — not the task's report,
    which the set does not carry."""
    repo, head = _repo(tmp_path)
    # The report as a run usually finds it: untracked in the user's checkout.
    (repo / "notes.md").write_text("add(2, 3) returns -1\n")
    # The user's branch moves past the own start: the view is still `head`.
    (repo / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    _git(repo, "commit", "-qam", "fixed later")
    st = _st(leerie, tmp_path, repo, head,
             task="fix the bug in notes.md")
    _writer_stub(leerie, monkeypatch, {1: {
        "acc/test_defect_sum.py": ("defect", DEFECT_TEST, ["sums"]),
        "acc/test_control_zero.py": ("control", CONTROL_TEST, ["zero"]),
    }})
    runs = []
    real = leerie._run_acceptance_file

    async def spy(st_, caps, tree, rel, *a, **kw):
        # What each placement holds, read while the run happens.
        runs.append((tree, rel, {r: (s, Path(s).read_text())
                                 for s, r in kw.get("place") or ()}))
        return await real(st_, caps, tree, rel, *a, **kw)
    monkeypatch.setattr(leerie, "_run_acceptance_file", spy)

    s = asyncio.run(leerie._write_acceptance_set(
        1, st.data["task"], st, _caps(leerie, 1), MODELS, EFFORTS, head))
    assert s["valid"] is True, s
    assert s["defect_files"] == ["acc/test_defect_sum.py"]
    assert s["control_files"] == ["acc/test_control_zero.py"]
    writer_wt = leerie._helper_worktree_path(st, "acceptance-1")
    view = leerie._helper_worktree_path(st, "acceptance-1-view")
    assert [(t, r) for t, r, _p in runs] == [
        (str(view), "acc/test_defect_sum.py"),
        (str(view), "acc/test_control_zero.py")]
    written = st.run_dir / "acceptance" / "set-1.written"
    for _t, _r, placed in runs:
        assert {r: text for r, (_s, text) in placed.items()} == {
            "acc/test_control_zero.py": CONTROL_TEST,
            "acc/test_defect_sum.py": DEFECT_TEST}
        assert all(Path(src).parent == written
                   for src, _text in placed.values())
    # Both trees and leerie's copy are gone; the set holds what it carries.
    assert not writer_wt.exists() and not view.exists()
    assert not written.exists()
    assert sorted(str(p.relative_to(s["dir"]))
                  for p in Path(s["dir"]).rglob("*") if p.is_file()) == [
        "acc/test_control_zero.py", "acc/test_defect_sum.py"]
    assert _git(repo, "status", "--porcelain") == "?? notes.md"


READS_THE_REPORT = (
    "import pathlib\n\n"
    "def test_report_there():\n"
    "    root = pathlib.Path(__file__).resolve().parent.parent\n"
    "    assert (root / 'notes.md').is_file()\n")


def test_a_set_that_needs_the_task_s_report_is_not_valid(
        leerie, tmp_path, monkeypatch):
    """The report is copied into the writer's checkout for the writer to
    read, but no set carries it: a control that needs it fails on the base
    once validation places only the set's files. Validated in the checkout,
    it passed, and then failed on every fix. Nothing of leerie's copy
    survives the discarded set."""
    repo, head = _repo(tmp_path)
    (repo / "notes.md").write_text("add(2, 3) returns -1\n")
    st = _st(leerie, tmp_path, repo, head, task="fix the bug in notes.md")

    async def writer(**kw):
        wt = Path(kw["cwd"])
        assert (wt / "notes.md").is_file()      # the writer still reads it
        (wt / "acc").mkdir()
        (wt / "acc" / "test_defect_sum.py").write_text(DEFECT_TEST)
        (wt / "acc" / "test_control_report.py").write_text(READS_THE_REPORT)
        return {"files": [
            {"path": "acc/test_defect_sum.py", "kind": "defect",
             "cases": ["sums"], "failure_mode": "assertion"},
            {"path": "acc/test_control_report.py", "kind": "control",
             "cases": ["report"], "failure_mode": "assertion"}]}
    monkeypatch.setattr(leerie, "claude_p", writer)
    s = asyncio.run(leerie._write_acceptance_set(
        1, st.data["task"], st, _caps(leerie, 1), MODELS, EFFORTS, head))
    assert s["valid"] is False
    assert s["reason"] == ("control file acc/test_control_report.py does "
                           "not pass on base")
    assert sorted(p.name for p in (st.run_dir / "acceptance").iterdir()) \
        == []


def test_a_writer_that_declares_nothing_makes_no_tree(leerie, tmp_path,
                                                      monkeypatch):
    """The tree is made only when a collection has a file to validate — and
    a crashed attempt's tree is removed all the same."""
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head)
    stale = leerie._helper_worktree_path(st, "acceptance-1-view")
    stale.mkdir(parents=True)
    (stale / "left.txt").write_text("from a crashed attempt\n")
    _writer_stub(leerie, monkeypatch, {1: {}})
    made = []
    real = leerie._acceptance_tree

    def spy(st_, caps, rev, name, label):
        made.append(name)
        return real(st_, caps, rev, name, label)
    monkeypatch.setattr(leerie, "_acceptance_tree", spy)
    acc = _write(leerie, st)
    assert acc["sets"] == [] and made == []
    assert not stale.exists()


CONTROL_NEEDS_BUILD_OUTPUT = (
    "import pathlib\n\n"
    "def test_built():\n"
    "    root = pathlib.Path(__file__).resolve().parent.parent\n"
    "    assert (root / 'build' / 'out.txt').read_text() == 'built'\n")


def test_ignored_output_away_from_the_tests_is_not_placed(
        leerie, tmp_path, monkeypatch):
    """An ignored file away from the declared tests (build output from the
    unfixed tree) is not carried, so it is not placed either: a control that
    needs it fails on the base. Validated in the checkout, it passed."""
    repo, _head = _repo(tmp_path)
    (repo / ".gitignore").write_text("__pycache__/\nbuild/\n")
    _git(repo, "commit", "-qam", "ignore build output")
    head = _git(repo, "rev-parse", "HEAD")
    st = _st(leerie, tmp_path, repo, head)

    async def writer(**kw):
        wt = Path(kw["cwd"])
        for rel, text in (("acc/test_defect_sum.py", DEFECT_TEST),
                          ("acc/test_control_built.py",
                           CONTROL_NEEDS_BUILD_OUTPUT),
                          ("build/out.txt", "built")):
            (wt / rel).parent.mkdir(parents=True, exist_ok=True)
            (wt / rel).write_text(text)
        return {"files": [
            {"path": "acc/test_defect_sum.py", "kind": "defect",
             "cases": ["sums"], "failure_mode": "assertion"},
            {"path": "acc/test_control_built.py", "kind": "control",
             "cases": ["built"], "failure_mode": "assertion"}]}
    monkeypatch.setattr(leerie, "claude_p", writer)
    s = asyncio.run(leerie._write_acceptance_set(
        1, st.data["task"], st, _caps(leerie, 1), MODELS, EFFORTS, head))
    assert s["valid"] is False
    assert s["reason"] == ("control file acc/test_control_built.py does not "
                           "pass on base")


def test_a_support_file_that_cannot_be_read_discards_the_set(
        leerie, tmp_path, monkeypatch):
    """A support file the set would carry, left as a link, cannot be read
    out of the checkout without following it: the set is discarded rather
    than validated or carried without it."""
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head)

    async def writer(**kw):
        wt = Path(kw["cwd"])
        (wt / "acc").mkdir()
        (wt / "acc" / "test_defect_sum.py").write_text(DEFECT_TEST)
        (wt / "acc" / "test_control_zero.py").write_text(CONTROL_TEST)
        (wt / "acc" / "data.json").symlink_to(wt / "calc.py")
        return {"files": [
            {"path": "acc/test_defect_sum.py", "kind": "defect",
             "cases": ["sums"], "failure_mode": "assertion"},
            {"path": "acc/test_control_zero.py", "kind": "control",
             "cases": ["zero"], "failure_mode": "assertion"}]}
    monkeypatch.setattr(leerie, "claude_p", writer)
    s = asyncio.run(leerie._write_acceptance_set(
        1, st.data["task"], st, _caps(leerie, 1), MODELS, EFFORTS, head))
    assert s["valid"] is False
    assert s["reason"].startswith(
        "could not read acc/data.json from the writer's checkout")


def test_a_declared_file_that_cannot_be_read_is_dropped(leerie, tmp_path,
                                                        monkeypatch):
    """A declared file left as a link is dropped, as a file that does not
    discriminate is; the rest of the set is validated and carried."""
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head)

    async def writer(**kw):
        wt = Path(kw["cwd"])
        (wt / "acc").mkdir()
        (wt / "acc" / "test_defect_sum.py").write_text(DEFECT_TEST)
        (wt / "acc" / "test_control_zero.py").write_text(CONTROL_TEST)
        (wt / "acc" / "test_control_link.py").symlink_to(
            wt / "acc" / "test_control_zero.py")
        return {"files": [
            {"path": "acc/test_defect_sum.py", "kind": "defect",
             "cases": ["sums"], "failure_mode": "assertion"},
            {"path": "acc/test_control_zero.py", "kind": "control",
             "cases": ["zero"], "failure_mode": "assertion"},
            {"path": "acc/test_control_link.py", "kind": "control",
             "cases": ["zero again"], "failure_mode": "assertion"}]}
    monkeypatch.setattr(leerie, "claude_p", writer)
    s = asyncio.run(leerie._write_acceptance_set(
        1, st.data["task"], st, _caps(leerie, 1), MODELS, EFFORTS, head))
    assert s["valid"] is True, s
    assert s["control_files"] == ["acc/test_control_zero.py"]
    assert sorted(str(p.relative_to(s["dir"]))
                  for p in Path(s["dir"]).rglob("*")) == [
        "acc", "acc/test_control_zero.py", "acc/test_defect_sum.py"]


def test_the_parse_probe_checks_only_what_is_placed(leerie, tmp_path,
                                                    monkeypatch):
    """An ignored Python file away from the tests (build output) is not
    placed, so the parse probe does not compile it: the import defect is
    honoured and the set is valid. Compiled from the view, the missing file
    read as a parse failure and discarded the set."""
    repo, _head = _repo(tmp_path)
    (repo / ".gitignore").write_text("__pycache__/\nbuild/\n")
    _git(repo, "commit", "-qam", "ignore build output")
    head = _git(repo, "rev-parse", "HEAD")
    st = _st(leerie, tmp_path, repo, head)
    _import_writer(leerie, monkeypatch, {
        "acc/test_defect_mul.py": (_IMPORT_DEFECT, "import"),
        "build/lib/calc_copy.py": ("VALUE = 6\n", None)})
    targets = []
    real = leerie._acceptance_parse_probe

    async def spy(st_, caps, tree, rel, tg, *a, **kw):
        targets.append(list(tg))
        return await real(st_, caps, tree, rel, tg, *a, **kw)
    monkeypatch.setattr(leerie, "_acceptance_parse_probe", spy)
    s = asyncio.run(leerie._write_acceptance_set(
        1, st.data["task"], st, _caps(leerie, 1), MODELS, EFFORTS, head))
    assert targets == [["acc/test_defect_mul.py",
                        "acc/test_control_zero.py"]]
    assert s["valid"] is True, s
    assert s["modes"] == {"acc/test_defect_mul.py": "import"}
    assert s["support_files"] == []


def test_a_path_holding_an_equals_sign_is_placed_whole(leerie, tmp_path,
                                                       monkeypatch):
    """The shim reads a placement as `SRC=REL` split at the first `=`:
    leerie's copy is named by number, so a writer's path that holds one
    still names the right file."""
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head)
    _writer_stub(leerie, monkeypatch, {1: {
        "acc/year=2024/test_defect_sum.py": ("defect", DEFECT_TEST, ["s"]),
        "acc/year=2024/test_control_zero.py": ("control", CONTROL_TEST,
                                               ["z"]),
    }})
    s = asyncio.run(leerie._write_acceptance_set(
        1, st.data["task"], st, _caps(leerie, 1), MODELS, EFFORTS, head))
    assert s["valid"] is True, s
    assert s["defect_files"] == ["acc/year=2024/test_defect_sum.py"]


def test_a_placed_file_at_the_probe_s_name_takes_it(leerie, tmp_path):
    """The view lacks the writer's files, so a placed one at the probe's
    path is a taken name too, never placed over."""
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head)
    probe = leerie._acceptance_probe_rel(st, "acc/test_x.py")
    got = asyncio.run(leerie._acceptance_parse_probe(
        st, _caps(leerie, 1), str(repo), "acc/test_x.py", ["acc/test_x.py"],
        st.run_dir / "logs" / "p.log", "p",
        place=((str(repo / "calc.py"), probe),)))
    assert got == (None, "the parse check's name is taken in the tree")


def test_the_own_start_retry_reuses_the_one_tree(leerie, tmp_path,
                                                 monkeypatch):
    """The writer's one own-start retry is validated in the same tree,
    made once and removed once, and a tree that cannot be set up discards
    the set with its reason."""
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head)
    async def writer(**kw):
        wt = Path(kw["cwd"])
        (wt / "acc").mkdir(exist_ok=True)
        (wt / "acc" / "test_defect_weak.py").write_text(NONDISCRIMINATING)
        (wt / "acc" / "test_control_zero.py").write_text(CONTROL_TEST)
        return {"files": [
            {"path": "acc/test_defect_weak.py", "kind": "defect",
             "cases": ["weak"], "failure_mode": "assertion"},
            {"path": "acc/test_control_zero.py", "kind": "control",
             "cases": ["zero"], "failure_mode": "assertion"}]}
    monkeypatch.setattr(leerie, "claude_p", writer)
    made, trees = [], []
    real = leerie._acceptance_tree
    real_run = leerie._run_acceptance_file

    def spy(st_, caps, rev, name, label):
        made.append((rev, name))
        return real(st_, caps, rev, name, label)

    async def run_spy(st_, caps, tree, rel, *a, **kw):
        trees.append(tree)
        return await real_run(st_, caps, tree, rel, *a, **kw)
    monkeypatch.setattr(leerie, "_acceptance_tree", spy)
    monkeypatch.setattr(leerie, "_run_acceptance_file", run_spy)
    s = asyncio.run(leerie._write_acceptance_set(
        1, st.data["task"], st, _caps(leerie, 1), MODELS, EFFORTS, head))
    assert s["reason"] == "fixed_on_head_by_execution"
    assert s["own_start_rewrite"] is True
    assert made == [(head, "acceptance-1-view")]
    view = str(leerie._helper_worktree_path(st, "acceptance-1-view"))
    assert trees == [view] * 4          # two files, two collections
    assert not Path(view).exists()

    @contextlib.asynccontextmanager
    async def no_tree(*_a, **_k):
        yield None
    monkeypatch.setattr(leerie, "_acceptance_tree", no_tree)
    s = asyncio.run(leerie._write_acceptance_set(
        1, st.data["task"], st, _caps(leerie, 1), MODELS, EFFORTS, head))
    assert s["valid"] is False
    assert s["reason"] == "the validity tree could not be set up"


def test_a_failure_mid_validation_leaves_nothing_behind(leerie, tmp_path,
                                                        monkeypatch):
    """A validation run that raises loses only this set, and the writer's
    checkout, the view and leerie's copy of the files are all removed."""
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head)
    _writer_stub(leerie, monkeypatch, {1: {
        "acc/test_defect_sum.py": ("defect", DEFECT_TEST, ["sums"]),
        "acc/test_control_zero.py": ("control", CONTROL_TEST, ["zero"]),
    }})
    seen = []

    async def boom(st_, caps, tree, rel, *a, **kw):
        seen.append(tree)
        raise RuntimeError("runner exploded")
    monkeypatch.setattr(leerie, "_run_acceptance_file", boom)
    s = asyncio.run(leerie._write_acceptance_set(
        1, st.data["task"], st, _caps(leerie, 1), MODELS, EFFORTS, head))
    assert s["valid"] is False
    assert s["reason"] == "RuntimeError: runner exploded"
    view = leerie._helper_worktree_path(st, "acceptance-1-view")
    assert seen == [str(view)]
    assert not view.exists()
    assert not leerie._helper_worktree_path(st, "acceptance-1").exists()
    assert not (st.run_dir / "acceptance" / "set-1.written").exists()
    assert "acceptance-1-view" not in _git(
        leerie._run_repo(st), "worktree", "list")


def test_a_dropped_file_is_not_placed_when_the_rest_is_validated_again(
        leerie, tmp_path, monkeypatch):
    """A declared defect file that passes on the base is dropped, so the
    set does not carry it; a control that imports it passed only beside it.
    The kept files are validated again without it: the control no longer
    runs and the set is discarded, instead of passing here and failing every
    fix."""
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head)
    control = ("from acc.test_defect_weak import test_callable\n\n"
               "def test_reuses_the_weak_one():\n    test_callable()\n")
    _writer_stub(leerie, monkeypatch, {1: {
        "acc/__init__.py": ("control", "", ["package marker"]),
        "acc/test_defect_sum.py": ("defect", DEFECT_TEST, ["sums"]),
        "acc/test_defect_weak.py": ("defect", NONDISCRIMINATING, ["weak"]),
        "acc/test_control_reuse.py": ("control", control, ["reuse"]),
    }})
    real = leerie.claude_p

    async def writer(**kw):
        res = await real(**kw)
        res["files"] = [f for f in res["files"]
                        if f["path"] != "acc/__init__.py"]
        return res
    monkeypatch.setattr(leerie, "claude_p", writer)
    runs = []
    real_run = leerie._run_acceptance_file

    async def spy(st_, caps, tree, rel, *a, **kw):
        runs.append((rel, sorted(r for _s, r in kw.get("place") or ())))
        return await real_run(st_, caps, tree, rel, *a, **kw)
    monkeypatch.setattr(leerie, "_run_acceptance_file", spy)
    s = asyncio.run(leerie._write_acceptance_set(
        1, st.data["task"], st, _caps(leerie, 1), MODELS, EFFORTS, head))
    assert s["valid"] is False
    # Without the dropped file the control cannot even load: no verdict.
    assert s["reason"] == ("could not run acc/test_control_reuse.py on the "
                           "validity base")
    first = ["acc/__init__.py", "acc/test_control_reuse.py",
             "acc/test_defect_sum.py", "acc/test_defect_weak.py"]
    again = ["acc/__init__.py", "acc/test_control_reuse.py",
             "acc/test_defect_sum.py"]
    assert runs == [("acc/test_defect_sum.py", first),
                    ("acc/test_defect_weak.py", first),
                    ("acc/test_control_reuse.py", first),
                    ("acc/test_defect_sum.py", again),
                    ("acc/test_control_reuse.py", again)]


CONTROL_READS_DATA_BESIDE = (
    "import pathlib\n\n"
    "def test_data():\n"
    "    here = pathlib.Path(__file__).resolve().parent\n"
    "    assert (here / 'data.out').read_text() == 'ok'\n")


def test_a_set_validated_again_carries_only_what_it_kept(
        leerie, tmp_path, monkeypatch):
    """`a/` holds a defect file that passes on the base and an ignored data
    file beside it; `b/` holds the discriminating defect
    file and a control that reads the ignored data file beside it. The
    first pass places `b/data.out` (an ignored file beside a declared test)
    and drops `a/test_defect_weak.py`; the second validates `b/` alone. The
    set is valid and carries exactly `b/`: `a/`'s data is no longer
    placed or carried."""
    repo, _head = _repo(tmp_path)
    (repo / ".gitignore").write_text("__pycache__/\n*.out\n")
    _git(repo, "commit", "-qam", "ignore data")
    head = _git(repo, "rev-parse", "HEAD")
    st = _st(leerie, tmp_path, repo, head)

    async def writer(**kw):
        wt = Path(kw["cwd"])
        for d in ("a", "b"):
            (wt / d).mkdir()
        (wt / "a" / "test_defect_weak.py").write_text(NONDISCRIMINATING)
        (wt / "a" / "data.out").write_text("ok")
        (wt / "b" / "test_defect_sum.py").write_text(DEFECT_TEST)
        (wt / "b" / "test_control_data.py").write_text(
            CONTROL_READS_DATA_BESIDE)
        (wt / "b" / "data.out").write_text("ok")
        return {"files": [
            {"path": "a/test_defect_weak.py", "kind": "defect",
             "cases": ["weak"], "failure_mode": "assertion"},
            {"path": "b/test_defect_sum.py", "kind": "defect",
             "cases": ["sums"], "failure_mode": "assertion"},
            {"path": "b/test_control_data.py", "kind": "control",
             "cases": ["data"], "failure_mode": "assertion"}]}
    monkeypatch.setattr(leerie, "claude_p", writer)
    runs = []
    real_run = leerie._run_acceptance_file

    async def spy(st_, caps, tree, rel, *a, **kw):
        runs.append(sorted(r for _s, r in kw.get("place") or ()))
        return await real_run(st_, caps, tree, rel, *a, **kw)
    monkeypatch.setattr(leerie, "_run_acceptance_file", spy)
    s = asyncio.run(leerie._write_acceptance_set(
        1, st.data["task"], st, _caps(leerie, 1), MODELS, EFFORTS, head))
    assert s["valid"] is True, s
    assert s["defect_files"] == ["b/test_defect_sum.py"]
    assert s["control_files"] == ["b/test_control_data.py"]
    assert s["support_files"] == ["b/data.out"]
    first = ["a/data.out", "a/test_defect_weak.py", "b/data.out",
             "b/test_control_data.py", "b/test_defect_sum.py"]
    again = ["b/data.out", "b/test_control_data.py", "b/test_defect_sum.py"]
    assert runs == [first] * 3 + [again] * 2
    assert sorted(str(p.relative_to(s["dir"]))
                  for p in Path(s["dir"]).rglob("*") if p.is_file()) == again


def test_an_unreadable_support_file_never_leads_to_the_writer_s_retry(
        leerie, tmp_path, monkeypatch):
    """An ignored link beside the declared tests cannot be read out of the
    checkout, so it is never placed. The pass run without it finds no
    defect case failing — which, with the file there, might not hold — so
    the set is discarded for the unread file: no `no_defect_fails`, no
    writer retry, no own-start no-work verdict built on it."""
    repo, _head = _repo(tmp_path)
    (repo / ".gitignore").write_text("__pycache__/\n*.gen.py\n")
    _git(repo, "commit", "-qam", "ignore generated")
    head = _git(repo, "rev-parse", "HEAD")
    st = _st(leerie, tmp_path, repo, head)
    calls = []

    async def writer(**kw):
        calls.append(kw["sid"])
        wt = Path(kw["cwd"])
        (wt / "a").mkdir(exist_ok=True)
        (wt / "a" / "test_defect_weak.py").write_text(NONDISCRIMINATING)
        if not (wt / "a" / "fixture.gen.py").is_symlink():
            (wt / "a" / "fixture.gen.py").symlink_to(wt / "calc.py")
        (wt / "a" / "test_control_zero.py").write_text(CONTROL_TEST)
        return {"files": [
            {"path": "a/test_defect_weak.py", "kind": "defect",
             "cases": ["weak"], "failure_mode": "assertion"},
            {"path": "a/test_control_zero.py", "kind": "control",
             "cases": ["zero"], "failure_mode": "assertion"}]}
    monkeypatch.setattr(leerie, "claude_p", writer)
    s = asyncio.run(leerie._write_acceptance_set(
        1, st.data["task"], st, _caps(leerie, 1), MODELS, EFFORTS, head))
    assert s["valid"] is False
    assert s["reason"].startswith(
        "could not read a/fixture.gen.py from the writer's checkout")
    assert "own_start_rewrite" not in s
    assert calls == ["acceptance-1"]


def test_an_unreadable_file_beside_a_dropped_one_does_not_count(
        leerie, tmp_path, monkeypatch):
    """The unread link sits beside a defect file that passes on the base;
    the discriminating files are elsewhere. The first pass drops the weak
    file, the second no longer holds the link, and the set is valid —
    validated, at the end, beside exactly what it carries."""
    repo, _head = _repo(tmp_path)
    (repo / ".gitignore").write_text("__pycache__/\n*.gen.py\n")
    _git(repo, "commit", "-qam", "ignore generated")
    head = _git(repo, "rev-parse", "HEAD")
    st = _st(leerie, tmp_path, repo, head)

    async def writer(**kw):
        wt = Path(kw["cwd"])
        for d in ("a", "b"):
            (wt / d).mkdir()
        (wt / "a" / "test_defect_weak.py").write_text(NONDISCRIMINATING)
        (wt / "a" / "fixture.gen.py").symlink_to(wt / "calc.py")
        (wt / "b" / "test_defect_sum.py").write_text(DEFECT_TEST)
        (wt / "b" / "test_control_zero.py").write_text(CONTROL_TEST)
        return {"files": [
            {"path": "a/test_defect_weak.py", "kind": "defect",
             "cases": ["weak"], "failure_mode": "assertion"},
            {"path": "b/test_defect_sum.py", "kind": "defect",
             "cases": ["sums"], "failure_mode": "assertion"},
            {"path": "b/test_control_zero.py", "kind": "control",
             "cases": ["zero"], "failure_mode": "assertion"}]}
    monkeypatch.setattr(leerie, "claude_p", writer)
    runs = []
    real_run = leerie._run_acceptance_file

    async def spy(st_, caps, tree, rel, *a, **kw):
        runs.append(sorted(r for _s, r in kw.get("place") or ()))
        return await real_run(st_, caps, tree, rel, *a, **kw)
    monkeypatch.setattr(leerie, "_run_acceptance_file", spy)
    s = asyncio.run(leerie._write_acceptance_set(
        1, st.data["task"], st, _caps(leerie, 1), MODELS, EFFORTS, head))
    assert s["valid"] is True, s
    assert s["defect_files"] == ["b/test_defect_sum.py"]
    assert s["support_files"] == []
    first = ["a/test_defect_weak.py", "b/test_control_zero.py",
             "b/test_defect_sum.py"]
    again = ["b/test_control_zero.py", "b/test_defect_sum.py"]
    assert runs == [first] * 3 + [again] * 2


def test_a_rewrite_is_validated_in_its_own_tree(leerie, tmp_path,
                                                monkeypatch):
    """The set's one rewrite puts the old set back in the writer's checkout
    and is validated in `acceptance-rewrite-<k>-view`, its copies under
    `set-<k>-rw.written`, which is gone afterwards."""
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head)
    old = st.run_dir / "acceptance" / "set-1"
    (old / "acc").mkdir(parents=True)
    (old / "acc" / "test_defect_sum.py").write_text(DEFECT_TEST)
    (old / "acc" / "test_control_zero.py").write_text(CONTROL_TEST)
    set_ = {"index": 1, "valid": True, "dir": str(old),
            "defect_files": ["acc/test_defect_sum.py"],
            "control_files": ["acc/test_control_zero.py"],
            "support_files": [], "cases": {}, "modes": {}}

    async def writer(**kw):
        wt = Path(kw["cwd"])
        # The old set is back where it was written; the writer keeps it.
        assert (wt / "acc" / "test_defect_sum.py").read_text() == DEFECT_TEST
        return {"files": [
            {"path": "acc/test_defect_sum.py", "kind": "defect",
             "cases": ["sums"], "failure_mode": "assertion"},
            {"path": "acc/test_control_zero.py", "kind": "control",
             "cases": ["zero"], "failure_mode": "assertion"}]}
    monkeypatch.setattr(leerie, "claude_p", writer)
    trees = []
    real_run = leerie._run_acceptance_file

    async def spy(st_, caps, tree, rel, *a, **kw):
        trees.append(tree)
        return await real_run(st_, caps, tree, rel, *a, **kw)
    monkeypatch.setattr(leerie, "_run_acceptance_file", spy)
    s = asyncio.run(leerie._write_acceptance_set(
        1, st.data["task"], st, _caps(leerie, 1), MODELS, EFFORTS, head,
        rewrite_of=set_, note="NOTE"))
    assert s["valid"] is True, s
    assert s["dir"] == str(st.run_dir / "acceptance" / "set-1-rw")
    view = leerie._helper_worktree_path(st, "acceptance-rewrite-1-view")
    assert trees == [str(view)] * 2 and not view.exists()
    assert not (st.run_dir / "acceptance" / "set-1-rw.written").exists()


def test_an_unread_defect_file_never_leads_to_the_writer_s_retry(
        leerie, tmp_path, monkeypatch):
    """A defect file that could not be read out was never run: another
    defect file passing on base then says nothing about the defect being
    fixed, so the set is invalid without `no_defect_fails` — no writer
    retry, and no ground for the no-work verdict."""
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head)
    calls = []

    async def writer(**kw):
        calls.append(kw)
        wt = Path(kw["cwd"])
        (wt / "acc").mkdir()
        # A link to a file inside the checkout passes the path checks and is
        # dropped only when read out.
        (wt / "acc" / "sums.txt").write_text(DEFECT_TEST)
        (wt / "acc" / "test_defect_link.py").symlink_to(wt / "acc" / "sums.txt")
        (wt / "acc" / "test_defect_callable.py").write_text(NONDISCRIMINATING)
        (wt / "acc" / "test_control_zero.py").write_text(CONTROL_TEST)
        return {"files": [
            {"path": "acc/test_defect_link.py", "kind": "defect",
             "cases": ["sums"], "failure_mode": "assertion"},
            {"path": "acc/test_defect_callable.py", "kind": "defect",
             "cases": ["callable"], "failure_mode": "assertion"},
            {"path": "acc/test_control_zero.py", "kind": "control",
             "cases": ["zero"], "failure_mode": "assertion"}]}
    monkeypatch.setattr(leerie, "claude_p", writer)
    s = asyncio.run(leerie._write_acceptance_set(
        1, st.data["task"], st, _caps(leerie, 1), MODELS, EFFORTS, head))
    assert s["valid"] is False, s
    assert "own_start_rewrite" not in s
    assert s.get("reason") != "fixed_on_head_by_execution"
    assert len(calls) == 1


def test_a_file_dropped_through_a_linked_directory_is_not_carried(
        leerie, tmp_path, monkeypatch):
    """A declared file reached through a tracked link to a directory cannot
    be read out, so it is dropped — and not carried after all as a support
    file under the real path git lists it by."""
    repo, _head = _repo(tmp_path)
    (repo / "test").mkdir()
    (repo / "test" / "README").write_text("tests\n")
    (repo / "tests").symlink_to("test")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "linked test dir")
    head = _git(repo, "rev-parse", "HEAD")
    st = _st(leerie, tmp_path, repo, head)

    async def writer(**kw):
        wt = Path(kw["cwd"])
        (wt / "test" / "test_defect_sum.py").write_text(DEFECT_TEST)
        (wt / "test" / "test_control_zero.py").write_text(CONTROL_TEST)
        (wt / "test" / "test_control_link.py").write_text(
            CONTROL_TEST.replace("test_add_zero", "test_add_zero_again"))
        return {"files": [
            {"path": "test/test_defect_sum.py", "kind": "defect",
             "cases": ["sums"], "failure_mode": "assertion"},
            {"path": "test/test_control_zero.py", "kind": "control",
             "cases": ["zero"], "failure_mode": "assertion"},
            {"path": "tests/test_control_link.py", "kind": "control",
             "cases": ["zero again"], "failure_mode": "assertion"}]}
    monkeypatch.setattr(leerie, "claude_p", writer)
    s = asyncio.run(leerie._write_acceptance_set(
        1, st.data["task"], st, _caps(leerie, 1), MODELS, EFFORTS, head))
    assert s["valid"] is True, s
    assert s["control_files"] == ["test/test_control_zero.py"]
    assert s["support_files"] == []
    assert sorted(str(p.relative_to(s["dir"]))
                  for p in Path(s["dir"]).rglob("*")) == [
        "test", "test/test_control_zero.py", "test/test_defect_sum.py"]


def test_a_declared_link_s_target_is_still_support(leerie, tmp_path,
                                                   monkeypatch):
    """A declared file that is itself a link is dropped, but the file it
    points at is a file of its own: carried as support when it is one."""
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head)
    control = ("import pathlib\n\n"
               "def test_expected():\n"
               "    here = pathlib.Path(__file__).parent\n"
               "    assert (here / 'expected.txt').read_text() == '0'\n")

    async def writer(**kw):
        wt = Path(kw["cwd"])
        (wt / "acc").mkdir()
        (wt / "acc" / "test_defect_sum.py").write_text(DEFECT_TEST)
        (wt / "acc" / "test_control_expected.py").write_text(control)
        (wt / "acc" / "expected.txt").write_text("0")
        (wt / "acc" / "test_control_link.py").symlink_to(
            wt / "acc" / "expected.txt")
        return {"files": [
            {"path": "acc/test_defect_sum.py", "kind": "defect",
             "cases": ["sums"], "failure_mode": "assertion"},
            {"path": "acc/test_control_expected.py", "kind": "control",
             "cases": ["expected"], "failure_mode": "assertion"},
            {"path": "acc/test_control_link.py", "kind": "control",
             "cases": ["linked"], "failure_mode": "assertion"}]}
    monkeypatch.setattr(leerie, "claude_p", writer)
    s = asyncio.run(leerie._write_acceptance_set(
        1, st.data["task"], st, _caps(leerie, 1), MODELS, EFFORTS, head))
    assert s["valid"] is True, s
    assert s["control_files"] == ["acc/test_control_expected.py"]
    assert s["support_files"] == ["acc/expected.txt"]


def test_a_defect_file_that_failed_only_beside_a_dropped_one_passes(
        leerie, tmp_path, monkeypatch):
    """Pass 1 drops a defect file that passes on base; a second defect file
    failed only beside it, so without it it passes too. No defect file
    fails: the writer's one retry is told of both, and the same set again
    ends as fixed on head."""
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head)
    alone = ("import pathlib\n\n"
             "def test_alone():\n"
             "    here = pathlib.Path(__file__).parent\n"
             "    assert not (here / 'test_defect_callable.py').exists()\n")
    calls = []

    async def writer(user_prompt, *a, **kw):
        calls.append(user_prompt)
        wt = Path(kw["cwd"])
        (wt / "acc").mkdir(exist_ok=True)
        (wt / "acc" / "test_defect_callable.py").write_text(NONDISCRIMINATING)
        (wt / "acc" / "test_defect_alone.py").write_text(alone)
        (wt / "acc" / "test_control_zero.py").write_text(CONTROL_TEST)
        return {"files": [
            {"path": "acc/test_defect_callable.py", "kind": "defect",
             "cases": ["callable"], "failure_mode": "assertion"},
            {"path": "acc/test_defect_alone.py", "kind": "defect",
             "cases": ["alone"], "failure_mode": "assertion"},
            {"path": "acc/test_control_zero.py", "kind": "control",
             "cases": ["zero"], "failure_mode": "assertion"}]}
    monkeypatch.setattr(leerie, "claude_p", writer)
    s = asyncio.run(leerie._write_acceptance_set(
        1, st.data["task"], st, _caps(leerie, 1), MODELS, EFFORTS, head))
    assert len(calls) == 2
    note = calls[1].split("NOTE FROM ORCHESTRATOR:", 1)[1]
    assert "- acc/test_defect_callable.py:" in note
    assert "- acc/test_defect_alone.py:" in note
    assert s["valid"] is False and s["own_start_rewrite"] is True
    assert s["reason"] == "fixed_on_head_by_execution"


def test_a_file_lacked_in_an_earlier_pass_still_withholds_the_retry(
        leerie, tmp_path, monkeypatch):
    """Pass 1 ran without an unreadable ignored file beside a weak defect
    file; pass 2, that file dropped, no longer holds it, and no defect file
    fails. Pass 1's verdicts were measured without a file, so the set is
    invalid with no writer retry and no fixed-on-head verdict."""
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head)
    alone = ("import pathlib\n\n"
             "def test_alone():\n"
             "    root = pathlib.Path(__file__).parent.parent\n"
             "    assert not (root / 'a' / 'test_defect_callable.py').exists()\n")
    calls = []

    async def writer(user_prompt, *a, **kw):
        calls.append(user_prompt)
        wt = Path(kw["cwd"])
        (wt / "a").mkdir()
        (wt / "b").mkdir()
        (wt / "a" / "test_defect_callable.py").write_text(NONDISCRIMINATING)
        (wt / "a" / ".gitignore").write_text("link.txt\n")
        (wt / "a" / "link.txt").symlink_to(tmp_path / "nowhere")
        (wt / "b" / "test_defect_alone.py").write_text(alone)
        (wt / "b" / "test_control_zero.py").write_text(CONTROL_TEST)
        return {"files": [
            {"path": "a/test_defect_callable.py", "kind": "defect",
             "cases": ["callable"], "failure_mode": "assertion"},
            {"path": "b/test_defect_alone.py", "kind": "defect",
             "cases": ["alone"], "failure_mode": "assertion"},
            {"path": "b/test_control_zero.py", "kind": "control",
             "cases": ["zero"], "failure_mode": "assertion"}]}
    monkeypatch.setattr(leerie, "claude_p", writer)
    runs = []
    real_run = leerie._run_acceptance_file

    async def spy(st_, caps, tree, rel, *a, **kw):
        runs.append(rel)
        return await real_run(st_, caps, tree, rel, *a, **kw)
    monkeypatch.setattr(leerie, "_run_acceptance_file", spy)
    s = asyncio.run(leerie._write_acceptance_set(
        1, st.data["task"], st, _caps(leerie, 1), MODELS, EFFORTS, head))
    # Two passes ran: the second without the dropped weak file.
    assert runs.count("b/test_defect_alone.py") == 2, runs
    assert len(calls) == 1
    assert s["valid"] is False and "own_start_rewrite" not in s
    assert s.get("reason") != "fixed_on_head_by_execution"
