"""A subtask that did not converge is reported, not a veto (DESIGN §6): with
commits and a delivery verdict, the outcome rests on what the inputs that
read the tree that ships say, and the PR still names the subtask; on an
empty run branch, or with no delivery verdict, it still vetoes. Drives the real
`_decide_outcome` on a real State and run repository, then renders the PR's
end-state section from the state it wrote."""
from __future__ import annotations

import json
import subprocess

import pytest

RUN_ID = "fix-001-abcdef"
REASON = "re-drive budget spent: the verify command still fails"


@pytest.fixture
def st(leerie, tmp_path):
    s = leerie.State(tmp_path / ".leerie", RUN_ID)
    s.data = {"task": "x",
              "subtask_status": {"feat-001": "complete",
                                 "feat-002": "unconverged"},
              "unconverged_subtasks": {"feat-002": {
                  "at": "t", "previous_status": "blocked",
                  "reason": REASON}}}
    s.save()
    repo = s.repo_root
    for args in (["init", "-q", "-b", f"leerie/runs/{RUN_ID}"],
                 ["config", "user.email", "t@t"],
                 ["config", "user.name", "t"],
                 ["commit", "-q", "--allow-empty", "-m", "base"]):
        subprocess.run(["git", *args], cwd=repo, check=True,
                       capture_output=True)
    run_repo = leerie._ensure_run_repo(s)
    subprocess.run(["git", "-C", str(run_repo), "branch",
                    f"leerie/runs/{RUN_ID}", "refs/leerie/user-head"],
                   check=True, capture_output=True)
    return s


def _ship_tree(st) -> str:
    return subprocess.run(["git", "rev-parse", f"leerie/runs/{RUN_ID}^{{tree}}"],
                          cwd=st.repo_root, check=True, capture_output=True,
                          text=True).stdout.strip()


def _run_json(st) -> dict:
    return json.loads((st.run_dir / "run.json").read_text())


MET = {"item_index": 0, "verdict": "met", "by": []}


def _judged(st, *rows: dict) -> None:
    """Final detection read the required items on the tree that ships."""
    st.data["delivery_gate"] = {"unmet_final": [],
                                "final_tree": _ship_tree(st),
                                "items": [MET, *rows]}


def test_with_commits_it_is_reported_and_the_run_is_done(leerie, st):
    _judged(st)
    assert leerie._decide_outcome(st, any_commits=True) == "done"
    assert st.data["outcome_reasons"] == []
    assert _run_json(st)["outcome"] == "done"
    section = leerie._format_pr_end_state_section(st.data)
    assert "This PR is a draft" not in section
    assert section.startswith("\n## Unconverged\n\n"
                              + leerie._PR_UNCONVERGED_NOT_IN_PR)
    head = section.index("## Unconverged")
    lead = section.index(leerie._PR_UNCONVERGED_NOT_IN_PR)
    entry = section.index(f"- **feat-002**: {REASON}")
    assert head < lead < entry
    assert "feat-001" not in section


def test_on_an_empty_branch_it_still_vetoes(leerie, st):
    assert leerie._decide_outcome(st, any_commits=False) == "unconverged"
    assert st.data["outcome_reasons"] == [
        "1 subtask(s) unconverged: feat-002"]
    assert _run_json(st)["outcome"] == "unconverged"
    section = leerie._format_pr_end_state_section(st.data)
    assert (section.index("This PR is a draft")
            < section.index("- 1 subtask(s) unconverged: feat-002")
            < section.index(leerie._PR_UNCONVERGED_NOT_IN_PR)
            < section.index(f"- **feat-002**: {REASON}"))


def test_another_veto_still_decides_and_the_subtask_is_still_named(
        leerie, st):
    st.data["delivery_gate"] = {
        "unmet_final": [{"item_index": 0, "item": "i"}],
        "final_tree": _ship_tree(st),
        "items": [{"item_index": 0, "verdict": "unmet", "by": ["judge"]}]}
    assert leerie._decide_outcome(st, any_commits=True) == "unconverged"
    assert st.data["outcome_reasons"] == ["1 required item(s) still unmet"]
    section = leerie._format_pr_end_state_section(st.data)
    # The subtask is introduced, so it does not read as another reason.
    assert (section.index("This PR is a draft")
            < section.index("- 1 required item(s) still unmet")
            < section.index(leerie._PR_UNCONVERGED_NOT_IN_PR)
            < section.index(f"- **feat-002**: {REASON}"))


def test_with_no_delivery_verdict_it_still_vetoes(leerie, st):
    """No run measured had commits and no delivery verdict: nothing read
    what the subtask was for, so the veto stays."""
    assert leerie._decide_outcome(st, any_commits=True) == "unconverged"
    assert st.data["outcome_reasons"] == [
        "1 subtask(s) unconverged: feat-002"]


@pytest.mark.parametrize("gate", [
    # Only an earlier pass's verdict, of the tree that ships.
    lambda tree: {"unmet_after": [], "judged_after": {"tree": tree},
                  "items": [MET]},
    # Final detection's judge crashed on every attempt.
    lambda tree: {"unmet_final": [], "final_tree": tree, "items": [MET],
                  "final_judge": {"error": "TimeoutError"}},
    # An item no judge sample inspected.
    lambda tree: {"unmet_final": [], "final_tree": tree, "items": [
        MET, {"verdict": "unknown", "judge": {"not_inspected": True}}]},
    # A test outcome the diff could not measure.
    lambda tree: {"unmet_final": [], "final_tree": tree, "items": [
        MET, {"verdict": "unknown", "by": ["test_id_diff"]}]},
    # A row the re-drive re-judged, whose test outcome the diff left unknown.
    lambda tree: {"unmet_final": [], "final_tree": tree, "items": [
        MET, {"verdict": "unknown", "by": ["test_id_diff"],
              "redrive_recheck": {"sha": "f" * 40}}]},
    # An unknown row with no mark that a judge read it.
    lambda tree: {"unmet_final": [], "final_tree": tree, "items": [
        MET, {"verdict": "unknown", "by": [], "judge": {"item_index": 1}}]},
    # A test outcome left unknown, under a stamp that says the judge read it.
    lambda tree: {"unmet_final": [], "final_tree": tree, "items": [
        MET, {"verdict": "unknown", "by": ["test_id_diff"],
              "redrive_recheck": {"sha": "f" * 40, "not_inspected": False}}]},
    # A test outcome left unknown on an unobservable row.
    lambda tree: {"unmet_final": [], "final_tree": tree, "items": [
        MET, {"verdict": "unknown", "by": ["test_id_diff"],
              "unobservable": [{"needs": "a browser"}]}]},
    # A re-drive stamp an older leerie wrote, which cannot say which unknown.
    lambda tree: {"unmet_final": [], "final_tree": tree, "items": [
        MET, {"verdict": "unknown", "by": [],
              "redrive_recheck": {"sha": "f" * 40}}]},
    # No item met or unmet: every one external or unknown.
    lambda tree: {"unmet_final": [], "final_tree": tree, "items": [
        {"verdict": "external", "by": []},
        {"verdict": "unknown", "unobservable": [{"needs": "a browser"}]}]},
], ids=["earlier-pass-only", "judge-crashed", "item-not-inspected",
        "test-outcome-unknown", "test-outcome-unknown-after-redrive",
        "unknown-with-no-read-mark", "stamp-before-not-inspected",
        "test-outcome-unknown-stamped-read", "test-outcome-unknown-unobservable",
        "nothing-decided"])
def test_when_final_detection_did_not_read_the_items_it_still_vetoes(
        leerie, st, gate):
    """Shapes no measured run had: the veto stays (DESIGN §6, R1: a
    demotion reaches only the shapes the measured runs had)."""
    st.data["delivery_gate"] = gate(_ship_tree(st))
    assert leerie._decide_outcome(st, any_commits=True) == "unconverged"
    assert st.data["outcome_reasons"] == [
        "1 subtask(s) unconverged: feat-002"]


def test_an_unobservable_item_is_read(leerie, st):
    """An item unknown because a part of it is unobservable was read, as in
    measured runs: the subtask does not veto."""
    _judged(st, {"verdict": "unknown", "judge": {"item_index": 1},
                 "unobservable": [{"needs": "a browser"}]},
            {"verdict": "unknown", "judge": {"worker_only": True}},
            {"verdict": "external", "by": []})
    assert leerie._decide_outcome(st, any_commits=True) == "done"


MIXED = {"item": "x", "parts": ["works", "no test newly fails"],
         "part_checks": [{"part": "no test newly fails",
                          "check": "test_id_diff",
                          "verifiability": "execution"}]}


@pytest.mark.parametrize("row", [
    {"item_index": 1, "verdict": "unknown", "by": [],
     "judge": {"item_index": 1, "worker_only": True}},
    {"item_index": 1, "verdict": "unknown", "by": [],
     "judge": {"item_index": 1}, "unobservable": [{"needs": "a browser"}]},
], ids=["worker-only", "unobservable"])
@pytest.mark.parametrize("diff_met", [False, True], ids=["diff-unknown",
                                                         "diff-met"])
def test_a_read_row_whose_test_outcome_is_unsettled_is_not_read(
        leerie, st, row, diff_met):
    """Final detection writes a worker-only or unobservable row before the
    item's test-outcome part, so the row does not say the diff left that
    part unsettled: the item's own parts do, and the veto stays until the
    diff settles it met on the tree that ships."""
    st.data["required_items"] = [{"item": "a"}, MIXED]
    if diff_met:
        st.data["test_id_diff"] = {"tree_sha": _ship_tree(st), "new": [],
                                   "candidates": []}
    _judged(st, row)
    assert leerie._decide_outcome(st, any_commits=True) == (
        "done" if diff_met else "unconverged")


def _recheck(leerie, st, verdict: dict) -> None:
    """The delivery re-drive's re-check of item 1, through the real writer
    of its row."""
    leerie._redrive_apply_item_verdicts(
        st, {1: {"unmet": False, "by": [], "evidence": "", **verdict}},
        "f" * 40)


def test_an_item_the_redrive_did_not_inspect_is_not_read(leerie, st):
    """Detection read item 1; the re-drive's re-check, whose verdict now
    stands for it, inspected it in no sample."""
    _judged(st, {"item_index": 1, "verdict": "met", "by": [],
                 "judge": {"item_index": 1}})
    _recheck(leerie, st, {"unknown": True, "not_inspected": True})
    row = st.data["delivery_gate"]["items"][1]
    assert row["verdict"] == "unknown"
    assert row["redrive_recheck"]["not_inspected"] is True
    assert leerie._decide_outcome(st, any_commits=True) == "unconverged"
    assert st.data["outcome_reasons"] == [
        "1 subtask(s) unconverged: feat-002"]


def test_a_worker_only_met_at_the_redrive_is_read(leerie, st):
    """Detection found item 1 unmet; the re-drive's re-check read it, and its
    met rests only on a worker's run: unknown, but read, as in measured
    runs."""
    _judged(st, {"item_index": 1, "verdict": "unmet", "by": ["judge"],
                 "judge": {"item_index": 1}})
    _recheck(leerie, st, {"unknown": True})
    assert st.data["delivery_gate"]["items"][1]["redrive_recheck"][
        "not_inspected"] is False
    assert leerie._decide_outcome(st, any_commits=True) == "done"


def test_an_item_the_ratchet_lists_unverified_is_not_read(leerie, st):
    """The ratchet marked a met row unknown after a move; the row still
    carries an earlier re-check's stamp. Not read on the commit, and the
    outcome says so (DESIGN §8: being listed unverified makes the outcome
    say it was not read on the tree that ships)."""
    _judged(st, {"item_index": 1, "verdict": "met", "by": [],
                 "redrive_recheck": {"sha": "e" * 40,
                                     "not_inspected": False}})
    leerie._ratchet_mark_unverified(st, [1], "f" * 40, "a re-drive round")
    assert st.data["delivery_gate"]["items"][1]["verdict"] == "unknown"
    assert leerie._decide_outcome(st, any_commits=True) == "unconverged"
    assert st.data["outcome_reasons"] == [
        "1 subtask(s) unconverged: feat-002",
        "1 required item(s) not re-verified on the tree that ships"]


def test_an_unreadable_tree_that_ships_keeps_the_subtask_reason(
        leerie, st, monkeypatch):
    _judged(st)
    monkeypatch.setattr(leerie, "_shipping_tree", lambda st_: "")
    assert leerie._decide_outcome(st, any_commits=True) == "unconverged"
    assert st.data["outcome_reasons"][0] == (
        "1 subtask(s) unconverged: feat-002")


def test_a_final_verdict_of_another_tree_keeps_the_subtask_reason(leerie, st):
    st.data["delivery_gate"] = {"unmet_final": [], "final_tree": "0" * 40,
                                "items": [MET]}
    assert leerie._decide_outcome(st, any_commits=True) == "unconverged"
    reasons = st.data["outcome_reasons"]
    assert reasons[0] == "1 subtask(s) unconverged: feat-002"
    assert reasons[1].startswith(
        "the required items were not read on the tree that ships")


def test_no_unconverged_subtask_no_lead_line(leerie, st):
    _judged(st)
    st.data["subtask_status"]["feat-002"] = "complete"
    assert leerie._decide_outcome(st, any_commits=True) == "done"
    assert "## Unconverged" not in leerie._format_pr_end_state_section(
        st.data)


def test_a_draft_with_no_unconverged_subtask_has_no_intro(leerie, st):
    st.data["subtask_status"]["feat-002"] = "complete"
    st.data["delivery_gate"] = {"unmet_final": [{"item": "i"}],
                                "final_tree": _ship_tree(st)}
    assert leerie._decide_outcome(st, any_commits=True) == "unconverged"
    section = leerie._format_pr_end_state_section(st.data)
    assert "- 1 required item(s) still unmet" in section
    assert leerie._PR_UNCONVERGED_NOT_IN_PR not in section


def test_an_unconverged_subtask_with_no_record_is_still_named(leerie, st):
    _judged(st)
    del st.data["unconverged_subtasks"]
    assert leerie._decide_outcome(st, any_commits=True) == "done"
    section = leerie._format_pr_end_state_section(st.data)
    assert (section.index(leerie._PR_UNCONVERGED_NOT_IN_PR)
            < section.index("- **feat-002**: no reason recorded"))
