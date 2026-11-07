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


def test_a_spent_claimer_leaves_a_typed_unrouted_item(
        leerie, tmp_path, monkeypatch):
    w = _world(leerie, tmp_path, monkeypatch, "unconverged")
    cap = int(w.caps["subtask_redrive_rounds"])
    w.st.data["redrive_rounds"] = {"s1": cap}
    w.run()
    assert w.calls == []
    rec = w.st.data["delivery_redrive"]
    assert rec["unrouted_items"] == [
        {"item_index": 0, "item": "a does X", "reason": "budget"}]
    assert w.L._decide_outcome(w.st, any_commits=True) == "unconverged"
    assert any("unrouted_item" in r and "item 0 (budget)" in r
               for r in w.st.data["outcome_reasons"])


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


def test_unrouted_items_is_pure_and_typed(leerie):
    notes = [{"item_index": 0, "item": "x"}, {"item_index": 1, "item": "y"},
             {"item_index": 2, "item": "z"}]
    assert leerie._redrive_unrouted_items(notes, [1], [2], False) == [
        {"item_index": 0, "item": "x", "reason": "no_owner"},
        {"item_index": 2, "item": "z", "reason": "budget"}]
    assert [u["reason"] for u in leerie._redrive_unrouted_items(
        notes[:1], [], [], True)] == ["budget"]


def test_unrouted_items_records_unreproducible_last(leerie):
    notes = [{"item_index": 0, "item": "x"}]
    unrepro = [{"item_index": 3, "item": "w", "probed": "w does V"}]
    assert leerie._redrive_unrouted_items(notes, [0], [], False,
                                          unreproducible=unrepro) == [
        {"item_index": 3, "item": "w", "reason": "unreproducible",
         "probed": "w does V"}]


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
    assert "unrouted_items" not in w.st.data["delivery_redrive"]


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
