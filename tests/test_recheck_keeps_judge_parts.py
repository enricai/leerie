"""A re-check keeps what its judge showed, as detection does (DESIGN §8
*A re-check keeps what its judge showed, as detection does*).

The re-drive's re-check replaced an item's verdict and kept only the judge's
prose: the row said `redrive_recheck: {sha}`. Of 31 judge-unmet items in the
recorded runs, the 4 a re-check judge made unmet had no part or citation
anywhere (navegando-nv 399f7c8b items 139, 146, 150; navegando b94226a8 item
9), so the PR could say only "unmet". These drive the real re-check and the
real judge over a real git tree, stubbing only the worker call."""
from __future__ import annotations

import asyncio
import subprocess

from tests.stub_helpers import (DELIVERY_CITED_FILE, DELIVERY_CITED_LINE,
                                _delivery_tree)

ITEM = "the delivered feature handles an empty value"


def _part(status: str, quote: str) -> dict:
    return {"requirement": "an empty value returns None", "kind": "static",
            "status": status, "checklist_index": 0,
            "cites": [{"path": DELIVERY_CITED_FILE, "line_start": 1,
                       "line_end": 1, "quote": quote}]}


def _verdict(met: bool) -> dict:
    if met:
        return {"item_index": 0, "met": True,
                "parts": [_part("met", DELIVERY_CITED_LINE)],
                "evidence": "handled"}
    part = _part("unmet", DELIVERY_CITED_LINE)
    # A second citation that does not exist in the file: never kept.
    part["cites"].append({"path": DELIVERY_CITED_FILE, "line_start": 2,
                          "line_end": 2, "quote": "if value is None:"})
    return {"item_index": 0, "met": False, "parts": [part],
            "evidence": "no empty-value branch"}


def _recheck(leerie, tmp_path, monkeypatch, met: bool) -> tuple:
    tree = _delivery_tree(tmp_path / "t")
    head = subprocess.run(["git", "-C", str(tree), "rev-parse", "HEAD"],
                          capture_output=True, text=True,
                          check=True).stdout.strip()
    st = leerie.State(tmp_path / "s", "r")
    st.run_dir.mkdir(parents=True, exist_ok=True)
    st.data = {
        "task": "t", "worker_count": 0,
        "required_items": [{"item": ITEM, "verifiability": "tree"}],
        "delivery_gate": {
            "final_sha": "0" * 40,
            "unmet_final": [{"item_index": 0, "item": ITEM, "by": ["judge"],
                             "evidence": "detection: missing"}],
            "items": [{"item_index": 0, "item": ITEM, "verdict": "unmet",
                       "by": ["judge"],
                       "judge": {"unmet_parts": [], "samples": 3}}]}}
    st.save()

    async def fake_claude_p(**kw):
        if kw["schema_key"] == "delivery_enumerator":
            return {"sets": []}
        if kw["schema_key"] == "delivery_judge":
            return {"verdicts": [_verdict(met)]}
        raise AssertionError(f"unexpected worker {kw['schema_key']}")
    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    caps = dict(leerie.DEFAULT_CAPS)
    ver = asyncio.run(leerie._redrive_recheck_items(
        tmp_path / "leerie", st, caps, {"delivery_judge": "sonnet"},
        {"delivery_judge": "medium"}, tree, [0]))
    leerie._redrive_apply_read(st, ver, head, None)
    row = st.data["delivery_gate"]["items"][0]
    return row, st, head


def test_an_unmet_recheck_keeps_its_parts_on_the_row(
        leerie, tmp_path, monkeypatch):
    row, st, head = _recheck(leerie, tmp_path, monkeypatch, met=False)
    assert row["verdict"] == "unmet" and row["by"] == ["judge"]
    rc = row["redrive_recheck"]
    assert rc["sha"] == head
    # Detection's shape: the part, with only the citation that verifies.
    assert rc["unmet_parts"] == [{
        "requirement": "an empty value returns None", "kind": "static",
        "cites": [{"path": DELIVERY_CITED_FILE, "line_start": 1,
                   "line_end": 1, "quote": DELIVERY_CITED_LINE}]}]
    # Evidence only: the unmet list carries the judge's text as before.
    u = st.data["delivery_gate"]["unmet_final"]
    assert [x["item_index"] for x in u] == [0]
    assert u[0]["evidence"] == "no empty-value branch"
    assert "unmet_parts" not in u[0]


def test_a_met_recheck_records_that_its_judge_found_nothing(
        leerie, tmp_path, monkeypatch):
    row, st, head = _recheck(leerie, tmp_path, monkeypatch, met=True)
    assert row["verdict"] == "met"
    assert row["redrive_recheck"] == {"sha": head, "unmet_parts": []}
    assert st.data["delivery_gate"]["unmet_final"] == []
