"""A held case that fails again is a failure, not a flake (DESIGN §8).

Replay shape (barnacle 7cc87f9a, early replay, pass 1): a set's counted case
failed at the own start by construction; on the shipped tree it failed about
half the runs on an `AssertionError` (a random sample had only `-`/`_` as
its special). The first run failed, confirm 1 failed, confirms 2 and 3
passed: B* typed the file `resource_affected`, the set went unmeasured, the
gate logged "held-out sets pass" and the run was `done`. About 91% of first
passes shipped that defect with a green verdict.

Real git repo and real pytest throughout. The defect file is a seeded-random
test: run n draws from `random.Random(BASE + n)` (n read from a counter
file), so every outcome sequence is deterministic and computed here with
the same generator.
"""
from __future__ import annotations

import asyncio
import random
from pathlib import Path

import pytest

from tests.conftest import run_git_cwd_first_stdout as _git

REL = "acc/test_defect_1.py"
CONTROL = "acc/test_control_1.py"


def _fails(base: int, n: int) -> bool:
    return random.Random(base + n).random() < 0.5


def _base_for(pattern: str) -> int:
    """The first BASE whose run outcomes start with `pattern` (`F` fails,
    `P` passes) — so the shape under test is pinned, not hoped for."""
    for base in range(100000):
        if all(_fails(base, n) == (c == "F") for n, c in enumerate(pattern)):
            return base
    raise AssertionError(pattern)


def _flaky(marker: Path, base: int, raise_with: str = "") -> str:
    fail = (f"raise RuntimeError({raise_with!r})" if raise_with else
            "assert False, 'sample BqHxC4-H-k2MnOl_yriqMPDl has only - or _'")
    return ("import pathlib, random\n\n"
            "def test_every_sample_has_a_conventional_special():\n"
            f"    m = pathlib.Path({str(marker)!r})\n"
            "    n = int(m.read_text()) if m.exists() else 0\n"
            "    m.write_text(str(n + 1))\n"
            f"    if random.Random({base} + n).random() < 0.5:\n"
            f"        {fail}\n")


def _repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@x")
    _git(repo, "config", "user.name", "t")
    (repo / "conftest.py").write_text("")
    (repo / ".leerie").mkdir()
    (repo / ".leerie" / "config.toml").write_text(
        'test = "python3 -m pytest -q -p no:cacheprovider"\n')
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    return repo, _git(repo, "rev-parse", "HEAD")


def _st(leerie, tmp_path, repo, head):
    root = tmp_path / "state"
    run_dir = root / "runs" / "run-a"
    run_dir.mkdir(parents=True)
    (run_dir / "logs").mkdir()
    st = leerie.State(root, "run-a", repo_root=repo)
    st.data = {"task": "fix the report", "verbosity": "quiet",
               "categories": ["bug-fixing"],
               "repo_state_before_planning": {"head": head},
               "worker_count": 0}
    st.save()
    return st


def _caps(leerie, **kw):
    caps = dict(leerie.DEFAULT_CAPS)
    caps.update(worker_timeout_sec=120, confirm_pairs=3,
                confirm_pairs_escalated=3, confirm_gate_wait_sec=0)
    caps.update(kw)
    return caps


@pytest.fixture(autouse=True)
def _no_pressure_wait(leerie, monkeypatch):
    async def gate(caps, **_kw):
        return {"waited_sec": 0.0, "timed_out": False, "psi": 0.0,
                "readable": True}
    monkeypatch.setattr(leerie, "_confirm_gate", gate)


def _held_id(leerie, st, repo, text: str) -> str:
    """The runner's case ID for the defect case, read the way validation
    reads it at the own start (`observed["failing_cases"]`)."""
    (repo / "acc").mkdir(exist_ok=True)
    (repo / REL).write_text(text)
    obs: dict = {}
    try:
        v = asyncio.run(leerie._run_acceptance_file(
            st, _caps(leerie), str(repo), REL,
            st.run_dir / "logs" / "own-start.log", "own-start",
            validating=True, observed=obs))
    finally:
        (repo / REL).unlink()
    assert v is False and len(obs["failing_cases"]) == 1
    return obs["failing_cases"][0]


def _set(leerie, st, repo, marker, base, *, raise_with="", bounded=None):
    # Validation at the own start: a run of the defect case that fails.
    cid = _held_id(leerie, st, repo, _flaky(marker.with_suffix(".own"),
                                             _base_for("F"), raise_with))
    d = st.run_dir / "acceptance" / "set-1"
    (d / "acc").mkdir(parents=True)
    (d / REL).write_text(_flaky(marker, base, raise_with))
    (d / CONTROL).write_text("def test_control():\n    assert True\n")
    s_ = {"index": 1, "valid": True, "dir": str(d),
          "defect_files": [REL], "control_files": [CONTROL],
          "cases": {REL: ["every sample has a conventional special"],
                    CONTROL: ["control"]},
          "base_failing_cases": {REL: [cid]}}
    if bounded is not None:
        s_["scope"] = {"bounded": True,
                       "counted_cases": {REL: [cid] if bounded else []}}
    return s_, cid


def _evaluate(leerie, st, repo, sets, caps, label="gate-initial"):
    (res,) = asyncio.run(leerie._evaluate_acceptance_sets(
        st, caps, str(repo), sets, label))
    return res


def _outcome(leerie, st, sets, res):
    st.data["acceptance"] = {"sets": sets, "gate": {"final": [res]}}
    if not res.get("passed") and not res.get("unmeasured"):
        st.data["acceptance"]["gate"]["residual"] = (
            leerie._acceptance_residual_of([res], sets))
    return leerie._decide_outcome(st, any_commits=True)


# --- the replay ------------------------------------------------------------

def test_the_replay_shape_is_a_failure_not_unmeasured(leerie, tmp_path,
                                                      capsys):
    """First run fails on an assertion, confirm 1 fails, confirms 2 and 3
    pass — the exact sequence of `acceptance-gate-initial.log` set 1. Before:
    `unmeasured: true` and outcome `done`. Now: failing, no vote, and the
    run is `unconverged`."""
    repo, own = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, own)
    marker = tmp_path / "runs"
    base = _base_for("FFPP")
    s_, cid = _set(leerie, st, repo, marker, base)
    res = _evaluate(leerie, st, repo, [s_], _caps(leerie))
    assert res["passed"] is False and res["unmeasured"] is False
    assert res["failing_files"] == [REL]
    assert res["failing_cases"] == {REL: [cid]}
    assert "resource_affected_files" not in res
    # A held case failing on an assertion is never voted on: one run.
    assert marker.read_text() == "1"
    assert "B* acceptance" not in capsys.readouterr().out
    assert leerie._acceptance_any_fails([res]) is True
    assert _outcome(leerie, st, [s_], res) == "unconverged"


@pytest.mark.parametrize("pattern", ["PF", "PPPF", "PPPPPPF"])
def test_a_first_run_that_passes_is_found_by_discovery(leerie, tmp_path,
                                                       pattern):
    """The 45% of first passes that never saw the case fail: discovery's
    reruns find the first assertion failure and stop there."""
    repo, own = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, own)
    marker = tmp_path / "runs"
    s_, cid = _set(leerie, st, repo, marker, _base_for(pattern))
    res = _evaluate(leerie, st, repo, [s_], _caps(leerie))
    assert res["failing_files"] == [REL] and res["passed"] is False
    assert res["failing_cases"] == {REL: [cid]}
    assert res["discovery"]["found"] == [REL]
    # Stopped at the first failure: exactly len(pattern) runs of the file.
    assert marker.read_text() == str(len(pattern))
    assert _outcome(leerie, st, [s_], res) == "unconverged"


@pytest.mark.parametrize("seed_pattern", [
    "FFPP", "FPPP", "PFFF", "PPFP", "FFFF", "PPPPPPPPF"])
def test_a_seeded_random_half_failing_case_never_passes(leerie, tmp_path,
                                                        seed_pattern):
    repo, own = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, own)
    marker = tmp_path / "runs"
    s_, _cid = _set(leerie, st, repo, marker, _base_for(seed_pattern))
    res = _evaluate(leerie, st, repo, [s_], _caps(leerie))
    assert res["passed"] is False and res["unmeasured"] is False
    assert leerie._acceptance_work_fails([res], [s_]) is True


def test_discovery_is_capped_at_ten(leerie, tmp_path):
    repo, own = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, own)
    marker = tmp_path / "runs"
    s_, _cid = _set(leerie, st, repo, marker, _base_for("P" * 11))
    res = _evaluate(leerie, st, repo, [s_],
                    _caps(leerie, acceptance_intermittent_repeats=50))
    assert res["passed"] is True
    # Ten clean reruns leave a record that says so.
    assert res["discovery"]["repeats_run"] == 10
    assert res["discovery"]["found"] == []
    assert res["discovery"]["incomplete"] is False
    assert marker.read_text() == "11"          # the first run + 10
    assert not any("discovery" in line.lower()
                   for line in leerie._pr_advisory(
                       {"acceptance": {"gate": {"final": [res]}}}))


def test_discovery_off_keeps_a_passing_first_run(leerie, tmp_path, capsys):
    """Discovery the operator turned off is recorded `disabled` — not
    `discovery_incomplete`, not logged as such, and not listed in the PR's
    advisory section: nothing stopped short of what was asked (review
    round 5 L4)."""
    repo, own = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, own)
    marker = tmp_path / "runs"
    s_, _cid = _set(leerie, st, repo, marker, _base_for("PF"))
    res = _evaluate(leerie, st, repo, [s_],
                    _caps(leerie, acceptance_intermittent_repeats=0))
    assert res["passed"] is True and marker.read_text() == "1"
    d = res["discovery"]
    assert d["disabled"] is True and d["reason"] == "disabled"
    assert d["incomplete"] is False
    assert d["repeats_run"] == 0 and d["repeats_required"] == 0
    assert "discovery_incomplete" not in capsys.readouterr().out
    state = {"acceptance": {"gate": {"final": [res]}}}
    assert not [x for x in leerie._pr_advisory(state)
                if "Held-out discovery" in x]
    assert "Held-out discovery" not in leerie._format_pr_end_state_section(
        state)


def test_a_pre_disabled_record_of_repeats_off_is_not_listed(leerie):
    """A `state.json` saved before the `disabled` record carries
    `incomplete: true, reason: "repeats_disabled"` for repeats of 0. A
    resumed run finalizing over it must not list that set as stopped
    early; a set the budget really cut, beside it, still is."""
    old = {"index": 0, "discovery": {
        "files": ["tests/old.test.ts"], "repeats_required": 0,
        "repeats_run": 0, "budget_exhausted": False, "found": [],
        "incomplete": True, "reason": "repeats_disabled"}}
    cut = {"index": 1, "discovery": {
        "files": ["tests/cut.test.ts"], "repeats_required": 10,
        "repeats_run": 2, "budget_exhausted": True, "found": [],
        "incomplete": True, "reason": "budget_exhausted"}}
    state = {"acceptance": {"gate": {"final": [old, cut]}}}
    lines = leerie._pr_advisory(state)
    assert lines[0] == "**Held-out discovery that did not finish**"
    listed = [x for x in lines[1:] if x.startswith("- set ")]
    assert len(listed) == 1
    assert "cut.test.ts" in listed[0] and "rerun 2 of 10" in listed[0]
    assert "budget ran out" in listed[0]
    body = leerie._format_pr_end_state_section(state)
    assert "cut.test.ts" in body and "old.test.ts" not in body
    assert "stopped early" not in body
    # Only the old record: nothing to list at all.
    only_old = {"acceptance": {"gate": {"final": [old]}}}
    assert "Held-out discovery" not in leerie._format_pr_end_state_section(
        only_old)


def test_starved_discovery_is_recorded_and_reaches_the_pr(leerie, tmp_path,
                                                          capsys):
    """Review round 4 L1: discovery whose budget ran out before any rerun
    left no trace, so the set read exactly like one rerun ten times clean.
    The pattern would be found on the second run (PF), so a record saying
    complete here would be false."""
    repo, own = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, own)
    marker = tmp_path / "runs"
    s_, _cid = _set(leerie, st, repo, marker, _base_for("PF"))
    res = _evaluate(leerie, st, repo, [s_],
                    _caps(leerie, confirm_budget_sec=0))
    assert res["passed"] is True and marker.read_text() == "1"
    d = res["discovery"]
    assert d["budget_exhausted"] is True and d["incomplete"] is True
    assert d["reason"] == "budget_exhausted"
    assert d["repeats_run"] == 0 and d["repeats_required"] == 10
    assert "discovery_incomplete — 0 of 10" in capsys.readouterr().out
    lines = leerie._pr_advisory({"acceptance": {"gate": {"final": [res]}}})
    assert lines[0] == "**Held-out discovery that did not finish**"
    assert REL in lines[1] and "rerun 0 of 10" in lines[1]
    assert "budget ran out" in lines[1]
    body = leerie._format_pr_end_state_section(
        {"acceptance": {"gate": {"final": [res]}}})
    assert "Held-out discovery that did not finish" in body.split(
        "## Advisory", 1)[1]


# --- rule 3: resource signatures only --------------------------------------

def test_a_held_case_failing_only_on_timeouts_is_intermittent(leerie,
                                                              tmp_path):
    """Same sequence, but every failure carries a resource signature: the
    vote types it resource_affected, and the held case still failed — a
    failing set typed intermittent, never unmeasured, and a typed residual."""
    repo, own = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, own)
    marker = tmp_path / "runs"
    s_, cid = _set(leerie, st, repo, marker, _base_for("FFPP"),
                   raise_with="Test timed out in 5000ms")
    res = _evaluate(leerie, st, repo, [s_],
                    _caps(leerie, acceptance_intermittent_repeats=0))
    assert res["passed"] is False and res["unmeasured"] is False
    assert res["intermittent_cases"] == {REL: [cid]}
    assert res["failing_files"] == [REL]
    assert "resource_affected_files" not in res
    residual = leerie._acceptance_residual_of([res], [s_])
    assert residual["intermittent"] == [1]
    assert _outcome(leerie, st, [s_], res) == "unconverged"


def test_a_held_case_failing_on_every_timeout_rerun_is_confirmed(leerie,
                                                                 tmp_path):
    repo, own = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, own)
    marker = tmp_path / "runs"
    s_, _cid = _set(leerie, st, repo, marker, _base_for("FFFF"),
                    raise_with="Test timed out in 5000ms")
    res = _evaluate(leerie, st, repo, [s_], _caps(leerie))
    assert res["failing_files"] == [REL]
    assert "intermittent_cases" not in res


def test_a_variant_case_keeps_the_symmetric_vote(leerie, tmp_path):
    """A bounded set whose failing case is not counted: no held case, so the
    old vote stands — fails once, passes the reruns, no evidence."""
    repo, own = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, own)
    marker = tmp_path / "runs"
    s_, _cid = _set(leerie, st, repo, marker, _base_for("FPPP"),
                    bounded=False)
    res = _evaluate(leerie, st, repo, [s_], _caps(leerie))
    assert res["unmeasured"] is True and res["failing_files"] == []
    assert res["resource_affected_files"] == [REL]
    assert "discovery" not in res


def test_unconfirmed_measurement_runs_no_vote_and_no_discovery(leerie,
                                                               tmp_path):
    repo, own = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, own)
    marker = tmp_path / "runs"
    s_, _cid = _set(leerie, st, repo, marker, _base_for("PF"))
    (res,) = asyncio.run(leerie._evaluate_acceptance_sets(
        st, _caps(leerie), str(repo), [s_], "report-base", confirm=False))
    assert res["passed"] is True and marker.read_text() == "1"


# --- the signals, pure -----------------------------------------------------

def test_held_signals_read_the_runner_message(leerie):
    s_ = {"defect_files": ["a"], "control_files": ["c"],
          "base_failing_cases": {"a": ["a::x", "a::y"]}}
    obs = {"failing_cases": ["a::x", "a::y", "a::z"],
           "failing_messages": {"a::x": "AssertionError: expected 1 to be +0",
                                "a::y": "Error: Test timed out in 240000ms"}}
    assert leerie._acceptance_held_signals(s_, "a", obs) == (
        {"a::x"}, {"a::x", "a::y"})
    # No message: seen, never asserted.
    assert leerie._acceptance_held_signals(
        s_, "a", {"failing_cases": ["a::x"]}) == (set(), {"a::x"})
    # A control holds no case.
    assert leerie._acceptance_held_cases(s_, "c") == set()
    # Bounded: only counted cases are held.
    s_["scope"] = {"bounded": True, "counted_cases": {"a": ["a::y"]}}
    assert leerie._acceptance_held_cases(s_, "a") == {"a::y"}


def test_the_gate_never_says_pass_with_a_set_unmeasured(leerie, tmp_path,
                                                        monkeypatch, capsys):
    repo, own = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, own)
    staging = st.run_dir / "worktrees" / "staging"
    _git(repo, "worktree", "add", "-q", "-b", f"leerie/runs/{st.run_id}",
         str(staging))
    st.data["acceptance"] = {"sets": [{"index": 1}, {"index": 2}]}

    async def fake_eval(st_, caps, tree, sets, label, **kw):
        return [{"index": 1, "passed": True, "unmeasured": False,
                 "failing_files": []},
                {"index": 2, "passed": False, "unmeasured": True,
                 "failing_files": []}]
    monkeypatch.setattr(leerie, "_evaluate_acceptance_sets", fake_eval)
    asyncio.run(leerie._run_acceptance_gate_inner(
        st.run_dir, st, _caps(leerie), {}, {}))
    out = capsys.readouterr().out
    assert "held-out sets pass" not in out
    assert "1 of 2 held-out set(s) pass, 1 unmeasured" in out


# --- the B* budget: its clock starts after the gate, and bounds each run ---
# (DESIGN §9 *Which failures are new*, step 2)

class _Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def _discover_with(leerie, tmp_path, monkeypatch, *, cost, gate_wait,
                   **caps):
    clock = _Clock()
    timeouts: list[float] = []

    async def gate(_caps, **_kw):
        clock.t += gate_wait
        return {"waited_sec": gate_wait, "timed_out": True, "psi": 99.0,
                "readable": True}

    async def run(st, caps_, tree, rel, log_path, label, *, cases_out=None,
                  **_kw):
        t = caps_["worker_timeout_sec"]
        timeouts.append(t)
        if cost > t:
            clock.t += t
            cases_out["resource"] = True
            return False
        clock.t += cost
        return True
    monkeypatch.setattr(leerie, "_confirm_gate", gate)
    monkeypatch.setattr(leerie, "_run_acceptance_file", run)
    st = leerie.State(tmp_path / "state", "r")
    d = asyncio.run(leerie._acceptance_discover(
        st, {**_caps(leerie, worker_timeout_sec=5400), **caps}, tmp_path,
        [REL], tmp_path / "t.log", "t", None, lambda _r, _o: False,
        index=1, clock=clock))
    return d, timeouts, clock


def test_discovery_gets_its_whole_budget_after_a_gate_wait(
        leerie, tmp_path, monkeypatch):
    """Ten rounds of 170 s fit 1800 s. A 300 s gate wait charged to the
    budget would have cut the tenth round and marked discovery incomplete."""
    d, timeouts, clock = _discover_with(leerie, tmp_path, monkeypatch,
                                        cost=170.0, gate_wait=300.0)
    assert d["repeats_run"] == 10 and d["incomplete"] is False
    assert d["budget_exhausted"] is False
    assert [round(t) for t in timeouts] == [1800 - 170 * i
                                            for i in range(10)]
    assert clock.t == 300.0 + 1700.0


def test_a_discovery_run_the_budget_cut_is_not_a_round(leerie, tmp_path,
                                                       monkeypatch):
    """Each rerun's timeout is what is left of the budget, never the 90 min
    worker timeout; one the budget cut short leaves discovery incomplete,
    its round uncounted."""
    d, timeouts, clock = _discover_with(leerie, tmp_path, monkeypatch,
                                        cost=170.0, gate_wait=0.0,
                                        confirm_budget_sec=250)
    assert [round(t) for t in timeouts] == [250, 80]
    assert d["repeats_run"] == 1 and d["budget_exhausted"] is True
    assert d["incomplete"] is True and d["reason"] == "budget_exhausted"
    assert clock.t == 250.0


def _vote_set(tmp_path):
    d = tmp_path / "set-1"
    (d / "acc").mkdir(parents=True)
    (d / REL).write_text("")
    (d / CONTROL).write_text("")
    wt = tmp_path / "wt"
    wt.mkdir()
    s_ = {"index": 1, "valid": True, "dir": str(d),
          "defect_files": [REL], "control_files": [CONTROL],
          "cases": {REL: ["a"], CONTROL: ["c"]},
          "base_failing_cases": {REL: ["acc/test_defect_1.py::test_a"]}}
    return s_, wt


def test_a_vote_rerun_the_budget_cut_is_not_a_run_and_never_a_pass(
        leerie, tmp_path, monkeypatch):
    """A vote rerun runs under the budget it is given; one the budget cuts
    short is not a run — and never evidence the failure went away. The
    first run failed and no rerun finished to say otherwise: the file stays
    failing, named as budget-short, and the run is not `done` (review round
    5 H1; DESIGN §9 *Which failures are new*, step 4)."""
    st = leerie.State(tmp_path / "state", "r")
    st.data = {"task": "t", "categories": ["bug-fixing"]}
    s_, wt = _vote_set(tmp_path)
    confirm_timeouts: list[float] = []

    async def run(st_, caps_, tree, rel, log_path, label, *, cases_out=None,
                  **_kw):
        if rel == CONTROL:
            return True
        if "-confirm" in label:
            t = caps_["worker_timeout_sec"]
            confirm_timeouts.append(t)
            await asyncio.sleep(t + 0.05)
            cases_out["resource"] = True
            return False
        return False                 # the first run: fails, names no case
    monkeypatch.setattr(leerie, "_run_acceptance_file", run)
    (res,) = asyncio.run(leerie._acceptance_run_placed(
        st, _caps(leerie, confirm_pairs=1, confirm_pairs_escalated=1,
                  confirm_budget_sec=0.3, acceptance_intermittent_repeats=0),
        wt, [s_], "t"))
    # Still bounded by the budget: the wall bound did not move.
    assert len(confirm_timeouts) == 1 and 0 < confirm_timeouts[0] <= 0.3
    assert res["failing_files"] == [REL] and res["passed"] is False
    assert res["unmeasured"] is False
    assert res.get("budget_short_files") == [REL]
    assert "resource_affected_files" not in res
    assert leerie._acceptance_any_fails([res]) is True
    assert _outcome(leerie, st, [s_], res) == "unconverged"


def test_an_always_failing_set_slower_than_its_reruns_is_unconverged(
        leerie, tmp_path, monkeypatch):
    """The review's driver (round 5 H1): each run costs 0.4 s and fails
    deterministically on a non-held case (an assertion, no resource
    signature); budget 1.0 s, k = 3, so two reruns finish and the third is
    cut. Before: `unmeasured`, outcome `done`. Every run that finished
    failed it: it is failing, and the run `unconverged`."""
    st = leerie.State(tmp_path / "state", "r")
    st.data = {"task": "t", "categories": ["bug-fixing"]}
    s_, wt = _vote_set(tmp_path)
    cost = 0.4
    reruns: list[str] = []

    async def run(st_, caps_, tree, rel, log_path, label, *, cases_out=None,
                  **_kw):
        if rel == CONTROL:
            return True
        t = caps_["worker_timeout_sec"]
        if "-confirm" in label:
            reruns.append("cut" if cost > t else "ran")
        if cost > t:
            await asyncio.sleep(t)
            cases_out["resource"] = True
            return False
        await asyncio.sleep(cost)
        cases_out["failing_cases"] = [f"{REL}::test_b"]
        return False
    monkeypatch.setattr(leerie, "_run_acceptance_file", run)
    (res,) = asyncio.run(leerie._acceptance_run_placed(
        st, _caps(leerie, confirm_pairs=3, confirm_pairs_escalated=3,
                  confirm_budget_sec=1.0, acceptance_intermittent_repeats=0),
        wt, [s_], "t"))
    assert reruns == ["ran", "ran", "cut"]
    assert res["failing_files"] == [REL] and res["unmeasured"] is False
    assert res["budget_short_files"] == [REL]
    assert res["failing_cases"] == {REL: [f"{REL}::test_b"]}
    assert leerie._acceptance_any_fails([res]) is True
    assert _outcome(leerie, st, [s_], res) == "unconverged"
    assert any("held-out" in r for r in st.data["outcome_reasons"])


def test_a_budget_short_vote_that_saw_a_pass_stays_resource_affected(
        leerie, tmp_path, monkeypatch):
    """Control: a rerun that finished and PASSED the file is evidence,
    budget or not — the shortfall does not confirm it."""
    st = leerie.State(tmp_path / "state", "r")
    st.data = {"task": "t", "categories": ["bug-fixing"]}
    s_, wt = _vote_set(tmp_path)
    n = {"i": 0}

    async def run(st_, caps_, tree, rel, log_path, label, *, cases_out=None,
                  **_kw):
        if rel == CONTROL:
            return True
        if "-confirm" not in label:
            return False
        n["i"] += 1
        t = caps_["worker_timeout_sec"]
        if n["i"] == 1:
            await asyncio.sleep(0.2)
            return True                   # a finished rerun passed it
        await asyncio.sleep(t)
        cases_out["resource"] = True
        return False
    monkeypatch.setattr(leerie, "_run_acceptance_file", run)
    (res,) = asyncio.run(leerie._acceptance_run_placed(
        st, _caps(leerie, confirm_pairs=3, confirm_pairs_escalated=3,
                  confirm_budget_sec=0.5, acceptance_intermittent_repeats=0),
        wt, [s_], "t"))
    assert res["failing_files"] == []
    assert res.get("resource_affected_files") == [REL]
    assert "budget_short_files" not in res


def test_a_cut_reruns_finished_file_that_passed_refutes_it(leerie):
    """A rerun cut partway still carries the files it finished: one that
    passed there is not confirmed by the shortfall; one it failed is."""
    clock = {"t": 0.0}

    async def run(left):
        clock["t"] += left
        return leerie._RerunCut(frozenset({"b"}), frozenset({"a", "b"}))
    leerie_caps = _caps(leerie, confirm_budget_sec=10)
    v = asyncio.run(leerie._confirm_still_failing(
        ["a", "b", "c"], run, leerie_caps, label="t", seen_failing=True,
        clock=lambda: clock["t"]))
    assert v["budget_exhausted"] is True and v["reruns_run"] == 0
    assert v["confirmed"] == ["b", "c"] and v["budget_short"] == ["b", "c"]
    assert v["resource_affected"] == ["a"] and v["no_verdict"] == []


def test_a_vote_file_seen_failing_only_by_a_timeout_is_unsettled_not_failing(
        leerie, tmp_path, monkeypatch):
    """Review round 6, unproven suspicion, driven: a vote file whose first
    run timed out (a resource signature, not an assertion) and whose every
    rerun the budget cut was confirmed failing on that timeout alone
    (`budget_short_files`), and the run `unconverged`. With no finished
    rerun it is `budget_short_unsettled`: the set is unmeasured, the run is
    not stopped by it (DESIGN §9 *Which failures are new*, step 4). Paired
    with the control above, whose first run failed on an assertion and
    stays failing under the same cut."""
    st = leerie.State(tmp_path / "state", "r")
    st.data = {"task": "t", "categories": ["bug-fixing"]}
    s_, wt = _vote_set(tmp_path)

    async def run(st_, caps_, tree, rel, log_path, label, *, cases_out=None,
                  **_kw):
        if rel == CONTROL:
            return True
        if "-confirm" in label:
            await asyncio.sleep(caps_["worker_timeout_sec"] + 0.05)
        cases_out["resource"] = True     # timed out, first run and reruns
        return False
    monkeypatch.setattr(leerie, "_run_acceptance_file", run)
    (res,) = asyncio.run(leerie._acceptance_run_placed(
        st, _caps(leerie, confirm_pairs=1, confirm_pairs_escalated=1,
                  confirm_budget_sec=0.3, acceptance_intermittent_repeats=0),
        wt, [s_], "t"))
    assert res["failing_files"] == [] and "budget_short_files" not in res
    assert res["unmeasured"] is True and res["passed"] is False
    assert res["budget_short_unsettled_files"] == [REL]
    assert leerie._acceptance_any_fails([res]) is False
    assert _outcome(leerie, st, [s_], res) == "done"


def test_a_timeout_only_vote_file_is_named_in_the_pr(leerie, tmp_path,
                                                     monkeypatch):
    """Review round 7, M3, driven (`held_out_unsettled_pr_silent.patch`):
    after the round-6 fix above, the same file, every rerun cut, left its
    set unmeasured, the run `done`, and the PR's end-state section empty —
    the file named nowhere. The outcome stays `done` (DESIGN §8 *A
    held-out set the final tree leaves unmeasured is never silent*), and
    the PR lists the set, the file and the timeout under "Needs human
    verification"."""
    st = leerie.State(tmp_path / "state", "r")
    st.data = {"task": "t", "categories": ["bug-fixing"]}
    s_, wt = _vote_set(tmp_path)

    async def run(st_, caps_, tree, rel, log_path, label, *, cases_out=None,
                  **_kw):
        if rel == CONTROL:
            return True
        if "-confirm" in label:
            await asyncio.sleep(caps_["worker_timeout_sec"] + 0.05)
        # What `_run_acceptance_file` records for a timeout.
        cases_out.update(resource=True, timed_out=True)
        return False
    monkeypatch.setattr(leerie, "_run_acceptance_file", run)
    (res,) = asyncio.run(leerie._acceptance_run_placed(
        st, _caps(leerie, confirm_pairs=1, confirm_pairs_escalated=1,
                  confirm_budget_sec=0.3, acceptance_intermittent_repeats=0),
        wt, [s_], "t"))
    assert res["unmeasured"] is True
    assert res["unmeasured_files"] == {REL: "timed_out"}
    assert _outcome(leerie, st, [s_], res) == "done"
    body = leerie._format_pr_end_state_section(st.data)
    nhv = body.split("## Needs human verification", 1)[1]
    line = next(x for x in nhv.splitlines() if "held-out set 1" in x)
    assert f"`{REL}` timed out on its first run" in line
    assert "made hang" in line
    assert nhv.index("**Held-out acceptance sets the run could not "
                     "measure**") < nhv.index(line)
