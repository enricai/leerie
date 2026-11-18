"""One B* engine (DESIGN §9 *Which failures are new*: one engine, not one
per caller). The final diff, the re-drive diff and the base form share
`_confirm_against_base` around `_confirm_new_failures`; a held-out vote, a
probe and a verify command's re-check are the same engine with no side A.

Real git repositories and a real pytest suite, and the real settle by
execution over real child processes (the `harness` of
tests/test_settle_by_execution.py). Only the host-pressure gate, the broker
and, where a test says so, one process boundary are stubbed.
"""
from __future__ import annotations

import asyncio

import pytest

from tests.conftest import relocate_into_run_repo
from tests.conftest import run_git_out
from tests.test_oom_naming import env  # noqa: F401  (pytest fixture)
from tests.test_settle_by_execution import (  # noqa: F401
    _py, _settle, _sub, harness)

CMD = "python3 -m pytest -q -p no:cacheprovider tests"
_git = run_git_out


def _world(leerie, tmp_path, monkeypatch, base_suite: str,
           run_suite: str):
    """A repository whose base has `base_suite` as tests/test_s.py and whose
    run branch has `run_suite`, the run's own repository, a staging tree on
    the run branch, and its final measurement with failing IDs."""
    monkeypatch.setattr(leerie, "_host_cpu_psi", lambda path=None: 0.0)
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / ".gitignore").write_text(".leerie/\n__pycache__/\n")
    (repo / "tests" / "test_s.py").write_text(base_suite)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    base = _git(repo, "rev-parse", "HEAD")
    run_id = "fix-001-abcdef"
    _git(repo, "checkout", "-qb", f"leerie/runs/{run_id}")
    (repo / "tests" / "test_s.py").write_text(run_suite)
    _git(repo, "commit", "-qam", "run")
    _git(repo, "checkout", "-q", "main")
    root = repo / ".leerie"
    run_dir = root / "runs" / run_id
    (run_dir / "logs").mkdir(parents=True)
    staging = run_dir / "worktrees" / "staging"
    _git(repo, "worktree", "add", "-q", str(staging), f"leerie/runs/{run_id}")
    st = leerie.State(root, run_id)
    st.data = {"task": "x", "working_branch": "main",
               "conformance": {"_baseline": {"sha": base}},
               "baseline_failing_ids": [], "subtask_status": {}}
    st.repo_root = repo
    st.save()
    relocate_into_run_repo(leerie, st)
    caps = {**leerie.DEFAULT_CAPS, "confirm_intermittent_repeats": 0,
            "confirm_pairs": 2}
    post = {"tests": {"ran": True, "measured": True, "passed": False,
                      "command": CMD, "summary": ""}}
    log = st.run_dir / "logs" / "t.log"
    assert asyncio.run(leerie._attach_failing_ids(
        post["tests"], str(staging), CMD, st, caps, log_path=log,
        label="m"))
    return st, caps, staging, post, log


def test_a_case_the_run_added_is_new_against_a_base_that_never_had_it(
        leerie, tmp_path, monkeypatch):
    """Side A's result is three-valued (DESIGN §9): the base's report has
    test_x passing and no test_y at all. For "this failure is new" an absent
    case is not failing there, so the failing test the run added is new on
    every pair — the final diff's reading, at its value before the engine
    was one."""
    st, caps, staging, post, log = _world(
        leerie, tmp_path, monkeypatch,
        "def test_x():\n    assert True\n",
        "def test_x():\n    assert True\n\n"
        "def test_y():\n    assert False\n")
    v = asyncio.run(leerie._confirm_final_test_failures(
        st, caps, str(staging), post, log_path=log, label="final-r0"))
    [y] = v["candidates"]
    assert y.endswith("test_y")
    assert v["new"] == [y]
    assert v["pairs_run"] == 2 and v["budget_short"] == []
    assert v["per_id"][y] == {
        "final_failed": 2, "base_failed": 0, "final_runs": 2,
        "base_runs": 2}


def test_a_case_failing_on_both_sides_is_pre_existing_through_the_helper(
        leerie, tmp_path, monkeypatch):
    """The other side of the same reading: a case the base reports failing
    on every rerun is `pre_existing`, never new — the base side is read
    from the base's own report, not from its absence."""
    st, caps, staging, post, log = _world(
        leerie, tmp_path, monkeypatch,
        "def test_x():\n    assert False\n",
        "def test_x():\n    assert False\n\n"
        "def test_y():\n    assert True\n")
    v = asyncio.run(leerie._confirm_final_test_failures(
        st, caps, str(staging), post, log_path=log, label="final-r0"))
    [x] = v["candidates"]
    assert x.endswith("test_x")
    assert v["pre_existing"] == [x]
    assert v["new"] == []


def test_a_verify_failure_with_no_budget_stands_on_its_one_run(harness):
    """A verify command is a required-behaviour check (DESIGN §9 *Which
    failures are new*): with `confirm_budget_sec` 0 nothing reruns and the
    gate is never consulted, and its own failing run stands — `fail` on
    one attempt, never a pass by default."""
    res = _settle(harness, _sub(_py("import sys; sys.exit(3)")),
                  confirm_budget_sec=0)
    assert res["verdict"] == "fail"
    assert res["commands"][0]["attempts"] == 1
    assert res["commands"][0]["rc"] == 3
    assert harness["gates"] == []


def test_a_rerun_that_raises_ends_the_settle_as_it_did(harness, monkeypatch):
    """The engine reads a raising rerun as no verdict; the settle's own
    loop let it propagate, and still does: the second run raises and so
    does the settle, after one gate and nothing more."""
    leerie = harness["leerie"]
    real = leerie._run_contained
    calls: list[int] = []

    async def contained(argv, **kw):
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("broker gone")
        return await real(argv, **kw)
    monkeypatch.setattr(leerie, "_run_contained", contained)
    with pytest.raises(RuntimeError, match="broker gone"):
        _settle(harness, _sub(_py("import sys; sys.exit(5)")))
    assert len(calls) == 2 and harness["gates"] == [1]


@pytest.mark.parametrize("absent,new", [("pass", ["c"]), ("fail", [])])
def test_a_case_absent_from_side_a_reads_as_the_caller_says(
        leerie, absent, new):
    """Side A's third value (DESIGN §9): every base rerun reports `c`
    neither failing nor passing (a probe that did not load there). Read as
    "pass" (the final diff) it never failed on the base, so it is new; read
    as "fail" (the probe flip) it never passed there, so it is not. A plain
    set from side A has no absent case; only a `_SideResult` does."""
    async def base(_left):
        return leerie._SideResult(frozenset(), frozenset({"other"}))

    async def final(_left):
        return {"c"}
    v = asyncio.run(leerie._confirm_new_failures(
        ["c"], base, final, {**leerie.DEFAULT_CAPS, "confirm_pairs": 2},
        label="t", observed="pair", absent_on_base=absent,
        read_psi=lambda: 0.0))
    assert v["new"] == new and v["pairs_run"] == 2
    assert v["per_id"]["c"]["base_failed"] == (0 if absent == "pass" else 2)
