"""The read-failure fallback of `_restamp_verdict_on_head` drops only the
final verdict's keys: `unmet_before` / `unmet_after` describe earlier trees on
purpose and stay (DESIGN §8); `verdict_dropped` names what was dropped."""
from __future__ import annotations

import asyncio

from tests.test_acceptance_no_work import _st


def test_fallback_drops_final_keys_only(leerie, tmp_path, monkeypatch):
    async def sha(_p):
        return None
    monkeypatch.setattr(leerie, "_branch_head_sha", sha)
    st = _st(leerie, tmp_path, delivery_gate={
        "unmet_final": [1], "final_sha": "OLD", "final_tree": "T",
        "items": [], "unmet_after": [2], "judged_after": {"sha": "OLD"},
        "unmet_before": [3], "judged_before": {"sha": "OLD"}})
    asyncio.run(leerie._restamp_verdict_on_head(
        tmp_path, st, {}, {}, {}, tmp_path))
    g = st.data["delivery_gate"]
    for k in ("unmet_final", "items", "final_sha", "final_tree"):
        assert k not in g
    assert g["unmet_after"] == [2] and g["unmet_before"] == [3]
    assert g["verdict_dropped"] == ["unmet_final"]
    # earlier trees' records survive whole, and an earlier tree's verdict is
    # still refused by the outcome rather than read as the shipped one
    assert g["judged_after"] == {"sha": "OLD"}
    assert g["judged_before"] == {"sha": "OLD"}
    unmet, tree, which = leerie._delivery_latest_verdict(g)
    assert (unmet, which) == ([2], "the recheck") and tree == ""
    assert not leerie._of_shipping(tree, "SHIPPING")
