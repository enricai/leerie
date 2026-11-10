"""Every confirmed finding reaches a worker who can act on it, or is recorded
as reaching none, typed with why (DESIGN §8 *An unmet item reaches its
owner, or is recorded as reaching none*).

Barnacle 87580f62 (leerie 0.55.2), two holes in one run:

- Item 0, "Fix the reported issues at their root cause … Introduce no
  regressions in existing behavior …", was claimed `process` by both
  subtasks. After the last re-drive round the shipping-tree re-check
  falsified it, and the probe confirmed a regression round 2 wrote:
  `isLoopbackOrLinkLocalHost("::0:5")` returns true, false at the start.
  `_redrive_item_notes` skipped every `process` item outright, so the item
  got no group and no `unrouted_items` record. The probe names the failing
  code (it imports `src/scripts/recon-http.ts`), and the diff names who
  changed it: only the kept re-drive groups, never an integrate merge.
- Item 8 was driven in rounds 1 and 2, each on another probe; the re-check
  after round 2 confirmed a third, and round 3's group was skipped for
  budget. The third probe reached nobody, yet the item was recorded only in
  `items_budget_skipped`: `items_driven` held it, so it was not unrouted.

The tests drive the real `_run_delivery_redrive` on a real git repository
(the `World` of `tests/test_delivery_redrive.py`): routing, the self-check,
the group's real commit and the accounting. Stubbed: `claude_p`, the set
evaluation, and the item re-check (`_redrive_recheck_items`, whose judge and
falsifier need a model and a JS runner), which here returns the verdict and
the confirmed probe the run recorded. Synthetic text.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_delivery_redrive import World, _git, _note_work_and_selfcheck

NO_REGRESSIONS = ("Fix the reported issues at their root cause. Introduce "
                  "no regressions in existing behaviour.")
QUOTE = "Introduce no regressions in existing behaviour."
FEATURE = "b does Y"


def _probe(run_dir: Path, rel: str, imp: str, tag: str) -> Path:
    """A saved probe of item 0 importing `imp` (relative to `rel`)."""
    d = run_dir / "acceptance" / "probes" / "i0"
    (d / rel).parent.mkdir(parents=True, exist_ok=True)
    (d / rel).write_text(
        'import { it, expect } from "vitest";\n'
        f'import {{ v }} from "{imp}";\n'
        f'it("{tag} keeps its earlier answer", () => {{\n'
        '  expect(v("::0:5")).toBe(false);\n'
        '});\n')
    return d


def _falsifier(d: Path, rel: str, tag: str) -> dict:
    return {"item_index": 0, "confirmed": True, "samples": [{
        "sample": 0, "confirmed": True, "lead_sets": [],
        "item_span_quote": QUOTE, "case_name": f"{tag} keeps its earlier "
        "answer", "assertion_quote": 'expect(v("::0:5")).toBe(false);',
        "reason": "the new branch treats ::0:N as loopback",
        "probe_saved": {"dir": str(d), "file": rel}}]}


def _unmet0(evidence: str = "a falsifier probe fails under B*") -> dict:
    return {"item_index": 0, "item": NO_REGRESSIONS, "evidence": evidence,
            "by": ["falsifier"], "evidence_by": {"falsifier": evidence}}


def _verdict(fi: dict | None) -> dict:
    if fi is None:
        return {"unmet": False, "by": [], "evidence": ""}
    return {"unmet": True, "by": ["falsifier"],
            "evidence": "a falsifier probe fails under B*",
            "evidence_by": {"falsifier": "a falsifier probe fails under B*"},
            "falsifier": fi}


def _process_world(leerie, tmp_path, monkeypatch, *, unmet, rows,
                   item1_claim=True):
    """Item 0 is the constraint both subtasks claim `process`; item 1 a
    deliverable s2 claims."""
    items = [{"item": NO_REGRESSIONS, "span": "S1", "verifiability": "tree"},
             {"item": FEATURE, "span": "S2", "verifiability": "tree"}]
    claims = {"s1": [{"item_index": 0, "kind": "process"}],
              "s2": [{"item_index": 0, "kind": "process"}]
              + ([{"item_index": 1, "kind": "deliverable"}]
                 if item1_claim else [])}
    w = World(leerie, tmp_path, n_sets=0, items=items, unmet=unmet,
              claims=claims)
    w.install(monkeypatch)
    # Only an implementer takes an edit: the delivery ratchet's judges,
    # which a kept group runs, go through the same stub.
    inner = leerie.claude_p

    async def drives_only(**kw):
        if not str(kw.get("sid") or "").startswith("delivery-redrive-r"):
            return {"status": "complete", "summary": "done"}
        return await inner(**kw)
    monkeypatch.setattr(leerie, "claude_p", drives_only)
    w.st.data["delivery_gate"]["items"] = rows
    w.st.save()
    return w


def _drives(w: World) -> list[dict]:
    """The re-drive implementer calls (the ratchet's judges are stubbed by
    the same `claude_p`)."""
    return [c for c in w.calls if c["sid"].startswith("delivery-redrive-r")]


def _commit(w: World, files: dict[str, str], msg: str = "fix") -> None:
    for rel, text in files.items():
        (w.staging / rel).write_text(text)
    _git(w.staging, "add", "-A")
    _git(w.staging, "commit", "-qm", msg)


def _recheck(monkeypatch, leerie, answers: list[dict]) -> list[list[int]]:
    """The re-check returns `answers[k]` on its k-th call (its last one
    after), each a {item: verdict} map; returns the indices asked."""
    asked: list[list[int]] = []

    async def recheck(leerie_dir, st, caps, models, efforts, staging,
                      items, **kw):
        asked.append(list(items))
        return answers[min(len(asked), len(answers)) - 1]
    monkeypatch.setattr(leerie, "_redrive_recheck_items", recheck)
    return asked


# === a process item's probe goes to the subtasks whose diff it exercises ====

def test_a_process_items_probe_goes_to_the_subtask_whose_diff_it_exercises(
        leerie, tmp_path, monkeypatch):
    rel = "src/zz-probe/regress.test.ts"
    w = _process_world(leerie, tmp_path, monkeypatch, unmet=[_unmet0()],
                       rows=[], item1_claim=False)
    d = _probe(w.st.run_dir, rel, "../a", "a")
    w.st.data["delivery_gate"]["items"] = [
        {"item_index": 0, "verdict": "unmet", "by": ["falsifier"],
         "falsifier": _falsifier(d, rel, "a")}]
    w.st.save()
    _recheck(monkeypatch, leerie, [{0: _verdict(None)}])
    w.edits = [lambda w: w.write("export const a = 1; // FIXED\n")]
    w.run()
    rec = w.st.data["delivery_redrive"]
    # s1's integrated merge changed src/a.ts, which the probe imports; s2
    # claimed the constraint too, but its diff is src/b.ts.
    assert [c["sid"] for c in w.calls] == ["delivery-redrive-r1-g0"]
    assert w.calls[0]["prompt"].startswith("Execute subtask `s1`.")
    work, _files = _note_work_and_selfcheck(w.calls[0]["prompt"])
    assert [x["probe"]["test_file"] for x in work] == [rel]
    assert rec["items_driven"] == [0]
    assert [k.split(":")[:2] for k in rec["evidence_handed"]] == [
        ["0", "falsifier"]]
    assert "unrouted_items" not in rec


def test_a_process_items_probe_follows_a_kept_rounds_diff(
        leerie, tmp_path, monkeypatch):
    """The 87580f62 owner: the regression is in a file only a kept
    re-drive group changed, never an integrate merge."""
    rel = "src/zz-probe/regress.test.ts"
    w = _process_world(
        leerie, tmp_path, monkeypatch,
        unmet=[{"item_index": 1, "item": FEATURE, "evidence": "missing",
                "by": ["judge"]}],
        rows=[{"item_index": 0, "verdict": "met", "by": []},
              {"item_index": 1, "verdict": "unmet", "by": ["judge"]}])
    d = _probe(w.st.run_dir, rel, "../c", "c")
    # Round 1 (s2, for item 1) adds src/c.ts; its re-check finds item 1 met
    # and item 0 unmet on a probe importing it.
    asked = _recheck(monkeypatch, leerie, [
        {1: _verdict(None), 0: _verdict(_falsifier(d, rel, "c"))},
        {0: _verdict(None)}])
    w.edits = [
        lambda w: _commit(w, {"src/b.ts": "export const b = 3;\n",
                              "src/c.ts": "export const v = (h: string) => "
                                          "h.startsWith('::0');\n"}),
        lambda w: _commit(w, {"src/c.ts": "export const v = (h: string) => "
                                          "h === '::1';\n"})]
    w.run()
    rec = w.st.data["delivery_redrive"]
    assert [c["sid"] for c in _drives(w)] == ["delivery-redrive-r1-g0",
                                              "delivery-redrive-r2-g0"]
    for c in _drives(w):
        assert c["prompt"].startswith("Execute subtask `s2`.")
    work, _files = _note_work_and_selfcheck(_drives(w)[1]["prompt"])
    assert [(x["item"], x["probe"]["test_file"]) for x in work] == [
        (NO_REGRESSIONS, rel)]
    assert asked[:2] == [[1], [0]]
    assert w.st.data["delivery_gate"]["unmet_final"] == []
    assert "unrouted_items" not in rec


def test_a_process_item_confirmed_after_the_last_round_is_typed(
        leerie, tmp_path, monkeypatch):
    """87580f62 item 0 itself: the shipping-tree re-check (`_redrive_met_
    unread`) confirms the regression after the last group; no round follows,
    so it is recorded, never dropped."""
    rel = "src/zz-probe/regress.test.ts"
    w = _process_world(
        leerie, tmp_path, monkeypatch,
        unmet=[{"item_index": 1, "item": FEATURE, "evidence": "missing",
                "by": ["judge"]}],
        rows=[{"item_index": 0, "verdict": "met", "by": []},
              {"item_index": 1, "verdict": "unmet", "by": ["judge"]}])
    d = _probe(w.st.run_dir, rel, "../c", "c")
    asked = _recheck(monkeypatch, leerie, [
        {1: _verdict(None)}, {0: _verdict(_falsifier(d, rel, "c"))}])
    w.edits = [lambda w: _commit(w, {
        "src/b.ts": "export const b = 3;\n",
        "src/c.ts": "export const v = (h: string) => h.startsWith('::0');\n"})]
    w.run()
    rec = w.st.data["delivery_redrive"]
    assert [c["sid"] for c in _drives(w)] == ["delivery-redrive-r1-g0"]
    # The met item no re-check had read on the moved tree was falsified.
    assert asked == [[1], [0]]
    assert rec["unrouted_items"] == [
        {"item_index": 0, "item": NO_REGRESSIONS,
         "reason": "after_last_round"}]
    assert w.L._decide_outcome(w.st, any_commits=True) == "unconverged"
    reasons = w.st.data["outcome_reasons"]
    assert any("item 0 (after_last_round)" in r for r in reasons)
    assert ("item 0's unmet evidence was confirmed on the tree that ships "
            "after the re-drive's last round, so no re-drive worker was "
            "handed it") in reasons


@pytest.mark.parametrize("shape", ["judge_only", "probe_without_owner"])
def test_a_process_item_with_no_code_target_is_typed(
        leerie, tmp_path, monkeypatch, shape):
    rel = "src/zz-probe/regress.test.ts"
    w = _process_world(leerie, tmp_path, monkeypatch, unmet=[], rows=[],
                       item1_claim=False)
    if shape == "judge_only":
        unmet = [{"item_index": 0, "item": NO_REGRESSIONS,
                  "evidence": "a band-aid", "by": ["judge"]}]
        rows = [{"item_index": 0, "verdict": "unmet", "by": ["judge"]}]
    else:
        # The probe exercises src/z.ts, which no subtask's diff changed.
        _commit(w, {"src/z.ts": "export const v = 1;\n"}, "unowned")
        head = _git(w.staging, "rev-parse", "HEAD")
        w.st.data["delivery_gate"].update(
            final_sha=head,
            final_tree=_git(w.staging, "rev-parse", "HEAD^{tree}"))
        d = _probe(w.st.run_dir, rel, "../z", "z")
        unmet = [_unmet0()]
        rows = [{"item_index": 0, "verdict": "unmet", "by": ["falsifier"],
                 "falsifier": _falsifier(d, rel, "z")}]
    w.st.data["delivery_gate"].update(unmet_final=unmet, items=rows)
    w.st.save()
    w.run()
    rec = w.st.data["delivery_redrive"]
    assert w.calls == [] and rec["rounds"] == []
    assert rec["unrouted_items"] == [
        {"item_index": 0, "item": NO_REGRESSIONS,
         "reason": "no_code_target"}]
    w.L._decide_outcome(w.st, any_commits=True)
    assert ("item 0 is a constraint over the whole change whose unmet "
            "evidence names no code a subtask changed, so no re-drive worker "
            "could be handed it") in w.st.data["outcome_reasons"]


# === a driven item's later probe ============================================

@pytest.mark.parametrize("same_probe", [False, True])
def test_a_driven_items_unshown_later_probe_is_recorded(
        leerie, tmp_path, monkeypatch, same_probe):
    """87580f62 item 8: driven on one probe, re-confirmed on another, the
    next group skipped for budget. The control: re-confirmed on the probe it
    was handed, it reached a worker and is not unrouted."""
    items = [{"item": "a does X", "span": "S1", "verifiability": "tree"}]
    w = World(leerie, tmp_path, n_sets=0, items=items,
              unmet=[{"item_index": 0, "item": "a does X",
                      "evidence": "probe fails", "by": ["falsifier"],
                      "evidence_by": {"falsifier": "probe fails"}}],
              claims={"s1": [{"item_index": 0, "kind": "deliverable"}]})
    w.install(monkeypatch)
    w.caps["subtask_redrive_rounds"] = 1
    first, later = "src/zz-probe/p1.test.ts", "src/zz-probe/p2.test.ts"
    d = _probe(w.st.run_dir, first, "../a", "first")
    w.st.data["delivery_gate"]["items"] = [
        {"item_index": 0, "verdict": "unmet", "by": ["falsifier"],
         "falsifier": _falsifier(d, first, "first")}]
    w.st.save()
    if same_probe:
        fi = _falsifier(d, first, "first")
    else:
        _probe(w.st.run_dir, later, "../a", "later")
        fi = _falsifier(d, later, "later")
    _recheck(monkeypatch, leerie, [{0: _verdict(fi)}])
    w.edits = [lambda w: w.write("export const a = 1; // tried\n")]
    w.run()
    rec = w.st.data["delivery_redrive"]
    assert [c["sid"] for c in w.calls] == ["delivery-redrive-r1-g0"]
    work, _f = _note_work_and_selfcheck(w.calls[0]["prompt"])
    assert [x["probe"]["test_file"] for x in work] == [first]
    assert rec["items_driven"] == [0]
    assert rec["items_budget_skipped"] == [0]
    w.L._decide_outcome(w.st, any_commits=True)
    reasons = w.st.data["outcome_reasons"]
    if same_probe:
        assert "unrouted_items" not in rec
        assert not any("unrouted_item" in r for r in reasons)
        return
    assert rec["unrouted_items"] == [
        {"item_index": 0, "item": "a does X", "reason": "budget"}]
    assert ("item 0 was driven on earlier evidence; its latest confirmed "
            "evidence reached no re-drive worker (budget)") in reasons
    # The next run is handed it.
    on_disk = json.loads((w.dir / "state.json").read_text())
    assert on_disk["delivery_redrive"]["unrouted_items"][0]["reason"] == \
        "budget"
