"""Review round 6 H1, end to end on a real pytest suite: a one-off flake
(fails on the first final measurement only, passes every run after) in a
file whose serial rerun takes longer than the confirm budget. Before the
fix it was typed `new` with ZERO finished reruns and the run ended
`unconverged`. Now no base/final pair finished, so it is
`budget_short_unsettled`: the run ends `done`, with a "Needs human
verification" entry naming the file. Control: the same flake with a budget
that lets the pairs run is `resource_affected` and leaves no gap (DESIGN
§9 *Which failures are new*, step 4)."""
from __future__ import annotations

import asyncio
import subprocess

import pytest

from tests.conftest import relocate_into_run_repo

CMD = "python3 -m pytest -q -p no:cacheprovider tests"
SUITE = '''\
import os, time

def test_slow():
    time.sleep({sleep})

def test_flaky():
    marker = os.path.join(os.path.dirname(__file__), "seen")
    first = not os.path.exists(marker)
    open(marker, "w").write("1")
    # only the FIRST final measurement fails; every run after passes
    assert not ({final} and first)
'''


def _git(cwd, *a):
    r = subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


@pytest.fixture
def repo(leerie, tmp_path):
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / ".gitignore").write_text("seen\n.leerie/\n")
    (repo / "tests" / "test_suite.py").write_text(
        SUITE.format(sleep=3, final="False"))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    base = _git(repo, "rev-parse", "HEAD")
    run_id = "fix-001-abcdef"
    _git(repo, "checkout", "-q", "-b", f"leerie/runs/{run_id}")
    (repo / "tests" / "test_suite.py").write_text(
        SUITE.format(sleep=3, final="True"))
    (repo / "x.txt").write_text("change")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "final")
    _git(repo, "checkout", "-q", "main")
    root = repo / ".leerie"
    run_dir = root / "runs" / run_id
    (run_dir / "logs").mkdir(parents=True)
    staging = run_dir / "worktrees" / "staging"
    _git(repo, "worktree", "add", "-q", str(staging), f"leerie/runs/{run_id}")
    st = leerie.State(root, run_id)
    st.data = {"task": "x", "working_branch": "main",
               "conformance": {"_baseline": {"sha": base}},
               "baseline_failing_ids": []}
    st.repo_root = repo
    st.save()
    # The run's own repository (DESIGN §6 *The run's own repository*).
    relocate_into_run_repo(leerie, st)
    return {"st": st, "staging": staging}


@pytest.mark.parametrize("budget,cut", [(2.0, True), (120.0, False)])
def test_a_one_off_flake_the_budget_cut_is_unsettled_not_new(
        leerie, repo, monkeypatch, budget, cut):
    monkeypatch.setattr(leerie, "_host_cpu_psi", lambda path=None: 0.0)
    st, staging = repo["st"], repo["staging"]
    caps = {**leerie.DEFAULT_CAPS, "confirm_budget_sec": budget,
            "confirm_intermittent_repeats": 0}
    post = {"tests": {"ran": True, "measured": True, "passed": False,
                      "command": CMD, "summary": ""}}
    # the final measurement: the flake fails this once
    assert asyncio.run(leerie._attach_failing_ids(
        post["tests"], str(staging), CMD, st, caps,
        log_path=st.run_dir / "logs" / "t.log", label="m"))
    flaky = [i for i in post["tests"]["failing_ids"] if "test_flaky" in i]
    assert flaky, post
    v = asyncio.run(leerie._confirm_final_test_failures(
        st, caps, str(staging), post,
        log_path=st.run_dir / "logs" / "t.log", label="final-r0"))
    st.data["subtask_status"] = {}
    out = leerie._decide_outcome(st, any_commits=True)
    gaps = [e for e in st.data.get("needs_human_verification") or []
            if e.get("source") == leerie.CONFIRM_BUDGET_GAP_SOURCE]
    assert v["new"] == [] and out == "done"
    assert st.data["outcome_reasons"] == []
    if cut:
        assert v["pairs_run"] == 0 and v["budget_short"] == []
        assert v["budget_short_unsettled"] == flaky
        assert len(gaps) == 1 and ("could not confirm within the budget: "
                                   "tests/test_suite.py") in gaps[0]["detail"]
        # The PR lists it under the checks the run could not settle.
        groups = leerie._pr_needs_human(st.data, lambda _s: True)
        assert any("could not confirm within the budget" in line
                   for line in groups.get(
                       "Regression checks the run could not settle", []))
    else:
        assert flaky[0] in v["resource_affected"] and gaps == []
