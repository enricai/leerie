"""Every item the tree that ships reports met passed detection's bar on it
(DESIGN §8 *Every item the tree that ships reports met passed detection's
bar on it*).

Live (lane T9 bench 4, navegando 4134b78c sample 1): a re-drive group sent
for something else (a base-form note) was kept; it made a component's labels
default to hard-coded Spanish, undoing the met item "the entire site's
user-facing strings are in the messages files". The item was routed nowhere,
so only the ratchet's judge read it on that tree, and it repeated the round's
own claim; no falsifier ran there. Detection's bar is the judge AND the
falsifier.

The replay: a real pytest repository, its run repository and staging, the
real `_run_delivery_redrive` — routing, the group's real commit, the ratchet,
the re-checks, the falsifier's real gates (B* on a pristine checkout of the
commit) — with only `claude_p` stubbed, and the TS AST facts, since the
probes here are Python (as `tests/test_delivery_detection.py`). The group is
sent for item 1 and, while fixing it, undoes item 0; every judge says met.
Synthetic text."""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import pytest

from tests.conftest import run_git_out as _git
from tests.test_delivery_detection import (PROBE_REL, PROBE_SRC, _claim,
                                           _facts_for, _placed, _st, _staging)

ITEM0 = "add(2, 3) returns 5, the sum of its arguments"
ITEM1 = "mul(2, 3) returns 6, the product of its arguments"
FIXED_ADD = "def add(a, b):\n    return a + b\n"
BUGGY_ADD = "def add(a, b):\n    return a - b\n"
BUGGY_MUL = "def mul(a, b):\n    return a + b\n"
FIXED_MUL = "def mul(a, b):\n    return a * b\n"
MODELS = {"implementer": "sonnet", "delivery_judge": "sonnet",
          "falsifier": "sonnet", "delivery_ratchet": "sonnet",
          "verifiability_classifier": "sonnet"}
EFFORTS = {"implementer": "low", "delivery_judge": "medium",
           "falsifier": "medium", "delivery_ratchet": "medium",
           "verifiability_classifier": "medium"}


@pytest.fixture(autouse=True)
def _quiet_host(leerie, monkeypatch):
    monkeypatch.setattr(leerie, "_host_cpu_psi", lambda *a, **k: 0.0)
    leerie._DEPS_INSTALLED.clear()
    yield
    leerie._DEPS_INSTALLED.clear()


def _repo(tmp_path: Path) -> tuple[Path, str, str]:
    """A base whose `add` and `mul` are both wrong, and subtask s1's
    integrated work, which fixed `add` only."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@x")
    _git(repo, "config", "user.name", "t")
    (repo / "calc.py").write_text(BUGGY_ADD + "\n\n" + BUGGY_MUL)
    (repo / "conftest.py").write_text(
        "import sys, pathlib\n"
        "sys.path.insert(0, str(pathlib.Path(__file__).parent))\n")
    (repo / "test_smoke.py").write_text("def test_smoke():\n    pass\n")
    (repo / ".leerie").mkdir()
    (repo / ".leerie" / "config.toml").write_text(
        'test_scoped = "python3 -m pytest -q -p no:cacheprovider '
        '{test_files}"\n'
        'test = "python3 -m pytest -q -p no:cacheprovider"\n')
    (repo / ".gitignore").write_text(".leerie/runs/\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    base = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "-b", "sub-s1")
    (repo / "calc.py").write_text(FIXED_ADD + "\n\n" + BUGGY_MUL)
    _git(repo, "commit", "-qam", "s1 work")
    _git(repo, "checkout", "-q", "main")
    _git(repo, "merge", "-q", "--no-ff", "-m", "leerie: integrate s1",
         "sub-s1")
    return repo, base, _git(repo, "rev-parse", "HEAD")


class Replay:
    """The 4134b78c s1 shape: detection read item 0 met and item 1 unmet on
    `head`; the re-drive drives s1 for item 1, and `edit` is its commit."""

    def __init__(self, leerie, tmp_path, monkeypatch, *, edit,
                 falsify0=True, judge_crash_after_drive=False):
        self.L = leerie
        repo, base, head = _repo(tmp_path)
        items = [{"item": ITEM0, "span": "S1", "verifiability": "tree"},
                 {"item": ITEM1, "span": "S2", "verifiability": "tree"}]
        st = _st(leerie, tmp_path, repo, base, required_items=items)
        self.staging = _staging(st, repo, head)
        self.head = head
        st.data["subtask_status"] = {"s1": "complete"}
        st.data["delivery_gate"] = {
            "final_sha": head,
            "final_tree": _git(self.staging, "rev-parse", "HEAD^{tree}"),
            "unmet_final": [{"item_index": 1, "item": ITEM1,
                             "evidence": "mul adds", "by": ["judge"],
                             "evidence_by": {"judge": "mul adds"}}],
            "items": [
                {"item_index": 0, "item": ITEM0, "verdict": "met", "by": [],
                 "judge": {"met_evidence": "calc.py add returns a + b"},
                 "falsifier": {"confirmed": False, "samples": []}},
                {"item_index": 1, "item": ITEM1, "verdict": "unmet",
                 "by": ["judge"]}]}
        st.save()
        (st.run_dir / "plan.json").write_text(json.dumps({"subtasks": {
            "s1": {"id": "s1", "title": "calc", "intent": "fix calc",
                   "change_kind": "fix", "files_likely_touched": ["calc.py"],
                   "claims": {"sites": [], "items": [
                       {"item_index": 0, "kind": "deliverable"},
                       {"item_index": 1, "kind": "deliverable"}]}}}}))
        self.st = st
        self.caps = dict(leerie.DEFAULT_CAPS)
        self.caps.update(max_parallel=1, falsifier_samples=3)
        self.calls: list[dict] = []
        self.drove = False
        replay = self

        async def workers(**kw):
            replay.calls.append({**kw, "head": _git(
                replay.staging, "rev-parse", "HEAD")})
            key = kw["schema_key"]
            if key == "implementer":
                edit(replay.staging)
                replay.drove = True
                return {"status": "complete", "summary": "fixed mul; add "
                        "keeps its documented default"}
            if key == "verifiability_classifier":
                return {"items": [{"item_index": i, "verifiability":
                                   "in_tree", "reason": "r"}
                                  for i in range(2)]}
            if key == "delivery_ratchet":
                # It repeats the round's own claim (T9 4134b78c s1).
                listed = json.loads(kw["user_prompt"].split(
                    "Re-verify each against the CURRENT tree):\n", 1)[1]
                    .split("\n\nCHANGES SINCE", 1)[0])
                return {"verdicts": [
                    {"item_index": e["item_index"], "met": True,
                     "parts": [], "evidence": "the round says add is fine",
                     "evidence_files": []} for e in listed]}
            if key == "delivery_judge":
                if replay.drove and judge_crash_after_drive and \
                        '"item_index": 0' in kw["user_prompt"]:
                    raise leerie.WorkerError("judge down")
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
            assert key == "falsifier", key
            if falsify0 and ITEM0 in kw["user_prompt"]:
                p = Path(kw["cwd"]) / PROBE_REL
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(PROBE_SRC)
                return _claim()
            return _claim(verdict="could_not_demonstrate", probe_path="")
        monkeypatch.setattr(leerie, "claude_p", workers)
        monkeypatch.setattr(leerie, "_ts_resolvable",
                            lambda tree, **_kw: True)
        monkeypatch.setattr(
            leerie, "_probe_case_facts_placed",
            _placed(lambda tree, probe, case, **_kw:
                    _facts_for(Path(probe).read_text())))

    def run(self) -> None:
        asyncio.run(self.L._run_delivery_redrive(
            self.st.run_dir, self.st, self.caps, MODELS, EFFORTS))

    def falsified(self, i: int) -> list[dict]:
        item = ITEM0 if i == 0 else ITEM1
        return [c for c in self.calls if c["schema_key"] == "falsifier"
                and item in c["user_prompt"]]


def _fix_mul_undo_add(staging: Path) -> None:
    (staging / "calc.py").write_text(BUGGY_ADD + "\n\n" + FIXED_MUL)
    _git(staging, "commit", "-qam", "fix mul")


def _fix_mul(staging: Path) -> None:
    (staging / "calc.py").write_text(FIXED_ADD + "\n\n" + FIXED_MUL)
    _git(staging, "commit", "-qam", "fix mul")


def test_a_kept_group_that_undoes_a_met_item_is_falsified_on_its_tree(
        leerie, tmp_path, monkeypatch):
    """The replay: the group is kept (its ratchet judge says met), and the
    tree that ships gets a falsifier pass on item 0, whose probe — `add(2,
    3) == 5` — is confirmed under B* there: item 0 is unmet, by the
    falsifier, and the verdict names it."""
    r = Replay(leerie, tmp_path, monkeypatch, edit=_fix_mul_undo_add)
    r.run()
    final = _git(r.staging, "rev-parse", "HEAD")
    assert final != r.head
    (g,) = r.st.data["delivery_redrive"]["rounds"][0]["groups"]
    assert not g.get("rolled_back") and g["ratchet"]["regressed"] == []
    assert g["ratchet"]["prior_met"] == [0]
    f0 = r.falsified(0)
    assert f0 and {c["head"] for c in f0} == {final}
    gate = r.st.data["delivery_gate"]
    row0 = next(x for x in gate["items"] if x["item_index"] == 0)
    assert row0["verdict"] == "unmet" and row0["by"] == ["falsifier"]
    assert row0["redrive_recheck"] == {"sha": final}
    assert row0["falsifier"]["confirmed"] is True
    u0 = next(u for u in gate["unmet_final"] if u["item_index"] == 0)
    assert u0["by"] == ["falsifier"]
    assert "add(2, 3) returns 5" in u0["evidence_by"]["falsifier"]
    # The item the group was sent for is met on the same tree.
    assert [u["item_index"] for u in gate["unmet_final"]] == [0]
    assert leerie._redrive_met_unread(r.st, final) == []


def test_a_kept_group_that_keeps_the_met_item_leaves_it_met_on_final(
        leerie, tmp_path, monkeypatch):
    """The same re-drive, the group fixing only what it was sent for: item 0
    is still falsified on the tree that ships (nothing says which items a
    change reaches), no sample demonstrates anything, and it stays met — read
    on `final`, so the verdict is re-keyed there."""
    r = Replay(leerie, tmp_path, monkeypatch, edit=_fix_mul, falsify0=False)
    r.run()
    final = _git(r.staging, "rev-parse", "HEAD")
    f0 = r.falsified(0)
    assert len(f0) == 3 and {c["head"] for c in f0} == {final}
    gate = r.st.data["delivery_gate"]
    row0 = next(x for x in gate["items"] if x["item_index"] == 0)
    assert row0["verdict"] == "met"
    assert row0["redrive_recheck"] == {"sha": final}
    assert gate["unmet_final"] == []
    assert gate["final_sha"] == final


def test_a_group_that_changed_nothing_costs_no_falsifier(
        leerie, tmp_path, monkeypatch):
    """No kept change, no invalidated falsification: detection's verdict on
    item 0 stands and no falsifier is spent on it."""
    r = Replay(leerie, tmp_path, monkeypatch, edit=lambda staging: None)
    r.run()
    assert _git(r.staging, "rev-parse", "HEAD") == r.head
    assert r.falsified(0) == []
    row0 = next(x for x in r.st.data["delivery_gate"]["items"]
                if x["item_index"] == 0)
    assert row0["verdict"] == "met" and "redrive_recheck" not in row0


def test_a_judge_that_crashes_on_final_leaves_the_item_unknown(
        leerie, tmp_path, monkeypatch):
    """The final read of item 0 crashes: it read nothing, so the item is not
    met on `final` — unknown, listed, and the verdict is not re-keyed to a
    commit it was never read on."""
    r = Replay(leerie, tmp_path, monkeypatch, edit=_fix_mul_undo_add,
               judge_crash_after_drive=True)
    r.run()
    final = _git(r.staging, "rev-parse", "HEAD")
    gate = r.st.data["delivery_gate"]
    row0 = next(x for x in gate["items"] if x["item_index"] == 0)
    assert row0["verdict"] == "unknown"
    assert "0" in r.st.data["delivery_ratchet"]["unverified"]
    assert gate["final_sha"] == r.head != final
    assert [e for e in r.st.data["needs_human_verification"]
            if e["source"] == "ratchet_unverified"
            and ITEM0 in e["detail"]]


def test_met_unread_selects_by_row_item_kind_and_read_commit(leerie, tmp_path):
    st = leerie.State(tmp_path / "s", "r", repo_root=tmp_path)
    st.data = {"required_items": [
        {"item": "a"},
        {"item": "b", "verifiability": "external"},
        {"item": "c", "untouched_paths": ["docs/"]},
        {"item": "d", "verifiability": "execution", "check": "test_id_diff"},
        {"item": "e"}, {"item": "f"}, {"item": "g"}],
        "delivery_gate": {"items": [
            {"item_index": 0, "verdict": "met"},
            {"item_index": 1, "verdict": "met"},
            {"item_index": 2, "verdict": "met"},
            {"item_index": 3, "verdict": "met"},
            {"item_index": 4, "verdict": "unmet", "other_parts": "met"},
            {"item_index": 5, "verdict": "met",
             "redrive_recheck": {"sha": "F"}},
            {"item_index": 6, "verdict": "unknown"}]}}
    assert leerie._redrive_met_unread(st, "F") == [0, 4]
    assert leerie._redrive_met_unread(st, "G") == [0, 4, 5]
