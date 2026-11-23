"""The delivery re-drive holds its routing in one table (DESIGN §8 *Delivery
re-drive*, *One routing table*): per note kind, its owner, whether it is
still open at a group's start, and what the group's self-check must
reproduce. And the record of what reached no worker is read off what the
notes were built from, never worked out a second time.

Each test changes one setting of `_REDRIVE_ROUTES` (or one field
`_redrive_item_notes` builds) and drives the real `_run_delivery_redrive` on
a real git repository, so the consumer that reads it is executed: the
router, the per-group filter, the pre-drive check and the unrouted record.
Stubbed as in the files the worlds come from: `claude_p`, the set
evaluation and the item re-check. Before the table, each of these read its
own kind list, and the change would have done nothing.
"""
from __future__ import annotations

from tests.test_redrive_check_note_only_while_red import (
    CI_NOTE, _fix_a_and_lint, _world as _lint_world, _write_b)
from tests.test_redrive_unrouted_items import (
    JUDGE_EVIDENCE, PROBE_EVIDENCE, _falsifier_row, _passing_sets_world)


def _set(monkeypatch, leerie, kind: str, **settings) -> None:
    monkeypatch.setitem(leerie._REDRIVE_ROUTES, kind,
                        {**leerie._REDRIVE_ROUTES[kind], **settings})


def test_the_group_filter_reads_open_from_the_table(
        leerie, tmp_path, monkeypatch):
    # 39c4c5ad's shape: group 0 turns lint green, and its kept measurement
    # clears the check for group 1. With the check's `open` set to
    # `always`, group 1 is handed it after all: the filter reads the table.
    _set(monkeypatch, leerie, "ci_check", open="always")
    w = _lint_world(leerie, tmp_path, monkeypatch, s2_item=True)
    w.edits = [_fix_a_and_lint,
               lambda w: _write_b(w, "export const b = 2; // FIXED\n")]
    w.run()
    g0, g1 = w.st.data["delivery_redrive"]["rounds"][0]["groups"]
    assert g1["checks_cleared"] == ["lint"]
    assert g1["notes"] == ["ci_check", "unmet_item"]
    assert CI_NOTE in w.calls[1]["prompt"]


def test_the_router_reads_owner_from_the_table(leerie, tmp_path, monkeypatch):
    # Item 0 is claimed by s1 alone. Routed as `touching` (an item names
    # no file, so no subtask is touched and no defect claims it), it goes
    # to every subtask, s2's group too.
    _set(monkeypatch, leerie, "unmet_item", owner="touching")
    w = _lint_world(leerie, tmp_path, monkeypatch, s2_item=True)
    w.edits = [_fix_a_and_lint,
               lambda w: _write_b(w, "export const b = 2; // FIXED\n")]
    w.run()
    g0, g1 = w.st.data["delivery_redrive"]["rounds"][0]["groups"]
    assert g1["members"] == ["s2"]
    assert "a is fixed" in w.calls[1]["prompt"]
    assert "b is fixed" in w.calls[1]["prompt"]


def test_the_pre_drive_check_reads_runnable_from_the_table(
        leerie, tmp_path, monkeypatch):
    # An item note naming a falsifier with no shown probe, forced past
    # `_redrive_item_notes`, stops the re-drive (test_redrive_unrouted_
    # items). With items not `runnable`, the check does not look at it.
    _set(monkeypatch, leerie, "unmet_item", runnable=None)
    unmet = [{"item_index": 0, "item": "a does X", "evidence": PROBE_EVIDENCE,
              "by": ["falsifier"]}]
    w = _passing_sets_world(leerie, tmp_path, monkeypatch, unmet,
                            [_falsifier_row(0)])

    def forced(unmet_final, claims, n_items, rows, **_kw):
        return ([{"kind": "unmet_item", "key": "item-0", "item_index": 0,
                  "item": "a does X", "evidence": PROBE_EVIDENCE,
                  "by": ["falsifier"], "claimed_by": ["s1"],
                  "evidence_keys": ["falsifier"], "keys": ["falsifier"],
                  "undriven": {"reason": "unreproducible", "probed": ""}}],
                [])
    monkeypatch.setattr(leerie, "_redrive_item_notes", forced)
    w.run()
    assert w.calls and "a does X" in w.calls[0]["prompt"]


def test_the_unrouted_record_is_read_off_the_notes(
        leerie, tmp_path, monkeypatch):
    # A2a825c2's shape: an item unmet on a judge vote and on a probe no
    # fixer may be shown is driven on its judge part; the probe part is
    # recorded `unreproducible`, with the words it tested. Those words are
    # the ones the note was built with: the record never derives them again.
    unmet = [{"item_index": 0, "item": "a does X",
              "evidence": JUDGE_EVIDENCE + " | " + PROBE_EVIDENCE,
              "by": ["judge", "falsifier"],
              "evidence_by": {"judge": JUDGE_EVIDENCE,
                              "falsifier": PROBE_EVIDENCE}}]
    row = _falsifier_row(0)
    row.update(by=["judge", "falsifier"])
    w = _passing_sets_world(leerie, tmp_path, monkeypatch, unmet, [row])

    async def still_unmet(*a, **k):
        return {0: {"unmet": True, "by": ["judge", "falsifier"],
                    "evidence": JUDGE_EVIDENCE + " | " + PROBE_EVIDENCE,
                    "evidence_by": {"judge": JUDGE_EVIDENCE,
                                    "falsifier": PROBE_EVIDENCE}}}
    monkeypatch.setattr(leerie, "_redrive_recheck_items", still_unmet)
    real = leerie._redrive_item_notes

    def built(*a, **kw):
        notes, records = real(*a, **kw)
        for n in notes:
            assert n["undriven"]["reason"] == "unreproducible"
            n["undriven"] = {**n["undriven"], "probed": "words the note had"}
        return notes, records
    monkeypatch.setattr(leerie, "_redrive_item_notes", built)
    w.run()
    rec = w.st.data["delivery_redrive"]
    assert rec["items_driven"] == [0]
    assert rec["unrouted_items"] == [
        {"item_index": 0, "item": "a does X", "reason": "unreproducible",
         "probed": "words the note had"}]


def test_every_kind_a_note_is_built_as_has_a_route(leerie):
    # The kinds the re-drive builds (sets, held-back sets, items, checks,
    # solution defects, base forms, pending controls): each states its
    # owner and its open rule, and each open rule is one the filter knows.
    built = {"defect", "control_regression", "held_defect", "held_control",
             "unmet_item", "ci_check", "solution_defect", "base_form_case",
             "keep_both_case"}
    assert set(leerie._REDRIVE_ROUTES) == built
    for kind, r in leerie._REDRIVE_ROUTES.items():
        assert r["owner"] in ("defects", "touching", "claimers"), kind
        assert r["runnable"] in ("set_file", "item", None), kind
        n = {"kind": kind, "key": "k", "file": "f"}
        assert isinstance(leerie._redrive_open_at_start(
            n, failing={"k"}, held={"k"}, cleared=set(), closed=set(),
            base_forms_open={"k"}, round_bf=set(),
            keep_both_open={"k"}, round_kb=set()), bool)
        # Open on its own evidence, closed when that evidence says so.
        assert leerie._redrive_open_at_start(
            n, failing={"k"}, held={"k"}, cleared=set(), closed=set(),
            base_forms_open={"k"}, round_bf=set(),
            keep_both_open={"k"}, round_kb=set()), kind
        assert leerie._redrive_open_at_start(
            n, failing=set(), held=set(), cleared={"k"}, closed={"k"},
            base_forms_open=set(), round_bf={"k"},
            keep_both_open=set(), round_kb={"k"}) is (
                r["open"] == "always"), kind
