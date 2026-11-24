"""A fix that passed is not a fix that did not pass (DESIGN §8 *Delivery
re-drive* rule 8).

Both barnacle batches (leerie 0.93.0, 0.95.0): 10 re-drive groups made the
probes of 12 items pass and were rolled back for harm elsewhere (one lint
error, newly failing tests, a met item made unmet, ...), and 11 of those 12
items ended unmet. The rolled-back drive had recorded the item's key handed, so rule 8
granted no further swing once the owner's rounds were spent, and no later
note told a fixer the fix had worked and why it was undone.

These drive the real `_run_delivery_redrive` over `test_shown_probe_contest`'s
World with the run's own subtask having added the pinning test, so breaking
it is a new failure, not a conflict with what the repository pins. Only
`claude_p` is stubbed. The owner has one round (`subtask_redrive_rounds = 1`),
so any second drive is rule 8's swing.
"""
from __future__ import annotations

from tests.conftest import run_git_out as _git
from tests.test_shown_probe_contest import (  # noqa: F401  (fixture import)
    FITTED,
    HONEST,
    TIDIED,
    World,
    _groups,
    _quiet_host,
)


def _world(leerie, tmp_path, monkeypatch, **kw) -> World:
    w = World(leerie, tmp_path, **kw)
    base = w._caps
    monkeypatch.setattr(w, "_caps",
                        lambda: {**base(), "subtask_redrive_rounds": 1})
    return w


def test_a_probe_passing_rollback_is_driven_again_with_its_commit(
        leerie, tmp_path, monkeypatch):
    w = _world(leerie, tmp_path, monkeypatch, pin_added_by_run=True)
    w.edits = [lambda w: w.commit({"calc.py": HONEST}),
               lambda w: w.commit({"calc.py": FITTED})]
    rec = w.run(monkeypatch)
    g1, g2 = _groups(rec)
    assert g1["probes_after"] == {"0": "pass"} and g1["rolled_back"] is True
    assert "probe_conflict" not in g1 and g1["rollback_pass"] == [0]
    [(hk, entries)] = rec["rollback_passes"].items()
    assert hk.startswith("0:falsifier")
    [e] = entries
    assert (e["round"], e["group"]) == (1, 0)
    assert e["reason"] == g1["rollback_reason"]
    # The second drive is handed the rolled-back commit and why.
    assert len(w.calls) == 2
    p2 = w.calls[1]["prompt"]
    assert f"git show {e['commit']}" in p2
    assert '"previous_fix"' in p2 and "PREVIOUS FIX:" in p2
    assert "PREVIOUS FIX:" not in w.calls[0]["prompt"]
    # The commit is readable from the tree the fixer works in.
    assert _git(w.staging, "show", f"{e['commit']}:calc.py") == HONEST.rstrip(
        "\n")
    assert not g2.get("rolled_back")
    assert _git(w.staging, "show", "HEAD:calc.py") == FITTED.rstrip("\n")
    assert w.st.data["delivery_gate"]["unmet_final"] == []


def test_a_second_probe_passing_rollback_spends_the_key(
        leerie, tmp_path, monkeypatch):
    w = _world(leerie, tmp_path, monkeypatch, pin_added_by_run=True)
    w.edits = [lambda w: w.commit({"calc.py": HONEST}),
               lambda w: w.commit({"calc.py": HONEST + "# again\n"}),
               lambda w: w.commit({"calc.py": FITTED})]
    rec = w.run(monkeypatch)
    assert len(w.calls) == 2
    [entries] = rec["rollback_passes"].values()
    assert [(e["round"], e["group"]) for e in entries] == [(1, 0), (2, 0)]
    hk = next(iter(rec["rollback_passes"]))
    assert hk in rec["evidence_handed"]
    assert [u["item_index"] for u in
            w.st.data["delivery_gate"]["unmet_final"]] == [0]


def test_a_rollback_whose_probe_still_failed_spends_the_key(
        leerie, tmp_path, monkeypatch):
    w = _world(leerie, tmp_path, monkeypatch, pin_added_by_run=True)
    w.edits = [lambda w: w.commit({"calc.py": TIDIED}),
               lambda w: w.commit({"calc.py": FITTED})]
    rec = w.run(monkeypatch)
    [g] = _groups(rec)
    assert g["probes_after"] == {"0": "fail"} and g["rolled_back"] is True
    assert len(w.calls) == 1
    assert "rollback_passes" not in rec and "rollback_pass" not in g


def test_a_pinned_conflict_keeps_its_own_path(leerie, tmp_path, monkeypatch):
    w = _world(leerie, tmp_path, monkeypatch)
    w.edits = [lambda w: w.commit({"calc.py": HONEST}),
               lambda w: w.commit({"calc.py": FITTED})]
    rec = w.run(monkeypatch)
    [g] = _groups(rec)
    assert g["probe_conflict"]["items"] == [0]
    assert len(w.calls) == 1 and "rollback_passes" not in rec


def test_passing_rollback_reads_typed_fields_only(leerie):
    f = leerie._redrive_passing_rollback
    live = [{"kind": "unmet_item", "item_index": 0, "evidence_keys": ["a"]},
            {"kind": "unmet_item", "item_index": 1, "evidence_keys": ["b"]},
            {"kind": "unmet_item", "item_index": 2, "evidence_keys": ["c"]},
            {"kind": "unmet_item", "item_index": 3, "evidence_keys": ["d"]},
            {"kind": "defect", "item_index": 4}]
    ge = {"probes_after": {"0": "pass", "1": "fail", "2": "pass",
                           "3": "pass", "4": "pass"},
          "probe_conflict": {"items": [2]}, "keep_both_scheduled": [3]}
    assert f(ge, live) == [(0, ["a"])]
    assert f({}, live) == []
