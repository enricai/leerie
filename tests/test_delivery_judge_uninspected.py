"""Every required item is judged before its verdict counts (DESIGN §8
*Every item is judged before its vote counts*).

Live (barnacle runs 0d1c4033 and a2a825c2, 34 required items each, packs
of 14, 20 and 30): one delivery-judge sample in three handed 5-21 of its
pack's items back typed `not_inspected` ("Not inspected."), and two
returned 1 and 2 verdicts for a 14-item pack — at 8-55 turns of a 90-turn
budget. A `not_inspected` verdict counted as an unmet vote and an omission
as a met vote, so items no sample read were routed to the conformer as
defects, or shipped met because a sample left them out.

Real `_run_delivery_prejudge` / `_delivery_judge_unmet` /
`_run_delivery_detection` over a real git staging tree; only the judge
(`claude_p`) — and, for detection, the falsifier — is stubbed."""
from __future__ import annotations

import asyncio
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(__file__))
from stub_helpers import _delivery_tree, _judge_verdict  # noqa: E402

MODELS = {"delivery_judge": "sonnet"}
EFFORTS = {"delivery_judge": "medium"}
N_ITEMS = 34
PACK0 = list(range(20))          # `d1_pack_items` = 20, no spans: own groups
PACK1 = list(range(20, N_ITEMS))
SOURCE = "delivery_judge_not_inspected"


def _item(i: int) -> str:
    return f"requirement {i}: the delivered feature handles case {i}"


def _state(leerie, tmp_path):
    leerie_root = tmp_path / ".leerie"
    run_id = "test-uninspected"
    run_dir = leerie_root / "runs" / run_id
    staging = _delivery_tree(run_dir / "worktrees" / "staging")
    st = leerie.State(leerie_root, run_id)
    st.data = {"task": "fix every defect in the report", "worker_count": 0,
               "working_branch": "main",
               "base_sha": subprocess.run(
                   ["git", "-C", str(staging), "rev-parse", "HEAD"],
                   capture_output=True, text=True,
                   check=True).stdout.strip(),
               "required_items": [{"item": _item(i)}
                                  for i in range(N_ITEMS)]}
    st.save()
    return st, run_dir


def _unread(i: int) -> dict:
    """The live shape: met false, every part typed `not_inspected`."""
    return {"item_index": i, "met": False, "evidence": "Not inspected.",
            "parts": [{"requirement": f"case {i}", "kind": "static",
                       "status": "not_inspected", "cites": []}]}


def _asked(prompt: str) -> list[int]:
    return [int(x) for x in re.findall(r'"item_index": (\d+)', prompt)]


def _judge(leerie, monkeypatch, respond):
    """Stub claude_p: `respond(indices, sid)` returns the verdict list for a
    prompt asking `indices`; every call is recorded."""
    calls: list[dict] = []

    async def fake(**kw):
        assert kw["schema_key"] == "delivery_judge"
        idx = _asked(kw["user_prompt"])
        calls.append({"sid": kw["sid"], "indices": idx,
                      "max_turns": kw["max_turns"]})
        return {"verdicts": respond(idx, kw["sid"])}
    monkeypatch.setattr(leerie, "claude_p", fake)
    return calls


def _prejudge(leerie, st, run_dir):
    return asyncio.run(leerie._run_delivery_prejudge(
        run_dir, st, dict(leerie.DEFAULT_CAPS), MODELS, EFFORTS))


def _details(st) -> dict[int, dict]:
    return {d["item_index"]: d for d in
            st.data["delivery_gate"]["judged_before"]["details"]}


def _gap(st) -> list[dict]:
    return [e for e in st.data.get("needs_human_verification") or []
            if e.get("source") == SOURCE]


def test_uninspected_and_omitted_items_are_re_asked_and_judged(
        leerie, tmp_path, monkeypatch):
    """Pack 0's first sample types two items `not_inspected` and leaves
    two out; pack 1's returns one verdict of 14 (the live 1-of-14). Each is
    asked once more for exactly those indices, and their real verdicts are
    the ones that count: nothing is unmet, nothing unknown."""
    st, run_dir = _state(leerie, tmp_path)

    def respond(idx, sid):
        if "-r" in sid.rsplit("-s", 1)[-1]:
            return [_judge_verdict(i, True, f"re-asked {i}") for i in idx]
        if idx == PACK0:
            return [_unread(i) if i in (3, 7) else _judge_verdict(i, True)
                    for i in idx if i not in (11, 12)]
        return [_judge_verdict(20, True)]
    calls = _judge(leerie, monkeypatch, respond)
    unmet = _prejudge(leerie, st, run_dir)
    assert unmet == []
    asks = {c["sid"]: c["indices"] for c in calls}
    assert asks == {
        "delivery_judge-pre-p0-s0": PACK0,
        "delivery_judge-pre-p0-s0-r0": [3, 7, 11, 12],
        "delivery_judge-pre-p1-s0": PACK1,
        "delivery_judge-pre-p1-s0-r0": list(range(21, N_ITEMS)),
    }
    # The follow-up's budget is its own pack's.
    by_sid = {c["sid"]: c for c in calls}
    assert by_sid["delivery_judge-pre-p0-s0-r0"]["max_turns"] == 30 + 6 * 4
    det = _details(st)
    for i in (3, 7, 11, 12, *range(21, N_ITEMS)):
        assert det[i]["met_evidence"] == f"re-asked {i}"
        assert det[i]["unmet_votes"] == 0
        assert "not_inspected" not in det[i] and "uninspected" not in det[i]
    assert _gap(st) == []
    assert "not_inspected" not in st.data["delivery_gate"]["routed_before"]


def test_a_re_asked_item_found_unmet_is_voted_on_its_real_verdict(
        leerie, tmp_path, monkeypatch):
    """The follow-up's verdict is a vote like any other: an item it finds
    unmet draws the dispute samples, and is confirmed on their majority
    with the judge's own evidence, never "Not inspected."."""
    st, run_dir = _state(leerie, tmp_path)

    def respond(idx, sid):
        if sid.endswith("-s0") and idx == PACK0:
            return [_unread(i) if i == 7 else _judge_verdict(i, True)
                    for i in idx]
        return [_judge_verdict(i, i != 7, f"read {i}") for i in idx]
    calls = _judge(leerie, monkeypatch, respond)
    unmet = _prejudge(leerie, st, run_dir)
    assert [(u["item_index"], u["evidence"]) for u in unmet] == [
        (7, "read 7")]
    assert [c["sid"] for c in calls if "-p0-" in c["sid"]] == [
        "delivery_judge-pre-p0-s0", "delivery_judge-pre-p0-s0-r0",
        "delivery_judge-pre-p0-s1", "delivery_judge-pre-p0-s2"]
    assert _details(st)[7]["unmet_votes"] == 3


def test_an_item_still_uninspected_after_the_re_ask_is_unknown_and_named(
        leerie, tmp_path, monkeypatch):
    """Item 5 is never read, however asked: it is neither confirmed unmet
    (no conformer is sent a defect nobody saw) nor met — it is unknown,
    named for a human, and final detection writes its row `unknown`."""
    st, run_dir = _state(leerie, tmp_path)

    def respond(idx, sid):
        return [_unread(i) if i == 5 else _judge_verdict(i, True)
                for i in idx]
    calls = _judge(leerie, monkeypatch, respond)
    unmet = _prejudge(leerie, st, run_dir)
    assert unmet == []
    # The first sample's unknown draws the dispute samples; each sample is
    # re-asked for item 5 alone.
    assert [(c["sid"], c["indices"]) for c in calls if "-p0-" in c["sid"]
            and "-r" in c["sid"].rsplit("-s", 1)[-1]] == [
        ("delivery_judge-pre-p0-s0-r0", [5]),
        ("delivery_judge-pre-p0-s1-r0", [5]),
        ("delivery_judge-pre-p0-s2-r0", [5])]
    det = _details(st)
    assert det[5]["not_inspected"] is True and det[5]["uninspected"] == 3
    assert det[5]["unmet_votes"] == 0
    assert st.data["delivery_gate"]["routed_before"]["not_inspected"] == [5]
    (e,) = _gap(st)
    assert e["item_index"] == 5 and _item(5) in e["detail"]
    sec = leerie._format_pr_end_state_section(st.data)
    assert "Task requirements" in sec and _item(5) in sec
    # Nor for the delivery ratchet reading the judged record itself.
    prior = leerie._ratchet_prior_met_all(st, st.data["required_items"])
    assert 5 not in prior and 4 in prior

    # Final detection on the same commit reuses this pass: the row is
    # unknown, never met.
    async def no_falsifier(*a, **k):
        return {"enabled": False, "skipped": "stubbed"}
    monkeypatch.setattr(leerie, "_falsify_items", no_falsifier)
    n_calls = len(calls)
    asyncio.run(leerie._run_delivery_detection(
        run_dir, st, dict(leerie.DEFAULT_CAPS), MODELS, EFFORTS))
    assert len(calls) == n_calls
    rows = {r["item_index"]: r for r in st.data["delivery_gate"]["items"]}
    assert rows[5]["verdict"] == "unknown" and rows[5]["by"] == []
    assert "did not inspect" in rows[5]["reason"]
    assert rows[4]["verdict"] == "met"
    # Nor is it prior met for the delivery ratchet, read from the rows.
    assert 5 not in leerie._ratchet_prior_met_all(
        st, st.data["required_items"])


def test_no_re_ask_when_every_item_was_inspected(
        leerie, tmp_path, monkeypatch):
    """A sample that read every item — met, or unmet with a typed unmet
    part — costs no follow-up."""
    st, run_dir = _state(leerie, tmp_path)

    def respond(idx, sid):
        return [_judge_verdict(i, i != 25) for i in idx]
    calls = _judge(leerie, monkeypatch, respond)
    unmet = _prejudge(leerie, st, run_dir)
    assert [u["item_index"] for u in unmet] == [25]
    assert [c["sid"] for c in calls] == [
        "delivery_judge-pre-p0-s0", "delivery_judge-pre-p1-s0",
        "delivery_judge-pre-p1-s1", "delivery_judge-pre-p1-s2"]
    assert _gap(st) == []


def test_a_failed_follow_up_leaves_its_items_uninspected(
        leerie, tmp_path, monkeypatch):
    """A follow-up that crashes does not take the gate down: its items stay
    uninspected, and are unknown on the majority."""
    st, run_dir = _state(leerie, tmp_path)
    calls: list[str] = []

    async def fake(**kw):
        calls.append(kw["sid"])
        if "-r" in kw["sid"].rsplit("-s", 1)[-1]:
            raise leerie.WorkerError("stub follow-up crash")
        return {"verdicts": [_judge_verdict(i, True)
                             for i in _asked(kw["user_prompt"]) if i != 9]}
    monkeypatch.setattr(leerie, "claude_p", fake)
    assert _prejudge(leerie, st, run_dir) == []
    assert _details(st)[9]["not_inspected"] is True
    assert [e["item_index"] for e in _gap(st)] == [9]


def test_a_sample_that_inspected_nothing_is_re_asked_in_halves(leerie):
    """The follow-up is smaller than the pack that left its items: halves
    when the sample read none of them."""
    assert leerie._delivery_reask_chunks(list(range(20)), 20) == [
        list(range(10)), list(range(10, 20))]
    assert leerie._delivery_reask_chunks([3, 7], 20) == [[3, 7]]
    assert leerie._delivery_reask_chunks([4], 1) == [[4]]
    assert leerie._delivery_reask_chunks([], 20) == []


def test_uninspected_reads_typed_fields_only(leerie):
    """No verdict, or a `not_inspected` part with no `unmet` one; the
    evidence prose is never read."""
    part = lambda s: {"requirement": "r", "kind": "static", "status": s,  # noqa: E731
                      "cites": []}
    sample = {"verdicts": [
        {"item_index": 0, "met": False, "evidence": "x",
         "parts": [part("met"), part("not_inspected")]},
        {"item_index": 1, "met": False, "evidence": "Not inspected.",
         "parts": [part("unmet"), part("not_inspected")]},
        {"item_index": 2, "met": False, "evidence": "Not inspected.",
         "parts": [part("unmet")]},
        {"item_index": 3, "met": True, "evidence": "x",
         "parts": [part("met"), part("not_inspected")]},
        {"item_index": 5, "met": True, "evidence": "x",
         "settled_by": "test_id_diff", "parts": []},
    ]}
    assert leerie._delivery_uninspected(sample, range(6)) == [0, 3, 4]


def test_a_redrive_recheck_writes_an_unread_item_unknown(
        leerie, tmp_path, monkeypatch):
    """A re-drive's re-check that no sample can read writes the row
    `unknown` — not `met` because nothing confirmed it unmet."""
    st, run_dir = _state(leerie, tmp_path)
    staging = run_dir / "worktrees" / "staging"
    monkeypatch.setattr(leerie, "_delivery_typed_evidence",
                        lambda *a, **k: ([], {}))
    calls = _judge(leerie, monkeypatch, lambda idx, sid: [
        _unread(i) for i in idx])
    st.data["delivery_gate"] = {
        "unmet_final": [{"item_index": 2, "item": _item(2),
                         "evidence": "e", "by": ["judge"]}],
        "items": [{"item_index": 2, "item": _item(2), "verdict": "unmet",
                   "by": ["judge"]}]}
    out = asyncio.run(leerie._redrive_recheck_items(
        run_dir, st, dict(leerie.DEFAULT_CAPS), MODELS, EFFORTS, staging,
        [2]))
    assert out[2]["unmet"] is False and out[2]["unknown"] is True
    assert [c["indices"] for c in calls] == [[2]] * 6
    assert [e["item_index"] for e in _gap(st)] == [2]
