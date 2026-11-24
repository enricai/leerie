"""A scheduled escalation is bounded by its own record, not its owners'
round counter (DESIGN §8 *Delivery re-drive* rule 8's second exception).

Barnacle batch 2, #795 (leerie 0.95.0): round 1 scheduled item 6's keep-both
swing; its only owner had spent both re-drive rounds (the wave-end re-drive
and round 1), so round 2's group was skipped for `budget` with most of the
wall time left, and the swing never ran. Every escalation is scheduled by a
failed round of its owners, which has just drawn their counter, so gating it
on that counter makes it unreachable whenever that round was their last.

These drive the real `_run_delivery_redrive` with the owners' counter set so
round 1 is their last (`subtask_redrive_rounds = 1`) — the existing keep-both
and base-form tests run with rounds to spare, which is why they never saw it.

Nor is an escalation drawn from the counter when one is left. Barnacle batch
2, #748: round 2's base-form opus attempt, driven alone, drew bugfix-001's
second round, so round 3's unmet items 2 and 5 (the same owner's) were
skipped for `budget` with wall time left.
"""
from __future__ import annotations

import json

from tests.conftest import run_git_cwd_first_stdout as _gitc
from tests.conftest import run_git_out as _git
from tests.test_base_form_redrive import CASE_A, _driven, _world
from tests.test_keep_both_redrive import _drive, _inject_verdict
from tests.test_shown_probe_contest import (  # noqa: F401  (fixture import)
    FITTED,
    HONEST,
    World,
    _groups,
    _quiet_host,
)


def test_keep_both_swing_runs_when_the_round_that_scheduled_it_was_the_last(
        leerie, tmp_path, monkeypatch):
    w = World(leerie, tmp_path)
    _inject_verdict(leerie, monkeypatch, "both_can_hold")
    w.edits = [lambda w: w.commit({"calc.py": HONEST}),
               lambda w: w.commit({"calc.py": FITTED})]
    rec, calls = _drive(w, leerie, monkeypatch, rounds=1)
    g1, g2 = _groups(rec)
    assert g1.get("keep_both_scheduled") == [0] and g1["rolled_back"] is True
    # Round 2: the owner's one round is spent, yet the swing is driven — on
    # opus/high, admitted as an escalation, not skipped for budget.
    assert g2.get("skipped") is None
    assert g2.get("escalation_swing") is True
    assert len(calls) == 2
    assert (calls[1]["model"], calls[1]["effort"]) == ("opus", "high")
    assert g2["probes_after"] == {"0": "pass"} and not g2.get("rolled_back")
    assert _git(w.staging, "show", "HEAD:calc.py") == FITTED.rstrip("\n")
    assert w.st.data["delivery_gate"]["unmet_final"] == []
    assert rec["keep_both_driven"]["keep_both:0"]["attempts"][-1][
        "outcome"] == "kept"
    # The swing drew no round: the owner's counter holds round 1 only.
    assert list(w.st.data["redrive_rounds"].values()) == [1]


def test_a_spent_swing_is_not_admitted_again(leerie, tmp_path, monkeypatch):
    """Its record is the bound: a swing that still breaks the pinned case is
    spent, the probe contests, and nothing drives the item a third time even
    though the escalation drew past the owner's cap."""
    w = World(leerie, tmp_path)
    _inject_verdict(leerie, monkeypatch, "both_can_hold")
    w.edits = [lambda w: w.commit({"calc.py": HONEST}),
               lambda w: w.commit({"calc.py": HONEST + "# again\n"}),
               lambda w: w.commit({"calc.py": FITTED})]
    rec, calls = _drive(w, leerie, monkeypatch, rounds=1)
    assert len(calls) == 2
    assert rec["keep_both_driven"]["keep_both:0"]["attempts"][-1][
        "outcome"] == "not_kept"
    assert [c["item_index"] for c in rec["contested_probes"]] == [0]
    assert sum(1 for g in _groups(rec) if g.get("escalation_swing")) == 1
    assert [u["item_index"] for u in
            w.st.data["delivery_gate"]["unmet_final"]] == [0]


def test_base_form_opus_attempt_runs_when_the_first_was_the_last_round(
        leerie, tmp_path, monkeypatch):
    w = _world(leerie, tmp_path, monkeypatch)
    w.caps["subtask_redrive_rounds"] = 1
    start = _gitc(w.staging, "rev-parse", "HEAD")
    w.edits = [lambda w: w.write("export const a = 1; // other\n"),
               lambda w: w.write("export const a = 1; // PINNED\n")]
    w.run()
    assert w.models == ["sonnet", "opus"]
    first, second = _driven(w)["attempts"]
    assert first["outcome"] == "rolled_back"
    assert second["escalation"] is True and second["outcome"] == "kept"
    assert _driven(w)["fixed"] == [CASE_A]
    groups = [g for r in w.st.data["delivery_redrive"]["rounds"]
              for g in r["groups"]]
    assert [g.get("escalation_swing") for g in groups
            if not g.get("skipped")] == [None, True]
    assert _gitc(w.staging, "rev-parse", "HEAD") != start
    # The opus attempt drew no round: the counter holds the first only.
    assert sum(w.st.data["redrive_rounds"].values()) == 1


def test_an_escalation_leaves_its_owners_round_to_their_other_work(
        leerie, tmp_path, monkeypatch):
    """The #748 shape: one owner holds a base-form note and an unmet item.
    Round 1 drives both and fixes neither (rolled back); round 2 is the
    base-form opus attempt, alone, and keeps the case; round 3 still has the
    owner's second round for the item, and its fix is kept."""
    w = _world(leerie, tmp_path, monkeypatch)
    w.caps["subtask_redrive_rounds"] = 2
    item = "a does X"
    w.st.data["required_items"] = [{"item": item, "span": "S1",
                                    "verifiability": "tree"}]
    w.st.data["delivery_gate"]["unmet_final"] = [
        {"item_index": 0, "item": item, "evidence": "missing",
         "by": ["judge"]}]
    w.st.data["delivery_gate"]["items"] = [
        {"item_index": 0, "item": item, "verdict": "unmet", "by": ["judge"]}]
    plan_p = w.st.run_dir / "plan.json"
    plan = json.loads(plan_p.read_text())
    plan["subtasks"]["s1"]["claims"]["items"] = [
        {"item_index": 0, "kind": "deliverable"}]
    plan_p.write_text(json.dumps(plan))
    w.st.save()

    async def judge(task, its, staging, st, caps, models, efforts,
                    phase_label, **kw):
        text = _gitc(staging, "show", "HEAD:src/a.ts")
        return ([] if "DONE" in text else
                [{"item_index": 0, "item": item, "evidence": "missing"}]
                ), 1, None
    monkeypatch.setattr(leerie, "_delivery_judge_unmet", judge)
    monkeypatch.setattr(leerie, "_delivery_record", lambda *a, **k: "")
    monkeypatch.setattr(leerie, "_delivery_typed_evidence",
                        lambda *a, **k: ([], {}))
    w.edits = [lambda w: w.write("export const a = 1; // other\n"),
               lambda w: w.write("export const a = 1; // PINNED\n"),
               lambda w: w.write("export const a = 1; // PINNED DONE\n")]
    w.run()
    rec = w.st.data["delivery_redrive"]
    driven = [g for r in rec["rounds"] for g in r["groups"]
              if not g.get("skipped")]
    assert [sorted(g["notes"]) for g in driven] == [
        ["base_form_case", "unmet_item"], ["base_form_case"], ["unmet_item"]]
    assert w.models == ["sonnet", "opus", "sonnet"]
    assert not any(g.get("skipped") == "budget" for r in rec["rounds"]
                   for g in r["groups"])
    assert w.st.data["redrive_rounds"] == {"s1": 2}
    assert w.st.data["delivery_gate"]["unmet_final"] == []
    assert "DONE" in w.a_text("HEAD")


def test_wall_time_still_binds_an_escalation(leerie, tmp_path, monkeypatch):
    """No round counter, but the clock: with the re-drive's time spent the
    scheduled swing is not driven, and the group is the `budget` case."""
    w = World(leerie, tmp_path)
    _inject_verdict(leerie, monkeypatch, "both_can_hold")
    real = leerie._redrive_keep_both_schedule

    def schedule(*a, **k):
        got = real(*a, **k)
        if got:  # the clock runs out as round 1 ends
            w.st.data["redrive_spent_sec"] = 10 ** 9
        return got
    monkeypatch.setattr(leerie, "_redrive_keep_both_schedule", schedule)
    w.edits = [lambda w: w.commit({"calc.py": HONEST}),
               lambda w: w.commit({"calc.py": FITTED})]
    rec, calls = _drive(w, leerie, monkeypatch, rounds=1)
    assert len(calls) == 1
    assert not any(g.get("escalation_swing") for g in _groups(rec))
    assert rec["keep_both_driven"] == {} or all(
        a["outcome"] == "interrupted" for d in rec["keep_both_driven"].values()
        for a in d["attempts"])


def test_escalation_open_reads_the_escalation_mark(leerie):
    f = leerie._redrive_escalation_open
    assert f([{"kind": "keep_both_case", "escalation": True}])
    assert f([{"kind": "unmet_item"},
              {"kind": "base_form_case", "escalation": True}])
    assert not f([{"kind": "base_form_case"}, {"kind": "unmet_item"}])
    assert not f([])
