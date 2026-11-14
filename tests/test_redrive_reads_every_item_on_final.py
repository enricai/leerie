"""Every item verdict keyed to the tree that ships was read on it (DESIGN §8
*Every item verdict keyed to the tree that ships was read on it*).

Live (lane W3, 2026-11-14): barnacle 59d17631 (leerie 0.65.0) detected on
4d8717bf; the re-drive kept a group that moved staging to 03f67a05. The
round's re-check read only the item that group drove, on 03f67a05, and the
end of the re-drive then skipped its read of the items open at its start,
because the last re-check had run on that commit. Items 3 (`budget`) and 4
(`no_code_target`) kept their 4d8717bf verdicts, and the re-key stamped
`delivery_gate.final_sha` / `final_tree` with 03f67a05 over them. navegando
399f7c8b had five such items.

The replay: a real pytest repository, its run repository and staging, the
real `_run_delivery_redrive` — routing, the group's real commit, the ratchet,
the re-checks, the falsifier's real gates — with only `claude_p` stubbed,
and the TS AST facts, since the probes here are Python. Item 1 is a
deliverable s1 owns; item 2 is a process constraint with judge-only
evidence, so it reaches no worker (`no_code_target`). Synthetic text."""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import pytest

from tests.conftest import run_git_out as _git
from tests.test_delivery_detection import (_claim, _facts_for, _placed, _st,
                                           _staging)
from tests.test_met_items_refalsified_after_kept_rounds import (
    EFFORTS, FIXED_MUL, ITEM0, ITEM1, MODELS, _fix_mul, _repo)

ITEM2 = "no change leaves mul(2, 3) returning anything but 6"


@pytest.fixture(autouse=True)
def _quiet_host(leerie, monkeypatch):
    monkeypatch.setattr(leerie, "_host_cpu_psi", lambda *a, **k: 0.0)
    leerie._DEPS_INSTALLED.clear()
    yield
    leerie._DEPS_INSTALLED.clear()


def _replay(leerie, tmp_path, monkeypatch, *, item2_met_when_fixed: bool):
    repo, base, head = _repo(tmp_path)
    items = [{"item": ITEM0, "span": "S1", "verifiability": "tree"},
             {"item": ITEM1, "span": "S2", "verifiability": "tree"},
             {"item": ITEM2, "span": "S3", "verifiability": "tree"}]
    st = _st(leerie, tmp_path, repo, base, required_items=items)
    staging = _staging(st, repo, head)
    st.data["subtask_status"] = {"s1": "complete"}
    st.data["delivery_gate"] = {
        "final_sha": head,
        "final_tree": _git(staging, "rev-parse", "HEAD^{tree}"),
        "unmet_final": [
            {"item_index": 1, "item": ITEM1, "evidence": "mul adds",
             "by": ["judge"], "evidence_by": {"judge": "mul adds"}},
            {"item_index": 2, "item": ITEM2, "evidence": "mul adds",
             "by": ["judge"], "evidence_by": {"judge": "mul adds"}}],
        "items": [
            {"item_index": 0, "item": ITEM0, "verdict": "met", "by": [],
             "falsifier": {"confirmed": False, "samples": []}},
            {"item_index": 1, "item": ITEM1, "verdict": "unmet",
             "by": ["judge"]},
            {"item_index": 2, "item": ITEM2, "verdict": "unmet",
             "by": ["judge"]}]}
    st.save()
    (st.run_dir / "plan.json").write_text(json.dumps({"subtasks": {
        "s1": {"id": "s1", "title": "calc", "intent": "fix calc",
               "change_kind": "fix", "files_likely_touched": ["calc.py"],
               "claims": {"sites": [], "items": [
                   {"item_index": 0, "kind": "deliverable"},
                   {"item_index": 1, "kind": "deliverable"},
                   {"item_index": 2, "kind": "process"}]}}}}))
    judged: list[tuple[str, list[int]]] = []

    async def workers(**kw):
        key = kw["schema_key"]
        now = _git(staging, "rev-parse", "HEAD")
        if key == "implementer":
            _fix_mul(staging)
            return {"status": "complete", "summary": "fixed mul"}
        if key == "verifiability_classifier":
            return {"items": [{"item_index": i, "verifiability": "in_tree",
                               "reason": "r"} for i in range(3)]}
        if key == "delivery_ratchet":
            listed = json.loads(kw["user_prompt"].split(
                "Re-verify each against the CURRENT tree):\n", 1)[1]
                .split("\n\nCHANGES SINCE", 1)[0])
            return {"verdicts": [
                {"item_index": e["item_index"], "met": True, "parts": [],
                 "evidence": "kept", "evidence_files": []} for e in listed]}
        if key == "delivery_judge":
            asked = sorted({int(x) for x in re.findall(
                r'"item_index": (\d+)', kw["user_prompt"])})
            judged.append((now, asked))
            fixed = FIXED_MUL in (staging / "calc.py").read_text()
            return {"verdicts": [{
                "item_index": i, "evidence": "read",
                "met": (i == 0 or (fixed and (
                    i == 1 or item2_met_when_fixed))),
                "parts": [{"requirement": "r", "kind": "static",
                           "status": "met", "cites": [{
                               "path": "calc.py", "line_start": 1,
                               "line_end": 1, "quote": "def add(a, b):"}]}]}
                for i in asked]}
        assert key == "falsifier", key
        return _claim(verdict="could_not_demonstrate", probe_path="")
    monkeypatch.setattr(leerie, "claude_p", workers)
    monkeypatch.setattr(leerie, "_ts_resolvable", lambda tree, **_kw: True)
    monkeypatch.setattr(
        leerie, "_probe_case_facts_placed",
        _placed(lambda tree, probe, case, **_kw:
                _facts_for(Path(probe).read_text())))
    caps = dict(leerie.DEFAULT_CAPS)
    caps.update(max_parallel=1, falsifier_samples=3)
    asyncio.run(leerie._run_delivery_redrive(st.run_dir, st, caps, MODELS,
                                             EFFORTS))
    return st, staging, head, judged


def _row(gate: dict, i: int) -> dict:
    return next(r for r in gate["items"] if r["item_index"] == i)


@pytest.mark.parametrize("item2_met_when_fixed", [False, True])
def test_an_unrouted_item_is_read_on_the_tree_the_verdict_is_keyed_to(
        leerie, tmp_path, monkeypatch, item2_met_when_fixed):
    """Item 2 reaches no worker, but the kept group moved the tree: it is
    judged on `final`, and its verdict is the one read there — met when the
    tree meets it, unmet with that read's evidence otherwise. Under the
    old shared commit, item 2 kept detection's verdict of `head` while
    `final_sha` said `final`."""
    st, staging, head, judged = _replay(
        leerie, tmp_path, monkeypatch,
        item2_met_when_fixed=item2_met_when_fixed)
    final = _git(staging, "rev-parse", "HEAD")
    assert final != head
    rd = st.data["delivery_redrive"]
    assert [(u["item_index"], u["reason"])
            for u in rd.get("unrouted_items") or []] == (
        [] if item2_met_when_fixed else [(2, "no_code_target")])
    assert any(sha == final and 2 in asked for sha, asked in judged)
    gate = st.data["delivery_gate"]
    assert gate["final_sha"] == final
    assert gate["final_tree"] == _git(staging, "rev-parse", "HEAD^{tree}")
    row2 = _row(gate, 2)
    assert row2["redrive_recheck"] == {"sha": final}
    assert row2["verdict"] == ("met" if item2_met_when_fixed else "unmet")
    assert ([u["item_index"] for u in gate["unmet_final"]]
            == ([] if item2_met_when_fixed else [2]))
    # The rule itself: every verdict keyed to `final` was read on it.
    for r in gate["items"]:
        assert (r.get("redrive_recheck") or {}).get("sha") == final, r


def test_items_unread_decides_per_item(leerie, tmp_path):
    st = leerie.State(tmp_path / "s", "r", repo_root=tmp_path)
    st.data = {"delivery_gate": {"items": [
        {"item_index": 0, "verdict": "unmet"},
        {"item_index": 1, "verdict": "unmet", "redrive_recheck": {"sha": "F"}},
        {"item_index": 2, "verdict": "met", "redrive_recheck": {"sha": "E"}},
        {"item_index": 3, "verdict": "unmet"}]}}
    unread = leerie._redrive_items_unread
    # Detection read 0 and 3 on D; the re-check read 1 on F and 2 on E.
    assert unread(st, [3, 0, 1, 2, 0], "F", "D") == [0, 2, 3]
    assert unread(st, [0, 1, 2, 3], "D", "D") == [1, 2]
    assert unread(st, [0, 1, 2, 3], "E", "D") == [0, 1, 3]
    # An index with no row was read where detection read.
    assert unread(st, [7], "D", "D") == [] and unread(st, [7], "F", "D") == [7]
