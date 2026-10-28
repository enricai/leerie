"""A no-op a sibling already delivered is settled on evidence (DESIGN §8
*A no-op a sibling already delivered is settled on evidence*, §13 routing
table).

The defect, reproduced from a live two-wave run: wave 1's `bugfix-001`
landed the slugify fix, and its conformer also wrote the regression test.
Wave 2's `bugfix-002`, planned to add that test, reported
`nothing_to_change`. It was re-driven twice, reported the same thing each
time, and the run ended `unconverged` with its whole deliverable on the
branch. These tests build that repo shape for real: the sibling's
`leerie: integrate bugfix-001` merge on the run branch, with the wave-2
worktree cut from it. They drive the real `_settle_subtask` with only the
implementer, the verify execution and the probe's `claude_p` stubbed. The
claim settles `complete` only when all three pieces of evidence agree, and
the e33 shape is still re-driven: nothing landed on the surface, so the
probe and execution are never asked.
"""
from __future__ import annotations

import asyncio
import json
import subprocess

import pytest

RUN_ID = "synth-002-abcdef"
SID = "bugfix-002"
SIBLING = "bugfix-001"
TEST_FILE = "tests/test_textutil.py"


def _git(cwd, *args):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                       text=True)
    assert r.returncode == 0, f"git {args} failed: {r.stderr}"
    return r.stdout.strip()


def _commit_on_branch(repo, branch, base, files: dict[str, str], msg):
    _git(repo, "checkout", "-q", "-b", branch, base)
    for rel, text in files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", msg)


@pytest.fixture
def synth(leerie, tmp_path):
    """The synth run's repo after wave 1: `bugfix-001`'s fix and its
    conformer's regression test merged into the run branch as `leerie:
    integrate bugfix-001`; `bugfix-002`'s worktree cut from that tip."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "src").mkdir()
    (repo / "tests").mkdir()
    (repo / "src" / "textutil.py").write_text(
        'def slugify(t):\n    return t.lower().replace(" ", "-")\n')
    (repo / TEST_FILE).write_text("def test_slugify_basic():\n    pass\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    run_branch = f"leerie/runs/{RUN_ID}"
    _git(repo, "checkout", "-q", "-b", run_branch)
    sib_branch = f"leerie/subtasks/{RUN_ID}/{SIBLING}"
    _commit_on_branch(repo, sib_branch, run_branch, {
        "src/textutil.py": 'def slugify(t):\n    return "-".join('
                           "t.lower().split())\n"}, "fix slugify")
    (repo / TEST_FILE).write_text(
        "def test_slugify_basic():\n    pass\n\n"
        "def test_slugify_collapses_and_strips_whitespace():\n    pass\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "conformer: test slugify whitespace")
    _git(repo, "checkout", "-q", run_branch)
    _git(repo, "merge", "--no-ff", "-q", "-m", f"leerie: integrate {SIBLING}",
         sib_branch)
    merge = _git(repo, "rev-parse", "HEAD")

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
    return {"leerie": leerie, "repo": repo, "run_dir": run_dir, "st": st,
            "worktree": worktree, "caps": caps, "merge": merge,
            "models": {w: "sonnet" for w in leerie.WORKER_TYPES},
            "efforts": {w: None for w in leerie.WORKER_TYPES}}


def _spec(env, **fields):
    sub = {"id": SID, "title": "Add slugify regression test",
           "success_criteria_seed": "a regression test asserts "
                                    "slugify('  Hello   World ') == "
                                    "'hello-world' and passes",
           "files_likely_touched": [TEST_FILE],
           "verify": [{"criterion": "regression test passes",
                       "argv": ["python3", "-m", "pytest", TEST_FILE]}],
           "verification_only": False}
    sub.update(fields)
    (env["run_dir"] / "subtasks" / f"{SID}.json").write_text(json.dumps(sub))


def _implementer(env, monkeypatch, result):
    calls = []

    async def _stub(sid, leerie_dir, caps, st, models, efforts,
                    continuation=False, note=""):
        calls.append(note)
        return {"subtask_id": sid, **result}
    monkeypatch.setattr(env["leerie"], "_run_implementer", _stub)
    return calls


NOTHING = {"status": "blocked", "blocker_kind": "nothing_to_change",
           "blocker": "the regression test is already there",
           "summary": "nothing to change"}
COMPLETE_NO_COMMITS = {"status": "complete", "summary": "already done",
                       "criteria_results": [],
                       "production_evidence": {"exercised": True,
                                               "how": "n/a",
                                               "observed": "n/a"}}


def _execution(env, monkeypatch, verdict):
    ran = []

    async def _ex(subtask, worktree, st, caps, *, sid):
        ran.append(str(worktree))
        return {"verdict": verdict, "why": f"exec {verdict}", "dirty": [],
                "commands": [{"argv": ["python3", "-m", "pytest", TEST_FILE],
                              "tail": "1 failed", "rc": 0
                              if verdict == "pass" else 1}]}
    monkeypatch.setattr(env["leerie"], "_settle_by_execution", _ex)
    return ran


def _probe(env, monkeypatch, satisfied):
    seen = []

    async def _cp(*, user_prompt, sid, cwd=None, **kw):
        seen.append({"prompt": user_prompt, "sid": sid, "cwd": cwd,
                     "tools": kw.get("allowed_tools"),
                     "schema": kw.get("schema_key")})
        return {"satisfied": satisfied,
                "evidence": "tests/test_textutil.py has "
                            "test_slugify_collapses_and_strips_whitespace",
                "checked": [TEST_FILE]}
    monkeypatch.setattr(env["leerie"], "claude_p", _cp)
    return seen


def _settle(env):
    c = env["leerie"]
    return asyncio.run(c._settle_subtask(
        SID, env["run_dir"], env["caps"], env["st"], env["models"],
        env["efforts"]))


@pytest.mark.parametrize("claim", [NOTHING, COMPLETE_NO_COMMITS],
                         ids=["nothing_to_change", "complete_no_commits"])
def test_sibling_delivered_noop_settles_complete_in_one_pass(
        synth, monkeypatch, claim):
    env = synth
    _spec(env)
    calls = _implementer(env, monkeypatch, claim)
    ran = _execution(env, monkeypatch, "pass")
    seen = _probe(env, monkeypatch, True)

    res = _settle(env)

    assert res["status"] == "complete"
    assert len(calls) == 1, "settled on the first claim, no re-drive"
    assert ran == [str(env["worktree"])], "verify ran on the subtask's HEAD"
    assert len(seen) == 1 and seen[0]["cwd"] == str(env["worktree"])
    assert seen[0]["schema"] == "satisfied_probe"
    assert seen[0]["tools"] == env["leerie"].SATISFIED_PROBE_TOOLS
    # The probe judges the subtask's own criteria, not only the seed.
    payload = json.loads(seen[0]["prompt"].split("SUBTASK:\n", 1)[1]
                         .split("\n\nReturn only", 1)[0])
    assert payload["criteria"] == ["regression test passes"]
    st = env["st"].data
    assert st["subtask_status"][SID] == "complete"
    drop = st["dropped_subtasks"][SID]
    assert drop["reason"] == "satisfied_by_sibling"
    assert drop["satisfied_by"] == [{"sid": SIBLING, "commit": env["merge"],
                                     "files": [TEST_FILE]}]
    assert drop["executed"] == [f"python3 -m pytest {TEST_FILE}"]
    assert "conformance" in st and SID in st["conformance"]
    assert SID not in (st.get("blocked") or {})


def test_without_verify_argvs_the_probe_decides_with_the_precondition(
        synth, monkeypatch):
    env = synth
    _spec(env, verify=[{"criterion": "regression test exists", "argv": []}])
    _implementer(env, monkeypatch, NOTHING)
    ran = _execution(env, monkeypatch, "pass")
    seen = _probe(env, monkeypatch, True)

    res = _settle(env)

    assert res["status"] == "complete"
    assert ran == [], "nothing to execute"
    assert len(seen) == 1
    drop = env["st"].data["dropped_subtasks"][SID]
    assert drop["reason"] == "satisfied_by_sibling" and "executed" not in drop


def test_e33_shape_nothing_landed_on_its_surface_is_redriven(
        leerie, tmp_path, monkeypatch):
    """Wave 1, nothing integrated yet: the claim cannot be a sibling's
    delivery, so neither execution nor the probe is asked."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "tests").mkdir()
    (repo / TEST_FILE).write_text("def test_slugify_basic():\n    pass\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    _git(repo, "checkout", "-q", "-b", f"leerie/runs/{RUN_ID}")
    leerie_root = repo / ".leerie"
    run_dir = leerie_root / "runs" / RUN_ID
    for d in ("subtasks", "criteria", "logs", "worktrees"):
        (run_dir / d).mkdir(parents=True)
    wt = leerie._subtask_worktree_path(run_dir, SID)
    _git(repo, "worktree", "add", "-q", "-b",
         f"leerie/subtasks/{RUN_ID}/{SID}", str(wt), f"leerie/runs/{RUN_ID}")
    st = leerie.State(leerie_root, RUN_ID)
    st.data = {"task": "x"}
    st.save()
    env = {"leerie": leerie, "run_dir": run_dir, "st": st, "worktree": wt,
           "caps": {**leerie.DEFAULT_CAPS, "failed_retries": 0},
           "models": {w: "sonnet" for w in leerie.WORKER_TYPES},
           "efforts": {w: None for w in leerie.WORKER_TYPES}}
    _spec(env)
    _implementer(env, monkeypatch, NOTHING)
    ran = _execution(env, monkeypatch, "pass")
    seen = _probe(env, monkeypatch, True)

    res = _settle(env)

    assert res["status"] == "blocked"
    assert ran == [] and seen == []
    assert "no sibling integrated this run" in res["blocker"]
    assert st.data["subtask_status"][SID] == "blocked"


def test_a_sibling_on_another_surface_is_not_evidence(synth, monkeypatch):
    env = synth
    _spec(env, files_likely_touched=["docs/usage.md"])
    _implementer(env, monkeypatch, NOTHING)
    ran = _execution(env, monkeypatch, "pass")
    seen = _probe(env, monkeypatch, True)

    res = _settle(env)

    assert res["status"] == "blocked"
    assert ran == [] and seen == []


def test_failed_execution_redrives_and_skips_the_probe(synth, monkeypatch):
    env = synth
    _spec(env)
    _implementer(env, monkeypatch, NOTHING)
    _execution(env, monkeypatch, "fail")
    seen = _probe(env, monkeypatch, True)

    res = _settle(env)

    assert res["status"] == "blocked"
    assert seen == []
    assert "settle by execution: exec fail" in res["blocker"]
    assert "untrusted data" in res["blocker"]
    assert SID not in (env["st"].data.get("dropped_subtasks") or {})


def test_probe_not_satisfied_redrives(synth, monkeypatch):
    """Execution passing is not enough: a test file's existing tests pass
    whether or not the new case was written (bench E4/d4 `e33`)."""
    env = synth
    _spec(env)
    _implementer(env, monkeypatch, COMPLETE_NO_COMMITS)
    _execution(env, monkeypatch, "pass")
    _probe(env, monkeypatch, False)

    res = _settle(env)

    assert res["status"] == "blocked"
    assert "satisfied-probe did not find" in res["blocker"]
    assert env["st"].data["subtask_status"][SID] == "blocked"


def test_execution_killed_at_the_ceiling_settles_unconverged(
        synth, monkeypatch):
    env = synth
    _spec(env)
    _implementer(env, monkeypatch, NOTHING)
    _execution(env, monkeypatch, "environment_limit")
    seen = _probe(env, monkeypatch, True)

    res = _settle(env)

    assert res["status"] == "unconverged"
    assert seen == []
    assert env["st"].data["unconverged_subtasks"][SID]["kind"] == \
        "environment_limit"


def test_sibling_deliveries_reads_only_integration_merges(synth):
    env = synth
    c = env["leerie"]
    wt = str(env["worktree"])
    got = asyncio.run(c._sibling_deliveries(wt, SID, [TEST_FILE]))
    assert got == [{"sid": SIBLING, "commit": env["merge"],
                    "files": [TEST_FILE]}]
    # Its own integration is never a sibling's delivery.
    assert asyncio.run(c._sibling_deliveries(wt, SIBLING, [TEST_FILE])) == []
    # A planned directory covers the files under it; `./` is a no-op.
    dirs = asyncio.run(c._sibling_deliveries(wt, SID, ["./tests/"]))
    assert [d["files"] for d in dirs] == [[TEST_FILE]]
    assert asyncio.run(c._sibling_deliveries(wt, SID, [])) == []


@pytest.mark.parametrize("path, planned, hit", [
    ("tests/a.py", ["tests/a.py"], True),
    ("tests/a.py", ["tests"], True),
    ("tests/a.py", ["./tests/a.py"], True),
    ("tests_old/a.py", ["tests"], False),
    (".github/ci.yml", [".github/ci.yml"], True),
    ("src/a.py", ["tests/a.py"], False),
])
def test_on_planned_surface(leerie, path, planned, hit):
    assert leerie._on_planned_surface(path, planned) is hit
