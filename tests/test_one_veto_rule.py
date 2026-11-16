"""One veto rule (DESIGN §8 *A check vetoes `done` only when it is
verifiable, actionable and measured accurate*).

A check may veto `done`, or roll a worker's round back, only when its name is
in `OUTCOME_VETO_CHECKS`; `_check_vetoes` is the one predicate. Every other
check's failure is a finding: `_decide_outcome` puts its reason in
`outcome_findings`, a run with commits and only findings is `done`, and the
PR's end-state section opens with `## Reported findings`.

Real paths: a real `State`, a real git run repository whose run branch is the
tree that ships (`_shipping_tree` reads it), the real decider and the real
renderer. Nothing is stubbed.
"""
from __future__ import annotations

import json
import re
import subprocess

import pytest

RUN = "fix-001-abcdef"
HELD = "1 held-out acceptance set(s) still fail"
CI_RED = ("1 of the repository's own merge check(s) red on the final tree: "
          "lint")


@pytest.fixture
def st(leerie, tmp_path):
    """A run with a real run repository: its run branch is the tree that
    ships, and the repository has no test command, so `done` rests on no
    test run (tests/test_unmeasured_is_not_done.py pins that rule)."""
    s = leerie.State(tmp_path / ".leerie", RUN)
    s.data = {"task": "x", "subtask_status": {"a": "complete"}}
    s.save()
    for args in (["init", "-q", "-b", f"leerie/runs/{RUN}"],
                 ["config", "user.email", "t@t"],
                 ["config", "user.name", "t"],
                 ["commit", "-q", "--allow-empty", "-m", "base"]):
        subprocess.run(["git", *args], cwd=s.repo_root, check=True,
                       capture_output=True)
    run_repo = leerie._ensure_run_repo(s)
    subprocess.run(["git", "-C", str(run_repo), "branch", f"leerie/runs/{RUN}",
                    "refs/leerie/user-head"], check=True, capture_output=True)
    yield s
    s.release_lock()


def _held_out_failing() -> dict:
    return {"acceptance": {"gate": {"final": [
        {"index": 0, "passed": True}, {"index": 1, "passed": False}]}}}


def _base_form_changed(leerie, st) -> dict:
    """Of the tree that ships, as phase 5's check records it."""
    return {"existing_behaviour": {"tree_sha": leerie._shipping_tree(st),
                                   "changed": [
        {"id": "tests/test_pin.py :: test_pins_zero",
         "file": "tests/test_pin.py"}]}}


def _ci_red() -> dict:
    return {"ci_parity": {"final": {"unmet": [{"id": "lint"}]}}}


def _run_json(st) -> dict:
    return json.loads((st.run_dir / "run.json").read_text())


# --- (1) the predicate --------------------------------------------------------

def test_only_the_merge_checks_veto(leerie):
    assert set(leerie.OUTCOME_VETO_CHECKS) <= set(leerie.OUTCOME_CHECKS)
    for check in leerie.OUTCOME_CHECKS:
        assert leerie._check_vetoes(check) is (check == "merge_checks"), check


@pytest.mark.parametrize("name", ["merge_check", "held-out", "", "MERGE_CHECKS"])
def test_an_unknown_check_raises(leerie, name):
    """A typo must not silently turn a check into a report."""
    with pytest.raises(ValueError, match="unknown outcome check"):
        leerie._check_vetoes(name)


# --- (2)-(4) the decider --------------------------------------------------------

@pytest.mark.parametrize("extra", ["held_out", "base_form"])
def test_a_finding_alone_on_a_run_with_commits_is_done(leerie, st, extra):
    data = _held_out_failing() if extra == "held_out" else _base_form_changed(leerie, st)
    st.data.update(data)
    want = (HELD if extra == "held_out" else leerie._base_form_reason(
        data["existing_behaviour"]["changed"]))
    assert leerie._decide_outcome(st, any_commits=True) == "done"
    assert st.data["outcome"] == "done"
    assert st.data["outcome_reasons"] == []
    assert st.data["outcome_findings"] == [want]
    rj = _run_json(st)
    assert rj["outcome"] == "done" and rj["findings"] == 1


def test_a_red_merge_check_vetoes_and_findings_stay_findings(leerie, st):
    st.data.update({**_ci_red(), **_held_out_failing(),
                    **_base_form_changed(leerie, st)})
    assert leerie._decide_outcome(st, any_commits=True) == "unconverged"
    assert st.data["outcome_reasons"] == [CI_RED]
    assert st.data["outcome_findings"] == [
        HELD, leerie._base_form_reason(
            st.data["existing_behaviour"]["changed"])]
    rj = _run_json(st)
    assert rj["outcome"] == "unconverged" and rj["findings"] == 2


def test_a_red_merge_check_alone_vetoes(leerie, st):
    st.data.update(_ci_red())
    assert leerie._decide_outcome(st, any_commits=True) == "unconverged"
    assert st.data["outcome_reasons"] == [CI_RED]
    assert st.data["outcome_findings"] == []


def test_a_finding_on_a_run_with_no_commits_is_unconverged_not_no_work(
        leerie, st):
    """The finding disputes "no work required": the run is `unconverged`,
    and the finding is its reason."""
    st.data.update(_held_out_failing())
    assert leerie._decide_outcome(st, any_commits=False) == "unconverged"
    assert st.data["outcome_reasons"] == [HELD]
    assert st.data["outcome_findings"] == []
    # Control: nothing found and no commits is no work.
    st.data.pop("acceptance")
    assert leerie._decide_outcome(st, any_commits=False) == "no_work"
    assert st.data["outcome_reasons"] == []
    assert st.data["outcome_findings"] == []


# --- (5) the PR's end-state section --------------------------------------------

def test_a_done_runs_findings_lead_the_pr_end_state(leerie, st):
    st.data.update({**_held_out_failing(), **_base_form_changed(leerie, st)})
    assert leerie._decide_outcome(st, any_commits=True) == "done"
    # A second section to order against: a round the run rolled back.
    leerie._record_round_rollback(st, "acceptance repair", "round 1",
                                  "left its tree unclean",
                                  pre_sha="a" * 40, post_sha="b" * 40)
    findings = st.data["outcome_findings"]
    assert len(findings) == 2
    end = leerie._format_pr_end_state_section(st.data)
    headings = re.findall(r"^## .+$", end, flags=re.M)
    assert headings[0] == "## Reported findings"
    assert "## Not verified by this run" in headings[1:]
    assert "## Unconverged" not in headings
    body = end.split("## Reported findings\n\n", 1)[1].split("\n\n## ", 1)[0]
    lead, _, listed = body.partition("\n\n")
    assert lead.startswith("The checks that may veto `done` passed, but 2 "
                           "check finding(s) below were reported, not "
                           "verified")
    assert listed.splitlines() == [f"- {f}" for f in findings]


@pytest.mark.parametrize("veto,commits,under", [
    (False, True, "## Reported findings"),
    (True, True, "## Reported findings"),
    (False, False, "## Unconverged")],
    ids=["done", "vetoed_by_a_merge_check", "no_commits"])
def test_an_unconverged_subtasks_reason_goes_where_its_check_went(
        leerie, st, veto, commits, under):
    """A subtask's verify command does not veto: on a `done` run, and on one
    a merge check vetoed, the subtask's own reason is listed with the
    findings, and no `## Unconverged` section claims it made the PR a draft.
    Only a run with no commits, whose findings all become vetoes, lists it
    under `## Unconverged`."""
    st.data["subtask_status"]["b"] = "unconverged"
    st.data["unconverged_subtasks"] = {
        "b": {"reason": "its verify command stayed red"}}
    if veto:
        st.data.update(_ci_red())
    outcome = leerie._decide_outcome(st, any_commits=commits)
    assert outcome == ("done" if commits and not veto else "unconverged")
    end = leerie._format_pr_end_state_section(st.data)
    headings = re.findall(r"^## .+$", end, flags=re.M)
    line = "- **b**: its verify command stayed red"
    sections = {h: b for h, b in (
        (s_.split("\n", 1)[0], s_) for s_ in ("\n" + end).split("\n## ")[1:])}
    assert line in sections[under[3:]].splitlines()
    assert sum(line in b.splitlines() for b in sections.values()) == 1
    if outcome == "done":
        assert headings[0] == "## Reported findings"
        assert "## Unconverged" not in headings
    elif veto:
        assert headings[:2] == ["## Unconverged", "## Reported findings"]
        assert CI_RED in sections["Unconverged"]


def test_a_clean_done_run_has_no_findings_section(leerie, st):
    assert leerie._decide_outcome(st, any_commits=True) == "done"
    assert "## Reported findings" not in \
        leerie._format_pr_end_state_section(st.data)


# --- (6) round rollbacks ---------------------------------------------------------

BROKE = [{"id": "lint", "lines": ["src/a.py:3: E501 line too long"]}]


def test_only_a_red_merge_check_rolls_a_round_back(leerie):
    reason = leerie._ratchet_rollback_reason
    assert reason({"merge_checks_broke": BROKE}) == (
        "turned the repository's merge check(s) red: lint "
        "(src/a.py:3: E501 line too long)")
    for rat in ({"regressed": [1]}, {"new_failures": ["t.py :: t"]},
                {"base_form_changed": ["t.py :: t"]},
                {"regressed": [1], "new_failures": ["t.py :: t"],
                 "base_form_changed": ["t.py :: t"]}, {}):
        assert reason(rat) is None, rat
    # Beside the others, the merge checks still decide.
    assert reason({"regressed": [1], "new_failures": ["t.py :: t"],
                   "merge_checks_broke": BROKE}).startswith(
        "turned the repository's merge check(s) red: lint")
