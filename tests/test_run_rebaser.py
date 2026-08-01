"""Tests for run_rebaser() — the host-side entrypoint for the `rebaser`
worker (DESIGN §6 *Finalization* "Rebase-onto-base before push"),
mirroring test_dep_capture_worker.py's _invoke-stubbing pattern and
test_run_recapture_deps's State-construction discipline.

Uses a REAL git repo (not a stubbed git) so the pre-rebase-sha capture and
the post-call check_rebaser_worktree_state() checkpoint both run against
real git state — only the LLM call itself (_invoke) is stubbed.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True, text=True)


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "test@x")
    _git(repo, "config", "user.name", "test")
    (repo / "a.txt").write_text("a\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "a")
    return repo


def _rebaser_envelope(structured: dict) -> dict:
    return {
        "type": "result",
        "subtype": "success",
        "num_turns": 3,
        "total_cost_usd": 0.01,
        "is_error": False,
        "terminal_reason": "completed",
        "result": json.dumps(structured),
        "structured_output": structured,
        "usage": {"input_tokens": 500, "output_tokens": 200},
    }


def _full_confidence(resolution: float) -> dict:
    return {
        "resolution": resolution,
        "basis": "checked worktree state",
        "falsifiers_tested": ["no conflict markers"],
        "contradictions_reconciled": [],
        "gap_to_close": {"resolution": ""},
    }


def _patch_invoke(leerie, monkeypatch, envelope: dict) -> None:
    async def fake_invoke(cmd, cwd, timeout, sid, leerie_dir, verbosity,
                          progress=None, **_kw):
        return envelope

    monkeypatch.setattr(leerie, "_invoke", fake_invoke)


@pytest.fixture(autouse=True)
def _no_state_lock_conflict(monkeypatch):
    """Each test uses a fresh leerie_root, so State() should never hit
    StateLockedError from a stale lock — nothing to patch, this fixture
    just documents the assumption for future readers."""
    yield


def test_run_rebaser_rebased_status_passes_through_on_clean_worktree(leerie, tmp_path, monkeypatch):
    """Worker claims 'rebased'; the worktree is genuinely clean (no
    conflict markers, no mid-rebase state) → run_rebaser returns the
    worker's own status unmodified."""
    repo = _init_repo(tmp_path)
    leerie_root = tmp_path / "leerie_root"
    _patch_invoke(leerie, monkeypatch, _rebaser_envelope({
        "status": "rebased",
        "final_branch_state": "clean",
        "resolution_summary": "no conflicts",
        "diagnosis": "",
        "confidence": _full_confidence(9.5),
    }))

    result = leerie.run_rebaser(
        leerie_root, repo, "run-001", repo,
        "leerie/runs/run-001", "main", "main")

    assert result["status"] == "rebased"
    assert result["final_branch_state"] == "clean"


def test_run_rebaser_irreconcilable_status_passes_through(leerie, tmp_path, monkeypatch):
    """Worker claims 'irreconcilable'; worktree HEAD is unchanged (a real
    abort would restore it) → passes the checkpoint, status preserved."""
    repo = _init_repo(tmp_path)
    leerie_root = tmp_path / "leerie_root"
    _patch_invoke(leerie, monkeypatch, _rebaser_envelope({
        "status": "irreconcilable",
        "final_branch_state": "aborted",
        "diagnosis": "two incompatible business rules",
        "confidence": _full_confidence(9.0),
    }))

    result = leerie.run_rebaser(
        leerie_root, repo, "run-002", repo,
        "leerie/runs/run-002", "main", "main")

    assert result["status"] == "irreconcilable"
    assert "incompatible business rules" in result["diagnosis"]


def test_run_rebaser_downgrades_to_failed_when_claim_mismatches_state(leerie, tmp_path, monkeypatch):
    """Worker claims 'rebased' but conflict markers are actually still in
    the tree — the mechanical checkpoint catches the mismatch and
    downgrades the result to 'failed' rather than trusting the self-report
    (the whole point of check_rebaser_worktree_state)."""
    repo = _init_repo(tmp_path)
    (repo / "a.txt").write_text("<<<<<<< HEAD\nx\n=======\ny\n>>>>>>> b\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "leaves markers")
    leerie_root = tmp_path / "leerie_root"
    _patch_invoke(leerie, monkeypatch, _rebaser_envelope({
        "status": "rebased",
        "final_branch_state": "claims clean but isn't",
        "resolution_summary": "",
        "diagnosis": "",
        "confidence": _full_confidence(9.5),
    }))

    result = leerie.run_rebaser(
        leerie_root, repo, "run-003", repo,
        "leerie/runs/run-003", "main", "main")

    assert result["status"] == "failed"
    assert "mechanical checkpoint failed" in result["diagnosis"]
    assert "conflict markers" in result["diagnosis"]


def test_run_rebaser_schema_validation_exhaustion_returns_failed(leerie, tmp_path, monkeypatch):
    """claude_p() itself raises WorkerError (never returns a dict missing
    structured_output — it validates internally and retries once before
    raising) when the worker never produces schema-valid output. run_rebaser
    must catch this and degrade to a 'failed' result rather than propagating
    the exception into host-finalize.sh's best-effort rebase step."""
    repo = _init_repo(tmp_path)
    leerie_root = tmp_path / "leerie_root"
    envelope = {
        "type": "result", "subtype": "error_max_turns",
        "is_error": False, "result": "gave up, no schema match",
        "structured_output": None,
        "usage": {"input_tokens": 100, "output_tokens": 10},
    }
    _patch_invoke(leerie, monkeypatch, envelope)

    result = leerie.run_rebaser(
        leerie_root, repo, "run-004", repo,
        "leerie/runs/run-004", "main", "main")

    assert result["status"] == "failed"
    assert "schema-valid output" in result["diagnosis"]


def test_run_rebaser_skips_worker_when_budget_exhausted(leerie, tmp_path, monkeypatch):
    """CLAUDE.md 'Caps are real Python counters': run_rebaser must respect
    the same max_total_workers ceiling every other worker invocation does
    (mirrors capture_repo_deps's identical pre-check) — a rebaser call must
    never be invisible to the per-run worker budget. A python3 stub that
    would fail the test if invoked at all proves the worker is never
    called when the budget is already exhausted."""
    repo = _init_repo(tmp_path)
    leerie_root = tmp_path / "leerie_root"
    run_dir = leerie_root / "runs" / "run-budget"
    run_dir.mkdir(parents=True)
    (run_dir / "state.json").write_text(json.dumps({
        "worker_count": leerie.DEFAULT_CAPS["max_total_workers"],
    }))

    def fail_if_called(*a, **kw):
        raise AssertionError("_invoke must not be called when budget is exhausted")

    monkeypatch.setattr(leerie, "_invoke", fail_if_called)

    result = leerie.run_rebaser(
        leerie_root, repo, "run-budget", repo,
        "leerie/runs/run-budget", "main", "main")

    assert result["status"] == "failed"
    assert "budget exhausted" in result["diagnosis"]


def test_run_rebaser_bumps_worker_count_on_invocation(leerie, tmp_path, monkeypatch):
    """A successful rebaser call increments and persists worker_count via
    st.bump_workers(caps), same as every other claude_p() caller — the
    call must count against the run's budget, not run invisibly."""
    repo = _init_repo(tmp_path)
    leerie_root = tmp_path / "leerie_root"
    run_dir = leerie_root / "runs" / "run-006"
    run_dir.mkdir(parents=True)
    (run_dir / "state.json").write_text(json.dumps({"worker_count": 5}))
    _patch_invoke(leerie, monkeypatch, _rebaser_envelope({
        "status": "rebased",
        "final_branch_state": "clean",
        "resolution_summary": "",
        "diagnosis": "",
        "confidence": _full_confidence(9.5),
    }))

    result = leerie.run_rebaser(
        leerie_root, repo, "run-006", repo,
        "leerie/runs/run-006", "main", "main")

    assert result["status"] == "rebased"
    after = json.loads((run_dir / "state.json").read_text())
    assert after["worker_count"] == 6


def test_run_rebaser_worker_exception_returns_failed_not_raised(leerie, tmp_path, monkeypatch):
    """claude_p raising (e.g. a WorkerError after retries) must be
    swallowed to a 'failed' result — this call must never propagate an
    exception into host-finalize.sh's best-effort rebase step."""
    repo = _init_repo(tmp_path)
    leerie_root = tmp_path / "leerie_root"

    async def raising_invoke(*a, **kw):
        raise leerie.WorkerError("simulated worker crash")

    monkeypatch.setattr(leerie, "_invoke", raising_invoke)

    result = leerie.run_rebaser(
        leerie_root, repo, "run-005", repo,
        "leerie/runs/run-005", "main", "main")

    assert result["status"] == "failed"
    assert "simulated worker crash" in result["diagnosis"]
