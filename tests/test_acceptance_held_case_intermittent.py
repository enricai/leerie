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


def test_discovery_off_keeps_a_passing_first_run(leerie, tmp_path):
    repo, own = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, own)
    marker = tmp_path / "runs"
    s_, _cid = _set(leerie, st, repo, marker, _base_for("PF"))
    res = _evaluate(leerie, st, repo, [s_],
                    _caps(leerie, acceptance_intermittent_repeats=0))
    assert res["passed"] is True and marker.read_text() == "1"
    assert res["discovery"]["incomplete"] is True
    assert res["discovery"]["reason"] == "repeats_disabled"
    assert res["discovery"]["repeats_run"] == 0


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
    _git(repo, "worktree", "add", "-q", "-b", "leerie/runs/x", str(staging))
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
