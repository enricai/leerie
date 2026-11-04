"""A held-out case has one name in every tree (DESIGN §8 *A case has one
name in every tree*).

A set is validated and scope-judged in its own tree (`acceptance-N`) and
evaluated in another (`acceptance-eval`). vitest's jest-compatible JSON
names a case by its file's absolute path, so before `_parse_runner_report`
took the tree, one case read in two trees had two IDs and the per-case
has-work test (`_acceptance_file_has_work`) never found a counted case
among the failing ones. Barnacle 48c48629 ended `done` with set 5's counted
cases still failing, and handed the next run (a192d275) no residual.

The replay fixture is that run's own `acceptance.sets` (files and scope) and
`acceptance.gate.{final,residual}`, verbatim.
"""
from __future__ import annotations

import copy
import json
import types
from pathlib import Path

FIXTURE = (Path(__file__).parent / "fixtures" / "heldout_case_ids"
           / "48c48629_acceptance.json")

TREE_A = "/leerie-state/runs/r/worktrees/abcdef012345-acceptance-2"
TREE_B = "/leerie-state/runs/r/worktrees/abcdef012345-acceptance-eval"
REL = "test/held/redirect.defect.test.ts"


def _jest_report(tree: str, failing: list[str], passing: list[str]) -> bytes:
    return json.dumps({"testResults": [{
        "name": f"{tree}/{REL}", "status": "failed",
        "assertionResults": (
            [{"fullName": t, "status": "failed", "failureMessages": ["x"]}
             for t in failing]
            + [{"fullName": t, "status": "passed"} for t in passing])}]}
    ).encode()


def _replay_state() -> dict:
    return copy.deepcopy(json.loads(FIXTURE.read_text()))


def test_one_case_parsed_in_two_trees_has_one_id(leerie):
    a = leerie._parse_runner_report(
        "jest-json", _jest_report(TREE_A, ["hop one"], ["ctl"]), TREE_A)
    b = leerie._parse_runner_report(
        "jest-json", _jest_report(TREE_B, ["hop one"], ["ctl"]), TREE_B)
    assert a["cases"] == b["cases"] == {f"{REL} :: hop one": "failed",
                                        f"{REL} :: ctl": "passed"}


def test_a_counted_case_from_one_tree_is_work_when_failing_in_another(
        leerie):
    """End to end over the parser: the counted IDs come from the set's own
    tree's report, the failing IDs from the evaluation tree's."""
    own = leerie._parse_runner_report(
        "jest-json", _jest_report(TREE_A, ["hop one", "variant"], []), TREE_A)
    ev = leerie._parse_runner_report(
        "jest-json", _jest_report(TREE_B, ["hop one"], ["variant"]), TREE_B)
    counted = [c for c in own["cases"] if c.endswith(":: hop one")]
    failing = sorted(c for c, s in ev["cases"].items() if s == "failed")
    set_ = {"index": 1, "defect_files": [REL],
            "scope": {"bounded": True, "counted_cases": {REL: counted}}}
    assert leerie._acceptance_file_has_work(set_, REL, {REL: failing})
    assert leerie._acceptance_work_fails(
        [{"index": 1, "passed": False, "unmeasured": False,
          "failing_files": [REL], "failing_cases": {REL: failing}}], [set_])


def test_48c48629_replay_finds_the_work(leerie):
    data = leerie._acceptance_case_ids_relative(_replay_state())
    acc = data["acceptance"]
    assert leerie._acceptance_work_fails(acc["gate"]["final"], acc["sets"])
    res = leerie._acceptance_residual_unbroken(acc["gate"]["residual"],
                                               acc["sets"])
    assert res is not None and res["sets"] == [5]
    assert res["failing_sets"] == 1


def test_48c48629_replay_as_recorded_shows_the_defect(leerie):
    """The recorded IDs, read raw: the shape the run ended `done` on. Pins
    that the fixture still carries the per-worktree names the normaliser
    exists for."""
    acc = _replay_state()["acceptance"]
    assert not leerie._acceptance_work_fails(acc["gate"]["final"],
                                             acc["sets"])
    assert leerie._acceptance_residual_unbroken(
        acc["gate"]["residual"], acc["sets"]) is None


def test_normalising_is_idempotent_and_leaves_relative_ids(leerie):
    once = leerie._acceptance_case_ids_relative(_replay_state())
    twice = leerie._acceptance_case_ids_relative(copy.deepcopy(once))
    assert once == twice
    assert "/worktrees/" not in json.dumps(
        once["acceptance"]["sets"][4]["scope"]["counted_cases"])
    assert leerie._case_id_relative(f"{REL} :: t", [REL]) == f"{REL} :: t"
    # An absolute path that is no set file's stays as it was.
    assert leerie._case_id_relative("/x/other.ts :: t", [REL]) == (
        "/x/other.ts :: t")


def test_a_resumed_run_reads_relative_ids(leerie, tmp_path):
    root = tmp_path / "state"
    st = leerie.State(root, "run-x", repo_root=tmp_path)
    st.path.write_text(json.dumps(_replay_state()))
    assert st.load()
    acc = st.data["acceptance"]
    assert leerie._acceptance_work_fails(acc["gate"]["final"], acc["sets"])


def test_the_next_run_is_handed_the_48c48629_residual(leerie, tmp_path):
    runs = tmp_path / "runs"
    prev = runs / "prev"
    prev.mkdir(parents=True)
    (prev / "state.json").write_text(json.dumps(
        {"task": "the task", **_replay_state()}))
    (prev / "orchestrator.exit_code").write_text("0")
    cur = runs / "cur"
    cur.mkdir()
    st = types.SimpleNamespace(data={"task": "the task"}, run_dir=cur)
    got = (leerie._prior_delivery_residual(st) or {}).get("acceptance_unmet")
    assert got is not None and got["failing_sets"] == 1
