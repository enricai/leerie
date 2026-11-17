"""An item is judged on its declared parts, and a set it quantifies over is
listed before it is judged (DESIGN §8).

Measured (lane W6, the 13 false `met` verdicts the ground-truth lanes
confirmed by execution): the classifier's `parts` never reached the
delivery judge, and in 11 of the 13 a declared part was never answered; in
7 the item quantified over a set ("every place in `src/` that …") the
judge searched one file of.

Real git trees; only `claude_p` is stubbed."""
from __future__ import annotations

import asyncio
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
from stub_helpers import (DELIVERY_CITED_FILE, DELIVERY_CITED_LINE,  # noqa: E402
                          _delivery_tree)

MODELS = {"delivery_judge": "sonnet", "delivery_enumerator": "sonnet"}
EFFORTS = {"delivery_judge": "medium", "delivery_enumerator": "medium"}


def _commit(tree: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        p = tree / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    subprocess.run(["git", "-C", str(tree), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(tree), "-c", "user.email=t@t",
                    "-c", "user.name=t", "commit", "-q", "-m", "files"],
                   check=True)


def _cite() -> dict:
    return {"path": DELIVERY_CITED_FILE, "line_start": 1, "line_end": 1,
            "quote": DELIVERY_CITED_LINE}


def _part(j: int, status: str = "met") -> dict:
    return {"requirement": f"entry {j}", "kind": "static", "status": status,
            "checklist_index": j, "cites": [_cite()]}


ITEM = {"item": "every sender uses the shared client; no copies",
        "parts": ["every sender uses the shared client", "no copies",
                  "existing tests still pass"],
        "part_checks": [{"part": "existing tests still pass",
                         "verifiability": "execution",
                         "check": "test_id_diff"}]}


def test_checklist_is_the_declared_parts_less_test_outcomes(leerie):
    got = leerie._delivery_checklist(ITEM)
    assert [e["part"] for e in got] == ["every sender uses the shared client",
                                        "no copies"]
    assert [e["checklist_index"] for e in got] == [0, 1]


def test_an_item_declaring_no_parts_is_its_own_entry(leerie):
    got = leerie._delivery_checklist({"item": "the timeout is 30 seconds"})
    assert got == [{"checklist_index": 0,
                    "part": "the timeout is 30 seconds"}]


def test_an_item_of_test_outcomes_only_has_no_checklist(leerie):
    item = {"item": "no regressions", "parts": ["no regressions"],
            "part_checks": [{"part": "no regressions",
                             "verifiability": "execution",
                             "check": "test_id_diff"}]}
    assert leerie._delivery_checklist(item) == []


def test_members_follow_the_declared_entries(leerie):
    got = leerie._delivery_checklist(ITEM, [
        {"member_of": 0, "path": "src/a.py", "line": 3, "place": "a sender"},
        {"member_of": 0, "path": "src/b.py", "line": 0, "place": "b"}])
    assert [(e["checklist_index"], e.get("member_of")) for e in got] == [
        (0, None), (1, None), (2, 0), (3, 0)]
    assert got[2]["part"] == "src/a.py:3 — a sender"
    assert got[3]["part"] == "src/b.py — b"


def test_a_met_leaving_an_entry_unanswered_is_not_met(leerie, tmp_path):
    tree = _delivery_tree(tmp_path / "t")
    v = {"item_index": 0, "met": True, "evidence": "e",
         "parts": [_part(0), _part(-1)]}
    assert leerie._delivery_verdict_check(v, tree, "", {}, 2) == (
        "unverified", "checklist_unanswered")
    v["parts"].append(_part(1))
    assert leerie._delivery_verdict_check(v, tree, "", {}, 2) == ("met", "")
    # Without a checklist the old rule stands.
    assert leerie._delivery_verdict_check(
        {**v, "parts": [_part(-1)]}, tree, "", {}) == ("met", "")


def test_each_entry_needs_its_own_verified_cite(leerie, tmp_path):
    tree = _delivery_tree(tmp_path / "t")
    bad = {**_part(1), "cites": [{**_cite(), "quote": "not in the file at all"}]}
    v = {"item_index": 0, "met": True, "evidence": "e",
         "parts": [_part(0), bad]}
    assert leerie._delivery_verdict_check(v, tree, "", {}, 2) == (
        "unverified", "part_uncited")


def test_an_unanswered_entry_is_uninspected_not_an_unmet_vote(leerie,
                                                              tmp_path):
    tree = _delivery_tree(tmp_path / "t")
    lists = {0: leerie._delivery_checklist(ITEM)}
    met_one = {"item_index": 0, "met": True, "evidence": "e",
               "parts": [_part(0)]}
    smp = leerie._delivery_screen_sample({"verdicts": [met_one]}, tree, "",
                                         {}, lists)
    assert smp["verdicts"][0]["rejected"] == "checklist_unanswered"
    assert leerie._delivery_uninspected(smp, [0], lists) == [0]
    # An unmet part is a finding, whatever else is unanswered.
    found = {"item_index": 0, "met": False, "evidence": "e",
             "parts": [_part(0, "unmet")]}
    smp = leerie._delivery_screen_sample({"verdicts": [found]}, tree, "",
                                         {}, lists)
    assert leerie._delivery_uninspected(smp, [0], lists) == []
    # Every entry answered and met: inspected, met.
    whole = {**met_one, "parts": [_part(0), _part(1)]}
    smp = leerie._delivery_screen_sample({"verdicts": [whole]}, tree, "",
                                         {}, lists)
    assert smp["verdicts"][0]["met"] is True
    assert leerie._delivery_uninspected(smp, [0], lists) == []


def _sender_tree(tmp_path: Path) -> Path:
    tree = _delivery_tree(tmp_path / "t")
    _commit(tree, {"src/a.py": "x = 1\nsend(url)\n",
                   "src/b.py": "def f():\n    return send(other)\n",
                   "src/c.py": "nothing here\n",
                   "docs/d.md": "send(example)\n"})
    return tree


def test_enumeration_adds_every_file_its_searches_find(leerie, tmp_path):
    tree = _sender_tree(tmp_path)
    lists = {4: leerie._delivery_checklist(ITEM)}
    raw = {"sets": [{"item_index": 4, "checklist_index": 0,
                     "set": "senders",
                     "members": [{"path": "src/a.py", "line": 2,
                                  "place": "a sends"},
                                 {"path": "src/gone.py", "line": 1,
                                  "place": "not a file of HEAD"}],
                     "searches": [{"regex": r"send\(", "pathspec": "src/"}]}]}
    got = leerie._delivery_enumeration(raw, tree, lists)
    assert [(m["path"], m["line"]) for m in got[4]] == [
        ("src/a.py", 2), ("src/b.py", 2)]
    assert got[4][1]["place"].startswith("matches the search")


def test_enumeration_keeps_only_declared_entries_and_valid_searches(
        leerie, tmp_path):
    tree = _sender_tree(tmp_path)
    lists = {4: leerie._delivery_checklist(ITEM)}
    raw = {"sets": [
        {"item_index": 4, "checklist_index": 7, "set": "x",
         "members": [{"path": "src/a.py", "line": 2, "place": "a"}],
         "searches": []},
        {"item_index": 9, "checklist_index": 0, "set": "x",
         "members": [{"path": "src/a.py", "line": 2, "place": "a"}],
         "searches": []},
        {"item_index": 4, "checklist_index": 1, "set": "x", "members": [],
         "searches": [{"regex": "(", "pathspec": ""}]}]}
    assert leerie._delivery_enumeration(raw, tree, lists) == {}


def test_the_judge_is_handed_the_checklist_and_its_members(
        leerie, tmp_path, monkeypatch):
    """Real `_delivery_judge_unmet`: the enumerator runs once before the
    pack, code adds the file its search finds, the judge's prompt carries
    every entry, and a met answering only the declared parts is re-asked
    and ends uninspected — never met, never an unmet vote."""
    root = tmp_path / ".leerie"
    run_dir = root / "runs" / "chk"
    staging = _sender_tree(tmp_path)
    st = leerie.State(root, "chk")
    st.data = {"task": "route every sender through the shared client",
               "worker_count": 0, "required_items": [ITEM]}
    st.save()
    seen: list[dict] = []

    async def fake(**kw):
        seen.append(kw)
        if kw["schema_key"] == "delivery_enumerator":
            return {"sets": [{"item_index": 0, "checklist_index": 0,
                              "set": "senders",
                              "members": [{"path": "src/a.py", "line": 2,
                                           "place": "a"}],
                              "searches": [{"regex": r"send\(",
                                            "pathspec": "src/"}]}]}
        return {"verdicts": [{"item_index": 0, "met": True, "evidence": "e",
                              "parts": [_part(0), _part(1)]}]}
    monkeypatch.setattr(leerie, "claude_p", fake)
    details: dict = {}
    routing: dict = {}
    unmet, _, _ = asyncio.run(leerie._delivery_judge_unmet(
        st.data["task"], st.data["required_items"], staging, st,
        dict(leerie.DEFAULT_CAPS), MODELS, EFFORTS, "pre",
        routing=routing, item_details=details))
    assert [k["schema_key"] for k in seen][0] == "delivery_enumerator"
    assert sum(k["schema_key"] == "delivery_enumerator" for k in seen) == 1
    judge = next(k for k in seen if k["schema_key"] == "delivery_judge")
    assert '"part": "src/a.py:2 \\u2014 a"' in judge["user_prompt"] or \
        "src/a.py:2 — a" in judge["user_prompt"]
    assert "src/b.py:2" in judge["user_prompt"]
    assert re.findall(r'"checklist_index": (\d+)', judge["user_prompt"]) == [
        "0", "1", "2", "3"]
    assert unmet == []
    assert routing.get("not_inspected") == [0]
    assert details[0].get("not_inspected") is True


def test_a_set_past_the_cap_is_one_entry_no_verdict_answers(leerie,
                                                            tmp_path):
    """An item whose sets hold more than `delivery_set_members` members is
    not listed whole (it could not be answered in one sample, and could
    outgrow the prompt): its set is one `unlisted` entry, a met answering
    every index is still not met, a part on that entry is dropped, and the
    item is uninspected (ends `unknown`) — never an unmet vote."""
    tree = _sender_tree(tmp_path)
    lists = {4: leerie._delivery_checklist(ITEM)}
    raw = {"sets": [{"item_index": 4, "checklist_index": 0, "set": "senders",
                     "members": [], "searches": [{"regex": r"send\(",
                                                  "pathspec": ""}]}]}
    # Three files match (src/a.py, src/b.py, docs/d.md): under a cap of 3
    # they are listed, past a cap of 2 they are not.
    assert len(leerie._delivery_enumeration(raw, tree, lists, cap=3)[4]) == 3
    got = leerie._delivery_enumeration(raw, tree, lists, cap=2)
    assert got == {4: [{"member_of": 0, "unlisted": 3}]}
    checklist = leerie._delivery_checklist(ITEM, got[4])
    assert checklist[2]["unlisted"] is True and checklist[2]["member_of"] == 0
    entry = leerie._delivery_item_entry({**ITEM, "item": ITEM["item"]}, 4,
                                        checklist)
    assert entry["checklist"][2]["unlisted"] is True
    every = {"item_index": 4, "met": True, "evidence": "e",
             "parts": [_part(0), _part(1), _part(2)]}
    assert leerie._delivery_verdict_check(every, tree, "", {}, checklist) == (
        "unverified", "checklist_unanswered")
    lists = {4: checklist}
    smp = leerie._delivery_screen_sample({"verdicts": [every]}, tree, "", {},
                                         lists)
    assert smp["verdicts"][0]["met"] is False
    assert smp["verdicts"][0]["rejected"] == "checklist_unanswered"
    assert leerie._delivery_uninspected(smp, [4], lists) == [4]
    # A part shown unmet on a declared entry is still a finding.
    found = {**every, "met": False, "parts": [_part(0, "unmet")]}
    smp = leerie._delivery_screen_sample({"verdicts": [found]}, tree, "", {},
                                         lists)
    assert leerie._delivery_uninspected(smp, [4], lists) == []
    # One answering the unlisted entry is dropped: none of its members was
    # shown, so it is no unmet vote; the item is uninspected.
    on_unlisted = {**every, "met": False, "parts": [_part(2, "unmet")]}
    smp = leerie._delivery_screen_sample({"verdicts": [on_unlisted]}, tree,
                                         "", {}, lists)
    assert smp["verdicts"][0]["parts"] == []
    assert leerie._delivery_uninspected(smp, [4], lists) == [4]


def test_a_search_lists_each_file_once(leerie, tmp_path):
    tree = _sender_tree(tmp_path)
    _commit(tree, {"src/many.py": "send(1)\nsend(2)\nsend(3)\n"})
    lists = {4: leerie._delivery_checklist(ITEM)}
    raw = {"sets": [{"item_index": 4, "checklist_index": 0, "set": "s",
                     "members": [], "searches": [{"regex": r"send\(",
                                                  "pathspec": "src/"}]}]}
    got = leerie._delivery_enumeration(raw, tree, lists)
    assert [(m["path"], m["line"]) for m in got[4]] == [
        ("src/a.py", 2), ("src/b.py", 2), ("src/many.py", 1)]


def test_the_cap_counts_an_items_sets_together_largest_unlisted_first(
        leerie, tmp_path):
    tree = _sender_tree(tmp_path)
    lists = {4: leerie._delivery_checklist(ITEM)}
    raw = {"sets": [
        {"item_index": 4, "checklist_index": 0, "set": "senders",
         "members": [], "searches": [{"regex": r"send\(", "pathspec": ""}]},
        {"item_index": 4, "checklist_index": 1, "set": "copies",
         "members": [{"path": "src/c.py", "line": 1, "place": "c"}],
         "searches": []}]}
    # 3 + 1 members fit a cap of 4; under 3 the larger set (3) goes, and
    # its one entry plus the other's member fit.
    assert len(leerie._delivery_enumeration(raw, tree, lists, cap=4)[4]) == 4
    got = leerie._delivery_enumeration(raw, tree, lists, cap=3)[4]
    assert got == [{"member_of": 1, "path": "src/c.py", "line": 1,
                    "place": "c"},
                   {"member_of": 0, "unlisted": 3}]


def test_malformed_enumerator_fields_are_skipped(leerie, tmp_path):
    tree = _sender_tree(tmp_path)
    lists = {4: leerie._delivery_checklist(ITEM)}
    raw = {"sets": [
        {"item_index": 4, "checklist_index": 0, "set": "x", "members": 5,
         "searches": ["send", None, {"regex": 3}]},
        {"item_index": 4, "checklist_index": 1, "set": "x",
         "members": ["src/a.py", None], "searches": {"regex": "send"}}]}
    assert leerie._delivery_enumeration(raw, tree, lists) == {}


def test_an_item_with_members_is_judged_alone(leerie):
    lists = {0: [{"checklist_index": 0, "part": "a"}],
             1: [{"checklist_index": 0, "part": "b"},
                 {"checklist_index": 1, "part": "m", "member_of": 0}],
             2: [{"checklist_index": 0, "part": "c"}],
             3: [{"checklist_index": 0, "part": "d", "member_of": 0}]}
    assert leerie._delivery_repack([[0, 1, 2], [3]], lists, False) == [
        [0, 2], [1], [3]]
    # The contract rides pack 0: kept, empty, when its items all left.
    assert leerie._delivery_repack([[1], [2]], lists, True) == [[], [1], [2]]
    assert leerie._delivery_repack([[1], [2]], lists, False) == [[1], [2]]
    assert leerie._delivery_repack([[]], {}, True) == [[]]


def test_enumerators_are_bounded_and_set_items_judged_alone(
        leerie, tmp_path, monkeypatch):
    """Real `_delivery_judge_unmet` over three packs at `max_parallel` 1:
    no two enumerators run at once, a sweep's derived item is not sent,
    and the item a set was listed for gets a judge call of its own."""
    root = tmp_path / ".leerie"
    staging = _sender_tree(tmp_path)
    st = leerie.State(root, "chk")
    items = [{"item": f"item {k}", "span": f"S{k}"} for k in range(45)]
    items[1] = {**ITEM, "span": "S1"}
    items[2] = {"item": "sweep", "span": "S2", "parts": ["src/a.py:2 x"],
                "sweep_of": {"file": "src/a.py"}}
    st.data = {"task": "t", "worker_count": 0, "required_items": items}
    st.save()
    active = [0, 0]
    enum_items: list[set] = []
    judged: list[list[int]] = []

    async def fake(**kw):
        found = sorted({int(x) for x in re.findall(r'"item_index": (\d+)',
                                                  kw["user_prompt"])})
        if kw["schema_key"] == "delivery_enumerator":
            active[0] += 1
            active[1] = max(active)
            await asyncio.sleep(0.05)
            active[0] -= 1
            enum_items.append(set(found))
            sets = ([{"item_index": 1, "checklist_index": 0, "set": "s",
                      "members": [{"path": "src/a.py", "line": 2,
                                   "place": "a"}], "searches": []}]
                    if 1 in found else [])
            return {"sets": sets}
        judged.append(found)
        return {"verdicts": [
            {"item_index": i, "met": False, "evidence": "e",
             "parts": [{**_part(0, "unmet"), "cites": []}]}
            for i in found]}
    monkeypatch.setattr(leerie, "claude_p", fake)
    caps = {**leerie.DEFAULT_CAPS, "max_parallel": 1}
    routing: dict = {}
    asyncio.run(leerie._delivery_judge_unmet(
        "t", items, staging, st, caps, MODELS, EFFORTS, "pre",
        routing=routing))
    assert len(enum_items) >= 2 and active[1] == 1
    assert all(2 not in e for e in enum_items)
    assert [1] in judged
    assert all(1 not in j for j in judged if j != [1])
    assert routing["packs"] == len({tuple(j) for j in judged})
