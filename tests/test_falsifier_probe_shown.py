"""A falsifier reads only what a fixer may read, so its probe can be shown
(DESIGN §6 *A falsifier reads only what a fixer may read*, §8 *Delivery
re-drive* rule 4).

Barnacle 4b5c564d (leerie 0.44.10): item 1 was unmet on a confirmed
falsifier probe alone. The falsifier's leads were every valid set, held-back
ones included, so the probe was held-out content: its implementers were
handed only "a falsifier probe of the item's words … fails under B*", ran a
self-check that passed 18 of 18 and changed nothing, and from 0.44.13 such
an item was not driven at all. 4 such items in 4 runs over three days, none
closed. Now the leads are the shown sets only, a sample records the sets it
read (`lead_sets`), and a probe whose sample read only shown sets is put in
the group's self-check and named in its note. A probe of an older sample
(no `lead_sets`) stays held-out content. A fixer may contest a shown probe,
typed and checked in code; the contest goes to a human.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest

from tests.test_delivery_redrive import World, _git, _note_work_and_selfcheck
from tests.test_falsifier_exec_queue import _events_env

ITEMS = [{"item": "the guard must evaluate only fields declared on the "
                  "payload schema", "span": "S1", "verifiability": "tree"}]
QUOTE = "must evaluate only fields declared on the payload schema"
PROBE_EVIDENCE = (f"a falsifier probe of the item's words \"{QUOTE}\" fails "
                  "under B* on the final tree")
PROBE_REL = "src/zz-probe/p.test.ts"
PROBE_SRC = ('import { it, expect } from "vitest";\n'
             'import { u } from "../a";\n'
             'it("exported payload schema, response mapUrl ignored", () => {\n'
             '  const c = "export const XPayloadSchema = z.object({});";\n'
             '  expect(u(c, "")).toEqual([]);\n'
             '});\n')
CASE = "exported payload schema, response mapUrl ignored"
ASSERTION = 'expect(u(c, "")).toEqual([]);'
REASON = ("The guard locates the payload schema only by `const`, so an "
          "`export const` declaration is not found")
HELD_MARK = "HELDBACK_SECRET_"
SHOWN_MARK = "SHOWN_LEAD_"


def _unmet() -> list[dict]:
    return [{"item_index": 0, "item": ITEMS[0]["item"],
             "evidence": PROBE_EVIDENCE, "by": ["falsifier"],
             "evidence_by": {"falsifier": PROBE_EVIDENCE}}]


def _row(probe_dir: Path, *, lead_sets=None, file: str = PROBE_REL) -> dict:
    sample = {"sample": 0, "confirmed": True, "item_span_quote": QUOTE,
              "case_name": CASE, "assertion_quote": ASSERTION,
              "reason": REASON,
              "probe_saved": {"dir": str(probe_dir), "file": file}}
    if lead_sets is not None:
        sample["lead_sets"] = lead_sets
    return {"item_index": 0, "verdict": "unmet", "by": ["falsifier"],
            "falsifier": {"item_index": 0, "confirmed": True,
                          "samples": [sample]}}


def _save_probe(run_dir: Path, src: str = PROBE_SRC) -> Path:
    d = run_dir / "acceptance" / "probes" / "i0"
    (d / PROBE_REL).parent.mkdir(parents=True, exist_ok=True)
    (d / PROBE_REL).write_text(src)
    return d


def _world(leerie, tmp_path, monkeypatch, *, n_sets: int, row=None,
           row_fn=None):
    """`n_sets` sets, every one passing on the detection commit (as on
    4b5c564d's final tree), one item unmet on a confirmed probe alone. With
    five sets, sets 4 and 5 are held back."""
    w = World(leerie, tmp_path, n_sets=n_sets, items=ITEMS, unmet=_unmet(),
              claims={"s1": [{"item_index": 0, "kind": "deliverable"}]})
    for s_ in w.sets:
        mark = (HELD_MARK if n_sets >= 4 and s_["index"] > n_sets - 2
                else SHOWN_MARK)
        for rel in s_["defect_files"] + s_["control_files"]:
            p_ = Path(s_["dir"]) / rel
            p_.write_text(p_.read_text() + f"// {mark}{s_['index']}\n")
    w.install(monkeypatch)
    w.write("export const a = 1; // FIXED GENERAL\n")
    head = _git(w.staging, "rev-parse", "HEAD")
    w.st.data["acceptance"]["gate"] = {"final_sha": head, "final": [
        {"index": s_["index"], "passed": True, "unmeasured": False,
         "failing_files": []} for s_ in w.sets]}
    rows = [row_fn(w) if row_fn else row] if (row or row_fn) else []
    w.st.data["delivery_gate"].update(
        final_sha=head,
        final_tree=_git(w.staging, "rev-parse", "HEAD^{tree}"), items=rows)
    w.st.save()
    return w


def _spy_selfcheck(leerie, monkeypatch, w) -> list[dict]:
    """Everything the implementer could read that leerie handed it: its
    prompt and every file of its self-check directory, captured while it
    runs (the directory is removed after)."""
    seen: list[dict] = []
    inner = leerie.claude_p

    async def spy(**kw):
        files: dict[str, str] = {}
        sc = kw.get("selfcheck_dir")
        if sc:
            for p_ in sorted(Path(sc).rglob("*")):
                if p_.is_file():
                    files[str(p_.relative_to(sc))] = p_.read_text(
                        errors="replace")
        seen.append({"prompt": kw["user_prompt"], "files": files,
                     "selfcheck_dir": sc})
        return await inner(**kw)
    monkeypatch.setattr(leerie, "claude_p", spy)
    return seen


# === the falsifier's leads ==================================================

def _five_sets(tmp_path: Path) -> list[dict]:
    sets = []
    for k in range(1, 6):
        d = tmp_path / f"set{k}"
        (d / "src").mkdir(parents=True)
        mark = HELD_MARK if k >= 4 else SHOWN_MARK
        (d / f"src/s{k}.test.ts").write_text(f"// {mark}{k}\n")
        sets.append({"index": k, "valid": True, "dir": str(d),
                     "defect_files": [f"src/s{k}.test.ts"],
                     "control_files": [], "support_files": []})
    return sets


def test_lead_sets_are_the_shown_sets_not_broken(leerie, tmp_path):
    sets = _five_sets(tmp_path)
    assert [s_["index"] for s_ in leerie._falsifier_lead_sets(sets)] == [
        1, 2, 3]
    sets[1]["broken"] = True
    # The held-back identity does not move when a shown set is broken.
    assert [s_["index"] for s_ in leerie._falsifier_lead_sets(sets)] == [
        1, 3]
    # Fewer than four sets: none is held back, every one is a lead.
    assert [s_["index"] for s_ in leerie._falsifier_lead_sets(
        sets[:3])] == [1, 3]


def test_a_falsifier_sample_is_given_and_records_shown_sets_only(
        leerie, tmp_path, monkeypatch):
    """The real `_falsify_items`, `_falsifier_place_leads` and
    `_falsifier_user_prompt`: what the worker's checkout holds and what its
    prompt lists, read while it runs, carry no held-back set; the sample
    records the sets it was given."""
    real_place = leerie._falsifier_place_leads
    real_prompt = leerie._falsifier_user_prompt
    st, caps, _events = _events_env(leerie, tmp_path, monkeypatch, lanes=1,
                                    worker_units=[1])
    monkeypatch.setattr(leerie, "_falsifier_place_leads", real_place)
    monkeypatch.setattr(leerie, "_falsifier_user_prompt", real_prompt)
    st.data["acceptance"]["sets"] = _five_sets(tmp_path / "sets")
    seen: list[dict] = []
    inner = leerie.claude_p

    async def spy(**kw):
        lane = Path(kw["cwd"])
        text = "".join(p_.read_text(errors="replace")
                       for p_ in lane.rglob("*") if p_.is_file())
        seen.append({"prompt": kw["user_prompt"], "text": text,
                     "lead_dirs": sorted(
                         p_.name for p_ in (lane / leerie.FALSIFIER_LEADS_DIR
                                            / "acceptance").iterdir())})
        return await inner(**kw)
    monkeypatch.setattr(leerie, "claude_p", spy)
    progress: dict = {}
    asyncio.run(leerie._falsify_items(
        st, caps, {}, {}, sha="0" * 40,
        normalized=[{"item": "add of two positives returns the sum"}],
        indices=[0], parts_by_idx={}, progress=progress))
    assert len(seen) == 1
    assert seen[0]["lead_dirs"] == ["set-1", "set-2", "set-3"]
    for k in (1, 2, 3):
        assert f"{SHOWN_MARK}{k}" in seen[0]["text"]
        assert f"set-{k}/src/s{k}.test.ts" in seen[0]["prompt"]
    for k in (4, 5):
        assert f"{HELD_MARK}{k}" not in seen[0]["text"]
        assert f"s{k}.test.ts" not in seen[0]["prompt"]
    sample = progress["items"]["0"]["samples"][0]
    assert sample["lead_sets"] == [1, 2, 3]


# === routing: the 4b5c564d shape, now driven ================================

def test_a_shown_probe_item_is_driven_with_its_probe_in_the_selfcheck(
        leerie, tmp_path, monkeypatch):
    def row(w):
        return _row(_save_probe(w.st.run_dir), lead_sets=[1, 2])
    w = _world(leerie, tmp_path, monkeypatch, n_sets=2, row_fn=row)
    seen = _spy_selfcheck(leerie, monkeypatch, w)
    w.edits = [lambda w: w.write("export const a = 1; // FIXED GENERAL "
                                 "EXPORTED\n")]
    w.run()
    rec = w.st.data["delivery_redrive"]
    assert len(seen) == 1, "the probe-confirmed item was not driven"
    work, files = _note_work_and_selfcheck(seen[0]["prompt"])
    assert work == [{"kind": "unmet_item", "item": ITEMS[0]["item"],
                     "evidence": PROBE_EVIDENCE,
                     "probe": {"test_file": PROBE_REL,
                               "failing_case": CASE,
                               "failing_assertion": ASSERTION,
                               "why_it_fails": REASON}}]
    # The probe is one of the self-check's test files, its source readable
    # beside the shown sets'.
    assert PROBE_REL in files
    cfg = json.loads(seen[0]["files"]["config.json"])
    assert PROBE_REL in cfg["run_files"]
    assert seen[0]["files"][f"sets/probe-i0/{PROBE_REL}"] == PROBE_SRC
    assert "PROBES:" in seen[0]["prompt"]
    assert "contested_probes" in seen[0]["prompt"]
    assert rec["items_driven"] == [0]
    assert "unrouted_items" not in rec
    # The group's commit is checked for the probe's source like a set's.
    g = rec["rounds"][0]["groups"][0]
    assert g["committed_staged"] == []


def test_committing_the_probe_voids_the_group(leerie, tmp_path,
                                              monkeypatch):
    def row(w):
        return _row(_save_probe(w.st.run_dir), lead_sets=[1, 2])
    w = _world(leerie, tmp_path, monkeypatch, n_sets=2, row_fn=row)

    def cheat(w):
        p_ = w.staging / PROBE_REL
        p_.parent.mkdir(parents=True, exist_ok=True)
        p_.write_text(PROBE_SRC)
        (w.staging / "src" / "a.ts").write_text("export const a = 2;\n")
        _git(w.staging, "add", "-A")
        _git(w.staging, "commit", "-qm", "copy the probe")
    w.edits = [cheat]
    w.run()
    g = w.st.data["delivery_redrive"]["rounds"][0]["groups"][0]
    assert PROBE_REL in g["committed_staged"]
    assert g["rolled_back"] is True
    assert g["rollback_reason"].startswith(
        "its commits hold the source or path of a shown held-out set or a "
        f"shown falsifier probe: {', '.join(g['committed_staged'])}")


@pytest.mark.parametrize("lead_sets,why", [
    (None, "a sample recorded before leads were the shown sets only"),
    ([1, 2, 4], "a sample whose leads named a held-back set")])
def test_an_old_or_held_back_probe_stays_unreproducible(
        leerie, tmp_path, monkeypatch, lead_sets, why):
    def row(w):
        return _row(_save_probe(w.st.run_dir), lead_sets=lead_sets)
    w = _world(leerie, tmp_path, monkeypatch, n_sets=5, row_fn=row)
    seen = _spy_selfcheck(leerie, monkeypatch, w)
    w.run()
    rec = w.st.data["delivery_redrive"]
    assert seen == [], why
    assert rec["unrouted_items"] == [
        {"item_index": 0, "item": ITEMS[0]["item"],
         "reason": "unreproducible", "probed": QUOTE}]


def test_item_notes_show_a_probe_only_from_shown_leads(leerie, tmp_path):
    d = _save_probe(tmp_path / "run")
    unmet = _unmet()
    shown = {1, 2, 3}
    for leads, shows in (([1, 2, 3], True), ([], True), ([2], True),
                         ([1, 4], False), (None, False), (["1"], False),
                         ([True], False)):
        notes, unrepro = leerie._redrive_item_notes(
            unmet, {}, 1, [_row(d, lead_sets=leads)], shown_idx=shown)
        assert bool(notes) is shows, leads
        assert bool(unrepro) is not shows, leads
        if shows:
            assert notes[0]["probe"]["file"] == PROBE_REL
            assert notes[0]["by"] == ["falsifier"]
    # A missing, linked or escaping saved probe is never shown.
    (d / "elsewhere.ts").write_text("x")
    os.symlink(d / "elsewhere.ts", d / "link.test.ts")
    for f in ("missing.test.ts", "link.test.ts", "../i0/" + PROBE_REL):
        notes, _u = leerie._redrive_item_notes(
            unmet, {}, 1, [_row(d, lead_sets=[1], file=f)], shown_idx=shown)
        assert notes == [], f


def test_a_forced_note_with_a_held_back_probe_is_raised(leerie, tmp_path,
                                                        monkeypatch):
    """The pre-drive check reads the note's probe: one whose sample read a
    held-back set, forced past `_redrive_item_notes`, stops the re-drive
    before any worker."""
    def row(w):
        return _row(_save_probe(w.st.run_dir), lead_sets=[1, 2, 3])
    w = _world(leerie, tmp_path, monkeypatch, n_sets=5, row_fn=row)
    real = leerie._redrive_item_notes

    def forced(*a, **kw):
        notes, unrepro = real(*a, **kw)
        for n in notes:
            n["probe"] = {**n["probe"], "lead_sets": [1, 4]}
        return notes, unrepro
    monkeypatch.setattr(leerie, "_redrive_item_notes", forced)
    with pytest.raises(RuntimeError, match="item-0"):
        w.run()
    assert w.calls == []


# === no held-back byte reaches a fixer, by any path =========================

def test_no_held_back_set_reaches_a_fixer_through_a_probe(
        leerie, tmp_path, monkeypatch):
    """An adversarial falsifier: its probe copies every byte of every lead
    it was given. Placed by the real `_falsifier_place_leads`, saved by the
    real `_falsifier_save_probe`, its lead record from the real
    `_falsifier_lead_sets`, then routed and driven by the real re-drive.
    The fixer's prompt and its whole self-check carry the shown sets' bytes
    and no held-back set's."""
    def row(w):
        lane = tmp_path / "lane"
        lane.mkdir()
        leerie._falsifier_place_leads(w.st, lane, w.sets, "")
        leads = sorted(p_ for p_ in (lane / leerie.FALSIFIER_LEADS_DIR)
                       .rglob("*") if p_.is_file())
        probe = lane / PROBE_REL
        probe.parent.mkdir(parents=True, exist_ok=True)
        probe.write_text(PROBE_SRC + "".join(
            "// " + p_.read_text().replace("\n", " ") + "\n"
            for p_ in leads))
        saved = leerie._falsifier_save_probe(
            w.st, lane, 0, {"probe_path": PROBE_REL})
        r = _row(Path(saved["dir"]), lead_sets=sorted(
            s_["index"] for s_ in leerie._falsifier_lead_sets(w.sets)))
        return r
    w = _world(leerie, tmp_path, monkeypatch, n_sets=5, row_fn=row)
    seen = _spy_selfcheck(leerie, monkeypatch, w)
    w.run()
    assert seen, "the shown probe was not driven"
    for drive in seen:
        handed = drive["prompt"] + "".join(drive["files"].values())
        probe_copy = drive["files"][f"sets/probe-i0/{PROBE_REL}"]
        for k in (1, 2, 3):
            assert f"{SHOWN_MARK}{k}" in probe_copy   # the channel is live
        for k in (4, 5):
            assert f"{HELD_MARK}{k}" not in handed


# === a fixer contests a shown probe =========================================

def _contest_world(leerie, tmp_path, monkeypatch, contest: dict):
    def row(w):
        return _row(_save_probe(w.st.run_dir), lead_sets=[1, 2])
    w = _world(leerie, tmp_path, monkeypatch, n_sets=2, row_fn=row)
    w.caps["subtask_redrive_rounds"] = 3

    async def fake(**kw):
        w.calls.append({"sid": kw["sid"], "prompt": kw["user_prompt"]})
        return {"status": "complete", "summary": "contested",
                "contested_probes": [contest]}
    monkeypatch.setattr(leerie, "claude_p", fake)

    async def still_unmet(*a, **k):
        return {0: {"unmet": True, "by": ["falsifier"],
                    "evidence": PROBE_EVIDENCE,
                    "evidence_by": {"falsifier": PROBE_EVIDENCE}}}
    monkeypatch.setattr(leerie, "_redrive_recheck_items", still_unmet)
    return w


CONTEST = {"probe_test_file": PROBE_REL, "kind": "input_outside_domain",
           "probe_quote": 'const c = "export const XPayloadSchema',
           "cites": [{"path": "src/a.ts", "line_start": 1, "line_end": 1,
                      "quote": "export const a = 1; // FIXED"}],
           "reason": "the emitter never writes `export const`"}


def test_an_accepted_contest_goes_to_a_human_and_the_item_stays_unmet(
        leerie, tmp_path, monkeypatch):
    w = _contest_world(leerie, tmp_path, monkeypatch, CONTEST)
    w.run()
    rec = w.st.data["delivery_redrive"]
    # Driven once: the contested probe is not handed to another round.
    assert len(w.calls) == 1
    [c] = rec["contested_probes"]
    assert c["accepted"] and c["item_index"] == 0
    assert c["kind"] == "input_outside_domain"
    assert c["cite_status"] == ["ok"]
    assert "unrouted_items" not in rec
    # Never dropped: unmet, unconverged, named for a human.
    assert [u["item_index"] for u in
            w.st.data["delivery_gate"]["unmet_final"]] == [0]
    assert leerie._decide_outcome(w.st, any_commits=True) == "unconverged"
    line = [r for r in w.st.data["outcome_reasons"]
            if r.startswith("item 0: a re-drive implementer contested")]
    assert line == [
        f"item 0: a re-drive implementer contested its falsifier probe "
        f"{PROBE_REL} as input_outside_domain (\"const c = \"export const "
        f"XPayloadSchema\", citing src/a.ts:1); a human decides — the item "
        f"stays unmet"]


@pytest.mark.parametrize("bad,why", [
    ({"probe_quote": "a line the probe does not have"},
     "probe_quote is not in the probe"),
    ({"cites": [{"path": "src/a.ts", "line_start": 1, "line_end": 1,
                 "quote": "nothing like this is in a.ts"}]},
     "no cite verifies on this tree"),
    ({"probe_test_file": "src/other.test.ts"},
     "not a probe this group was handed"),
    ({"kind": "contradicts_task", "task_quote": "words the task lacks"},
     "task_quote is not in the task or a required item")])
def test_a_contest_that_fails_a_check_changes_nothing(
        leerie, tmp_path, monkeypatch, bad, why):
    w = _contest_world(leerie, tmp_path, monkeypatch, {**CONTEST, **bad})
    w.run()
    rec = w.st.data["delivery_redrive"]
    assert all(not c["accepted"] and c["rejected"] == why
               for c in rec["contested_probes"])
    # Still driven in the next round: a rejected contest is no contest.
    assert len(w.calls) >= 2
    leerie._decide_outcome(w.st, any_commits=True)
    assert not any("contested" in r for r in w.st.data["outcome_reasons"])


def test_contradicts_task_is_checked_against_the_task_and_items(
        leerie, tmp_path):
    d = _save_probe(tmp_path / "run")
    probes = [{"index": "probe-i0", "item_index": 0, "dir": str(d),
               "defect_files": [PROBE_REL], "control_files": [],
               "support_files": []}]
    items = [{"item": "x", "parts": ["default behaviour preserved"]}]
    c = {"probe_test_file": "/abs/selfcheck/sets/probe-i0/" + PROBE_REL,
         "kind": "contradicts_task", "probe_quote": ASSERTION}
    got = leerie._redrive_check_contests(
        [{**c, "task_quote": "default behaviour preserved"},
         {**c, "task_quote": "never touch the emitter"},
         {**c, "task_quote": "nowhere at all in this text"}],
        probes, tmp_path, "Fix it; never touch the emitter.", items)
    assert [g["accepted"] for g in got] == [True, True, False]
    assert got[0]["probe_test_file"] == PROBE_REL
    assert got[0]["item_index"] == 0


def test_the_implementer_schema_types_the_contest(leerie):
    p_ = leerie.SCHEMAS["implementer"]["properties"]["contested_probes"]
    item = p_["items"]
    assert item["required"] == ["probe_test_file", "kind"]
    assert item["properties"]["kind"]["enum"] == list(
        leerie.REDRIVE_CONTEST_KINDS)
