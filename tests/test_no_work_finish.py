"""Tests for `_finish_no_work_run` — the cleared-but-empty terminal
state handler (DESIGN §8).

When every planner returns `status: "ready"` with empty `subtasks`,
the orchestrator records the no-work outcome, writes `finished_at` to
both state.json and run.json, and exits 0. `_derive_run_status` reads
the `finished_at` + missing `pushed_at` / `pr_url` and renders the run
as `done` in `leerie --list`.

These tests pin the contract end-to-end: state.json gets the audit
fields, run.json gets `finished_at` + `no_push=True`, and the
existing terminal-status derivation returns `done` without any
new status enum.
"""
from __future__ import annotations

import json
from pathlib import Path


def _bootstrap_run_dir(tmp_path: Path, leerie):
    """Create a minimal `.leerie/runs/<id>/` dir and a `State` anchored
    on it with the run-identity fields pre-populated, matching the
    state the orchestrator would be in immediately after
    `phase_reconcile` returns. Returns the State."""
    leerie_root = tmp_path / ".leerie"
    runs_dir = leerie_root / "runs"
    run_id = "bugfix-nothing-to-do-deadbeef"
    (runs_dir / run_id).mkdir(parents=True)
    st = leerie.State(leerie_root, run_id)
    st.data = {
        "task": "fix something that turns out to be already fixed",
        "started_at": "2026-05-31T20:00:00+00:00",
        "worker_count": 4,
        "categories": ["bug-fixing", "testing"],
    }
    st.save()
    return st


def test_finish_no_work_run_writes_done_local(leerie, tmp_path):
    """_finish_no_work_run records no_work_required + finished_at in
    state.json, writes finished_at + no_push=True to run.json, and
    _derive_run_status renders the run as done."""
    st = _bootstrap_run_dir(tmp_path, leerie)
    reasons = {
        "bug-fixing": "HEAD already ships the fix",
        "testing": "regression test already exists in src/tests/...",
    }
    leerie._finish_no_work_run(st, reasons)
    # state.json contract
    loaded = json.loads(st.path.read_text())
    assert loaded["no_work_required"] is True
    assert loaded["no_work_reasons"] == reasons
    assert isinstance(loaded["finished_at"], str)
    assert loaded["finished_at"]
    assert loaded["waves"] == []
    assert loaded["subtask_status"] == {}
    # run.json contract
    rj_path = st.run_dir / "run.json"
    assert rj_path.exists()
    rj = json.loads(rj_path.read_text())
    assert rj["finished_at"] == loaded["finished_at"]
    assert rj["no_push"] is True
    assert rj["no_verify"] is False
    # And the existing terminal-status derivation classifies this as
    # done (no push, no PR — there's no commit to propose).
    assert leerie._derive_run_status(rj, loaded) == "done"


def test_finish_no_work_run_preserves_existing_run_json_fields(leerie, tmp_path):
    """Regression guard for the merge-semantics contract of
    `_write_run_json` (`leerie.py:1872` — it reads, merges, writes). The
    no-work finisher must not clobber the run-identity fields written
    at run start (`run_id`, `branch`, `working_branch`, `started_at`,
    `task`)."""
    st = _bootstrap_run_dir(tmp_path, leerie)
    # Pre-write a run.json the way `_orchestrate` does after the
    # run.json initialization after phase_classify.
    leerie._write_run_json(
        st.run_dir,
        run_id=st.run_id,
        branch=leerie._compute_run_branch(st.run_id),
        working_branch="main",
        started_at=st.data["started_at"],
        task=st.data["task"],
    )
    # Now run the no-work finisher.
    leerie._finish_no_work_run(st, {"bug-fixing": "nothing to fix"})
    rj = json.loads((st.run_dir / "run.json").read_text())
    # The run-identity fields are still there.
    assert rj["run_id"] == st.run_id
    assert rj["branch"] == leerie._compute_run_branch(st.run_id)
    assert rj["working_branch"] == "main"
    assert rj["started_at"] == st.data["started_at"]
    assert rj["task"] == st.data["task"]
    # And the no-work finisher's additions are there too.
    assert rj["finished_at"]
    assert rj["no_push"] is True


def test_finish_no_work_run_logs_per_domain_basis(leerie, tmp_path, capsys):
    """The user-facing log lines must quote each planner's basis so
    the user can see WHY each domain concluded there was no work —
    that's the whole point of replacing the bare die() with this
    handler."""
    st = _bootstrap_run_dir(tmp_path, leerie)
    reasons = {
        "bug-fixing": "commit ff2e97f already ships the fix",
        "configuration-build": "package.json already has NODE_OPTIONS=4096",
    }
    leerie._finish_no_work_run(st, reasons)
    out = capsys.readouterr().out
    # The summary line names the trigger.
    assert "nothing to _schedule" in out.lower()
    # Each domain + basis is quoted.
    assert "bug-fixing" in out
    assert "ff2e97f" in out
    assert "configuration-build" in out
    assert "NODE_OPTIONS=4096" in out
    # And the closing line tells the user no commits were made.
    assert "already satisfied on HEAD" in out


def test_finish_no_work_run_logs_telemetry_when_present(leerie, tmp_path, capsys):
    """A no-work run still invoked the classifier + 1 planner per
    category (3-5 worker calls + tokens — non-trivial). Surface what
    it cost in the same shape as phase_finalize's `run weight: …`
    line so the user sees the cost on both paths."""
    st = _bootstrap_run_dir(tmp_path, leerie)
    # Bootstrap a realistic telemetry blob — matching the shape
    # State.add_telemetry produces (leerie.py:5672+).
    st.data["telemetry"] = {
        "calls": 4,
        "cost_usd": 0.83,
        "input_tokens": 12345,
        "output_tokens": 6789,
    }
    st.save()
    leerie._finish_no_work_run(st, {"bug-fixing": "no work needed"})
    out = capsys.readouterr().out
    # Same prefix and shape as phase_finalize's run-weight line so
    # downstream tooling (and humans) can parse both paths
    # identically.
    assert "run weight: 4 claude -p calls" in out
    assert "12,345 in" in out
    assert "6,789 out" in out


def test_finish_no_work_run_no_telemetry_log_when_absent(leerie, tmp_path, capsys):
    """If no telemetry was accumulated (e.g. an early code path or a
    test stub), the run-weight line is omitted — mirroring
    phase_finalize's `if tel:` guard at leerie.py:10954-10958."""
    st = _bootstrap_run_dir(tmp_path, leerie)
    # _bootstrap_run_dir does not set st.data["telemetry"].
    assert "telemetry" not in st.data
    leerie._finish_no_work_run(st, {"bug-fixing": "no work needed"})
    out = capsys.readouterr().out
    assert "run weight:" not in out
