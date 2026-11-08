"""A done run's change on a later run's base settles the task (DESIGN §8 *A
done run's change on the base settles the task*).

Barnacle 73751002 ended `done`; its PR was squash-merged; the next run of the
same task started on a base holding it, was overturned by fresh held-out sets
demanding the opposite, and opened a PR reversing it. So a `done` run's change
is part of the task's settled bar: at a later own start, git alone says whether
it is on the base (every non-test hunk reverse-applies, each on its own), and
its test files run in the form it shipped them. On the base and passing, the
run ends `no_work` before planning with the fresh cases advisory; on the base
and failing, the task is reopened on those tests; not on the base, nothing
changes.

Real git repositories (a squash-merged change, later commits on the same file)
and a real pytest runner in the held-out sandbox; only `claude_p` is stubbed,
and the phases around the one under test where real `_run_phases` is driven.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest

from tests.conftest import run_git_cwd_first_stdout as _git
from tests.test_acceptance_gate import CONTROL_TEST, _caps
from tests.test_settled_bar import (OVERREACH, Workers, _drive_to_plan_or_end,
                                    _name)
from tests.test_settled_bar import _mkst as _mkst_bar
from tests.test_settled_bar import _write as _write_bar

TASK = "make add sum its arguments instead of subtracting them"
# Real source has lines around a change: a hunk with no trailing context is
# pinned to the end of its file by `git apply`.
_HEAD = "def sub(a, b):\n    return a - b\n\n\n"
_TAIL = "\n\ndef neg(a):\n    return -a\n"
BUGGY = _HEAD + "def add(a, b):\n    return a - b\n" + _TAIL
FIXED = _HEAD + "def add(a, b):\n    return a + b\n" + _TAIL
# The done run's own test: fails on BUGGY, passes on FIXED.
CHANGE_TEST = ("from calc import add\n\n"
               "def test_add_sums():\n    assert add(2, 3) == 5\n\n"
               "def test_add_negatives():\n    assert add(-1, -1) == -2\n")
# A later commit elsewhere in the same file: a squash plus later work.
LATER = FIXED + "\n\ndef mul(a, b):\n    return a * b\n"


def _mkst(leerie, tmp_path, repo, head, run_id, task=TASK, **kw):
    return _mkst_bar(leerie, tmp_path, repo, head, run_id, task=task, **kw)


def _write(leerie, st, monkeypatch, workers, n=1, task=TASK):
    return _write_bar(leerie, st, monkeypatch, workers, n=n, task=task)


@pytest.fixture(autouse=True)
def _fast(leerie, monkeypatch):
    leerie._DEPS_INSTALLED.clear()
    monkeypatch.setattr(leerie, "_host_cpu_psi", lambda *a, **k: 0.0)
    yield
    leerie._DEPS_INSTALLED.clear()


def _base_repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@x")
    _git(repo, "config", "user.name", "t")
    # A body long enough that a later commit at its end leaves the change's
    # hunk context intact.
    (repo / "calc.py").write_text(BUGGY)
    (repo / "conftest.py").write_text(
        "import sys, pathlib\n"
        "sys.path.insert(0, str(pathlib.Path(__file__).parent))\n")
    (repo / ".leerie").mkdir()
    (repo / ".leerie" / "config.toml").write_text(
        'test_scoped = "python3 -m pytest -q -p no:cacheprovider '
        '{test_files}"\n'
        'test = "python3 -m pytest -q -p no:cacheprovider"\n')
    (repo / ".gitignore").write_text("__pycache__/\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    return repo, _git(repo, "rev-parse", "HEAD")


def _write_files(repo: Path, files: dict[str, str | None]) -> None:
    for rel, text in files.items():
        p = repo / rel
        if text is None:
            p.unlink()
            continue
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)


def _commit(repo: Path, files: dict[str, str | None], msg: str) -> str:
    _write_files(repo, files)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", msg)
    return _git(repo, "rev-parse", "HEAD")


def _done_run(leerie, tmp_path, monkeypatch, repo, base, run_id="run-done",
              change=None, task=TASK, final_failing=()):
    """Run 1: started at `base`, its fix committed on its own branch in its
    own repository (never on main), ending `done`: its change recorded."""
    change = change or {"calc.py": FIXED, "tests/test_calc_sums.py":
                        CHANGE_TEST}
    _git(repo, "checkout", "-q", "-b", f"work-{run_id}", base)
    tip = _commit(repo, change, "the run's fix")
    _git(repo, "checkout", "-q", "main")
    st = _mkst(leerie, tmp_path, repo, base, run_id, task=task)
    assert leerie._run_repo_commit(st, tip, "refs/leerie/t")
    _git(repo, "--git-dir", str(Path(st.run_dir) / "repo"), "update-ref",
         f"refs/heads/{leerie._compute_run_branch(run_id)}", tip)
    st.data["acceptance"] = {
        "skipped": "not a defect-fix run",
        "settled_bar_key": leerie._settled_bar_key(task, repo)}
    st.data["final_failing_ids"] = list(final_failing)
    st.data["outcome"] = "done"
    st.save()
    got = leerie._record_settled_bar(st)
    assert got and got["change"]["files"] == len(change), got
    return st, tip


def _squash(repo: Path, files: dict[str, str | None]) -> str:
    """The user's squash merge onto main: the same content, no ancestry."""
    return _commit(repo, files, "squash: the run's fix (#1)")


def _key(leerie, repo, task=TASK):
    return leerie._settled_bar_key(task, repo)


def _check(leerie, st, sets, base):
    return asyncio.run(leerie._settled_bar_check(
        _key(leerie, st.repo_root, st.data["task"]), st, _caps(leerie, 1),
        sets, base, written=1))


def _sets_failing():
    """One fresh set whose counted cases fail on the fixed tree."""
    return Workers({1: {"acc/test_fresh.py": ("defect", OVERREACH),
                        "acc/test_fresh_control.py": ("control",
                                                      CONTROL_TEST)}})


# --- the git answer ------------------------------------------------------------

def test_a_squash_merged_change_with_later_work_in_the_same_file_is_on_base(
        leerie, tmp_path, monkeypatch):
    repo, base = _base_repo(tmp_path)
    st1, tip = _done_run(leerie, tmp_path, monkeypatch, repo, base)
    _squash(repo, {"calc.py": FIXED, "tests/test_calc_sums.py": CHANGE_TEST})
    head = _commit(repo, {"calc.py": LATER}, "later work in calc.py")
    # The run's own commit is not on main: ancestry cannot answer.
    assert _git(repo, "branch", "--contains", tip) == "work-run-done"
    st2 = _mkst(leerie, tmp_path, repo, head, "run-next")
    bar = leerie._load_settled_bar(st2.leerie_root, _key(leerie, repo))
    assert leerie._settled_change_landed(st2, st2.leerie_root,
                                         _key(leerie, repo), bar["change"],
                                         head)
    # Live falsification of the signal on the same record: the base before
    # the merge does not hold it.
    assert not leerie._settled_change_landed(st2, st2.leerie_root,
                                             _key(leerie, repo),
                                             bar["change"], base)


def test_a_later_rewrite_of_the_changes_own_line_is_not_on_base(
        leerie, tmp_path, monkeypatch):
    """Degrades safely: unsure is "not on the base", today's behaviour."""
    repo, base = _base_repo(tmp_path)
    _done_run(leerie, tmp_path, monkeypatch, repo, base)
    _squash(repo, {"calc.py": FIXED, "tests/test_calc_sums.py": CHANGE_TEST})
    head = _commit(repo, {"calc.py": FIXED.replace("return a + b",
                                                   "return sum((a, b))")},
                   "rewrite")
    st2 = _mkst(leerie, tmp_path, repo, head, "run-next")
    bar = leerie._load_settled_bar(st2.leerie_root, _key(leerie, repo))
    assert not leerie._settled_change_landed(
        st2, st2.leerie_root, _key(leerie, repo), bar["change"], head)
    rec = _check(leerie, st2, [], head)
    assert rec["status"] == "unlanded"
    assert rec["change"] == {"run": "run-done", "landed": False,
                             "backfilled": False}


def test_a_change_never_merged_is_unlanded_and_settles_nothing(
        leerie, tmp_path, monkeypatch):
    repo, base = _base_repo(tmp_path)
    _done_run(leerie, tmp_path, monkeypatch, repo, base)
    head = _commit(repo, {"other.py": "X = 1\n"}, "unrelated")
    st2 = _mkst(leerie, tmp_path, repo, head, "run-next")
    rec = _check(leerie, st2, [], head)
    assert rec["status"] == "unlanded"
    st2.data["acceptance"] = {"settled_bar": rec}
    assert leerie._settled_bar_settles_run(st2) is False


# --- the record --------------------------------------------------------------

def test_a_task_text_task_has_a_key_and_records_its_change(
        leerie, tmp_path, monkeypatch):
    repo, base = _base_repo(tmp_path)
    assert _key(leerie, repo) is not None
    assert _key(leerie, repo, "  ") is None
    st1, tip = _done_run(leerie, tmp_path, monkeypatch, repo, base)
    bar = leerie._load_settled_bar(st1.leerie_root, _key(leerie, repo))
    ch = bar["change"]
    assert (ch["run"], ch["own_start"], ch["tree"]) == ("run-done", base, tip)
    assert {p["path"]: p["test"] for p in ch["patches"]} == {
        "calc.py": False, "tests/test_calc_sums.py": True}
    assert ch["tests"] == ["tests/test_calc_sums.py"]
    kept = (leerie._settled_change_dir(st1.leerie_root, _key(leerie, repo),
                                       "run-done")
            / "files" / "tests" / "test_calc_sums.py")
    assert kept.read_text() == CHANGE_TEST
    # Under the state root, never in the run's own directory.
    assert st1.run_dir not in kept.parents


@pytest.mark.parametrize("cid", [
    "tests/test_calc_sums.py :: test_add_negatives",          # vitest, jest
    "tests.test_calc_sums :: tests.test_calc_sums.test_add_negatives"])
def test_known_failing_ids_are_kept_per_test_file(leerie, tmp_path,
                                                  monkeypatch, cid):
    repo, base = _base_repo(tmp_path)
    st1, _tip = _done_run(leerie, tmp_path, monkeypatch, repo, base,
                          final_failing=[cid, "elsewhere.py :: test_x",
                                         "calc :: calc.test_x"])
    bar = leerie._load_settled_bar(st1.leerie_root, _key(leerie, repo))
    assert bar["change"]["known_failing"] == {
        "tests/test_calc_sums.py": [cid]}


# --- at the own start --------------------------------------------------------

def _merged_next_run(leerie, tmp_path, monkeypatch, later=None, change=None,
                     final_failing=()):
    """A done run, its change squash-merged, `later` committed on top, and
    the next run of the same task started there."""
    repo, base = _base_repo(tmp_path)
    change = change or {"calc.py": FIXED,
                        "tests/test_calc_sums.py": CHANGE_TEST}
    _done_run(leerie, tmp_path, monkeypatch, repo, base, change=change,
              final_failing=final_failing)
    head = _squash(repo, change)
    if later:
        head = _commit(repo, later, "later")
    return repo, head, _mkst(leerie, tmp_path, repo, head, "run-next")


def _prep_drive(st):
    st.data.update({"plans_after_classify": [], "artifact_registry": [],
                    "provision": {"recipe": [], "gate": {}}})
    st.save()


def test_a_merged_done_change_ends_the_next_run_no_work_before_planning(
        leerie, tmp_path, monkeypatch):
    """The 73751002 → a79384a4 shape: fresh sets that fail on the merged
    tree are advisory, and real `_run_phases` never reaches planning."""
    _repo, head, st = _merged_next_run(leerie, tmp_path, monkeypatch,
                                       later={"calc.py": LATER})
    workers = _sets_failing()
    acc = _write(leerie, st, monkeypatch, workers)
    bar = acc["settled_bar"]
    assert bar["status"] == "holds", bar
    assert bar["change"] == {"run": "run-done", "landed": True,
                             "backfilled": False}
    assert bar["passed"] == 2 and bar["failed"] == 0
    withheld = {(a["file"], _name(a["case"])) for a in bar["advisory"]}
    assert withheld == {("acc/test_fresh.py", "test_add_not_five"),
                        ("acc/test_fresh.py", "test_add_strings")}
    assert workers.keys() <= {"acceptance_writer", "scope_judge"}
    _prep_drive(st)
    calls = _drive_to_plan_or_end(leerie, monkeypatch, st)
    assert "phase_plan" not in calls
    assert st.data["outcome"] == "no_work"
    assert st.data["no_work_acceptance"] == {"verdict": "settled_bar_holds",
                                             "advisory": 2}
    reason = st.data["no_work_reasons"]["<settled bar holds>"]
    assert reason.startswith("the change run run-done shipped when this "
                             "task ended done is on this tree")
    rj = json.loads((st.run_dir / "run.json").read_text())
    assert len(rj["settled_bar_advisory"]["cases"]) == 2


def test_a_change_whose_tests_were_deleted_on_base_still_runs_them(
        leerie, tmp_path, monkeypatch):
    """Its tests run in the form it shipped them, whatever the base did to
    the files: deleting them does not stop the check."""
    _repo, head, st = _merged_next_run(
        leerie, tmp_path, monkeypatch,
        later={"tests/test_calc_sums.py": None})
    rec = _check(leerie, st, [], head)
    assert rec["status"] == "holds" and rec["passed"] == 2


def test_a_change_on_base_whose_tests_fail_reopens_the_task(
        leerie, tmp_path, monkeypatch):
    """Its line is intact, so git says it is on the base; its tests fail, so
    it regressed: every case failing is a regression, not unlanded, and the
    failing tests join the run as a counted set in the change's form."""
    broken = FIXED + ("\n\n_orig = add\n\n\ndef add(a, b):\n"
                      "    return _orig(a, b) + 1\n")
    _repo, head, st = _merged_next_run(leerie, tmp_path, monkeypatch,
                                       later={"calc.py": broken})
    acc = _write(leerie, st, monkeypatch, _sets_failing())
    bar = acc["settled_bar"]
    assert bar["change"]["landed"] is True
    assert bar["status"] == "regressed", bar
    assert bar["failed"] == 2 and bar["passed"] == 0
    assert sorted(_name(c) for c in bar["change_failed"]) == [
        "test_add_negatives", "test_add_sums"]
    # The repository's own tests are named, never carried as a held-out set.
    assert bar["injected"] == []
    assert not [s_ for s_ in acc["sets"] if s_.get("settled_bar")]
    _prep_drive(st)
    calls = _drive_to_plan_or_end(leerie, monkeypatch, st)
    assert "phase_plan" in calls
    assert st.data["no_work_acceptance"]["verdict"] == "settled_bar_regressed"
    assert "test_add_sums" in st.data["no_work_dispute"]["judge_evidence"]


def test_a_case_that_already_failed_when_the_task_ended_done_is_no_regression(
        leerie, tmp_path, monkeypatch):
    negatives_broken = FIXED + ("\n\n_orig = add\n\n\ndef add(a, b):\n"
                                "    return _orig(a, b) if a >= 0 else 0\n")
    _repo, head, st = _merged_next_run(
        leerie, tmp_path, monkeypatch,
        later={"calc.py": negatives_broken})
    rec = _check(leerie, st, [], head)
    # Live falsification first: with nothing known failing, it regressed.
    assert rec["status"] == "regressed" and rec["failed"] == 1
    root, key = st.leerie_root, _key(leerie, st.repo_root)
    bar = leerie._load_settled_bar(root, key)
    st2 = _mkst(leerie, tmp_path, st.repo_root, head, "run-third")
    failing = rec["change_failed"]
    assert [_name(c) for c in failing] == ["test_add_negatives"], rec
    bar["change"]["known_failing"] = {"tests/test_calc_sums.py": failing[:1]}
    leerie._save_settled_bar(root, key, bar)
    rec2 = _check(leerie, st2, [], head)
    assert rec2["status"] == "holds" and rec2["passed"] == 1, rec2


def test_a_helper_that_holds_no_test_is_dropped_from_the_run_and_still_placed(
        leerie, tmp_path, monkeypatch):
    change = {"calc.py": FIXED,
              "tests/numbers.py": "PAIR = (2, 3)\n",
              "tests/test_calc_sums.py": (
                  "from calc import add\nfrom tests.numbers import PAIR\n\n"
                  "def test_add_sums():\n    assert add(*PAIR) == 5\n")}
    _repo, head, st = _merged_next_run(
        leerie, tmp_path, monkeypatch, change=change,
        later={"tests/numbers.py": None, "tests/__init__.py": ""})
    rec = _check(leerie, st, [], head)
    assert rec["status"] == "holds", rec
    assert rec["dropped"] == ["run-done/change/tests/numbers.py"]
    bar = leerie._load_settled_bar(st.leerie_root, _key(leerie, st.repo_root))
    assert bar["change"]["run_tests"] == ["tests/test_calc_sums.py"]
    assert "tests/numbers.py" in bar["change"]["tests"]


# --- backfill ------------------------------------------------------------------

def _old_done_run(leerie, tmp_path, repo, base, task=TASK, run_id="run-old",
                  started_at="2026-01-01T00:00:00+00:00"):
    """A run that ended `done` before changes were recorded: its run
    directory and repository, and no bar."""
    _git(repo, "checkout", "-q", "-b", f"work-{run_id}", base)
    tip = _commit(repo, {"calc.py": FIXED,
                         "tests/test_calc_sums.py": CHANGE_TEST}, "fix")
    _git(repo, "checkout", "-q", "main")
    st = _mkst(leerie, tmp_path, repo, base, run_id, task=task)
    assert leerie._run_repo_commit(st, tip, "refs/leerie/t")
    _git(repo, "--git-dir", str(Path(st.run_dir) / "repo"), "update-ref",
         f"refs/heads/{leerie._compute_run_branch(run_id)}", tip)
    st.data.update({"outcome": "done", "started_at": started_at,
                    "base_sha": base, "acceptance": {"skipped": "x"}})
    st.save()
    (st.run_dir / "run.json").write_text(json.dumps(
        {"outcome": "done", "task": task, "finished_at": started_at}))
    return st, tip


def test_a_done_run_from_before_this_is_backfilled_from_its_run_dir(
        leerie, tmp_path, monkeypatch):
    repo, base = _base_repo(tmp_path)
    _old, tip = _old_done_run(leerie, tmp_path, repo, base)
    head = _squash(repo, {"calc.py": FIXED,
                          "tests/test_calc_sums.py": CHANGE_TEST})
    st = _mkst(leerie, tmp_path, repo, head, "run-next")
    key = _key(leerie, repo)
    assert leerie._load_settled_bar(st.leerie_root, key) == {"entries": []}
    rec = _check(leerie, st, [], head)
    assert rec["status"] == "holds", rec
    assert rec["change"] == {"run": "run-old", "landed": True,
                             "backfilled": True}
    bar = leerie._load_settled_bar(st.leerie_root, key)
    assert (bar["change"]["run"], bar["change"]["tree"]) == ("run-old", tip)


def test_a_run_dir_whose_repository_is_gone_cannot_be_backfilled(
        leerie, tmp_path, monkeypatch):
    repo, base = _base_repo(tmp_path)
    old, _tip = _old_done_run(leerie, tmp_path, repo, base)
    leerie._rmtree_owned(Path(old.run_dir) / "repo")
    head = _squash(repo, {"calc.py": FIXED,
                          "tests/test_calc_sums.py": CHANGE_TEST})
    st = _mkst(leerie, tmp_path, repo, head, "run-next")
    assert _check(leerie, st, [], head) is None


def test_a_report_edited_since_the_old_run_started_is_not_backfilled(
        leerie, tmp_path, monkeypatch):
    """A run from before keys has no report hash: its report must be
    unmodified since it started, or the old run asked another question."""
    repo, base = _base_repo(tmp_path)
    task = "fix the bug in bug-report.md"
    # The report is never tracked: a report-pointer task.
    with open(repo / ".git" / "info" / "exclude", "a") as f:
        f.write("bug-report.md\n")
    (repo / "bug-report.md").write_text("add subtracts\n")
    _old_done_run(leerie, tmp_path, repo, base, task=task)
    head = _squash(repo, {"calc.py": FIXED,
                          "tests/test_calc_sums.py": CHANGE_TEST})
    st = _mkst(leerie, tmp_path, repo, head, "run-next", task=task)
    # The report's mtime (now) is after the old run's start.
    assert _check(leerie, st, [], head) is None
    old = 1_000_000_000            # 2001: before the old run started
    os.utime(repo / "bug-report.md", (old, old))
    rec = _check(leerie, st, [], head)
    assert rec["status"] == "holds" and rec["change"]["backfilled"] is True
