"""A re-check replaces a verdict only on a tree that changed, and only by the
vote that made it (DESIGN §8 *Delivery re-drive*).

Barnacle 48c48629: the delivery judge voted 2 of 3 unmet on an item, the
re-drive's one group made no change (`pre_sha == post_sha`), and the
re-check still re-judged — its last-seen commit started empty — and its
first sample, saying met, ended confirm-on-first-pass: one sample on the
same tree overturned the majority, and the run shipped `done`.
"""
from __future__ import annotations

import asyncio

import pytest

from tests.stub_helpers import _delivery_tree, _judge_verdict
from tests.test_delivery_redrive import World, _git

MODELS = {"delivery_judge": "sonnet"}
EFFORTS = {"delivery_judge": "medium"}


def _judge(leerie, tmp_path, monkeypatch, samples, **kw):
    tree = _delivery_tree(tmp_path / "t")
    st = leerie.State(tmp_path / "s", "r")
    st.data = {"task": "t", "worker_count": 0}

    async def fake_claude_p(**kw):
        if kw["schema_key"] == "delivery_enumerator":
            return {"sets": []}         # the item quantifies over no set
        return samples.pop(0)
    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    unmet, n, _ = asyncio.run(leerie._delivery_judge_unmet(
        "t", [{"item": "the feature exists"}], tree, st,
        dict(leerie.DEFAULT_CAPS), MODELS, EFFORTS, phase_label="redrive",
        **kw))
    return unmet, n


def _met_then_unmet():
    return [{"verdicts": [_judge_verdict(0, True)]},
            {"verdicts": [_judge_verdict(0, False)]},
            {"verdicts": [_judge_verdict(0, False)]}]


def test_one_met_sample_never_overturns_a_majority(
        leerie, tmp_path, monkeypatch):
    unmet, n = _judge(leerie, tmp_path, monkeypatch, _met_then_unmet(),
                      full_vote=[0])
    assert n == 3
    assert [u["item_index"] for u in unmet] == [0]


def test_a_first_look_still_ends_on_one_clean_sample(
        leerie, tmp_path, monkeypatch):
    unmet, n = _judge(leerie, tmp_path, monkeypatch, _met_then_unmet())
    assert n == 1 and unmet == []


def test_judge_majority_items_names_only_judge_settled_rows(leerie):
    assert leerie._judge_majority_items([
        {"item_index": 0, "by": ["judge"]},
        {"item_index": 1, "by": ["falsifier"]},
        {"item_index": 2},
        {"item_index": 3, "settled_by": "untouched_paths"},
        {"item_index": 4, "by": ["judge", "falsifier"]},
        "junk"]) == [0, 2, 4]


@pytest.mark.parametrize("changes", [False, True])
def test_a_routed_item_is_rejudged_only_on_a_changed_tree(
        leerie, tmp_path, monkeypatch, changes):
    items = [{"item": "a does X", "span": "S1", "verifiability": "tree"}]
    unmet = [{"item_index": 0, "item": "a does X", "evidence": "missing",
              "by": ["judge"]}]
    w = World(leerie, tmp_path, n_sets=0, items=items, unmet=unmet,
              claims={"s1": [{"item_index": 0, "kind": "deliverable"}]})
    start = _git(w.staging, "rev-parse", "HEAD")
    assert w.st.data["delivery_gate"]["final_sha"] == start
    # Detection's row for the item: the re-check records its read there
    # (`redrive_recheck`), and the end of the re-drive reads per item which
    # commit each was last read on (DESIGN §8 *Every item verdict keyed to
    # the tree that ships was read on it*).
    w.st.data["delivery_gate"]["items"] = [
        {"item_index": 0, "item": "a does X", "verdict": "unmet",
         "by": ["judge"]}]
    w.st.save()
    w.install(monkeypatch)
    w.edits = ([lambda w: w.write("export const a = 1; // done\n")]
               if changes else [None, None])
    judged: list[dict] = []

    async def fake_judge(task, its, staging, st, caps, models, efforts,
                         phase_label, **kw):
        judged.append(kw)
        return [], 1, None
    monkeypatch.setattr(leerie, "_delivery_judge_unmet", fake_judge)
    monkeypatch.setattr(leerie, "_delivery_record", lambda *a, **k: "")
    monkeypatch.setattr(leerie, "_delivery_typed_evidence",
                        lambda *a, **k: ([], {}))
    w.run()
    # Unchanged, the item stays open and s1 is driven until its budget is
    # spent; no round re-judges a tree its verdict was made on.
    assert len(w.calls) == (1 if changes else 2)
    left = w.st.data["delivery_gate"]["unmet_final"]
    if changes:
        # Re-judged on the new tree, once, by the whole vote: the end of
        # the re-drive does not read it again on the commit it was read on.
        assert len(judged) == 1 and list(judged[0]["full_vote"]) == [0]
        assert left == []
    else:
        assert judged == []
        assert [u["item_index"] for u in left] == [0]


LOC = [{"path": "a.py", "start_line": 3, "end_line": 4}]
PARTS = [{"cites": [{"path": "a.py", "line_start": 3, "line_end": 4},
                    {"path": "RECORD"}, {"path": "ABSENT x"},
                    {"path": "a.py", "line_start": 3, "line_end": 4}]}]


def _recheck(leerie, tmp_path, monkeypatch, *, unmet, details, prior,
             crash=False):
    st = leerie.State(tmp_path / "s", "r")
    st.data = {"task": "t", "required_items": ["i0"],
               "delivery_gate": {"unmet_final": prior, "items": []}}

    async def fake_judge(*a, **kw):
        if crash:
            raise RuntimeError("boom")
        kw["item_details"].update(details)
        return unmet, 1, None
    monkeypatch.setattr(leerie, "_delivery_judge_unmet", fake_judge)
    monkeypatch.setattr(leerie, "_delivery_record", lambda *a, **k: "")
    monkeypatch.setattr(leerie, "_delivery_typed_evidence",
                        lambda *a, **k: ([], {}))
    return asyncio.run(leerie._redrive_recheck_items(
        tmp_path, st, dict(leerie.DEFAULT_CAPS), MODELS, EFFORTS,
        tmp_path, [0]))


PRIOR = [{"item_index": 0, "item": "i0", "evidence": "old",
          "by": ["judge"], "locations": [{"path": "old.py"}]}]


def test_recheck_unmet_verdict_carries_typed_locations(
        leerie, tmp_path, monkeypatch):
    out = _recheck(leerie, tmp_path, monkeypatch,
                   unmet=[{"item_index": 0, "evidence": "e"}],
                   details={0: {"unmet_parts": PARTS}}, prior=PRIOR)
    assert out[0]["unmet"] and out[0]["locations"] == LOC


def test_recheck_unmet_without_cites_has_empty_locations(
        leerie, tmp_path, monkeypatch):
    out = _recheck(leerie, tmp_path, monkeypatch,
                   unmet=[{"item_index": 0, "evidence": "e"}],
                   details={0: {"unmet_parts": []}}, prior=PRIOR)
    assert out[0]["locations"] == []


def test_crashed_rejudge_keeps_prior_locations(
        leerie, tmp_path, monkeypatch):
    out = _recheck(leerie, tmp_path, monkeypatch, unmet=[], details={},
                   prior=PRIOR, crash=True)
    assert out[0]["crashed"] and out[0]["locations"] == [{"path": "old.py"}]


def test_gate_rewrite_replaces_stale_locations_and_joins_item(
        leerie, tmp_path, monkeypatch):
    out = _recheck(leerie, tmp_path, monkeypatch,
                   unmet=[{"item_index": 0, "evidence": "e"}],
                   details={0: {"unmet_parts": PARTS}}, prior=PRIOR)
    st = leerie.State(tmp_path / "g", "r")
    st.data = {"required_items": ["i0"],
               "delivery_gate": {"unmet_final": PRIOR, "items": []}}
    leerie._redrive_apply_item_verdicts(st, out, "sha")
    assert st.data["delivery_gate"]["unmet_final"][0]["locations"] == LOC
    # An item not on the list joins it with the verdict's locations.
    st.data["delivery_gate"]["unmet_final"] = []
    leerie._redrive_apply_item_verdicts(st, out, "sha")
    assert st.data["delivery_gate"]["unmet_final"][0]["locations"] == LOC


def test_different_locations_keep_the_same_evidence_key(leerie):
    a = {"by": ["judge"], "locations": [{"path": "a.py"}]}
    b = {"by": ["judge"], "locations": [{"path": "b.py"}]}
    assert leerie._redrive_evidence_keys(a, None) == ["judge"]
    assert leerie._redrive_evidence_keys(b, None) == ["judge"]
    assert leerie._redrive_evidence_keys({"by": ["judge"]}, None) == ["judge"]
