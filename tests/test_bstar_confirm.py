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
    assert v["per_id"]["t::x"] == {"final_failed": 3, "base_failed": 0}


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
    """Each rerun costs 400 s of a 1800 s budget: pairs are cut short, every
    candidate is resource_affected, and each rerun gets the budget left."""
    clock = _Clock()
    calls = []
    b, f = _runners([set()] * 3, [{"x"}] * 3, calls, clock=clock, cost=400.0)
    v = asyncio.run(leerie._confirm_new_failures(
        ["x"], b, f, _caps(leerie), label="t", read_psi=lambda: 0.0,
        sleep=clock.sleep, clock=clock))
    assert v["budget_exhausted"] is True and v["pairs_run"] == 2
    assert v["new"] == [] and v["resource_affected"] == ["x"]
    assert [round(c[1]) for c in calls] == [1800, 1400, 1000, 600, 200]


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
    _git(repo, "checkout", "-q", "-b", f"leerie/runs/{run_id}")
    (repo / "tests" / "test_suite.py").write_text(
        _SUITE.format(stable="False", flaky="n % 2 == 1"))
    _git(repo, "commit", "-q", "-am", "final")
    _git(repo, "checkout", "-q", "main")
    leerie_root = repo / ".leerie"
    run_dir = leerie_root / "runs" / run_id
    (run_dir / "logs").mkdir(parents=True)
    (run_dir / "worktrees").mkdir(parents=True)
    staging = run_dir / "worktrees" / "staging"
    _git(repo, "worktree", "add", "-q", str(staging), f"leerie/runs/{run_id}")
    st = leerie.State(leerie_root, run_id)
    st.data = {"task": "x", "working_branch": "main",
               "conformance": {"_baseline": {"sha": base}}}
    st.repo_root = repo
    st.save()
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
    assert not (st.run_dir / "worktrees" / "confirm-base").exists()


def test_e3_unknown_base_ids_leaves_the_axis_verdict(leerie, e3_repo):
    st = e3_repo["st"]
    st.data["baseline_failing_ids"] = None
    post = {"tests": {"ran": True, "measured": True, "passed": False,
                      "command": CMD, "failing_ids": ["x"]}}
    assert asyncio.run(leerie._confirm_final_test_failures(
        st, _caps(leerie), str(e3_repo["staging"]), post,
        log_path=st.run_dir / "logs" / "t.log", label="t")) is None
    assert "test_id_diff" not in st.data


# --- the consumer: the final conformance loop -------------------------------

def _clean_final():
    ax = {"ran": False, "passed": False, "command": "(none)", "summary": ""}
    return {"subtask_id": "_final", "rules_files_read": [],
            "rule_violations_fixed": [], "rule_violations_residual": [],
            "docs_updates": [], "tests_updates": [], "build": dict(ax),
            "lint": dict(ax), "tests": dict(ax), "summary": "ok",
            "solution_defects": []}


@pytest.mark.parametrize("new,expected_rounds", [([], 1), (["t :: x"], 3)])
def test_final_loop_continues_only_on_a_confirmed_new_failure(
        leerie, e3_repo, monkeypatch, new, expected_rounds):
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
                "candidates": list(new) or ["t :: flaky"], "pairs_run": 3}

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
