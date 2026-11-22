"""An unmet item reaches its owner, or is recorded as reaching none (DESIGN
§8 *Delivery re-drive*).

Barnacle a192d275: the falsifier confirmed an item unmet whose only
claimer, feat-001, had settled `unconverged`. The re-drive left unconverged
subtasks out of its routing, so the item went to nobody and the re-drive
logged "done" one second later, with nothing recording that the finding
reached no worker. (That item's only evidence was a falsifier probe, which
rule 4 now records `unreproducible` before routing; the routing tests here
use a judge-unmet item, which is still driven.)

Barnacle 4b5c564d (leerie 0.44.10): an item unmet on a confirmed falsifier
probe alone was handed to two re-drive rounds whose implementers could
neither see the probe (held-out content, DESIGN §6) nor reproduce it with
their self-check; both changed nothing. Rule 4 now covers every note.
"""
from __future__ import annotations

import json
import types

import pytest

from tests.test_delivery_redrive import (World, _git,
                                         _note_work_and_selfcheck)

ITEMS = [{"item": "a does X", "span": "S1", "verifiability": "tree"}]
UNMET = [{"item_index": 0, "item": "a does X", "evidence": "missing",
          "by": ["judge"]}]


def _world(leerie, tmp_path, monkeypatch, status: str):
    w = World(leerie, tmp_path, n_sets=0, items=ITEMS,
              unmet=[dict(u) for u in UNMET],
              claims={"s1": [{"item_index": 0, "kind": "deliverable"}]})
    w.st.data["subtask_status"] = {"s1": status, "s2": "complete"}
    w.install(monkeypatch)

    async def still_unmet(*a, **k):
        return {0: {"unmet": True, "by": ["judge"], "evidence": "missing"}}
    monkeypatch.setattr(leerie, "_redrive_recheck_items", still_unmet)
    return w


def test_an_unconverged_claimer_is_driven(leerie, tmp_path, monkeypatch):
    w = _world(leerie, tmp_path, monkeypatch, "unconverged")
    w.edits = [lambda w: w.write("export const a = 1; // tried\n")]
    w.run()
    assert w.calls, "the item's only claimer was never handed it"
    assert w.calls[0]["prompt"].startswith("Execute subtask `s1`.")
    assert "a does X" in w.calls[0]["prompt"]
    assert "unrouted_items" not in w.st.data["delivery_redrive"]


def test_a_rounds_spent_claimer_still_swings_at_a_never_handed_item(
        leerie, tmp_path, monkeypatch):
    """DESIGN §8 rule 8: a claimer whose shared re-drive rounds are spent is
    still admitted for one swing when it holds a drivable evidence key no
    worker was handed, bounded only by the wall clock. Edit (b): the member
    gate admits the group (`extra_swing`) rather than stranding the item on
    `budget` when a round could have attempted it."""
    w = _world(leerie, tmp_path, monkeypatch, "unconverged")
    cap = int(w.caps["subtask_redrive_rounds"])
    w.st.data["redrive_rounds"] = {"s1": cap}   # rounds spent; time remains
    w.edits = [lambda w: w.write("export const a = 1; // tried\n")]
    w.run()
    rec = w.st.data["delivery_redrive"]
    assert [c["sid"] for c in w.calls] == ["delivery-redrive-r1-g0"]
    assert w.calls[0]["prompt"].startswith("Execute subtask `s1`.")
    assert rec["rounds"][0]["groups"][0].get("extra_swing") is True
    assert rec["items_driven"] == [0]
    assert rec["evidence_handed"] == ["0:judge"]
    # Driven, so it reached a worker and is never recorded unrouted.
    assert "unrouted_items" not in rec


def test_a_time_spent_claimer_leaves_a_typed_unrouted_item(
        leerie, tmp_path, monkeypatch):
    """The genuine `budget` case after the fix: the wall clock, not the round
    count, is the limiter. With `redrive_budget_sec` spent on entry, no swing
    is taken and the never-handed item is recorded `budget`."""
    w = _world(leerie, tmp_path, monkeypatch, "unconverged")
    w.st.data["redrive_rounds"] = {"s1": int(w.caps["subtask_redrive_rounds"])}
    w.st.data["redrive_spent_sec"] = float(w.caps["redrive_budget_sec"])
    w.run()
    assert w.calls == []
    rec = w.st.data["delivery_redrive"]
    assert rec["unrouted_items"] == [
        {"item_index": 0, "item": "a does X", "reason": "budget"}]
    assert w.L._decide_outcome(w.st, any_commits=True) == "unconverged"
    assert any("unrouted_item" in r and "item 0 (budget)" in r
               for r in w.st.data["outcome_reasons"])


def test_the_round_ceiling_is_non_binding_for_never_handed_work(
        leerie, tmp_path, monkeypatch):
    """Edit (c): `rnd > max_rounds` does not stop a first attempt at a
    never-handed drivable key. With the round counter spent and one round
    already recorded, the first new round exceeds the ceiling, but the item
    still gets its swing — the budget check remains the real stop."""
    w = _world(leerie, tmp_path, monkeypatch, "unconverged")
    w.caps["subtask_redrive_rounds"] = 0        # max_rounds = 0 * 2 + 1 = 1
    # One round already on record, so the first new round is rnd = 2 > 1.
    w.st.data["delivery_redrive"] = {"rounds": [{"round": 1, "groups": []}]}
    w.st.save()
    w.edits = [lambda w: w.write("export const a = 1; // tried\n")]
    w.run()
    rec = w.st.data["delivery_redrive"]
    assert [c["sid"] for c in w.calls] == ["delivery-redrive-r2-g0"]
    assert rec["rounds"][-1]["groups"][0].get("extra_swing") is True
    assert rec["items_driven"] == [0]


def test_wall_clock_is_the_only_backstop_against_a_fresh_key_each_check(
        leerie, tmp_path, monkeypatch):
    """Termination rests solely on `redrive_budget_sec`. A non-deterministic
    falsifier minting a fresh evidence key every re-check keeps
    `_redrive_never_handed` True forever, so the round count never stops the
    loop; only the wall clock does, after one charged swing. With the budget
    all but spent on entry, exactly one swing runs, then the budget check
    breaks — never a runaway."""
    w = _world(leerie, tmp_path, monkeypatch, "unconverged")
    w.st.data["redrive_rounds"] = {"s1": int(w.caps["subtask_redrive_rounds"])}
    n = {"k": 0}

    def fresh_notes(unmet_final, claims, n_items, rows, **kw):
        n["k"] += 1
        key = f"judge-{n['k']}"       # a new key every call: never handed
        return ([{"kind": "unmet_item", "key": "item-0", "item_index": 0,
                  "item": "a does X", "evidence": "x", "by": ["judge"],
                  "claimed_by": ["s1"], "evidence_keys": [key], "keys": [key],
                  "undriven": {"reason": "no_code_target", "probed": ""}}], [])
    monkeypatch.setattr(leerie, "_redrive_item_notes", fresh_notes)
    budget = float(w.caps["redrive_budget_sec"])
    w.st.data["redrive_spent_sec"] = budget - 0.001
    w.st.save()
    w.edits = [lambda w: w.write("export const a = 1; // tried\n")]
    w.run()
    rec = w.st.data["delivery_redrive"]
    assert len(w.calls) == 1
    assert float(w.st.data["redrive_spent_sec"]) >= budget
    # Still a never-handed key at the end: the budget stopped it, not a
    # handed-key fixpoint.
    assert [u["reason"] for u in rec["unrouted_items"]] == ["budget"]


def test_an_already_handed_key_gets_no_extra_swing(
        leerie, tmp_path, monkeypatch):
    """The structural exclusion of the barnacle `budget` cases: a key already
    in `evidence_handed` (a fix that did not pass) grants no further swing.
    The member gate's `_redrive_never_handed` is False, so the group still
    falls through to `skipped = "budget"` — the fix leaves it untouched."""
    w = _world(leerie, tmp_path, monkeypatch, "unconverged")
    w.st.data["redrive_rounds"] = {"s1": int(w.caps["subtask_redrive_rounds"])}
    w.st.data["delivery_redrive"] = {"evidence_handed": ["0:judge"]}
    w.st.save()
    w.run()
    rec = w.st.data["delivery_redrive"]
    assert w.calls == []
    g = rec["rounds"][0]["groups"][0]
    assert "extra_swing" not in g and g["skipped"] == "budget"
    assert rec["items_budget_skipped"] == [0]


def test_resume_does_not_re_grant_a_swing_for_a_handed_key(
        leerie, tmp_path, monkeypatch):
    """`evidence_handed` is persisted, so a resume after a swing recorded a
    key does not hand that key a second swing (DESIGN §8 rule 8: the handed
    set only grows, each key swings at most once)."""
    w = _world(leerie, tmp_path, monkeypatch, "unconverged")
    w.st.data["redrive_rounds"] = {"s1": int(w.caps["subtask_redrive_rounds"])}
    w.edits = [lambda w: w.write("export const a = 1; // tried\n")]
    w.run()
    rec = w.st.data["delivery_redrive"]
    assert len(w.calls) == 1 and rec["evidence_handed"] == ["0:judge"]
    assert rec["items_driven"] == [0]
    # Resume: the record persists and `done` is re-opened as a crash would
    # leave it; the handed key is not re-granted a swing.
    rec.pop("done")
    w.st.save()
    w.calls.clear()
    w.run()
    assert w.calls == []
    assert w.st.data["delivery_redrive"]["evidence_handed"] == ["0:judge"]


def test_a_blocked_only_claimer_is_no_owner(leerie, tmp_path, monkeypatch):
    w = _world(leerie, tmp_path, monkeypatch, "blocked")
    # The fallback is every drivable subtask; with s2 accepted out too,
    # nothing may take the item.
    w.st.data["accepted_blocked"] = {"s2": {"reason": "x"}}
    w.run()
    assert w.calls == []
    assert w.st.data["delivery_redrive"]["unrouted_items"] == [
        {"item_index": 0, "item": "a does X", "reason": "no_owner"}]


@pytest.mark.parametrize("reason", ["budget", "no_owner", "unreproducible"])
def test_the_next_run_is_handed_the_unrouted_item(leerie, tmp_path, reason):
    runs = tmp_path / "runs"
    prev = runs / "prev"
    prev.mkdir(parents=True)
    (prev / "state.json").write_text(json.dumps({
        "task": "t",
        "delivery_gate": {"unmet_final": UNMET},
        "delivery_redrive": {"unrouted_items": [
            {"item_index": 0, "item": "a does X", "reason": reason}]}}))
    (prev / "orchestrator.exit_code").write_text("0")
    (runs / "cur").mkdir()
    st = types.SimpleNamespace(data={"task": "t"}, run_dir=runs / "cur")
    got = leerie._prior_delivery_residual(st)
    assert got["unrouted_items"] == [{"item": "a does X", "reason": reason}]


def _open(i, item, keys, drivable=None, reason="unreproducible",
          probed=""):
    return {"item_index": i, "item": item, "keys": keys,
            "drivable": keys if drivable is None else drivable,
            "reason": reason, "probed": probed}


def test_unrouted_items_is_pure_and_typed(leerie):
    items = [_open(0, "x", ["judge"]), _open(1, "y", ["judge"]),
             _open(2, "z", ["judge"])]
    assert leerie._redrive_unrouted_items(items, ["1:judge"], [2],
                                          False) == [
        {"item_index": 0, "item": "x", "reason": "no_owner"},
        {"item_index": 2, "item": "z", "reason": "budget"}]
    assert [u["reason"] for u in leerie._redrive_unrouted_items(
        items[:1], [], [], True)] == ["budget"]


def test_unrouted_items_records_an_undrivable_part_with_its_own_reason(
        leerie):
    items = [_open(0, "x", ["judge"]),
             _open(3, "w", ["falsifier:aa"], drivable=[],
                   probed="w does V"),
             _open(4, "v", ["judge"], drivable=[], reason="no_code_target"),
             # Driven on its judge part; its probe no fixer may be shown.
             _open(5, "u", ["falsifier:bb", "judge"], drivable=["judge"],
                   probed="u does T")]
    assert leerie._redrive_unrouted_items(
        items, ["0:judge", "5:judge"], [], False) == [
        {"item_index": 3, "item": "w", "reason": "unreproducible",
         "probed": "w does V"},
        {"item_index": 4, "item": "v", "reason": "no_code_target"},
        {"item_index": 5, "item": "u", "reason": "unreproducible",
         "probed": "u does T"}]


def test_a_later_probe_than_the_one_handed_is_unrouted(leerie):
    """Barnacle 87580f62 item 8: driven in two rounds on earlier probes, its
    latest confirmed probe held by a group with no member left a round."""
    items = [_open(8, "x", ["falsifier:new"])]
    handed = ["8:falsifier:old1", "8:falsifier:old2"]
    assert leerie._redrive_unrouted_items(
        items, handed, [8], False, seen=["8:falsifier:new"]) == [
        {"item_index": 8, "item": "x", "reason": "budget"}]
    # The same probe handed once is reached, whatever came after.
    assert leerie._redrive_unrouted_items(
        items, handed + ["8:falsifier:new"], [8], False) == []
    # Confirmed after the last round (never open at a round's start).
    assert leerie._redrive_unrouted_items(
        items, handed, [], False, seen=["8:falsifier:old2"]) == [
        {"item_index": 8, "item": "x", "reason": "after_last_round"}]


# === rule 4 over item notes: the 4b5c564d shape =============================

QUOTE = "must evaluate only fields declared on the payload schema"
PROBE_EVIDENCE = (f"a falsifier probe of the item's words \"{QUOTE}\" fails "
                  "under B* on the final tree")
JUDGE_EVIDENCE = "no guard reads the payload schema at src/a.ts:1"


def _falsifier_row(i: int) -> dict:
    return {"item_index": i, "verdict": "unmet", "by": ["falsifier"],
            "falsifier": {"item_index": i, "confirmed": True, "samples": [
                {"sample": 0, "confirmed": False,
                 "item_span_quote": "not this one"},
                {"sample": 1, "confirmed": True, "item_span_quote": QUOTE,
                 "case_name": "ZQX probe case title",
                 "probe_saved": {"dir": "/nonexistent", "file": "p.test.ts"}}]}}


def _passing_sets_world(leerie, tmp_path, monkeypatch, unmet, rows):
    """Two shown sets, both passing on the detection commit (as in
    4b5c564d's final tree), and one open item."""
    w = World(leerie, tmp_path, n_sets=2, items=ITEMS, unmet=unmet,
              claims={"s1": [{"item_index": 0, "kind": "deliverable"}]})
    w.install(monkeypatch)
    w.write("export const a = 1; // FIXED\n")
    head = _git(w.staging, "rev-parse", "HEAD")
    w.st.data["acceptance"]["gate"] = {"final_sha": head, "final": [
        {"index": s_["index"], "passed": True, "unmeasured": False,
         "failing_files": []} for s_ in w.sets]}
    w.st.data["delivery_gate"].update(
        final_sha=head, final_tree=_git(w.staging, "rev-parse", "HEAD^{tree}"),
        items=rows)
    w.st.save()
    return w


def test_a_falsifier_only_item_is_unrouted_unreproducible_never_driven(
        leerie, tmp_path, monkeypatch):
    unmet = [{"item_index": 0, "item": "a does X", "evidence": PROBE_EVIDENCE,
              "by": ["falsifier"], "evidence_by": {"falsifier":
                                                   PROBE_EVIDENCE}}]
    w = _passing_sets_world(leerie, tmp_path, monkeypatch, unmet,
                            [_falsifier_row(0)])
    spent = []

    async def recheck(*a, **k):
        spent.append(a)
        return {}
    monkeypatch.setattr(leerie, "_redrive_recheck_items", recheck)
    w.run()
    rec = w.st.data["delivery_redrive"]
    # No implementer, no round, no re-check spent on it.
    assert w.calls == [] and rec["rounds"] == [] and not spent
    assert rec["done"]
    assert rec["unrouted_items"] == [
        {"item_index": 0, "item": "a does X", "reason": "unreproducible",
         "probed": QUOTE}]
    # It stays unmet, and the outcome names it and why no worker had it.
    assert [u["item_index"] for u in
            w.st.data["delivery_gate"]["unmet_final"]] == [0]
    assert leerie._decide_outcome(w.st, any_commits=True) == "unconverged"
    reasons = w.st.data["outcome_reasons"]
    assert "1 required item(s) still unmet" in reasons
    assert any("item 0 (unreproducible)" in r for r in reasons)
    why = [r for r in reasons if r.startswith("item 0 is unmet on a "
                                              "confirmed falsifier probe")]
    assert len(why) == 1
    assert f"\"{QUOTE}\"" in why[0]
    assert "held-out content no fixer could see" in why[0]
    # The next run is handed it, typed.
    assert rec["unrouted_items"][0]["reason"] == "unreproducible"


def test_the_pr_says_the_falsifier_found_it_and_no_fixer_saw_the_probe(
        leerie, tmp_path, monkeypatch):
    """The rendered PR end state, not the reason list: the draft section
    names the item, the probed words, and that the probe was not shown —
    and none of the probe's own text (its case title)."""
    unmet = [{"item_index": 0, "item": "a does X", "evidence": PROBE_EVIDENCE,
              "by": ["falsifier"], "evidence_by": {"falsifier":
                                                   PROBE_EVIDENCE}}]
    w = _passing_sets_world(leerie, tmp_path, monkeypatch, unmet,
                            [_falsifier_row(0)])
    w.run()
    leerie._decide_outcome(w.st, any_commits=True)
    body = leerie._format_pr_end_state_section(w.st.data)
    section = body.split("## Unconverged", 1)[1].split("\n## ", 1)[0]
    line = next(ln for ln in section.splitlines()
                if "confirmed falsifier probe" in ln)
    assert line.startswith("- item 0 is unmet on a confirmed falsifier probe")
    assert f"of its words \"{QUOTE}\", which fails under B*" in line
    assert "held-out content no fixer could see" in line
    assert line.endswith("no re-drive round was spent on it")
    assert "ZQX probe case title" not in body


def test_a_mixed_item_is_driven_on_its_judge_part_alone(
        leerie, tmp_path, monkeypatch):
    unmet = [{"item_index": 0, "item": "a does X",
              "evidence": JUDGE_EVIDENCE + " | " + PROBE_EVIDENCE,
              "by": ["judge", "falsifier"],
              "evidence_by": {"judge": JUDGE_EVIDENCE,
                              "falsifier": PROBE_EVIDENCE}}]
    row = _falsifier_row(0)
    row.update(by=["judge", "falsifier"], judge={
        "item_index": 0, "samples": 3, "unmet_votes": 2, "rejected": 0,
        "unmet_parts": [{"requirement": "reads the payload schema",
                         "kind": "behavioral", "cites": [
                             {"path": "src/a.ts", "line_start": 1,
                              "line_end": 1, "quote": "export const a"}]}]})
    w = _passing_sets_world(leerie, tmp_path, monkeypatch, unmet, [row])

    async def still_unmet(*a, **k):
        return {0: {"unmet": True, "by": ["judge", "falsifier"],
                    "evidence": JUDGE_EVIDENCE + " | " + PROBE_EVIDENCE,
                    "evidence_by": {"judge": JUDGE_EVIDENCE,
                                    "falsifier": PROBE_EVIDENCE}}}
    monkeypatch.setattr(leerie, "_redrive_recheck_items", still_unmet)
    w.run()
    assert w.calls, "the item's reproducible part was never driven"
    for c in w.calls:
        work, _files = _note_work_and_selfcheck(c["prompt"])
        assert work == [{"kind": "unmet_item", "item": "a does X",
                         "evidence": JUDGE_EVIDENCE}]
        assert QUOTE not in c["prompt"]
        assert "falsifier" not in c["prompt"]
    # The probe part reached no worker, and the record says so: every
    # confirmed finding is handed on or recorded, never silently neither.
    rec = w.st.data["delivery_redrive"]
    assert rec["items_driven"] == [0]
    assert rec["unrouted_items"] == [
        {"item_index": 0, "item": "a does X", "reason": "unreproducible",
         "probed": QUOTE}]
    assert leerie._decide_outcome(w.st, any_commits=True) == "unconverged"
    why = [r for r in w.st.data["outcome_reasons"]
           if r.startswith("item 0 is unmet on a confirmed falsifier probe")]
    assert len(why) == 1
    # Its judge part was driven, so the reason never says no round was.
    assert why[0].endswith("no re-drive worker was handed it (the item's "
                           "other evidence was driven)")


def test_a_mixed_item_without_evidence_by_carries_no_evidence(leerie):
    """A state written before `evidence_by`: code never splits the joined
    prose, so the note carries the item alone."""
    unmet = [{"item_index": 0, "item": "a does X",
              "evidence": JUDGE_EVIDENCE + " | " + PROBE_EVIDENCE,
              "by": ["judge", "falsifier"]},
             {"item_index": 1, "item": "b does Y", "evidence": "gone",
              "by": ["judge"]},
             {"item_index": 2, "item": "c does Z", "evidence": "no by"}]
    notes, unrepro = leerie._redrive_item_notes(unmet, {}, 3, [])
    assert [(n["item_index"], n["evidence"], n["by"]) for n in notes] == [
        (0, "", ["judge"]), (1, "gone", ["judge"]), (2, "no by", ["judge"])]
    assert unrepro == []


def test_a_forced_falsifier_note_is_raised_never_driven(
        leerie, tmp_path, monkeypatch):
    """The pre-drive check covers item notes: one naming a falsifier source
    (forced past `_redrive_item_notes`) stops the re-drive before any
    worker is spawned."""
    unmet = [{"item_index": 0, "item": "a does X", "evidence": PROBE_EVIDENCE,
              "by": ["falsifier"]}]
    w = _passing_sets_world(leerie, tmp_path, monkeypatch, unmet,
                            [_falsifier_row(0)])

    def forced(unmet_final, claims, n_items, rows, **_kw):
        return ([{"kind": "unmet_item", "key": "item-0", "item_index": 0,
                  "item": "a does X", "evidence": PROBE_EVIDENCE,
                  "by": ["falsifier"], "claimed_by": ["s1"]}], [])
    monkeypatch.setattr(leerie, "_redrive_item_notes", forced)
    with pytest.raises(RuntimeError, match="item-0"):
        w.run()
    assert w.calls == []


def test_a_set_only_round_still_swings_at_an_item_falsified_on_the_final_tree(
        leerie, tmp_path, monkeypatch):
    """DESIGN §8 rule 8 — the non-item-regressor shape. Round 1 drives ONLY a
    failing held-out set (no item evidence key is handed), then the re-check
    of the tree that ships falsifies a previously-met item. The re-entry must
    still swing at that never-handed item: the progress guard is "a drive
    happened this pass" (which charged the budget), not "the handed set grew"
    — the latter would strand the item `after_last_round` with budget left,
    the exact waste this change removes."""
    items = [{"item": "a does X", "span": "S1", "verifiability": "tree"}]
    w = World(leerie, tmp_path, n_sets=1, items=items, unmet=[],
              claims={"s1": [{"item_index": 0, "kind": "deliverable"}]})
    # item 0 reported met going in → round 1 has no item work, only the
    # failing DEFECT set; its met row makes it a `_redrive_met_unread`
    # candidate on the final tree.
    w.st.data["delivery_gate"]["items"] = [
        {"item_index": 0, "item": "a does X", "verdict": "met",
         "by": ["judge"]}]
    w.st.save()
    w.install(monkeypatch)
    w.edits = [lambda w: w.write("export const a = 1; // FIXED\n")]

    async def recheck(leerie_dir, st, caps, models, efforts, staging,
                      indices):
        # The previously-met item flips to unmet on the shipping tree.
        return {i: {"unmet": True, "by": ["judge"], "evidence": "regressed"}
                for i in indices}
    monkeypatch.setattr(leerie, "_redrive_recheck_items", recheck)
    w.run()
    rec = w.st.data["delivery_redrive"]
    # Round 1 drove the set only; the re-entry drove the falsified item.
    assert 0 in rec["items_driven"], (
        "item falsified after a set-only round was never swung at")
    assert rec["evidence_handed"] == ["0:judge"]
    # It reached a worker, so it is never stranded `after_last_round`.
    assert not any(u.get("reason") == "after_last_round"
                   for u in rec.get("unrouted_items") or [])
