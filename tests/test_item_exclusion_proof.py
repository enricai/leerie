"""An `already_on_tree` exclusion is proven by execution (DESIGN §5 *An
`already_on_tree` exclusion is a claim about the code, so it is proven by
execution*).

Motive (bench PC, arm C): once the planner loop made every plan account for
every required item, planners excluded as `already_on_tree` items their runs
had ended with unmet, in 9 of 12 target samples. 4f9a4b4f item 13 was
excluded in 3 of 3, with evidence citing the optional-step skip, the same
misreading the original run's delivery judge made. The planner's word is no
evidence; the exclusion's own commands must pass on the run's own start and
fail on an earlier tree of the task, with the commands' test files as the own
start has them.

Real git repositories and real commands throughout (`_settle_by_execution`,
confined, in committed views); only `claude_p` is stubbed, and B*'s host
pressure reading is pinned quiet as the acceptance tests pin it.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

from tests.conftest import run_git_cwd_first_stdout as _git

BUGGY = "def add(a, b):\n    return a - b\n"
FIXED = "def add(a, b):\n    return a + b\n"
DETECTS = ("from calc import add\n\n"
           "def test_add_sums():\n    assert add(2, 3) == 5\n")
UNRELATED = "def test_truth():\n    assert 1 + 1 == 2\n"
CONFTEST = ("import sys, pathlib\n"
            "sys.path.insert(0, str(pathlib.Path(__file__).parent))\n")
TASK = "fix the bug in report.md"
ITEMS = [{"item": "add(2, 3) returns 5", "span": "R1"}]


def _pytest(*files: str) -> list[str]:
    return [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
            *files]


@pytest.fixture(autouse=True)
def _quiet_host(leerie, monkeypatch):
    monkeypatch.setattr(leerie, "_host_cpu_psi", lambda *a, **k: 0.0)
    leerie._DEPS_INSTALLED.clear()
    yield
    leerie._DEPS_INSTALLED.clear()


@pytest.fixture
def chain(tmp_path):
    """The earlier tree (the task's first run started here: add subtracts,
    an unrelated test exists) and the own start (add fixed, a test that
    detects it and a new test that detects nothing)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@x")
    _git(repo, "config", "user.name", "t")
    (repo / "calc.py").write_text(BUGGY)
    (repo / "conftest.py").write_text(CONFTEST)
    (repo / "test_other.py").write_text(UNRELATED)
    (repo / "report.md").write_text("add(2, 3) returns -1; it must be 5\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "earlier")
    earlier = _git(repo, "rev-parse", "HEAD")
    (repo / "calc.py").write_text(FIXED)
    (repo / "test_calc.py").write_text(DETECTS)
    (repo / "test_new_trivial.py").write_text(UNRELATED)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "own start")
    return {"repo": repo, "earlier": earlier,
            "own": _git(repo, "rev-parse", "HEAD")}


def _st(leerie, tmp_path, chain, *, sibling=True, **data):
    root = tmp_path / "state"
    run_dir = root / "runs" / "run-b"
    run_dir.mkdir(parents=True)
    (run_dir / "logs").mkdir()
    if sibling:
        # The task's first run, which started on the earlier tree.
        sib = root / "runs" / "run-a"
        sib.mkdir()
        (sib / "state.json").write_text(json.dumps({
            "task": TASK, "started_at": "2026-11-01T00:00:00+00:00",
            "repo_state_before_planning": {"head": chain["earlier"]}}))
    st = leerie.State(root, "run-b", repo_root=chain["repo"])
    st.data = {"task": TASK, "verbosity": "quiet",
               "categories": ["bug-fixing"], "worker_count": 0,
               "started_at": "2026-11-08T00:00:00+00:00",
               "repo_state_before_planning": {"head": chain["own"]},
               "required_items": ITEMS, "skip_repo_map": True,
               "classifier_questions": [], "needs_source_of_truth": False,
               "source_of_truth_pref": "both"}
    st.data.update(data)
    st.save()
    leerie._ensure_run_repo(st)
    return st


def _caps(leerie):
    caps = dict(leerie.DEFAULT_CAPS)
    caps.update(worker_timeout_sec=120, confirm_pairs=1,
                confirm_pairs_escalated=1, confirm_budget_sec=60,
                planner_samples=1)
    return caps


def _prove(leerie, st, argvs, trees=None):
    trees = leerie._item_exclusion_trees(st) if trees is None else trees
    return asyncio.run(leerie._prove_item_exclusion(
        st, _caps(leerie), 0, argvs, trees))


# === the trees ==============================================================

def test_the_earlier_tree_is_the_tasks_first_run_start(
        leerie, tmp_path, chain):
    st = _st(leerie, tmp_path, chain)
    assert leerie._item_exclusion_trees(st) == [chain["earlier"]]


def test_a_tasks_first_run_has_no_earlier_tree(leerie, tmp_path, chain):
    st = _st(leerie, tmp_path, chain, sibling=False)
    assert leerie._item_exclusion_trees(st) == []


def test_the_proof_tree_carries_the_own_starts_test_files(
        leerie, tmp_path, chain):
    st = _st(leerie, tmp_path, chain)
    rev = leerie._exclusion_proof_commit(
        st, chain["earlier"], chain["own"], [_pytest("test_calc.py")])
    repo = leerie._run_repo(st)
    show = lambda path: _git(repo, "show", f"{rev}:{path}")  # noqa: E731
    assert show("test_calc.py").strip() == DETECTS.strip()
    assert show("calc.py").strip() == BUGGY.strip(), \
        "only the test files come from the own start"
    assert leerie._exclusion_proof_commit(
        st, chain["earlier"], chain["own"],
        [["grep", "-q", "a + b", "calc.py"]]) == chain["earlier"]


# === the proof, executed ====================================================

def test_a_command_that_detects_the_item_proves_the_exclusion(
        leerie, tmp_path, chain):
    st = _st(leerie, tmp_path, chain)
    rec = _prove(leerie, st, [_pytest("test_calc.py")])
    assert rec["proven"] is True, rec
    assert rec["own"] == "pass"
    assert rec["earlier"] == [{"base": chain["earlier"], "verdict": "fail"}]


def test_a_command_that_passes_on_both_trees_proves_nothing(
        leerie, tmp_path, chain):
    st = _st(leerie, tmp_path, chain)
    rec = _prove(leerie, st, [_pytest("test_other.py")])
    assert rec["proven"] is False
    assert rec["earlier"] == [{"base": chain["earlier"], "verdict": "pass"}]
    assert "do not detect the item" in rec["why"]


def test_a_test_missing_on_the_earlier_tree_cannot_fail_by_its_absence(
        leerie, tmp_path, chain):
    """`test_new_trivial.py` does not exist on the earlier tree; run there
    as-is it fails for that reason alone. Written onto it as the own start
    has it, it passes, and the exclusion is not proven."""
    st = _st(leerie, tmp_path, chain)
    rec = _prove(leerie, st, [_pytest("test_new_trivial.py")])
    assert rec["proven"] is False, rec
    assert rec["earlier"][0]["verdict"] == "pass"


def test_a_command_failing_on_the_own_start_proves_nothing(
        leerie, tmp_path, chain):
    st = _st(leerie, tmp_path, chain)
    rec = _prove(leerie, st, [[sys.executable, "-c", "raise SystemExit(1)"]])
    assert rec["proven"] is False
    assert rec["own"] == "fail" and rec["earlier"] == []


def test_a_first_run_proves_nothing_and_runs_nothing(
        leerie, tmp_path, chain, monkeypatch):
    st = _st(leerie, tmp_path, chain, sibling=False)

    async def never(*a, **k):
        pytest.fail("nothing can be proven without an earlier tree")
    monkeypatch.setattr(leerie, "_settle_by_execution", never)
    rec = _prove(leerie, st, [_pytest("test_calc.py")])
    assert rec["proven"] is False
    assert "first run" in rec["why"]


# === the accounting reads the proof ========================================

def _plan(*entries, subtasks=()):
    return {"domain": "bug-fixing", "status": "ready", "blocker_kind": "none",
            "subtasks": list(subtasks), "items_not_needed": list(entries),
            "confidence": {"task_understanding": 9.5,
                           "decomposition_quality": 9.5, "basis": "stub"}}


def _excl(*argvs, i=0):
    return {"item_index": i, "reason_kind": "already_on_tree",
            "reason": "calc.py:2 adds", "verify": [{"argv": a} for a in argvs]}


@pytest.mark.parametrize("proof,status", [
    ({"proven": True, "why": "w"}, "excluded"),
    ({"proven": False, "why": "they also pass on abc"}, "uncovered"),
    (None, "uncovered"),
])
def test_only_a_proven_exclusion_excludes(leerie, proof, status):
    argv = _pytest("test_calc.py")
    proofs = ({leerie._exclusion_proof_key(0, [argv]): proof}
              if proof else {})
    rows = leerie._item_accounting_rows([_plan(_excl(argv))], ITEMS, proofs)
    assert rows[0]["status"] == status
    assert rows[0]["excluded"][0]["proven"] is (status == "excluded")


def test_a_proof_of_other_commands_is_not_this_ones(leerie):
    proofs = {leerie._exclusion_proof_key(0, [_pytest("test_calc.py")]):
              {"proven": True, "why": "w"}}
    rows = leerie._item_accounting_rows(
        [_plan(_excl(_pytest("test_other.py")))], ITEMS, proofs)
    assert rows[0]["status"] == "uncovered"


def test_an_unproven_exclusion_goes_back_with_its_reason(leerie):
    argv = _pytest("test_other.py")
    proofs = {leerie._exclusion_proof_key(0, [argv]): {
        "proven": False, "why": "its commands also pass on 0123abcd"}}
    issues = leerie._check_item_claims(_plan(_excl(argv)), ITEMS, 1, proofs)
    assert len(issues) == 1
    assert issues[0].startswith("ITEM_EXCLUSION_UNPROVEN: item 0 — ")
    assert "its commands also pass on 0123abcd" in issues[0]
    assert "no-op on its own evidence" in issues[0]


def test_planner_schema_types_the_exclusions_verify(leerie):
    entry = leerie.SCHEMAS["planner"]["$defs"]["items_not_needed"]["items"]
    assert entry["properties"]["verify"]["items"]["required"] == ["argv"]
    assert "verify" not in entry["required"], \
        "an other_domain entry carries none; code requires it for the proof"


def test_planner_prompt_documents_the_proof(leerie):
    text = leerie._load_prompt("planner")
    assert "`already_on_tree_provable`" in text
    assert "fail on an earlier tree of this task" in text


# === the real planning phase ================================================

def _drive_plan(leerie, monkeypatch, st, replies):
    calls: list[dict] = []

    async def fake_claude_p(**kw):
        if kw["schema_key"] == "fit_judge":
            return {"score": 0.95, "rationale": "fits"}
        assert kw["schema_key"] == "planner", kw["schema_key"]
        calls.append(kw)
        return json.loads(json.dumps(replies[min(len(calls),
                                                 len(replies)) - 1]))
    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    plans = asyncio.run(leerie.phase_plan(TASK, st, _caps(leerie), {
        "planner": "sonnet", "fit_judge": "sonnet", "splitter": "sonnet"},
        {"planner": "medium", "fit_judge": "medium", "splitter": "medium"}))
    return plans, calls


def test_an_unproven_exclusion_is_sent_back_and_a_proven_one_proceeds(
        leerie, tmp_path, chain, monkeypatch):
    st = _st(leerie, tmp_path, chain)
    monkeypatch.chdir(chain["repo"])
    weak = _plan(_excl(_pytest("test_other.py")))
    strong = _plan(_excl(_pytest("test_calc.py")))
    plans, calls = _drive_plan(leerie, monkeypatch, st, [weak, strong])

    assert '"already_on_tree_provable": true' in calls[0]["user_prompt"]
    assert len(calls) == 2, "the unproven exclusion must re-invoke the planner"
    assert "ITEM_EXCLUSION_UNPROVEN: item 0" in calls[1]["user_prompt"]
    assert "do not detect the item" in calls[1]["user_prompt"]
    proofs = st.data["item_exclusion_proofs"]
    assert [p["proven"] for p in proofs.values()] == [False, True]
    rows = leerie._item_accounting_rows(plans, ITEMS, proofs)
    assert rows[0]["status"] == "excluded"


def test_a_first_run_is_told_and_an_exclusion_there_goes_back(
        leerie, tmp_path, chain, monkeypatch):
    st = _st(leerie, tmp_path, chain, sibling=False)
    monkeypatch.chdir(chain["repo"])
    claimed = _plan(subtasks=[{
        "id": "bugfix-001", "title": "t", "intent": "i",
        "success_criteria_seed": "c", "files_likely_touched": ["calc.py"],
        "depends_on": [], "provides": [], "requires": [],
        "change_kind": "fix", "change_shape": "point", "size": "small",
        "claims": {"sites": [], "items": [
            {"item_index": 0, "kind": "deliverable"}]}}])
    _, calls = _drive_plan(leerie, monkeypatch, st,
                           [_plan(_excl(_pytest("test_calc.py"))), claimed])
    assert '"already_on_tree_provable": false' in calls[0]["user_prompt"]
    assert len(calls) == 2
    assert "first run" in calls[1]["user_prompt"]


def test_an_unproven_residual_reaches_the_pr_with_its_reason(
        leerie, tmp_path, chain, monkeypatch):
    st = _st(leerie, tmp_path, chain)
    argv = _pytest("test_other.py")
    st.data["item_exclusion_proofs"] = {
        leerie._exclusion_proof_key(0, [argv]): {
            "proven": False, "why": "its commands also pass on 0123abcd"}}

    async def judge(**kw):
        assert kw["schema_key"] == "task_coverage_judge"
        return {"task_covered": True, "coverage_gaps": [], "rationale": "r"}
    monkeypatch.setattr(leerie, "claude_p", judge)
    asyncio.run(leerie.phase_planning_coverage_gate(
        [_plan(_excl(argv))], TASK, st, _caps(leerie), {}, {}))
    assert st.data["item_coverage"]["uncovered"] == [0]
    lines = leerie._pr_not_verified(st.data, lambda sid: True)[
        "Required items no planned subtask claimed"]
    assert lines == ["- [ ] item 0: add(2, 3) returns 5 — the plan excluded "
                     "it as already on the tree, not proven: its commands "
                     "also pass on 0123abcd"]
