"""A keep-both swing before a reconcilable probe is contested (DESIGN §8
*Delivery re-drive* rule 4, *A keep-both swing before a reconcilable probe is
contested*).

When making a required item's probe pass broke a case an existing test pins
AND the contradiction judge found both CAN hold (`both_can_hold`), the probe
is not contested at once: its item gets exactly one escalated keep-both swing
on `MODEL_KEEP_BOTH_ESCALATION` at `EFFORT_KEEP_BOTH_ESCALATION` before the
conflict rule contests it. The swing is bounded to one per item; a swing that
still breaks the pinned case contests on the normal path, exactly as it would
have without the swing — so the swing can only convert a reconcilable conflict
the escalation resolves, never add a retry to one it cannot.

The behavioural tests drive the real `_run_delivery_redrive` over the real
git repository and real pytest of `test_shown_probe_contest`'s World (the same
probe-re-run, failing-test-ID B* confirmation, base-form check). Only
`claude_p` (the implementer) and `_contradiction_verdict` are stubbed: the
real contradiction judge reads TS assertion spans and answers `unsettled` for
this Python World, so the judge's `both_can_hold`/`unsettled` verdict is
injected directly, recorded in `rec` exactly as the real path records it.
"""
from __future__ import annotations

import asyncio

import pytest

from tests.conftest import run_git_out as _git
from tests.test_shown_probe_contest import (  # noqa: F401  (fixture import)
    FITTED,
    HONEST,
    PIN_ID,
    PIN_REL,
    PROBE_REL,
    World,
    _groups,
    _quiet_host,
)


def _inject_verdict(leerie, monkeypatch, status: str) -> None:
    """Make the contradiction judge answer `status` for every (pinned case,
    probe) pair, recording the verdict in `rec` as `_contradiction_verdict`
    does. The intent resolver is a no-op (consulted only on `unsettled`, and
    these tests never want an intent `yes` to make a case task-required)."""
    async def verdict(st, caps, models, efforts, staging, rec, *, base,
                      case, item_index, probe, rnd, gi):
        v = {"case": str(case.get("id") or ""), "file": case.get("file"),
             "item_index": item_index, "probe_test_file": probe.get("file"),
             "round": rnd, "group": gi, "status": status}
        rec.setdefault("contradiction_verdicts", []).append(v)
        return v

    async def intent(st, caps, models, efforts, v):
        return None
    monkeypatch.setattr(leerie, "_contradiction_verdict", verdict)
    monkeypatch.setattr(leerie, "_contradiction_intent", intent)


def _drive(w, leerie, monkeypatch, *, rounds: int = 3):
    """Run the real re-drive, recording each implementer call's model, effort
    and prompt. Returns `(rec, calls)`."""
    calls: list[dict] = []

    async def fake_claude_p(**kw):
        assert kw["schema_key"] == "implementer", kw["schema_key"]
        calls.append({"model": kw["model"], "effort": kw["effort"],
                      "prompt": kw["user_prompt"]})
        w.kw = kw
        edit = w.edits.pop(0) if w.edits else None
        if edit:
            edit(w)
        ans = w.answers.pop(0) if w.answers else {}
        return {"status": "complete", "summary": "done", **ans}
    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    caps = w._caps()
    caps["subtask_redrive_rounds"] = rounds
    asyncio.run(leerie._run_delivery_redrive(
        w.st.run_dir, w.st, caps, {"implementer": "sonnet"},
        {"implementer": "low"}))
    return w.st.data["delivery_redrive"], calls


# --- behavioural, over the real loop -------------------------------------

def test_reconcilable_probe_gets_one_keep_both_swing_then_keeps_both(
        leerie, tmp_path, monkeypatch):
    """Round 1 makes the probe pass (HONEST) and breaks the pinned case; the
    judge says both_can_hold, so the probe is scheduled for one keep-both
    swing, NOT contested, and the group is rolled back. Round 2 drives that
    swing on opus/high; its commit (FITTED) makes the probe pass AND keeps
    the pinned case, so it is kept and the item is met."""
    w = World(leerie, tmp_path)
    _inject_verdict(leerie, monkeypatch, "both_can_hold")
    w.edits = [lambda w: w.commit({"calc.py": HONEST}),
               lambda w: w.commit({"calc.py": FITTED})]
    rec, calls = _drive(w, leerie, monkeypatch, rounds=3)
    # Exactly two drives: the probe, then the one keep-both swing. A kept
    # swing credits the item met in its own round — no wasted third drive.
    g1, g2 = _groups(rec)
    # Round 1: scheduled, not contested, rolled back.
    assert g1["probes_after"] == {"0": "pass"}
    assert g1["rolled_back"] is True
    assert g1.get("keep_both_scheduled") == [0]
    assert "probe_conflict" not in g1
    assert rec["contested_probes"] == []
    assert [n["item_index"] for n in rec["keep_both_open"]] == [0]
    assert rec["keep_both_open"][0]["keep_pinned"]  # names the pinned case(s)
    assert any(PIN_ID in c for c in rec["keep_both_open"][0]["keep_pinned"])
    # Round 2: the swing ran on opus/high (never the sonnet/low default).
    assert len(calls) == 2
    assert calls[1]["model"] == leerie.MODEL_KEEP_BOTH_ESCALATION == "opus"
    assert calls[1]["effort"] == leerie.EFFORT_KEEP_BOTH_ESCALATION == "high"
    # The prompt showed it as a keep_both_case with the probe and keep_pinned.
    assert "keep_both_case" in calls[1]["prompt"]
    assert PROBE_REL in calls[1]["prompt"]
    # The swing kept both.
    assert g2["probes_after"] == {"0": "pass"}
    assert not g2.get("rolled_back")
    assert _git(w.staging, "show", "HEAD:calc.py") == FITTED.rstrip("\n")
    assert w.st.data["delivery_gate"]["unmet_final"] == []
    # Bounded: the pair is recorded driven and kept.
    d = rec["keep_both_driven"]["keep_both:0"]
    assert d["attempts"][-1]["outcome"] == "kept"
    assert d["attempts"][-1]["model"] == "opus"


def test_a_swing_that_still_breaks_the_pin_contests_and_is_not_repeated(
        leerie, tmp_path, monkeypatch):
    """Regression-safety: the swing is bounded to one. Round 1 schedules it;
    round 2's swing (HONEST again) makes the probe pass but still breaks the
    pinned case. The item is already driven, so it is NOT re-scheduled — it
    contests on the normal path, exactly as it would have in round 1 without
    the swing. Net cost of the mechanism on an irreconcilable-in-practice
    conflict: one extra escalated round, then the same contest."""
    w = World(leerie, tmp_path)
    _inject_verdict(leerie, monkeypatch, "both_can_hold")
    w.edits = [lambda w: w.commit({"calc.py": HONEST}),
               lambda w: w.commit({"calc.py": HONEST + "# again\n"})]
    rec, calls = _drive(w, leerie, monkeypatch, rounds=4)
    gs = _groups(rec)
    assert gs[0].get("keep_both_scheduled") == [0]
    # Exactly one swing was ever scheduled.
    assert sum(1 for g in gs if g.get("keep_both_scheduled")) == 1
    # Round 2 ran the swing on opus/high and it did not keep both.
    assert len(calls) == 2
    assert calls[1]["model"] == "opus" and calls[1]["effort"] == "high"
    assert rec["keep_both_driven"]["keep_both:0"][
        "attempts"][-1]["outcome"] == "not_kept"
    # The probe is now contested — the item is not driven a third time.
    assert [c["item_index"] for c in rec["contested_probes"]] == [0]
    assert gs[1].get("probe_conflict", {}).get("items") == [0]
    assert [u["item_index"] for u in
            w.st.data["delivery_gate"]["unmet_final"]] == [0]


def test_an_unsettled_probe_gets_no_swing_and_contests_at_once(
        leerie, tmp_path, monkeypatch):
    """Regression-safety: only a `both_can_hold` verdict earns a swing. An
    `unsettled` conflict (the judge could not verify a joint implementation)
    gets no keep-both note and contests at once, exactly as before this
    mechanism — no extra escalated round spent on a conflict the judge did
    not find reconcilable."""
    w = World(leerie, tmp_path)
    _inject_verdict(leerie, monkeypatch, "unsettled")
    w.edits = [lambda w: w.commit({"calc.py": HONEST})]
    rec, calls = _drive(w, leerie, monkeypatch, rounds=3)
    [g] = _groups(rec)
    assert g["probes_after"] == {"0": "pass"}
    assert g["rolled_back"] is True
    assert "keep_both_scheduled" not in g
    assert rec["keep_both_open"] == []
    assert rec["keep_both_driven"] == {}
    assert [c["item_index"] for c in rec["contested_probes"]] == [0]
    assert len(calls) == 1


# --- pure-function guards (fast, no World) --------------------------------

def _note(i: int = 0):
    return {"kind": "unmet_item", "item_index": i, "item": f"item {i}",
            "claimed_by": ["s1"],
            "probe": {"dir": "/d", "file": f"p{i}.test.ts",
                      "case_name": "c", "reason": "r"},
            "evidence": "e"}


def test_schedule_only_when_every_broken_case_is_both_can_hold(leerie):
    """All of an item's broken pinned cases must be `both_can_hold`; one
    `unsettled`/`exclusive`/unjudged case means no clean joint target, so no
    swing — it contests."""
    pinned = [{"id": "f.ts :: a"}, {"id": "f.ts :: b"}]
    rec = {"contradiction_verdicts": [
        {"item_index": 0, "case": "f.ts :: a", "status": "both_can_hold"},
        {"item_index": 0, "case": "f.ts :: b", "status": "unsettled"}]}
    sched = leerie._redrive_keep_both_schedule(
        rec, [_note(0)], [0], pinned, rnd=1, gi=0)
    assert sched == set()
    assert rec.get("keep_both_open", []) == []
    # Flip the second to both_can_hold: now it schedules, keeping both cases.
    rec["contradiction_verdicts"][1]["status"] = "both_can_hold"
    sched = leerie._redrive_keep_both_schedule(
        rec, [_note(0)], [0], pinned, rnd=1, gi=0)
    assert sched == {0}
    [n] = rec["keep_both_open"]
    assert n["kind"] == "keep_both_case" and n["key"] == "keep_both:0"
    assert n["escalation"] is True and n["claimed_by"] == ["s1"]
    assert n["keep_pinned"] == ["f.ts :: a", "f.ts :: b"]
    # A pinned case left UNJUDGED — no verdict at all, because
    # `_redrive_task_required` short-circuits at the first case no item is
    # task-required for, so later cases get none — is not confirmed
    # reconcilable: no swing (testing only the verdicts that exist, as the
    # gate did before, would schedule on the lone both_can_hold).
    rec2 = {"contradiction_verdicts": [
        {"item_index": 0, "case": "f.ts :: a", "status": "both_can_hold"}]}
    assert leerie._redrive_keep_both_schedule(
        rec2, [_note(0)], [0], pinned, rnd=1, gi=0) == set()
    assert rec2.get("keep_both_open", []) == []


def test_schedule_is_bounded_to_one_per_item(leerie):
    pinned = [{"id": "f.ts :: a"}]
    rec = {"contradiction_verdicts": [
        {"item_index": 0, "case": "f.ts :: a", "status": "both_can_hold"}],
        "keep_both_driven": {"keep_both:0": {"attempts": [{"outcome": "x"}]}}}
    # Already driven: never scheduled again.
    assert leerie._redrive_keep_both_schedule(
        rec, [_note(0)], [0], pinned, rnd=2, gi=0) == set()
    assert rec.get("keep_both_open", []) == []


def test_keep_both_open_offers_until_one_finished_attempt(leerie):
    note = {"kind": "keep_both_case", "key": "keep_both:0", "item_index": 0}
    rec = {"keep_both_open": [note], "keep_both_driven": {}}
    assert leerie._redrive_keep_both_open(rec) == [note]
    # An interrupted attempt does not finish it.
    rec["keep_both_driven"]["keep_both:0"] = {"attempts": [
        {"outcome": "interrupted"}]}
    assert leerie._redrive_keep_both_open(rec) == [note]
    # One finished attempt closes it.
    rec["keep_both_driven"]["keep_both:0"]["attempts"].append(
        {"outcome": "not_kept"})
    assert leerie._redrive_keep_both_open(rec) == []


def test_route_table_and_open_rule(leerie):
    r = leerie._REDRIVE_ROUTES["keep_both_case"]
    assert r == {"owner": "claimers", "open": "keep_both", "runnable": None}
    n = {"kind": "keep_both_case", "key": "keep_both:0"}
    assert leerie._redrive_open_at_start(
        n, failing=(), held=(), cleared=(), closed=(), base_forms_open=(),
        round_bf=(), keep_both_open={"keep_both:0"}, round_kb=()) is True
    # Driven this round: no longer open.
    assert leerie._redrive_open_at_start(
        n, failing=(), held=(), cleared=(), closed=(), base_forms_open=(),
        round_bf=(), keep_both_open={"keep_both:0"},
        round_kb={"keep_both:0"}) is False


def test_probe_sets_collects_keep_both_notes(leerie):
    """Hook that makes the swing's probe measurable: a keep_both_case note's
    probe joins the self-check sets, so its re-run produces a `probes_after`
    verdict (without it a kept swing would read as zero progress)."""
    kb = {"kind": "keep_both_case", "item_index": 3,
          "probe": {"dir": "/d", "file": "p3.test.ts"}}
    [s] = leerie._redrive_probe_sets([kb])
    assert s["item_index"] == 3 and s["defect_files"] == ["p3.test.ts"]


def test_note_renders_the_keep_both_clause_and_keep_pinned(leerie):
    n = {"kind": "keep_both_case", "key": "keep_both:0", "item_index": 0,
         "item": "add returns the sum",
         "probe": {"file": "p.test.ts", "case_name": "sums",
                   "reason": "subtracts"},
         "keep_pinned": ["t.ts :: pins zero"]}
    text = leerie._redrive_note(
        [n], rnd=1, rounds=2, merged=[], held_names=[],
        defect_shape="", selfcheck=None)
    assert "kind=keep_both_case means" in text
    assert "t.ts :: pins zero" in text          # the keep_pinned case is shown
    assert "p.test.ts" in text                   # the probe is shown
    assert "never weaken, edit or delete the pinned test" in text
