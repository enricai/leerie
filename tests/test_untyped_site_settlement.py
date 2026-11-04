"""A site resting on an untyped resolution is settled by the run (DESIGN §8;
§6 *The PR's end-state section*).

The driver: the user's 0.44.0 run 48c48629 (barnacle#665) put "Check
`src/scraper/session-shared.ts` … (D8) is resolved" under "Needs human
verification" — a reading of the repository's own code, under the heading
kept for a resource a human must bring. The delivery judge now settles each
such site with citations code verifies; what it does not settle is listed
under "Not verified by this run". Each test executes the renderer or the gate pass and reads the
rendered text or the recorded settlement.
"""
from __future__ import annotations

import asyncio

from tests.test_delivery_redrive import World

ITEMS = [{"item": "a is exported", "span": "S0", "verifiability": "tree"}]
ADVISORY = "**Fix sites resting on an untyped change the run did not settle**"


def _claims(settlement: dict | None = None) -> dict:
    d8 = {"site_id": "D8", "file": "src/scraper/session-shared.ts",
          "symbol": "module (shared session helpers)",
          "resolution": "share one session helper", "status": "claimed",
          "surface_other": True}
    if settlement is not None:
        d8["settlement"] = settlement
    return {"domain": "bug-fixing", "sites": [
        d8, {"site_id": "D1", "file": "src/a.ts", "symbol": "f",
             "resolution": "guard", "status": "claimed",
             "surface_other": False}]}


def test_an_untyped_site_is_never_a_human_check(leerie):
    for settlement in (None, {"settled": False, "evidence": ""},
                       {"settled": True, "evidence": ""}):
        state = {"site_claims": _claims(settlement)}
        assert leerie._pr_needs_human(state, lambda _s: True) == {}
        assert "Needs human verification" not in (
            leerie._format_pr_end_state_section(state))


def test_a_settled_site_renders_nothing(leerie):
    state = {"site_claims": _claims({"settled": True, "evidence": "e"})}
    assert leerie._format_pr_end_state_section(state) == ""


def test_an_unsettled_site_is_not_verified_with_the_judges_evidence(leerie):
    for settlement, tail in (
            (None, ""),
            ({"settled": False, "evidence": "the helper is still copied"},
             " — the delivery judge: the helper is still copied")):
        sec = leerie._format_pr_end_state_section(
            {"site_claims": _claims(settlement)})
        assert sec.startswith("\n## Not verified by this run\n")
        assert (ADVISORY + "\n- [ ] `src/scraper/session-shared.ts` `module "
                "(shared session helpers)` (D8): share one session helper"
                + tail) in sec, sec
        assert "(D1)" not in sec and "Needs human verification" not in sec


def _gate(leerie, tmp_path, monkeypatch, site_verdicts) -> dict:
    """One pre-finalize gate pass whose judge answers `site_verdicts`
    (each sample the same); returns D8's recorded row and the prompt."""
    w = World(leerie, tmp_path, items=ITEMS)
    st, staging = w.st, w.staging
    st.data["site_claims"] = _claims()
    st.save()
    line1 = (staging / "src" / "a.ts").read_text().splitlines()[0]
    asked: list[str] = []

    async def judge(**kw):
        assert kw["schema_key"] == "delivery_judge"
        asked.append(kw["user_prompt"])
        return {"verdicts": [{"item_index": 0, "met": True, "evidence": "e",
                              "parts": [{"requirement": "r",
                                         "kind": "static", "status": "met",
                                         "cites": [{"path": "src/a.ts",
                                                    "line_start": 1,
                                                    "line_end": 1,
                                                    "quote": line1}]}]}],
                "contract": {"verdict": "met", "evidence": "holds"},
                "site_verdicts": [
                    {**v, "cites": [{**c, "quote": c["quote"].replace(
                        "LINE1", line1)} for c in v["cites"]]}
                    for v in site_verdicts]}
    monkeypatch.setattr(leerie, "claude_p", judge)
    caps = {**leerie.DEFAULT_CAPS, "max_parallel": 1}
    asyncio.run(leerie._delivery_judge_unmet(
        "task", ITEMS, staging, st, caps, {"delivery_judge": "sonnet"},
        {"delivery_judge": "medium"}, "pre",
        contract_ctx={"defect_shape": "s", "sites": []}))
    row = next(r for r in st.data["site_claims"]["sites"]
               if r["site_id"] == "D8")
    return {"row": row, "asked": asked,
            "other": next(r for r in st.data["site_claims"]["sites"]
                          if r["site_id"] == "D1")}


CITE = {"path": "src/a.ts", "line_start": 1, "line_end": 1, "quote": "LINE1"}


def test_the_gate_asks_for_the_site_and_settles_it_on_a_verified_quote(
        leerie, tmp_path, monkeypatch):
    got = _gate(leerie, tmp_path, monkeypatch, [
        {"site_id": "D8", "resolved": True, "cites": [CITE],
         "evidence": "one helper"}])
    assert "SITES RESTING ON AN UNTYPED RESOLUTION" in got["asked"][0]
    assert '"site_id": "D8"' in got["asked"][0]
    assert '"site_id": "D1"' not in got["asked"][0]
    assert got["row"]["settlement"]["settled"] is True
    assert "settlement" not in got["other"]
    assert leerie._format_pr_end_state_section(
        {"site_claims": {"sites": [got["row"]]}}) == ""


def test_a_fabricated_quote_or_a_no_settles_nothing(leerie, tmp_path,
                                                    monkeypatch):
    for i, verdict in enumerate((
            {"site_id": "D8", "resolved": True, "evidence": "made up",
             "cites": [{**CITE, "quote": "export const nowhere = 42;"}]},
            {"site_id": "D8", "resolved": True, "evidence": "no cite",
             "cites": []},
            {"site_id": "D8", "resolved": False, "cites": [],
             "evidence": "the helper is still copied"})):
        (tmp_path / str(i)).mkdir()
        got = _gate(leerie, tmp_path / str(i), monkeypatch, [verdict])
        assert got["row"]["settlement"]["settled"] is False, verdict
        sec = leerie._format_pr_end_state_section(
            {"site_claims": {"sites": [got["row"]]}})
        assert ADVISORY in sec and "(D8)" in sec, sec
