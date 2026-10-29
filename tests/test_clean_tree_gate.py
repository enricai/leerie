"""I1: a worker leaves its tree clean (DESIGN §8 *A worker leaves its tree
clean*).

The checker (`_tree_dirt`) is exact — `git status --porcelain
--untracked-files=all`, so an ignored file never makes a tree dirty — and
the gate (`_clean_tree_gate`) re-asks the SAME worker (`_clean_tree_reasker`:
its CLI session resumed) with the porcelain lines, at most
`clean_tree_reasks` times, never committing or deleting anything itself.
Exhausted, the caller rolls the round back and records the lines.

The staging sites are executed here: the delivery re-drive implementer, the
acceptance-repair conformer and the integrator. The subtask-worktree sites
are executed beside their own harnesses: the implementer in
tests/test_settle_subtask_branch_coverage.py, the per-subtask conformer in
tests/test_run_conformance_phase.py, the final conformer in
tests/test_run_final_conformance.py.
"""
from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

import pytest


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=str(cwd), check=True,
                          capture_output=True, text=True).stdout.strip()


def _repo(tmp_path: Path) -> Path:
    r = tmp_path / "r"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    _git(r, "config", "user.email", "t@x")
    _git(r, "config", "user.name", "t")
    (r / ".gitignore").write_text("node_modules/\n*.log\n")
    (r / "a.py").write_text("x = 1\n")
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "base")
    return r


# --- the checker -----------------------------------------------------------

def test_ignored_files_never_make_a_tree_dirty(leerie, tmp_path):
    r = _repo(tmp_path)
    (r / "node_modules" / "pkg").mkdir(parents=True)
    (r / "node_modules" / "pkg" / "index.js").write_text("x\n")
    (r / "debug.log").write_text("x\n")
    assert asyncio.run(leerie._tree_dirt(r)) == []


def test_the_checker_lists_every_kind_of_dirt_verbatim(leerie, tmp_path):
    """Untracked files are listed one per path, even under a new directory
    (`--untracked-files=all`, not `?? dir/`); a tracked edit and a staged
    file are listed too."""
    r = _repo(tmp_path)
    (r / "a.py").write_text("x = 2\n")
    (r / "new").mkdir()
    (r / "new" / "helpers.py").write_text("y = 1\n")
    (r / "staged.py").write_text("z = 1\n")
    _git(r, "add", "staged.py")
    assert asyncio.run(leerie._tree_dirt(r)) == [
        " M a.py", "A  staged.py", "?? new/helpers.py"]


def test_a_tree_git_cannot_read_is_not_clean(leerie, tmp_path):
    assert asyncio.run(leerie._tree_dirt(tmp_path)) is None


# --- the gate --------------------------------------------------------------

def _caps(leerie, n=2):
    return {**leerie.DEFAULT_CAPS, "clean_tree_reasks": n}


def test_a_clean_tree_is_never_reasked(leerie, tmp_path):
    r = _repo(tmp_path)
    asked: list[str] = []

    async def reask(note):
        asked.append(note)
    assert asyncio.run(leerie._clean_tree_gate(
        r, caps=_caps(leerie), label="t", reask=reask)) == []
    assert asked == []


def test_a_worker_that_commits_on_the_reask_passes(leerie, tmp_path):
    r = _repo(tmp_path)
    (r / "helpers.py").write_text("y = 1\n")
    asked: list[str] = []

    async def reask(note):
        asked.append(note)
        _git(r, "add", "helpers.py")
        _git(r, "commit", "-qm", "add helpers")
    assert asyncio.run(leerie._clean_tree_gate(
        r, caps=_caps(leerie), label="t", reask=reask)) == []
    assert len(asked) == 1
    # The note carries the exact porcelain line and only the two acts.
    assert "    ?? helpers.py" in asked[0]
    assert "commit it" in asked[0] and "delete it" in asked[0]
    assert _git(r, "ls-files", "helpers.py") == "helpers.py"


def test_exhaustion_returns_the_lines_and_touches_nothing(leerie, tmp_path):
    """Bounded at `clean_tree_reasks`; a re-ask that raises spends its
    attempt; the gate itself never commits or deletes."""
    r = _repo(tmp_path)
    (r / "helpers.py").write_text("y = 1\n")
    head = _git(r, "rev-parse", "HEAD")
    asked: list[str] = []

    async def reask(note):
        asked.append(note)
        if len(asked) == 1:
            raise leerie.WorkerError("session died")
    got = asyncio.run(leerie._clean_tree_gate(
        r, caps=_caps(leerie), label="t", reask=reask))
    assert got == ["?? helpers.py"]
    assert len(asked) == 2
    assert _git(r, "rev-parse", "HEAD") == head
    assert (r / "helpers.py").exists()


def test_the_bound_is_the_cap(leerie, tmp_path):
    r = _repo(tmp_path)
    (r / "helpers.py").write_text("y = 1\n")
    asked: list[str] = []

    async def reask(note):
        asked.append(note)
    asyncio.run(leerie._clean_tree_gate(
        r, caps=_caps(leerie, 0), label="t", reask=reask))
    assert asked == []
    assert leerie.DEFAULT_CAPS["clean_tree_reasks"] == 2


# --- the re-ask is the same worker -----------------------------------------

def test_the_reask_resumes_the_workers_own_session(leerie, tmp_path,
                                                   monkeypatch):
    st = leerie.State(tmp_path / "state", "run-c")
    st.data = {"worker_count": 0}
    logs = st.run_dir / "logs"
    logs.mkdir(parents=True)
    (logs / "w1.log").write_text(
        json.dumps({"type": "system", "session_id": "S-old"}) + "\n"
        + json.dumps({"type": "result", "session_id": "S-1"}) + "\n")
    calls: list[dict] = []

    async def fake_claude_p(**kw):
        calls.append(kw)
        (logs / f"{kw['sid']}.log").write_text(
            json.dumps({"session_id": f"S-{len(calls) + 1}"}) + "\n")
        return {}
    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    reask = leerie._clean_tree_reasker(
        st, _caps(leerie), log_sid="w1", cwd="/tree", schema_key="conformer",
        system_prompt="SYS", model="sonnet", effort="low", max_turns=60)
    asyncio.run(reask("note one"))
    asyncio.run(reask("note two"))
    assert [c["resume_session"] for c in calls] == ["S-1", "S-2"]
    assert [c["sid"] for c in calls] == ["w1-clean1", "w1-clean2"]
    for c in calls:
        assert (c["schema_key"], c["system_prompt"], c["cwd"], c["model"],
                c["effort"], c["max_turns"]) == (
            "conformer", "SYS", "/tree", "sonnet", "low", 60)
    assert [c["user_prompt"] for c in calls] == ["note one", "note two"]
    assert st.data["worker_count"] == 2


def test_no_session_means_no_reask_and_no_worker_spent(leerie, tmp_path,
                                                       monkeypatch):
    st = leerie.State(tmp_path / "state", "run-c")
    st.data = {"worker_count": 0}
    (st.run_dir / "logs").mkdir(parents=True)

    async def fake_claude_p(**kw):
        raise AssertionError("no session: nothing to resume")
    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    reask = leerie._clean_tree_reasker(
        st, _caps(leerie), log_sid="w1", cwd="/tree", schema_key="conformer",
        system_prompt="SYS", model="sonnet", effort=None, max_turns=60)
    with pytest.raises(leerie.WorkerError):
        asyncio.run(reask("note"))
    assert st.data["worker_count"] == 0


# --- the outcome -----------------------------------------------------------

def test_a_recorded_unclean_round_makes_the_run_unconverged(leerie,
                                                            tmp_path):
    st = leerie.State(tmp_path / "state", "run-c")
    st.data = {"subtask_status": {"s1": "complete"}}
    leerie._record_unclean_tree(st, "final conformer", "round 0",
                                ["?? helpers.py"])
    assert leerie._decide_outcome(st, any_commits=True) == "unconverged"
    (reason,) = st.data["outcome_reasons"]
    assert "final conformer" in reason and "?? helpers.py" in reason
    body = leerie._format_pr_end_state_section(st.data)
    assert "?? helpers.py" in body


# --- the delivery re-drive implementer --------------------------------------

def _world(leerie, tmp_path, monkeypatch):
    from tests import test_delivery_redrive as tdr
    w = tdr.World(leerie, tmp_path)
    w.install(monkeypatch)
    monkeypatch.setattr(leerie, "_log_session_id", lambda p: "sess-1")
    return w, tdr


def test_redrive_bash_made_file_is_committed_by_the_worker_on_reask(
        leerie, tmp_path, monkeypatch):
    """Review round 10's case: a module made through Bash (`mv`), the
    deletion and the edited import committed, the new file not. It used to
    be committed by leerie's leftovers commit; it is now committed by the
    implementer when re-asked, and ships."""
    w, tdr = _world(leerie, tmp_path, monkeypatch)

    def bash_mv(w):
        (w.staging / "src" / "util.ts").write_text("export const u = 1;\n")
        _git(w.staging, "add", "src/util.ts")
        _git(w.staging, "commit", "-qm", "util")
        (w.staging / "src" / "util.ts").rename(
            w.staging / "src" / "helpers.ts")
        _git(w.staging, "rm", "-q", "--cached", "src/util.ts")
        w.write("import { u } from './helpers';\n"
                "export const a = 1; // FIXED\n")

    def commits_it(w):
        _git(w.staging, "add", "src/helpers.ts")
        _git(w.staging, "commit", "-qm", "add helpers")
    w.edits = [bash_mv, commits_it]
    w.run()
    reask = [c for c in w.calls if c["sid"].endswith("-clean1")]
    assert len(reask) == 1
    assert "?? src/helpers.ts" in reask[0]["prompt"]
    ge = w.st.data["delivery_redrive"]["rounds"][0]["groups"][0]
    assert not ge.get("rolled_back") and "unclean_tree" not in ge
    assert _git(w.staging, "ls-files", "src/helpers.ts") == "src/helpers.ts"
    assert _git(w.staging, "status", "--porcelain",
                "--untracked-files=all") == ""
    assert "unclean_trees" not in w.st.data


def test_redrive_bash_made_file_left_uncommitted_ends_unconverged(
        leerie, tmp_path, monkeypatch):
    """The same case when the implementer never commits it: the group is
    rolled back (its fix with it — HEAD could not import what staging
    could), the lines are recorded, and the run is unconverged with them
    named. Never dropped silently, never committed by leerie."""
    w, tdr = _world(leerie, tmp_path, monkeypatch)
    start = _git(w.staging, "rev-parse", "HEAD")

    def bash_heredoc(w):
        (w.staging / "src" / "helpers.ts").write_text("export const h = 1;\n")
        w.write("import { h } from './helpers';\n"
                "export const a = 1; // FIXED\n")
    w.edits = [bash_heredoc]
    w.caps["subtask_redrive_rounds"] = 1
    w.run()
    reasks = [c for c in w.calls if "-clean" in c["sid"]]
    assert len(reasks) == 2
    ge = w.st.data["delivery_redrive"]["rounds"][0]["groups"][0]
    assert ge["unclean_tree"] == ["?? src/helpers.ts"]
    assert ge["rolled_back"] is True
    assert _git(w.staging, "rev-parse", "HEAD") == start
    assert not (w.staging / "src" / "helpers.ts").exists()
    rec = w.st.data["unclean_trees"]
    assert list(rec) == ["delivery re-drive implementer:r1 g0"]
    assert leerie._decide_outcome(w.st, any_commits=True) == "unconverged"
    assert any("src/helpers.ts" in r for r in w.st.data["outcome_reasons"])


# --- the acceptance-repair conformer ----------------------------------------

def _gate_env(leerie, tmp_path, monkeypatch):
    from tests import test_acceptance_gate as tag
    # The fixture's repository ignores `__pycache__/`: leerie's own test
    # runs in staging leave it until I2 (DESIGN §6½), and this tests the
    # worker's dirt.
    repo, head = tag._repo(tmp_path)
    assert "__pycache__/" in (repo / ".gitignore").read_text()
    st = tag._st(leerie, tmp_path, repo, head, working_branch="main")
    staging = tag._staging(st, repo)
    st.data["acceptance"] = {"sets": tag._make_sets(leerie, st, 5)}
    monkeypatch.setattr(leerie, "_log_session_id", lambda p: "sess-1")
    return tag, st, staging


def _run_repair(leerie, monkeypatch, tag, st, actions):
    calls: list[dict] = []

    async def fake_claude_p(**kw):
        if not kw["sid"].startswith("acceptance-repair-"):
            return {}         # the set audit, the ratchet's judges
        calls.append(kw)
        act = actions.pop(0) if actions else None
        if act:
            act(Path(kw["cwd"]))
        return {}
    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    asyncio.run(leerie._run_acceptance_gate(
        st.run_dir, st, tag._caps(leerie, 5), tag.MODELS, tag.EFFORTS))
    return calls


def test_acceptance_repair_reasked_commits_and_its_fix_is_kept(
        leerie, tmp_path, monkeypatch):
    tag, st, staging = _gate_env(leerie, tmp_path, monkeypatch)

    def fix_uncommitted(wt):
        (wt / "calc.py").write_text(tag.FIXED)

    def commit_it(wt):
        _git(wt, "commit", "-qam", "conformer: fix add")
    calls = _run_repair(leerie, monkeypatch, tag, st,
                        [fix_uncommitted, commit_it])
    assert len(calls) == 2
    assert calls[1]["resume_session"] == "sess-1"
    assert calls[1]["sid"] == "acceptance-repair-r1-clean1"
    assert " M calc.py" in calls[1]["user_prompt"]
    gate = st.data["acceptance"]["gate"]
    assert all(r["passed"] for r in gate["final"])
    assert "unclean_trees" not in st.data


def test_acceptance_repair_left_dirty_is_rolled_back_and_recorded(
        leerie, tmp_path, monkeypatch):
    tag, st, staging = _gate_env(leerie, tmp_path, monkeypatch)
    before = _git(staging, "rev-parse", "HEAD")

    def fix_and_scratch(wt):
        (wt / "calc.py").write_text(tag.FIXED)
        _git(wt, "commit", "-qam", "conformer: fix add")
        (wt / "scratch.py").write_text("print(1)\n")
    calls = _run_repair(leerie, monkeypatch, tag, st, [fix_and_scratch])
    assert len(calls) == 3                       # the worker + 2 re-asks
    gate = st.data["acceptance"]["gate"]
    (rnd,) = gate["rounds"]
    assert rnd["unclean_tree"] == ["?? scratch.py"]
    assert rnd["rolled_back"] is True
    assert _git(staging, "rev-parse", "HEAD") == before
    assert not (staging / "scratch.py").exists()
    assert "acceptance repair:round 1" in st.data["unclean_trees"]


# --- the integrator -----------------------------------------------------------

def _integ(leerie, tmp_path, monkeypatch):
    from tests import test_integrate_wave_judge_gate as tij
    st = tij._state(leerie, tmp_path)
    st.data["skip_integration_check"] = True
    leerie_dir = tmp_path / ".leerie"
    staging, pre = tij._conflicting_staging(leerie_dir)
    monkeypatch.setattr(leerie, "_run_script",
                        tij._real_conflict_script(staging))
    monkeypatch.setattr(leerie, "_log_session_id", lambda p: "sess-1")
    return tij, st, leerie_dir, staging, pre


def test_integrator_reasked_deletes_its_scratch_and_integrates(
        leerie, tmp_path, monkeypatch):
    tij, st, leerie_dir, staging, pre = _integ(leerie, tmp_path, monkeypatch)
    calls: list[dict] = []

    async def fake_claude_p(**kw):
        calls.append(kw)
        if kw.get("resume_session"):
            (staging / "notes.txt").unlink()
            return {"status": "resolved"}
        out = tij._resolve_and_commit(staging)
        (staging / "notes.txt").write_text("merge notes\n")
        return out
    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    results = {"feat-001": {"status": "complete"}}
    out = asyncio.run(leerie.integrate_wave(
        ["feat-001"], results, leerie_dir, tij._caps(leerie), st, tij.MODELS,
        tij.EFFORTS))
    assert out == ["feat-001"]
    assert [c["sid"] for c in calls] == ["integrator-feat-001",
                                        "integrator-feat-001-clean1"]
    assert "?? notes.txt" in calls[1]["user_prompt"]
    assert calls[1]["schema_key"] == "integrator"


def test_integrator_left_dirty_is_backed_out_unconverged(
        leerie, tmp_path, monkeypatch):
    tij, st, leerie_dir, staging, pre = _integ(leerie, tmp_path, monkeypatch)

    async def fake_claude_p(**kw):
        if kw.get("resume_session"):
            return {"status": "resolved"}
        out = tij._resolve_and_commit(staging)
        (staging / "notes.txt").write_text("merge notes\n")
        return out
    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    results = {"feat-001": {"status": "complete"}}
    out = asyncio.run(leerie.integrate_wave(
        ["feat-001"], results, leerie_dir, tij._caps(leerie), st, tij.MODELS,
        tij.EFFORTS))
    assert out == []
    assert results["feat-001"]["status"] == "unconverged"
    assert _git(staging, "rev-parse", "HEAD") == pre
    # The back-out is exact: the untracked file went with the merge.
    assert not (staging / "notes.txt").exists()
    rec = st.data["unconverged_subtasks"]["feat-001"]
    assert "?? notes.txt" in rec["reason"]


# --- a span holder written beside a staged set (DESIGN §8 *No line of a
# staged set's source reaches history*, rule 1). Rewritten from
# tests/test_redrive_leftovers_guards.py: the implementer's own file there
# is now committed by the implementer when re-asked, never by leerie; the
# file removed at unstage for holding the set's lines is still listed. ----

NEW_DIR_SET = ("import { a } from '../src/a';\n"
               "describe('the codes', () => {\n"
               "  it('are stable', () => {\n"
               "    expect(a).toEqual(lookup(table, key));\n"
               "  });\n"
               "});\n")


def test_a_span_holder_in_a_staging_made_dir_is_listed(leerie, tmp_path,
                                                       monkeypatch):
    w, tdr = _world(leerie, tmp_path, monkeypatch)
    monkeypatch.setattr(tdr, "DEFECT", "acc/a.test.ts")
    s0 = w.sets[0]
    sdir = tmp_path / "set1"
    (sdir / "acc").mkdir()
    (sdir / "acc" / "a.test.ts").write_text(NEW_DIR_SET)
    s0["defect_files"] = ["acc/a.test.ts"]
    s0["cases"] = {"acc/a.test.ts": ["case 1"],
                   s0["control_files"][0]: ["control 1"]}
    w.st.save()

    def fix(w):
        (w.staging / "acc" / "index.ts").write_text(
            "export const codes = 1\n")
        (w.staging / "acc" / "notes.ts").write_text(NEW_DIR_SET)
        (w.staging / "acc" / "__snapshots__").mkdir()
        (w.staging / "acc" / "__snapshots__" / "a.test.ts.snap"
         ).write_text("snap\n")
        w.write("export const a = 1; // FIXED\n")

    def commit_index(w):
        _git(w.staging, "add", "acc/index.ts")
        _git(w.staging, "commit", "-qm", "codes")
    w.edits = [fix, commit_index]
    w.run()
    ge = w.st.data["delivery_redrive"]["rounds"][0]["groups"][0]
    assert not ge.get("rolled_back")
    assert _git(w.staging, "ls-files", "acc/index.ts") == "acc/index.ts"
    assert ge["heldout_listed"] == ["acc/notes.ts"]
    assert "acc/__snapshots__/a.test.ts.snap" in ge["heldout_removed"]
    assert ge["heldout_removed"].count("acc/notes.ts") == 1
    (e,) = [e for e in w.st.data.get("needs_human_verification") or []
            if e.get("source") == leerie._REDRIVE_HELDOUT_GAP_SOURCE]
    assert "acc/notes.ts" in e["detail"] and ".snap" not in e["detail"]
    assert "acc/notes.ts" in leerie._format_pr_end_state_section(w.st.data)
