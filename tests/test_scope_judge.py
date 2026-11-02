"""Report-bounded has-work (DESIGN §8 *A task that points at a report is
bounded by the report's own scenarios*): the `scope_judge` types facts, code
decides which own-start-failing cases count, and only a failing file holding
a counted case is unfinished work — a variant set is repaired but never
creates a run. The decision cases below are the bench P2 v3b shapes (a
5-15 s stated range, variants at 17-20 s, a ms/s mix)."""
from __future__ import annotations

import asyncio
import subprocess
import unicodedata
from pathlib import Path
from unittest import mock

import pytest

REPORT = ("# Report\n\nThe page re-renders the main body 8s later with no "
          "counted network. Observed delays range from 5 to 15 seconds.\n")


def _hay(leerie, task="Fix it, see BUG.md"):
    return leerie._scope_norm(task + "\n" + REPORT)


def _out(cases, ranges=None):
    return {"report_ranges": ranges if ranges is not None else [
        {"param": "delay", "min": 5, "max": 15, "unit": "s",
         "span_quote": "Observed delays range from 5 to 15 seconds."}],
        "cases": cases}


def _case(i, origin="report_example", params=(), quote=(
        "re-renders the main body 8s later")):
    return {"case_index": i, "origin": origin, "params": list(params),
            "span_quote": quote, "reason": "r"}


# --- the decision ------------------------------------------------------------

def test_in_range_report_case_counts_and_variants_do_not(leerie):
    names = ["a", "b", "c", "d"]
    out = _out([
        _case(0, params=[{"range_ref": 0, "value": 8000, "unit": "ms"}]),
        _case(1, params=[{"range_ref": 0, "value": 17000, "unit": "ms"}]),
        _case(2, origin="other_scenario"),
        _case(3, params=[{"range_ref": 0, "value": 15, "unit": "s"}]),
    ])
    v = leerie._scope_judge_counted(out, _hay(leerie), names,
                                    {"a", "b", "c", "d"})
    assert [v[n]["counted"] for n in names] == [True, False, False, True]
    assert v["b"]["in_range"] is False
    assert v["c"]["origin"] == "other_scenario"


def test_passing_case_never_counts(leerie):
    v = leerie._scope_judge_counted(_out([_case(0)]), _hay(leerie), ["a"],
                                    set())
    assert v["a"]["counted"] is False


def test_quote_must_be_a_normalised_substring(leerie):
    """Whitespace and Unicode form are normalised (NFC); anything else in the
    quote must be verbatim."""
    decomposed = unicodedata.normalize("NFD", "café")
    task = "Fix the café menu, see BUG.md"
    hay = leerie._scope_norm(task + "\n" + REPORT)
    out = _out([
        _case(0, quote="re-renders   the main\nbody 8s later"),
        _case(1, quote=f"the {decomposed} menu"),
        _case(2, quote="re-renders the main body 9s later"),
        _case(3, quote=""),
    ])
    v = leerie._scope_judge_counted(out, hay, ["a", "b", "c", "d"],
                                    {"a", "b", "c", "d"})
    assert [v[n]["quote_ok"] for n in "abcd"] == [True, True, False, False]


def test_unverified_range_is_neutral_and_overlapping_ranges_merge(leerie):
    ranges = [
        {"param": "delay", "min": 5, "max": 15, "unit": "s",
         "span_quote": "Observed delays range from 5 to 15 seconds."},
        {"param": "delay2", "min": 14000, "max": 20000, "unit": "ms",
         "span_quote": "Observed delays range from 5 to 15 seconds."},
        {"param": "made up", "min": 1, "max": 2, "unit": "s",
         "span_quote": "a passage that is not in the report"},
    ]
    out = _out([
        # 18 s lies outside range 0 alone but inside its hull with range 1.
        _case(0, params=[{"range_ref": 0, "value": 18, "unit": "s"}]),
        # The unverified range 2 says nothing: neutral.
        _case(1, params=[{"range_ref": 2, "value": 99, "unit": "s"}]),
        # Out of the merged hull.
        _case(2, params=[{"range_ref": 1, "value": 25000, "unit": "ms"}]),
        # A different unit than the range: neutral.
        _case(3, params=[{"range_ref": 0, "value": 999, "unit": "count"}]),
    ], ranges)
    v = leerie._scope_judge_counted(out, _hay(leerie), list("abcd"),
                                    set("abcd"))
    assert [v[n]["counted"] for n in "abcd"] == [True, True, False, True]


def test_bad_indices_and_bool_index_are_ignored(leerie):
    out = _out([_case(5), _case(True), _case(-1)])
    assert leerie._scope_judge_counted(out, _hay(leerie), ["a"], {"a"}) == {}


# --- has-work readers ----------------------------------------------------------

def _set(idx, scope=None, broken=False):
    s = {"index": idx, "defect_files": ["t/d.test.ts"],
         "control_files": ["t/c.test.ts"], "cases": {}}
    if scope is not None:
        s["scope"] = scope
    if broken:
        s["broken"] = True
    return s


BOUND_NONE = {"bounded": True, "counted_cases": {"t/d.test.ts": []},
              "judged": {}}
BOUND_ONE = {"bounded": True, "counted_cases": {"t/d.test.ts": ["c1"]},
             "judged": {}}


@pytest.mark.parametrize("set_, files, expected", [
    (_set(1), ["t/d.test.ts"], True),                      # unbounded
    (_set(1, {"bounded": False, "error": "x"}), ["t/d.test.ts"], True),
    (_set(1, BOUND_ONE), ["t/d.test.ts"], True),
    (_set(1, BOUND_ONE), ["t/other.test.ts"], False),
    (_set(1, BOUND_NONE), ["t/d.test.ts"], False),           # variant set
    (_set(1, BOUND_ONE, broken=True), ["t/d.test.ts"], False),
])
def test_result_has_work(leerie, set_, files, expected):
    assert leerie._acceptance_result_has_work(set_, files) is expected


def test_work_fails_vs_any_fails(leerie):
    """A variant set failing is repaired (`_acceptance_any_fails`) but is not
    work (`_acceptance_work_fails`) — the two readers must disagree here."""
    sets = [_set(1, BOUND_NONE), _set(2, BOUND_ONE)]
    res = [{"index": 1, "passed": False, "unmeasured": False,
            "failing_files": ["t/d.test.ts"]},
           {"index": 2, "passed": True, "unmeasured": False,
            "failing_files": []}]
    assert leerie._acceptance_any_fails(res) is True
    assert leerie._acceptance_work_fails(res, sets) is False
    res[1] = {"index": 2, "passed": False, "unmeasured": False,
              "failing_files": ["t/d.test.ts"]}
    assert leerie._acceptance_work_fails(res, sets) is True


def test_residual_drops_variant_sets_at_read_time(leerie):
    residual = {"failing_sets": 2, "total_sets": 2, "cases": ["x", "y"],
                "sets": [1, 2], "cases_by_set": {"1": ["x"], "2": ["y"]},
                "files_by_set": {"1": ["t/d.test.ts"], "2": ["t/d.test.ts"]}}
    out = leerie._acceptance_residual_unbroken(
        residual, [_set(1, BOUND_NONE), _set(2, BOUND_ONE)])
    assert out["sets"] == [2] and out["cases"] == ["y"]
    assert leerie._acceptance_residual_unbroken(
        residual, [_set(1, BOUND_NONE), _set(2, BOUND_NONE)]) is None
    # A residual recorded before files_by_set falls back to the set's bound.
    old = {k: v for k, v in residual.items() if k != "files_by_set"}
    assert leerie._acceptance_residual_unbroken(
        old, [_set(1, BOUND_NONE), _set(2)])["sets"] == [2]


def _outcome_state(leerie, tmp_path, sets, final):
    st = mock.Mock()
    st.data = {"subtask_status": {"a": "complete"},
               "acceptance": {"sets": sets, "gate": {"final": final}}}
    st.save = mock.Mock()
    st.run_dir = tmp_path
    return st


def test_decide_outcome_ignores_a_failing_variant_set(leerie, tmp_path,
                                                      monkeypatch):
    monkeypatch.setattr(leerie, "_write_run_json", lambda *a, **k: None)
    final = [{"index": 1, "passed": False, "unmeasured": False,
              "failing_files": ["t/d.test.ts"]}]
    st = _outcome_state(leerie, tmp_path, [_set(1, BOUND_NONE)], final)
    assert leerie._decide_outcome(st, any_commits=True) == "done"
    st = _outcome_state(leerie, tmp_path, [_set(1, BOUND_ONE)], final)
    assert leerie._decide_outcome(st, any_commits=True) == "unconverged"
    st = _outcome_state(leerie, tmp_path, [_set(1)], final)
    assert leerie._decide_outcome(st, any_commits=True) == "unconverged"


# --- per case, not per file (barnacle 7cc87f9a replay) ------------------------

# The replay's shape: one defect file holds a counted case (the special-
# character case, passing by the end) and a variant case the scope judge
# typed other_scenario (the `$` case), the only one still failing.
BOUND_MIXED = {"bounded": True,
               "counted_cases": {"t/d.test.ts": ["special chars"]},
               "judged": {}}


@pytest.mark.parametrize("cases, expected", [
    ({"t/d.test.ts": ["no $"]}, False),                 # only the variant
    ({"t/d.test.ts": ["special chars"]}, True),          # the counted one
    ({"t/d.test.ts": ["no $", "special chars"]}, True),
    ({"t/d.test.ts": []}, True),        # no per-case record: whole file
    (None, True),
    ({"t/other.test.ts": ["no $"]}, True),   # no record for THIS file
])
def test_has_work_is_decided_per_failing_case(leerie, cases, expected):
    set_ = _set(1, BOUND_MIXED)
    assert leerie._acceptance_result_has_work(
        set_, ["t/d.test.ts"], cases) is expected


def test_an_unbounded_set_is_work_whatever_its_cases(leerie):
    assert leerie._acceptance_result_has_work(
        _set(1), ["t/d.test.ts"], {"t/d.test.ts": ["no $"]}) is True


def test_decide_outcome_reads_failing_cases_not_files(leerie, tmp_path,
                                                      monkeypatch):
    """The replay ended `unconverged` on a variant: its file also held a
    counted case. Per case, it is `done` with the variant left over — and
    the same file failing on the counted case is still work."""
    monkeypatch.setattr(leerie, "_write_run_json", lambda *a, **k: None)
    variant = [{"index": 5, "passed": False, "unmeasured": False,
                "failing_files": ["t/d.test.ts"],
                "failing_cases": {"t/d.test.ts": ["no $"]}}]
    st = _outcome_state(leerie, tmp_path, [_set(5, BOUND_MIXED)], variant)
    assert leerie._decide_outcome(st, any_commits=True) == "done"
    counted = [dict(variant[0], failing_cases={
        "t/d.test.ts": ["special chars"]})]
    st = _outcome_state(leerie, tmp_path, [_set(5, BOUND_MIXED)], counted)
    assert leerie._decide_outcome(st, any_commits=True) == "unconverged"


def test_residual_is_read_per_case(leerie):
    res = [{"index": 5, "passed": False, "unmeasured": False,
            "failing_files": ["t/d.test.ts"],
            "failing_cases": {"t/d.test.ts": ["no $"]}},
           {"index": 6, "passed": False, "unmeasured": False,
            "failing_files": ["t/d.test.ts"],
            "failing_cases": {"t/d.test.ts": ["special chars"]}}]
    sets = [_set(5, BOUND_MIXED), _set(6, BOUND_MIXED)]
    residual = leerie._acceptance_residual_of(res, sets)
    assert residual["failing_cases_by_set"] == {
        "5": {"t/d.test.ts": ["no $"]},
        "6": {"t/d.test.ts": ["special chars"]}}
    out = leerie._acceptance_residual_unbroken(residual, sets)
    assert out["sets"] == [6] and out["failing_sets"] == 1
    assert leerie._acceptance_work_fails(res[:1], sets) is False
    assert leerie._acceptance_work_fails(res, sets) is True


def test_settle_pending_no_work_accepts_variant_failures(leerie, monkeypatch):
    finished = []
    monkeypatch.setattr(leerie, "_finish_no_work_run",
                        lambda st, m, *a, **k: finished.append(m))

    async def _res(st, caps):
        return [{"index": 1, "passed": False, "unmeasured": False,
                 "failing_files": ["t/d.test.ts"]}]
    monkeypatch.setattr(leerie, "_acceptance_results_on_head", _res)
    st = mock.Mock()
    st.data = {"no_work_confirmation": {"judge_evidence": "ev"},
               "acceptance": {"sets": [_set(1, BOUND_NONE)]}}
    assert asyncio.run(leerie._settle_pending_no_work(st, {})) is True
    assert st.data["no_work_acceptance"]["verdict"] == "pass"
    assert finished


# --- the judge call -------------------------------------------------------------

def _repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    for c in (["git", "init", "-q", "-b", "main"],
              ["git", "config", "user.email", "t@t"],
              ["git", "config", "user.name", "t"]):
        subprocess.run(c, cwd=repo, check=True)
    (repo / "a.txt").write_text("a\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "i"], cwd=repo, check=True)
    (repo / "BUG.md").write_text(REPORT)          # untracked: the report
    return repo


def _judge_state(tmp_path, repo):
    st = mock.Mock()
    st.repo_root = repo
    st.run_dir = tmp_path / "run"
    st.run_dir.mkdir()
    st.data = {}
    st.bump_workers = mock.Mock()
    return st


def _valid_set(tmp_path):
    d = tmp_path / "set-1"
    (d / "t").mkdir(parents=True)
    (d / "t" / "d.test.ts").write_text("it('a'); it('b'); it('c')\n")
    return {"index": 1, "dir": str(d), "defect_files": ["t/d.test.ts"],
            "control_files": [], "cases": {},
            "base_failing_cases": {"t/d.test.ts": ["a", "b"]},
            "base_case_status": {"t/d.test.ts": {"a": "failed",
                                                 "b": "failed",
                                                 "c": "passed"}},
            "base_failure_messages": {"t/d.test.ts": {"a": "expected 1"}}}


def test_judge_records_scope_on_a_report_pointer_task(leerie, tmp_path,
                                                      monkeypatch):
    repo = _repo(tmp_path)
    st = _judge_state(tmp_path, repo)
    seen = []

    async def fake(**kw):
        seen.append(kw)
        return _out([
            _case(0, params=[{"range_ref": 0, "value": 8, "unit": "s"}]),
            _case(1, origin="other_scenario"),
            _case(2)])
    monkeypatch.setattr(leerie, "claude_p", fake)
    s_ = _valid_set(tmp_path)
    asyncio.run(leerie._acceptance_scope_judge_sets(
        "Fix it, see BUG.md", st, dict(leerie.DEFAULT_CAPS), {}, {}, [s_]))
    assert len(seen) == 1
    kw = seen[0]
    assert kw["schema_key"] == "scope_judge"
    assert kw["allowed_tools"] == "Read" and kw["max_turns"] == 3
    up = kw["user_prompt"]
    assert "[0] (failed) a\n    failure: expected 1" in up
    assert "[2] (passed) c" in up
    assert "re-renders the main body" in up        # the report is inlined
    assert s_["scope"]["bounded"] is True
    # c passed at the own start, so it never counts even typed in-scope.
    assert s_["scope"]["counted_cases"] == {"t/d.test.ts": ["a"]}
    st.bump_workers.assert_called_once()


def test_no_report_means_no_judge(leerie, tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    st = _judge_state(tmp_path, repo)

    async def fake(**kw):
        pytest.fail("the judge must not run on a task with no report")
    monkeypatch.setattr(leerie, "claude_p", fake)
    s_ = _valid_set(tmp_path)
    asyncio.run(leerie._acceptance_scope_judge_sets(
        "Fix the login timeout", st, dict(leerie.DEFAULT_CAPS), {}, {}, [s_]))
    assert "scope" not in s_


def test_judge_failure_leaves_the_set_unbounded(leerie, tmp_path,
                                                monkeypatch):
    repo = _repo(tmp_path)
    st = _judge_state(tmp_path, repo)

    async def fake(**kw):
        raise leerie.WorkerError("crashed")
    monkeypatch.setattr(leerie, "claude_p", fake)
    s_ = _valid_set(tmp_path)
    asyncio.run(leerie._acceptance_scope_judge_sets(
        "Fix it, see BUG.md", st, dict(leerie.DEFAULT_CAPS), {}, {}, [s_]))
    assert s_["scope"]["bounded"] is False
    assert leerie._acceptance_counted_files(s_) is None
    assert leerie._acceptance_result_has_work(s_, ["t/d.test.ts"]) is True


def test_budget_exhaustion_propagates(leerie, tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    st = _judge_state(tmp_path, repo)
    st.bump_workers = mock.Mock(side_effect=leerie.WorkerError("budget"))

    async def fake(**kw):
        pytest.fail("no call past an exhausted budget")
    monkeypatch.setattr(leerie, "claude_p", fake)
    with pytest.raises(leerie.WorkerError):
        asyncio.run(leerie._acceptance_scope_judge_sets(
            "Fix it, see BUG.md", st, dict(leerie.DEFAULT_CAPS), {}, {},
            [_valid_set(tmp_path)]))


def test_registration(leerie):
    assert "scope_judge" in leerie.WORKER_TYPES
    assert "scope_judge" in leerie.PLANNING_WORKER_TYPES
    assert leerie.EFFORT_DEFAULT_PER_WORKER["scope_judge"] == "medium"
    assert "scope_judge" not in leerie.MODEL_DEFAULT_PER_WORKER
    props = leerie.SCHEMAS["scope_judge"]["properties"]
    assert (props["cases"]["items"]["properties"]["origin"]["enum"]
            == list(leerie.SCOPE_ORIGINS))
    assert (Path(leerie.__file__).resolve().parent.parent / "prompts"
            / "scope_judge.md").is_file()
