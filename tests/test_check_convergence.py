"""Tests for check_convergence(), _write_heal_report(), phase_heal(),
HEAL_* constants, and importability.

Coverage:
  - check_convergence returns SUCCESS / PLATEAUED / TIMEOUT / BUDGET_EXHAUSTED /
    REGRESSED / CONTINUE under the correct conditions
  - _write_heal_report creates the expected file with best patch text and
    iteration count
  - phase_heal with a stubbed no-op _request_patch terminates with PLATEAUED
    after plateau_window iterations and writes the heal report
  - HEAL_* module constants have the documented default values
  - check_convergence, _write_heal_report, phase_heal are importable and callable
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _make_heal_state(leerie, tmp_path: Path, call_type: str = "classifier",
                     history: list | None = None,
                     baseline: dict | None = None,
                     best_so_far: dict | None = None):
    """Build a HealState with controlled field values (no file I/O)."""
    hs = leerie.HealState.__new__(leerie.HealState)
    hs.call_type = call_type
    hs.state_dir = tmp_path / call_type
    hs.path = hs.state_dir / "state.json"
    hs.failing_samples = []
    hs.history = list(history) if history is not None else []
    hs.baseline = dict(baseline) if baseline is not None else {}
    hs.best_so_far = dict(best_so_far) if best_so_far is not None else {}
    return hs


def _base_config(leerie, **overrides) -> dict:
    """Default config dict for check_convergence, overridable per test."""
    cfg = {
        "success_threshold": leerie.HEAL_SUCCESS_THRESHOLD_DEFAULT,
        "max_iterations": leerie.HEAL_MAX_ROUNDS_DEFAULT,
        "plateau_window": leerie.HEAL_PLATEAU_WINDOW_DEFAULT,
        "plateau_delta": leerie.HEAL_PLATEAU_DELTA_DEFAULT,
        "worker_count": 0,
        "max_total_workers": 999,
    }
    cfg.update(overrides)
    return cfg


# ---------------------------------------------------------------------------
# Criterion 9: importability
# ---------------------------------------------------------------------------

def test_convergence_symbols_importable(leerie):
    """check_convergence, _write_heal_report, phase_heal must be importable."""
    assert hasattr(leerie, "check_convergence"), \
        "check_convergence not in leerie"
    assert callable(leerie.check_convergence)
    assert hasattr(leerie, "_write_heal_report"), \
        "_write_heal_report not in leerie"
    assert callable(leerie._write_heal_report)
    assert hasattr(leerie, "phase_heal"), \
        "phase_heal not in leerie"
    assert callable(leerie.phase_heal)


# ---------------------------------------------------------------------------
# Criterion 10: HEAL_* constants
# ---------------------------------------------------------------------------

def test_heal_constants_exist(leerie):
    """HEAL_* convergence constants must exist with the documented defaults."""
    assert leerie.HEAL_MAX_ROUNDS_DEFAULT == 10
    assert leerie.HEAL_SUCCESS_THRESHOLD_DEFAULT == 0.9
    assert leerie.HEAL_PLATEAU_WINDOW_DEFAULT == 3
    assert leerie.HEAL_PLATEAU_DELTA_DEFAULT == 0.03
    assert leerie.HEAL_N_REPLAYS_DEFAULT == 5


# ---------------------------------------------------------------------------
# Criterion 1: SUCCESS verdict
# ---------------------------------------------------------------------------

def test_check_convergence_success(leerie, tmp_path):
    """Returns SUCCESS when best_so_far.pass_rate >= success_threshold."""
    hs = _make_heal_state(
        leerie, tmp_path,
        best_so_far={"pass_rate": 0.95, "iter_n": 1},
        baseline={"a": {"pass_rate": 0.5}},
        history=[{"iter_n": 1, "pass_rate": 0.95}],
    )
    config = _base_config(leerie)
    assert leerie.check_convergence(hs, config) == "SUCCESS"


def test_check_convergence_success_at_exact_threshold(leerie, tmp_path):
    """SUCCESS when pass_rate equals the threshold exactly."""
    hs = _make_heal_state(
        leerie, tmp_path,
        best_so_far={"pass_rate": 0.9, "iter_n": 1},
        baseline={"a": {"pass_rate": 0.5}},
        history=[{"iter_n": 1, "pass_rate": 0.9}],
    )
    config = _base_config(leerie)
    assert leerie.check_convergence(hs, config) == "SUCCESS"


# ---------------------------------------------------------------------------
# Criterion 2: PLATEAUED verdict
# ---------------------------------------------------------------------------

def test_check_convergence_plateaued(leerie, tmp_path):
    """Returns PLATEAUED when last plateau_window entries all have
    |delta| < plateau_delta (0.03)."""
    # plateau_window=3 → need at least 3 history entries
    # Use rates that are all close together (delta < 0.03)
    history = [
        {"iter_n": 1, "pass_rate": 0.50},
        {"iter_n": 2, "pass_rate": 0.51},  # delta=0.01 < 0.03
        {"iter_n": 3, "pass_rate": 0.52},  # delta=0.01 < 0.03
    ]
    hs = _make_heal_state(
        leerie, tmp_path,
        best_so_far={"pass_rate": 0.52, "iter_n": 3},
        baseline={"a": {"pass_rate": 0.50}},
        history=history,
    )
    config = _base_config(leerie)
    assert leerie.check_convergence(hs, config) == "PLATEAUED"


def test_check_convergence_not_plateaued_when_improving(leerie, tmp_path):
    """Does NOT return PLATEAUED when improvement delta exceeds plateau_delta."""
    history = [
        {"iter_n": 1, "pass_rate": 0.50},
        {"iter_n": 2, "pass_rate": 0.60},  # delta=0.10 >= 0.03
        {"iter_n": 3, "pass_rate": 0.70},  # delta=0.10 >= 0.03
    ]
    hs = _make_heal_state(
        leerie, tmp_path,
        best_so_far={"pass_rate": 0.70, "iter_n": 3},
        baseline={"a": {"pass_rate": 0.50}},
        history=history,
    )
    config = _base_config(leerie)
    result = leerie.check_convergence(hs, config)
    assert result != "PLATEAUED", f"Unexpected PLATEAUED: {result}"


# ---------------------------------------------------------------------------
# Criterion 3: TIMEOUT verdict
# ---------------------------------------------------------------------------

def test_check_convergence_timeout(leerie, tmp_path):
    """Returns TIMEOUT when len(history) >= max_iterations."""
    max_iter = 5
    history = [{"iter_n": i, "pass_rate": 0.50} for i in range(1, max_iter + 1)]
    hs = _make_heal_state(
        leerie, tmp_path,
        best_so_far={"pass_rate": 0.50, "iter_n": 5},
        baseline={"a": {"pass_rate": 0.40}},
        history=history,
    )
    config = _base_config(leerie, max_iterations=max_iter)
    assert leerie.check_convergence(hs, config) == "TIMEOUT"


def test_check_convergence_not_timeout_before_limit(leerie, tmp_path):
    """Does NOT return TIMEOUT when fewer than max_iterations entries."""
    hs = _make_heal_state(
        leerie, tmp_path,
        best_so_far={"pass_rate": 0.50, "iter_n": 1},
        baseline={"a": {"pass_rate": 0.40}},
        history=[{"iter_n": 1, "pass_rate": 0.50}],
    )
    config = _base_config(leerie, max_iterations=10)
    result = leerie.check_convergence(hs, config)
    assert result != "TIMEOUT", f"Should not be TIMEOUT: {result}"


# ---------------------------------------------------------------------------
# Criterion 4: BUDGET_EXHAUSTED verdict
# ---------------------------------------------------------------------------

def test_check_convergence_budget_exhausted(leerie, tmp_path):
    """Returns BUDGET_EXHAUSTED when worker_count >= max_total_workers."""
    hs = _make_heal_state(
        leerie, tmp_path,
        best_so_far={"pass_rate": 0.50, "iter_n": 1},
        baseline={"a": {"pass_rate": 0.40}},
        history=[{"iter_n": 1, "pass_rate": 0.50}],
    )
    config = _base_config(leerie, worker_count=10, max_total_workers=10)
    assert leerie.check_convergence(hs, config) == "BUDGET_EXHAUSTED"


def test_check_convergence_budget_not_exhausted(leerie, tmp_path):
    """Does NOT return BUDGET_EXHAUSTED when under cap."""
    hs = _make_heal_state(
        leerie, tmp_path,
        best_so_far={"pass_rate": 0.50, "iter_n": 1},
        baseline={"a": {"pass_rate": 0.40}},
        history=[{"iter_n": 1, "pass_rate": 0.50}],
    )
    config = _base_config(leerie, worker_count=9, max_total_workers=10)
    result = leerie.check_convergence(hs, config)
    assert result != "BUDGET_EXHAUSTED", f"Should not be BUDGET_EXHAUSTED: {result}"


# ---------------------------------------------------------------------------
# Criterion 5: REGRESSED verdict
# ---------------------------------------------------------------------------

def test_check_convergence_regressed(leerie, tmp_path):
    """Returns REGRESSED when every history entry pass_rate < baseline pass_rate."""
    baseline = {
        "a": {"pass_rate": 0.70},
        "b": {"pass_rate": 0.70},
    }
    # Both history entries are below baseline (avg 0.70)
    history = [
        {"iter_n": 1, "pass_rate": 0.60},
        {"iter_n": 2, "pass_rate": 0.65},
    ]
    hs = _make_heal_state(
        leerie, tmp_path,
        best_so_far={"pass_rate": 0.65, "iter_n": 2},
        baseline=baseline,
        history=history,
    )
    config = _base_config(leerie)
    assert leerie.check_convergence(hs, config) == "REGRESSED"


def test_check_convergence_not_regressed_when_one_improves(leerie, tmp_path):
    """Does NOT return REGRESSED when at least one history entry >= baseline."""
    baseline = {"a": {"pass_rate": 0.60}}
    history = [
        {"iter_n": 1, "pass_rate": 0.50},  # below
        {"iter_n": 2, "pass_rate": 0.70},  # above → not all below
    ]
    hs = _make_heal_state(
        leerie, tmp_path,
        best_so_far={"pass_rate": 0.70, "iter_n": 2},
        baseline=baseline,
        history=history,
    )
    config = _base_config(leerie)
    result = leerie.check_convergence(hs, config)
    assert result != "REGRESSED", f"Should not be REGRESSED: {result}"


def test_check_convergence_no_regression_without_history(leerie, tmp_path):
    """Does NOT return REGRESSED with empty history."""
    hs = _make_heal_state(
        leerie, tmp_path,
        best_so_far={"pass_rate": 0.0, "iter_n": 0},
        baseline={"a": {"pass_rate": 0.60}},
        history=[],
    )
    config = _base_config(leerie)
    result = leerie.check_convergence(hs, config)
    assert result != "REGRESSED"


# ---------------------------------------------------------------------------
# Criterion 6: CONTINUE verdict
# ---------------------------------------------------------------------------

def test_check_convergence_continue(leerie, tmp_path):
    """Returns CONTINUE when none of the terminal conditions are met."""
    history = [
        {"iter_n": 1, "pass_rate": 0.50},
        {"iter_n": 2, "pass_rate": 0.60},  # improving, so not plateau
    ]
    hs = _make_heal_state(
        leerie, tmp_path,
        best_so_far={"pass_rate": 0.60, "iter_n": 2},
        baseline={"a": {"pass_rate": 0.40}},
        history=history,
    )
    config = _base_config(leerie, max_iterations=10, worker_count=0,
                          max_total_workers=999)
    assert leerie.check_convergence(hs, config) == "CONTINUE"


def test_check_convergence_continue_with_no_history(leerie, tmp_path):
    """Returns CONTINUE immediately after baseline (empty history, no budget
    issue, pass_rate below threshold)."""
    hs = _make_heal_state(
        leerie, tmp_path,
        best_so_far={"pass_rate": 0.50, "iter_n": 0},
        baseline={"a": {"pass_rate": 0.50}},
        history=[],
    )
    config = _base_config(leerie)
    assert leerie.check_convergence(hs, config) == "CONTINUE"


# ---------------------------------------------------------------------------
# Criterion 7: _write_heal_report creates file
# ---------------------------------------------------------------------------

def test_write_heal_report_creates_file(leerie, tmp_path):
    """_write_heal_report creates healing-<call_type>.md in state_dir."""
    hs = _make_heal_state(
        leerie, tmp_path,
        call_type="classifier",
        best_so_far={"pass_rate": 0.80, "iter_n": 2},
        baseline={"a": {"pass_rate": 0.60}},
        history=[
            {"iter_n": 1, "pass_rate": 0.70},
            {"iter_n": 2, "pass_rate": 0.80},
        ],
    )
    path = leerie._write_heal_report("classifier", hs, "patch content here")
    assert path.exists(), f"Report not created at {path}"
    assert path.name == "healing-classifier.md"


def test_write_heal_report_contains_best_patch(leerie, tmp_path):
    """Report content contains the best patch text."""
    hs = _make_heal_state(
        leerie, tmp_path,
        call_type="planner",
        best_so_far={"pass_rate": 0.75, "iter_n": 1},
        baseline={"x": {"pass_rate": 0.50}},
        history=[{"iter_n": 1, "pass_rate": 0.75}],
    )
    patch = "THIS IS THE BEST PATCH CONTENT"
    path = leerie._write_heal_report("planner", hs, patch)
    content = path.read_text()
    assert patch in content, f"Best patch text not found in report:\n{content}"


def test_write_heal_report_contains_iteration_count(leerie, tmp_path):
    """Report content mentions the number of iterations run."""
    n_iters = 3
    history = [{"iter_n": i, "pass_rate": 0.5 + i * 0.05} for i in range(1, n_iters + 1)]
    hs = _make_heal_state(
        leerie, tmp_path,
        call_type="implementer",
        best_so_far={"pass_rate": 0.65, "iter_n": n_iters},
        baseline={"z": {"pass_rate": 0.40}},
        history=history,
    )
    path = leerie._write_heal_report("implementer", hs, "patch")
    content = path.read_text()
    assert str(n_iters) in content, (
        f"Iteration count {n_iters} not found in report:\n{content}")


def test_write_heal_report_no_patch_placeholder(leerie, tmp_path):
    """When no best patch text is given, report contains a placeholder."""
    hs = _make_heal_state(
        leerie, tmp_path,
        call_type="classifier",
        best_so_far={"pass_rate": 0.0, "iter_n": 0},
        baseline={},
        history=[],
    )
    path = leerie._write_heal_report("classifier", hs)
    content = path.read_text()
    assert "no patch" in content.lower() or "baseline" in content.lower(), (
        f"Expected placeholder in empty-patch report:\n{content}")


# ---------------------------------------------------------------------------
# Criterion 8: phase_heal with stub terminates with PLATEAUED
# ---------------------------------------------------------------------------

def _make_failing_records(n: int = 2) -> list[dict]:
    records = []
    for i in range(n):
        records.append({
            "call_id": f"fail-{i:04d}",
            "run_id": "test-phase-heal",
            "call_type": "classifier",
            "model": "opus",
            "system_prompt": f"Prompt {i} with ANCHOR_HERE marker.",
            "user_content": f"User input {i}.",
            "response_content": json.dumps({"categories": ["bug-fixing"]}),
            "parsed_ok": False,
            "input_tokens": 100,
            "output_tokens": 20,
            "latency_ms": 500,
            "success": False,
            "ts": "2026-01-01T00:00:00.000Z",
        })
    return records


_JUDGE_ENVELOPE = {
    "type": "result",
    "subtype": "success",
    "num_turns": 1,
    "total_cost_usd": 0.001,
    "is_error": False,
    "terminal_reason": "completed",
    "result": "{}",
    "structured_output": {
        "passed": True,
        "dimensions": {"schema_ok": True, "factual_ok": True, "hallucination_ok": True},
        "rationale": "OK",
        "suggested_fixes": [],
    },
    "usage": {"input_tokens": 100, "output_tokens": 20},
}

_REPLAY_ENVELOPE = {
    "type": "result",
    "subtype": "success",
    "num_turns": 1,
    "total_cost_usd": 0.001,
    "is_error": False,
    "terminal_reason": "completed",
    "result": json.dumps({"categories": ["bug-fixing"]}),
    "structured_output": {"categories": ["bug-fixing"]},
    "usage": {"input_tokens": 100, "output_tokens": 20},
}

_CAPS = {
    "worker_timeout_sec": 60,
    "max_total_workers": 99,
    "max_parallel": 4,
}

_MODELS = {"judge": "opus"}
_EFFORTS: dict[str, str | None] = {}


def _make_state(leerie, run_dir: Path):
    st = leerie.State.__new__(leerie.State)
    st.run_id = "test-phase-heal"
    st.run_dir = run_dir
    st.path = run_dir / "state.json"
    st.data = {
        "telemetry": {"calls": 0, "cost_usd": 0.0,
                      "input_tokens": 0, "output_tokens": 0},
        "verbosity": "quiet",
        "worker_count": 0,
    }
    run_dir.mkdir(parents=True, exist_ok=True)
    st.path.write_text("{}")
    return st


def _patch_network(leerie, monkeypatch):
    """Stub out all network I/O (replay + judge invoke)."""
    async def fake_replay(record, *, override_system_prompt=None, cwd=None):
        return (_REPLAY_ENVELOPE, {"categories": ["bug-fixing"]})

    monkeypatch.setattr(leerie, "_replay_capture", fake_replay)

    async def fake_invoke(cmd, cwd, timeout, sid, leerie_dir, verbosity,
                          progress=None, **_kw):
        return _JUDGE_ENVELOPE

    monkeypatch.setattr(leerie, "_invoke", fake_invoke)


def _no_op_request_patch(hs, iter_n: int):
    """Stub _request_patch that always returns a fixed no-op patch."""
    return ("ANCHOR_HERE", f"FIXED_PATCH_iter{iter_n}")


def test_phase_heal_stub_terminates_plateaued(leerie, tmp_path, monkeypatch):
    """phase_heal with a no-op stub terminates with PLATEAUED after
    plateau_window iterations (because the patch never improves pass_rate)."""
    run_dir = tmp_path / "run"
    heal_dir = tmp_path / "heal"
    st = _make_state(leerie, run_dir)
    records = _make_failing_records(2)
    _patch_network(leerie, monkeypatch)

    # Use a small plateau window and max_iterations so the test is fast.
    config = {
        "success_threshold": 0.99,   # very high → can't be SUCCESS with stub
        "max_iterations": 20,
        "plateau_window": 3,
        "plateau_delta": 0.03,
    }

    verdict = asyncio.run(
        leerie.phase_heal(
            "classifier", records, heal_dir, _CAPS, st, _MODELS, _EFFORTS,
            _no_op_request_patch, n=1, config=config,
        )
    )

    # Stub always returns passed=True → pass_rate constant → plateaued.
    # Or SUCCESS if pass_rate >= threshold — but 0.99 threshold prevents that
    # when baseline already passes (judge returns passed=True for all replays).
    # The verdict must be terminal (not CONTINUE).
    assert verdict in ("PLATEAUED", "SUCCESS", "TIMEOUT", "BUDGET_EXHAUSTED",
                       "REGRESSED"), (
        f"Expected a terminal verdict, got: {verdict!r}")


def test_phase_heal_stub_writes_report(leerie, tmp_path, monkeypatch):
    """phase_heal writes a heal report regardless of the termination reason."""
    run_dir = tmp_path / "run"
    heal_dir = tmp_path / "heal"
    st = _make_state(leerie, run_dir)
    records = _make_failing_records(2)
    _patch_network(leerie, monkeypatch)

    config = {
        "success_threshold": 0.99,
        "max_iterations": 5,
        "plateau_window": 3,
        "plateau_delta": 0.03,
    }

    asyncio.run(
        leerie.phase_heal(
            "classifier", records, heal_dir, _CAPS, st, _MODELS, _EFFORTS,
            _no_op_request_patch, n=1, config=config,
        )
    )

    report_path = heal_dir / "classifier" / "healing-classifier.md"
    assert report_path.exists(), (
        f"Heal report not written at {report_path}")
    content = report_path.read_text()
    assert "# Heal report" in content, "Report missing header"
    assert "Iterations" in content, "Report missing iteration count"
