"""A held-out repair round that turns a confirmed probe from passing to
failing is rolled back (DESIGN §8 *A held-out repair round that turns a
confirmed probe from passing to failing is rolled back*; issue 42, lane HR).

Live (barnacle 2dcb4cce, leerie 0.49.0): acceptance repair round 1
(`3dffb039` -> `fa80ed17`) fixed the held-out frame-locator case and, in the
same commit, made a bare `[role='combobox'][aria-expanded]` a prompt
trigger. The delivery ratchet's judge read item 8 (layered dropdown widgets)
met on the round's commit — "the layered collapse logic is untouched" — so
the round was kept. Final detection's falsifier then confirmed item 8 unmet
with a layered widget of the item's own shape; that probe passes 3/3 on the
round's start and fails 3/3 on its commit (lane FL's replay). Detection's
confirm was routed as an unmet item and never tried against the round, and
the run ended `unconverged` with the regression it wrote on its branch.

The replay: a real pytest repository, its run repository and staging, the
real acceptance gate (its repair round's real commit, its per-case rule, the
delivery ratchet), the real shipping-diff refresh, the real detection (the
falsifier's real gates and B* on pristine checkouts), the real flip check
(the probe on pristine checkouts of the round's start and commit, B*'s real
pairs), and the real delivery re-drive. Stubbed: `claude_p`, the TS AST
facts (the probes are Python), the build/lint/test axes outside the ratchet
and the held-out set B*/discovery reruns, as tests/test_acceptance_gate.py
stubs them. Synthetic text."""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import pytest

from tests.conftest import run_git_out as _git
from tests.test_acceptance_gate import (  # noqa: F401  (autouse fixtures)
    _clear_deps_memo, _confirm_at_once, _no_set_audit)
from tests.test_delivery_detection import (PROBE_REL, PROBE_SRC, _claim,
                                           _facts_for, _placed, _staging)

ITEM0 = "add(2, 3) returns 5, the sum of its arguments"
BUGGY_ADD = "def add(a, b):\n    return a - b\n"
FIXED_ADD = "def add(a, b):\n    return a + b\n"
SUB = "\n\ndef sub(a, b):\n    return a - b\n"
SUB_TEST = ("from calc import sub\n\n"
            "def test_sub():\n    assert sub(5, 3) == 2\n")
CONTROL = ("from calc import add\n\n"
           "def test_add_zero():\n    assert add(0, 0) == 0\n")
# Over-reads item 0: it also demands a name the item never asks for, so it
# fails on every tree, the round's start included.
FALSE_PROBE = ("from calc import add\n\n\n"
               "def test_add_returns_the_sum():\n"
               "    assert add(2, 3) == 5 and add.__name__ == \"plus\"\n")
MODELS = {k: "sonnet" for k in (
    "conformer", "implementer", "delivery_judge", "falsifier",
    "delivery_ratchet", "verifiability_classifier", "observability_judge")}
EFFORTS = {k: "medium" for k in MODELS}


@pytest.fixture(autouse=True)
def _quiet_host(leerie, monkeypatch):
    monkeypatch.setattr(leerie, "_host_cpu_psi", lambda *a, **k: 0.0)


def _repo(tmp_path: Path) -> tuple[Path, str, str]:
    """A base whose `add` subtracts, and subtask s1's integrated work, which
    fixed it. No existing test covers `add`: only the probe reads it."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@x")
    _git(repo, "config", "user.name", "t")
    (repo / "calc.py").write_text(BUGGY_ADD)
    (repo / "conftest.py").write_text(
        "import sys, pathlib\n"
        "sys.path.insert(0, str(pathlib.Path(__file__).parent))\n")
    (repo / "test_smoke.py").write_text("def test_smoke():\n    pass\n")
    (repo / ".leerie").mkdir()
    (repo / ".leerie" / "config.toml").write_text(
        'test_scoped = "python3 -m pytest -q -p no:cacheprovider '
        '{test_files}"\n'
        'test = "python3 -m pytest -q -p no:cacheprovider"\n')
    (repo / ".gitignore").write_text("__pycache__/\n.leerie/runs/\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    base = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "-b", "sub-s1")
    (repo / "calc.py").write_text(FIXED_ADD)
    _git(repo, "commit", "-qam", "s1 work")
    _git(repo, "checkout", "-q", "main")
    _git(repo, "merge", "-q", "--no-ff", "-m", "leerie: integrate s1",
         "sub-s1")
    return repo, base, _git(repo, "rev-parse", "HEAD")


def _commit(tree: Path, text: str, msg: str) -> None:
    (tree / "calc.py").write_text(text)
    _git(tree, "commit", "-qam", msg)


def repair_breaking_add(tree: Path) -> None:
    """2dcb4cce's round 1: the held-out case is fixed, and the same commit
    regresses the met item."""
    _commit(tree, BUGGY_ADD + SUB, "conformer: add sub")


def repair_keeping_add(tree: Path) -> None:
    _commit(tree, FIXED_ADD + SUB, "conformer: add sub")


class Run:
    def __init__(self, leerie, tmp_path, monkeypatch, *, repair,
                 probe=PROBE_SRC, redrive=None):
        self.L = leerie
        repo, base, head = _repo(tmp_path)
        self.head = head
        root = tmp_path / "state"
        run_dir = root / "runs" / "run-h"
        (run_dir / "logs").mkdir(parents=True)
        st = leerie.State(root, "run-h", repo_root=repo)
        st.data = {
            "task": "make add return the sum and add sub",
            "verbosity": "quiet", "categories": ["bug-fixing"],
            "working_branch": "main", "base_sha": base,
            "repo_state_before_planning": {"head": base},
            "defect_scope": {"applicable": True, "sites": [],
                             "defect_shape": "add subtracts"},
            "worker_count": 0,
            "required_items": [{"item": ITEM0, "span": "S1",
                                "verifiability": "tree"}],
            "subtask_status": {"s1": "complete"},
            # The judge before the gate read item 0 met on the integrated
            # tree (2dcb4cce's `judged_before`: every item met).
            "delivery_gate": {"unmet_after": [], "judged_after": {
                "sha": head, "unmet": [], "details": [{
                    "item_index": 0, "samples": 3, "unmet_votes": 0,
                    "rejected": 0, "unmet_parts": []}]}}}
        st.save()
        leerie._ensure_run_repo(st)
        self.staging = _staging(st, repo, head)
        d = run_dir / "acceptance" / "set-1"
        (d / "acc").mkdir(parents=True)
        (d / "acc" / "test_defect_1.py").write_text(SUB_TEST)
        (d / "acc" / "test_control_1.py").write_text(CONTROL)
        st.data["acceptance"] = {"sets": [{
            "index": 1, "valid": True, "dir": str(d),
            "defect_files": ["acc/test_defect_1.py"],
            "control_files": ["acc/test_control_1.py"],
            "cases": {"acc/test_defect_1.py": ["sub subtracts"],
                      "acc/test_control_1.py": ["zero"]}}]}
        st.save()
        (run_dir / "plan.json").write_text(json.dumps({"subtasks": {
            "s1": {"id": "s1", "title": "calc", "intent": "fix calc",
                   "change_kind": "fix", "files_likely_touched": ["calc.py"],
                   "claims": {"sites": [], "items": [
                       {"item_index": 0, "kind": "deliverable"}]}}}}))
        self.st = st
        self.caps = dict(leerie.DEFAULT_CAPS)
        self.caps.update(max_parallel=1, falsifier_samples=1,
                         acceptance_sets=1, acceptance_repair_rounds=1,
                         worker_timeout_sec=120)
        self.calls: list[dict] = []
        me = self

        async def workers(**kw):
            me.calls.append(kw)
            key = kw["schema_key"]
            if key == "conformer":
                repair(Path(kw["cwd"]))
                return {}
            if key == "implementer":
                redrive(Path(kw["cwd"]))
                return {"status": "complete", "summary": "added sub"}
            if key == "verifiability_classifier":
                return {"items": [{"item_index": 0, "verifiability":
                                   "in_tree", "reason": "r"}]}
            if key == "delivery_ratchet":
                # It repeats the round's claim, as 2dcb4cce's did.
                listed = json.loads(kw["user_prompt"].split(
                    "Re-verify each against the CURRENT tree):\n", 1)[1]
                    .split("\n\nCHANGES SINCE", 1)[0])
                return {"verdicts": [
                    {"item_index": e["item_index"], "met": True,
                     "parts": [], "evidence": "the diff only adds sub",
                     "evidence_files": []} for e in listed]}
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
                return {"items": []}
            assert key == "falsifier", key
            p = Path(kw["cwd"]) / PROBE_REL
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(probe)
            return _claim()
        monkeypatch.setattr(leerie, "claude_p", workers)
        monkeypatch.setattr(leerie, "_ts_resolvable",
                            lambda tree, **_kw: True)
        monkeypatch.setattr(
            leerie, "_probe_case_facts_placed",
            _placed(lambda tree, probe_, case, **_kw:
                    _facts_for(Path(probe_).read_text())))
        real = leerie._measure_axes

        async def green(tree, axes, st_, caps, **kw):
            if kw.get("log_prefix") == "delivery ratchet":
                return await real(tree, axes, st_, caps, **kw)
            return {a: {"ran": True, "measured": True, "passed": True,
                        "command": c, "summary": ""} for a, c in axes.items()}
        monkeypatch.setattr(leerie, "_measure_axes", green)

    def finalize(self, *, flips=True, redrive=False) -> None:
        """`_run_phases`' order from the gate on: gate, diff refresh,
        detection, the flip check, then the re-drive."""
        L, d, st = self.L, self.st.run_dir, self.st

        async def go():
            await L._run_acceptance_gate(d, st, self.caps, MODELS, EFFORTS)
            await L._refresh_shipping_test_id_diff(
                d, st, self.caps, label="test-id-diff-acceptance")
            await L._run_delivery_detection(d, st, self.caps, MODELS,
                                            EFFORTS)
            if flips:
                await L._acceptance_item_flips(d, st, self.caps, MODELS,
                                               EFFORTS)
            if redrive:
                await L._run_delivery_redrive(d, st, self.caps, MODELS,
                                              EFFORTS)
        asyncio.run(go())

    def keys(self) -> list[str]:
        return [c["schema_key"] for c in self.calls]


def _unmet(st) -> list[int]:
    return [u["item_index"] for u in st.data["delivery_gate"]["unmet_final"]]


def test_replay_2dcb4cce_the_round_is_rolled_back_and_the_case_redriven(
        leerie, tmp_path, monkeypatch):
    """The round fixes the held-out case and breaks `add`; the ratchet's
    judge keeps item 0 met, detection's probe confirms it unmet. The probe
    passes on the round's start and fails on its commit under B*: the round
    is rolled back, detection reads item 0 met on the restored tree, and the
    held-out case goes to the re-drive with the item's note. The re-drive's
    fix keeps both, and both ship."""
    r = Run(leerie, tmp_path, monkeypatch, repair=repair_breaking_add,
            redrive=repair_keeping_add)
    r.finalize(redrive=True)
    st = r.st
    gate = st.data["acceptance"]["gate"]
    (rnd,) = gate["rounds"]
    # The round's own rules kept it: it fixed the case, broke none, and the
    # ratchet's judge held the item met.
    assert rnd["fixed_cases"] and rnd["broken_cases"] == []
    assert rnd["ratchet"].get("regressed") in (None, [])
    # The flip check rolled it back.
    assert rnd["rolled_back"] is True
    (flip,) = rnd["flip_regressed"]
    assert flip["item_index"] == 0 and flip["pre_sha"] == r.head
    assert flip["post_sha"] == rnd["sha"]
    assert flip["b_star"]["new"] == [flip["case_id"]]
    assert "from passing on its start to failing on its commit" in \
        rnd["rollback_reason"]
    (rec,) = gate["item_flips"]
    assert rec["landed"] is True and rec["rollback_to"] == r.head
    assert rec["from_sha"] == rnd["sha"]
    rows = st.data["round_rollbacks"]
    assert [(x["worker"], x["label"], x["pre_sha"]) for x in rows][:1] == [
        ("acceptance repair", "round 1", r.head)]
    # The re-drive was handed the case and the item's note, and its fix
    # kept both: what ships is the joined fix, item met, set passing.
    impl = [c for c in r.calls if c["schema_key"] == "implementer"]
    assert impl, r.keys()
    p = impl[0].get("user_prompt", "")
    assert '"test": "required item 0"' in p and flip["case_id"] in p
    assert '"kind": "control_regression"' in p
    assert "acceptance repair round" in p
    final = _git(r.staging, "rev-parse", "HEAD")
    assert (r.staging / "calc.py").read_text() == FIXED_ADD + SUB
    assert _unmet(st) == []
    acc = st.data["acceptance"]["gate"]
    assert acc["final_sha"] == final
    assert all(x["passed"] for x in acc["final"])


def test_without_a_redrive_the_restored_tree_ships_the_case_failing(
        leerie, tmp_path, monkeypatch):
    """After the rollback, the verdicts are of the restored tree: the set
    fails again (the residual names it), detection reads item 0 met, and the
    note waits in `delivery_redrive.pending_controls` for the next fixer."""
    r = Run(leerie, tmp_path, monkeypatch, repair=repair_breaking_add)
    r.finalize()
    st = r.st
    assert _git(r.staging, "rev-parse", "HEAD") == r.head
    assert (r.staging / "calc.py").read_text() == FIXED_ADD
    gate = st.data["acceptance"]["gate"]
    assert gate["final_sha"] == r.head
    assert [x["passed"] for x in gate["final"]] == [False]
    assert gate["residual"]["failing_sets"] == 1
    dg = st.data["delivery_gate"]
    assert dg["final_sha"] == r.head and _unmet(st) == []
    (row,) = dg["items"]
    assert row["verdict"] == "met"
    (note,) = st.data["delivery_redrive"]["pending_controls"]
    assert note["key"] == "met-item-0" and note["kind"] == \
        "control_regression"
    assert "an earlier acceptance repair round made this required item" in \
        note["detail"]
    # Detection ran twice: on the round's commit, then on the restored one.
    assert r.keys().count("falsifier") == 2
    # No kept round is left to try a probe against.
    assert "item_flip_checked" not in gate


def test_a_round_that_keeps_the_item_is_kept(leerie, tmp_path, monkeypatch):
    """Control: the round fixes the case and leaves `add` alone. Detection's
    probe passes, nothing is confirmed, no probe runs on the round's start,
    and the round ships."""
    r = Run(leerie, tmp_path, monkeypatch, repair=repair_keeping_add)
    r.finalize()
    gate = r.st.data["acceptance"]["gate"]
    (rnd,) = gate["rounds"]
    assert not rnd.get("rolled_back") and "flip_regressed" not in rnd
    assert "item_flips" not in gate
    assert gate["item_flip_checked"] == rnd["sha"]
    assert _git(r.staging, "rev-parse", "HEAD") == rnd["sha"]
    assert _unmet(r.st) == []
    assert "round_rollbacks" not in r.st.data


def test_a_probe_failing_on_the_rounds_start_too_is_a_verdict_only(
        leerie, tmp_path, monkeypatch):
    """A confirmed probe that over-reads the item (barnacle 2dcb4cce items 0
    and 13, a2a825c2's four: each fails on both sides of its repair round)
    fails on the round's start too. The round did not write it: nothing is
    rolled back, and the probe stays the item's verdict."""
    r = Run(leerie, tmp_path, monkeypatch, repair=repair_breaking_add,
            probe=FALSE_PROBE)
    r.finalize()
    gate = r.st.data["acceptance"]["gate"]
    (rnd,) = gate["rounds"]
    assert not rnd.get("rolled_back") and "item_flips" not in gate
    assert gate["item_flip_checked"] == rnd["sha"]
    assert _git(r.staging, "rev-parse", "HEAD") == rnd["sha"]
    assert _unmet(r.st) == [0]


def test_the_check_runs_once_per_detected_commit(
        leerie, tmp_path, monkeypatch):
    """A resume that finds the check done on staging's HEAD runs no probe."""
    r = Run(leerie, tmp_path, monkeypatch, repair=repair_breaking_add,
            probe=FALSE_PROBE)
    r.finalize()
    seen: list = []

    async def spy(*a, **kw):
        seen.append(kw.get("label"))
        return {"flip": False, "pre": "fail", "post": "fail"}
    monkeypatch.setattr(leerie, "_redrive_probe_flips", spy)
    asyncio.run(leerie._acceptance_item_flips(
        r.st.run_dir, r.st, r.caps, MODELS, EFFORTS))
    assert seen == []


def test_a_decided_rollback_is_finished_on_resume(
        leerie, tmp_path, monkeypatch):
    """The decision reaches disk before the reset: a resume that finds it
    unfinished resets staging, marks the round, leaves the note and has
    detection read the restored tree."""
    r = Run(leerie, tmp_path, monkeypatch, repair=repair_breaking_add)
    r.finalize(flips=False)
    st = r.st
    gate = st.data["acceptance"]["gate"]
    (rnd,) = gate["rounds"]
    smp = leerie._redrive_flip_probe(st.data["delivery_gate"]["items"][0])
    assert smp is not None
    culprit = {"item_index": 0, "round": 1, "pre_sha": r.head,
               "post_sha": rnd["sha"], "case_id": smp["case_id"],
               "probe_saved": smp["probe_saved"], "b_star": None}
    gate["item_flips"] = [{
        "round": 1, "rollback_to": r.head, "from_sha": rnd["sha"],
        "culprits": [culprit], "reason": "decided before a crash",
        "notes": [leerie._redrive_flip_note(
            0, ITEM0, smp, who="acceptance repair round", short="round")],
        "landed": None}]
    st.save()

    async def no_probe(*a, **kw):
        raise AssertionError("a decided flip is not measured again")
    monkeypatch.setattr(leerie, "_redrive_probe_flips", no_probe)
    asyncio.run(leerie._acceptance_item_flips(
        st.run_dir, st, r.caps, MODELS, EFFORTS))
    gate = st.data["acceptance"]["gate"]
    assert gate["item_flips"][0]["landed"] is True
    assert gate["rounds"][0]["rolled_back"] is True
    assert gate["rounds"][0]["rollback_reason"] == "decided before a crash"
    assert _git(r.staging, "rev-parse", "HEAD") == r.head
    assert st.data["delivery_gate"]["final_sha"] == r.head
    assert _unmet(st) == []
    assert [n["key"] for n in
            st.data["delivery_redrive"]["pending_controls"]] == ["met-item-0"]


def test_kept_rounds_are_the_committed_ones_still_in_history(leerie):
    rounds = [
        {"round": 1, "pre_sha": "a", "sha": "b", "results": [{}]},
        {"round": 2, "pre_sha": "b", "sha": "c", "results": [{}],
         "rollback_reason": "fixed no failing case", "rolled_back": True},
        {"round": 3, "pre_sha": "b", "sha": "b", "results": [{}]},
        {"round": 4, "pre_sha": "b", "error": "x"},
        {"round": 5, "pre_sha": "b", "sha": "d", "results": [{}]}]
    assert [e["round"] for e in leerie._acceptance_kept_rounds(
        {"rounds": rounds})] == [1, 5]
    assert leerie._acceptance_kept_rounds(
        {"rounds": rounds, "rolled_back": True}) == []
