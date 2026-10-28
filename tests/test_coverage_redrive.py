"""The coverage re-drive (DESIGN §9 *Changed lines no test executes are
re-driven*; plan row G, bench Q7/G v7 + v8).

Driven for real: a git repo, a subtask worktree on its own branch, and a
stand-in `node_modules/.bin/vitest` — a small executable that speaks the
two interfaces leerie reads (vitest's JSON reporter and the v8
`coverage-final.json`), so the parsing, the pass-only counting, the
importing-test settle, the threshold, the re-drive note and the rollback
all run unmodified. Only the broker cgroup is stubbed (commands run
uncontained). In the stand-in, a production line containing `STMT` is a
statement, a test executes `<file>:<line>` for each `covers <file>:<line>`
comment it carries, and a test containing `FAIL` fails.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from tests.conftest import _run

FAKE_VITEST = r'''#!/usr/bin/env python3
import json, os, pathlib, re, sys
args = sys.argv[1:]
if args and args[0] == "run":
    args = args[1:]
out = cov = None
inc, tests, covon = [], [], False
for a in args:
    if a.startswith("--outputFile="):
        out = a.split("=", 1)[1]
    elif a.startswith("--coverage.reportsDirectory="):
        cov = a.split("=", 1)[1]
    elif a.startswith("--coverage.include="):
        inc.append(a.split("=", 1)[1])
    elif a == "--coverage.enabled=true":
        covon = True
    elif not a.startswith("-"):
        tests.append(a)
log = pathlib.Path(os.environ.get("FAKE_VITEST_LOG", "/dev/null"))
with open(log, "a") as fh:
    fh.write(json.dumps(tests) + "\n")
hits = {}
for f in inc:
    p = pathlib.Path(f)
    if p.is_file():
        hits[f] = {i + 1: 0 for i, l in enumerate(p.read_text().splitlines())
                   if "STMT" in l}
results, ok_all = [], True
for t in tests:
    p = pathlib.Path(t)
    text = p.read_text() if p.is_file() else ""
    ok = p.is_file() and "FAIL" not in text
    ok_all = ok_all and ok
    results.append({"name": os.path.abspath(t),
                    "status": "passed" if ok else "failed"})
    if ok:
        for f, ln in re.findall(r"covers (\S+):(\d+)", text):
            if f in hits and int(ln) in hits[f]:
                hits[f][int(ln)] += 1
if out:
    pathlib.Path(out).write_text(json.dumps({"testResults": results}))
if covon and cov:
    os.makedirs(cov, exist_ok=True)
    final = {}
    for f, lh in hits.items():
        ids = sorted(lh)
        final[os.path.abspath(f)] = {
            "statementMap": {str(i): {"start": {"line": ln}}
                             for i, ln in enumerate(ids)},
            "s": {str(i): lh[ln] for i, ln in enumerate(ids)}}
    pathlib.Path(cov, "coverage-final.json").write_text(json.dumps(final))
sys.exit(0 if ok_all else 1)
'''

A_BASE = "export function f(x) {\n  return x; // STMT\n}\n"
A_NEW = ("export function f(x) {\n  return x; // STMT\n}\n"
         "export function g(x) {\n  if (x) { // STMT\n    return 1; // STMT\n"
         "  }\n  return 2; // STMT\n}\n")
# Changed executable lines of A_NEW: 5, 6, 8.


def _git(cwd, *a):
    return subprocess.run(["git", *a], cwd=str(cwd), capture_output=True,
                          text=True, check=True).stdout.strip()


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)


def _install_fake(tree: Path) -> None:
    exe = tree / "node_modules" / ".bin" / "vitest"
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_text(FAKE_VITEST)
    exe.chmod(0o755)
    (tree / "node_modules" / "@vitest" / "coverage-v8").mkdir(parents=True)


@pytest.fixture
def env(leerie, tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    _write(repo, {"src/a.ts": A_BASE, ".gitignore": ".leerie/\nnode_modules/\n",
                  "src/old.test.ts": 'import { f } from "./a";\n'})
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    run_id = "cov-001-abcdef"
    leerie_root = repo / ".leerie"
    run_dir = leerie_root / "runs" / run_id
    (run_dir / "logs").mkdir(parents=True)
    _git(repo, "branch", f"leerie/runs/{run_id}", "main")
    tree = run_dir / "worktrees" / "feat-001"
    _git(repo, "worktree", "add", "-q", "-b",
         f"leerie/subtasks/{run_id}/feat-001", str(tree),
         f"leerie/runs/{run_id}")
    _install_fake(tree)
    st = leerie.State(leerie_root, run_id)
    st.data = {"task": "x", "subtask_status": {"feat-001": "complete"}}
    st.save()
    caps = dict(leerie.DEFAULT_CAPS)
    caps["worker_timeout_sec"] = 60
    monkeypatch.setattr(leerie, "_cgroup_create", lambda *a, **k: None)
    monkeypatch.setenv("FAKE_VITEST_LOG", str(tmp_path / "vitest.log"))
    return {"leerie": leerie, "repo": repo, "run_dir": run_dir,
            "tree": tree, "st": st, "caps": caps,
            "base": f"leerie/runs/{run_id}", "log": tmp_path / "vitest.log"}


def _commit(tree: Path, files: dict[str, str], msg="work") -> None:
    _write(tree, files)
    _git(tree, "add", "-A")
    _git(tree, "commit", "-q", "-m", msg)


def _measure(env):
    lr = env["leerie"]
    return _run(lr._measure_subtask_coverage(
        "feat-001", env["tree"], env["base"], env["st"], env["caps"]))


def _runs(env) -> list[list[str]]:
    return [json.loads(x) for x in env["log"].read_text().splitlines()]


# ---- the detector -----------------------------------------------------------

def test_uncovered_changed_lines_and_share(leerie, env):
    _commit(env["tree"], {
        "src/a.ts": A_NEW,
        "src/a.test.ts": ('import { g } from "./a";\n'
                          "// covers src/a.ts:5\n"),
        # A helper module under a test directory: neither production code
        # nor a test, so it is never run.
        "src/tests/helper.ts": "export const h = 1; // STMT\n"})
    rec = _measure(env)
    assert rec["measured"] is True
    assert rec["prods"] == ["src/a.ts"]
    assert rec["own_tests"] == ["src/a.test.ts"]
    assert rec["exec_changed"] == {"src/a.ts": 3}
    assert rec["uncovered"] == {"src/a.ts": [6, 8]}
    assert rec["share"] == round(2 / 3, 4)
    assert all("src/tests/helper.ts" not in r for r in _runs(env))
    # The baseline file is never left in the worktree.
    assert not (env["tree"] / leerie._COV_BASELINE_TEST).exists()
    assert _git(env["tree"], "status", "--porcelain") == ""


def test_a_failing_own_test_counts_for_nothing(leerie, env):
    _commit(env["tree"], {
        "src/a.ts": A_NEW,
        "src/a.test.ts": ('import { g } from "./a";\nFAIL\n'
                          "// covers src/a.ts:5\n// covers src/a.ts:6\n"
                          "// covers src/a.ts:8\n")})
    rec = _measure(env)
    assert rec["own_failed_twice"] == ["src/a.test.ts"]
    assert rec["uncovered"] == {"src/a.ts": [5, 6, 8]}
    assert rec["share"] == 1.0
    # run once with coverage, re-run once without: failed twice.
    assert _runs(env).count(["src/a.test.ts"]) == 2


def test_importing_test_settles_lines_but_a_mocking_one_does_not(leerie,
                                                                 env):
    # Tracked tests that predate the subtask (on the run branch): one
    # imports and executes line 6; one mocks the module away (and would
    # claim line 8).
    _commit(env["tree"], {
        "src/old.test.ts": ('import { f } from "./a";\n'
                            "// covers src/a.ts:6\n"),
        "src/mocked.test.ts": ('import { g } from "./a";\n'
                               'vi.mock("./a");\n// covers src/a.ts:8\n')},
        msg="pre-existing tests")
    _git(env["repo"], "branch", "-f", env["base"],
         _git(env["tree"], "rev-parse", "HEAD"))
    _commit(env["tree"], {
        "src/a.ts": A_NEW,
        "src/a.test.ts": 'import { g } from "./a";\n'})
    rec = _measure(env)
    assert rec["own_tests"] == ["src/a.test.ts"]
    assert rec["uncovered"] == {"src/a.ts": [5, 8]}
    assert rec["settled"] == {"src/a.ts": ["src/old.test.ts"]}
    assert all("src/mocked.test.ts" not in r for r in _runs(env))


def test_no_vitest_is_unmeasured(leerie, env):
    import shutil
    shutil.rmtree(env["tree"] / "node_modules" / "@vitest")
    _commit(env["tree"], {"src/a.ts": A_NEW})
    rec = _measure(env)
    assert rec["measured"] is False
    assert "vitest" in rec["why"]


def test_changed_line_numbers_read_the_hunk_headers(leerie, env):
    _commit(env["tree"], {"src/a.ts": A_NEW})
    got = _run(leerie._changed_line_numbers(env["tree"], env["base"],
                                            "src/a.ts"))
    assert got == [4, 5, 6, 7, 8, 9]


def test_test_case_file_excludes_helpers(leerie):
    assert leerie._is_test_case_file("src/a.test.ts")
    assert leerie._is_test_case_file("tests/test_x.py")
    assert not leerie._is_test_case_file("src/tests/page-inventory.ts")
    assert leerie._is_test_file("src/tests/page-inventory.ts")
    assert leerie._is_test_case_file("x/y.ts", ["x/*.ts"])


# ---- the re-drive loop ------------------------------------------------------

def _redrive(env, settle):
    lr = env["leerie"]
    results = {"feat-001": {"subtask_id": "feat-001", "status": "complete",
                            "summary": "done"}}
    _run(lr._redrive_uncovered_subtasks(results, settle, env["st"],
                                        env["caps"], env["run_dir"]))
    return results, env["st"].data["coverage_redrive"]["feat-001"]


def test_redrive_closes_the_gap_and_round_two_says_why(leerie, env):
    _commit(env["tree"], {"src/a.ts": A_NEW,
                          "src/a.test.ts": 'import { g } from "./a";\n'})
    notes: list[str] = []

    async def settle(sid, note):
        notes.append(note)
        if len(notes) == 1:      # round 1 covers one line of three
            _commit(env["tree"], {"src/g.test.ts": (
                'import { g } from "./a";\n// covers src/a.ts:5\n')})
        else:                    # round 2 covers the rest
            _commit(env["tree"], {"src/g2.test.ts": (
                'import { g } from "./a";\n// covers src/a.ts:6\n'
                "// covers src/a.ts:8\n")})
        return {"subtask_id": sid, "status": "complete", "summary": "tests"}

    results, rec = _redrive(env, settle)
    assert len(notes) == 2
    assert "src/a.ts:5, src/a.ts:6, src/a.ts:8" in notes[0]
    assert "NOT ADDRESSED" not in notes[0]
    assert "round 2 of at most 2" in notes[1]
    assert ("NOT ADDRESSED by round 1: lines it was asked to cover are "
            "still executed by no test: src/a.ts:6, src/a.ts:8") in notes[1]
    assert rec["final"] == "covered"
    assert [r["round"] for r in rec["rounds"]] == [1, 2]
    assert env["st"].data["redrive_rounds"]["feat-001"] == 2
    assert results["feat-001"]["summary"] == "tests"


def test_spent_budget_lists_untested_and_never_unconverges(leerie, env):
    _commit(env["tree"], {"src/a.ts": A_NEW})
    calls = []

    async def settle(sid, note):
        calls.append(note)
        return {"subtask_id": sid, "status": "complete", "summary": "noop"}

    results, rec = _redrive(env, settle)
    assert len(calls) == 2           # the shared counter's two rounds
    assert rec["final"] == "untested"
    assert rec["uncovered"] == {"src/a.ts": [5, 6, 8]}
    st = env["st"]
    assert st.data["subtask_status"]["feat-001"] == "complete"
    assert "feat-001" not in (st.data.get("unconverged_subtasks") or {})
    sec = leerie._format_pr_end_state_section(st.data)
    assert "- **feat-001**: `src/a.ts` lines 5, 6, 8" in sec


def test_below_threshold_is_not_redriven(leerie, env):
    env["caps"]["coverage_uncovered_share"] = 0.7    # 2/3 unexecuted < 0.7
    _commit(env["tree"], {"src/a.ts": A_NEW, "src/a.test.ts": (
        'import { g } from "./a";\n// covers src/a.ts:5\n')})

    async def settle(sid, note):
        raise AssertionError("must not re-drive below the threshold")

    _results, rec = _redrive(env, settle)
    assert rec["final"] == "covered" and rec["rounds"] == []


def test_a_redrive_that_does_not_complete_is_rolled_back(leerie, env):
    _commit(env["tree"], {"src/a.ts": A_NEW})
    done_at = _git(env["tree"], "rev-parse", "HEAD")

    async def settle(sid, note):
        _commit(env["tree"], {"src/a.ts": "broken\n"})
        env["st"].data["subtask_status"][sid] = "blocked"
        env["st"].data["blocked"] = {sid: {"reason": "x"}}
        return {"subtask_id": sid, "status": "blocked", "blocker": "gave up"}

    results, rec = _redrive(env, settle)
    assert _git(env["tree"], "rev-parse", "HEAD") == done_at
    assert results["feat-001"]["status"] == "complete"
    assert env["st"].data["subtask_status"]["feat-001"] == "complete"
    assert "blocked" not in env["st"].data
    assert rec["final"] == "rolled_back"
    assert rec["rolled_back"] == {"status": "blocked", "reason": "gave up"}


def test_settled_record_is_skipped_on_resume(leerie, env):
    env["st"].data["coverage_redrive"] = {"feat-001": {"final": "covered"}}
    _commit(env["tree"], {"src/a.ts": A_NEW})

    async def settle(sid, note):
        raise AssertionError("resume must not re-measure a settled sid")

    _redrive(env, settle)
    assert not env["log"].exists()


# ---- typed gaps ---------------------------------------------------------------

def _gap(gap, paths, settle_by="test"):
    return {"kind": "untested_change", "detail": f"{gap} on {paths}",
            "source": "implementer", "gap": gap, "where_paths": paths,
            "settle_by": settle_by, "addressed": False}


def test_test_missing_settles_on_a_passing_importing_test(leerie, env):
    _commit(env["tree"], {"src/a.ts": A_NEW, "src/a.test.ts": (
        'import { g } from "./a";\n// covers src/a.ts:5\n'
        "// covers src/a.ts:6\n// covers src/a.ts:8\n")})
    env["st"].data["risk_register"] = {"feat-001": [
        _gap("test_missing", ["src/a.ts"]),
        _gap("test_weak", ["src/a.ts"]),
        _gap("test_missing", [])]}   # no verified path: triggers nothing
    gaps = _run(leerie._settle_typed_gaps(
        "feat-001", env["tree"], env["st"], env["caps"],
        redrive_changed=set()))
    assert [(g["gap"], g["settled"]) for g in gaps] == [
        ("test_missing", True), ("test_weak", False)]
    assert gaps[0]["how"] == "src/a.test.ts imports it and passes"


def test_an_open_typed_gap_redrives_even_when_lines_are_covered(leerie,
                                                                env):
    _commit(env["tree"], {"src/a.ts": A_NEW, "src/a.test.ts": (
        'import { g } from "./a";\n// covers src/a.ts:5\n'
        "// covers src/a.ts:6\n// covers src/a.ts:8\n")})
    env["st"].data["risk_register"] = {"feat-001": [
        _gap("test_weak", ["src/a.ts"])]}
    notes = []

    async def settle(sid, note):
        notes.append(note)
        _commit(env["tree"], {"src/a.test.ts": (
            'import { g } from "./a";\n// covers src/a.ts:5\n'
            "// covers src/a.ts:6\n// covers src/a.ts:8\n// stronger\n")})
        return {"subtask_id": sid, "status": "complete", "summary": "s"}

    _results, rec = _redrive(env, settle)
    assert len(notes) == 1
    assert "- [test_weak] test_weak on ['src/a.ts'] (where: src/a.ts)" \
        in notes[0]
    # The re-drive changed a test importing the file, which passes.
    assert rec["final"] == "covered"


def test_typecheck_gap_needs_the_file_in_tscs_own_list(leerie, env):
    tree = env["tree"]
    (tree / "tsconfig.json").write_text("{}")
    tsc = tree / "node_modules" / ".bin" / "tsc"
    tsc.write_text("#!/bin/sh\necho \"$PWD/src/a.ts\"\nexit 0\n")
    tsc.chmod(0o755)
    _commit(tree, {"src/a.ts": A_NEW, "src/b.ts": "x\n"})
    lr = env["leerie"]
    assert _run(lr._tsc_lists_paths(tree, ["src/a.ts"], st=env["st"],
                                    caps=env["caps"], sid="feat-001"))
    # Passing, but src/b.ts is outside the axis's own file list.
    assert not _run(lr._tsc_lists_paths(tree, ["src/a.ts", "src/b.ts"],
                                        st=env["st"], caps=env["caps"],
                                        sid="feat-001"))
    tsc.write_text("#!/bin/sh\necho \"$PWD/src/a.ts\"\nexit 2\n")
    assert not _run(lr._tsc_lists_paths(tree, ["src/a.ts"], st=env["st"],
                                        caps=env["caps"], sid="feat-001"))


def test_coverage_cap_is_registered(leerie):
    assert leerie.DEFAULT_CAPS["coverage_uncovered_share"] == 0.25
    assert "coverage_redrive" in leerie.STATE_FIELDS
