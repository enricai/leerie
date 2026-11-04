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

    async def fake_claude_p(**_kw):
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
        # Re-judged on the new tree, by the whole vote.
        assert judged and all(list(k["full_vote"]) == [0] for k in judged)
        assert left == []
    else:
        assert judged == []
        assert [u["item_index"] for u in left] == [0]
