"""The falsifier gets the item whole, and a re-checked met is falsified again
(DESIGN §8 *The falsifier gets the item whole*, *A re-check replaces a
verdict only on a tree that changed, and only by the vote that made it*).

Live (navegando 4134b78c, leerie 0.48.0, a whole-site sweep): the item's
sentence named only the two catalog files; its fourth classifier part,
"Coverage: the entire site's user-facing strings are present in the
messages files", was where the sweep lived. The falsifier was handed the
sentence alone, demonstrated one catalog defect, and after the re-drive
fixed it the re-check re-ran only that saved probe, so the shipped tree was
never falsified and shipped `met` with components still hard-coding text.

Real `_run_delivery_detection` / `_falsify_items` / `_falsifier_gates` /
`_redrive_recheck_items` over a real pytest repository; only `claude_p` and
the TS AST facts are stubbed (the probes are Python). Synthetic text."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from tests.test_delivery_detection import (EFFORTS, MODELS, PASSING_PROBE_SRC,
                                           PROBE_REL, PROBE_SRC, _claim,
                                           _detection_env, _falsifier_stub)

SENTENCE = "the module keeps its name"
PART = "add(2, 3) returns 5 for every caller of the module"


def _whole(leerie, tmp_path, monkeypatch, *, k=1):
    repo, head, st, caps = _detection_env(leerie, tmp_path, monkeypatch, k=k)
    st.data["required_items"] = [
        {"item": SENTENCE, "span": "S1", "verifiability": "tree",
         "parts": ["the module file is not renamed", PART]}]
    st.data["delivery_gate"] = {"unmet_after": [], "judged_after": {
        "sha": head, "unmet": [], "details": [{
            "item_index": 0, "samples": 1, "unmet_votes": 0, "rejected": 0,
            "unmet_parts": []}]}}
    st.save()
    return head, st, caps


def _write(kw, src, rel=PROBE_REL):
    p = Path(kw["cwd"]) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(src)


def test_a_part_the_sentence_omits_is_probed_and_confirmed(
        leerie, tmp_path, monkeypatch):
    """The payload carries the item's parts; a probe quoting a part, not
    the sentence, is the item's own words, and is confirmed."""
    head, st, caps = _whole(leerie, tmp_path, monkeypatch)

    def behave(kw, n):
        if PART in kw["user_prompt"]:
            _write(kw, PROBE_SRC)
            return _claim(item_span_quote="returns 5 for every caller")
        return _claim(verdict="could_not_demonstrate", probe_path="")
    calls = _falsifier_stub(leerie, monkeypatch, st, behave)
    asyncio.run(leerie._run_delivery_detection(
        st.run_dir, st, caps, MODELS, EFFORTS))
    p0 = next(c for c in calls if c["schema_key"] == "falsifier")
    item = json.loads(p0["user_prompt"].split("THE ITEM TO CHECK:\n", 1)[1]
                      .split("\n\n", 1)[0])
    assert item == {"item": SENTENCE, "source_ref": "S1",
                    "parts": ["the module file is not renamed", PART]}
    gate = st.data["delivery_gate"]
    (row,) = gate["items"]
    s0 = row["falsifier"]["samples"][0]
    assert s0["gates"]["item_quote"] is True and s0["confirmed"] is True
    assert row["verdict"] == "unmet" and row["by"] == ["falsifier"]
    assert [u["item_index"] for u in gate["unmet_final"]] == [0]


def test_a_quote_in_no_part_and_not_in_the_sentence_is_refused(
        leerie, tmp_path, monkeypatch):
    head, st, caps = _whole(leerie, tmp_path, monkeypatch)

    def behave(kw, n):
        _write(kw, PROBE_SRC)
        return _claim(item_span_quote="returns 6 for every caller")
    _falsifier_stub(leerie, monkeypatch, st, behave)
    asyncio.run(leerie._run_delivery_detection(
        st.run_dir, st, caps, MODELS, EFFORTS))
    (row,) = st.data["delivery_gate"]["items"]
    s0 = row["falsifier"]["samples"][0]
    assert s0["gates"]["item_quote"] is False and not s0["confirmed"]
    assert row["verdict"] == "met"


def test_an_item_without_parts_gets_the_sentence_alone(leerie):
    got = leerie._falsifier_user_prompt(
        "t", {"item": SENTENCE, "span": "S1"}, None, "", [], [], [])
    assert ('THE ITEM TO CHECK:\n{\n "item": "the module keeps its name",'
            '\n "source_ref": "S1"\n}\n') in got
    assert leerie._falsifier_item_text(
        {"item": SENTENCE, "parts": ["a", 3, " ", "b"]}) == \
        f"{SENTENCE}\na\nb"


def _detect_then_fix(leerie, tmp_path, monkeypatch):
    """Detection confirms the item on one probe; the 'fix' then makes that
    saved probe pass (the instance it shows is fixed)."""
    head, st, caps = _whole(leerie, tmp_path, monkeypatch, k=3)

    def first(kw, n):
        _write(kw, PROBE_SRC)
        return _claim(item_span_quote="returns 5 for every caller")
    _falsifier_stub(leerie, monkeypatch, st, first)
    asyncio.run(leerie._run_delivery_detection(
        st.run_dir, st, caps, MODELS, EFFORTS))
    gate = st.data["delivery_gate"]
    saved = gate["items"][0]["falsifier"]["samples"][0]["probe_saved"]
    Path(saved["dir"], saved["file"]).write_text(PASSING_PROBE_SRC)
    return st, caps, saved


OTHER_REL = "falsify_probe2/test_other_caller.py"


def test_a_recheck_whose_probe_passes_falsifies_the_item_again(
        leerie, tmp_path, monkeypatch):
    """The saved probe now passes, but another place the part covers still
    falls short: the re-check's fresh falsifier shows it, the item stays
    unmet by the falsifier, and its new probe is the one the next re-check
    re-runs."""
    st, caps, old = _detect_then_fix(leerie, tmp_path, monkeypatch)

    def again(kw, n):
        _write(kw, PROBE_SRC, OTHER_REL)
        return _claim(probe_path=OTHER_REL,
                      item_span_quote="for every caller of the module")
    calls = _falsifier_stub(leerie, monkeypatch, st, again)
    staging = st.run_dir / "worktrees" / "staging"
    out = asyncio.run(leerie._redrive_recheck_items(
        st.run_dir, st, caps, MODELS, EFFORTS, staging, [0]))
    assert [c["sid"] for c in calls if c["schema_key"] == "falsifier"] == [
        "falsifier-i0-s0"]
    assert out[0]["unmet"] is True and out[0]["by"] == ["falsifier"]
    assert "for every caller of the module" in out[0]["evidence_by"][
        "falsifier"]
    leerie._redrive_apply_item_verdicts(st, out, "sha-after")
    gate = st.data["delivery_gate"]
    (u,) = gate["unmet_final"]
    assert u["by"] == ["falsifier"]
    saved = gate["items"][0]["falsifier"]["samples"][0]["probe_saved"]
    assert saved["file"] == OTHER_REL
    assert "final_progress" not in gate


def test_a_recheck_the_fresh_falsifier_cannot_break_reads_met(
        leerie, tmp_path, monkeypatch):
    st, caps, old = _detect_then_fix(leerie, tmp_path, monkeypatch)
    calls = _falsifier_stub(leerie, monkeypatch, st, lambda kw, n: _claim(
        verdict="could_not_demonstrate", probe_path=""))
    staging = st.run_dir / "worktrees" / "staging"
    out = asyncio.run(leerie._redrive_recheck_items(
        st.run_dir, st, caps, MODELS, EFFORTS, staging, [0]))
    assert len([c for c in calls if c["schema_key"] == "falsifier"]) == 3
    assert out[0]["unmet"] is False and out[0]["by"] == []
    leerie._redrive_apply_item_verdicts(st, out, "sha-after")
    gate = st.data["delivery_gate"]
    assert gate["unmet_final"] == []
    row = gate["items"][0]
    assert row["verdict"] == "met"
    assert not row["falsifier"]["confirmed"]
    assert len(row["falsifier"]["samples"]) == 3
