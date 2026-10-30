"""The run outcome is decided once (DESIGN §6): `_decide_outcome` is the only
writer of `outcome`, consults unfinished work before the empty-branch check,
and every no-work exit reaches it through `_finish_no_work_run`."""
from __future__ import annotations

import ast
import json
import subprocess
from pathlib import Path

import pytest

LEERIE_PY = Path(__file__).resolve().parent.parent / "orchestrator" / "leerie.py"


@pytest.fixture
def st(leerie, tmp_path):
    s = leerie.State(tmp_path / ".leerie", "fix-001-abcdef")
    s.data = {"task": "x", "subtask_status": {"a": "complete"}}
    s.save()
    # The run branch whose tree ships: a delivery verdict counts only on it
    # (DESIGN §6 *A delivery verdict counts only on the tree it was
    # measured on*); the params below write SHIP for that tree.
    repo = s.repo_root
    for args in (["init", "-q", "-b", "leerie/runs/fix-001-abcdef"],
                 ["config", "user.email", "t@t"],
                 ["config", "user.name", "t"],
                 ["commit", "-q", "--allow-empty", "-m", "base"]):
        subprocess.run(["git", *args], cwd=repo, check=True,
                       capture_output=True)
    return s


def _ship(st, extra: dict) -> dict:
    tree = subprocess.run(["git", "rev-parse",
                           "leerie/runs/fix-001-abcdef^{tree}"],
                          cwd=st.repo_root,
                          check=True, capture_output=True,
                          text=True).stdout.strip()
    return json.loads(json.dumps(extra).replace("SHIP", tree))


def _run_json(st):
    return json.loads((st.run_dir / "run.json").read_text())


@pytest.mark.parametrize("any_commits,expected", [(True, "done"),
                                                  (False, "no_work")])
def test_clean_run(leerie, st, any_commits, expected):
    assert leerie._decide_outcome(st, any_commits=any_commits) == expected
    assert st.data["outcome"] == expected
    assert st.data["outcome_reasons"] == []
    assert _run_json(st)["outcome"] == expected


@pytest.mark.parametrize("extra,reason_word", [
    ({"subtask_status": {"a": "complete", "b": "unconverged"}},
     "unconverged: b"),
    ({"test_id_diff": {"new": ["t :: x"]}}, "newly failing"),
    ({"acceptance": {"gate": {"final": [
        {"index": 0, "passed": True}, {"index": 1, "passed": False}]}}},
     "1 held-out acceptance set(s)"),
    ({"acceptance": {"gate": {"final": [
        {"index": 0, "passed": False, "unmeasured": True}],
        "residual": {"failing_sets": 2, "total_sets": 3}}}},
     "2 held-out acceptance set(s)"),
    ({"delivery_gate": {"unmet_before": [{"item": "i"}],
                        "unmet_after": [{"item": "i"}],
                        "judged_after": {"tree": "SHIP"}}},
     "1 required item(s)"),
    ({"delivery_gate": {"unmet_before": [{"item": "i"}],
                        "judged_before": {"tree": "SHIP"}}},
     "1 required item(s)"),
    ({"delivery_gate": {"unmet_final": [], "final_tree": "0" * 40}},
     "not read on the tree that ships"),
])
@pytest.mark.parametrize("any_commits", [True, False])
def test_unfinished_work_is_unconverged_before_the_empty_branch_check(
        leerie, st, extra, reason_word, any_commits):
    st.data.update(_ship(st, extra))
    assert leerie._decide_outcome(st, any_commits=any_commits) == "unconverged"
    assert any(reason_word in r for r in st.data["outcome_reasons"]), \
        st.data["outcome_reasons"]
    assert _run_json(st)["outcome"] == "unconverged"


@pytest.mark.parametrize("extra", [
    {"delivery_gate": {"unmet_before": [{"item": "i"}], "unmet_after": [],
                       "judged_after": {"tree": "SHIP"}}},
    {"acceptance": {"gate": {"final": [{"index": 0, "passed": False,
                                        "unmeasured": True}]}}},
    {"test_id_diff": {"new": [], "resource_affected": ["t :: flaky"]}},
    {"subtask_status": {"a": "accepted_external"}},
])
def test_resolved_or_unmeasured_signals_do_not_unconverge(leerie, st, extra):
    st.data.update(_ship(st, extra))
    assert leerie._decide_outcome(st, any_commits=True) == "done"


def test_decide_outcome_is_the_only_writer_of_outcome():
    tree = ast.parse(LEERIE_PY.read_text())
    writers = set()
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for node in ast.walk(fn):
            if (isinstance(node, ast.Subscript)
                    and isinstance(node.ctx, ast.Store)
                    and isinstance(node.slice, ast.Constant)
                    and node.slice.value == "outcome"
                    and isinstance(node.value, ast.Attribute)
                    and node.value.attr == "data"):
                writers.add(fn.name)
    assert writers == {"_decide_outcome"}


def test_finish_no_work_run_with_unconverged_work_is_not_no_work(leerie, st):
    """The post-execution empty-branch exit reaches the decider: a run whose
    branch is empty because its work did not converge is NOT reported as
    "no work required"."""
    st.data["subtask_status"] = {"a": "unconverged"}
    leerie._finish_no_work_run(st, {"<finalize>": "empty"},
                               reset_plan_state=False)
    assert st.data["outcome"] == "unconverged"
    assert st.data["no_work_required"] is False
    assert _run_json(st)["outcome"] == "unconverged"
    assert _run_json(st)["no_push"] is True


def test_finish_no_work_run_planning_time_is_no_work(leerie, st):
    leerie._finish_no_work_run(st, {"backend": "already there"})
    assert st.data["outcome"] == "no_work"
    assert st.data["no_work_required"] is True
    assert st.data["current_phase"] == "done: no work required"


def test_phase_finalize_normal_path_decides_with_commits(leerie):
    """Structure paired with the value tests above: the normal finalize path
    calls the decider with `any_commits=True` before recording finished_at."""
    import inspect
    src = inspect.getsource(leerie.phase_finalize)
    i = src.index("_decide_outcome(st, any_commits=True)")
    assert i < src.index('st.data["finished_at"] = now()')


@pytest.mark.parametrize("statuses,expected", [
    ({"feat-001": "complete"}, "done"),
    ({"feat-001": "complete", "feat-002": "unconverged"}, "unconverged"),
])
def test_phase_finalize_records_the_outcome_in_run_json(
        leerie, monkeypatch, tmp_path, statuses, expected):
    """Executes the consumer: a branch with commits finalizes `done`, or
    `unconverged` when a subtask did not converge — the run.json field the
    host launcher reads to open a draft PR."""
    import asyncio
    import subprocess

    async def _fake_run_script(name, *args):
        return subprocess.CompletedProcess(["bash", name, *args], 0, "", "")

    async def _fake_run_proc(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, "3\n", "")

    async def _noop(*a, **k):
        return None

    monkeypatch.setattr(leerie, "_run_script", _fake_run_script)
    monkeypatch.setattr(leerie, "run_proc", _fake_run_proc)
    monkeypatch.setattr(leerie, "capture_repo_deps", _noop)
    leerie_root = tmp_path / ".leerie"
    (leerie_root / "runs" / "r1").mkdir(parents=True)
    s = leerie.State(leerie_root, "r1", repo_root=tmp_path)
    s.data.update({"waves": [sorted(statuses)], "completed_waves": 1,
                   "subtask_status": dict(statuses),
                   "working_branch": "main"})
    try:
        asyncio.run(leerie.phase_finalize(
            s.leerie_root, s, no_push=True, no_verify=False,
            caps=dict(leerie.DEFAULT_CAPS), models={}, efforts={}))
    finally:
        s.release_lock()
    assert s.data["outcome"] == expected
    assert json.loads((s.run_dir / "run.json").read_text())["outcome"] \
        == expected
    assert s.data["finished_at"]


def test_residual_solution_defects_are_unconverged(leerie, st):
    st.data["conformance"] = {"_final": {"blocked": False, "residual_defects": [
        {"kind": "unhandled_path", "where": "src/x.ts:8",
         "concrete_case": "late results", "why_ships_a_defect": "w"}]}}
    assert leerie._decide_outcome(st, any_commits=True) == "unconverged"
    assert st.data["outcome_reasons"] == [
        "1 solution defect(s) the final conformance left unfixed after its "
        "last round (0 of them driven by the delivery re-drive, none "
        "re-verified): [unhandled_path] src/x.ts:8: late results"]
