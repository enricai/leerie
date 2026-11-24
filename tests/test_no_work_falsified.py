"""No work rests on the bar a met item has, so the falsifier is asked first
(DESIGN §8).

Barnacle #788 (leerie 0.100.0, run 8978e20f): three held-out sets were
discarded as fixed on HEAD and two as invalid, the no-work judge said it had
not run the tests, and the run ended as no work. The falsifier probe batch 1
confirmed on the tree merged as #808 still fails on that HEAD.

These run the real `_settle_pending_no_work`, `_no_work_unless_falsified` and
`_falsify_no_work`; the held-out results and the falsifier itself
(`_falsify_items`, a worker) are stubbed, and HEAD is a fixed sha.
"""
from __future__ import annotations

import asyncio

import pytest

from tests.test_acceptance_no_work import (  # noqa: F401  (fixture import)
    _SETS,
    _results,
    _sibling,
    _st,
    finished,
)

HEAD = "a" * 40
ITEMS = [{"item": "the reported GET stays a GET", "verifiability": "tree"},
         {"item": "the fix is at the root cause", "verifiability": "tree"},
         {"item": "a human checks the live site", "verifiability": "external"}]


def _falsifier(leerie, monkeypatch, confirm: set[int], *, skipped=None,
               raises=None):
    calls: list[dict] = []

    async def head(_tree):
        return HEAD

    async def fake(st, caps, models, efforts, **kw):
        calls.append(kw)
        if raises is not None:
            raise raises
        for i in kw["indices"]:
            ok = i in confirm
            kw["progress"]["items"][str(i)] = {"confirmed": ok, "samples": [{
                "confirmed": ok, "verdict": ("unmet_demonstrated" if ok
                                             else "could_not_demonstrate"),
                "case_name": f"variant {i} still fails",
                "probe_path": f"src/probe-i{i}.test.ts",
                "reason": f"the probe of item {i} fails under B*"}]}
        return {"enabled": True, **({"skipped": skipped} if skipped else {})}

    monkeypatch.setattr(leerie, "_branch_head_sha", head)
    monkeypatch.setattr(leerie, "_falsify_items", fake)
    return calls


def _settle(leerie, monkeypatch, st, results, acceptance=None):
    async def on_head(st_, caps):
        return results
    monkeypatch.setattr(leerie, "_acceptance_results_on_head", on_head)
    st.data["acceptance"] = acceptance or {"sets": _SETS}
    st.data["required_items"] = ITEMS
    st.data["no_work_pending"] = True
    st.data["no_work_confirmation"] = {"classifier_evidence": "c",
                                       "judge_evidence": "j"}
    return asyncio.run(leerie._settle_pending_no_work_failing_open(
        st, dict(leerie.DEFAULT_CAPS), {}, {}))


def test_a_shown_item_disputes_a_no_work_with_no_valid_set(
        leerie, tmp_path, monkeypatch, finished):
    st = _st(leerie, tmp_path)
    calls = _falsifier(leerie, monkeypatch, {1})
    assert _settle(leerie, monkeypatch, st, None) is False
    assert finished == []
    [kw] = calls
    assert kw["base_claim"] is True and kw["sha"] == HEAD
    assert kw["indices"] == [0, 1]            # never the external item
    acc = st.data["no_work_acceptance"]
    assert acc["verdict"] == "falsifier dispute"
    assert [d["item_index"] for d in acc["falsifier"]] == [1]
    ev = st.data["no_work_dispute"]["judge_evidence"]
    assert "the fix is at the root cause" in ev
    assert "variant 1 still fails" in ev and "variant 0" not in ev
    assert st.data["acceptance_dispute"]["falsifier"] is True
    assert "no_work_pending" not in st.data
    assert st.data["no_work_falsifier"]["sha"] == HEAD


def test_every_set_fixed_on_head_is_falsified_too(leerie, tmp_path,
                                                 monkeypatch, finished):
    """The #788 shape: no valid set, every written one fixed on HEAD."""
    st = _st(leerie, tmp_path)
    _falsifier(leerie, monkeypatch, {1})
    acc = {"sets": [], "written": 2, "discarded": [
        {"index": 1, "reason": "fixed_on_head_by_execution"},
        {"index": 2, "reason": "fixed_on_head_by_execution"}]}
    assert _settle(leerie, monkeypatch, st, None, acc) is False
    assert finished == []
    rec = st.data["no_work_acceptance"]
    assert rec["verdict"] == "falsifier dispute" and rec["discarded"]


def test_passing_sets_and_no_shown_item_end_as_no_work(
        leerie, tmp_path, monkeypatch, finished):
    st = _st(leerie, tmp_path)
    calls = _falsifier(leerie, monkeypatch, set())
    assert _settle(leerie, monkeypatch, st, _results([True, True, True]))
    assert len(calls) == 1 and len(finished) == 1
    assert st.data["no_work_acceptance"]["verdict"] == "pass"
    assert st.data["no_work_falsifier"]["confirmed"] == []
    assert "acceptance_dispute" not in st.data


def test_a_failing_set_disputes_without_the_falsifier(
        leerie, tmp_path, monkeypatch, finished):
    st = _st(leerie, tmp_path)
    calls = _falsifier(leerie, monkeypatch, {0, 1})
    assert _settle(leerie, monkeypatch, st,
                   _results([False, True, True])) is False
    assert calls == [] and st.data["no_work_acceptance"]["verdict"] == "dispute"


def test_a_shown_item_after_a_prior_dispute_is_accepted_at_the_bound(
        leerie, tmp_path, monkeypatch, finished):
    st = _st(leerie, tmp_path)
    _sibling(st, "run-prev", dispute={"failing_sets": 0, "falsifier": True,
                                      "cases": ["item 1: x"]})
    _falsifier(leerie, monkeypatch, {1})
    assert _settle(leerie, monkeypatch, st, None) is True
    assert len(finished) == 1
    assert "variant 1 still fails" in next(iter(finished[0].values()))
    assert st.data["no_work_acceptance"]["verdict"] == "accepted after dispute"
    assert st.data["acceptance_dispute"]["accepted"] is True


def test_a_falsifier_that_cannot_run_leaves_the_no_work(
        leerie, tmp_path, monkeypatch, finished):
    st = _st(leerie, tmp_path)
    _falsifier(leerie, monkeypatch, set(), skipped="no scoped test runner")
    assert _settle(leerie, monkeypatch, st, None) is True
    assert st.data["no_work_acceptance"]["verdict"] == "no valid sets"
    assert st.data["no_work_falsifier"]["skipped"] == "no scoped test runner"


def test_budget_exhaustion_in_the_falsifier_stops_the_run(
        leerie, tmp_path, monkeypatch, finished):
    st = _st(leerie, tmp_path)
    _falsifier(leerie, monkeypatch, set(),
               raises=leerie.WorkerError("worker budget exhausted"))
    with pytest.raises(leerie.WorkerError):
        _settle(leerie, monkeypatch, st, None)
    assert finished == []
