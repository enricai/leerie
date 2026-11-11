"""A residual solution defect is driven once (DESIGN §8 *Delivery re-drive*
rules 1 and 3): before each group its note is re-checked by its record, and
once a kept group was handed it no later group is — in that round or a later
one. A group that is rolled back, at once or after the last round by a flip,
leaves it open for the next group it is routed to.

Before this rule a round's notes were built once, and a defect routed to
several groups (one whose `where` names no subtask's file goes to every
group, rule 2) was handed to each of them, whatever the first had done; only
the next round's notes left it out. Found by reading the code while fixing
the merge-check form of the same waste (#367); no recorded run hit it.

Real `_run_delivery_redrive` over real git: the routing, the groups' commits
and rollbacks, the clean-tree gate and the flip's B* reruns. Only `claude_p`
is stubbed, plus what `tests/test_redrive_check_note_only_while_red.py` stubs
(the World harness's set evaluation and test-ID read, the delivery judge's
verdict and the met-item falsifier), and, for the flip, what
`tests/test_flipped_probe_rolls_back_its_group.py` stubs.
"""
from __future__ import annotations

import json

from tests.conftest import run_git_cwd_first_stdout as _git
from tests.test_delivery_redrive import World
from tests.test_flipped_probe_rolls_back_its_group import _prompts
from tests.test_met_items_refalsified_after_kept_rounds import (
    Replay, _fix_mul, _fix_mul_undo_add)

DEFECT_NOTE = '"kind": "solution_defect"'
CASE = "results arrive after the first read"
KEY = "solution-defect-0"


def _residual(where: str) -> dict:
    return {"_final": {"result": None, "warnings": [], "blocked": False,
                       "residual_defects": [{
                           "kind": "unhandled_path", "where": where,
                           "concrete_case": CASE,
                           "why_ships_a_defect": "Enter is sent early"}]}}


ITEMS = [{"item": "a is fixed", "span": "S1", "verifiability": "tree"},
         {"item": "b is fixed", "span": "S2", "verifiability": "tree"}]


def _world(leerie, tmp_path, monkeypatch, *, where: str,
           s2_item: bool = False) -> World:
    claims = ({"s2": [{"item_index": 1, "kind": "deliverable"}]}
              if s2_item else None)
    unmet = ([{"item_index": 1, "item": ITEMS[1]["item"],
               "evidence": "missing", "by": ["judge"]}] if s2_item else None)
    w = World(leerie, tmp_path, n_sets=0, items=ITEMS if s2_item else None,
              unmet=unmet, claims=claims)
    w.st.data["conformance"] = _residual(where)
    w.st.save()
    w.install(monkeypatch)
    w.caps["subtask_redrive_rounds"] = 1

    async def met(task, its, staging, st, caps, models, efforts,
                  phase_label, **kw):
        return [], 1, None
    monkeypatch.setattr(leerie, "_delivery_judge_unmet", met)
    monkeypatch.setattr(leerie, "_delivery_record", lambda *a, **k: "")
    monkeypatch.setattr(leerie, "_delivery_typed_evidence",
                        lambda *a, **k: ([], {}))

    async def no_falsifier(st, caps, models, efforts, staging, out):
        return out
    monkeypatch.setattr(leerie, "_redrive_falsify_met", no_falsifier)
    return w


def test_a_defect_a_kept_group_drove_is_not_handed_to_the_next_group(
        leerie, tmp_path, monkeypatch):
    # `where` names no subtask's file, so rule 2 routes it to every group.
    # Group 0 drives it and is kept; group 1, left only that note, is
    # skipped and draws no round.
    w = _world(leerie, tmp_path, monkeypatch, where="src/elsewhere.ts:3")
    w.edits = [lambda w: w.write("export const a = 1; // guard\n", "guard")]
    w.run()
    rec = w.st.data["delivery_redrive"]
    g0, g1 = rec["rounds"][0]["groups"]
    assert g0["members"] == ["s1"] and g1["members"] == ["s2"]
    assert len(w.calls) == 1
    assert DEFECT_NOTE in w.calls[0]["prompt"] and CASE in w.calls[0]["prompt"]
    assert not g0.get("rolled_back") and g0["defects_driven"] == [KEY]
    assert g1["defects_closed"] == [KEY]
    assert g1["skipped"] == "nothing_left" and "pre_sha" not in g1
    assert w.st.data["redrive_rounds"] == {"s1": 1}
    assert rec["defects_driven"] == [KEY]
    assert w.L._decide_outcome(w.st, any_commits=True) == "unconverged"
    assert "(1 of them driven by the delivery re-drive" in \
        w.st.data["outcome_reasons"][-1]


def test_the_next_group_is_driven_on_its_own_work_without_the_defect(
        leerie, tmp_path, monkeypatch):
    w = _world(leerie, tmp_path, monkeypatch, where="src/elsewhere.ts:3",
               s2_item=True)

    def fix_b(w: World) -> None:
        (w.staging / "src" / "b.ts").write_text("export const b = 2;\n")
        _git(w.staging, "commit", "-qam", "fix b")
    w.edits = [lambda w: w.write("export const a = 1; // guard\n", "guard"),
               fix_b]
    w.run()
    rec = w.st.data["delivery_redrive"]
    g0, g1 = rec["rounds"][0]["groups"]
    assert g0["members"] == ["s1"] and g1["members"] == ["s2"]
    assert len(w.calls) == 2
    assert DEFECT_NOTE in w.calls[0]["prompt"]
    assert g1["defects_closed"] == [KEY]
    assert g1["notes"] == ["unmet_item"]
    assert DEFECT_NOTE not in w.calls[1]["prompt"]
    assert CASE not in w.calls[1]["prompt"]
    assert '"kind": "unmet_item"' in w.calls[1]["prompt"]
    assert "defects_driven" not in g1
    assert w.st.data["redrive_rounds"] == {"s1": 1, "s2": 1}


def test_a_rolled_back_group_leaves_the_defect_open_for_the_next(
        leerie, tmp_path, monkeypatch):
    # Group 0 leaves its tree dirty past the re-asks, so it is rolled back:
    # its change is gone, the defect is as open on group 1's start as on the
    # round's, and group 1 is handed it.
    w = _world(leerie, tmp_path, monkeypatch, where="src/elsewhere.ts:3")

    def dirty(w: World) -> None:
        (w.staging / "src" / "a.ts").write_text("export const a = 9;\n")

    def guard_b(w: World) -> None:
        (w.staging / "src" / "b.ts").write_text("export const b = 2;\n")
        _git(w.staging, "commit", "-qam", "guard b")
    # The stub records no session, so the gate's re-asks reach no worker.
    w.edits = [dirty, guard_b]
    start = _git(w.staging, "rev-parse", "HEAD")
    w.run()
    rec = w.st.data["delivery_redrive"]
    g0, g1 = rec["rounds"][0]["groups"]
    assert g0["rolled_back"] is True and g0["unclean_tree"]
    assert "defects_driven" not in g0
    assert _git(w.staging, "rev-parse", f"{g1['pre_sha']}") == start
    assert len(w.calls) == 2
    assert DEFECT_NOTE in w.calls[0]["prompt"]
    assert DEFECT_NOTE in w.calls[1]["prompt"] and CASE in w.calls[1]["prompt"]
    assert "defects_closed" not in g1
    assert g1["post_sha"] != g1["pre_sha"]
    assert not g1.get("rolled_back") and g1["defects_driven"] == [KEY]
    assert rec["defects_driven"] == [KEY]
    assert w.st.data["redrive_rounds"] == {"s1": 1, "s2": 1}


def test_two_owners_of_the_defect_file_drive_it_once_as_one_group(
        leerie, tmp_path, monkeypatch):
    # s3 touches src/a.ts as s1 does: both own the defect, and subtasks that
    # share a file are one session, so it is driven once, by both, and s2,
    # whose diff never reaches it, is not handed it at all.
    w = _world(leerie, tmp_path, monkeypatch, where="src/a.ts:1")
    plan = json.loads((w.dir / "plan.json").read_text())
    plan["subtasks"]["s3"] = {
        "id": "s3", "title": "A2", "intent": "also a", "change_kind": "fix",
        "files_likely_touched": ["src/a.ts"],
        "claims": {"sites": [], "items": []}}
    (w.dir / "plan.json").write_text(json.dumps(plan))
    w.st.data["subtask_status"]["s3"] = "complete"
    w.st.save()
    w.edits = [lambda w: w.write("export const a = 1; // guard\n", "guard")]
    w.run()
    rec = w.st.data["delivery_redrive"]
    (g,) = rec["rounds"][0]["groups"]
    assert g["members"] == ["s1", "s3"]
    assert len(w.calls) == 1
    assert DEFECT_NOTE in w.calls[0]["prompt"]
    assert g["defects_driven"] == [KEY] and rec["defects_driven"] == [KEY]
    assert w.st.data["redrive_rounds"] == {"s1": 1, "s3": 1}


def test_a_group_a_flip_rolls_back_leaves_its_defect_open(
        leerie, tmp_path, monkeypatch):
    # The 22a30525 shape (tests/test_flipped_probe_rolls_back_its_group.py)
    # with a residual defect in calc.py: round 1's group drives it and is
    # kept, then rolled back after the last round for turning met item 0's
    # probe failing. The defect is open again, and round 2's group, which
    # is kept, is handed it.
    r = Replay(leerie, tmp_path, monkeypatch, judge_reads_tree=True,
               edit=[_fix_mul_undo_add, _fix_mul])
    r.st.data["conformance"] = _residual("calc.py:4")
    r.st.save()
    r.run()
    rec = r.st.data["delivery_redrive"]
    g1 = rec["rounds"][0]["groups"][0]
    g2 = rec["rounds"][1]["groups"][0]
    assert g1["rolled_back"] is True and g1["flip_regressed"]
    assert g1["defects_driven"] == [KEY]
    p1, p2 = _prompts(r)
    assert DEFECT_NOTE in p1 and CASE in p1
    assert DEFECT_NOTE in p2 and CASE in p2
    assert not g2.get("rolled_back") and g2["defects_driven"] == [KEY]
    assert rec["defects_driven"] == [KEY]


def test_unkept_drops_only_keys_no_kept_group_drove(leerie):
    rec = {"defects_driven": ["solution-defect-0", "solution-defect-1",
                              "solution-defect-2"],
           "rounds": [{"groups": [
               {"group": 0, "rolled_back": True,
                "defects_driven": ["solution-defect-0", "solution-defect-1"]},
               {"group": 1, "defects_driven": ["solution-defect-1"]}]}]}
    leerie._redrive_defects_unkept(rec)
    # 0: only a rolled-back group drove it. 1: a kept group drove it too.
    # 2: no group records it (driven before groups recorded theirs).
    assert rec["defects_driven"] == ["solution-defect-1", "solution-defect-2"]
