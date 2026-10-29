"""Review round 8 L1: a kept re-drive group's unsettled entry defers to the
failing-test-ID diff of the tree that ships (DESIGN §9 *Which failures are
new*, step 4).

Before: a kept group left `test_x` unsettled; the shipping refresh then
measured `test_x` passing (no candidate) and the run ended `done` — and the
PR still said `tests/test_s.py` was "failing after a re-drive group that
was kept … whether the group made them fail is unknown"."""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from test_redrive_test_id_diff_refresh import redriven  # noqa: E402,F401

KEPT = "failing after a re-drive group that was kept"


def _keep(leerie, st, x, *, items=()):
    rec = {"kept_unsettled": [{"group": "r0g0", "ids": [x], "sized_out": [],
                               "files": {x: "tests/test_s.py"},
                               "unchecked_items": list(items)}]}
    st.data["delivery_redrive"] = rec
    leerie._set_redrive_unsettled_gap(
        st, rec["kept_unsettled"],
        [leerie._delivery_norm_item(it) for it in st.data["required_items"]])
    assert KEPT in leerie._format_pr_end_state_section(st.data)


def _final(leerie, redriven):
    asyncio.run(leerie._refresh_shipping_test_id_diff(
        redriven["st"].run_dir, redriven["st"], redriven["caps"],
        label="test-id-diff-final"))


def test_a_settled_shipping_diff_drops_the_kept_entry(leerie, redriven):
    """The reviewer's driver, now ending with an empty section."""
    st, x = redriven["st"], redriven["x"]
    _keep(leerie, st, x)
    _final(leerie, redriven)
    diff = st.data["test_id_diff"]
    assert diff["tree_sha"] == redriven["t1"] and diff["candidates"] == []
    assert leerie._decide_outcome(st, any_commits=True) == "done"
    assert KEPT not in leerie._format_pr_end_state_section(st.data)
    assert not [e for e in st.data.get("needs_human_verification") or []
                if e.get("site") == leerie._REDRIVE_UNSETTLED_SITE]
    # The record is kept as it happened.
    assert st.data["delivery_redrive"]["kept_unsettled"][0]["ids"] == [x]


def test_an_id_still_unsettled_on_the_shipping_tree_stays(leerie, redriven,
                                                          monkeypatch):
    st, x = redriven["st"], redriven["x"]
    _keep(leerie, st, x)
    st.data["test_id_diff"] = {
        "tree_sha": redriven["t1"], "candidates": [x], "new": [],
        "pre_existing": [], "budget_short_unsettled": [x],
        "budget_exhausted": True}
    leerie._settle_redrive_unsettled_by_diff(st, redriven["t1"])
    assert KEPT in leerie._format_pr_end_state_section(st.data)
    # Typed against the base on the shipping tree: settled either way.
    for kind in ("new", "pre_existing"):
        _keep(leerie, st, x)
        st.data["test_id_diff"] = {"tree_sha": redriven["t1"],
                                   "candidates": [x], "new": [],
                                   "pre_existing": [], kind: [x]}
        leerie._settle_redrive_unsettled_by_diff(st, redriven["t1"])
        assert KEPT not in leerie._format_pr_end_state_section(st.data)


def test_a_diff_of_another_tree_settles_nothing(leerie, redriven):
    st, x = redriven["st"], redriven["x"]
    _keep(leerie, st, x)
    st.data["test_id_diff"] = {"tree_sha": redriven["t0"], "candidates": []}
    leerie._settle_redrive_unsettled_by_diff(st, redriven["t1"])
    assert KEPT in leerie._format_pr_end_state_section(st.data)
    st.data["test_id_diff"] = {"tree_sha": redriven["t1"], "candidates": [],
                               "ids_unknown": True}
    leerie._settle_redrive_unsettled_by_diff(st, redriven["t1"])
    assert KEPT in leerie._format_pr_end_state_section(st.data)


def test_unchecked_items_outlive_their_settled_files(leerie, redriven):
    """No diff checks a required item the ratchet could not: with every
    file settled, the item is still listed, alone."""
    st, x = redriven["st"], redriven["x"]
    _keep(leerie, st, x, items=[0])
    _final(leerie, redriven)
    sec = leerie._format_pr_end_state_section(st.data)
    assert KEPT not in sec
    entry = [e for e in st.data["needs_human_verification"]
             if e.get("site") == leerie._REDRIVE_UNSETTLED_SITE]
    assert len(entry) == 1
    assert entry[0]["detail"].startswith(
        "delivery re-drive: required item(s) its ratchet could not check:")
    assert "fix y" in entry[0]["detail"]
