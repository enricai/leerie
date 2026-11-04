"""An unmet item reaches its owner, or is recorded as reaching none (DESIGN
§8 *Delivery re-drive*).

Barnacle a192d275: the falsifier confirmed an item unmet whose only
claimer, feat-001, had settled `unconverged`. The re-drive left unconverged
subtasks out of its routing, so the item went to nobody and the re-drive
logged "done" one second later, with nothing recording that the finding
reached no worker.
"""
from __future__ import annotations

import json
import types

import pytest

from tests.test_delivery_redrive import World

ITEMS = [{"item": "a does X", "span": "S1", "verifiability": "tree"}]
UNMET = [{"item_index": 0, "item": "a does X", "evidence": "probe fails",
          "by": ["falsifier"]}]


def _world(leerie, tmp_path, monkeypatch, status: str):
    w = World(leerie, tmp_path, n_sets=0, items=ITEMS,
              unmet=[dict(u) for u in UNMET],
              claims={"s1": [{"item_index": 0, "kind": "deliverable"}]})
    w.st.data["subtask_status"] = {"s1": status, "s2": "complete"}
    w.install(monkeypatch)

    async def still_unmet(*a, **k):
        return {0: {"unmet": True, "by": ["falsifier"],
                    "evidence": "probe fails"}}
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


@pytest.mark.parametrize("reason", ["budget", "no_owner"])
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
