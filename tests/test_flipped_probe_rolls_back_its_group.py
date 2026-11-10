"""A kept group that turns a met item's confirmed probe from passing to
failing is rolled back (DESIGN §8 *A kept group that turns a met item's
confirmed probe from passing to failing is rolled back*).

Live (barnacle 22a30525, leerie 0.58.0): re-drive round 1's group, sent for
item 13 (clear the field before typing), deleted a guard in
`withClearBeforeType`; "Press Enter to submit the form" then got a
select-all clause, a regression on item 0 ("no regressions"). The group was
kept: its merge checks were green, no existing test covers that input, and
the ratchet's judge does not rule on a test-outcome part. The re-check after
the last round falsified item 0 on the tree that ships and recorded a
verdict; nothing rolled the group back, and the regression shipped. The
confirmed probe passed on the group's start and failed on its commit.

The replay: a real pytest repository, its run repository and staging, the
real `_run_delivery_redrive` — routing, the groups' real commits, the
ratchet, the re-checks, the falsifier's real gates and the flip's real B*
reruns on pristine checkouts of each commit — with only `claude_p` stubbed,
and the TS AST facts, since the probes here are Python. Synthetic text."""
from __future__ import annotations

import asyncio
import json

from tests.conftest import run_git_out as _git
from tests.test_met_items_refalsified_after_kept_rounds import (
    BUGGY_MUL, FIXED_ADD, FIXED_MUL, Replay, _fix_mul, _fix_mul_undo_add)

# A probe that over-reads item 0 — it also demands a name the item never
# asks for — so it fails on every tree, the group's start included.
FALSE_PROBE = ("from calc import add\n\n\n"
               "def test_add_returns_the_sum():\n"
               "    assert add(2, 3) == 5 and add.__name__ == \"plus\"\n")


def _prompts(r: Replay) -> list[str]:
    return [c["prompt"] if "prompt" in c else c.get("user_prompt", "")
            for c in r.calls if c["schema_key"] == "implementer"]


def test_the_group_that_wrote_the_regression_is_rolled_back_and_redriven(
        leerie, tmp_path, monkeypatch):
    """The 22a30525 shape. Round 1's group fixes item 1 and undoes met item
    0; the re-check after the last round confirms item 0's probe; the probe
    passed on the group's start and fails on its commit under B*, so the
    group is rolled back. Its member still has a round: it goes back to it
    with the probe's case as a `control_regression` note, and the second
    attempt, which keeps item 0, ships with both items met."""
    r = Replay(leerie, tmp_path, monkeypatch, judge_reads_tree=True,
               edit=[_fix_mul_undo_add, _fix_mul])
    r.run()
    rec = r.st.data["delivery_redrive"]
    g1 = rec["rounds"][0]["groups"][0]
    assert g1["rolled_back"] is True
    (flip,) = g1["flip_regressed"]
    assert flip["item_index"] == 0 and flip["pre_sha"] == r.head
    assert flip["b_star"]["new"] == [flip["case_id"]]
    assert "from passing on its start to failing on its commit" in \
        g1["rollback_reason"]
    assert rec["flip_rollbacks"][0]["items"] == [0]
    # The member is told what broke, by the probe's own case.
    p1, p2 = _prompts(r)
    assert "required item 0" not in p1
    assert '"test": "required item 0"' in p2 and flip["case_id"] in p2
    assert '"kind": "control_regression"' in p2
    # Its second attempt is kept, and what ships has both items met.
    g2 = rec["rounds"][1]["groups"][0]
    assert not g2.get("rolled_back") and "flip_regressed" not in g2
    final = _git(r.staging, "rev-parse", "HEAD")
    assert final == g2["post_sha"]
    assert (r.staging / "calc.py").read_text() == FIXED_ADD + "\n\n" + \
        FIXED_MUL
    gate = r.st.data["delivery_gate"]
    assert gate["unmet_final"] == []
    assert {x["item_index"]: x["verdict"] for x in gate["items"]} == {
        0: "met", 1: "met"}
    assert rec["final_sha"] == final


def test_with_no_round_left_the_regression_is_still_not_shipped(
        leerie, tmp_path, monkeypatch):
    """The member has spent its rounds (22a30525's bugfix-004 had): the
    group is rolled back all the same, and the run ships its start, where
    item 1 is unmet as it was before the re-drive — an honest unmet, never
    a regression it wrote."""
    r = Replay(leerie, tmp_path, monkeypatch, judge_reads_tree=True,
               edit=[_fix_mul_undo_add])
    r.caps["subtask_redrive_rounds"] = 1
    r.run()
    rec = r.st.data["delivery_redrive"]
    assert len([c for c in r.calls if c["schema_key"] == "implementer"]) == 1
    g1 = rec["rounds"][0]["groups"][0]
    assert g1["rolled_back"] is True and g1["flip_regressed"]
    assert _git(r.staging, "rev-parse", "HEAD") == r.head
    assert (r.staging / "calc.py").read_text() == FIXED_ADD + "\n\n" + \
        BUGGY_MUL
    gate = r.st.data["delivery_gate"]
    assert [u["item_index"] for u in gate["unmet_final"]] == [1]
    row0 = next(x for x in gate["items"] if x["item_index"] == 0)
    assert row0["verdict"] == "met"
    assert [n["key"] for n in rec["pending_controls"]] == ["met-item-0"]


def test_a_probe_failing_on_the_groups_start_too_is_a_verdict_only(
        leerie, tmp_path, monkeypatch):
    """A confirmed probe that over-reads the item (barnacle 22a30525 item 8:
    it fails on the base and on every commit) fails on the group's start
    too: no group wrote it, so nothing is rolled back and the probe is the
    item's verdict, as before."""
    r = Replay(leerie, tmp_path, monkeypatch, edit=_fix_mul,
               probe0=FALSE_PROBE)
    r.run()
    rec = r.st.data["delivery_redrive"]
    (g,) = rec["rounds"][0]["groups"]
    assert not g.get("rolled_back") and "flip_regressed" not in g
    assert "flip_rollbacks" not in rec
    final = _git(r.staging, "rev-parse", "HEAD")
    assert final == g["post_sha"] != r.head
    gate = r.st.data["delivery_gate"]
    u0 = next(u for u in gate["unmet_final"] if u["item_index"] == 0)
    assert u0["by"] == ["falsifier"]


# A probe that imports what only the group's commit defines: on the start
# it never loads, so its case never runs there.
NEW_NAME_PROBE = ("from calc import add, sub\n\n\n"
                  "def test_add_returns_the_sum():\n"
                  "    assert add(2, 3) == 5 and sub\n")


def _undo_add_and_define_sub(staging) -> None:
    (staging / "calc.py").write_text(
        "def add(a, b):\n    return a - b\n\n\n" + FIXED_MUL
        + "\n\ndef sub(a, b):\n    return a - b\n")
    _git(staging, "commit", "-qam", "fix mul, add sub")


def test_a_start_where_the_probe_never_ran_is_not_a_pass(
        leerie, tmp_path, monkeypatch):
    """The probe's case must pass on the group's start, not merely be
    absent from its failures: on a start where the probe does not load (it
    imports what the group added), no case ran, so no flip is claimed and
    the probe is the item's verdict."""
    r = Replay(leerie, tmp_path, monkeypatch, edit=_undo_add_and_define_sub,
               probe0=NEW_NAME_PROBE)
    r.run()
    rec = r.st.data["delivery_redrive"]
    (g,) = rec["rounds"][0]["groups"]
    assert not g.get("rolled_back") and "flip_regressed" not in g
    assert _git(r.staging, "rev-parse", "HEAD") == g["post_sha"]
    gate = r.st.data["delivery_gate"]
    assert [u["by"] for u in gate["unmet_final"]
            if u["item_index"] == 0] == [["falsifier"]]


def test_the_flip_reads_the_probe_on_both_commits_under_b_star(
        leerie, tmp_path, monkeypatch):
    """`_redrive_probe_flips` on its own, over the real commits: pass on
    the start and fail on the commit is a flip only through B*'s pairs; a
    start where the probe fails is no flip and runs no pairs."""
    r = Replay(leerie, tmp_path, monkeypatch, edit=_fix_mul_undo_add)
    r.run()
    g = r.st.data["delivery_redrive"]["rounds"][0]["groups"][0]
    flip = g["flip_regressed"][0]
    smp = leerie._redrive_flip_probe({"falsifier": {"samples": [{
        "confirmed": True, "case_id": flip["case_id"],
        "probe_saved": flip["probe_saved"]}]}})
    assert smp is not None
    yes = asyncio.run(leerie._redrive_probe_flips(
        r.st, r.caps, smp, g["pre_sha"], g["post_sha"], label="t-yes"))
    assert (yes["pre"], yes["post"], yes["flip"]) == ("pass", "fail", True)
    assert yes["b_star"]["pairs_run"] == r.caps["confirm_pairs"]
    no = asyncio.run(leerie._redrive_probe_flips(
        r.st, r.caps, smp, g["post_sha"], g["post_sha"], label="t-no"))
    assert (no["pre"], no["post"], no["flip"]) == ("fail", "fail", False)
    assert "b_star" not in no


def test_kept_groups_are_the_committed_ones_still_in_history(leerie):
    rec = {"rounds": [
        {"round": 1, "groups": [
            {"group": 0, "pre_sha": "a", "post_sha": "b"},
            {"group": 1, "pre_sha": "b", "post_sha": "b", "no_change": True},
            {"group": 2, "pre_sha": "b", "post_sha": "c",
             "rolled_back": True}]},
        {"round": 2, "groups": [
            {"group": 0, "skipped": "budget"},
            {"group": 1, "pre_sha": "b", "post_sha": "d"}]}]}
    assert [(r_, g_) for r_, g_, _ in leerie._redrive_kept_groups(rec)] == [
        (1, 0), (2, 1)]


def test_a_flip_needs_a_confirmed_saved_probe(leerie):
    ok = {"confirmed": True, "case_id": "f :: t",
          "probe_saved": {"dir": "/d", "file": "f.py"}}
    assert leerie._redrive_flip_probe({"falsifier": {"samples": [
        {"confirmed": False, "case_id": "x", "probe_saved": ok["probe_saved"]},
        ok]}}) is ok
    for bad in ({**ok, "confirmed": False}, {**ok, "case_id": None},
                {**ok, "probe_saved": None},
                {**ok, "probe_saved": {"dir": "/d"}}):
        assert leerie._redrive_flip_probe(
            {"falsifier": {"samples": [bad]}}) is None
    assert leerie._redrive_flip_probe(None) is None
    note = leerie._redrive_flip_note(0, "add returns the sum", {
        **ok, "assertion_quote": "assert add(2, 3) == 5",
        "failure_message": "assert -1 == 5"})
    assert note["kind"] == "control_regression"
    assert note["key"] == "met-item-0"
    assert "`f :: t` passed on the tree before the attempt and failed" in \
        note["detail"]
    assert note["detail"].index("Keep it met: add returns the sum") < \
        note["detail"].index("assert add(2, 3) == 5") < \
        note["detail"].index("assert -1 == 5")
    json.dumps(note)
