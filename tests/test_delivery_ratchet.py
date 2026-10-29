"""The delivery ratchet (DESIGN §8 *A repair that regresses met work is
rolled back*; bench Q4 N5, frozen config C + vote change V).

After every acceptance-repair round and every delivery re-drive group that
commits, the required items the latest delivery verdict holds met are
verified again on the tree after it: `untouched_paths` items in code,
`execution` items (typed once by the verifiability classifier) through the
repair's B* failing-test-ID diff, `in_tree` items by the delivery judge with
their prior evidence and the repair's diff, one sample unless it flags an
item or an item's typed `evidence_files` meet the diff, then 2-of-3.

The gate tests run the REAL acceptance gate against a real git repo and a
real pytest command; only `claude_p` is stubbed (and the host's PSI file,
so B*'s pressure gate never waits).
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from tests.conftest import run_git_cwd_first_stdout as _git
from tests.test_acceptance_gate import (BUGGY, FIXED, _caps, _make_sets,
                                        _repo, _st, _staging)
from tests.test_delivery_redrive import DEFECT, World

MODELS = {"conformer": "sonnet", "verifiability_classifier": "sonnet",
          "delivery_ratchet": "sonnet"}
EFFORTS = {"conformer": "low", "verifiability_classifier": "medium",
           "delivery_ratchet": "medium"}
ITEMS = [{"item": "add returns the sum", "span": "S1",
          "verifiability": "tree"},
         {"item": "subtraction keeps its own function, no fallback in add",
          "span": "S2", "verifiability": "tree"},
         {"item": "no regressions in existing tests", "span": "S3",
          "verifiability": "tree"},
         {"item": "the PR body lists the fix", "span": "S4",
          "verifiability": "external"},
         {"item": "docs/ stays untouched", "span": "S5",
          "verifiability": "tree", "untouched_paths": ["docs/"]}]


@pytest.fixture(autouse=True)
def _quiet(leerie, monkeypatch):
    monkeypatch.setattr(leerie, "_host_cpu_psi", lambda *a, **k: 0.0)

    async def unchanged(st, caps, models, efforts, staging, final, sha):
        return final
    monkeypatch.setattr(leerie, "_acceptance_audit_and_rewrite", unchanged)
    leerie._DEPS_INSTALLED.clear()
    yield
    leerie._DEPS_INSTALLED.clear()


def _judged_before(sha: str, unmet: list[int]) -> dict:
    return {"sha": sha,
            "unmet": [{"item_index": i, "item": ITEMS[i]["item"],
                       "evidence": "missing"} for i in unmet],
            "details": [{"item_index": i, "samples": 1, "unmet_votes": 0,
                         "rejected": 0, "unmet_parts": [],
                         "met_evidence": f"calc.py cites item {i}"}
                        for i in (0, 1, 2) if i not in unmet]}


class Workers:
    """A stub `claude_p` for every worker the gate spawns: the conformer
    runs the next fixer, the classifier types item 2 `execution`, and the
    ratchet judge answers from `regress` — the items it marks unmet on the
    tree after the round it is judging (a list per round, per sample)."""

    def __init__(self, fixers, regress=None, files=None):
        self.fixers = list(fixers)
        self.regress = regress or {}
        self.files = files or {}
        self.calls: list[dict] = []
        self.round = 0

    async def __call__(self, **kw):
        self.calls.append(kw)
        key = kw["schema_key"]
        if key == "conformer":
            self.round += 1
            self.fixers.pop(0)(Path(kw["cwd"]))
            return {}
        if key == "verifiability_classifier":
            return {"items": [
                {"item_index": i, "verifiability":
                 "execution" if i == 2 else "in_tree", "reason": "r"}
                for i in range(len(ITEMS))]}
        assert key == "delivery_ratchet"
        sample = int(kw["sid"].rsplit("-s", 1)[1])
        unmet = self.regress.get(self.round, [[]] * 3)[sample]
        listed = json.loads(kw["user_prompt"].split(
            "Re-verify each against the CURRENT tree):\n", 1)[1].split(
            "\n\nCHANGES SINCE", 1)[0])
        return {"verdicts": [
            {"item_index": e["item_index"],
             "met": e["item_index"] not in unmet, "parts": [],
             "evidence": f"ok r{self.round} i{e['item_index']}",
             "evidence_files": self.files.get(e["item_index"], [])}
            for e in listed]}

    def of(self, key):
        return [c for c in self.calls if c["schema_key"] == key]


def _hedge_and_fix(staging: Path):
    """Fixes the failing case and adds the fallback item 1 forbids."""
    (staging / "calc.py").write_text(
        FIXED + "\n\ndef _fallback(a, b):\n    return a - b\n")
    _git(staging, "commit", "-qam", "conformer: fix add, keep a fallback")


def _fix(staging: Path):
    (staging / "calc.py").write_text(FIXED)
    _git(staging, "commit", "-qam", "conformer: fix add")


def _gate(leerie, monkeypatch, st, workers):
    monkeypatch.setattr(leerie, "claude_p", workers)
    asyncio.run(leerie._run_acceptance_gate(
        st.run_dir, st, _caps(leerie, 5), MODELS, EFFORTS))
    return st.data["acceptance"]["gate"]


# --- the acceptance gate ----------------------------------------------------

def test_a_round_that_regresses_a_met_item_is_reset_and_the_next_kept(
        leerie, tmp_path, monkeypatch):
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head, working_branch="main",
             required_items=ITEMS)
    staging = _staging(st, repo)
    start = _git(staging, "rev-parse", "HEAD")
    st.data["delivery_gate"] = {"judged_before": _judged_before(start, [])}
    st.data["acceptance"] = {"sets": _make_sets(leerie, st, 5)}
    # Round 1 fixes the case but hedges: the judge marks item 1 unmet in 2
    # of 3 samples. Round 2 fixes it cleanly.
    w = Workers([_hedge_and_fix, _fix],
                regress={1: [[1], [], [1]]})
    gate = _gate(leerie, monkeypatch, st, w)
    r1, r2 = gate["rounds"]
    assert r1["fixed_cases"] and r1["pre_sha"] == start
    assert r1["rollback_reason"] == "regressed a previously met required item"
    assert r1["rolled_back"] is True
    rat = r1["ratchet"]
    # Routes: 3 external never re-verified, 4 settled in code, 2 execution
    # (not judged; no test newly failed), 0 and 1 judged.
    assert rat["prior_met"] == [0, 1, 2, 4]
    assert [u["item_index"] for u in rat["untouched"]] == [4]
    assert rat["execution"] == [2] and rat["exec_flagged"] == []
    assert rat["judge"]["judged"] == [0, 1]
    assert rat["judge"]["sample1_flags"] == [1]
    assert rat["judge"]["confirmed"] == [1] and rat["regressed"] == [1]
    assert rat["judge"]["samples"] == 3
    # Round 2 starts from the tree before round 1, and is kept.
    assert r2["pre_sha"] == start and not r2.get("rollback_reason")
    assert r2["ratchet"]["regressed"] == []
    assert r2["ratchet"]["judge"]["samples"] == 1     # nothing flagged
    assert _git(staging, "log", "--format=%s", f"{start}..HEAD") == \
        "conformer: fix add"
    assert "_fallback" not in (staging / "calc.py").read_text()
    assert all(r["passed"] for r in gate["final"])
    # The classifier ran once for the two ratchets.
    assert len(w.of("verifiability_classifier")) == 1
    # The kept round's verdicts are the next ratchet's prior evidence.
    ev = st.data["delivery_ratchet"]["evidence"]
    assert ev["0"] == {"sha": r2["sha"], "evidence": "ok r2 i0"}


def test_the_ratchet_judge_sees_prior_evidence_and_the_round_diff(
        leerie, tmp_path, monkeypatch):
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head, working_branch="main",
             required_items=ITEMS)
    staging = _staging(st, repo)
    start = _git(staging, "rev-parse", "HEAD")
    st.data["delivery_gate"] = {"judged_before": _judged_before(start, [])}
    st.data["acceptance"] = {"sets": _make_sets(leerie, st, 5)}
    w = Workers([_fix])
    _gate(leerie, monkeypatch, st, w)
    j = w.of("delivery_ratchet")[0]
    assert j["system_prompt"].startswith(
        leerie._load_prompt("delivery_judge"))
    assert "evidence_files" in j["system_prompt"]
    up = j["user_prompt"]
    i_items = up.index("PREVIOUSLY VERIFIED ITEMS")
    i_diff = up.index("CHANGES SINCE THAT VERIFICATION")
    assert up.index("TASK:") < i_items < i_diff
    listed = json.loads(up[up.index("[", i_items):up.rindex("]", 0, i_diff)
                           + 1])
    assert listed == [
        {"item_index": 0, "item": ITEMS[0]["item"],
         "prior_verified_evidence": "calc.py cites item 0"},
        {"item_index": 1, "item": ITEMS[1]["item"],
         "prior_verified_evidence": "calc.py cites item 1"}]
    diff = up[i_diff:]
    assert f"git diff {start} HEAD" in diff
    assert "-    return a - b" in diff and "+    return a + b" in diff
    # A checkout of the commit after the group, never staging (DESIGN §8
    # *Every post-execution verdict is of the committed tree*;
    # tests/test_judges_read_the_committed_tree.py).
    assert j["schema_key"] == "delivery_ratchet"
    assert j["cwd"] == str(leerie._helper_worktree_path(st,
                                                        "delivery-ratchet"))
    assert j["allowed_tools"] == leerie.SATISFIED_PROBE_TOOLS


def test_evidence_files_touching_the_diff_buy_two_more_samples(
        leerie, tmp_path, monkeypatch):
    """Vote change V: an item sample 1 holds met is still re-sampled when
    the files its verdict relies on are files the repair changed — and
    confirmed only on 2 of 3."""
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head, working_branch="main",
             required_items=ITEMS)
    staging = _staging(st, repo)
    start = _git(staging, "rev-parse", "HEAD")
    st.data["delivery_gate"] = {"judged_before": _judged_before(start, [])}
    st.data["acceptance"] = {"sets": _make_sets(leerie, st, 5)}
    # Item 0 relies on calc.py (absolute and ./-prefixed, with a line):
    # touched. Item 1 relies on an unchanged file. Samples 2 and 3 split on
    # item 0 (one unmet vote) and both mark item 1 unmet — but item 1 is
    # not a candidate, so neither is confirmed.
    w = Workers([_fix], regress={1: [[], [0, 1], [1]]},
                files={0: [f"{staging}/calc.py:2", "./calc.py"],
                       1: ["report.md"]})
    gate = _gate(leerie, monkeypatch, st, w)
    j = gate["rounds"][0]["ratchet"]["judge"]
    assert j["sample1_flags"] == [] and j["touched"] == [0]
    assert j["candidates"] == [0] and j["samples"] == 3
    assert j["per_sample_regressed"] == [[[], [0, 1], [1]]]
    assert j["confirmed"] == []
    assert not gate["rounds"][0].get("rollback_reason")


def test_a_round_that_breaks_a_passing_test_is_reset_though_the_axis_was_red(
        leerie, tmp_path, monkeypatch):
    """The test axis always runs after a repair: the start is red (one
    failing test), so the gate-wide red-axis rule cannot fire, yet the
    round that newly breaks another test is reset — confirmed under B*
    against its own start — and flags the `execution` item."""
    repo, head = _repo(tmp_path)
    (repo / "calc.py").write_text(BUGGY + "\n\ndef mul(a, b):\n"
                                  "    return a * b\n")
    (repo / "test_mul.py").write_text(
        "from calc import mul\n\ndef test_mul():\n    assert mul(2, 3) == 6\n")
    (repo / "test_red.py").write_text(
        "def test_red():\n    assert False\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "mul and a red test")
    head = _git(repo, "rev-parse", "HEAD")
    st = _st(leerie, tmp_path, repo, head, working_branch="main",
             required_items=ITEMS)
    staging = _staging(st, repo)
    start = _git(staging, "rev-parse", "HEAD")
    st.data["delivery_gate"] = {"judged_before": _judged_before(start, [])}
    st.data["acceptance"] = {"sets": _make_sets(leerie, st, 5)}

    def break_mul(p):
        (p / "calc.py").write_text(FIXED + "\n\ndef mul(a, b):\n"
                                   "    return a + b\n")
        _git(p, "commit", "-qam", "conformer: fix add, break mul")

    def fix_both(p):
        (p / "calc.py").write_text(FIXED + "\n\ndef mul(a, b):\n"
                                   "    return a * b\n")
        _git(p, "commit", "-qam", "conformer: fix add")
    w = Workers([break_mul, fix_both])
    caps = _caps(leerie, 5)
    caps["confirm_intermittent_repeats"] = 2
    monkeypatch.setattr(leerie, "claude_p", w)
    asyncio.run(leerie._run_acceptance_gate(st.run_dir, st, caps, MODELS,
                                            EFFORTS))
    gate = st.data["acceptance"]["gate"]
    assert gate["pre_tests_passed"] is False
    r1, r2 = gate["rounds"]
    # The start's red test is not a candidate; only the newly broken one is.
    pre_ids = gate["test_ids"][start]["ids"]
    assert len(pre_ids) == 1 and pre_ids[0].startswith("test_red.py")
    rat = r1["ratchet"]
    assert len(rat["new_failures"]) == 1
    assert rat["new_failures"][0].startswith("test_mul.py")
    assert rat["exec_flagged"] == [2] and 2 in rat["regressed"]
    assert r1["rollback_reason"] == "regressed a previously met required item"
    assert r1["rolled_back"] is True
    assert r2["ratchet"]["new_failures"] == [] and \
        not r2.get("rollback_reason")
    assert "return a * b" in (staging / "calc.py").read_text()
    assert not gate.get("rolled_back")


def test_with_no_recorded_delivery_verdict_only_the_test_axis_ratchets(
        leerie, tmp_path, monkeypatch):
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head, working_branch="main",
             required_items=ITEMS)
    _staging(st, repo)
    st.data["acceptance"] = {"sets": _make_sets(leerie, st, 5)}
    w = Workers([_hedge_and_fix], regress={1: [[1], [1], [1]]})
    gate = _gate(leerie, monkeypatch, st, w)
    rat = gate["rounds"][0]["ratchet"]
    assert rat["prior_met"] == [] and rat["judge"] is None
    assert not gate["rounds"][0].get("rollback_reason")
    assert w.of("verifiability_classifier") == []
    assert w.of("delivery_ratchet") == []


# --- the delivery re-drive --------------------------------------------------

def test_a_redrive_group_that_regresses_a_met_item_is_rolled_back(
        leerie, tmp_path, monkeypatch):
    items = [{"item": "a keeps its guard", "span": "S1",
              "verifiability": "tree"}]
    w = World(leerie, tmp_path, items=items)
    w.st.data["delivery_gate"]["items"] = [
        {"item_index": 0, "item": items[0]["item"], "verdict": "met",
         "by": [], "judge": {"met_evidence": "src/a.ts guards"}}]
    w.st.save()
    w.install(monkeypatch)
    implementer = leerie.claude_p
    judged: list[dict] = []
    drives = {"n": 0}

    async def workers(**kw):
        if kw["schema_key"] == "implementer":
            drives["n"] += 1
            return await implementer(**kw)
        if kw["schema_key"] == "verifiability_classifier":
            return {"items": [{"item_index": 0, "verifiability": "in_tree",
                               "reason": "r"}]}
        judged.append(kw)
        # The first group's tree dropped the guard; the second's kept it.
        met = drives["n"] > 1
        return {"verdicts": [{"item_index": 0, "met": met, "parts": [],
                              "evidence": "e", "evidence_files": []}]}
    monkeypatch.setattr(leerie, "claude_p", workers)
    start = _git(w.staging, "rev-parse", "HEAD")
    w.edits = [lambda w: w.write("export const a = 1; // FIXED no guard\n"),
               lambda w: w.write("export const a = 1; // FIXED\n")]
    w.run()
    rec = w.st.data["delivery_redrive"]
    g1 = rec["rounds"][0]["groups"][0]
    assert g1["ratchet_regressed"] == [0] and g1["rolled_back"] is True
    assert g1["ratchet"]["judge"]["confirmed"] == [0]
    assert g1["ratchet"]["prior_met"] == [0]
    assert "src/a.ts guards" in judged[0]["user_prompt"]
    # The regressed item goes to the next round as a typed note.
    assert len(w.calls) == 2
    note = w.calls[1]["prompt"]
    assert '"test": "required item 0"' in note
    assert "a keeps its guard" in note
    g2 = rec["rounds"][1]["groups"][0]
    assert not g2.get("rolled_back") and g2["ratchet"]["regressed"] == []
    assert _git(w.staging, "log", "--format=%s", f"{start}..HEAD") == "fix"
    assert "no guard" not in (w.staging / "src" / "a.ts").read_text()


# --- units ------------------------------------------------------------------

def test_prior_met_reads_final_detection_then_the_judge_passes(leerie,
                                                               tmp_path):
    st = leerie.State(tmp_path / "s", "r", repo_root=tmp_path)
    st.data = {"required_items": ITEMS}
    assert leerie._ratchet_prior_met(st) == {}
    # The recheck's verdict wins over the pre-pass's; external and unmet
    # items are not previously met.
    st.data["delivery_gate"] = {
        "judged_before": _judged_before("a", []),
        "judged_after": _judged_before("b", [1])}
    assert leerie._ratchet_prior_met(st) == {
        0: "calc.py cites item 0", 2: "calc.py cites item 2", 4: ""}
    # Final detection's rows win over both; a kept ratchet's evidence
    # replaces the judge's.
    st.data["delivery_gate"]["items"] = [
        {"item_index": 0, "verdict": "unmet"},
        {"item_index": 1, "verdict": "met",
         "judge": {"met_evidence": "final i1"}},
        {"item_index": 3, "verdict": "external"}]
    st.data["delivery_ratchet"] = {"evidence": {
        "1": {"sha": "c", "evidence": "ratchet i1"}}}
    assert leerie._ratchet_prior_met(st) == {1: "ratchet i1"}
    st.data["skip_coverage_check"] = True
    assert leerie._ratchet_prior_met(st) == {}


def test_a_failed_classifier_skips_the_judge_and_is_asked_again(
        leerie, tmp_path, monkeypatch):
    st = leerie.State(tmp_path / "s", "r", repo_root=tmp_path)
    st.data = {"required_items": ITEMS[:2]}
    calls = []

    async def boom(**kw):
        calls.append(kw)
        raise leerie.WorkerError("no structured output")
    monkeypatch.setattr(leerie, "claude_p", boom)
    assert asyncio.run(leerie._ratchet_verifiability(
        st, leerie.DEFAULT_CAPS, {}, {})) is None
    assert "delivery_ratchet" not in st.data

    async def ok(**kw):
        calls.append(kw)
        return {"items": [{"item_index": 0, "verifiability": "execution",
                           "reason": "r"},
                          {"item_index": 7, "verifiability": "in_tree",
                           "reason": "out of range"}]}
    monkeypatch.setattr(leerie, "claude_p", ok)
    got = asyncio.run(leerie._ratchet_verifiability(
        st, leerie.DEFAULT_CAPS, {}, {}))
    assert got == {0: "execution"}
    assert st.data["delivery_ratchet"]["verifiability"] == {"0": "execution"}
    assert len(calls) == 2
    # Cached: no third call.
    assert asyncio.run(leerie._ratchet_verifiability(
        st, leerie.DEFAULT_CAPS, {}, {})) == {0: "execution"}
    assert len(calls) == 2
    payload = calls[1]["user_prompt"]
    assert json.loads(payload[payload.index("["):payload.rindex("]") + 1]) \
        == [{"item_index": 0, "item": ITEMS[0]["item"]},
            {"item_index": 1, "item": ITEMS[1]["item"]}]


def test_the_ratchet_schema_is_the_judges_plus_evidence_files(leerie):
    judge = leerie.SCHEMAS["delivery_judge"]
    rat = leerie.SCHEMAS["delivery_ratchet"]
    v = rat["properties"]["verdicts"]["items"]
    assert v["required"] == (judge["properties"]["verdicts"]["items"]
                             ["required"] + ["evidence_files"])
    assert v["properties"]["evidence_files"] == {
        "type": "array", "items": {"type": "string"}}
    assert "evidence_files" not in judge["properties"]["verdicts"]["items"][
        "properties"]
    assert leerie.SCHEMAS["verifiability_classifier"]["properties"]["items"][
        "items"]["properties"]["verifiability"]["enum"] == [
            "in_tree", "execution"]


def test_intermittent_rows_of_kept_ratchets_reach_the_evidence(leerie):
    state = {"test_id_diff": {"intermittent_regression": [
                 {"id": "t::a", "file": "t"}]},
             "delivery_ratchet": {"intermittent_regression": [
                 {"id": "t::a", "file": "dup"}, {"id": "t::b", "file": "t"}]}}
    assert leerie._intermittent_regression_rows(state) == [
        {"id": "t::a", "file": "t"}, {"id": "t::b", "file": "t"}]
    assert leerie._intermittent_regression_rows({}) == []


@pytest.mark.parametrize("unmeasured", [True, False, "budget"])
def test_an_unmeasured_id_diff_leaves_execution_items_unchecked_not_held(
        leerie, tmp_path, monkeypatch, unmeasured):
    """DESIGN §8 *A held case that fails again*: with no ID diff on either
    side the execution route checked nothing. The ratchet says so
    (`exec_unchecked`) instead of reading the items as held; a measured
    diff with no new failure leaves the list empty. A candidate the confirm
    budget left unsettled (review round 6 H1) is unchecked too — never a
    rollback."""
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head, working_branch="main",
             required_items=ITEMS)
    staging = _staging(st, repo)
    start = _git(staging, "rev-parse", "HEAD")
    st.data["delivery_gate"] = {"judged_before": _judged_before(start, [])}
    w = Workers([])
    monkeypatch.setattr(leerie, "claude_p", w)
    ex = ({"new": [], "resource_affected": [], "unmeasured": True,
           "intermittent_regression": []} if unmeasured is True else
          {"new": [], "resource_affected": [], "unmeasured": False,
           "budget_short_unsettled": ["t::a"] if unmeasured else [],
           "intermittent_regression": []})
    rat = asyncio.run(leerie._delivery_ratchet(
        st, _caps(leerie, 5), MODELS, EFFORTS, staging, start, start,
        exec_result=ex, label="t"))
    assert rat["execution"] == [2]
    assert rat["exec_unchecked"] == ([2] if unmeasured else [])
    assert rat["regressed"] == [] and 2 not in rat["judge"]["judged"]
    assert leerie._ratchet_rollback_reason(rat) is None


# --- review round 13 claims M1: a failed ratchet never keeps an item met ----

def _kept_group_world(leerie, tmp_path, monkeypatch, *, fail, recheck_ok):
    """A re-drive World with one met item and a kept group whose ratchet
    `fail`s at the `judge` or the `classifier`; the end-of-re-drive re-read
    of the unverified item crashes too unless `recheck_ok`."""
    items = [{"item": "a keeps its guard", "span": "S1",
              "verifiability": "tree"}]
    w = World(leerie, tmp_path, items=items)
    head = _git(w.staging, "rev-parse", "HEAD")
    w.st.data["delivery_gate"]["items"] = [
        {"item_index": 0, "item": items[0]["item"], "verdict": "met",
         "by": [], "judge": {"met_evidence": "src/a.ts guards"}}]
    w.st.data["delivery_gate"]["final_sha"] = head
    w.st.save()
    w.install(monkeypatch)
    implementer = leerie.claude_p

    async def workers(**kw):
        key = kw["schema_key"]
        if key == "implementer":
            return await implementer(**kw)
        if key == "verifiability_classifier":
            if fail == "classifier":
                raise leerie.WorkerError("classifier down")
            return {"items": [{"item_index": 0, "verifiability": "in_tree",
                               "reason": "r"}]}
        if key == "delivery_ratchet":
            raise leerie.WorkerError("ratchet judge down")
        assert key == "delivery_judge"
        if not recheck_ok:
            raise leerie.WorkerError("judge down")
        return {"verdicts": [{"item_index": 0, "met": True,
                              "parts": [{"requirement": "r",
                                         "kind": "behavioral",
                                         "status": "met", "cites": [{
                                             "path": "src/a.ts",
                                             "line_start": 1, "line_end": 1,
                                             "quote": "export const a = 1"}]}],
                              "evidence": "src/a.ts"}]}
    monkeypatch.setattr(leerie, "claude_p", workers)
    w.edits = [lambda w: w.write("export const a = 1; // FIXED\n")]
    w.run()
    return w, head


@pytest.mark.parametrize("fail", ["judge", "classifier"])
def test_a_failed_ratchet_leaves_the_item_unknown_and_unkeyed(
        leerie, tmp_path, monkeypatch, fail):
    w, head = _kept_group_world(leerie, tmp_path, monkeypatch, fail=fail,
                                recheck_ok=False)
    g = w.st.data["delivery_redrive"]["rounds"][0]["groups"][0]
    assert not g.get("rolled_back")
    assert g["ratchet"]["unverified"] == [0]
    gate = w.st.data["delivery_gate"]
    (row,) = gate["items"]
    assert row["verdict"] == "unknown"        # never `met` with no read
    assert "0" in w.st.data["delivery_ratchet"]["unverified"]
    assert leerie._ratchet_prior_met(w.st) == {}
    # Not re-keyed to the commit it was never read on.
    final = _git(w.staging, "rev-parse", "HEAD")
    assert final != head and gate["final_sha"] == head
    (e,) = [e for e in w.st.data["needs_human_verification"]
            if e["source"] == "ratchet_unverified"]
    assert "a keeps its guard" in e["detail"]


def test_a_failed_ratchet_item_is_read_again_at_the_end(leerie, tmp_path,
                                                        monkeypatch):
    """The retry: the re-drive's end re-reads the unverified item on the
    final commit; a read that succeeds settles it and the verdict is
    re-keyed."""
    w, head = _kept_group_world(leerie, tmp_path, monkeypatch, fail="judge",
                                recheck_ok=True)
    gate = w.st.data["delivery_gate"]
    (row,) = gate["items"]
    final = _git(w.staging, "rev-parse", "HEAD")
    assert row["verdict"] == "met" and row["redrive_recheck"]["sha"] == final
    assert "unverified" not in w.st.data["delivery_ratchet"]
    assert gate["final_sha"] == final
    assert not [e for e in w.st.data.get("needs_human_verification") or []
                if e["source"] == "ratchet_unverified"]


def test_a_failed_acceptance_round_ratchet_drops_the_item_from_prior_met(
        leerie, tmp_path, monkeypatch):
    """The acceptance path: the kept round's ratchet judge fails, so the
    in-tree items it was to re-read are unverified on the round's commit —
    no longer prior met for the next round, listed until detection reads
    them."""
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head, working_branch="main",
             required_items=ITEMS)
    staging = _staging(st, repo)
    start = _git(staging, "rev-parse", "HEAD")
    st.data["delivery_gate"] = {"judged_before": _judged_before(start, [])}
    st.data["acceptance"] = {"sets": _make_sets(leerie, st, 5)}
    w = Workers([_fix])

    async def workers(**kw):
        if kw["schema_key"] == "delivery_ratchet":
            raise leerie.WorkerError("ratchet judge down")
        return await w(**kw)
    monkeypatch.setattr(leerie, "claude_p", workers)
    asyncio.run(leerie._run_acceptance_gate(
        st.run_dir, st, _caps(leerie, 5), MODELS, EFFORTS))
    (r1,) = st.data["acceptance"]["gate"]["rounds"]
    assert not r1.get("rollback_reason")
    # Items 0 and 1 for the failed judge; item 2, execution-shaped, because
    # this round's ID diff measured nothing (review round 14 M3: such an
    # item was not read either, and used to stay prior met).
    assert r1["ratchet"]["exec_unchecked"] == [2]
    assert r1["ratchet"]["unverified"] == [0, 1, 2]
    assert sorted(st.data["delivery_ratchet"]["unverified"]) == [
        "0", "1", "2"]
    assert sorted(leerie._ratchet_prior_met(st)) == [4]


def test_the_execution_route_measures_staging_at_its_commit(
        leerie, tmp_path, monkeypatch, capsys):
    """The failing-test-ID measurement the ratchet's execution route reads
    runs where only the commit is: staging itself, asserted clean (I1 +
    I2; DESIGN §8 *The final diff describes the tree that ships*). Round
    13's withheld leftover in staging no longer exists; a file there now
    stops the run before anything is measured."""
    w = World(leerie, tmp_path)
    seen: list[tuple[str, bool]] = []

    async def ids(st, caps, tree, *, label, log_path):
        seen.append((tree, (Path(tree) / "src" / "leftover.ts").exists()))
        return {"ids": [], "files": {}, "command": "t"}
    monkeypatch.setattr(leerie, "_ratchet_test_ids", ids)
    asyncio.run(leerie._redrive_test_ids(w.st, w.caps, w.staging, "x"))
    assert seen == [(str(w.staging), False)]
    (w.staging / "src" / "leftover.ts").write_text("export const z = 1\n")
    seen.clear()
    with pytest.raises(SystemExit):
        asyncio.run(leerie._redrive_test_ids(w.st, w.caps, w.staging, "x"))
    assert seen == []
    assert "?? src/leftover.ts" in capsys.readouterr().err


def test_the_ratchet_exec_discovery_and_bstar_run_on_the_commit(
        leerie, tmp_path, monkeypatch):
    """`_ratchet_exec`'s discovery repeats and B*'s final side run in the
    same tree as the IDs: staging, asserted clean; never beside a file
    that does not ship."""
    w = World(leerie, tmp_path)
    seen: list[tuple[str, str]] = []

    async def disc(st, caps, tree, cmd, base, **k):
        seen.append(("discovery", str(tree)))
        return {"failed_ids": [], "fail_counts": {}, "id_files": {},
                "_runs": []}

    async def newf(st, caps, tree, pre, before, after, label, **k):
        seen.append(("bstar", str(tree)))
        return {"new": [], "resource_affected": [], "unmeasured": False}
    monkeypatch.setattr(leerie, "_intermittent_discovery", disc)
    monkeypatch.setattr(leerie, "_redrive_new_failures", newf)
    ids = {"ids": [], "files": {}, "command": "t"}

    def run():
        return asyncio.run(leerie._ratchet_exec(
            w.st, w.caps, w.staging, "HEAD", ids, ids, label="x",
            log_path=w.st.run_dir / "x.log"))
    run()
    assert seen == [("discovery", str(w.staging)), ("bstar", str(w.staging))]
    (w.staging / "src" / "leftover.ts").write_text("export const z = 1\n")
    seen.clear()
    with pytest.raises(SystemExit):
        run()
    assert seen == []
