"""The keep-both swing is reachable when a probe breaks MORE than one pinned
case (DESIGN §8 *A keep-both swing before a reconcilable probe is contested*).

`_redrive_keep_both_schedule` schedules a swing only when every broken pinned
case carries a `both_can_hold` verdict for the item. `_redrive_task_required`
used to return at the first case no item settles, so a second case was never
judged and the swing could fire only on a single broken case. Measured on the
barnacle batch (#795, round 5): a probe broke two pinned cases, the first was
judged `both_can_hold`, the second never — the item contested.

These drive the real `_redrive_task_required` and then the real
`_redrive_keep_both_schedule`; only the judge (`_contradiction_verdict`, which
needs a TS payload) and the intent resolver are stubbed, recording each
verdict in `rec` as the real path does.
"""
from __future__ import annotations

import asyncio


def _stub(leerie, monkeypatch, statuses: dict[str, str]) -> list[str]:
    """Judge each case with `statuses[case id]`; returns the judged case ids
    in call order."""
    calls: list[str] = []

    async def verdict(st, caps, models, efforts, staging, rec, *, base,
                      case, item_index, probe, rnd, gi):
        cid = str(case.get("id"))
        calls.append(cid)
        v = {"case": cid, "file": case.get("file"), "item_index": item_index,
             "probe_test_file": probe.get("file"), "round": rnd, "group": gi,
             "status": statuses[cid]}
        rec.setdefault("contradiction_verdicts", []).append(v)
        return v

    async def intent(st, caps, models, efforts, v):
        return None
    monkeypatch.setattr(leerie, "_contradiction_verdict", verdict)
    monkeypatch.setattr(leerie, "_contradiction_intent", intent)
    monkeypatch.setattr(leerie, "_diff_base", lambda st: "base-sha")
    return calls


def _run(leerie, rec: dict, pinned: list[dict]) -> dict:
    return asyncio.run(leerie._redrive_task_required(
        None, {}, {}, {}, None, rec, pinned,
        {3: {"file": "probe.test.ts"}}, rnd=5, gi=0))


def _note() -> dict:
    return {"kind": "unmet_item", "item_index": 3, "item": "x",
            "claimed_by": ["s1"], "probe": {"file": "probe.test.ts"}}


PINNED = [{"id": "a.test.ts :: one", "file": "a.test.ts"},
          {"id": "b.test.ts :: two", "file": "b.test.ts"}]


def test_second_case_is_judged_and_the_swing_is_scheduled(leerie, monkeypatch):
    calls = _stub(leerie, monkeypatch, {
        "a.test.ts :: one": "both_can_hold", "b.test.ts :: two": "both_can_hold"})
    rec: dict = {}
    assert _run(leerie, rec, PINNED) == {}  # nothing task-required: rule stands
    assert calls == ["a.test.ts :: one", "b.test.ts :: two"]
    assert leerie._redrive_keep_both_schedule(
        rec, [_note()], [3], PINNED, rnd=5, gi=0) == {3}
    [n] = rec["keep_both_open"]
    assert n["keep_pinned"] == ["a.test.ts :: one", "b.test.ts :: two"]


def test_second_case_not_reconcilable_gets_no_swing(leerie, monkeypatch):
    for second in ("exclusive", "unsettled"):
        calls = _stub(leerie, monkeypatch, {
            "a.test.ts :: one": "both_can_hold", "b.test.ts :: two": second})
        rec: dict = {}
        _run(leerie, rec, PINNED)
        assert calls == ["a.test.ts :: one", "b.test.ts :: two"]
        assert leerie._redrive_keep_both_schedule(
            rec, [_note()], [3], PINNED, rnd=5, gi=0) == set()


def test_first_case_not_reconcilable_stops_judging(leerie, monkeypatch):
    # The cost of the old path is kept: a first case that is neither settled
    # nor both_can_hold ends the judging — no swing can follow it.
    calls = _stub(leerie, monkeypatch, {
        "a.test.ts :: one": "unsettled", "b.test.ts :: two": "both_can_hold"})
    rec: dict = {}
    assert _run(leerie, rec, PINNED) == {}
    assert calls == ["a.test.ts :: one"]
    assert leerie._redrive_keep_both_schedule(
        rec, [_note()], [3], PINNED, rnd=5, gi=0) == set()


def test_task_required_route_still_all_or_nothing(leerie, monkeypatch):
    # Every case exclusive: task-required, as before.
    _stub(leerie, monkeypatch, {
        "a.test.ts :: one": "exclusive", "b.test.ts :: two": "exclusive"})
    got = _run(leerie, {}, PINNED)
    assert set(got) == {"a.test.ts :: one", "b.test.ts :: two"}
    # One both_can_hold among them: nothing is task-required (the conflict
    # rule stands), even though the exclusive case was judged.
    _stub(leerie, monkeypatch, {
        "a.test.ts :: one": "both_can_hold", "b.test.ts :: two": "exclusive"})
    assert _run(leerie, {}, PINNED) == {}
