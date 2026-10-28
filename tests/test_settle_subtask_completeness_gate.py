"""Tests for the implementer completeness gate (DESIGN §9 *The one gating
axis: solution completeness*): the conformer's gating `solution_defects` axis
routed through _settle_subtask (per-subtask retry/block) and
_run_final_conformance (whole-tree: the loop continues while a defect is
unfixed, and a survivor becomes `residual_defects` — never a die), both
INDEPENDENT of --strict-conformer.

- Tier 1: source-coupling wiring pins for the _settle_subtask seam (the retry
  branch is deep inside a large function driving real implementers, so its
  wiring is pinned by source inspection, per the discipline used in
  test_dep_capture_wiring.py). The decision predicate itself
  (_actionable_solution_defects) is unit-tested in test_solution_defects.py.
- Tier 2: behavioral tests of _run_final_conformance's solution_defects
  handling, using a real staging worktree.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import subprocess
from pathlib import Path

import pytest


# === Tier 1: _settle_subtask source-coupling ================================

class TestSettleSubtaskWiring:
    def test_calls_actionable_solution_defects(self, leerie):
        src = inspect.getsource(leerie._settle_subtask)
        assert "_actionable_solution_defects(conf_res)" in src

    def test_gates_on_completeness_retry_rounds_cap(self, leerie):
        src = inspect.getsource(leerie._settle_subtask)
        assert 'completeness_retry_rounds' in src

    def test_retry_sets_continuation_and_note(self, leerie):
        """A found defect re-drives the implementer: continuation=True, a
        note carrying the defects, in_progress status, and continue."""
        src = inspect.getsource(leerie._settle_subtask)
        # locate the completeness block
        block = src[src.index("_actionable_solution_defects(conf_res)"):]
        assert "continuation = True" in block
        assert "_format_solution_defects(" in block
        assert '"in_progress"' in block
        assert "continue" in block

    def test_exhaustion_returns_blocked(self, leerie):
        src = inspect.getsource(leerie._settle_subtask)
        block = src[src.index("_actionable_solution_defects(conf_res)"):]
        assert '"status": "blocked"' in block

    def test_completeness_retries_counter_is_separate(self, leerie):
        """The counter must be distinct from confidence/failed/continuation
        budgets (a self-graded axis borrowing another budget is the bug this
        change removes)."""
        src = inspect.getsource(leerie._settle_subtask)
        assert "completeness_retries = 0" in src

    def test_gate_precedes_strict_blocked_reason(self, leerie):
        """The completeness gate (independent of strict) must run before the
        strict-conformer blocked_reason return, so it is not preempted."""
        src = inspect.getsource(leerie._settle_subtask)
        i_gate = src.index("_actionable_solution_defects(conf_res)")
        i_blocked = src.index("if blocked_reason:")
        assert i_gate < i_blocked

    def test_logs_actionable_warning_at_blocked_detection(self, leerie):
        """N21: the moment `subtask_status[sid]` is set to 'blocked', a
        log() call fires immediately naming the sid and the exact
        `leerie accept-blocked <run-id> <sid>` remedy — not only surfaced
        later at wave close. Checked at EVERY occurrence of the literal
        write, not just the first — the declared-command terminal arm
        added a second site, and an index()-of-first check would have
        gone silently vacuous for whichever site came later."""
        src = inspect.getsource(leerie._settle_subtask)
        occurrences = [i for i in range(len(src))
                       if src.startswith('[sid] = "blocked"', i)]
        assert occurrences, "no direct blocked-status write found"
        for i_assign in occurrences:
            # The very next statements after the assignment must include
            # a log() carrying the sid and the remedy command, before
            # st.save().
            after = src[i_assign:i_assign + 400]
            i_log = after.index("log(")
            i_save = after.index("st.save()")
            assert i_log < i_save, (
                "log() must fire immediately at blocked-detection, not "
                "after the state write is saved and the loop moves on")
            log_call = after[i_log:i_save]
            assert "{sid}" in log_call
            assert "accept-blocked" in log_call
            assert "{st.run_id}" in log_call or "st.run_id" in log_call


class TestFinalConformanceWiring:
    def test_final_uses_actionable_solution_defects(self, leerie):
        src = inspect.getsource(leerie._run_final_conformance)
        assert "_actionable_solution_defects(last_res)" in src

    def test_final_never_dies_on_defects(self, leerie):
        """The only die() left in the final pass is the --strict-conformer
        one (DESIGN §9 *A stop after execution begins*); the behavioural
        pair is test_final_defects_never_die_and_are_recorded."""
        src = inspect.getsource(leerie._run_final_conformance)
        block = src[src.index("final_defects ="):]
        assert block.count("die(") == 1
        assert "strict-conformer" in block[block.index("die("):]


# === Tier 2: _run_final_conformance behavioral ==============================

def _run(cmd, cwd, check=True):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if check:
        assert r.returncode == 0, f"{cmd} failed in {cwd}: {r.stderr}"
    return r


@pytest.fixture
def env(leerie, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _run(["git", "init", "-q", "-b", "main"], cwd=repo)
    _run(["git", "config", "user.email", "t@t"], cwd=repo)
    _run(["git", "config", "user.name", "t"], cwd=repo)
    (repo / "README.md").write_text("# repo\n")
    _run(["git", "add", "-A"], cwd=repo)
    _run(["git", "commit", "-q", "-m", "initial"], cwd=repo)
    run_id = "fix-001-abcdef"
    run_branch = f"leerie/runs/{run_id}"
    _run(["git", "checkout", "-q", "-b", run_branch], cwd=repo)
    (repo / "src.py").write_text("def f():\n    pass\n")
    _run(["git", "add", "-A"], cwd=repo)
    _run(["git", "commit", "-q", "-m", "wave-1: add f"], cwd=repo)
    # Move the primary checkout off the run branch before creating the
    # staging worktree on it — git refuses to check the same branch out of
    # two worktrees (mirrors setup-run.sh running from a fresh process).
    _run(["git", "checkout", "-q", "main"], cwd=repo)
    leerie_root = repo / ".leerie"
    run_dir = leerie_root / "runs" / run_id
    (run_dir / "logs").mkdir(parents=True)
    (run_dir / "worktrees").mkdir(parents=True)
    staging = run_dir / "worktrees" / "staging"
    _run(["git", "worktree", "add", "-q", str(staging), run_branch], cwd=repo)
    st = leerie.State(leerie_root, run_id)
    st.data = {"task": "x", "working_branch": "main"}
    st.save()
    caps = dict(leerie.DEFAULT_CAPS)
    models = {w: "sonnet" for w in leerie.WORKER_TYPES}
    efforts = {w: None for w in leerie.WORKER_TYPES}
    return {"leerie": leerie, "repo": repo, "staging": staging,
            "run_dir": run_dir, "st": st, "caps": caps, "models": models,
            "efforts": efforts}


def _clean_final(**overrides):
    base = {
        "subtask_id": "_final",
        "rules_files_read": [], "rule_violations_fixed": [],
        "rule_violations_residual": [], "docs_updates": [], "tests_updates": [],
        "build": {"ran": False, "passed": False, "command": "(none)",
                  "summary": ""},
        "lint": {"ran": False, "passed": False, "command": "(none)",
                 "summary": ""},
        "tests": {"ran": False, "passed": False, "command": "(none)",
                  "summary": ""},
        "summary": "ok", "solution_defects": [],
    }
    base.update(overrides)
    return base


def _stub_claude_p(leerie_mod, result):
    async def _stub(**kwargs):
        return result
    leerie_mod.claude_p = _stub


@pytest.fixture(autouse=True)
def _restore_claude_p(leerie):
    original = leerie.claude_p
    yield
    leerie.claude_p = original


_DEFECT = {"kind": "unhandled_input", "concrete_case": "empty list",
           "where": "src.py:10", "why_ships_a_defect": "crashes"}


def _stub_rounds(leerie_mod, results):
    """claude_p returning `results` in order, recording each call's sid and
    user prompt."""
    calls: list[dict] = []

    async def _stub(**kwargs):
        calls.append({"sid": kwargs.get("sid"),
                      "prompt": kwargs.get("user_prompt") or ""})
        return results[min(len(calls), len(results)) - 1]
    leerie_mod.claude_p = _stub
    return calls


def test_final_defects_never_die_and_are_recorded(env):
    """An unfixed actionable defect keeps the loop going to the cap, and the
    survivor is recorded for the delivery re-drive and the outcome — the run
    is not stopped (it used to die "fix and resume")."""
    c = env["leerie"]
    assert not env["caps"].get("strict_conformer")
    calls = _stub_rounds(c, [_clean_final(solution_defects=[dict(_DEFECT)])])
    asyncio.run(c._run_final_conformance(
        env["run_dir"], env["st"], env["caps"], env["models"],
        env["efforts"]))
    assert [x["sid"] for x in calls] == [
        f"final-conformer-r{i}"
        for i in range(env["caps"]["conformance_rounds"])]
    final = env["st"].data["conformance"]["_final"]
    assert final["blocked"] is False
    assert final["residual_defects"] == [dict(_DEFECT)]
    assert c._residual_defects_reason(env["st"]).startswith(
        "1 solution defect(s) the final conformance left unfixed")


def test_final_defect_fixed_in_the_next_round(env):
    """Replay shape (barnacle 45fa4253 pass 1): round 0 reports an unfixed
    defect on a tree whose axes read clean; round 1 runs, is handed the
    defect, fixes it, and the pass ends there with nothing residual."""
    c = env["leerie"]
    calls = _stub_rounds(c, [
        _clean_final(solution_defects=[dict(_DEFECT, fixed=False)]),
        _clean_final(solution_defects=[dict(_DEFECT, fixed=True)]),
    ])
    asyncio.run(c._run_final_conformance(
        env["run_dir"], env["st"], env["caps"], env["models"],
        env["efforts"]))
    assert [x["sid"] for x in calls] == ["final-conformer-r0",
                                         "final-conformer-r1"]
    assert "UNFIXED SOLUTION DEFECTS" not in calls[0]["prompt"]
    assert "UNFIXED SOLUTION DEFECTS" in calls[1]["prompt"]
    assert "empty list" in calls[1]["prompt"]
    final = env["st"].data["conformance"]["_final"]
    assert "residual_defects" not in final
    assert final["blocked"] is False
    assert c._residual_defects_reason(env["st"]) is None


def test_final_clean_passes(env):
    c = env["leerie"]
    _stub_claude_p(c, _clean_final())
    asyncio.run(c._run_final_conformance(
        env["run_dir"], env["st"], env["caps"], env["models"], env["efforts"]))
    final = (env["st"].data.get("conformance") or {}).get("_final")
    assert final is not None
    assert final["blocked"] is False


def test_final_vague_defect_does_not_die(env):
    """A defect missing concrete_case is non-actionable and must NOT die."""
    c = env["leerie"]
    _stub_claude_p(c, _clean_final(solution_defects=[{
        "kind": "unhandled_input", "concrete_case": "",
        "where": "src.py:10", "why_ships_a_defect": "vague",
    }]))
    asyncio.run(c._run_final_conformance(
        env["run_dir"], env["st"], env["caps"], env["models"], env["efforts"]))
    final = (env["st"].data.get("conformance") or {}).get("_final")
    assert final["blocked"] is False


def test_skip_completeness_check_demotes_final_gate(env):
    """R1: with skip_completeness_check set, a real actionable defect on the
    final tree does NOT die — it surfaces as an advisory warning instead."""
    c = env["leerie"]
    env["st"].data["skip_completeness_check"] = True
    env["st"].save()
    _stub_claude_p(c, _clean_final(solution_defects=[{
        "kind": "unhandled_input", "concrete_case": "empty list",
        "where": "src.py:10", "why_ships_a_defect": "crashes",
    }]))
    calls = _stub_rounds(c, [_clean_final(solution_defects=[dict(_DEFECT)])])
    asyncio.run(c._run_final_conformance(
        env["run_dir"], env["st"], env["caps"], env["models"], env["efforts"]))
    final = (env["st"].data.get("conformance") or {}).get("_final")
    assert final["blocked"] is False
    assert any("skip-completeness-check" in w for w in final["warnings"])
    # Advisory: the defect neither holds the loop open nor stays residual.
    assert len(calls) == 1
    assert "residual_defects" not in final


class TestSkipWiring:
    def test_settle_subtask_honors_skip_flag(self, leerie):
        src = inspect.getsource(leerie._settle_subtask)
        assert 'skip_completeness_check' in src

    def test_final_conformance_honors_skip_flag(self, leerie):
        src = inspect.getsource(leerie._run_final_conformance)
        assert 'skip_completeness_check' in src
