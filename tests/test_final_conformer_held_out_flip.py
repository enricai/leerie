"""A final-conformer round that turns a passing held-out case failing is
rolled back (DESIGN §8; lane FC, barnacle 39c4c5ad).

39c4c5ad's shape on real git, real pytest held-out sets and the real
final-conformance pass and acceptance gate: on the integrated tree set 1
fails (its defect is not fixed) and sets 2-5 pass. The final conformer's
round 0 commits a change that makes sets 2-5 fail (`9712d56`, "keep replay
success as transport verdict"); its round 1 commits more on top
(`b0ef30f`). The gate's first measurement was then of the conformer's end,
read "5 of 5 measured held-out set(s) fail", and nothing said the conformer
broke four of them.

Stubbed, as tests/test_acceptance_gate.py stubs them: `claude_p` (the
conformer and the repair), `_measure_axes` for the build/lint/test axes
(this rule reads held-out sets only; the delivery ratchet's measurement
stays real), B* reruns of a failing set file and held-case discovery
(`_confirm_at_once`), and the post-repair set audit (`_no_set_audit`).
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from tests.test_acceptance_gate import (  # noqa: F401  (fixtures by name)
    BUGGY, EFFORTS, FIXED, MODELS, _caps, _clear_deps_memo, _confirm_at_once,
    _git, _no_set_audit, _repo, _st, _staging)

# Set 1 tests a function the integrated tree does not have yet (its defect
# is open); sets 2-5 test `add`, which the integrated tree fixed.
SUB_TEST = ("from calc import sub\n\n"
            "def test_sub():\n    assert sub(5, 3) == 2\n")
ADD_TEST = ("from calc import add\n\n"
            "def test_add_sums():\n    assert add(2, 3) == 5\n")
CONTROL = ("from calc import add\n\n"
           "def test_add_zero():\n    assert add(0, 0) == 0\n")
WITH_SUB = FIXED + "\n\ndef sub(a, b):\n    return a - b\n"


def _sets(st) -> list[dict]:
    sets = []
    for k in range(1, 6):
        d = st.run_dir / "acceptance" / f"set-{k}"
        (d / "acc").mkdir(parents=True)
        (d / "acc" / f"test_defect_{k}.py").write_text(
            SUB_TEST if k == 1 else ADD_TEST)
        (d / "acc" / f"test_control_{k}.py").write_text(CONTROL)
        sets.append({"index": k, "valid": True, "dir": str(d),
                     "defect_files": [f"acc/test_defect_{k}.py"],
                     "control_files": [f"acc/test_control_{k}.py"],
                     "cases": {f"acc/test_defect_{k}.py": [f"case {k}"],
                               f"acc/test_control_{k}.py": ["control"]}})
    return sets


def _conformer_result(defect: bool) -> dict:
    res = {
        "subtask_id": "whole-tree", "rules_files_read": [],
        "rule_violations_fixed": [], "rule_violations_residual": [],
        "docs_updates": [], "tests_updates": [],
        "build": {"ran": False, "passed": False, "command": "",
                  "summary": ""},
        "lint": {"ran": False, "passed": False, "command": "",
                 "summary": ""},
        "tests": {"ran": False, "passed": False, "command": "",
                  "summary": ""},
        "summary": "conformance",
        "production_evidence": {"exercised": True, "how": "pytest",
                                "observed": "ran"},
    }
    if defect:
        # Keeps the pass going into a second round (DESIGN §9, final-tree
        # pass: an unfixed defect is what the remaining rounds are for).
        res["solution_defects"] = [{
            "kind": "unhandled_case", "where": "calc.py:1",
            "concrete_case": "add(-1, 1)", "actionable": True}]
    return res


@pytest.fixture
def run(leerie, tmp_path, monkeypatch):
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head, working_branch="main",
             base_sha=head)
    staging = _staging(st, repo)
    # The integrated tree: `add` fixed by the run's subtasks, `sub` not.
    (staging / "calc.py").write_text(FIXED)
    _git(staging, "commit", "-qam", "wave-1: integrate")
    st.data["acceptance"] = {"sets": _sets(st)}
    st.save()
    real = leerie._measure_axes

    async def green(tree, axes, st_, caps, **kw):
        if kw.get("log_prefix") == "delivery ratchet":
            return await real(tree, axes, st_, caps, **kw)
        return {a: {"ran": True, "measured": True, "passed": True,
                    "command": c, "summary": ""} for a, c in axes.items()}
    monkeypatch.setattr(leerie, "_measure_axes", green)
    return {"leerie": leerie, "st": st, "staging": staging,
            "caps": _caps(leerie, 5), "monkeypatch": monkeypatch}


def _final_conformer(r, rounds: list) -> list[str]:
    """Run the real final pass; `rounds[i]` edits staging in round i (or
    None) and the round commits it. Returns HEAD after each round."""
    c, staging = r["leerie"], r["staging"]
    heads: list[str] = []

    async def conformer(**kw):
        i = len(heads)
        edit = rounds[i] if i < len(rounds) else None
        if edit is not None:
            edit(Path(kw["cwd"]))
            _git(Path(kw["cwd"]), "commit", "-qam", f"conformer: round {i}")
        heads.append(_git(staging, "rev-parse", "HEAD"))
        return _conformer_result(defect=i < len(rounds) - 1)
    r["monkeypatch"].setattr(c, "claude_p", conformer)
    asyncio.run(c._run_final_conformance(
        r["st"].run_dir, r["st"], r["caps"],
        {"conformer": "sonnet"}, {"conformer": None}))
    return heads


def _gate(r, repair=None) -> list[dict]:
    c = r["leerie"]
    calls: list[dict] = []

    async def fixer(**kw):
        calls.append(kw)
        if repair is not None:
            repair(Path(kw["cwd"]))
        return {}
    r["monkeypatch"].setattr(c, "claude_p", fixer)
    asyncio.run(c._run_acceptance_gate(r["st"].run_dir, r["st"], r["caps"],
                                       MODELS, EFFORTS))
    return calls


def _break_add(tree: Path) -> None:
    (tree / "calc.py").write_text(BUGGY)


def _add_doc(name: str):
    def edit(tree: Path) -> None:
        (tree / name).write_text("conformance note\n")
        _git(tree, "add", name)
    return edit


def _write_sub(tree: Path) -> None:
    (tree / "calc.py").write_text(WITH_SUB)


def _add_sub(tree: Path) -> None:
    _write_sub(tree)
    _git(tree, "commit", "-qam", "conformer: add sub")


def _passing(results: list[dict]) -> list[int]:
    return [x["index"] for x in results if x.get("passed")]


def test_replay_39c4c5ad_the_conformers_breaking_round_is_rolled_back(run):
    """Round 0 breaks sets 2-5, round 1 commits on top. The gate rolls the
    pass back to round 0's start and repairs only set 1 from there."""
    staging = run["staging"]
    start = _git(staging, "rev-parse", "HEAD")
    heads = _final_conformer(run, [_break_add, _add_doc("NOTES.md")])
    end = heads[-1]
    assert len(heads) == 2 and end != start
    fin = run["st"].data["conformance"]["_final"]
    assert fin["round_starts"] == [start, heads[0]]
    assert fin["end_sha"] == end

    calls = _gate(run, repair=_add_sub)

    gate = run["st"].data["acceptance"]["gate"]
    flip = gate["conformer_flip"]
    assert flip["start_sha"] == start and flip["end_sha"] == end
    assert flip["culprit_round"] == 0 and flip["rollback_to"] == start
    assert flip["rolled_back"] is True
    # Every case of sets 2-5 passed on the start and fails on the end.
    assert {b.split(":", 1)[0] for b in flip["broken_cases"]} == {
        "2", "3", "4", "5"}
    # The gate's pre-repair verdict is the start's: only set 1 fails there.
    assert gate["initial_sha"] == start
    assert _passing(gate["initial"]) == [2, 3, 4, 5]
    # The repair then worked from the start and fixed set 1 alone.
    assert len(calls) == 1
    assert _passing(gate["final"]) == [1, 2, 3, 4, 5]
    assert (staging / "calc.py").read_text() == WITH_SUB
    # Round 1, built on the culprit, went with it.
    assert not (staging / "NOTES.md").exists()
    rows = run["st"].data["round_rollbacks"]
    assert [(x["worker"], x["label"], x["reason"], x["pre_sha"])
            for x in rows] == [("final conformer", "round 0",
                                "broke previously passing held-out case(s)",
                                start)]


def test_a_conformer_that_broke_nothing_is_kept(run):
    """Control: set 1 fails at the end, as on the start; nothing passing
    on the start fails at the end, so the conformer's commit stands."""
    staging = run["staging"]
    start = _git(staging, "rev-parse", "HEAD")
    heads = _final_conformer(run, [_add_doc("NOTES.md")])
    _gate(run)
    gate = run["st"].data["acceptance"]["gate"]
    assert gate["conformer_flip"] == {"start_sha": start, "end_sha": heads[-1],
                                      "broken_cases": []}
    assert gate["initial_sha"] == heads[-1]
    assert (staging / "NOTES.md").exists()
    assert "round_rollbacks" not in run["st"].data


def test_the_culprit_is_the_round_that_broke_the_case(run):
    """Round 0 is harmless and round 1 breaks `add`: only round 1 goes."""
    staging = run["staging"]
    start = _git(staging, "rev-parse", "HEAD")
    heads = _final_conformer(run, [_add_doc("NOTES.md"), _break_add])
    _gate(run, repair=_add_sub)
    flip = run["st"].data["acceptance"]["gate"]["conformer_flip"]
    assert flip["start_sha"] == start
    assert flip["culprit_round"] == 1 and flip["rollback_to"] == heads[0]
    assert (staging / "NOTES.md").exists()
    assert (staging / "calc.py").read_text() == WITH_SUB


def test_a_pass_whose_sets_all_pass_at_its_end_pays_nothing(run):
    """No failing case at the end: no evaluation of the start, no record."""
    _final_conformer(run, [_write_sub])
    c = run["leerie"]
    evals: list[str] = []
    real = c._evaluate_acceptance_sets

    async def spy(st, caps, tree, sets, label, **kw):
        evals.append(label)
        return await real(st, caps, tree, sets, label, **kw)
    run["monkeypatch"].setattr(c, "_evaluate_acceptance_sets", spy)
    _gate(run)
    gate = run["st"].data["acceptance"]["gate"]
    assert "conformer_flip" not in gate
    assert evals[0] == "gate-initial"
    assert "conformer-start" not in evals


def test_a_decided_rollback_is_finished_on_resume(run):
    """The decision reaches disk before the reset: a resume that finds it
    unfinished resets, then measures the restored tree afresh."""
    staging = run["staging"]
    start = _git(staging, "rev-parse", "HEAD")
    heads = _final_conformer(run, [_break_add])
    st = run["st"]
    st.data["acceptance"]["gate"] = {
        "rounds": [], "initial_sha": heads[-1], "initial": [],
        "conformer_flip": {"start_sha": start, "end_sha": heads[-1],
                           "broken_cases": ["2:acc/test_defect_2.py"],
                           "culprit_round": 0, "rollback_to": start}}
    st.save()
    _gate(run, repair=_add_sub)
    gate = st.data["acceptance"]["gate"]
    assert gate["conformer_flip"]["rolled_back"] is True
    assert gate["initial_sha"] == start
    assert _passing(gate["initial"]) == [2, 3, 4, 5]
    assert (staging / "calc.py").read_text() == WITH_SUB
