"""A criterion leerie's own execution decided is not undone by a probe that
could not check it (DESIGN §8 *A no-op a sibling already delivered is
settled on evidence*, §13 routing table).

Reproduced from the live barnacle run `54fb56c3`: wave 3's `bugfix-004`
changed files on the surface of `bugfix-006`, which then reported
`nothing_to_change`. Its sibling precondition held and its one verify
command (`pnpm vitest run` over two test files) exited 0 when leerie ran it.
The satisfied-probe re-ran the tests in its read-only tree, eleven of twelve
failed on `mkdirSync`, and it answered `cannot_verify` while reporting the
migration present. That verdict outvoted the pass three times; `bugfix-006`
settled `unconverged` and its dependent `bugfix-010` never ran.

These tests build that repo shape for real (the sibling's `leerie:
integrate bugfix-004` merge on the run branch, the subtask worktree cut from
it) and drive the real `_settle_subtask`, stubbing only the implementer, the
verify execution (the live record's shape) and the probe's `claude_p` (the
live verdict). Whether the dependent runs is then asked of
`_unconverged_predecessors`, the check the wave loop makes before running
it.
"""
from __future__ import annotations

import asyncio
import json

import pytest
from tests.conftest import run_git_out  # noqa: E402

RUN_ID = "54fb56c3ade3-live"
PRED = "bugfix-004"
SID = "bugfix-006"
DEP = "bugfix-010"
FILES = ["src/scripts/recon-generate-multiendpoint-e2e.test.ts",
         "src/scripts/recon-generate-url-param-bind.test.ts"]
ARGV = ["pnpm", "vitest", "run", *FILES]

_git = run_git_out


@pytest.fixture
def live(leerie, tmp_path):
    """The run after wave 3: `bugfix-004`'s change to the test files merged
    into the run branch as `leerie: integrate bugfix-004`; `bugfix-006`'s
    worktree cut from that tip."""
    repo = tmp_path / "repo"
    (repo / "src" / "scripts").mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    for f in FILES:
        (repo / f).write_text("expect(payload.BaseUrl).toBe(origin)\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    run_branch = f"leerie/runs/{RUN_ID}"
    _git(repo, "checkout", "-q", "-b", run_branch)
    pred_branch = f"leerie/subtasks/{RUN_ID}/{PRED}"
    _git(repo, "checkout", "-q", "-b", pred_branch, run_branch)
    for f in FILES:
        (repo / f).write_text("expect(context.baseUrl).toBe(origin)\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "origin from context.baseUrl")
    _git(repo, "checkout", "-q", run_branch)
    _git(repo, "merge", "--no-ff", "-q", "-m", f"leerie: integrate {PRED}",
         pred_branch)

    leerie_root = repo / ".leerie"
    run_dir = leerie_root / "runs" / RUN_ID
    for d in ("subtasks", "criteria", "logs", "worktrees"):
        (run_dir / d).mkdir(parents=True)
    worktree = leerie._subtask_worktree_path(run_dir, SID)
    _git(repo, "worktree", "add", "-q", "-b",
         f"leerie/subtasks/{RUN_ID}/{SID}", str(worktree), run_branch)
    st = leerie.State(leerie_root, RUN_ID)
    st.data = {"task": "x", "answers": {"source_of_truth": "codebase"},
               "subtask_status": {PRED: "complete"}}
    st.save()
    caps = dict(leerie.DEFAULT_CAPS)
    caps["failed_retries"] = 0
    (run_dir / "plan.json").write_text(json.dumps({"subtasks": {
        PRED: {"depends_on": []},
        SID: {"depends_on": [PRED]},
        DEP: {"depends_on": [SID]}}}))
    return {"leerie": leerie, "run_dir": run_dir, "st": st,
            "worktree": worktree, "caps": caps,
            "models": {w: "sonnet" for w in leerie.WORKER_TYPES},
            "efforts": {w: None for w in leerie.WORKER_TYPES}}


def _spec(env, **fields):
    """`bugfix-006` as the live plan typed it, trimmed to two files."""
    sub = {"id": SID, "title": "Migrate tests to context.baseUrl",
           "success_criteria_seed": "Each listed test file passes when run "
                                    "individually with its original "
                                    "assertion intent preserved.",
           "files_likely_touched": list(FILES),
           "verify": [{"criterion": "migrated files pass", "argv": ARGV}],
           "verification_only": False, "depends_on": [PRED]}
    sub.update(fields)
    (env["run_dir"] / "subtasks" / f"{SID}.json").write_text(json.dumps(sub))


NOTHING = {"status": "blocked", "blocker_kind": "nothing_to_change",
           "blocker": "All listed test files already pass on HEAD (after "
                      "bugfix-004). No payload.BaseUrl usage needs migrating.",
           "summary": "nothing to change"}


def _implementer(env, monkeypatch):
    calls = []

    async def _stub(sid, leerie_dir, caps, st, models, efforts,
                    continuation=False, note=""):
        calls.append(note)
        return {"subtask_id": sid, **NOTHING}
    monkeypatch.setattr(env["leerie"], "_run_implementer", _stub)
    return calls


def _execution(env, monkeypatch, verdict):
    """`_settle_by_execution` returning the live record's shape."""
    ran = []

    async def _ex(subtask, worktree, st, caps, *, sid):
        ran.append(str(worktree))
        ok = verdict == "pass"
        rec = {"verdict": verdict,
               "why": ("every verify command exited 0" if ok else
                       f"`{' '.join(ARGV)}` exited 1 on 3 run(s)"),
               "dirty": [],
               "commands": [{"argv": list(ARGV), "rc": 0 if ok else 1,
                             "attempts": 1 if ok else 3, "passed": ok,
                             "resource_affected": False,
                             "environment_limit": False,
                             "tail": " Test Files  2 passed (2)" if ok
                             else " Test Files  1 failed (2)"}]}
        st.data.setdefault("settled_by_execution", {})[sid] = rec
        return rec
    monkeypatch.setattr(env["leerie"], "_settle_by_execution", _ex)
    return ran


CANNOT_VERIFY = {
    "satisfied": False,
    "evidence": "None of the 12 files contain `payload.BaseUrl`, and each "
                "has `context.baseUrl` references, so the migration looks "
                "done. I could not confirm \"migrated files pass\". When "
                "run, 11 of the 12 files fail with ENOENT/read-only on "
                "`mkdir tmp/recon-generate/src/sites/...`.",
    "checked": list(FILES),
    "unsatisfied_reason": "cannot_verify",
    "equivalent_coverage_exists": False,
    "sibling_invalidation_risk": True,
    # It could not run the tests (the seed and the one criterion), and the
    # one passing command runs every file both name: the two listed.
    "unverifiable": ["seed", "criterion-1"],
    "covered_by_execution": ["seed", "criterion-1"]}


def _probe(env, monkeypatch, verdict):
    seen = []

    async def _cp(*, user_prompt, sid, cwd=None, **kw):
        seen.append({"prompt": user_prompt, "sid": sid})
        return dict(verdict)
    monkeypatch.setattr(env["leerie"], "claude_p", _cp)
    return seen


def _payload(prompt: str) -> dict:
    return json.loads(prompt.split("SUBTASK:\n", 1)[1]
                      .split("\n\nReturn only", 1)[0])


def _settle(env, note=None):
    c = env["leerie"]
    return asyncio.run(c._settle_subtask(
        SID, env["run_dir"], env["caps"], env["st"], env["models"],
        env["efforts"], redrive_note=note))


def test_live_sequence_settles_complete_and_its_dependent_runs(
        live, monkeypatch):
    env = live
    c = env["leerie"]
    _spec(env)
    calls = _implementer(env, monkeypatch)
    ran = _execution(env, monkeypatch, "pass")
    seen = _probe(env, monkeypatch, CANNOT_VERIFY)

    res = _settle(env)

    assert res["status"] == "complete", res
    assert len(calls) == 1, "settled on the first claim, no re-drive"
    assert ran == [str(env["worktree"])]
    # The probe is told what leerie's execution decided, so it need not
    # re-run it in its read-only tree.
    assert len(seen) == 1
    payload = _payload(seen[0]["prompt"])
    assert payload["decided_by_execution"] == [
        {"id": "criterion-1", "criterion": "migrated files pass",
         "argv": ARGV, "exit": 0}]
    assert payload["criteria"] == [{"id": "criterion-1",
                                    "criterion": "migrated files pass"}]
    assert "Do not run it again" in seen[0]["prompt"]
    st = env["st"].data
    assert st["subtask_status"][SID] == "complete"
    drop = st["dropped_subtasks"][SID]
    assert drop["reason"] == "satisfied_by_sibling"
    assert drop["decided_by"] == "execution"
    assert [d["sid"] for d in drop["satisfied_by"]] == [PRED]
    assert drop["executed"] == [" ".join(ARGV)]
    assert SID not in (st.get("unconverged_subtasks") or {})
    # Nothing it depends on is unfinished, so the dependent is not settled
    # `unconverged` without running.
    assert c._unconverged_predecessors(env["run_dir"], env["st"], DEP) == []


@pytest.mark.parametrize("why, verdict, verify", [
    ("a gap the probe found",
     {**CANNOT_VERIFY, "unsatisfied_reason": "partially_met"}, None),
    ("an untyped verdict", {k: v for k, v in CANNOT_VERIFY.items()
                            if k != "unsatisfied_reason"}, None),
    ("a criterion no command decides", CANNOT_VERIFY,
     [{"criterion": "migrated files pass", "argv": ARGV},
      {"criterion": "assertion intent preserved", "argv": []}]),
    ("a verdict that names nothing it could not check",
     {k: v for k, v in CANNOT_VERIFY.items()
      if k not in ("unverifiable", "covered_by_execution")}, None),
    ("the seed, which no passing command covers in full",
     {**CANNOT_VERIFY, "covered_by_execution": ["criterion-1"]}, None),
    ("an id leerie does not know",
     {**CANNOT_VERIFY, "unverifiable": ["criterion-2"],
      "covered_by_execution": ["criterion-2"]}, None),
], ids=["partially_met", "untyped", "uncommanded_criterion", "no_ids",
        "seed_uncovered", "unknown_id"])
def test_cannot_verify_counts_only_when_execution_decided_everything(
        live, monkeypatch, why, verdict, verify):
    env = live
    _spec(env, **({"verify": verify} if verify else {}))
    _implementer(env, monkeypatch)
    _execution(env, monkeypatch, "pass")
    _probe(env, monkeypatch, verdict)

    res = _settle(env)

    assert res["status"] == "blocked", why
    assert "satisfied-probe did not find" in res["blocker"]
    assert SID not in (env["st"].data.get("dropped_subtasks") or {})


def test_control_failing_verify_redrives_and_ends_unconverged(
        live, monkeypatch):
    """`nothing_to_change` with a failing verify is re-driven, the probe is
    never asked, and once the shared budget is spent it settles
    `unconverged` and holds its dependent back."""
    env = live
    c = env["leerie"]
    _spec(env)
    calls = _implementer(env, monkeypatch)
    _execution(env, monkeypatch, "fail")
    seen = _probe(env, monkeypatch, {**CANNOT_VERIFY, "satisfied": True})

    first = _settle(env)
    assert first["status"] == "blocked"
    assert "settle by execution" in first["blocker"]
    assert seen == []

    st = env["st"]
    for _ in range(env["caps"]["subtask_redrive_rounds"]):
        assert c._redrive_allowed(st, env["caps"], SID)
        n = c._consume_redrive(st, SID)
        again = _settle(env, note=f"RE-DRIVE {n}: {first['blocker']}")
        assert again["status"] == "blocked"
    assert not c._redrive_allowed(st, env["caps"], SID)
    assert len(calls) == 1 + env["caps"]["subtask_redrive_rounds"]

    c._settle_unconverged(st, SID, first["blocker"],
                          previous_status="blocked")
    assert st.data["subtask_status"][SID] == "unconverged"
    assert c._unconverged_predecessors(env["run_dir"], st, DEP) == [SID]


def test_decided_by_execution_needs_a_pass_on_every_criterion(leerie):
    sub = {"verify": [{"criterion": "a", "argv": ["x", "1"]},
                      {"criterion": "b", "argv": ["x", "2"]}]}
    passed = {"verdict": "pass", "commands": [
        {"argv": ["x", "1"], "passed": True},
        {"argv": ["x", "2"], "passed": True}]}
    assert leerie._execution_decides_criteria(sub, passed)
    assert not leerie._execution_decides_criteria(
        sub, {**passed, "verdict": "fail"})
    assert not leerie._execution_decides_criteria(sub, None)
    one = {"verdict": "pass", "commands": [{"argv": ["x", "1"],
                                            "passed": True}]}
    assert not leerie._execution_decides_criteria(sub, one)
    assert not leerie._execution_decides_criteria({"verify": []}, passed)
    assert leerie._criteria_decided_by_execution(sub, one) == [
        {"id": "criterion-1", "criterion": "a", "argv": ["x", "1"],
         "exit": 0}]


def test_execution_answers_only_what_its_commands_cover_in_full(leerie):
    sub = {"verify": [{"criterion": "a", "argv": ["x", "1"]},
                      {"criterion": "b", "argv": ["x", "2"]}]}
    passed = {"verdict": "pass", "commands": [
        {"argv": ["x", "1"], "passed": True},
        {"argv": ["x", "2"], "passed": True}]}
    base = {"satisfied": False, "unsatisfied_reason": "cannot_verify"}

    def ok(unv, cov, ex=passed):
        return leerie._execution_answers_cannot_verify(
            sub, ex, {**base, "unverifiable": unv,
                      "covered_by_execution": cov})
    assert ok(["criterion-1", "criterion-2"], ["criterion-1", "criterion-2"])
    assert ok(["seed"], ["seed", "criterion-2"])
    assert not ok(["seed", "criterion-1"], ["criterion-1"])
    assert not ok([], ["criterion-1"])
    assert not ok(["criterion-3"], ["criterion-3"])
    assert not ok(["criterion-1"], ["criterion-1"],
                  ex={**passed, "verdict": "fail"})
    assert not leerie._execution_answers_cannot_verify(
        sub, passed, {**base, "unverifiable": ["criterion-1"]})


# --- review round 39, execution E1 ------------------------------------------

# The live plan untrimmed: the seed names twelve files, the one verify argv
# runs two of them, and the sibling left the tenth it migrated broken.
ALL12 = [f"src/scripts/f{i:02d}.test.ts" for i in range(10)] + list(FILES)
BROKEN = ALL12[0]


@pytest.fixture
def live12(leerie, tmp_path):
    repo = tmp_path / "repo"
    (repo / "src" / "scripts").mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    for f in ALL12:
        (repo / f).write_text("expect(payload.BaseUrl).toBe(origin)\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    run_branch = f"leerie/runs/{RUN_ID}"
    _git(repo, "checkout", "-q", "-b", run_branch)
    pred_branch = f"leerie/subtasks/{RUN_ID}/{PRED}"
    _git(repo, "checkout", "-q", "-b", pred_branch, run_branch)
    for f in ALL12:
        (repo / f).write_text(
            "expect(context.baseUrl.nope()).toBe(origin)\n" if f == BROKEN
            else "expect(context.baseUrl).toBe(origin)\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "origin from context.baseUrl")
    _git(repo, "checkout", "-q", run_branch)
    _git(repo, "merge", "--no-ff", "-q", "-m", f"leerie: integrate {PRED}",
         pred_branch)
    leerie_root = repo / ".leerie"
    run_dir = leerie_root / "runs" / RUN_ID
    for d in ("subtasks", "criteria", "logs", "worktrees"):
        (run_dir / d).mkdir(parents=True)
    worktree = leerie._subtask_worktree_path(run_dir, SID)
    _git(repo, "worktree", "add", "-q", "-b",
         f"leerie/subtasks/{RUN_ID}/{SID}", str(worktree), run_branch)
    st = leerie.State(leerie_root, RUN_ID)
    st.data = {"task": "x", "answers": {"source_of_truth": "codebase"},
               "subtask_status": {PRED: "complete"}}
    st.save()
    caps = dict(leerie.DEFAULT_CAPS)
    caps["failed_retries"] = 0
    (run_dir / "plan.json").write_text(json.dumps({"subtasks": {
        PRED: {"depends_on": []}, SID: {"depends_on": [PRED]},
        DEP: {"depends_on": [SID]}}}))
    return {"leerie": leerie, "run_dir": run_dir, "st": st,
            "worktree": worktree, "caps": caps,
            "models": {w: "sonnet" for w in leerie.WORKER_TYPES},
            "efforts": {w: None for w in leerie.WORKER_TYPES}}


def test_a_command_over_part_of_a_criterion_does_not_settle_it(
        live12, monkeypatch):
    """r39e `test_r39e_settle_uncovered_file`: the 2-file argv passes, the
    probe could not run tests and says no passing command covers the seed
    or the criterion in full. The claim re-drives, and the dependent is
    not run on the broken file."""
    env = live12
    c = env["leerie"]
    _spec(env, files_likely_touched=list(ALL12))
    _implementer(env, monkeypatch)
    _execution(env, monkeypatch, "pass")
    _probe(env, monkeypatch, {**CANNOT_VERIFY, "checked": list(ALL12),
                              "covered_by_execution": []})
    assert BROKEN not in ARGV

    res = _settle(env)

    assert res["status"] == "blocked", res
    assert "satisfied-probe did not find" in res["blocker"]
    st = env["st"].data
    assert SID not in (st.get("dropped_subtasks") or {})
    assert st["subtask_status"].get(SID) != "complete"
