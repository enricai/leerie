"""Intermittent discovery sized to its budget (DESIGN §9 *An intermittent
failure the run introduced is evidence*, "Sized to the budget").

Barnacle 45fa4253 pass 2: `flow-runner.ts` is imported directly by 301 test
files, one serial repeat took ~15 min, and discovery ran 2 of 20 repeats in
each 30-min budget — a 19% chance per p = 0.1 flake, at the full budget per
final-conformer round. Discovery now reruns only the files whose every
repeat fits, chosen from measured per-file durations."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest


def _caps(leerie, **over):
    return {**leerie.DEFAULT_CAPS, **over}


# --- the per-file duration readers ------------------------------------------

def test_vitest_json_durations_per_file(leerie, tmp_path):
    rep = tmp_path / "r.json"
    rep.write_text(json.dumps({"testResults": [
        {"name": str(tmp_path / "src/a.test.ts"), "startTime": 1000,
         "endTime": 3500, "assertionResults": []},
        # No start/end: the cases' durations are summed.
        {"name": str(tmp_path / "src/b.test.ts"),
         "assertionResults": [{"duration": 250}, {"duration": 750}]}]}))
    got = leerie._runner_report_file_durations("jest-json", rep.read_bytes(),
                                               str(tmp_path))
    assert got == {"src/a.test.ts": 2.5, "src/b.test.ts": 1.0}


def test_junit_durations_sum_per_file(leerie, tmp_path):
    rep = tmp_path / "r.xml"
    rep.write_text(
        '<testsuites><testsuite>'
        '<testcase file="tests/test_a.py" classname="tests.test_a" '
        'name="t1" time="0.5"/>'
        '<testcase file="tests/test_a.py" classname="tests.test_a" '
        'name="t2" time="1.25"/>'
        '<testcase file="tests/test_b.py" classname="tests.test_b" '
        'name="t" time="2"/>'
        '</testsuite></testsuites>')
    assert leerie._runner_report_file_durations(
        "junit", rep.read_bytes(), str(tmp_path)) \
        == {"tests/test_a.py": 1.75, "tests/test_b.py": 2.0}
    assert leerie._runner_report_file_durations(
        "junit", None, str(tmp_path)) == {}


# --- ranking and selection (pure) -------------------------------------------

def test_rank_is_closeness_then_duration(leerie):
    coupled = {"a": ["x"], "b": ["x", "y"], "c": ["x"], "d": ["x"]}
    dur = {"a": 5.0, "b": 50.0, "c": 1.0}
    # b imports two changed files: closest. Then shortest; unmeasured last.
    assert leerie._discovery_rank(list(coupled), coupled, dur) == [
        "b", "c", "a", "d"]


def test_select_takes_whole_repeats_and_records_the_rest(leerie):
    dur = {"a": 1.0, "b": 2.0, "c": 3.0, "d": 100.0}
    sel = leerie._discovery_select(["a", "b", "c", "d", "e"], dur, reps=20,
                                   budget=200.0, overhead=4.0)
    # (1 + 2 + 4) * 20 = 140 fits; + c (3) = 200 fits; d never fits.
    assert sel["chosen"] == ["a", "b", "c"]
    assert sel["planned_sec"] == 200.0
    assert sel["skipped"] == [
        {"file": "d", "reason": "over budget", "duration_sec": 100.0},
        {"file": "e", "reason": "no measured duration"}]


def test_select_runs_nothing_when_one_file_cannot_take_every_repeat(leerie):
    sel = leerie._discovery_select(["a"], {"a": 100.0}, reps=20,
                                   budget=1800.0, overhead=10.0)
    assert sel["chosen"] == [] and sel["planned_sec"] == 0.0
    assert sel["skipped"][0]["reason"] == "over budget"


# --- the discovery itself, on the barnacle shape ------------------------------

class _Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def hub(leerie, tmp_path, monkeypatch):
    """301 test files importing one changed module; each takes 3 s serially
    (one repeat of all of them ~15 min, as measured on barnacle). The fake
    runner advances a fake clock by the files' durations plus 5 s start-up
    and reports a per-file duration for each. `model` changes the runner:
    `per_file` is a file's real serial cost, `reported` what its report
    says (a report excludes the file's import and collection). Like the
    real capture, a serial run over named files records its own cost."""
    staging = tmp_path / "staging"
    files = [f"src/t{i:03d}.test.ts" for i in range(301)]
    for f in files:
        (staging / f).parent.mkdir(parents=True, exist_ok=True)
        (staging / f).write_text("")
    clock = _Clock()
    calls: list[dict] = []
    model = {"startup": 5.0, "per_file": 3.0, "reported": 3.0}

    async def capture(tree, cmd, st, caps, *, label, log_path, timeout,
                      serial=False, files=None, env_extra=None):
        cost = model["startup"] + model["per_file"] * len(files or [])
        calls.append({"label": label, "files": list(files or []),
                      "timeout": timeout})
        if cost > timeout:
            clock.t += timeout
            return None
        clock.t += cost
        durations = {f: model["reported"] for f in files or []}
        if serial and files:
            leerie._record_rerun_cost(st, len(files), cost,
                                      sum(durations.values()))
        return {"ids": [], "files": {}, "kind": "jest-json", "messages": {},
                "durations": durations, "wall_sec": cost}

    async def head(_s):
        return "h"

    async def changed(_s, _b):
        return ["src/flow-runner.ts"]

    async def gate(_caps, **_k):
        return {"waited_sec": 0.0, "timed_out": False, "psi": 0.0,
                "readable": True}
    monkeypatch.setattr(leerie, "_capture_failing_test_ids", capture)
    monkeypatch.setattr(leerie, "_branch_head_sha", head)
    monkeypatch.setattr(leerie, "_changed_files", changed)
    monkeypatch.setattr(leerie, "_confirm_gate", gate)
    monkeypatch.setattr(leerie, "_acceptance_cmd", lambda *a, **k: "x")
    monkeypatch.setattr(leerie, "resolve_test_file_globs", lambda _r: [])
    monkeypatch.setattr(
        leerie, "_coupled_test_files",
        lambda *a, **k: {f: ["src/flow-runner.ts"] for f in files})
    st = leerie.State(tmp_path / "state", "r")
    st.data = {}
    st.repo_root = tmp_path
    return {"st": st, "staging": staging, "files": files, "clock": clock,
            "calls": calls, "model": model}


# Earlier serial reruns of this run, two sizes, matching the fake runner's
# default cost (5 s start-up, no per-file overhead beyond the reported 3 s).
_MEASURED = [{"files": 1, "wall_sec": 8.0, "reported_sec": 3.0},
             {"files": 2, "wall_sec": 11.0, "reported_sec": 6.0}]


def _discover(leerie, hub, **caps):
    return asyncio.run(leerie._intermittent_discovery(
        hub["st"], _caps(leerie, **caps), str(hub["staging"]), "x", "base",
        log_path=hub["st"].run_dir / "t.log", label="t", clock=hub["clock"]))


def test_hub_file_gets_every_repeat_on_fewer_files(leerie, hub):
    """Durations already measured (the final run's report), the rerun cost
    not yet: discovery calibrates two chunks (1 and 2 files, 19 s) to fit
    it, then every chosen file gets all 20 repeats, the whole discovery
    stays inside the budget, and every other coupled file is recorded as
    skipped."""
    hub["st"].data["test_file_durations"] = {f: 3.0 for f in hub["files"]}
    d = _discover(leerie, hub)
    reps = leerie.DEFAULT_CAPS["confirm_intermittent_repeats"]
    budget = leerie.DEFAULT_CAPS["confirm_budget_sec"]
    assert d["repeats_run"] == reps and not d["budget_exhausted"]
    repeats = [c for c in hub["calls"] if "-repeat" in c["label"]]
    assert len(repeats) == reps
    assert all(c["files"] == d["files"] for c in repeats)
    # Fitted: 5 s start-up, 0 s per file. (n*3 + 5) * 20 <= 1781  ->  n = 28.
    assert d["selection"]["cost"]["startup_sec"] == 5.0
    assert d["selection"]["cost"]["per_file_sec"] == 0.0
    assert len(d["files"]) == 28
    assert len(d["skipped_files"]) == 301 - 28
    assert {x["reason"] for x in d["skipped_files"]} == {"over budget"}
    assert hub["clock"].t <= budget
    assert d["selection"]["calibration"]["runs"] == 2


def test_unmeasured_hub_is_calibrated_within_one_repeats_share(leerie, hub):
    """No duration known: calibration measures the first files in doubling
    chunks inside budget/R (90 s), and the measured ones then get all 20
    repeats. The whole discovery still fits the budget."""
    d = _discover(leerie, hub)
    calib = [c for c in hub["calls"] if "-calibrate" in c["label"]]
    assert [len(c["files"]) for c in calib][:4] == [1, 2, 4, 8]
    measured = d["selection"]["calibration"]["measured"]
    assert measured == 15 and len(calib) == 5   # the 16-file chunk times out
    assert d["repeats_run"] == 20 and len(d["files"]) > 0
    assert set(d["files"]) <= set(hub["files"][:measured])
    reasons = {x["reason"] for x in d["skipped_files"]}
    assert "no measured duration" in reasons
    assert hub["clock"].t <= leerie.DEFAULT_CAPS["confirm_budget_sec"]


def test_one_file_over_budget_runs_no_partial_repeats(leerie, hub):
    hub["st"].data["test_file_durations"] = {f: 600.0 for f in hub["files"]}
    hub["st"].data["test_run_overhead"] = list(_MEASURED)
    d = _discover(leerie, hub)
    assert d["skipped"] == "over budget"
    assert d["repeats_run"] == 0 and d["files"] == []
    assert not [c for c in hub["calls"] if "-repeat" in c["label"]]
    assert hub["clock"].t == 0.0


def test_gate_wait_does_not_starve_discovery(leerie, hub, monkeypatch):
    """A gate that waits its full 300 s on a loaded host: discovery still
    sizes to the whole 1800 s budget and chooses the same 28 files it does
    with no wait, and the call ends inside gate cap + budget (DESIGN §9
    *Which failures are new*, step 2)."""
    async def slow_gate(_caps, **_k):
        hub["clock"].t += 300.0
        return {"waited_sec": 300.0, "timed_out": True, "psi": 99.0,
                "readable": True}
    monkeypatch.setattr(leerie, "_confirm_gate", slow_gate)
    hub["st"].data["test_file_durations"] = {f: 3.0 for f in hub["files"]}
    hub["st"].data["test_run_overhead"] = list(_MEASURED)
    d = _discover(leerie, hub)
    budget = leerie.DEFAULT_CAPS["confirm_budget_sec"]
    assert d["selection"]["budget_sec"] == budget
    assert len(d["files"]) == 28 and d["repeats_run"] == 20
    assert not d["budget_exhausted"]
    assert 300.0 < hub["clock"].t <= 300.0 + budget


def test_a_repeat_the_budget_cut_is_not_counted(leerie, hub):
    """Durations that understate the real cost (2.5 s known, 3 s real):
    the chosen 34 files' repeats take 107 s each, so the 17th is cut by the
    budget. It wrote no report, so it is not a repeat run — counted, it
    would inflate the "failed x of n final runs" a typed row reports — and
    it ends discovery (DESIGN §9 *Sized to the budget*)."""
    hub["st"].data["test_file_durations"] = {f: 2.5 for f in hub["files"]}
    hub["st"].data["test_run_overhead"] = list(_MEASURED)
    d = _discover(leerie, hub)
    assert len(d["files"]) == 34
    repeats = [c for c in hub["calls"] if "-repeat" in c["label"]]
    assert len(repeats) == 17
    assert d["repeats_run"] == 16 and d["budget_exhausted"] is True
    assert hub["clock"].t == leerie.DEFAULT_CAPS["confirm_budget_sec"]


def test_capture_records_durations_run_wide(leerie, tmp_path):
    st = leerie.State(tmp_path / "state", "r")
    st.data = {"test_file_durations": {"a": 1.0, "b": 2.0}}
    leerie._record_test_durations(st, {"b": 5.0, "c": 3.0})
    assert st.data["test_file_durations"] == {"a": 1.0, "b": 5.0, "c": 3.0}
    reloaded = json.loads(Path(st.run_dir / "state.json").read_text())
    assert reloaded["test_file_durations"]["b"] == 5.0


# --- the rerun cost is measured, not assumed (live a1f5, 0.43.0) -------------

def test_known_durations_that_omit_imports_still_fit_every_repeat(leerie,
                                                                  hub):
    """The live shape: every duration known from a report that leaves out
    each file's import (1 s reported, 3 s real), so the old rule calibrated
    nothing and planned with an assumed 10 s start-up — 80 files whose
    repeats each take 243 s, 7 of 20 run. Measured, the start-up (3 s) and
    the per-file overhead (2 s) size the set so all 20 repeats finish."""
    hub["model"].update(startup=3.0, per_file=3.0, reported=1.0)
    hub["st"].data["test_file_durations"] = {f: 1.0 for f in hub["files"]}
    d = _discover(leerie, hub)
    cost = d["selection"]["cost"]
    assert (cost["startup_sec"], cost["per_file_sec"]) == (3.0, 2.0)
    assert d["repeats_run"] == 20 and d["budget_exhausted"] is False
    # (n * (1 + 2) + 3) * 20 <= 1800 - 15  ->  n = 28.
    assert len(d["files"]) == 28
    assert hub["clock"].t <= leerie.DEFAULT_CAPS["confirm_budget_sec"]


def test_no_measured_cost_plans_nothing(leerie, hub):
    """A first chunk that cannot finish inside one repeat's share leaves no
    sample: discovery plans nothing rather than plan from an assumption."""
    hub["model"].update(per_file=200.0)
    hub["st"].data["test_file_durations"] = {f: 1.0 for f in hub["files"]}
    d = _discover(leerie, hub)
    assert d["skipped"] == "overhead not measured"
    assert d["repeats_run"] == 0 and d["files"] == []
    assert {x["reason"] for x in d["skipped_files"]} == {
        "overhead not measured"}


@pytest.mark.parametrize("samples, want", [
    ([], None),
    ([{"files": 4, "wall_sec": 20.0, "reported_sec": 4.0}], (0.0, 4.0)),
    ([{"files": 1, "wall_sec": 6.0, "reported_sec": 1.0},
      {"files": 3, "wall_sec": 12.0, "reported_sec": 3.0}], (3.0, 2.0)),
    # Overhead falling with size (noise): all of it start-up.
    ([{"files": 1, "wall_sec": 10.0, "reported_sec": 1.0},
      {"files": 3, "wall_sec": 8.0, "reported_sec": 3.0}], (7.0, 0.0)),
])
def test_rerun_cost_fit(leerie, samples, want):
    got = leerie._rerun_cost(samples)
    assert (None if got is None
            else (got["startup_sec"], got["per_file_sec"])) == want


def test_confirm_select_charges_each_unit_its_overhead(leerie):
    """B* sizing reads the same cost: with 2 s per file on top of 1 s
    reported, one base/final pair of three units costs (9 + 3) * 2."""
    sel = leerie._confirm_select(["a", "b", "c"], {"a": 1, "b": 1, "c": 1},
                                 rank={}, reps=2, budget=20.0, overhead=3.0,
                                 per_file=2.0)
    assert sel == {"chosen": ["a", "b"], "sized_out": ["c"]}
