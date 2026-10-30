"""Review round 7 H1 + M1: the failing-test-ID diff the outcome reads must
describe the tree that ships (DESIGN §8 *The final diff describes the tree
that ships*).

H1: a final conformance round that ended before its post-measurement (a
worker error, a timeout, a malformed result, a protected-path or clobber
rollback) took no diff, so a real regression shipped `done` with an empty
end-state section. M1: an acceptance repair commit (or anything else) moved
staging after the diff, and only a re-drive that itself moved HEAD measured
it again. Every driver runs on a real pytest suite."""
from __future__ import annotations

import asyncio
import inspect
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))
from test_redrive_test_id_diff_refresh import (CMD, ITEM, SUITE, _git,  # noqa: F401
                                               redriven)


@pytest.fixture
def broken_run(leerie, tmp_path, monkeypatch):
    """`test_x` passes on the base and fails on the run branch: a real
    regression. Baseline failing IDs `[]`; no required item names tests."""
    monkeypatch.setattr(leerie, "_host_cpu_psi", lambda path=None: 0.0)
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / ".gitignore").write_text(".leerie/\n__pycache__/\n")
    (repo / "tests" / "test_s.py").write_text(SUITE.format(ok="True"))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    base = _git(repo, "rev-parse", "HEAD")
    run_id = "fix-001-abcdef"
    _git(repo, "checkout", "-qb", f"leerie/runs/{run_id}")
    (repo / "tests" / "test_s.py").write_text(SUITE.format(ok="False"))
    _git(repo, "commit", "-qam", "break")
    _git(repo, "checkout", "-q", "main")
    root = repo / ".leerie"
    run_dir = root / "runs" / run_id
    (run_dir / "logs").mkdir(parents=True)
    staging = run_dir / "worktrees" / "staging"
    _git(repo, "worktree", "add", "-q", str(staging), f"leerie/runs/{run_id}")
    st = leerie.State(root, run_id)
    st.data = {"task": "x", "working_branch": "main",
               "conformance": {"_baseline": {"sha": base}},
               "baseline_failing_ids": [], "subtask_status": {"a": "done"},
               "required_items": [{"item": "fix y"}]}
    st.repo_root = repo
    st.save()
    monkeypatch.setattr(leerie, "resolve_blt", lambda root: {"test": CMD})
    caps = {**leerie.DEFAULT_CAPS, "confirm_intermittent_repeats": 0}
    return {"st": st, "staging": staging, "run_dir": run_dir, "caps": caps,
            "repo": repo}


def _conformer_returns(leerie, monkeypatch, res: dict) -> None:
    async def fake(*a, **k):
        return dict(res)
    monkeypatch.setattr(leerie, "claude_p", fake)
    monkeypatch.setattr(leerie, "_validate_conformance_result",
                        lambda r, wt: None)
    monkeypatch.setattr(leerie, "check_production_evidence", lambda r: [])


def _break_worker_error(leerie, monkeypatch, caps):
    async def boom(*a, **k):
        raise leerie.WorkerError("final conformer: transport error")
    monkeypatch.setattr(leerie, "claude_p", boom)
    return caps


def _break_timeout(leerie, monkeypatch, caps):
    async def slow(*a, **k):
        raise subprocess.TimeoutExpired("claude", 1)
    monkeypatch.setattr(leerie, "claude_p", slow)
    return caps


def _break_malformed(leerie, monkeypatch, caps):
    async def fake(*a, **k):
        return {}
    monkeypatch.setattr(leerie, "claude_p", fake)
    monkeypatch.setattr(leerie, "_validate_conformance_result",
                        lambda r, wt: "residuals without rules files read")
    return caps


def _break_protected(leerie, monkeypatch, caps):
    _conformer_returns(leerie, monkeypatch, {})

    async def touched(wt, before):
        return [".github/workflows/ci.yml"]

    async def rollback(wt, before, *, where):
        return True
    monkeypatch.setattr(leerie, "_protected_paths_since", touched)
    monkeypatch.setattr(leerie, "_restore_tree", rollback)
    return caps


def _break_clobber(leerie, monkeypatch, caps):
    _conformer_returns(leerie, monkeypatch, {})

    async def clobbered(wt, base, head):
        return ["tests/test_s.py"]

    async def rollback(wt, before, *, where):
        return True
    monkeypatch.setattr(leerie, "_clobbered_owned_files", clobbered)
    monkeypatch.setattr(leerie, "_restore_tree", rollback)
    return {**caps, "strict_conformer": True}


@pytest.mark.parametrize("breaker", [
    _break_worker_error, _break_timeout, _break_malformed, _break_protected,
    _break_clobber], ids=["worker_error", "timeout", "malformed",
                          "protected_path", "clobber"])
def test_a_round_that_breaks_before_its_post_keeps_its_pre_ids(
        leerie, broken_run, monkeypatch, breaker):
    """H1, every early exit the reviewer named: round 0's pre-measurement
    failed `test_x`, the tree did not move, so its IDs are confirmed against
    the base there and then. Before: no `test_id_diff`, outcome `done`, an
    empty end-state section."""
    st, run_dir = broken_run["st"], broken_run["run_dir"]
    caps = breaker(leerie, monkeypatch, broken_run["caps"])
    run = leerie._run_final_conformance(
        run_dir, st, caps, {"conformer": "sonnet"}, {"conformer": "low"})
    if caps.get("strict_conformer"):
        # Strict mode stops the run on the clobber — after the diff.
        with pytest.raises(SystemExit):
            asyncio.run(run)
    else:
        asyncio.run(run)
    diff = st.data["test_id_diff"]
    assert [x.rsplit(".", 1)[-1] for x in diff["new"]] == ["test_x"]
    assert diff["tree_sha"] == asyncio.run(
        leerie._worktree_tree_sha(str(broken_run["staging"])))
    warns = st.data["conformance"]["_final"]["warnings"]
    assert any("test_x" in w for w in warns), warns
    assert leerie._decide_outcome(st, any_commits=True) == "unconverged"
    assert "1 test(s) newly failing" in leerie._format_pr_end_state_section(
        st.data)


def test_a_round_whose_tree_moved_leaves_it_to_the_shipping_refresh(
        leerie, broken_run, monkeypatch):
    """The conformer committed and then its transport failed: the pre IDs
    are of another tree and are not used; phase 5's refresh measures the
    tree that ships instead."""
    st, run_dir, staging = (broken_run["st"], broken_run["run_dir"],
                            broken_run["staging"])

    async def commit_then_fail(*a, **k):
        (staging / "notes.txt").write_text("x\n")
        _git(staging, "add", "-A")
        _git(staging, "commit", "-qm", "conformer: note")
        raise leerie.WorkerError("transport error after a commit")
    monkeypatch.setattr(leerie, "claude_p", commit_then_fail)
    asyncio.run(leerie._run_final_conformance(
        run_dir, st, broken_run["caps"], {"conformer": "sonnet"},
        {"conformer": "low"}))
    assert "test_id_diff" not in st.data
    asyncio.run(leerie._refresh_shipping_test_id_diff(
        run_dir, st, broken_run["caps"], label="test-id-diff-final"))
    diff = st.data["test_id_diff"]
    assert diff["tree_sha"] == asyncio.run(
        leerie._worktree_tree_sha(str(staging)))
    assert [x.rsplit(".", 1)[-1] for x in diff["new"]] == ["test_x"]
    assert leerie._decide_outcome(st, any_commits=True) == "unconverged"


def test_a_redrive_with_nothing_routed_no_longer_leaves_a_stale_new(
        leerie, redriven):
    """M1, direction 1 (the reviewer's driver): the diff confirmed `test_x`
    new on tree A, an acceptance repair fixed it (tree B), and the re-drive
    had nothing to route. Before: `unconverged`, "newly failing on the
    final tree". Now the refresh before the outcome measures tree B."""
    st, staging = redriven["st"], redriven["staging"]
    st.data["delivery_gate"] = {**st.data["delivery_gate"],
                                "unmet_final": []}
    st.save()
    asyncio.run(leerie._run_delivery_redrive(
        st.run_dir, st, redriven["caps"], {}, {}))
    assert st.data["test_id_diff"]["tree_sha"] == redriven["t0"]
    asyncio.run(leerie._refresh_shipping_test_id_diff(
        st.run_dir, st, redriven["caps"], label="test-id-diff-final"))
    diff = st.data["test_id_diff"]
    assert diff["tree_sha"] == redriven["t1"] and diff["new"] == []
    row = st.data["delivery_gate"]["items"][0]
    assert row["verdict"] == "met", row
    assert leerie._decide_outcome(st, any_commits=True) == "done"


def test_no_new_on_an_earlier_tree_settles_met_after_the_refresh(
        leerie, redriven):
    """M1, direction 2: a "no regressions" diff of tree A read unknown on
    tree B ("not of the final tree") on every run whose repair kept a
    commit. Measured again on B, the row is met and no gap is listed."""
    st = redriven["st"]
    st.data["test_id_diff"] = {**st.data["test_id_diff"], "new": [],
                               "candidates": [], "budget_exhausted": False}
    assert leerie._test_outcome_verdict(
        st, redriven["t1"])["verdict"] == "unknown"
    asyncio.run(leerie._refresh_shipping_test_id_diff(
        st.run_dir, st, redriven["caps"], label="test-id-diff-acceptance"))
    assert leerie._test_outcome_verdict(
        st, redriven["t1"])["verdict"] == "met"
    assert st.data["delivery_gate"]["items"][0]["verdict"] == "met"
    assert not [e for e in st.data.get("needs_human_verification") or []
                if e.get("source") in (leerie.TEST_OUTCOME_GAP_SOURCE,
                                       leerie.FINAL_DIFF_GAP_SOURCE)]


def test_an_unmeasurable_shipping_tree_is_an_explicit_unknown(
        leerie, broken_run, monkeypatch):
    """No diff, and the test axis does not measure on the shipping tree:
    the gap is listed under "Regression checks the run could not settle"
    though no required item names the test outcome."""
    st, run_dir = broken_run["st"], broken_run["run_dir"]

    async def unmeasured(tree, axes, st_, caps_, **kw):
        return {"tests": {"ran": False, "measured": False, "passed": None,
                          "command": CMD, "summary": "runner missing"}}
    monkeypatch.setattr(leerie, "_measure_axes", unmeasured)
    asyncio.run(leerie._refresh_shipping_test_id_diff(
        run_dir, st, broken_run["caps"], label="test-id-diff-final"))
    assert "test_id_diff" not in st.data
    groups = leerie._pr_needs_human(st.data, lambda sid: True)
    lines = groups["Regression checks the run could not settle"]
    assert len(lines) == 1 and "runner missing" in lines[0]
    assert "not measured on the tree that ships" in lines[0]
    # Measured on a later attempt: the gap is cleared.
    monkeypatch.undo()
    monkeypatch.setattr(leerie, "_host_cpu_psi", lambda path=None: 0.0)
    monkeypatch.setattr(leerie, "resolve_blt", lambda root: {"test": CMD})
    asyncio.run(leerie._refresh_shipping_test_id_diff(
        run_dir, st, broken_run["caps"], label="test-id-diff-final"))
    assert st.data["test_id_diff"]["new"]
    assert not [e for e in st.data.get("needs_human_verification") or []
                if e.get("source") == leerie.FINAL_DIFF_GAP_SOURCE]


def test_no_test_command_and_no_diff_lists_nothing(leerie, broken_run,
                                                   monkeypatch):
    st = broken_run["st"]
    monkeypatch.setattr(leerie, "resolve_blt", lambda root: {})
    asyncio.run(leerie._refresh_shipping_test_id_diff(
        broken_run["run_dir"], st, broken_run["caps"], label="t"))
    assert not st.data.get("needs_human_verification")


def test_staging_is_measured_in_place_and_its_regression_found(
        leerie, broken_run):
    """Review round 8 M1's pin, rewritten for I1 + I2 (DESIGN §8 *The final
    diff describes the tree that ships*): staging is its commit plus ignored
    files, so it is measured in place, stamped with its committed tree, and
    the regression it carries is found; nothing is listed as unmeasured.
    (A stray untracked file there now stops the run:
    tests/test_shipping_tree_is_committed.py.)"""
    st, staging = broken_run["st"], broken_run["staging"]
    asyncio.run(leerie._refresh_shipping_test_id_diff(
        broken_run["run_dir"], st, broken_run["caps"], label="t"))
    diff = st.data["test_id_diff"]
    assert [x.rsplit(".", 1)[-1] for x in diff["new"]] == ["test_x"]
    assert diff["tree_sha"] == asyncio.run(
        leerie._committed_tree_sha(str(staging)))
    assert not [e for e in st.data.get("needs_human_verification") or []
                if e.get("source") == leerie.FINAL_DIFF_GAP_SOURCE]


def test_a_stale_tree_is_not_measured_twice(leerie, redriven, monkeypatch):
    """The re-drive's tail already tried the shipping tree and could not
    measure it: the final refresh keeps the stale mark and the gap rather
    than running the suite again."""
    st, staging = redriven["st"], redriven["staging"]
    monkeypatch.setattr(leerie, "resolve_blt", lambda root: {})
    asyncio.run(leerie._redrive_refresh_test_id_diff(
        st.run_dir, st, redriven["caps"], staging))
    assert st.data["test_id_diff"]["stale_for_tree"] == redriven["t1"]
    calls: list = []

    async def measure(*a, **k):
        calls.append(a)
        return {}
    monkeypatch.setattr(leerie, "_measure_axes", measure)
    monkeypatch.setattr(leerie, "resolve_blt", lambda root: {"test": CMD})
    asyncio.run(leerie._refresh_shipping_test_id_diff(
        st.run_dir, st, redriven["caps"], label="t"))
    assert calls == []
    assert [e for e in st.data["needs_human_verification"]
            if e.get("source") == leerie.FINAL_DIFF_GAP_SOURCE]


def test_phase5_refreshes_after_the_acceptance_gate_and_before_finalize(
        leerie):
    """Structure, paired with the consumer tests above: the refresh runs
    after the acceptance gate (before detection) and after the re-drive,
    with nothing between it and phase_finalize."""
    src = inspect.getsource(leerie._run_phases)
    i_acc = src.index("await _run_acceptance_gate(")
    i_r1 = src.index('label="test-id-diff-acceptance"')
    i_det = src.index("await _run_delivery_detection(")
    i_red = src.index("await _run_delivery_redrive(")
    i_r2 = src.index('label="test-id-diff-final"')
    i_fin = src.index("await phase_finalize(")
    assert i_acc < i_r1 < i_det < i_red < i_r2 < i_fin
    between = src[i_r2:i_fin]
    assert "await " not in between
