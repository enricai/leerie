"""A met is only what the run could observe (DESIGN §8).

A final `met` used to be "no detector showed it unmet": on navegando
a70fb4a6 an item asking for the whole site to work on phones was `met` on
held-out sets and falsifier probes that read class names in a DOM
emulation, which computes no layout, while the inbox was still broken at
375px. The observability judge types each part by how the run can observe
it; a part that neither reading nor the repository's test command can
observe (with a verified cite of the test setup, on a quorum of samples)
makes an otherwise-met item `unknown`, listed for a human. An unmet stays
unmet: a proxy can show a gap, never certify its absence.
"""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

from tests.test_delivery_detection import (EFFORTS, MODELS, _detection_env,
                                           _demonstrate)

SETUP_QUOTE = 'test = "python3 -m pytest -q -p no:cacheprovider"'


def _payload(leerie):
    items = [{"item": "the page fits a phone screen",
              "parts": ["no sideways scroll at 375px", "the menu opens"]},
             {"item": "add(2, 3) returns 5"}]
    return leerie._observability_payload(items, [0, 1])


def _neither(i, j, needs="rendering_engine", quote=SETUP_QUOTE,
             path=".leerie/config.toml"):
    return {"item_index": i, "parts": [{
        "part_index": j, "observable": "neither", "needs": needs,
        "reason": "r", "cites": [{"path": path, "line_start": 2,
                                  "line_end": 2, "quote": quote}]}]}


def _seen(*items):
    return {"items": list(items)}


# --- the decision rule (`_observability_unobservable`) -------------------

def test_payload_numbers_parts_and_makes_a_partless_item_its_own_part(
        leerie):
    p = _payload(leerie)
    assert p[0]["parts"] == [
        {"part_index": 0, "part": "no sideways scroll at 375px"},
        {"part_index": 1, "part": "the menu opens"}]
    assert p[1]["parts"] == [{"part_index": 0, "part": "add(2, 3) returns 5"}]


def test_a_quorum_with_verified_cites_flags_the_part(leerie, tmp_path,
                                                     monkeypatch):
    repo, _head, _st, _caps = _detection_env(leerie, tmp_path, monkeypatch)
    out = leerie._observability_unobservable(
        [_seen(_neither(0, 0)), _seen(_neither(0, 0)), None],
        _payload(leerie), repo)
    assert list(out) == [0]
    (part,) = out[0]
    assert part["part"] == "no sideways scroll at 375px"
    assert part["needs"] == "rendering_engine" and part["votes"] == 2
    assert part["cite"]["path"] == ".leerie/config.toml"


def test_one_vote_a_failed_sample_or_a_fabricated_cite_flags_nothing(
        leerie, tmp_path, monkeypatch):
    repo, _head, _st, _caps = _detection_env(leerie, tmp_path, monkeypatch)
    pay = _payload(leerie)
    # One vote of three.
    assert leerie._observability_unobservable(
        [_seen(_neither(0, 0)), _seen(), _seen()], pay, repo) == {}
    # Two votes, but one quote is not in the file: an unproven claim is a
    # vote for observable.
    assert leerie._observability_unobservable(
        [_seen(_neither(0, 0)),
         _seen(_neither(0, 0, quote='environment: "a real browser engine"')),
         None], pay, repo) == {}
    # A `neither` with no cite at all.
    bare = _neither(0, 0)
    bare["parts"][0]["cites"] = []
    assert leerie._observability_unobservable(
        [_seen(bare), _seen(bare), _seen(bare)], pay, repo) == {}


def test_votes_on_different_parts_do_not_add_up(leerie, tmp_path,
                                                monkeypatch):
    """The inputs disagree: two samples each call a DIFFERENT part
    unobservable. Counting per item would flag it; per part, nothing
    reaches the quorum."""
    repo, _head, _st, _caps = _detection_env(leerie, tmp_path, monkeypatch)
    out = leerie._observability_unobservable(
        [_seen(_neither(0, 0)), _seen(_neither(0, 1)), _seen()],
        _payload(leerie), repo)
    assert out == {}


def test_a_sample_repeating_a_part_counts_once_and_unknown_indices_drop(
        leerie, tmp_path, monkeypatch):
    repo, _head, _st, _caps = _detection_env(leerie, tmp_path, monkeypatch)
    twice = {"item_index": 0, "parts": [*_neither(0, 0)["parts"],
                                        *_neither(0, 0)["parts"]]}
    stray = _seen(_neither(0, 7), _neither(5, 0))
    assert leerie._observability_unobservable(
        [_seen(twice), stray, stray], _payload(leerie), repo) == {}


def test_needs_is_the_most_voted(leerie, tmp_path, monkeypatch):
    repo, _head, _st, _caps = _detection_env(leerie, tmp_path, monkeypatch)
    out = leerie._observability_unobservable(
        [_seen(_neither(0, 0, needs="physical_device")),
         _seen(_neither(0, 0, needs="rendering_engine")),
         _seen(_neither(0, 0, needs="physical_device"))],
        _payload(leerie), repo)
    assert out[0][0]["needs"] == "physical_device"
    assert out[0][0]["votes"] == 3


# --- final delivery detection, executed ---------------------------------

def _stub(leerie, monkeypatch, *, obs, falsify_i0=False):
    """claude_p: the judge says every asked item met (citing calc.py), the
    falsifier demonstrates item 0 only when `falsify_i0`, and observability
    sample n answers `obs(n)`."""
    calls: list[dict] = []

    async def fake(**kw):
        calls.append(kw)
        key = kw["schema_key"]
        if key == "delivery_judge":
            asked = sorted({int(x) for x in re.findall(
                r'"item_index": (\d+)', kw["user_prompt"])})
            return {"verdicts": [{
                "item_index": i, "met": True, "evidence": "read",
                "parts": [{"requirement": "r", "kind": "static",
                           "status": "met", "cites": [{
                               "path": "calc.py", "line_start": 1,
                               "line_end": 1,
                               "quote": "def add(a, b):"}]}]}
                for i in asked]}
        if key == "observability_judge":
            n = sum(1 for c in calls if c["schema_key"] == key) - 1
            return obs(n, kw)
        assert key == "falsifier"
        if falsify_i0 and "-i0-" in kw["sid"]:
            return _demonstrate(kw, 0)
        return {"verdict": "could_not_demonstrate", "probe_path": "",
                "case_name": "", "assertion_quote": "", "item_span_quote": "",
                "entry_point": "", "reason": "passed"}
    monkeypatch.setattr(leerie, "claude_p", fake)
    return calls


def _detect(leerie, tmp_path, monkeypatch, **kw):
    repo, head, st, caps = _detection_env(leerie, tmp_path, monkeypatch,
                                          judged=False, k=1)
    calls = _stub(leerie, monkeypatch, **kw)
    asyncio.run(leerie._run_delivery_detection(
        st.run_dir, st, caps, MODELS, EFFORTS))
    rows = {r["item_index"]: r for r in st.data["delivery_gate"]["items"]}
    return st, rows, calls


def _item3_unobservable(n, _kw):
    return _seen(_neither(3, 0)) if n < 2 else _seen()


def test_an_unobservable_part_makes_a_met_item_unknown_and_reaches_the_pr(
        leerie, tmp_path, monkeypatch):
    st, rows, calls = _detect(leerie, tmp_path, monkeypatch,
                              obs=_item3_unobservable)
    obs_calls = [c for c in calls if c["schema_key"] == "observability_judge"]
    assert len(obs_calls) == leerie.OBSERVABILITY_SAMPLES
    # The payload: the judged items, not the external or untouched-paths one.
    asked = json.loads(obs_calls[0]["user_prompt"].split(
        "REQUIRED ITEMS (each with its numbered parts):\n", 1)[1].split(
        "\n\nReturn", 1)[0])
    assert [e["item_index"] for e in asked] == [0, 3]
    assert "python3 -m pytest -q -p no:cacheprovider" in obs_calls[0][
        "user_prompt"]
    assert rows[3]["verdict"] == "unknown"
    assert rows[3]["unobservable"][0]["needs"] == "rendering_engine"
    assert rows[0]["verdict"] == "met"                     # the control
    gate = st.data["delivery_gate"]
    assert 3 not in {u["item_index"] for u in gate["unmet_final"]}
    assert 3 not in leerie._ratchet_prior_met(st)
    assert 0 in leerie._ratchet_prior_met(st)
    (e,) = [e for e in st.data["needs_human_verification"]
            if e["source"] == leerie.UNOBSERVABLE_GAP_SOURCE]
    assert e["item_index"] == 3
    assert e["detail"].startswith("the module keeps its name — not shown met")
    assert "a real rendering engine" in e["detail"]
    assert ".leerie/config.toml:2" in e["detail"]
    body = leerie._format_pr_end_state_section(st.data)
    assert "the module keeps its name — not shown met" in body
    assert gate["observability"]["sha"] == gate["final_sha"]


def test_an_unmet_item_stays_unmet_whatever_the_typing(leerie, tmp_path,
                                                       monkeypatch):
    """The asymmetry: a probe through a proxy can show a gap. Item 0 is
    confirmed unmet by the falsifier and typed unobservable by every
    sample; it stays unmet and is not listed for a human."""
    st, rows, _calls = _detect(
        leerie, tmp_path, monkeypatch, falsify_i0=True,
        obs=lambda n, kw: _seen(_neither(0, 0)))
    assert rows[0]["verdict"] == "unmet" and rows[0]["by"] == ["falsifier"]
    assert not [e for e in st.data.get("needs_human_verification") or []
                if e["source"] == leerie.UNOBSERVABLE_GAP_SOURCE]


def test_a_failing_judge_leaves_the_verdicts_as_the_detectors_left_them(
        leerie, tmp_path, monkeypatch):
    def boom(n, kw):
        raise leerie.WorkerError("down")
    st, rows, _calls = _detect(leerie, tmp_path, monkeypatch, obs=boom)
    assert rows[0]["verdict"] == "met" and rows[3]["verdict"] == "met"
    assert st.data["delivery_gate"]["observability"]["samples"] == 0


def test_the_typing_is_reused_only_on_its_commit(leerie, tmp_path,
                                                 monkeypatch):
    repo, head, st, caps = _detection_env(leerie, tmp_path, monkeypatch,
                                          judged=False, k=1)
    st.data["delivery_gate"] = {"observability": {
        "sha": "0" * 40, "samples": 3,
        "unobservable": {"0": [{"part_index": 0, "part": ITEM_TEXT,
                                "needs": "rendering_engine", "votes": 3,
                                "cite": {}}]}}}
    st.save()
    calls = _stub(leerie, monkeypatch, obs=lambda n, kw: _seen())
    asyncio.run(leerie._run_delivery_detection(
        st.run_dir, st, caps, MODELS, EFFORTS))
    rows = {r["item_index"]: r for r in st.data["delivery_gate"]["items"]}
    # Another commit's typing is not this tree's: asked again, item 0 met.
    assert [c for c in calls if c["schema_key"] == "observability_judge"]
    assert rows[0]["verdict"] == "met"


ITEM_TEXT = "add(2, 3) returns 5, the sum of its arguments"


def test_the_prompt_names_the_schema_vocabulary(leerie):
    text = (Path(leerie.__file__).parent.parent / "prompts"
            / "observability_judge.md").read_text()
    for word in (*leerie.OBSERVABLE_KINDS, *leerie.OBSERVABILITY_NEEDS):
        assert f"`{word}`" in text, word
    enum = leerie.SCHEMAS["observability_judge"]["properties"]["items"][
        "items"]["properties"]["parts"]["items"]["properties"]
    assert enum["observable"]["enum"] == list(leerie.OBSERVABLE_KINDS)
    assert enum["needs"]["enum"] == ["", *leerie.OBSERVABILITY_NEEDS]
