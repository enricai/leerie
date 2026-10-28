"""A test outcome is the failing-test-ID diff's to settle, never prose
(DESIGN §8).

Replay shape (barnacle 45fa4253, pass 1): the delivery judges held item 0
unmet on its part "No regressions: final-tree full test run must not show
new failures", read from a record that said only "red". The delivery
re-drive then sent that unmet item to implementers for its whole budget;
each ran its own base-vs-tree comparison, committed nothing, and the item
stayed unmet. Here such an item or part is settled from `test_id_diff` in
code: never judged, never falsified, never re-driven."""
from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

from tests.stub_helpers import (DELIVERY_CITED_FILE, DELIVERY_CITED_LINE,
                                _delivery_tree, _judge_verdict)

MODELS = {"delivery_judge": "sonnet", "delivery_ratchet": "sonnet",
          "verifiability_classifier": "sonnet"}
EFFORTS = {"delivery_judge": "medium", "delivery_ratchet": "medium",
           "verifiability_classifier": "medium"}
NO_REGRESSIONS = ("No regressions: final-tree full test run must not show "
                  "new failures")


def _state(leerie, tmp_path, **data):
    root = tmp_path / ".leerie"
    st = leerie.State(root, "test-outcome")
    st.data = {"task": "t", "worker_count": 0, **data}
    st.save()
    return st


def _tree_sha(tree: Path) -> str:
    return subprocess.run(["git", "-C", str(tree), "rev-parse", "HEAD^{tree}"],
                          capture_output=True, text=True,
                          check=True).stdout.strip()


def _part(kind: str, status: str, cited: bool = True) -> dict:
    return {"requirement": "r", "kind": kind, "status": status,
            "cites": ([{"path": DELIVERY_CITED_FILE, "line_start": 1,
                        "line_end": 1, "quote": DELIVERY_CITED_LINE}]
                      if cited else [])}


class TestVerdict:
    def test_met_unmet_unknown(self, leerie, tmp_path):
        st = _state(leerie, tmp_path)
        v = leerie._test_outcome_verdict
        assert v(st, "t1") == {"verdict": "unknown", "new": []}
        st.data["test_id_diff"] = {"new": [], "tree_sha": "t1"}
        assert v(st, "t1")["verdict"] == "met"
        # The tree moved since the diff: no evidence for this tree.
        assert v(st, "t2")["verdict"] == "unknown"
        st.data["test_id_diff"] = {"new": [], "tree_sha": "t1",
                                   "ids_unknown": True}
        assert v(st, "t1")["verdict"] == "unknown"
        st.data["test_id_diff"] = {"new": ["a.test.ts > b"], "tree_sha": "t0"}
        assert v(st, "t1") == {"verdict": "unmet", "new": ["a.test.ts > b"]}


class TestDropParts:
    def test_replay_verdict_stays_unmet_on_its_other_parts(self, leerie):
        # The replay's judge: the test-outcome part unmet, and a real
        # in-tree part unmet too. Only the test-outcome part is dropped.
        v = {"item_index": 0, "met": False, "evidence": "e",
             "parts": [_part("test_outcome", "unmet", cited=False),
                       _part("static", "unmet")]}
        out = leerie._drop_test_outcome_parts(v)
        assert out["met"] is False
        assert [p["kind"] for p in out["parts"]] == ["static"]

    def test_unmet_only_on_the_test_outcome_is_met(self, leerie):
        v = {"item_index": 0, "met": False, "evidence": "e",
             "parts": [_part("test_outcome", "unmet", cited=False),
                       _part("behavioral", "met")]}
        out = leerie._drop_test_outcome_parts(v)
        assert out["met"] is True and "settled_by" not in out
        only = leerie._drop_test_outcome_parts(
            {"item_index": 0, "met": False, "evidence": "e",
             "parts": [_part("test_outcome", "unmet", cited=False)]})
        assert only["met"] is True and only["settled_by"] == "test_id_diff"

    def test_screened_sample_votes_met(self, leerie, tmp_path):
        tree = _delivery_tree(tmp_path / "tree")
        smp = {"verdicts": [
            {"item_index": 0, "met": False, "evidence": "e",
             "parts": [_part("test_outcome", "unmet", cited=False),
                       _part("static", "met")]},
            {"item_index": 1, "met": False, "evidence": "e",
             "parts": [_part("test_outcome", "unmet", cited=False)]}]}
        out = leerie._delivery_screen_sample(smp, tree, "")
        assert [v["met"] for v in out["verdicts"]] == [True, True]
        assert leerie._delivery_unmet_majority([out], 2) == []


class TestGate:
    def test_whole_item_is_never_judged_or_confirmed(
            self, leerie, tmp_path, monkeypatch):
        tree = _delivery_tree(tmp_path / "tree")
        items = [{"item": "the export keeps every row", "span": "S1",
                  "verifiability": "tree",
                  "parts": ["keeps every row", NO_REGRESSIONS],
                  "part_checks": [{"part": NO_REGRESSIONS,
                                   "verifiability": "execution",
                                   "check": "test_id_diff"},
                                  {"part": "not one of the parts",
                                   "verifiability": "execution",
                                   "check": "test_id_diff"}]},
                 {"item": NO_REGRESSIONS, "span": "S2",
                  "verifiability": "execution", "check": "test_id_diff"}]
        st = _state(leerie, tmp_path, test_id_diff={
            "new": ["x.test.ts > y"], "tree_sha": _tree_sha(tree)})
        calls: list[dict] = []

        async def fake_claude_p(**kw):
            calls.append(kw)
            return {"verdicts": [_judge_verdict(0, True, "ok")]}
        monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
        routing: dict = {}
        unmet, n, _c = asyncio.run(leerie._delivery_judge_unmet(
            "task", items, tree, st, dict(leerie.DEFAULT_CAPS), MODELS,
            EFFORTS, phase_label="final", routing=routing))
        entries = json.loads(
            calls[0]["user_prompt"].split("):\n", 1)[1].split("\n(These")[0])
        assert [e["item_index"] for e in entries] == [0]
        assert entries[0]["test_outcome_parts"] == [NO_REGRESSIONS]
        assert routing["test_outcome"] == [
            {"item_index": 1, "verdict": "unmet", "new": ["x.test.ts > y"]}]
        # Unmet by the diff, but never confirmed: nothing for a fixer.
        assert unmet == []

    def test_detection_rows_and_unknown_goes_to_the_pr(
            self, leerie, tmp_path):
        st = _state(leerie, tmp_path)
        leerie._set_test_outcome_gap(st, [NO_REGRESSIONS])
        entries = [e for e in st.data["needs_human_verification"]
                   if e["source"] == "test_outcome"]
        assert len(entries) == 1 and NO_REGRESSIONS in entries[0]["detail"]
        groups = leerie._pr_needs_human(st.data, lambda sid: True)
        assert any(NO_REGRESSIONS in line
                   for lines in groups.values() for line in lines)
        leerie._set_test_outcome_gap(st, [])
        assert not [e for e in st.data["needs_human_verification"]
                    if e["source"] == "test_outcome"]


class TestRatchet:
    def test_typed_item_is_execution_without_the_classifier(
            self, leerie, tmp_path, monkeypatch):
        tree = _delivery_tree(tmp_path / "tree")
        items = [{"item": NO_REGRESSIONS, "span": "S1",
                  "verifiability": "execution", "check": "test_id_diff"}]
        st = _state(leerie, tmp_path, required_items=items,
                    delivery_gate={"items": [
                        {"item_index": 0, "verdict": "met", "judge": {}}]})
        called: list[str] = []

        async def fake_claude_p(**kw):
            called.append(kw["sid"])
            return {"items": []}
        monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
        out = asyncio.run(leerie._delivery_ratchet(
            st, dict(leerie.DEFAULT_CAPS), MODELS, EFFORTS, tree, "a", "b",
            exec_result={"new": ["x.test.ts > y"], "resource_affected": [],
                         "unmeasured": False}, label="t"))
        assert called == []
        assert out["execution"] == [0]
        assert out["regressed"] == [0]
