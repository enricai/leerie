"""A re-judge that crashed read nothing (DESIGN §8 *A re-judge that crashed
read nothing*).

When `_delivery_judge_unmet` raises inside `_redrive_recheck_items`, every
item it was asked about comes back `{unmet: True, crashed: True}` with its
prior evidence. The met read and the unverified read always applied only the
verdicts that read the commit. The routed read after a round and the
end-of-pass read of unread items applied crashed verdicts too, and stamped
them `redrive_recheck.sha`: the item counted as read on that commit, the
gate's `final_sha` was re-keyed to it, and the outcome called the earlier
tree's evidence unmet "on the tree that ships". This is the #401 class on two
paths #401 did not reach. No recorded run shows it (lane S9-R3: 0 "item
re-judge failed" lines).

This is the replay of `tests/test_redrive_reads_every_item_on_final.py`:
- a real pytest repository, its run repository and staging;
- the real `_run_delivery_redrive`, including the group's real commit, the
  ratchet, the re-checks and the falsifier's gates;
- `claude_p` stubbed, plus the TS facts.

The judge raises on chosen calls. Synthetic text."""
from __future__ import annotations

import pytest

from tests.conftest import run_git_out as _git
from tests.test_redrive_reads_every_item_on_final import _replay, _row


def _crash_on(leerie, monkeypatch, should_crash):
    """Wrap the real `_delivery_judge_unmet`: raise for the re-drive calls
    `should_crash(n, only)` picks (n counts the re-drive calls from 0)."""
    real = leerie._delivery_judge_unmet
    calls: list[list[int]] = []

    async def judge(*a, **kw):
        if kw.get("phase_label") == "redrive":
            only = sorted(kw.get("only") or [])
            n = len(calls)
            calls.append(only)
            if should_crash(n, only):
                raise RuntimeError("judge transport died")
        return await real(*a, **kw)
    monkeypatch.setattr(leerie, "_delivery_judge_unmet", judge)
    return calls


def test_a_round_reread_that_crashed_is_read_again_on_the_tree_that_ships(
        leerie, tmp_path, monkeypatch):
    """The in-round re-check of item 1, the routed item the kept group fixed,
    crashes. Its old evidence ("mul adds") must not be stamped as read on
    the group's commit. Otherwise the end of the pass sees item 1 as already
    read on `final` and never reads it again, so a fixed item ships as unmet.
    Listed unverified instead, it is read on `final`, read met, and the gate
    is keyed to `final`."""
    calls = _crash_on(leerie, monkeypatch, lambda n, only: n == 0)
    st, staging, head, _judged = _replay(
        leerie, tmp_path, monkeypatch, item2_met_when_fixed=True)
    final = _git(staging, "rev-parse", "HEAD")
    assert final != head
    assert calls[0] == [1], calls          # the round's routed re-check
    gate = st.data["delivery_gate"]
    row1 = _row(gate, 1)
    assert row1["verdict"] == "met", row1
    assert row1["redrive_recheck"] == {"sha": final}
    assert [u["item_index"] for u in gate["unmet_final"]] == []
    assert not (st.data.get("delivery_ratchet") or {}).get("unverified")
    assert gate["final_sha"] == final


def test_an_end_of_pass_read_that_crashed_is_not_a_read_of_the_tree_that_ships(
        leerie, tmp_path, monkeypatch):
    """Item 2 reaches no worker and is read only at the end of the pass, on
    `final`. That read crashes, and so does the unverified items' second try.
    So item 2 was never read on `final`. Its row carries no `final` stamp, it
    is listed unverified, the gate keeps detection's key, and the outcome
    says the required items were not read on the tree that ships."""
    calls = _crash_on(leerie, monkeypatch, lambda n, only: 2 in only)
    st, staging, head, _judged = _replay(
        leerie, tmp_path, monkeypatch, item2_met_when_fixed=False)
    final = _git(staging, "rev-parse", "HEAD")
    assert final != head
    assert sum(1 for c in calls if 2 in c) >= 2, calls
    gate = st.data["delivery_gate"]
    row2 = _row(gate, 2)
    assert (row2.get("redrive_recheck") or {}).get("sha") != final, row2
    assert row2["verdict"] == "unmet"
    assert "2" in (st.data.get("delivery_ratchet") or {}).get("unverified", {})
    assert gate["final_sha"] == head
    leerie._decide_outcome(st, any_commits=True)
    assert st.data["outcome"] == "unconverged"
    assert any(r.startswith("the required items were not read on the tree "
                            "that ships")
               for r in st.data["outcome_reasons"]), st.data["outcome_reasons"]


@pytest.mark.parametrize("crashed", [False, True])
def test_apply_read_stamps_only_what_was_read(leerie, tmp_path, crashed):
    st = leerie.State(tmp_path / "s", "r", repo_root=tmp_path)
    st.data = {"required_items": [{"item": "a"}],
               "delivery_gate": {"unmet_final": [
                   {"item_index": 0, "item": "a", "evidence": "old",
                    "by": ["judge"]}],
                   "items": [{"item_index": 0, "verdict": "unmet",
                              "by": ["judge"]}]}}
    v = {"unmet": True, "by": ["judge"], "evidence": "old"}
    if crashed:
        v["crashed"] = True
    leerie._redrive_apply_read(st, {0: v}, "F", None)
    row = st.data["delivery_gate"]["items"][0]
    unv = (st.data.get("delivery_ratchet") or {}).get("unverified") or {}
    if crashed:
        assert "redrive_recheck" not in row
        assert unv == {"0": {"sha": "F", "label": "the delivery re-drive"}}
    else:
        assert row["redrive_recheck"] == {"sha": "F"}
        assert unv == {}
