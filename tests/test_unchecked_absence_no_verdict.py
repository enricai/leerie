"""An absence claim no search checked is no verdict (DESIGN §8 *The judge
cites, and code checks every citation*).

Barnacle batch 2, #748 (leerie 0.95.0): every judge sample found item 0
("names no site or plugin") met, citing the absence of the site's names as
`ABSENT:<file>` with a regex led by `(?i)`. `git grep -E` cannot read `(?i)`
(exit 128, `bad_regex`), and the screen counted that met as an unmet vote, so
the item was confirmed unmet and the run could not converge. Across both
batches 13 of the 102 absence citations on met parts were such regexes.

These run the real `_verify_delivery_cite`, `_delivery_screen_sample`,
`_delivery_uninspected` and `_delivery_judge_unmet` (only `claude_p` stubbed)
over a real git tree.
"""
from __future__ import annotations

import asyncio

from tests.test_delivery_checklist import EFFORTS, MODELS, _part, _sender_tree

PCRE = {"path": "ABSENT:src/", "quote": "(?i)workday|myworkday"}
EMPTY = {"path": "ABSENT", "quote": ""}
VALID = {"path": "ABSENT:src/", "quote": "workday|myworkday"}


def _met(cite: dict, i: int = 0) -> dict:
    return {"item_index": i, "met": True, "evidence": "no site names",
            "parts": [{**_part(0), "cites": [cite]}]}


def test_an_unreadable_regex_is_bad_regex_and_verifies_nothing(leerie,
                                                               tmp_path):
    tree = _sender_tree(tmp_path)
    v = leerie._verify_delivery_cite
    assert v(PCRE, tree, "", {}) == "bad_regex"
    assert v(VALID, tree, "", {}) == "absent_ok"
    assert "bad_regex" not in leerie._DELIVERY_CITE_OK
    assert leerie._ABSENT_UNCHECKED == {"absent_unmeasured", "bad_regex"}


def test_a_met_held_only_by_an_unreadable_regex_is_re_asked_not_unmet(
        leerie, tmp_path):
    tree = _sender_tree(tmp_path)
    for cite in (PCRE, EMPTY):
        scr = leerie._delivery_screen_sample(
            {"verdicts": [_met(cite)]}, tree, "", {})
        v = scr["verdicts"][0]
        assert v["parts"][0]["status"] == "not_inspected", (cite, v)
        assert v.get("rejected") != "part_uncited", (cite, v)
        assert leerie._delivery_uninspected(scr, [0]) == [0]


def test_the_748_pass_does_not_confirm_the_item_unmet(leerie, tmp_path,
                                                     monkeypatch):
    """The real `_delivery_judge_unmet`, three samples all met, two citing a
    `(?i)` regex (and its re-asks citing it again): before the fix those two
    were unmet votes, a 2-of-3 majority confirming the item unmet. Now they
    are re-asked and the item is never confirmed unmet."""
    item = "The fix and its tests do not name any site from the report."
    staging = _sender_tree(tmp_path)
    st = leerie.State(tmp_path / ".leerie", "abs")
    st.data = {"task": "fix it, site-agnostic", "worker_count": 0,
               "required_items": [item]}
    st.save()
    cites = iter([PCRE, VALID, PCRE] + [PCRE] * 10)
    calls: list[str] = []

    async def fake(**kw):
        calls.append(kw["schema_key"])
        if kw["schema_key"] != "delivery_judge":
            return {"sets": []}
        c = next(cites)
        return {"verdicts": [{"item_index": 0, "met": True,
                              "evidence": "no site names",
                              "parts": [{"requirement": item,
                                         "kind": "static",
                                         "checklist_index": 0,
                                         "status": "met", "cites": [c]}]}]}
    monkeypatch.setattr(leerie, "claude_p", fake)
    details: dict = {}
    unmet, _, _ = asyncio.run(leerie._delivery_judge_unmet(
        st.data["task"], st.data["required_items"], staging, st,
        dict(leerie.DEFAULT_CAPS), MODELS, EFFORTS, "recheck",
        full_vote={0}, item_details=details))
    assert unmet == [], details
    assert details[0]["unmet_votes"] == 0, details


def test_a_real_refutation_beside_it_still_stands(leerie, tmp_path):
    tree = _sender_tree(tmp_path)
    made_up = {"path": "src/a.py", "line_start": 1, "line_end": 1,
               "quote": "export const nowhere_in_this_file = 42;"}
    scr = leerie._delivery_screen_sample({"verdicts": [{
        "item_index": 0, "met": True, "evidence": "e",
        "parts": [{**_part(0), "cites": [PCRE]},
                  {**_part(1), "cites": [made_up]}]}]}, tree, "", {})
    assert scr["verdicts"][0]["rejected"] == "part_uncited"
    assert leerie._delivery_uninspected(scr, [0]) == []
    assert not leerie._part_is_mechanical_unmet(
        {**_part(0), "cites": [PCRE]}, tree, "", {})
    assert leerie._part_is_mechanical_unmet(
        {**_part(1), "cites": [made_up]}, tree, "", {})


def test_an_absence_a_search_found_is_still_a_refutation(leerie, tmp_path):
    tree = _sender_tree(tmp_path)
    found = {"path": "ABSENT:src/", "quote": "def |const |import "}
    assert leerie._verify_delivery_cite(found, tree, "", {}) == "absent_found"
    scr = leerie._delivery_screen_sample({"verdicts": [_met(found)]},
                                         tree, "", {})
    assert scr["verdicts"][0]["met"] is False
    assert scr["verdicts"][0].get("rejected")
