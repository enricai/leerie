"""A no-op the base already carries is settled on evidence (DESIGN §8 *A
no-op the base already carries*, §13 routing table).

The defect, from the live barnacle run 2a663a3e: split child
`bugfix-005-1`, an audit of call sites planned to relink any that lacked an
argument, reported `nothing_to_change` three times. Nothing had integrated
on its surface, so the settle returned before any verify command or the
satisfied-probe ran; it was re-driven twice and settled `unconverged`, and
its dependents `bugfix-005-3` and `bugfix-006` never ran. Its parent had a
`verify`; the split dropped it, so the child had none.

These tests build that shape for real (wave 1, nothing integrated, the
child's worktree cut from the run branch) and drive the real
`_settle_subtask` and `_redrive_blocked_subtasks`, stubbing only the
implementer, the verify execution and the probe's `claude_p`.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from tests.conftest import run_git_out  # noqa: E402

RUN_ID = "synth-005-abcdef"
SID = "bugfix-005-1"
DEP = "bugfix-005-3"
DEP2 = "bugfix-006"
SRC = "src/flow.py"
TEST_FILE = "tests/test_flow.py"

_git = run_git_out


@pytest.fixture
def base(leerie, tmp_path):
    """Wave 1 of the synth run: the call sites already pass the argument on
    the base tree, nothing is integrated, and `bugfix-005-1`'s worktree is
    cut from the run branch."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "src").mkdir()
    (repo / "tests").mkdir()
    (repo / SRC).write_text(
        "def read_advance(snapshot, mid_flow):\n    return snapshot\n\n"
        "def site_a(s):\n    return read_advance(s, mid_flow=True)\n\n"
        "def site_b(s):\n    return read_advance(s, mid_flow=True)\n")
    (repo / TEST_FILE).write_text("def test_sites():\n    pass\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    run_branch = f"leerie/runs/{RUN_ID}"
    _git(repo, "checkout", "-q", "-b", run_branch)
    leerie_root = repo / ".leerie"
    run_dir = leerie_root / "runs" / RUN_ID
    for d in ("subtasks", "criteria", "logs", "worktrees"):
        (run_dir / d).mkdir(parents=True)
    worktree = leerie._subtask_worktree_path(run_dir, SID)
    _git(repo, "worktree", "add", "-q", "-b",
         f"leerie/subtasks/{RUN_ID}/{SID}", str(worktree), run_branch)
    st = leerie.State(leerie_root, RUN_ID)
    st.data = {"task": "x", "answers": {"source_of_truth": "codebase"}}
    st.save()
    caps = dict(leerie.DEFAULT_CAPS)
    caps["failed_retries"] = 0
    (run_dir / "plan.json").write_text(json.dumps({"subtasks": {
        SID: {"id": SID, "depends_on": []},
        DEP: {"id": DEP, "depends_on": [SID]},
        DEP2: {"id": DEP2, "depends_on": [SID, DEP]},
    }}))
    return {"leerie": leerie, "repo": repo, "run_dir": run_dir, "st": st,
            "worktree": worktree, "caps": caps,
            "models": {w: "sonnet" for w in leerie.WORKER_TYPES},
            "efforts": {w: None for w in leerie.WORKER_TYPES}}


VERIFY = [{"criterion": "existing site tests still pass",
           "argv": ["python3", "-m", "pytest", TEST_FILE]}]


def _spec(env, **fields):
    sub = {"id": SID, "title": "Audit and relink read_advance call sites",
           "success_criteria_seed": "every site passes mid_flow; existing "
                                    "site tests still pass",
           "files_likely_touched": [SRC],
           "verify": VERIFY, "verification_only": False}
    sub.update(fields)
    (env["run_dir"] / "subtasks" / f"{SID}.json").write_text(json.dumps(sub))


CLAIM = {"status": "blocked", "blocker_kind": "nothing_to_change",
         "blocker": "both sites already pass mid_flow",
         "summary": "audit only; nothing to relink",
         "criteria_results": [
             {"criterion": "every site passes mid_flow", "met": True,
              "evidence": "site_a and site_b both pass mid_flow=True",
              "locations": [{"path": SRC, "start_line": 4, "end_line": 8},
                            {"path": "src/missing.py", "start_line": 1},
                            {"path": "../outside.py", "start_line": 1},
                            {"path": "/etc/passwd"}]}]}


def _implementer(env, monkeypatch, result):
    notes = []

    async def _stub(sid, leerie_dir, caps, st, models, efforts,
                    continuation=False, note=""):
        notes.append(note)
        return {"subtask_id": sid, **result}
    monkeypatch.setattr(env["leerie"], "_run_implementer", _stub)
    return notes


def _execution(env, monkeypatch, verdicts):
    """`verdicts`: one per execution, in order. The stub persists its
    record as the real one does."""
    ran = []
    queue = list(verdicts)

    async def _ex(subtask, worktree, st, caps, *, sid):
        verdict = queue.pop(0) if len(queue) > 1 else queue[0]
        ran.append(verdict)
        # `why` as the real record words it: the command and its exit.
        why = ("every verify command exited 0" if verdict == "pass" else
               f"`python3 -m pytest {TEST_FILE}` exited 1 on 3 run(s)")
        rec = {"verdict": verdict, "why": why, "dirty": [],
               "commands": [{"argv": VERIFY[0]["argv"],
                             "tail": "E   AssertionError: site_b drops "
                                     "mid_flow", "rc": 0
                             if verdict == "pass" else 1}]}
        st.data.setdefault("settled_by_execution", {})[sid] = rec
        st.save()
        return rec
    monkeypatch.setattr(env["leerie"], "_settle_by_execution", _ex)
    return ran


def _probe(env, monkeypatch, verdict):
    seen = []

    async def _cp(*, user_prompt, sid, cwd=None, **kw):
        assert kw.get("schema_key") == "satisfied_probe"
        seen.append({"prompt": user_prompt, "sid": sid, "cwd": cwd})
        return dict(verdict)
    monkeypatch.setattr(env["leerie"], "claude_p", _cp)
    return seen


def _payload(seen):
    return json.loads(seen["prompt"].split("SUBTASK:\n", 1)[1]
                      .split("\n\nReturn only", 1)[0])


def _settle(env, note=None):
    c = env["leerie"]
    return asyncio.run(c._settle_subtask(
        SID, env["run_dir"], env["caps"], env["st"], env["models"],
        env["efforts"], redrive_note=note))


def _redrive(env, results):
    c = env["leerie"]

    async def settle(s, note):
        return await c._settle_subtask(
            s, env["run_dir"], env["caps"], env["st"], env["models"],
            env["efforts"], redrive_note=note)
    asyncio.run(c._redrive_blocked_subtasks(
        results, settle, env["st"], env["caps"], env["run_dir"]))
    return results


SATISFIED = {"satisfied": True, "evidence": "both sites pass mid_flow",
             "checked": [SRC]}


def test_passing_verify_and_satisfied_probe_settle_complete_and_dependents_run(
        base, monkeypatch):
    env = base
    _spec(env)
    calls = _implementer(env, monkeypatch, CLAIM)
    ran = _execution(env, monkeypatch, ["pass"])
    seen = _probe(env, monkeypatch, SATISFIED)

    res = _settle(env)

    assert res["status"] == "complete"
    assert len(calls) == 1 and ran == ["pass"] and len(seen) == 1
    assert seen[0]["cwd"] == str(env["worktree"])
    # The probe is shown the implementer's evidence; only locations that
    # are relative paths to files on the tree survive.
    claim = _payload(seen[0])["implementer_claim"]
    assert claim[0]["locations"] == [{"path": SRC, "start_line": 4,
                                      "end_line": 8}]
    assert "a claim to check" in seen[0]["prompt"]
    st = env["st"].data
    drop = st["dropped_subtasks"][SID]
    assert drop["reason"] == "satisfied_on_base"
    assert "satisfied_by" not in drop
    assert drop["executed"] == [f"python3 -m pytest {TEST_FILE}"]
    assert drop["claimed_locations"] == 1
    c = env["leerie"]
    assert c._unconverged_predecessors(env["run_dir"], env["st"], DEP) == []
    assert c._unconverged_predecessors(env["run_dir"], env["st"], DEP2) == []


def test_failing_verify_redrives_once_with_its_output_then_settles(
        base, monkeypatch):
    env = base
    _spec(env)
    notes = _implementer(env, monkeypatch, CLAIM)
    ran = _execution(env, monkeypatch, ["fail"])
    seen = _probe(env, monkeypatch, SATISFIED)

    first = _settle(env)
    assert first["status"] == "blocked"
    assert seen == [], "a refuted claim never reaches the probe"
    assert "site_b drops mid_flow" in first["blocker"]
    assert "untrusted data" in first["blocker"]

    results = _redrive(env, {SID: first})

    st = env["st"].data
    assert results[SID]["status"] == "unconverged"
    assert st["unconverged_subtasks"][SID]["kind"] == "no_op_refuted"
    # One re-drive, carrying the failing command's output, then settled.
    assert st["redrive_rounds"][SID] == 1
    assert len(notes) == 2
    assert notes[1].startswith("RE-DRIVE 1:")
    assert "site_b drops mid_flow" in notes[1]
    assert f"python3 -m pytest {TEST_FILE}" in notes[1]
    assert ran == ["fail", "fail"]


def test_cannot_verify_with_no_command_settles_undecidable_at_once(
        base, monkeypatch):
    env = base
    _spec(env, verify=[{"criterion": "every site passes mid_flow",
                        "argv": []}])
    calls = _implementer(env, monkeypatch, CLAIM)
    ran = _execution(env, monkeypatch, ["pass"])
    seen = _probe(env, monkeypatch, {
        "satisfied": False, "unsatisfied_reason": "cannot_verify",
        "evidence": "whether a site judges from a pre-settle snapshot "
                    "needs a run", "unverifiable": ["seed", "criterion-1"],
        "covered_by_execution": []})

    res = _settle(env)
    results = _redrive(env, {SID: res})

    st = env["st"].data
    assert results[SID]["status"] == "unconverged"
    assert st["unconverged_subtasks"][SID]["kind"] == "undecidable_no_op"
    assert len(calls) == 1 and len(seen) == 1 and ran == []
    assert (st.get("redrive_rounds") or {}).get(SID, 0) == 0, \
        "no re-drive round spent"
    # Zero commits: its dependents run, and test the claim themselves.
    c = env["leerie"]
    assert c._unconverged_predecessors(env["run_dir"], env["st"], DEP) == []
    assert c._unconverged_predecessors(env["run_dir"], env["st"], DEP2) == []


def test_any_other_unconverged_predecessor_still_stops_dependents(
        base, monkeypatch):
    env = base
    c = env["leerie"]
    for kind in (None, "no_op_refuted", "environment_limit"):
        c._settle_unconverged(env["st"], SID, "x", previous_status="blocked",
                              kind=kind)
        assert c._unconverged_predecessors(
            env["run_dir"], env["st"], DEP) == [SID], kind


def test_e33_shape_verify_passes_but_the_probe_finds_the_gap(
        base, monkeypatch):
    """Running a test file passes whether or not the new case was written:
    the probe, shown the implementer's evidence, finds the case missing, and
    the claim is re-driven with that gap — never settled complete."""
    env = base
    _spec(env, title="Add a regression test for site_b",
          success_criteria_seed="a test asserts site_b passes mid_flow",
          files_likely_touched=[TEST_FILE])
    _implementer(env, monkeypatch, {**CLAIM, "criteria_results": [
        {"criterion": "a test asserts site_b passes mid_flow", "met": True,
         "evidence": "test_sites covers it",
         "locations": [{"path": TEST_FILE, "start_line": 1,
                        "end_line": 2}]}]})
    _execution(env, monkeypatch, ["pass"])
    seen = _probe(env, monkeypatch, {
        "satisfied": False, "unsatisfied_reason": "artifact_missing",
        "evidence": "test_sites asserts nothing",
        "gaps": [{"criterion": "seed", "path": TEST_FILE, "start_line": 1,
                  "end_line": 2,
                  "missing": "no assertion on site_b's mid_flow"}]})

    res = _settle(env)

    assert res["status"] == "blocked"
    assert len(seen) == 1
    assert f"seed at {TEST_FILE}:1-2: no assertion on site_b's mid_flow" \
        in res["blocker"]
    st = env["st"].data
    assert st["subtask_status"][SID] == "blocked"
    assert SID not in (st.get("dropped_subtasks") or {})


def test_a_crashed_probe_redrives_and_never_settles(base, monkeypatch):
    env = base
    _spec(env)
    _implementer(env, monkeypatch, CLAIM)
    _execution(env, monkeypatch, ["pass"])

    async def _boom(**kw):
        raise env["leerie"].WorkerError("probe died")
    monkeypatch.setattr(env["leerie"], "claude_p", _boom)

    res = _settle(env)

    assert res["status"] == "blocked"
    assert "reached no verdict" in res["blocker"]


def test_old_saved_state_resumes(base, monkeypatch):
    """A state written before this change: a predecessor's unconverged
    record without `kind`, a failed execution record without `no_op`, and
    a no-op claim without `locations`. The record without `kind` still
    stops dependents; the old failure does not count as the one re-drive."""
    env = base
    c = env["leerie"]
    st = env["st"]
    st.data.update({
        "subtask_status": {SID: "blocked"},
        "redrive_rounds": {SID: 1},
        "settled_by_execution": {SID: {
            "verdict": "fail", "why": "old", "dirty": [],
            "commands": [{"argv": VERIFY[0]["argv"], "rc": 1, "tail": ""}]}},
        "unconverged_subtasks": {"bugfix-004": {
            "at": "2026-11-01T00:00:00Z", "previous_status": "blocked",
            "reason": "[nothing_to_change] old"}},
        "dropped_subtasks": {"bugfix-002": {
            "reason": "satisfied_by_sibling", "satisfied_by": [],
            "evidence": "", "checked": []}},
    })
    st.data["subtask_status"]["bugfix-004"] = "unconverged"
    st.save()
    (env["run_dir"] / "plan.json").write_text(json.dumps({"subtasks": {
        "bugfix-004": {"id": "bugfix-004", "depends_on": []},
        SID: {"id": SID, "depends_on": []},
        DEP: {"id": DEP, "depends_on": ["bugfix-004"]}}}))
    assert c._unconverged_predecessors(env["run_dir"], st, DEP) == \
        ["bugfix-004"]
    _spec(env)
    old_claim = {k: v for k, v in CLAIM.items() if k != "criteria_results"}
    _implementer(env, monkeypatch, old_claim)
    _execution(env, monkeypatch, ["fail"])
    seen = _probe(env, monkeypatch, SATISFIED)

    res = _settle(env, note="RE-DRIVE 1: resumed")

    assert res["status"] == "blocked", "the old record is not a no-op's"
    assert seen == []
    assert st.data["settled_by_execution"][SID]["no_op"] is True


@pytest.mark.parametrize("res, expected", [
    ({}, []),
    ({"criteria_results": None}, []),
    ({"criteria_results": [{"criterion": "c", "met": True, "evidence": "e"}]},
     [{"criterion": "c", "evidence": "e", "locations": [], "met": True}]),
    ({"criteria_results": [{"criterion": "c", "locations": [
        {"path": SRC, "start_line": 0, "end_line": True}]}]},
     [{"criterion": "c", "evidence": "", "locations": [{"path": SRC}]}]),
])
def test_no_op_claim_evidence_filters_mechanically(base, res, expected):
    got = base["leerie"]._no_op_claim_evidence(res, str(base["worktree"]))
    assert got == expected


# --- split children carry `verify` -------------------------------------

PARENT = {"id": "bugfix-005", "title": "Audit call sites",
          "success_criteria_seed": "every site passes mid_flow",
          "files_likely_touched": [SRC, TEST_FILE, "src/other.py"],
          "change_shape": "localized", "independent_sites": False,
          "verify": VERIFY, "verification_only": False}


def _decompose(leerie, monkeypatch, tmp_path, children):
    from tests.conftest import _fit_response, _make_caps
    from unittest.mock import MagicMock

    async def fake(*args, schema_key, sid="", **kw):
        if schema_key == "fit_judge":
            return _fit_response(0.2 if sid.endswith("-d0") else 0.9)
        assert schema_key == "splitter"
        return {"children": [dict(c) for c in children]}
    monkeypatch.setattr(leerie, "claude_p", fake)
    st = MagicMock()
    st.data = {"worker_count": 0}
    st.bump_workers = MagicMock()
    logged = []
    monkeypatch.setattr(leerie, "log", lambda m, *a, **k: logged.append(m))
    caps = _make_caps(leerie, subfile_split_max_span=leerie.DEFAULT_CAPS[
        "subfile_split_max_span"])
    leaves = asyncio.run(leerie._recursive_decompose(
        dict(PARENT), 0, st, caps, {"fit_judge": "sonnet",
                                    "splitter": "sonnet"},
        {"fit_judge": None, "splitter": None}, tmp_path))
    return leaves, logged


def test_splitter_children_keep_their_own_verify(leerie, monkeypatch,
                                                 tmp_path):
    kids = [{"id": "bugfix-005-1", "title": "sites",
             "success_criteria_seed": "every site passes mid_flow",
             "files_likely_touched": [SRC], "verify": VERIFY,
             "verification_only": True},
            {"id": "bugfix-005-2", "title": "other",
             "success_criteria_seed": "other passes",
             "files_likely_touched": ["src/other.py", TEST_FILE]}]
    leaves, logged = _decompose(leerie, monkeypatch, tmp_path, kids)
    by = {c["id"]: c for c in leaves}
    assert by["bugfix-005-1"]["verify"] == VERIFY
    assert "verification_only" not in by["bugfix-005-1"], \
        "a split child never claims verification-only"
    # A child the splitter left without verify is a logged plan defect.
    assert "verify" not in by["bugfix-005-2"]
    assert any("SPLIT_CHILD_NO_VERIFY" in m and "bugfix-005-2" in m
               and "bugfix-005-1" not in m.split("child(ren)")[1]
               for m in logged)


def test_splitter_schema_offers_verify_per_child(leerie):
    """The item schema admits unknown keys, so validation alone proves
    nothing: the property must be declared (it is what the CLI's schema
    and the strict grammar offer the worker), typed like the planner's."""
    item = leerie.SCHEMAS["splitter"]["properties"]["children"]["items"]
    planner = leerie.SCHEMAS["planner"]["properties"]["subtasks"]["items"]
    assert item["properties"]["verify"] == planner["properties"]["verify"]
    assert "verification_only" not in item["properties"]


def test_code_built_children_inherit_the_parents_verify(leerie, tmp_path):
    (tmp_path / "big.py").write_text("x = 1\n" * 2000)
    (tmp_path / "small.py").write_text("y = 1\n")
    parent = {**PARENT, "files_likely_touched": ["big.py", "small.py"],
              "verification_only": True}
    kids = leerie._peel_oversized_file(parent, tmp_path, 400, 8)
    assert len(kids) == 2
    for k in kids:
        assert k["verify"] == VERIFY
        assert k["verify"] is not parent["verify"]
        assert "verification_only" not in k
    region = leerie._subfile_child(parent, "big.py", (1, 400), [], "r-1",
                                   "c", 0, 2, "r-1", True)
    assert region["verify"] == VERIFY
    no_verify = {k: v for k, v in PARENT.items() if k != "verify"}
    assert "verify" not in leerie._migration_child(
        no_verify, [SRC], "m-1", "t", "c")
    own = [{"criterion": "chunk", "argv": ["python3", "-m", "pytest"]}]
    assert leerie._migration_child(PARENT, [SRC], "m-1", "t", "c",
                                   verify=own)["verify"] == own
