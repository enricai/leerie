"""A done task stays done unless its recorded tests regress (DESIGN §8 *Done
is a fixed bar per task, not re-sampled each run*).

For a task that points at a report, "done" used to be re-decided on every run
from freshly written held-out sets. On barnacle's REST-GET lineage 43bd4671
and 396c84e3 each overturned a confirmed no-work on counted failures with none
genuine, and attempts to have a worker tell real gaps from over-reach failed
their benches. So no worker decides: when a report-pointer run ends `done`,
its passing counted cases are the task's settled bar, kept under the state
root; a later run re-runs it at its own start, and while it holds every fresh
failing case is advisory and the run ends `no_work` before planning. A bar is
keyed by the task text and its report's content.

Real git repositories and a real pytest runner throughout (the held-out
sandbox, as a run uses it); only `claude_p` is stubbed, dispatching on the
worker's schema, and the phases around the one under test in the two tests
that drive real `_run_phases`.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from tests.conftest import run_git_cwd_first_stdout as _git
from tests.test_acceptance_gate import (CONTROL_TEST, FIXED, _caps,
                                        _repo)
from tests.test_resume_planning_reentry import (EFFORTS as DRIVE_EFFORTS,
                                                MODELS as DRIVE_MODELS,
                                                _StopAtExecute, _args,
                                                _stub_common)

MODELS = {"acceptance_writer": "sonnet", "scope_judge": "sonnet"}
EFFORTS = {"acceptance_writer": "medium", "scope_judge": "medium"}
TASK = "fix the bug in bug-report.md"
REPORT = "add(2, 3) returns -1; it must return 5. Negative sums are wrong too.\n"
# The done run's set: two report cases.
BAR_DEFECT = ("from calc import add\n\n"
              "def test_add_sums():\n    assert add(2, 3) == 5\n\n"
              "def test_add_negatives():\n    assert add(-1, -1) == -2\n")
# The next run's fresh set on a tree that holds the fix: one case asserts the
# opposite of a bar assertion (a test error), one asks for behaviour the
# report never asks for (over-reach).
OVERREACH = ("from calc import add\n\n"
             "def test_add_not_five():\n    assert add(2, 3) != 5\n\n"
             "def test_add_strings():\n    assert add('2', '3') == 5\n")
# A tree on which one accepted case fails and the other passes.
NEGATIVES_BROKEN = "def add(a, b):\n    return a + b if a >= 0 else 0\n"
# A tree on which every accepted report case passes and the control fails.
CONTROL_BROKEN = ("def add(a, b):\n"
                  "    return a + b if (a, b) != (0, 0) else 1\n")


@pytest.fixture(autouse=True)
def _fast(leerie, monkeypatch):
    leerie._DEPS_INSTALLED.clear()
    monkeypatch.setattr(leerie, "_host_cpu_psi", lambda *a, **k: 0.0)
    yield
    leerie._DEPS_INSTALLED.clear()


def _commit(repo: Path, text: str, msg: str) -> str:
    (repo / "calc.py").write_text(text)
    _git(repo, "commit", "-qam", msg)
    return _git(repo, "rev-parse", "HEAD")


def _lineage_repo(tmp_path):
    repo, buggy = _repo(tmp_path)
    # The report the task points at is not tracked: a report-pointer task.
    (repo / "bug-report.md").write_text(REPORT)
    return repo, buggy


def _mkst(leerie, tmp_path, repo, head, run_id, task=TASK,
          categories=("bug-fixing",)):
    root = tmp_path / "state"
    run_dir = root / "runs" / run_id
    (run_dir / "logs").mkdir(parents=True)
    (run_dir / "subtasks").mkdir(exist_ok=True)
    st = leerie.State(root, run_id, repo_root=repo)
    st.data = {"task": task, "verbosity": "quiet",
               "categories": list(categories),
               "repo_state_before_planning": {"head": head},
               "defect_scope": {"applicable": True, "sites": [],
                                "defect_shape": "add subtracts"},
               "worker_count": 0}
    st.save()
    leerie._ensure_run_repo(st)
    return st


class Workers:
    """`claude_p` by schema: the writer writes `files`, the scope judge
    counts every failing case as a report example. Any other worker is an
    error: nothing else may be asked about a settled bar."""

    def __init__(self, files):
        self.files = files
        self.calls: list[dict] = []

    async def __call__(self, **kw):
        self.calls.append(kw)
        key = kw["schema_key"]
        if key == "acceptance_writer":
            k = int(kw["sid"].split("-")[1])
            decl = []
            for rel, (kind, text) in self.files.get(k, {}).items():
                p = Path(kw["cwd"]) / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(text)
                decl.append({"path": rel, "kind": kind, "cases": [rel],
                             "failure_mode": "assertion"})
            return {"files": decl}
        if key == "scope_judge":
            n = kw["user_prompt"].count("\n[")
            return {"report_ranges": [], "cases": [
                {"case_index": i, "origin": "report_example", "params": [],
                 "span_quote": "it must return 5"} for i in range(n + 1)]}
        raise AssertionError(f"no {key} takes part in the bar's decision")

    def keys(self):
        return {c["schema_key"] for c in self.calls}


def _write(leerie, st, monkeypatch, workers, n=1, task=TASK):
    monkeypatch.setattr(leerie, "claude_p", workers)
    acc = asyncio.run(leerie.phase_acceptance_write(
        task, st, _caps(leerie, n), MODELS, EFFORTS))
    st.data["acceptance"] = acc
    st.save()
    return acc


def _fresh(text=OVERREACH):
    return Workers({1: {"acc/test_fresh.py": ("defect", text),
                        "acc/test_fresh_control.py": ("control",
                                                      CONTROL_TEST)}})


def _done_gate(st, fixed, n=1):
    st.data["acceptance"]["gate"] = {
        "final": [{"index": k + 1, "passed": True, "unmeasured": False,
                   "failing_files": []} for k in range(n)],
        "final_sha": fixed}
    st.save()


def _settle_done_run(leerie, tmp_path, monkeypatch, repo, buggy, fixed,
                     run_id="run-done"):
    """Run 1: sets written at the unfixed own start, the gate passing on the
    fixed tree, the run ending `done` — its bar recorded."""
    st = _mkst(leerie, tmp_path, repo, buggy, run_id)
    _write(leerie, st, monkeypatch, Workers({1: {
        "acc/test_bar.py": ("defect", BAR_DEFECT),
        "acc/test_bar_control.py": ("control", CONTROL_TEST)}}))
    (s1,) = st.data["acceptance"]["sets"]
    assert len(s1["scope"]["counted_cases"]["acc/test_bar.py"]) == 2
    _done_gate(st, fixed)
    got = leerie._record_settled_bar(st)
    assert got == {"entries": 1, "cases": 2}
    return st


def _name(case_id: str) -> str:
    """A runner case ID's test function name."""
    return case_id.rsplit(".", 1)[-1].rsplit("::", 1)[-1].strip()


def _key(leerie, st, task=TASK):
    return leerie._settled_bar_key(task, st.repo_root)


def _bar(leerie, st, task=TASK):
    return leerie._load_settled_bar(st.leerie_root, _key(leerie, st, task))


def _confirmed(st):
    """A held no-work confirmation, as `_confirm_no_work_on_converged_gate`
    leaves it."""
    st.data["no_work_pending"] = True
    st.data["no_work_confirmation"] = {"classifier_evidence": "c",
                                       "judge_evidence": "j"}
    st.save()


def _run_json(st) -> dict:
    return json.loads((st.run_dir / "run.json").read_text())


def _next_run_on(leerie, tmp_path, monkeypatch, tree=FIXED):
    """A done run on the fixed tree, then a later run on `tree`."""
    repo, buggy = _lineage_repo(tmp_path)
    fixed = _commit(repo, FIXED, "fix")
    _settle_done_run(leerie, tmp_path, monkeypatch, repo, buggy, fixed)
    head = fixed if tree == FIXED else _commit(repo, tree, "later")
    return repo, fixed, head, _mkst(leerie, tmp_path, repo, head, "run-next")


# --- recording ---------------------------------------------------------------

def test_a_done_run_records_its_passing_counted_cases_under_the_state_root(
        leerie, tmp_path, monkeypatch):
    repo, buggy = _lineage_repo(tmp_path)
    fixed = _commit(repo, FIXED, "fix")
    st = _settle_done_run(leerie, tmp_path, monkeypatch, repo, buggy, fixed)
    bar = _bar(leerie, st)
    (e,) = bar["entries"]
    assert e["run"] == "run-done" and e["set"] == 1 and e["tree"] == fixed
    assert sorted(_name(c) for c in e["defect"]["acc/test_bar.py"]) \
        == ["test_add_negatives", "test_add_sums"]
    assert e["control"] == ["acc/test_bar_control.py"]
    # Under the state root, not the run's directory; the test files, no fix.
    d = leerie._settled_bar_entry_dir(st.leerie_root, _key(leerie, st), e)
    assert d.is_relative_to(st.leerie_root / "settled-bars")
    assert not d.is_relative_to(st.run_dir)
    assert (d / "acc" / "test_bar.py").read_text() == BAR_DEFECT
    assert sorted(p.name for p in d.rglob("*") if p.is_file()) == [
        "test_bar.py", "test_bar_control.py"]
    # Idempotent per run: recording again replaces the entry.
    leerie._record_settled_bar(st)
    assert len(_bar(leerie, st)["entries"]) == 1


def test_a_case_still_failing_or_a_variant_or_broken_set_is_not_the_bar(
        leerie, tmp_path, monkeypatch):
    repo, buggy = _lineage_repo(tmp_path)
    fixed = _commit(repo, FIXED, "fix")
    st = _mkst(leerie, tmp_path, repo, buggy, "run-done")
    _write(leerie, st, monkeypatch, Workers({
        k: {f"acc{k}/test_bar.py": ("defect", BAR_DEFECT),
            f"acc{k}/test_c.py": ("control", CONTROL_TEST)}
        for k in (1, 2, 3)}), n=3)
    sets = st.data["acceptance"]["sets"]
    ids = sets[0]["scope"]["counted_cases"]["acc1/test_bar.py"]
    neg = [c for c in ids if "negatives" in c]
    sets[1]["scope"]["counted_cases"] = {}           # a variant set
    sets[2]["broken"] = True
    st.data["acceptance"]["gate"] = {"final_sha": fixed, "final": [
        {"index": 1, "passed": False, "unmeasured": False,
         "failing_files": ["acc1/test_bar.py"],
         "failing_cases": {"acc1/test_bar.py": neg}},
        {"index": 2, "passed": True, "failing_files": []},
        {"index": 3, "passed": True, "failing_files": []}]}
    leerie._record_settled_bar(st)
    (e,) = _bar(leerie, st)["entries"]
    assert e["set"] == 1
    assert [_name(c) for c in e["defect"]["acc1/test_bar.py"]] == [
        "test_add_sums"]


def test_only_a_report_pointer_task_has_a_bar(leerie, tmp_path, monkeypatch):
    repo, buggy = _lineage_repo(tmp_path)
    fixed = _commit(repo, FIXED, "fix")
    plain = "fix the add bug"                   # names no report
    assert leerie._settled_bar_key(plain, repo) is None
    st = _mkst(leerie, tmp_path, repo, buggy, "run-plain", task=plain)
    acc = _write(leerie, st, monkeypatch, _fresh(BAR_DEFECT), task=plain)
    assert "settled_bar_key" not in acc
    _done_gate(st, fixed)
    assert leerie._record_settled_bar(st) is None
    assert not (st.leerie_root / "settled-bars").exists()


def test_the_outcome_decider_records_the_bar_only_on_done(
        leerie, tmp_path, monkeypatch):
    repo, buggy = _lineage_repo(tmp_path)
    fixed = _commit(repo, FIXED, "fix")
    st = _settle_done_run(leerie, tmp_path, monkeypatch, repo, buggy, fixed)
    leerie._remove_beneath(st.leerie_root,
                           st.leerie_root / "settled-bars")
    monkeypatch.setattr(leerie, "_unmeasured_on_shipping_tree", lambda s: [])
    assert leerie._decide_outcome(st, any_commits=False) == "no_work"
    assert _bar(leerie, st)["entries"] == []
    assert leerie._decide_outcome(st, any_commits=True) == "done"
    assert len(_bar(leerie, st)["entries"]) == 1
    assert st.data["acceptance"]["settled_bar_recorded"]["cases"] == 2


def test_a_later_done_run_adds_nothing_to_the_bar(leerie, tmp_path,
                                                  monkeypatch):
    """No ratchet: a later done run's fresh cases are not the bar, or its
    unmerged PR would leave them failing beside passing older ones, and that
    reads as a regression. The later run is one a regression reopened, the
    shape that can end done with fresh counted cases that passed."""
    _repo_, _fixed, head, st = _next_run_on(leerie, tmp_path, monkeypatch,
                                            NEGATIVES_BROKEN)
    acc = _write(leerie, st, monkeypatch, _fresh())
    assert acc["settled_bar"]["status"] == "regressed"
    (fresh, _bar_set) = acc["sets"]
    assert fresh["scope"]["counted_cases"]["acc/test_fresh.py"]
    _done_gate(st, head, n=2)
    st.data["acceptance"]["gate"]["final"][1]["index"] = _bar_set["index"]
    before = _bar(leerie, st)
    assert leerie._record_settled_bar(st) is None
    assert _bar(leerie, st) == before
    assert not (leerie._settled_bar_dir(st.leerie_root, _key(leerie, st))
                / "run-next").exists()


def test_a_done_run_that_found_the_bar_unlanded_replaces_it(
        leerie, tmp_path, monkeypatch):
    """The first fix never landed, so nothing of its bar is on this tree; a
    later done run's cases become the bar, and the old files go."""
    repo, buggy = _lineage_repo(tmp_path)
    fixed = _commit(repo, FIXED, "fix")
    _settle_done_run(leerie, tmp_path, monkeypatch, repo, buggy, fixed)
    _git(repo, "reset", "-q", "--hard", buggy)
    st = _mkst(leerie, tmp_path, repo, buggy, "run-second")
    acc = _write(leerie, st, monkeypatch, _fresh(BAR_DEFECT))
    assert acc["settled_bar"]["status"] == "unlanded"
    _done_gate(st, "second-tree")
    assert leerie._record_settled_bar(st) == {"entries": 1, "cases": 2}
    (e,) = _bar(leerie, st)["entries"]
    assert e["run"] == "run-second" and e["tree"] == "second-tree"
    d = leerie._settled_bar_dir(st.leerie_root, _key(leerie, st))
    assert sorted(p.name for p in d.iterdir() if p.is_dir()) == ["run-second"]


# --- the bar holds -----------------------------------------------------------

def test_a_held_bar_withholds_every_fresh_case_with_no_worker_asked(
        leerie, tmp_path, monkeypatch, capsys):
    """396c84e3's shape: the bar holds and the fresh counted failures are a
    test error and an over-reach. Each is withheld, the run ends no_work, the
    confirmed no-work stands, and each case is listed where the user looks."""
    _repo_, _fixed, _head, st = _next_run_on(leerie, tmp_path, monkeypatch)
    w = _fresh()
    acc = _write(leerie, st, monkeypatch, w)
    _confirmed(st)
    assert w.keys() == {"acceptance_writer", "scope_judge"}
    bar = acc["settled_bar"]
    assert bar["status"] == "holds" and bar["passed"] == 2
    assert sorted((a["set"], a["file"], _name(a["case"]))
                  for a in bar["advisory"]) == [
        (1, "acc/test_fresh.py", "test_add_not_five"),
        (1, "acc/test_fresh.py", "test_add_strings")]
    (s1,) = acc["sets"]
    assert s1["scope"]["counted_cases"] == {}
    # The has-work reader, executed on the very failure the case produces.
    assert leerie._acceptance_result_has_work(
        s1, ["acc/test_fresh.py"],
        {"acc/test_fresh.py": [a["case"] for a in bar["advisory"]]}) is False

    capsys.readouterr()
    assert leerie._settled_bar_settles_run(st) is True
    assert st.data["outcome"] == "no_work"
    assert st.data["no_work_required"] is True
    assert st.data["no_work_acceptance"]["verdict"] == "settled_bar_holds"
    assert "no_work_pending" not in st.data
    assert "acceptance_dispute" not in st.data
    # The final log: each case with its set, file and name, and the statement,
    # after the run's closing line.
    out = capsys.readouterr().out
    assert "set 1: acc/test_fresh.py :: " in out
    assert "test_add_not_five" in out and "test_add_strings" in out
    assert "stays done on its recorded tests" in out
    assert "Editing the report or rewording the task starts a new bar" in out
    assert out.index("working branch unchanged") < out.index(
        "stays done on its recorded tests", out.index(
            "working branch unchanged"))
    # The no-work summary and the outcome report carry the same list.
    (reason,) = st.data["no_work_reasons"].values()
    assert "test_add_strings" in reason and "rewording" in reason
    report = _run_json(st)["settled_bar_advisory"]
    assert report["note"] == leerie.SETTLED_BAR_ADVISORY_NOTE
    assert sorted(_name(c["case"]) for c in report["cases"]) == [
        "test_add_not_five", "test_add_strings"]
    assert {c["file"] for c in report["cases"]} == {"acc/test_fresh.py"}


def test_a_withheld_case_never_reaches_the_next_runs_residual(
        leerie, tmp_path, monkeypatch):
    _repo_, fixed, _head, st = _next_run_on(leerie, tmp_path, monkeypatch)
    _write(leerie, st, monkeypatch, _fresh())
    assert leerie._settled_bar_settles_run(st) is True
    (st.run_dir / "orchestrator.exit_code").write_text("0\n")
    after = _mkst(leerie, tmp_path, _repo_, fixed, "run-after")
    got = leerie._prior_delivery_residual(after) or {}
    assert "acceptance_unmet" not in got
    assert "test_add_strings" not in json.dumps(got)
    assert leerie._prior_acceptance_dispute(after) is False


def test_a_pr_would_carry_the_whole_list(leerie):
    many = leerie._PR_ADVISORY_SHOWN + 3
    state = {"acceptance": {"settled_bar": {"status": "holds", "advisory": [
        {"set": 2, "file": "t/a.py", "case": f"t/a.py :: case_{i}"}
        for i in range(many)]}}}
    text = leerie._format_pr_end_state_section(state)
    assert "stays done on its recorded tests" in text
    assert "rewording the task starts a new bar" in text
    assert "set 2: t/a.py :: case_0" in text
    assert f"case_{many - 1}" in text
    assert leerie._format_pr_end_state_section(
        {"acceptance": {"settled_bar": {"status": "holds",
                                        "advisory": []}}}) == ""


def test_a_set_the_judge_could_not_bound_is_withheld_too(leerie):
    """An unbounded set counts whole: unless the withdrawal bounds it, its
    failing files would still be work."""
    f, g = "t/a.py", "t/whole.py"
    set_ = {"index": 3, "defect_files": [f, g],
            "base_failing_cases": {f: [f"{f} :: x"]},
            "scope": {"bounded": False, "error": "the judge typed no case"}}
    assert leerie._acceptance_result_has_work(set_, [f, g]) is True
    got = leerie._settled_bar_withdraw([set_])
    assert got == [{"set": 3, "file": f, "case": f"{f} :: x"},
                   {"set": 3, "file": g, "case": None}]
    assert leerie._acceptance_result_has_work(set_, [f, g]) is False
    assert set_["scope"]["unbounded_error"] == "the judge typed no case"
    lines = leerie._settled_bar_advisory_lines(
        {"acceptance": {"settled_bar": {"advisory": got}}})
    assert lines[1:] == ["  set 3: t/a.py :: x",
                         "  set 3: t/whole.py :: (whole file)"]


def _drive_to_plan_or_end(leerie, monkeypatch, st):
    """Real `_run_phases` down a resume, with the phases around the gate
    stubbed: whether planning is reached, and the phases' call counts."""
    calls: dict = {}
    _stub_common(leerie, monkeypatch, calls)
    caps = dict(leerie.DEFAULT_CAPS)
    caps["judgment_check_rounds"] = 2
    try:
        asyncio.run(leerie._run_phases(
            _args(), caps, st.run_dir, st, "codebase", "normal",
            DRIVE_MODELS, DRIVE_EFFORTS))
    except _StopAtExecute:
        pass
    return calls


@pytest.mark.parametrize("opinion", ["no_opinion", "confirmed", "disputed",
                                     "items_unmet", "interrupted"])
def test_nothing_starts_work_while_a_bar_holds(leerie, tmp_path, monkeypatch,
                                               opinion):
    """The invariant decides, not who else has an opinion: a classifier that
    claimed nothing, a judge that confirmed or disputed, required items typed
    unmet with no held-out failure. Real `_run_phases`: planning is never
    reached, and the outcome is no_work."""
    _repo_, _fixed, _head, st = _next_run_on(leerie, tmp_path, monkeypatch)
    _write(leerie, st, monkeypatch, _fresh())
    st.data.update({"plans_after_classify": [], "artifact_registry": [],
                    "provision": {"recipe": [], "gate": {}}})
    if opinion == "confirmed":
        _confirmed(st)
    elif opinion == "disputed":
        st.data["no_work_dispute"] = {"judge_evidence": "item 1 is unmet",
                                      "checked": []}
    elif opinion == "items_unmet":
        st.data["required_items"] = [{"item": "negative sums", "met": False}]
    elif opinion == "interrupted":
        # Killed after the verdict reached disk, before `finished_at` did.
        st.data["no_work_acceptance"] = {"verdict": "settled_bar_holds",
                                         "advisory": 2}
    st.save()
    calls = _drive_to_plan_or_end(leerie, monkeypatch, st)
    assert "phase_plan" not in calls
    assert st.data["outcome"] == "no_work"
    assert st.data["no_work_acceptance"]["verdict"] == "settled_bar_holds"


def test_a_classifier_that_drops_bug_fixing_does_not_reopen_a_done_task(
        leerie, tmp_path, monkeypatch):
    """The classifier is an LLM, and no LLM decides this: the bar runs with
    no fresh sets, and a run with no bar still skips exactly as before."""
    repo, fixed, _head, st = _next_run_on(leerie, tmp_path, monkeypatch)
    st.data["categories"] = ["feature-implementation"]
    w = _fresh()
    acc = _write(leerie, st, monkeypatch, w)
    assert w.calls == []
    assert acc["settled_bar"]["status"] == "holds"
    assert acc["settled_bar"]["advisory"] == []
    assert acc["skipped"].startswith("not a defect-fix run")
    assert leerie._settled_bar_settles_run(st) is True
    assert st.data["outcome"] == "no_work"

    other = _mkst(leerie, tmp_path, repo, fixed, "run-other",
                  task="fix the other bug in bug-report.md",
                  categories=("feature-implementation",))
    got = _write(leerie, other, monkeypatch, _fresh(),
                 task="fix the other bug in bug-report.md")
    assert got == {"skipped": "not a defect-fix run (no defect-scope audit)"}


def test_a_reworded_task_has_no_bar_and_is_not_swallowed(
        leerie, tmp_path, monkeypatch):
    repo, fixed, _head, st = _next_run_on(leerie, tmp_path, monkeypatch)
    worded = "fix the bug in bug-report.md, and accept strings"
    st.data["task"] = worded
    acc = _write(leerie, st, monkeypatch, _fresh(), task=worded)
    assert "settled_bar" not in acc
    (s1,) = acc["sets"]
    assert len(s1["scope"]["counted_cases"]["acc/test_fresh.py"]) == 2
    assert leerie._settled_bar_settles_run(st) is False
    # Whitespace around the same text is the same task, as every lookup says.
    assert (_key(leerie, st, f"\n{TASK}  \n") == _key(leerie, st))
    assert _key(leerie, st, worded) != _key(leerie, st)


@pytest.mark.parametrize("edited", [False, True])
def test_the_same_task_with_an_edited_report_starts_a_new_bar(
        leerie, tmp_path, monkeypatch, edited):
    """Same task text both ways. An unchanged report: the bar applies, the
    fresh failures are withheld, planning is never reached. A new finding in
    the report: no bar applies, the fresh failures are work, and planning is
    reached, with the old bar left in place under its own key. Real
    `_run_phases`."""
    repo, _fixed, _head, st = _next_run_on(leerie, tmp_path, monkeypatch)
    old_key = _key(leerie, st)
    if edited:
        (repo / "bug-report.md").write_text(
            REPORT + "New finding: add('2', '3') must return 5 as well.\n")
    acc = _write(leerie, st, monkeypatch, _fresh())
    (s1,) = acc["sets"]
    if edited:
        assert acc["settled_bar_key"] != old_key
        assert "settled_bar" not in acc
        counted = s1["scope"]["counted_cases"]["acc/test_fresh.py"]
        assert len(counted) == 2
        assert leerie._acceptance_result_has_work(
            s1, ["acc/test_fresh.py"], {"acc/test_fresh.py": counted}) is True
    else:
        assert acc["settled_bar_key"] == old_key
        assert acc["settled_bar"]["status"] == "holds"
        assert s1["scope"]["counted_cases"] == {}
    st.data.update({"plans_after_classify": [], "artifact_registry": [],
                    "provision": {"recipe": [], "gate": {}}})
    st.save()
    calls = _drive_to_plan_or_end(leerie, monkeypatch, st)
    assert ("phase_plan" in calls) is edited
    assert st.data.get("outcome") == (None if edited else "no_work")
    assert len(leerie._load_settled_bar(st.leerie_root,
                                        old_key)["entries"]) == 1


def test_a_report_edited_during_a_run_is_a_new_bar_for_the_next_run(
        leerie, tmp_path, monkeypatch):
    """A done run fixed the report it read. Keyed on the file as it is at the
    finish, the next run's new finding would be withheld against a bar that
    never saw it."""
    repo, buggy = _lineage_repo(tmp_path)
    fixed = _commit(repo, FIXED, "fix")
    st = _mkst(leerie, tmp_path, repo, buggy, "run-done")
    acc = _write(leerie, st, monkeypatch, _fresh(BAR_DEFECT))
    read_at_start = acc["settled_bar_key"]
    (repo / "bug-report.md").write_text(REPORT + "New finding.\n")
    _done_gate(st, fixed)
    assert leerie._record_settled_bar(st) == {"entries": 1, "cases": 2}
    assert len(leerie._load_settled_bar(st.leerie_root,
                                        read_at_start)["entries"]) == 1
    assert _bar(leerie, st)["entries"] == []


def test_a_report_that_cannot_be_read_means_no_bar_and_is_logged(
        leerie, tmp_path, monkeypatch, capsys):
    """Beside a readable report, so the task is still a report-pointer task:
    leaving the unreadable one out of the key would hide its edits."""
    repo, _fixed, _head, st = _next_run_on(leerie, tmp_path, monkeypatch)
    (repo / "other.md").write_text("another report\n")
    both = f"{TASK} and other.md"
    assert _key(leerie, st, both) is not None
    real = leerie._read_beneath

    def refuse(anchor, path, *, limit):
        if Path(path).name == "other.md":
            raise OSError("refused")
        return real(anchor, path, limit=limit)
    monkeypatch.setattr(leerie, "_read_beneath", refuse)
    capsys.readouterr()
    assert _key(leerie, st, both) is None
    assert "the report other.md cannot be read" in capsys.readouterr().out


def test_an_oversized_report_means_no_bar(leerie, tmp_path, monkeypatch,
                                          capsys):
    repo, _fixed, _head, st = _next_run_on(leerie, tmp_path, monkeypatch)
    (repo / "other.md").write_text("x" * (leerie.D1_REPORT_MAX_BYTES + 1))
    assert _key(leerie, st, f"{TASK} and other.md") is None
    assert "the report other.md cannot be read" in capsys.readouterr().out


def test_an_edited_report_leaves_the_same_task_lookups_alone(
        leerie, tmp_path, monkeypatch):
    """The prior residual and the dispute bound keep their own key, the task
    text: editing the report changes the bar and neither of them."""
    repo, fixed, _head, st = _next_run_on(leerie, tmp_path, monkeypatch)
    prior = st.run_dir.parent / "run-done"
    (prior / "orchestrator.exit_code").write_text("0\n")
    data = json.loads((prior / "state.json").read_text())
    data["acceptance_dispute"] = {"failing_sets": 1, "total_sets": 1,
                                  "cases": ["x"]}
    data["delivery_gate"] = {"unmet_after": [{"item": "negative sums",
                                              "evidence": "still wrong"}]}
    (prior / "state.json").write_text(json.dumps(data))
    before = (leerie._prior_acceptance_dispute(st),
              leerie._prior_delivery_residual(st))
    assert before[0] is True
    assert before[1]["unmet_after"][0]["item"] == "negative sums"
    old_key = _key(leerie, st)
    (repo / "bug-report.md").write_text(REPORT + "New finding.\n")
    assert _key(leerie, st) != old_key
    assert (leerie._prior_acceptance_dispute(st),
            leerie._prior_delivery_residual(st)) == before


# --- the bar regresses -------------------------------------------------------

def test_a_bar_case_that_now_fails_reopens_the_task(leerie, tmp_path,
                                                    monkeypatch):
    """673a0cc6's shape: a later change broke accepted work. One bar case
    passes and one fails at the own start: the failing one joins the run as a
    counted set, it overturns the held no-work whatever the previous run
    disputed, and only it is named to the planners."""
    repo, _fixed, _head, st = _next_run_on(leerie, tmp_path, monkeypatch,
                                           NEGATIVES_BROKEN)
    acc = _write(leerie, st, monkeypatch, _fresh("from calc import add\n\n"
                 "def test_add_floats():\n    assert add(-0.5, 1) == 0.5\n"))
    bar = acc["settled_bar"]
    assert bar["status"] == "regressed" and bar["passed"] == 1
    assert bar["advisory"] == []
    assert bar["injected"] == [2]       # past every index written
    fresh, s = acc["sets"]
    assert s["settled_bar"] == {"run": "run-done", "set": 1}
    assert s["defect_files"] == ["acc/test_bar.py"]
    (cid,) = s["scope"]["counted_cases"]["acc/test_bar.py"]
    assert cid.endswith("test_add_negatives")
    assert (Path(s["dir"]) / "acc" / "test_bar.py").read_text() == BAR_DEFECT
    # The fresh sets stand as typed: the task is no longer done.
    assert fresh["scope"]["counted_cases"]["acc/test_fresh.py"]

    # The dispute bound would accept: the previous same-task run disputed.
    prior = st.run_dir.parent / "run-done"
    (prior / "orchestrator.exit_code").write_text("0\n")
    data = json.loads((prior / "state.json").read_text())
    data["acceptance_dispute"] = {"failing_sets": 1, "total_sets": 1,
                                  "cases": ["x"]}
    (prior / "state.json").write_text(json.dumps(data))
    assert leerie._prior_acceptance_dispute(st) is True

    _confirmed(st)
    st.data.update({"plans_after_classify": [], "artifact_registry": [],
                    "provision": {"recipe": [], "gate": {}}})
    st.save()
    calls = _drive_to_plan_or_end(leerie, monkeypatch, st)
    assert calls.get("phase_plan") == 1
    assert st.data["no_work_acceptance"]["verdict"] == "settled_bar_regressed"
    assert "no_work_pending" not in st.data
    assert cid in st.data["no_work_dispute"]["judge_evidence"]
    assert "test_add_sums" not in st.data["no_work_dispute"]["judge_evidence"]


def test_a_bar_control_that_now_fails_reopens_the_task(leerie, tmp_path,
                                                       monkeypatch):
    """What the REST-GET replay turns on: 107cd8a3's one counted case kept
    passing on the trees carrying #685 while its set's control file did not."""
    _repo_, _fixed, _head, st = _next_run_on(leerie, tmp_path, monkeypatch,
                                             CONTROL_BROKEN)
    acc = _write(leerie, st, monkeypatch, Workers({}))
    bar = acc["settled_bar"]
    assert bar["status"] == "regressed" and bar["passed"] == 2
    (s,) = acc["sets"]
    assert s["defect_files"] == ["acc/test_bar_control.py"]
    (cid,) = s["scope"]["counted_cases"]["acc/test_bar_control.py"]
    assert cid.endswith("test_add_zero")
    assert leerie._settled_bar_settles_run(st) is False
    assert cid in st.data["no_work_dispute"]["judge_evidence"]


def test_a_regression_on_a_run_that_changed_nothing_is_not_no_work(
        leerie, tmp_path, monkeypatch):
    _repo_, _fixed, _head, st = _next_run_on(leerie, tmp_path, monkeypatch,
                                             NEGATIVES_BROKEN)
    _write(leerie, st, monkeypatch, Workers({}))
    assert leerie._decide_outcome(st, any_commits=False) == "unconverged"
    assert any("passed when this task last ended done" in r
               for r in st.data["outcome_reasons"])


def test_a_regressed_bar_set_is_always_shown(leerie):
    """It takes the highest index, which would otherwise hold it back from
    the planners and the repair rounds whose work it is."""
    sets = [{"index": k} for k in range(1, 5)]
    sets.append({"index": 6, "settled_bar": {"run": "r", "set": 1}})
    assert leerie._acceptance_shown_indices(sets) == {1, 2, 6}
    assert leerie._acceptance_shown_indices(
        [{"index": 1}, {"index": 2}, {"index": 3},
         {"index": 6, "settled_bar": {"run": "r", "set": 1}}]) == {1, 2, 3, 6}


def test_no_worker_audits_or_rewrites_a_regressed_bar_set(leerie, tmp_path,
                                                          monkeypatch):
    audited: list[int] = []

    async def audit(st, caps, models, efforts, rev, s_, label):
        audited.append(s_["index"])
        return {"failing_cases": [], "findings": [], "kinds": [],
                "sample_kinds": [], "samples": 0}

    async def surrogate(*a, **k):
        return {"abstained": "test"}
    monkeypatch.setattr(leerie, "_acceptance_audit_on_rev", audit)
    monkeypatch.setattr(leerie, "_acceptance_surrogate", surrogate)
    monkeypatch.setattr(leerie, "_acceptance_mechanical_findings",
                        lambda *a, **k: [])
    st = leerie.State(tmp_path / "s", "r", repo_root=tmp_path)
    (st.run_dir / "logs").mkdir(parents=True, exist_ok=True)
    st.data = {"task": "t", "acceptance": {"validity_base": "own", "sets": [
        {"index": 1}, {"index": 2, "settled_bar": {"run": "r", "set": 1}}]}}
    final = [{"index": i, "passed": False, "failing_files": ["f"]}
             for i in (1, 2)]
    asyncio.run(leerie._acceptance_audit_and_rewrite(
        st, dict(leerie.DEFAULT_CAPS), {}, {}, "staging", final, "sha"))
    assert audited == [1]


# --- the bar says nothing ----------------------------------------------------

def test_a_bar_whose_fix_is_not_on_the_tree_is_ignored(leerie, tmp_path,
                                                       monkeypatch):
    """Every bar report case fails again, as before the done run (its PR was
    never merged): the bar says nothing here and the fresh sets stand."""
    repo, buggy = _lineage_repo(tmp_path)
    fixed = _commit(repo, FIXED, "fix")
    _settle_done_run(leerie, tmp_path, monkeypatch, repo, buggy, fixed)
    _git(repo, "reset", "-q", "--hard", buggy)
    st = _mkst(leerie, tmp_path, repo, buggy, "run-next")
    acc = _write(leerie, st, monkeypatch, _fresh(BAR_DEFECT))
    assert acc["settled_bar"]["status"] == "unlanded"
    (s1,) = acc["sets"]
    assert len(s1["scope"]["counted_cases"]["acc/test_fresh.py"]) == 2
    assert leerie._settled_bar_settles_run(st) is False
    assert "no_work_acceptance" not in st.data
    assert "no_work_dispute" not in st.data


def test_a_bar_case_that_no_longer_runs_is_dropped_for_good(
        leerie, tmp_path, monkeypatch, capsys):
    """Q6: a bar file whose module is gone is never held over the tree; it
    leaves the bar, and the bar store says so for every later run."""
    repo, buggy = _lineage_repo(tmp_path)
    fixed = _commit(repo, FIXED, "fix")
    st1 = _settle_done_run(leerie, tmp_path, monkeypatch, repo, buggy, fixed)
    _git(repo, "mv", "calc.py", "adder.py")
    _git(repo, "commit", "-qm", "rename")
    moved = _git(repo, "rev-parse", "HEAD")
    st = _mkst(leerie, tmp_path, repo, moved, "run-next")
    acc = _write(leerie, st, monkeypatch, Workers({}))
    bar = acc["settled_bar"]
    assert bar["status"] == "empty"
    assert any(d.endswith("acc/test_bar.py") for d in bar["dropped"])
    assert "dropped acc/test_bar.py" in capsys.readouterr().out
    assert _bar(leerie, st1)["entries"] == []
    assert leerie._settled_bar_settles_run(st) is False


@pytest.mark.parametrize("unrunnable", ["acc/test_bar.py",
                                        "acc/test_bar_control.py"])
def test_an_environment_failure_drops_nothing_and_applies_nothing(
        leerie, tmp_path, monkeypatch, unrunnable):
    """A file the environment could not run says nothing of its cases: a
    transient failure must not erase a bar, and it cannot be said to hold,
    whether the file is the report cases' or the control beside passing ones."""
    _repo_, _fixed, _head, st = _next_run_on(leerie, tmp_path, monkeypatch)
    real = leerie._run_acceptance_file

    async def no_verdict(st_, caps, tree, rel, log_path, label, **kw):
        if label.startswith("settled-bar") and rel == unrunnable:
            return None
        return await real(st_, caps, tree, rel, log_path, label, **kw)
    monkeypatch.setattr(leerie, "_run_acceptance_file", no_verdict)
    before = _bar(leerie, st)
    acc = _write(leerie, st, monkeypatch, _fresh())
    assert acc["settled_bar"]["status"] == "unmeasured"
    assert acc["settled_bar"]["dropped"] == []
    assert _bar(leerie, st) == before
    (s1,) = acc["sets"]
    assert len(s1["scope"]["counted_cases"]["acc/test_fresh.py"]) == 2
    assert leerie._settled_bar_settles_run(st) is False


def test_one_flake_does_not_reopen_a_done_task(leerie, tmp_path, monkeypatch):
    """A failing bar file runs once more, and a case stays failed only when
    it failed both times."""
    _repo_, _fixed, _head, st = _next_run_on(leerie, tmp_path, monkeypatch)
    real = leerie._run_acceptance_file
    flaked: list[str] = []

    async def flaky(st_, caps, tree, rel, log_path, label, **kw):
        got = await real(st_, caps, tree, rel, log_path, label, **kw)
        if label.startswith("settled-bar") and rel == "acc/test_bar.py" \
                and not flaked:
            obs = kw["observed"]
            cid = next(c for c in obs["case_status"] if "sums" in c)
            flaked.append(cid)
            obs["case_status"][cid] = "failed"
            obs["failing_cases"] = [cid]
            return False
        return got
    monkeypatch.setattr(leerie, "_run_acceptance_file", flaky)
    acc = _write(leerie, st, monkeypatch, _fresh())
    assert flaked
    assert acc["settled_bar"]["status"] == "holds"
    assert acc["settled_bar"]["passed"] == 2


def test_a_case_failing_both_times_is_failed(leerie, tmp_path, monkeypatch):
    """The retry's converse: it must not launder a real failure."""
    _repo_, _fixed, _head, st = _next_run_on(leerie, tmp_path, monkeypatch,
                                             NEGATIVES_BROKEN)
    runs: list[str] = []
    real = leerie._run_acceptance_file

    async def counting(st_, caps, tree, rel, log_path, label, **kw):
        if label.startswith("settled-bar"):
            runs.append(rel)
        return await real(st_, caps, tree, rel, log_path, label, **kw)
    monkeypatch.setattr(leerie, "_run_acceptance_file", counting)
    acc = _write(leerie, st, monkeypatch, Workers({}))
    assert runs.count("acc/test_bar.py") == 2
    assert runs.count("acc/test_bar_control.py") == 1
    assert acc["settled_bar"]["status"] == "regressed"


def test_old_state_with_no_bar_is_unchanged(leerie, tmp_path, monkeypatch):
    """No bar recorded (every run before this change), even with an earlier
    `done` run of the task on disk: nothing is backfilled, the sets stand as
    the scope judge typed them, nothing is recorded, and the settle is
    reached as before."""
    repo, buggy = _lineage_repo(tmp_path)
    fixed = _commit(repo, FIXED, "fix")
    old = _mkst(leerie, tmp_path, repo, buggy, "run-old")
    old.data.update({"outcome": "done", "acceptance": {"sets": []}})
    old.save()
    (old.run_dir / "orchestrator.exit_code").write_text("0\n")
    st = _mkst(leerie, tmp_path, repo, fixed, "run-next")
    w = _fresh()
    acc = _write(leerie, st, monkeypatch, w)
    assert "settled_bar" not in acc
    assert w.keys() == {"acceptance_writer", "scope_judge"}
    (s1,) = acc["sets"]
    assert len(s1["scope"]["counted_cases"]["acc/test_fresh.py"]) == 2
    assert leerie._settled_bar_settles_run(st) is False
    assert "no_work_acceptance" not in st.data
    assert not (st.leerie_root / "settled-bars").exists()


def test_a_state_from_before_this_change_is_read_as_no_bar(leerie, tmp_path):
    st = leerie.State(tmp_path / "s", "r", repo_root=tmp_path)
    for acc in (None, {}, {"skipped": "x"}, {"sets": []}):
        st.data = {"task": "t", **({"acceptance": acc} if acc is not None
                                   else {})}
        assert leerie._settled_bar_settles_run(st) is False
        assert leerie._settled_bar_advisory(st.data) == []
        assert leerie._settled_bar_advisory_lines(st.data) == []


# --- the dispute evidence names only counted failing cases --------------------

def test_dispute_evidence_names_only_the_counted_failing_cases(
        leerie, tmp_path, monkeypatch):
    """396c84e3's evidence named every case of a failing file — passing and
    uncounted ones too — so its planners were told two passing cases fail
    on HEAD. Only the counted failing cases are work, and only they are
    named; a variant set's cases are not named at all."""
    monkeypatch.setattr(leerie, "_finish_no_work_run", lambda *a, **k: None)
    st = leerie.State(tmp_path / "s", "r", repo_root=tmp_path)
    f = "t/test_a.py"
    sets = [{"index": 1, "cases": {f: ["declared a", "declared b",
                                       "declared c"]},
             "scope": {"bounded": True,
                       "counted_cases": {f: [f"{f}::counted"]}}},
            {"index": 2, "cases": {"t/test_v.py": ["variant"]},
             "scope": {"bounded": True, "counted_cases": {}}},
            {"index": 3, "cases": {"t/test_u.py": ["whole file"]}}]

    async def fake(st_, caps):
        return [{"index": 1, "passed": False, "failing_files": [f],
                 "failing_cases": {f: [f"{f}::counted", f"{f}::uncounted"]}},
                {"index": 2, "passed": False,
                 "failing_files": ["t/test_v.py"],
                 "failing_cases": {"t/test_v.py": ["t/test_v.py::v"]}},
                {"index": 3, "passed": False,
                 "failing_files": ["t/test_u.py"]}]
    monkeypatch.setattr(leerie, "_acceptance_results_on_head", fake)
    st.data = {"task": "t", "acceptance": {"sets": sets},
               "no_work_pending": True,
               "no_work_confirmation": {"judge_evidence": "j"}}
    assert asyncio.run(leerie._settle_pending_no_work(
        st, dict(leerie.DEFAULT_CAPS))) is False
    assert st.data["acceptance_dispute"]["cases"] == [
        f"{f}::counted", "whole file"]
