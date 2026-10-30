"""B* — flake-robust failure verdicts (DESIGN §9 *Which failures are new*).

Unit tests of the reusable confirm (`_host_cpu_psi`, `_confirm_gate`,
`_confirm_new_failures`), the report parser that feeds it
(`_runner_report_failing_ids`), and end-to-end runs of the failing-test-ID
diff against a real pytest suite in a real git repo
(`_capture_failing_test_ids`, `_confirm_final_test_failures`)."""
from __future__ import annotations

import asyncio
import inspect
import json
import subprocess
from pathlib import Path

import pytest


class _Clock:
    """A fake monotonic clock: `sleep` advances it, as do reruns."""

    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    async def sleep(self, s: float) -> None:
        self.t += s


def _caps(leerie, **over):
    return {**leerie.DEFAULT_CAPS, **over}


# --- the host PSI reader ---------------------------------------------------

def test_host_psi_reads_some_avg10(leerie, tmp_path):
    f = tmp_path / "cpu"
    f.write_text("some avg10=37.50 avg60=1.00 avg300=0.00 total=12\n"
                 "full avg10=99.00 avg60=0.00 avg300=0.00 total=3\n")
    assert leerie._host_cpu_psi(str(f)) == 37.5


@pytest.mark.parametrize("body", ["", "garbage\n", "some avg10=x total=1\n"])
def test_host_psi_unparseable_is_none(leerie, tmp_path, body):
    f = tmp_path / "cpu"
    f.write_text(body)
    assert leerie._host_cpu_psi(str(f)) is None


def test_host_psi_missing_file_is_none(leerie, tmp_path):
    assert leerie._host_cpu_psi(str(tmp_path / "absent")) is None


def test_gate_reads_the_host_file_never_a_cgroup(leerie):
    """The container cgroup's cpu.pressure reads ~0 while the host is
    saturated (Q9); the gate must read the host-wide /proc file."""
    assert leerie._HOST_PSI_CPU_PATH == "/proc/pressure/cpu"
    for fn in (leerie._host_cpu_psi, leerie._confirm_gate,
               leerie._confirm_new_failures):
        assert "cpu.pressure" not in inspect.getsource(fn)


def test_gate_default_reader_is_the_module_reader(leerie, monkeypatch):
    """Substance for the pin above: with no injected reader the gate calls
    `_host_cpu_psi` (resolved at call time)."""
    seen = []

    def fake():
        seen.append(1)
        return 1.0
    monkeypatch.setattr(leerie, "_host_cpu_psi", fake)
    g = asyncio.run(leerie._confirm_gate(_caps(leerie)))
    assert seen and g["timed_out"] is False and g["psi"] == 1.0


# --- the gate --------------------------------------------------------------

def test_gate_waits_until_pressure_drops(leerie):
    clock = _Clock()
    readings = iter([90.0, 70.0, 39.9])
    g = asyncio.run(leerie._confirm_gate(
        _caps(leerie), read_psi=lambda: next(readings),
        sleep=clock.sleep, clock=clock))
    assert g == {"waited_sec": 4.0, "timed_out": False, "psi": 39.9,
                 "readable": True}


def test_gate_times_out_after_max_wait(leerie):
    clock = _Clock()
    g = asyncio.run(leerie._confirm_gate(
        _caps(leerie), read_psi=lambda: 100.0, sleep=clock.sleep,
        clock=clock))
    assert g["timed_out"] is True and g["readable"] is True
    assert g["waited_sec"] == 300.0


def test_gate_unreadable_psi_is_an_immediate_timeout(leerie):
    clock = _Clock()
    g = asyncio.run(leerie._confirm_gate(
        _caps(leerie), read_psi=lambda: None, sleep=clock.sleep,
        clock=clock))
    assert g == {"waited_sec": 0.0, "timed_out": True, "psi": None,
                 "readable": False}


# --- the confirm -----------------------------------------------------------

def _runners(base_sets, final_sets, calls, clock=None, cost=0.0):
    base_it, final_it = iter(base_sets), iter(final_sets)

    async def run_base(timeout):
        calls.append(("base", timeout))
        if clock is not None:
            clock.t += cost
        return next(base_it)

    async def run_final(timeout):
        calls.append(("final", timeout))
        if clock is not None:
            clock.t += cost
        return next(final_it)
    return run_base, run_final


def test_deterministic_regression_is_new_with_interleaved_pairs(leerie):
    calls = []
    b, f = _runners([set()] * 3, [{"t::x"}] * 3, calls)
    v = asyncio.run(leerie._confirm_new_failures(
        ["t::x"], b, f, _caps(leerie), label="t", read_psi=lambda: 0.0))
    assert v["new"] == ["t::x"] and v["resource_affected"] == []
    assert v["pairs_required"] == 3 and v["pairs_run"] == 3
    # Interleaved, base first: B F B F B F.
    assert [c[0] for c in calls] == ["base", "final"] * 3
    assert v["per_id"]["t::x"] == {"final_failed": 3, "base_failed": 0,
                                   "final_runs": 3, "base_runs": 3}


def test_flaky_final_is_resource_affected(leerie):
    calls = []
    b, f = _runners([set()] * 3, [{"t::x"}, set(), {"t::x"}], calls)
    v = asyncio.run(leerie._confirm_new_failures(
        ["t::x"], b, f, _caps(leerie), label="t", read_psi=lambda: 0.0))
    assert v["new"] == [] and v["resource_affected"] == ["t::x"]


def test_failing_on_base_too_is_not_new(leerie):
    calls = []
    b, f = _runners([set(), {"t::x"}, set()], [{"t::x"}] * 3, calls)
    v = asyncio.run(leerie._confirm_new_failures(
        ["t::x"], b, f, _caps(leerie), label="t", read_psi=lambda: 0.0))
    assert v["new"] == [] and v["resource_affected"] == ["t::x"]


def test_failing_every_base_rerun_is_pre_existing_not_flaky(leerie):
    """Barnacle 45fa4253 pass 2: 33 IDs failed 3/3 on both sides and were
    labelled resource_affected — the flake label. Failing every base rerun
    is the base's own failure (DESIGN §9 *Which failures are new*, step
    3), whatever the final side did."""
    calls = []
    b, f = _runners([{"t::x", "t::y"}] * 3,
                    [{"t::x", "t::y"}, {"t::x"}, {"t::x", "t::y"}], calls)
    v = asyncio.run(leerie._confirm_new_failures(
        ["t::x", "t::y"], b, f, _caps(leerie), label="t",
        read_psi=lambda: 0.0))
    assert v["pre_existing"] == ["t::x", "t::y"]
    assert v["resource_affected"] == [] and v["new"] == []
    assert v["no_verdict"] == []


def test_base_failing_some_reruns_stays_resource_affected(leerie):
    """2 of 3 base reruns is mixed evidence, not pre_existing; nor is a
    base rerun with no report counted as a failure."""
    calls = []
    b, f = _runners([{"x", "y"}, {"x"}, {"x"}], [{"x", "y"}] * 3, calls)
    v = asyncio.run(leerie._confirm_new_failures(
        ["x", "y"], b, f, _caps(leerie), label="t", read_psi=lambda: 0.0))
    assert v["pre_existing"] == ["x"] and v["resource_affected"] == ["y"]
    calls = []
    b, f = _runners([{"x"}, None, {"x"}], [{"x"}] * 3, calls)
    v = asyncio.run(leerie._confirm_new_failures(
        ["x"], b, f, _caps(leerie), label="t", read_psi=lambda: 0.0))
    assert v["pre_existing"] == [] and v["resource_affected"] == ["x"]


def test_no_rerun_with_a_report_is_no_verdict(leerie):
    """No report on either side measured nothing: `no_verdict`, never the
    flake label."""
    v = asyncio.run(leerie._confirm_new_failures(
        ["x"], leerie._no_rerun, leerie._no_rerun, _caps(leerie), label="t",
        read_psi=lambda: 0.0))
    assert v["no_verdict"] == ["x"] and v["resource_affected"] == []
    assert v["pre_existing"] == [] and v["new"] == []


def test_still_failing_splits_no_verdict(leerie):
    async def none(_t):
        return None
    v = asyncio.run(leerie._confirm_still_failing(
        ["x"], none, _caps(leerie), label="t", read_psi=lambda: 0.0))
    assert v["no_verdict"] == ["x"] and v["resource_affected"] == []
    runs = iter([{"x"}, set(), {"x"}])

    async def mixed(_t):
        return next(runs)
    v = asyncio.run(leerie._confirm_still_failing(
        ["x"], mixed, _caps(leerie), label="t", read_psi=lambda: 0.0))
    assert v["resource_affected"] == ["x"] and v["no_verdict"] == []


def test_pre_existing_reaches_the_judge_record_apart_from_new(leerie,
                                                              tmp_path):
    from tests.stub_helpers import _delivery_tree
    staging = _delivery_tree(tmp_path / "staging")
    tree = subprocess.run(["git", "-C", str(staging), "rev-parse",
                           "HEAD^{tree}"], capture_output=True, text=True,
                          check=True).stdout.strip()
    st = leerie.State(tmp_path / "s", "r")
    st.data = {"test_id_diff": {"candidates": ["a", "b"], "new": ["t :: a"],
                                "pre_existing": ["t :: b"],
                                "resource_affected": [], "tree_sha": tree}}
    lines = leerie._orchestrator_results_digest(st, staging).splitlines()
    assert lines == [
        "[orchestrator B* failing-test diff] tests newly failing on the "
        "final tree, confirmed against the base: t :: a",
        "[orchestrator B* failing-test diff] 1 test(s) failing on every "
        "base rerun too (pre-existing, not this run's): t :: b"]


def test_mixed_candidates_split_per_id(leerie):
    calls = []
    b, f = _runners([set()] * 3, [{"a", "b"}, {"a"}, {"a", "b"}], calls)
    v = asyncio.run(leerie._confirm_new_failures(
        ["b", "a"], b, f, _caps(leerie), label="t", read_psi=lambda: 0.0))
    assert v["new"] == ["a"] and v["resource_affected"] == ["b"]


def test_gate_timeout_escalates_to_five_pairs(leerie):
    clock = _Clock()
    calls = []
    b, f = _runners([set()] * 5, [{"x"}] * 5, calls)
    v = asyncio.run(leerie._confirm_new_failures(
        ["x"], b, f, _caps(leerie), label="t", read_psi=lambda: 99.0,
        sleep=clock.sleep, clock=clock))
    assert v["gate"]["timed_out"] is True
    assert v["pairs_required"] == 5 and len(calls) == 10
    assert v["new"] == ["x"]


def test_unreadable_psi_escalates_to_five_pairs(leerie):
    calls = []
    b, f = _runners([set()] * 5, [{"x"}] * 5, calls)
    v = asyncio.run(leerie._confirm_new_failures(
        ["x"], b, f, _caps(leerie), label="t", read_psi=lambda: None))
    assert v["gate"]["readable"] is False and v["pairs_required"] == 5
    assert len(calls) == 10


def test_rerun_without_verdict_makes_resource_affected(leerie):
    calls = []
    b, f = _runners([set(), None, set()], [{"x"}] * 3, calls)
    v = asyncio.run(leerie._confirm_new_failures(
        ["x"], b, f, _caps(leerie), label="t", read_psi=lambda: 0.0))
    assert v["new"] == [] and v["resource_affected"] == ["x"]


def test_raising_rerun_is_no_verdict_not_a_crash(leerie):
    async def boom(timeout):
        raise OSError("fork failed")

    async def final(timeout):
        return {"x"}
    v = asyncio.run(leerie._confirm_new_failures(
        ["x"], boom, final, _caps(leerie), label="t", read_psi=lambda: 0.0))
    assert v["new"] == [] and v["pairs_run"] == 3


def test_budget_bounds_the_confirm(leerie):
    """Each rerun costs 400 s of a 1800 s budget: pairs are cut short, and
    each rerun gets the budget left. Without `seen_failing` a shortfall is
    never `new`, and never "went away" either: `budget_short_unsettled`."""
    clock = _Clock()
    calls = []
    b, f = _runners([set()] * 3, [{"x"}] * 3, calls, clock=clock, cost=400.0)
    v = asyncio.run(leerie._confirm_new_failures(
        ["x"], b, f, _caps(leerie), label="t", read_psi=lambda: 0.0,
        sleep=clock.sleep, clock=clock))
    assert v["budget_exhausted"] is True and v["pairs_run"] == 2
    assert v["new"] == [] and v["budget_short_unsettled"] == ["x"]
    assert v["resource_affected"] == []
    assert [round(c[1]) for c in calls] == [1800, 1400, 1000, 600, 200]


@pytest.mark.parametrize("seen_failing,new", [(True, ["x"]), (False, [])])
def test_a_budget_shortfall_is_never_evidence_a_failure_went_away(
        leerie, seen_failing, new):
    """DESIGN §9 *Which failures are new*, step 4 (review round 5 H1): the
    same cut as above. Every pair that finished agreed — final failed, base
    passed — and the cut pair's base side passed too. A caller that saw the
    failure (`seen_failing`) gets `new` on the two finished pairs, named
    `budget_short`; without it the candidate is `budget_short_unsettled` —
    unknown, never `new` and never "went away" (review round 6 H1)."""
    clock = _Clock()
    calls = []
    b, f = _runners([set()] * 3, [{"x"}] * 3, calls, clock=clock, cost=400.0)
    v = asyncio.run(leerie._confirm_new_failures(
        ["x"], b, f, _caps(leerie), label="t", read_psi=lambda: 0.0,
        sleep=clock.sleep, clock=clock, seen_failing=seen_failing))
    assert v["budget_exhausted"] is True and v["pairs_run"] == 2
    assert v["new"] == new and v["budget_short"] == new
    assert v["budget_short_unsettled"] == ([] if new else ["x"])
    assert v["resource_affected"] == []
    # The wall bound did not move: every rerun still gets the budget left.
    assert [round(c[1]) for c in calls] == [1800, 1400, 1000, 600, 200]


def test_a_rerun_ending_past_the_budget_with_no_verdict_is_not_a_run(leerie):
    """A rerun whose timeout was the budget left, ending with no report, was
    cut by the budget: it is not counted as a pair and not as a no-verdict
    run. Zero finished pairs (the cut pair's base side passed): the
    caller's one observation is not a verdict — under load it is mostly a
    flake (Q9), and before review round 6 H1 this read `new` with zero
    reruns. It is `budget_short_unsettled`: unknown, never `new`, never
    "went away"."""
    clock = _Clock()

    async def base(timeout):
        return set()

    async def final(timeout):
        clock.t += timeout
        return None
    v = asyncio.run(leerie._confirm_new_failures(
        ["x"], base, final, _caps(leerie), label="t", read_psi=lambda: 0.0,
        sleep=clock.sleep, clock=clock, seen_failing=True))
    assert v["pairs_run"] == 0 and v["budget_exhausted"] is True
    assert v["new"] == [] and v["budget_short"] == []
    assert v["budget_short_unsettled"] == ["x"]
    assert v["no_verdict"] == [] and v["resource_affected"] == []


def test_a_shortfall_never_overrides_disagreeing_evidence(leerie):
    """Budget-short, but the evidence that finished disagrees: a base rerun
    failed it (the cut pair's base side), or a rerun ended with no verdict
    before the budget ran out. Neither is `new`."""
    clock = _Clock()
    calls = []
    b, f = _runners([set(), {"y"}], [{"x", "y"}] * 2, calls, clock=clock,
                    cost=700.0)
    v = asyncio.run(leerie._confirm_new_failures(
        ["x", "y"], b, f, _caps(leerie), label="t", read_psi=lambda: 0.0,
        sleep=clock.sleep, clock=clock, seen_failing=True))
    assert v["pairs_run"] == 1 and v["budget_exhausted"] is True
    assert v["new"] == ["x"] and v["resource_affected"] == ["y"]
    clock = _Clock()
    b, f = _runners([None, set()], [{"x"}] * 2, [], clock=clock, cost=700.0)
    v = asyncio.run(leerie._confirm_new_failures(
        ["x"], b, f, _caps(leerie), label="t", read_psi=lambda: 0.0,
        sleep=clock.sleep, clock=clock, seen_failing=True))
    assert v["budget_exhausted"] is True and v["new"] == []
    assert v["resource_affected"] == ["x"]


def test_a_no_verdict_base_rerun_in_the_cut_pair_is_not_a_budget_shortfall(
        leerie):
    """DESIGN §9 *Which failures are new*, step 4: a candidate is decided
    on what finished only when none of its reruns ended with no verdict for
    a reason other than the budget. Here the cut pair's base rerun ends
    early with no report (not a budget cut: the budget is not spent), then
    the budget cuts the final side. Before review round 6 D-L3 that base
    rerun was dropped with the cut pair and the candidate read `new` on the
    one finished pair; it is mixed evidence, `resource_affected`."""
    clock = _Clock()
    bases = iter([(400.0, set()), (100.0, None)])
    finals = iter([(400.0, {"x"}), (1000.0, None)])

    async def base(timeout):
        cost, r = next(bases)
        clock.t += cost
        return r

    async def final(timeout):
        cost, r = next(finals)
        clock.t += min(cost, timeout)
        return r
    v = asyncio.run(leerie._confirm_new_failures(
        ["x"], base, final, _caps(leerie), label="t", read_psi=lambda: 0.0,
        sleep=clock.sleep, clock=clock, seen_failing=True))
    assert v["budget_exhausted"] is True and v["pairs_run"] == 1
    assert v["new"] == [] and v["budget_short"] == []
    assert v["budget_short_unsettled"] == []
    assert v["resource_affected"] == ["x"]


@pytest.mark.parametrize("seen_failing,confirmed", [(True, ["x"]),
                                                    (False, [])])
def test_still_failing_confirms_a_budget_short_vote_only_when_seen(
        leerie, seen_failing, confirmed):
    clock = _Clock()

    async def run_final(timeout):
        cost = 700.0
        if cost >= timeout:
            clock.t += timeout
            return None
        clock.t += cost
        return {"x"}
    v = asyncio.run(leerie._confirm_still_failing(
        ["x"], run_final, _caps(leerie), label="t", read_psi=lambda: 0.0,
        sleep=clock.sleep, clock=clock, seen_failing=seen_failing))
    assert v["reruns_run"] == 2 and v["budget_exhausted"] is True
    assert v["confirmed"] == confirmed and v["budget_short"] == confirmed
    assert v["resource_affected"] == ([] if confirmed else ["x"])


def test_gate_wait_is_not_charged_to_the_confirm_budget(leerie):
    """The gate times out on a loaded host after its full 300 s — exactly
    when the pairs escalate to five. The budget's clock starts after it, so
    the first rerun still gets all 1800 s, and the whole call stays inside
    the gate wait plus the budget (DESIGN §9 *Which failures are new*,
    step 2)."""
    clock = _Clock()
    calls = []
    b, f = _runners([set()] * 5, [{"x"}] * 5, calls, clock=clock,
                    cost=400.0)
    v = asyncio.run(leerie._confirm_new_failures(
        ["x"], b, f, _caps(leerie), label="t", read_psi=lambda: 99.0,
        sleep=clock.sleep, clock=clock))
    gate = v["gate"]["waited_sec"]
    assert v["gate"]["timed_out"] is True and gate >= 300
    assert [round(c[1]) for c in calls] == [1800, 1400, 1000, 600, 200]
    assert v["pairs_run"] == 2 and v["budget_exhausted"] is True
    assert clock.t == gate + 2000


def test_still_failing_does_not_charge_the_gate_wait_either(leerie):
    clock = _Clock()
    calls = []

    async def run_final(timeout):
        calls.append(timeout)
        clock.t += 400.0
        return {"x"}
    v = asyncio.run(leerie._confirm_still_failing(
        ["x"], run_final, _caps(leerie), label="t", read_psi=lambda: 99.0,
        sleep=clock.sleep, clock=clock))
    assert v["gate"]["timed_out"] is True and v["gate"]["waited_sec"] >= 300
    assert [round(t) for t in calls] == [1800, 1400, 1000, 600, 200]
    assert v["reruns_run"] == 5 and v["confirmed"] == ["x"]


def test_no_candidates_runs_nothing(leerie):
    calls = []
    b, f = _runners([], [], calls)
    v = asyncio.run(leerie._confirm_new_failures(
        [], b, f, _caps(leerie), label="t", read_psi=lambda: 0.0))
    assert calls == [] and v["new"] == [] and v["gate"] is None


# --- the report parser -----------------------------------------------------

def test_jest_json_failing_ids(leerie, tmp_path):
    tree = tmp_path / "tree"
    rep = tmp_path / "r.json"
    rep.write_text(json.dumps({"testResults": [
        {"name": str(tree / "src/a.test.ts"), "status": "failed",
         "assertionResults": [
             {"status": "passed", "fullName": "a ok"},
             {"status": "failed", "fullName": "a breaks"}]},
        {"name": str(tree / "src/b.test.ts"), "status": "failed",
         "assertionResults": [], "testExecError": {"message": "boom"}},
    ]}))
    ids = leerie._runner_report_failing_ids("jest-json", rep, str(tree))
    assert ids == {"src/a.test.ts :: a breaks": "src/a.test.ts",
                   "src/b.test.ts :: (file failed to load)": "src/b.test.ts"}


def test_junit_failing_ids_and_file_mapping(leerie, tmp_path):
    tree = tmp_path / "tree"
    (tree / "tests").mkdir(parents=True)
    (tree / "tests" / "test_m.py").write_text("")
    rep = tmp_path / "r.xml"
    rep.write_text(
        '<testsuites><testsuite>'
        '<testcase classname="tests.test_m" name="test_ok"/>'
        '<testcase classname="tests.test_m.TestC" name="test_bad">'
        '<failure message="x"/></testcase>'
        '<testcase classname="tests.test_m" name="test_skip"><skipped/>'
        '</testcase>'
        '<testcase classname="" name="tests.test_broken">'
        '<error message="collection failure"/></testcase>'
        '</testsuite></testsuites>')
    ids = leerie._runner_report_failing_ids("junit", rep, str(tree))
    assert ids == {
        "tests/test_m.py :: tests.test_m.TestC.test_bad": "tests/test_m.py",
        "tests/test_broken.py :: (file failed to load)":
            "tests/test_broken.py"}


def test_report_with_nothing_executed_is_none(leerie, tmp_path):
    rep = tmp_path / "r.json"
    rep.write_text(json.dumps({"testResults": []}))
    assert leerie._runner_report_failing_ids("jest-json", rep,
                                             str(tmp_path)) is None
    assert leerie._runner_report_failing_ids("junit", tmp_path / "no.xml",
                                             str(tmp_path)) is None


# --- end to end against a real pytest suite --------------------------------

def _git(cwd, *a):
    r = subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


_SUITE = """\
import os


def test_pre_existing():
    assert False


def test_stable():
    assert {stable}


def test_flaky():
    marker = os.path.join(os.path.dirname(__file__), "flaky_count")
    n = int(open(marker).read()) if os.path.exists(marker) else 0
    open(marker, "w").write(str(n + 1))
    assert {flaky}
"""


@pytest.fixture
def e3_repo(leerie, tmp_path):
    """Base commit: test_pre_existing fails, test_stable and test_flaky pass.
    Final commit (staging): test_stable regresses for real; test_flaky fails
    on alternate runs only (a resource-affected flake)."""
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / ".gitignore").write_text("flaky_count\n.leerie/\n")
    (repo / "tests" / "test_suite.py").write_text(
        _SUITE.format(stable="True", flaky="True"))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    base = _git(repo, "rev-parse", "HEAD")
    run_id = "fix-001-abcdef"
    leerie_root = repo / ".leerie"
    run_dir = leerie_root / "runs" / run_id
    (run_dir / "logs").mkdir(parents=True)
    (run_dir / "worktrees").mkdir(parents=True)
    st = leerie.State(leerie_root, run_id)
    st.data = {"task": "x", "working_branch": "main", "base_sha": base,
               "conformance": {"_baseline": {"sha": base}}}
    st.repo_root = repo
    st.save()
    # Staging on the run branch in the run's own repository (DESIGN §6
    # *The run's own repository*), as setup-run.sh leaves it.
    leerie._ensure_run_repo(st)
    staging = run_dir / "worktrees" / "staging"
    _git(st.run_repo, "worktree", "add", "-q", "-b", f"leerie/runs/{run_id}",
         str(staging), base)
    (staging / "tests" / "test_suite.py").write_text(
        _SUITE.format(stable="False", flaky="n % 2 == 1"))
    _git(staging, "commit", "-q", "-am", "final")
    return {"repo": repo, "staging": staging, "st": st, "base": base}


CMD = "python3 -m pytest -q -p no:cacheprovider tests"


def test_capture_failing_ids_runs_the_real_suite(leerie, e3_repo):
    st = e3_repo["st"]
    _git(e3_repo["repo"], "checkout", "-q", "--detach", e3_repo["base"])
    got = asyncio.run(leerie._capture_failing_test_ids(
        str(e3_repo["repo"]), CMD, st, _caps(leerie), label="t",
        log_path=st.run_dir / "logs" / "t.log", timeout=120, serial=True))
    assert got is not None and got["kind"] == "junit"
    assert got["ids"] == [
        "tests/test_suite.py :: tests.test_suite.test_pre_existing"]


def test_e3_confirms_the_real_regression_and_types_the_flake(
        leerie, e3_repo, monkeypatch):
    """The failing-ID diff end to end: the pre-existing failure is not a
    candidate, the real regression is confirmed new, the alternating flake is
    resource_affected — and both sides' IDs are persisted."""
    monkeypatch.setattr(leerie, "_host_cpu_psi", lambda path=None: 0.0)
    st, staging = e3_repo["st"], e3_repo["staging"]
    st.data["baseline_failing_ids"] = [
        "tests/test_suite.py :: tests.test_suite.test_pre_existing"]
    # The final measurement, as `_measure_axes` records it: the flake failed
    # on this first run.
    (staging / "flaky_count").write_text("1")
    post = {"tests": {"ran": True, "measured": True, "passed": False,
                      "command": CMD, "summary": ""}}
    assert asyncio.run(leerie._attach_failing_ids(
        post["tests"], str(staging), CMD, st, _caps(leerie),
        log_path=st.run_dir / "logs" / "t.log", label="t"))
    stable = "tests/test_suite.py :: tests.test_suite.test_stable"
    flaky = "tests/test_suite.py :: tests.test_suite.test_flaky"
    assert set(post["tests"]["failing_ids"]) >= {stable}
    # Force the flake into the candidate set (it failed on the measurement).
    post["tests"]["failing_ids"] = sorted(
        set(post["tests"]["failing_ids"]) | {flaky})
    post["tests"]["failing_id_files"][flaky] = "tests/test_suite.py"
    v = asyncio.run(leerie._confirm_final_test_failures(
        st, _caps(leerie), str(staging), post,
        log_path=st.run_dir / "logs" / "t.log", label="final-r0"))
    assert v["candidates"] == sorted([stable, flaky])
    assert v["new"] == [stable]
    assert v["resource_affected"] == [flaky]
    assert v["pairs_run"] == 3
    assert st.data["test_id_diff"]["new"] == [stable]
    assert stable in st.data["final_failing_ids"]
    # The base worktree is disposable.
    assert not leerie._helper_worktree_path(st, "confirm-base").exists()


def test_e3_a_regression_the_budget_cut_short_is_still_new(
        leerie, e3_repo, monkeypatch):
    """The consumer, end to end on a real suite (review round 5 H1, swept to
    the new-failure confirm): one pair finishes — the real regression fails
    on the final tree and passes on the base — and the next final rerun is
    cut by the budget. Before: `resource_affected`, no `new`, and the run
    `done`. The cut is not a run: the regression is `new` on the pair the
    budget allowed, and the run is `unconverged`."""
    monkeypatch.setattr(leerie, "_host_cpu_psi", lambda path=None: 0.0)
    st, staging = e3_repo["st"], e3_repo["staging"]
    stable = "tests/test_suite.py :: tests.test_suite.test_stable"
    st.data["baseline_failing_ids"] = [
        "tests/test_suite.py :: tests.test_suite.test_pre_existing"]
    post = {"tests": {"ran": True, "measured": True, "passed": False,
                      "command": CMD, "summary": "",
                      "failing_ids": st.data["baseline_failing_ids"]
                      + [stable],
                      "failing_id_files": {stable: "tests/test_suite.py"}}}
    real = leerie._capture_failing_test_ids
    labels: list[str] = []

    async def capture(tree, cmd, st_, caps_, *, label, timeout, **kw):
        labels.append(label)
        if label.endswith("-final4"):
            # Cut by the budget: its timeout is the budget left.
            await asyncio.sleep(timeout)
            return None
        return await real(tree, cmd, st_, caps_, label=label,
                          timeout=timeout, **kw)
    monkeypatch.setattr(leerie, "_capture_failing_test_ids", capture)
    v = asyncio.run(leerie._confirm_final_test_failures(
        st, _caps(leerie, confirm_budget_sec=8.0), str(staging), post,
        log_path=st.run_dir / "logs" / "t.log", label="final-r0"))
    assert labels[-1] == "final-r0-final4", labels
    assert v["pairs_run"] == 1 and v["budget_exhausted"] is True
    assert v["new"] == [stable] and v["budget_short"] == [stable]
    st.data["subtask_status"] = {}
    assert leerie._decide_outcome(st, any_commits=True) == "unconverged"
    assert any("newly failing" in r for r in st.data["outcome_reasons"])


def test_e3_unknown_base_ids_leaves_the_axis_verdict_but_runs_discovery(
        leerie, e3_repo, monkeypatch):
    """No diff without the base's IDs — no final ID is a candidate and the
    verdict says `ids_unknown` — but discovery still runs: it needs only the
    coupled files and the base commit (DESIGN §9; barnacle 7cc87f9a's early
    replay never reached it on a red base)."""
    monkeypatch.setattr(leerie, "_host_cpu_psi", lambda path=None: 0.0)
    seen = {}

    async def disc(st_, caps, staging, cmd, base_sha, **kw):
        seen["base_sha"] = base_sha
        return {"failed_ids": [], "fail_counts": {}, "id_files": {},
                "coupled": {}, "repeats_run": 0, "_runs": []}
    monkeypatch.setattr(leerie, "_intermittent_discovery", disc)
    st = e3_repo["st"]
    st.data["baseline_failing_ids"] = None
    post = {"tests": {"ran": True, "measured": True, "passed": False,
                      "command": CMD, "failing_ids": ["x"]}}
    v = asyncio.run(leerie._confirm_final_test_failures(
        st, _caps(leerie), str(e3_repo["staging"]), post,
        log_path=st.run_dir / "logs" / "t.log", label="t"))
    assert seen["base_sha"] == e3_repo["base"]
    assert v["ids_unknown"] is True
    assert v["final_candidates"] == [] and v["new"] == []
    assert st.data["test_id_diff"]["ids_unknown"] is True
    # Unmeasured axis: still no verdict at all.
    post["tests"]["measured"] = False
    st.data.pop("test_id_diff")
    assert asyncio.run(leerie._confirm_final_test_failures(
        st, _caps(leerie), str(e3_repo["staging"]), post,
        log_path=st.run_dir / "logs" / "t.log", label="t")) is None


# --- the consumer: the final conformance loop -------------------------------

def _clean_final():
    ax = {"ran": False, "passed": False, "command": "(none)", "summary": ""}
    return {"subtask_id": "_final", "rules_files_read": [],
            "rule_violations_fixed": [], "rule_violations_residual": [],
            "docs_updates": [], "tests_updates": [], "build": dict(ax),
            "lint": dict(ax), "tests": dict(ax), "summary": "ok",
            "solution_defects": []}


@pytest.mark.parametrize("new,expected_rounds,unknown", [
    ([], 1, False), (["t :: x"], 3, False),
    # IDs unknown on a side (DESIGN §9): no diff, so the red axis stands
    # and the loop goes on, as before the diff existed.
    ([], 3, True)])
def test_final_loop_continues_only_on_a_confirmed_new_failure(
        leerie, e3_repo, monkeypatch, new, expected_rounds, unknown):
    """Executes `_run_final_conformance`: the tests axis is red before and
    after every round on a GREEN base. Today's axis rule would spend every
    round on it; with B* the loop stops after one round when no failing ID
    is confirmed new, and keeps going (with a `tests:` regression) when one
    is."""
    st = e3_repo["st"]
    st.data["conformance"]["_baseline"].update(
        {"axes": {}, "red_axes": []})
    red = {"tests": {"ran": True, "measured": True, "passed": False,
                     "command": CMD, "summary": "1 failed"}}
    calls = []

    async def fake_measure(*a, **k):
        assert k.get("failing_ids_axes") == {"tests"}
        return {ax: dict(v) for ax, v in red.items()}

    async def fake_confirm(st_, caps, staging, post, **k):
        return {"new": list(new), "resource_affected": [],
                "candidates": list(new) or ["t :: flaky"], "pairs_run": 3,
                **({"ids_unknown": True} if unknown else {})}

    async def fake_claude_p(**kw):
        calls.append(kw)
        return _clean_final()

    monkeypatch.setattr(leerie, "_measure_axes", fake_measure)
    monkeypatch.setattr(leerie, "_confirm_final_test_failures", fake_confirm)
    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    caps = _caps(leerie)
    models = {w: "sonnet" for w in leerie.WORKER_TYPES}
    efforts = {w: None for w in leerie.WORKER_TYPES}
    asyncio.run(leerie._run_final_conformance(
        st.run_dir, st, caps, models, efforts))
    assert len(calls) == expected_rounds
    warnings = st.data["conformance"]["_final"]["warnings"]
    has_new = any("passed on the base tree" in w for w in warnings)
    assert has_new is bool(new)


# --- the intermittent-regression rule (DESIGN §9 *An intermittent failure the
# run introduced is evidence, not a verdict*) --------------------------------

_COUPLED_SUITE = """\
import os

from app import core


def _tick(name):
    n = int(open(name).read()) + 1 if os.path.exists(name) else 1
    open(name, "w").write(str(n))
    return n


def test_intermittent():
    # Fails one run in four, and only once the run's change is in place.
    n = _tick("count_a")
    assert not (core.VALUE == 2 and n % 4 == 2), "value leaked"


def test_slow_box():
    n = _tick("count_b")
    if core.VALUE == 2 and n % 4 == 2:
        raise AssertionError("Failed: Timeout >1.0s")
"""

_UNCOUPLED_SUITE = """\
import os


def test_wall_clock():
    name = "count_c"
    n = int(open(name).read()) + 1 if os.path.exists(name) else 1
    open(name, "w").write(str(n))
    assert n % 4 != 2
"""


@pytest.fixture
def coupled_repo(leerie, tmp_path):
    """Base: `app/core.py` VALUE=1. Final: VALUE=2. `tests/test_core.py`
    imports `app.core` directly and fails intermittently on the final tree
    (one assertion, one timeout-shaped message); `tests/test_clock.py`
    imports nothing and is flaky on both sides."""
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    (repo / "app").mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / ".gitignore").write_text("count_*\n.leerie/\n")
    (repo / ".leerie").mkdir()
    (repo / ".leerie" / "config.toml").write_text(
        'test = "python3 -m pytest -q -p no:cacheprovider"\n')
    (repo / "app" / "__init__.py").write_text("")
    (repo / "app" / "core.py").write_text("VALUE = 1\n")
    (repo / "tests" / "test_core.py").write_text(_COUPLED_SUITE)
    (repo / "tests" / "test_clock.py").write_text(_UNCOUPLED_SUITE)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    base = _git(repo, "rev-parse", "HEAD")
    run_id = "fix-002-abcdef"
    leerie_root = repo / ".leerie"
    run_dir = leerie_root / "runs" / run_id
    (run_dir / "logs").mkdir(parents=True)
    (run_dir / "worktrees").mkdir(parents=True)
    st = leerie.State(leerie_root, run_id)
    st.data = {"task": "x", "working_branch": "main", "base_sha": base,
               "conformance": {"_baseline": {"sha": base}},
               "baseline_failing_ids": []}
    st.repo_root = repo
    st.save()
    # Staging on the run branch in the run's own repository (DESIGN §6
    # *The run's own repository*), as setup-run.sh leaves it.
    leerie._ensure_run_repo(st)
    staging = run_dir / "worktrees" / "staging"
    _git(st.run_repo, "worktree", "add", "-q", "-b", f"leerie/runs/{run_id}",
         str(staging), base)
    (staging / "app" / "core.py").write_text("VALUE = 2\n")
    _git(staging, "commit", "-q", "-am", "final")
    return {"repo": repo, "staging": staging, "st": st, "base": base}


COUPLED_CMD = "python3 -m pytest -q -p no:cacheprovider"
INTERMITTENT = "tests/test_core.py :: tests.test_core.test_intermittent"
TIMEOUT_SHAPED = "tests/test_core.py :: tests.test_core.test_slow_box"
CLOCK = "tests/test_clock.py :: tests.test_clock.test_wall_clock"


def test_intermittent_regression_is_found_typed_and_never_counted(
        leerie, coupled_repo, monkeypatch):
    """End to end on a real suite: the final run surfaced nothing. Discovery
    reruns the one import-coupled file, finds both of its intermittent
    failures, and B* confirms neither as new. Only the assertion failure is
    typed `intermittent_regression`: the timeout-shaped one carries a
    resource signature, and the uncoupled flake (forced into the final run)
    imports nothing the run changed."""
    monkeypatch.setattr(leerie, "_host_cpu_psi", lambda path=None: 0.0)
    st, staging = coupled_repo["st"], coupled_repo["staging"]
    post = {"tests": {"ran": True, "measured": True, "passed": False,
                      "command": COUPLED_CMD, "failing_ids": [CLOCK],
                      "failing_id_files": {CLOCK: "tests/test_clock.py"}}}
    v = asyncio.run(leerie._confirm_final_test_failures(
        st, _caps(leerie, confirm_intermittent_repeats=8), str(staging),
        post, log_path=st.run_dir / "logs" / "t.log", label="final-r0"))
    disc = v["discovery"]
    assert disc["files"] == ["tests/test_core.py"]
    assert disc["coupled"] == {"tests/test_core.py": ["app/core.py"]}
    assert disc["repeats_run"] == 8
    assert {INTERMITTENT, TIMEOUT_SHAPED} <= set(disc["failed_ids"])
    assert v["final_candidates"] == [CLOCK]
    assert set(v["candidates"]) == {CLOCK, INTERMITTENT, TIMEOUT_SHAPED}
    # Never counted: B* needs every final rerun to fail.
    assert v["new"] == []
    rows = v["intermittent_regression"]
    assert [r["id"] for r in rows] == [INTERMITTENT]
    (row,) = rows
    assert row["imports_changed"] == ["app/core.py"]
    assert row["base_failed"] == 0 and row["base_runs"] == 3
    assert 1 <= row["final_failed"] < row["final_runs"]
    assert "value leaked" in row["message"]
    assert "_runs" not in st.data["test_id_diff"]["discovery"]
    # Evidence reaches the delivery judge and the PR.
    sec = leerie._intermittent_regression_evidence_section(
        leerie._intermittent_regression_rows(st.data))
    assert INTERMITTENT in sec and TIMEOUT_SHAPED not in sec
    groups = leerie._pr_needs_human(st.data, lambda _s: True)
    (line,) = groups["Intermittent failures the run may have introduced"]
    assert "test_intermittent" in line and "`app/core.py`" in line


def test_discovery_needs_a_scoped_runner(leerie, coupled_repo, monkeypatch):
    """Without a scoped runner a repeat would rerun the whole suite twenty
    times: discovery is skipped, by name."""
    monkeypatch.setattr(leerie, "_acceptance_cmd", lambda *a, **k: None)
    st = coupled_repo["st"]
    d = asyncio.run(leerie._intermittent_discovery(
        st, _caps(leerie), str(coupled_repo["staging"]), COUPLED_CMD,
        coupled_repo["base"], log_path=st.run_dir / "logs" / "t.log",
        label="t"))
    assert d["repeats_run"] == 0
    assert d["skipped"] == "no scoped test runner for the coupled files"


@pytest.mark.parametrize("change,typed", [
    ({}, True),
    ({"base_ids": {"x"}}, False),            # failed on the base run
    ({"per_id": {"base_failed": 1}}, False),  # failed a base rerun
    ({"pairs_run": 2}, False),                # base reruns incomplete
    ({"per_id": {"base_runs": 2}}, False),    # a base rerun had no report
    ({"coupled": {}}, False),                 # imports nothing changed
    ({"messages": {}}, False),                # no signature to read
    ({"messages": {"x": ["Test timed out in 5000ms."]}}, False),
    ({"messages": {"x": ["Worker exited unexpectedly"]}}, False),
    ({"messages": {"x": ["SyntaxError: Nothing to repeat"]}}, True),
    ({"discovery_counts": {}, "final_ids": set()}, False),  # never failed
])
def test_intermittent_typing_conditions(leerie, change, typed):
    """Each condition of the rule, removed one at a time."""
    kw = {"final_ids": set(), "base_ids": set(),
          "discovery_counts": {"x": 2}, "repeats_run": 20,
          "coupled": {"a.test.ts": ["src/a.ts"]},
          "id_files": {"x": "a.test.ts"},
          "messages": {"x": ["AssertionError: expected [] to equal ['$']"]}}
    verdict = {"resource_affected": ["x"], "pairs_run": 3,
               "pairs_required": 3,
               "per_id": {"x": {"final_failed": 0, "base_failed": 0}}}
    for k, v in change.items():
        if k == "pairs_run":
            verdict["pairs_run"] = v
        elif k == "per_id":
            verdict["per_id"]["x"].update(v)
        else:
            kw[k] = v
    rows = leerie._type_intermittent_regressions(verdict, **kw)
    assert bool(rows) is typed
    if typed:
        assert rows[0]["final_failed"] == 2 and rows[0]["final_runs"] == 24


def test_base_checkout_failure_is_recorded_and_reads_unknown(
        leerie, e3_repo, monkeypatch):
    """Review round 4 M2: when the confirm's base worktree cannot be
    created every candidate is `no_verdict`, the diff says
    `base_unavailable`, and the test outcome is unknown — never met."""
    monkeypatch.setattr(leerie, "_host_cpu_psi", lambda path=None: 0.0)

    async def disc(*a, **kw):
        return {"failed_ids": [], "fail_counts": {}, "id_files": {},
                "coupled": {}, "repeats_run": 0, "_runs": []}
    monkeypatch.setattr(leerie, "_intermittent_discovery", disc)

    async def add_fails(*a, **k):
        return subprocess.CompletedProcess(["git"], 128, "", "no space")
    monkeypatch.setattr(leerie, "_worktree_add_detached_async", add_fails)
    st, staging = e3_repo["st"], e3_repo["staging"]
    st.data["baseline_failing_ids"] = []
    stable = "tests/test_suite.py :: tests.test_suite.test_stable"
    post = {"tests": {"ran": True, "measured": True, "passed": False,
                      "command": CMD, "failing_ids": [stable],
                      "failing_id_files": {stable: "tests/test_suite.py"}}}
    v = asyncio.run(leerie._confirm_final_test_failures(
        st, _caps(leerie), str(staging), post,
        log_path=st.run_dir / "logs" / "t.log", label="final-r0"))
    assert v["new"] == [] and v["no_verdict"] == [stable]
    assert v["base_unavailable"] is True
    got = leerie._test_outcome_verdict(
        st, asyncio.run(leerie._worktree_tree_sha(str(staging))))
    assert got["verdict"] == "unknown"
    assert "base checkout" in got["reason"] and stable in got["reason"]


def test_every_measuring_confirm_says_whether_it_saw_the_failure(leerie):
    """Structure, paired with the consumer tests above (the held-out vote,
    the e3 final diff): every B* call that has real runners states
    `seen_failing` — True where `confirmed`/`new` counts a failure, False
    only for the integration overlay, whose `confirmed` routes a failure to
    a test update rather than a code fix (DESIGN §9 *Which failures are
    new*, step 4). A call with no runners
    (`_no_rerun`, `_none`) runs nothing, so it needs none."""
    import ast
    tree = ast.parse(inspect.getsource(leerie))
    calls: list[tuple[str, str, bool]] = []
    for fn in tree.body:
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for call in ast.walk(fn):
            if not (isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Name)
                    and call.func.id in ("_confirm_new_failures",
                                         "_confirm_still_failing")):
                continue
            runners = call.args[1:]
            if all(isinstance(a, ast.Name) and a.id in ("_no_rerun", "_none")
                   for a in runners if not (isinstance(a, ast.Name)
                                            and a.id == "caps")):
                continue
            kw = {k.arg: k.value for k in call.keywords}
            assert "seen_failing" in kw, (fn.name, ast.unparse(call))
            calls.append((fn.name, ast.unparse(call.args[1]),
                          ast.literal_eval(kw["seen_failing"])))
    false = [c for c in calls if not c[2]]
    assert false == [("_integration_test_failures", "_ov", False)], calls
    assert {c[0] for c in calls if c[2]} == {
        "_confirm_final_test_failures", "_ci_confirm_chosen",
        "_acceptance_run_placed", "_falsifier_gates",
        "_redrive_new_failures", "_integration_test_failures"}, calls


# --- review round 6 H1: a budget shortfall is unsettled, not `new` ---------

def test_a_discovery_find_is_never_new_on_a_shortfall(leerie):
    """Discovery candidates are intermittent by construction (DESIGN §9 *An
    intermittent failure the run introduced is evidence*): the same two
    agreeing pairs that make a final-run candidate `new` on a shortfall
    leave a discovery find `budget_short_unsettled`."""
    clock = _Clock()
    b, f = _runners([set()] * 3, [{"x", "d"}] * 3, [], clock=clock,
                    cost=400.0)
    v = asyncio.run(leerie._confirm_new_failures(
        ["x", "d"], b, f, _caps(leerie), label="t", read_psi=lambda: 0.0,
        sleep=clock.sleep, clock=clock, seen_failing=True,
        intermittent=["d"]))
    assert v["pairs_run"] == 2 and v["budget_exhausted"] is True
    assert v["new"] == ["x"] and v["budget_short"] == ["x"]
    assert v["budget_short_unsettled"] == ["d"]


def test_a_zero_budget_confirms_nothing_and_waits_for_nothing(leerie):
    """`confirm_budget_sec = 0` (not an operator setting; DEFAULT_CAPS
    only): no gate wait, no rerun, every candidate unsettled — never `new`
    (round 6 found it typed every candidate `new`)."""
    calls: list = []

    def psi():
        calls.append("psi")
        return 99.0
    b, f = _runners([set()], [{"x"}], calls)
    v = asyncio.run(leerie._confirm_new_failures(
        ["x", "y"], b, f, _caps(leerie, confirm_budget_sec=0), label="t",
        read_psi=psi, seen_failing=True))
    assert calls == [] and v["gate"]["skipped"] == "no budget"
    assert v["new"] == [] and v["budget_short_unsettled"] == ["x", "y"]
    v = asyncio.run(leerie._confirm_still_failing(
        ["x"], f, _caps(leerie, confirm_budget_sec=0), label="t",
        read_psi=psi, seen_failing=True, needs_rerun=["x"]))
    assert calls == [] and v["budget_short_unsettled"] == ["x"]
    assert v["confirmed"] == []


@pytest.mark.parametrize("needs,seen_runs,confirmed,unsettled", [
    # The held-out reading: a file written to fail, seen failing.
    ((), 0, ["x"], []),
    # Seen only as a timeout / a merged-tree failure: needs a rerun.
    (("x",), 0, [], ["x"]),
    (("x",), 1, ["x"], [])])
def test_still_failing_needs_a_finished_rerun_for_a_weak_observation(
        leerie, needs, seen_runs, confirmed, unsettled):
    clock = _Clock()
    n = {"i": 0}

    async def run_final(timeout):
        n["i"] += 1
        if n["i"] > seen_runs:
            clock.t += timeout
            return None
        clock.t += 10.0
        return {"x"}
    v = asyncio.run(leerie._confirm_still_failing(
        ["x"], run_final, _caps(leerie), label="t", read_psi=lambda: 0.0,
        sleep=clock.sleep, clock=clock, seen_failing=True,
        needs_rerun=needs))
    assert v["reruns_run"] == seen_runs and v["budget_exhausted"] is True
    assert v["confirmed"] == confirmed
    assert v["budget_short_unsettled"] == unsettled


def test_confirm_select_ranks_then_sizes(leerie):
    """Pure: the observed-failing file ranks before a discovery-only one,
    then shortest first; taken while (sum + overhead) * reps fits, the
    first always; an unmeasured file costs the overhead alone."""
    sel = leerie._confirm_select(
        ["a", "b", "c", "d"], {"a": 100.0, "b": 10.0, "c": 5.0},
        rank={"c": 1}, reps=6, budget=900.0, overhead=10.0)
    # b (20*6=120), a (120*6=720), d unmeasured (130*6=780); c ranks last
    # (145*6=870 fits).
    assert sel == {"chosen": ["b", "a", "d", "c"], "sized_out": []}
    sel = leerie._confirm_select(
        ["a", "b", "c"], {"a": 100.0, "b": 10.0, "c": 5.0},
        rank={"c": 1}, reps=6, budget=500.0, overhead=10.0)
    # a's pairs do not fit; c, after it in rank, still does: a sized-out
    # file never blocks a cheaper one behind it.
    assert sel == {"chosen": ["b", "c"], "sized_out": ["a"]}
    # Nothing fits: the first is still taken (it may finish a pair).
    sel = leerie._confirm_select(["a"], {"a": 1e6}, rank={}, reps=6,
                                 budget=1.0, overhead=0.0)
    assert sel == {"chosen": ["a"], "sized_out": []}


def _cut_base(leerie, monkeypatch):
    """Every base rerun is cut by the budget (its timeout was the budget
    left, and it ends with no report)."""
    real = leerie._capture_failing_test_ids
    labels: list[str] = []

    async def capture(tree, cmd, st_, caps_, *, label, timeout, **kw):
        labels.append(label)
        if "-base" in label:
            await asyncio.sleep(timeout)
            return None
        return await real(tree, cmd, st_, caps_, label=label,
                          timeout=timeout, **kw)
    monkeypatch.setattr(leerie, "_capture_failing_test_ids", capture)
    return labels


def test_scoped_diff_with_no_base_evidence_is_never_new(
        leerie, e3_repo, monkeypatch):
    """Review round 6 D-H3, caller 1: a scoped final result starts from an
    empty base list, so `test_pre_existing` — failing on the base too — is
    a candidate. The budget cuts the first base rerun: before, it was
    `new` with no base evidence and the run `unconverged`; now it is
    unsettled and the run `done` with the gap listed."""
    monkeypatch.setattr(leerie, "_host_cpu_psi", lambda path=None: 0.0)
    st, staging = e3_repo["st"], e3_repo["staging"]
    pre = "tests/test_suite.py :: tests.test_suite.test_pre_existing"
    st.data["baseline_failing_ids"] = [pre]
    post = {"tests": {"ran": True, "measured": True, "passed": False,
                      "command": CMD, "summary": "",
                      "scoped_replacement": {"replaces": CMD},
                      "failing_ids": [pre],
                      "failing_id_files": {pre: "tests/test_suite.py"}}}
    labels = _cut_base(leerie, monkeypatch)
    v = asyncio.run(leerie._confirm_final_test_failures(
        st, _caps(leerie, confirm_budget_sec=1.0,
                  confirm_intermittent_repeats=0), str(staging), post,
        log_path=st.run_dir / "logs" / "t.log", label="final-r0"))
    assert labels == ["final-r0-base1"], labels
    assert v["pairs_run"] == 0 and v["new"] == []
    assert v["budget_short_unsettled"] == [pre]
    assert v["budget_short_unsettled_files"] == ["tests/test_suite.py"]
    st.data["subtask_status"] = {}
    assert leerie._decide_outcome(st, any_commits=True) == "done"
    gap = [e for e in st.data["needs_human_verification"]
           if e.get("source") == leerie.CONFIRM_BUDGET_GAP_SOURCE]
    assert "could not confirm within the budget: tests/test_suite.py" \
        in gap[0]["detail"]
    got = leerie._test_outcome_verdict(
        st, asyncio.run(leerie._worktree_tree_sha(str(staging))))
    assert got["verdict"] == "unknown"
    assert "could not confirm within the budget: tests/test_suite.py" \
        in got["reason"]


def test_unknown_baseline_discovery_find_is_never_new_on_a_cut(
        leerie, e3_repo, monkeypatch):
    """Review round 6 D-H3, caller 2: base IDs unknown, so the only
    candidates are discovery finds against an empty base list. The budget
    cuts the first base rerun: unsettled, never `new`."""
    monkeypatch.setattr(leerie, "_host_cpu_psi", lambda path=None: 0.0)
    pre = "tests/test_suite.py :: tests.test_suite.test_pre_existing"

    async def disc(*a, **kw):
        return {"failed_ids": [pre], "fail_counts": {pre: 1},
                "id_files": {pre: "tests/test_suite.py"}, "coupled": {},
                "repeats_run": 20, "_runs": []}
    monkeypatch.setattr(leerie, "_intermittent_discovery", disc)
    st, staging = e3_repo["st"], e3_repo["staging"]
    st.data["baseline_failing_ids"] = None
    post = {"tests": {"ran": True, "measured": True, "passed": False,
                      "command": CMD, "failing_ids": [pre]}}
    _cut_base(leerie, monkeypatch)
    v = asyncio.run(leerie._confirm_final_test_failures(
        st, _caps(leerie, confirm_budget_sec=1.0), str(staging), post,
        log_path=st.run_dir / "logs" / "t.log", label="final-r0"))
    assert v["ids_unknown"] is True and v["pairs_run"] == 0
    assert v["new"] == [] and v["budget_short_unsettled"] == [pre]
    st.data["subtask_status"] = {}
    assert leerie._decide_outcome(st, any_commits=True) == "done"


def test_final_diff_sizes_out_a_file_too_slow_for_its_pairs(
        leerie, e3_repo, monkeypatch):
    """Sizing by the recorded per-file durations: a candidate file whose
    pairs cannot fit the budget is not rerun at all and is unsettled; the
    rerun runs only the files that fit."""
    monkeypatch.setattr(leerie, "_host_cpu_psi", lambda path=None: 0.0)
    st, staging = e3_repo["st"], e3_repo["staging"]
    stable = "tests/test_suite.py :: tests.test_suite.test_stable"
    slow = "tests/test_slow.py :: tests.test_slow.test_x"
    st.data["baseline_failing_ids"] = []
    st.data["test_file_durations"] = {"tests/test_suite.py": 0.1,
                                      "tests/test_slow.py": 900.0}
    post = {"tests": {"ran": True, "measured": True, "passed": False,
                      "command": CMD, "summary": "",
                      "failing_ids": [stable, slow],
                      "failing_id_files": {
                          stable: "tests/test_suite.py",
                          slow: "tests/test_slow.py"}}}
    files_run: list = []
    real = leerie._capture_failing_test_ids

    async def capture(tree, cmd, st_, caps_, *, label, timeout, files=None,
                      **kw):
        files_run.append(tuple(files or ()))
        return await real(tree, cmd, st_, caps_, label=label,
                          timeout=timeout, files=files, **kw)
    monkeypatch.setattr(leerie, "_capture_failing_test_ids", capture)
    v = asyncio.run(leerie._confirm_final_test_failures(
        st, _caps(leerie, confirm_budget_sec=600.0,
                  confirm_intermittent_repeats=0), str(staging), post,
        log_path=st.run_dir / "logs" / "t.log", label="final-r0"))
    assert set(files_run) == {("tests/test_suite.py",)}, files_run
    assert v["new"] == [stable] and v["pairs_run"] == 3
    assert v["sized_out"] == [slow] and v["budget_short_unsettled"] == [slow]
    assert set(v["candidates"]) == {stable, slow}


def test_the_as_written_retry_gets_only_what_is_left(leerie, e3_repo,
                                                     monkeypatch):
    """Review round 6 C1: a serial run that wrote no report used to be
    retried as written with the full timeout again, so one B* rerun could
    take twice the budget left. The retry gets what is left; none left,
    no retry."""
    st = e3_repo["st"]
    seen: list[float] = []

    async def run(argv, *, timeout, **kw):
        seen.append(timeout)
        await asyncio.sleep(0.3)
    monkeypatch.setattr(leerie, "_run_streaming", run)
    got = asyncio.run(leerie._capture_failing_test_ids(
        str(e3_repo["staging"]), CMD, st, _caps(leerie), label="t",
        log_path=st.run_dir / "logs" / "t.log", timeout=2.0, serial=True))
    assert got is None and len(seen) == 2, seen
    assert seen[0] == 2.0 and seen[1] <= 2.0 - 0.3 + 0.05, seen
    seen.clear()
    got = asyncio.run(leerie._capture_failing_test_ids(
        str(e3_repo["staging"]), CMD, st, _caps(leerie), label="t",
        log_path=st.run_dir / "logs" / "t.log", timeout=1.1, serial=True))
    assert got is None and seen == [1.1], seen
