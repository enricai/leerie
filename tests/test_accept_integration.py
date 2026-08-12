"""Tests for the accept-integration launcher verb (local path).

Mirrors tests/test_accept_blocked.py's local-path harness exactly — the
verb has the identical shape (same allowlist validation, same
ACCEPTED:/NOOP:/ERROR: sentinel convention, same local/Fly/EC2
dispatch) but mutates a different field: `state.data["integration_gate"]
[sid]["accepted"]`, flipped so `integrate_wave`'s resume-time gate check
(orchestrator/leerie.py) stops re-invoking `integration_judge` for that
sid and treats the already-committed merge as fine to advance past.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _run_accept(state_path: Path, sid: str) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items()}
    env["LEERIE_STATE_DIR"] = str(state_path.parent.parent.parent)
    run_id = state_path.parent.name
    return subprocess.run(
        [str(REPO_ROOT / "leerie"), "accept-integration", run_id, sid,
         "--runtime", "local"],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _make_state(tmp_path: Path, integration_gate: dict,
                integration_defects: dict | None = None) -> Path:
    run_dir = tmp_path / "runs" / "test-run-001"
    run_dir.mkdir(parents=True)
    state = {"integration_gate": integration_gate}
    if integration_defects is not None:
        state["integration_defects"] = integration_defects
    state_file = run_dir / "state.json"
    state_file.write_text(json.dumps(state, indent=2))
    return state_file


def test_accepts_a_rejected_finding(tmp_path):
    sf = _make_state(
        tmp_path,
        {"feat-001": {"defects": ["INTEGRATION_DEFECT dropped call site"],
                      "advisories": [], "merge_commit_sha": "abc123",
                      "accepted": False}},
        integration_defects={"feat-001": ["INTEGRATION_DEFECT dropped call site"]},
    )
    r = _run_accept(sf, "feat-001")
    assert r.returncode == 0, r.stderr
    st = json.loads(sf.read_text())
    assert st["integration_gate"]["feat-001"]["accepted"] is True
    assert "feat-001" not in st.get("integration_defects", {})


def test_cleans_up_empty_integration_defects_dict(tmp_path):
    sf = _make_state(
        tmp_path,
        {"feat-001": {"defects": ["x"], "advisories": [],
                      "merge_commit_sha": "abc123", "accepted": False}},
        integration_defects={"feat-001": ["x"]},
    )
    r = _run_accept(sf, "feat-001")
    assert r.returncode == 0, r.stderr
    st = json.loads(sf.read_text())
    assert "integration_defects" not in st


def test_preserves_other_defect_entries(tmp_path):
    sf = _make_state(
        tmp_path,
        {"feat-001": {"defects": ["x"], "advisories": [],
                      "merge_commit_sha": "a", "accepted": False},
         "feat-002": {"defects": ["y"], "advisories": [],
                      "merge_commit_sha": "b", "accepted": False}},
        integration_defects={"feat-001": ["x"], "feat-002": ["y"]},
    )
    r = _run_accept(sf, "feat-001")
    assert r.returncode == 0, r.stderr
    st = json.loads(sf.read_text())
    assert st["integration_gate"]["feat-001"]["accepted"] is True
    assert st["integration_gate"]["feat-002"]["accepted"] is False
    assert st["integration_defects"] == {"feat-002": ["y"]}


def test_noop_on_already_accepted(tmp_path):
    sf = _make_state(
        tmp_path,
        {"feat-001": {"defects": [], "advisories": [],
                      "merge_commit_sha": "abc123", "accepted": True}})
    r = _run_accept(sf, "feat-001")
    assert r.returncode == 0
    assert "already accepted" in r.stdout


def test_errors_on_unknown_subtask(tmp_path):
    sf = _make_state(
        tmp_path,
        {"feat-001": {"defects": ["x"], "advisories": [],
                      "merge_commit_sha": "a", "accepted": False}})
    r = _run_accept(sf, "nonexistent")
    assert r.returncode != 0
    assert "not found" in r.stderr


def test_errors_on_missing_state_file(tmp_path):
    run_dir = tmp_path / "runs" / "test-run-001"
    run_dir.mkdir(parents=True)
    sf = run_dir / "state.json"
    r = _run_accept(sf, "feat-001")
    assert r.returncode != 0
    assert "no state.json" in r.stderr


def test_rejects_invalid_sid_before_touching_filesystem(tmp_path):
    sf = _make_state(
        tmp_path,
        {"feat-001": {"defects": ["x"], "advisories": [],
                      "merge_commit_sha": "a", "accepted": False}})
    r = _run_accept(sf, "feat/../001")
    assert r.returncode != 0
    assert "invalid subtask-id" in r.stderr
    # state.json must be untouched
    st = json.loads(sf.read_text())
    assert st["integration_gate"]["feat-001"]["accepted"] is False
