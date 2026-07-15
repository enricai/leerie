"""Tests for filter_satisfied_subtasks() — the phase-3 per-subtask
already-satisfied gate (DESIGN §8 *Already-satisfied subtask elimination*).

Covers:
  - a subtask the probe marks satisfied is removed from `plans` and
    recorded in state.data["dropped_subtasks"] with reason
    "already_satisfied" + evidence
  - an unsatisfied subtask survives
  - all-satisfied → returns a no_work_map (domain → basis) so the caller
    routes to _finish_no_work_run; the map's basis is drop-derived, NOT
    the planner's confidence.basis
  - a blocked plan with 0 subtasks does NOT trigger the no-work route
  - --skip-satisfied-check short-circuits (no probe, no drops, returns None)
  - a subtask with no success_criteria_seed is never probed (survives)
  - a probe crash (WorkerError) fails safe — subtask survives, no drop
  - SCHEMAS["satisfied_probe"] validates good / rejects malformed payloads

The schema tests use a HAS_JSONSCHEMA gate with a manual structural fallback
(mirroring test_dep_capture_schema.py / test_fit_judge_schema.py) so CI
without jsonschema installed still catches drift — it is not a declared
dependency.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

try:
    import jsonschema  # type: ignore
    HAS_JSONSCHEMA = True
except ImportError:
    HAS_JSONSCHEMA = False


_CAPS = {"max_parallel": 4, "max_total_workers": 999}
_MODELS = {"satisfied_probe": "sonnet"}
_EFFORTS = {"satisfied_probe": None}


def _make_state(leerie, run_dir: Path):
    st = leerie.State.__new__(leerie.State)
    st.run_id = "test-run-satisfied"
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


def _sub(sid, **kw):
    s = {"id": sid, "title": sid, "success_criteria_seed": f"{sid} done"}
    s.update(kw)
    return s


def _patch_probe(leerie, monkeypatch, verdicts: dict):
    """Patch claude_p so each subtask sid returns verdicts[sid] (a dict
    with at least {satisfied}). A sid mapped to the sentinel "CRASH"
    raises WorkerError."""
    async def fake_claude_p(*, user_prompt, sid, **_kw):
        # sid is like "satisfied_probe-<subtask-id>"
        stid = sid.split("satisfied_probe-", 1)[-1]
        v = verdicts.get(stid, {"satisfied": False, "evidence": "n/a"})
        if v == "CRASH":
            raise leerie.WorkerError("probe boom")
        return v

    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# drop / survive
# ---------------------------------------------------------------------------

def test_satisfied_subtask_dropped_and_recorded(leerie, tmp_path, monkeypatch):
    st = _make_state(leerie, tmp_path / "run")
    plans = [{"domain": "feature-implementation", "status": "ready",
              "subtasks": [_sub("feat-001"), _sub("feat-002")]}]
    _patch_probe(leerie, monkeypatch, {
        "feat-001": {"satisfied": True, "evidence": "already on HEAD",
                     "checked": ["a.py"]},
        "feat-002": {"satisfied": False, "evidence": "missing"},
    })
    res = _run(leerie.filter_satisfied_subtasks(
        plans, tmp_path, st, _CAPS, _MODELS, _EFFORTS))
    # not all dropped → None (proceed to schedule)
    assert res is None
    surviving = [s["id"] for s in plans[0]["subtasks"]]
    assert surviving == ["feat-002"]
    dropped = st.data["dropped_subtasks"]
    assert "feat-001" in dropped
    assert dropped["feat-001"]["reason"] == "already_satisfied"
    assert dropped["feat-001"]["evidence"] == "already on HEAD"
    assert "feat-002" not in dropped


def test_unsatisfied_all_survive_no_drop_key(leerie, tmp_path, monkeypatch):
    st = _make_state(leerie, tmp_path / "run")
    plans = [{"domain": "d", "status": "ready",
              "subtasks": [_sub("feat-001"), _sub("feat-002")]}]
    _patch_probe(leerie, monkeypatch, {
        "feat-001": {"satisfied": False, "evidence": "x"},
        "feat-002": {"satisfied": False, "evidence": "y"},
    })
    res = _run(leerie.filter_satisfied_subtasks(
        plans, tmp_path, st, _CAPS, _MODELS, _EFFORTS))
    assert res is None
    assert len(plans[0]["subtasks"]) == 2
    assert "dropped_subtasks" not in st.data


# ---------------------------------------------------------------------------
# all-dropped → no_work_map routing
# ---------------------------------------------------------------------------

def test_all_satisfied_returns_no_work_map(leerie, tmp_path, monkeypatch):
    st = _make_state(leerie, tmp_path / "run")
    plans = [{"domain": "feature-implementation", "status": "ready",
              # confidence.basis is the planner's "I found work" rationale;
              # the returned map must NOT echo it.
              "confidence": {"basis": "I found real work to do"},
              "subtasks": [_sub("feat-001"), _sub("feat-002")]}]
    _patch_probe(leerie, monkeypatch, {
        "feat-001": {"satisfied": True, "evidence": "done"},
        "feat-002": {"satisfied": True, "evidence": "done"},
    })
    res = _run(leerie.filter_satisfied_subtasks(
        plans, tmp_path, st, _CAPS, _MODELS, _EFFORTS))
    assert res is not None
    assert "feature-implementation" in res
    basis = res["feature-implementation"]
    assert "already satisfied" in basis.lower()
    assert basis != "I found real work to do"
    assert plans[0]["subtasks"] == []


def test_blocked_empty_plan_does_not_route_no_work(leerie, tmp_path,
                                                   monkeypatch):
    # A ready plan fully dropped, but another plan is blocked with 0
    # subtasks → must NOT route to no-work (blocked must reach schedule's
    # all-blocked die). Mirrors detect_no_work's ready-only guard.
    st = _make_state(leerie, tmp_path / "run")
    plans = [
        {"domain": "feature-implementation", "status": "ready",
         "subtasks": [_sub("feat-001")]},
        {"domain": "testing", "status": "blocked", "subtasks": []},
    ]
    _patch_probe(leerie, monkeypatch,
                 {"feat-001": {"satisfied": True, "evidence": "done"}})
    res = _run(leerie.filter_satisfied_subtasks(
        plans, tmp_path, st, _CAPS, _MODELS, _EFFORTS))
    assert res is None  # blocked plan present → no no-work route


# ---------------------------------------------------------------------------
# skip flag / no-criteria / crash-safety
# ---------------------------------------------------------------------------

def test_skip_flag_short_circuits(leerie, tmp_path, monkeypatch):
    st = _make_state(leerie, tmp_path / "run")
    st.data["skip_satisfied_check"] = True
    plans = [{"domain": "d", "status": "ready",
              "subtasks": [_sub("feat-001")]}]

    async def boom(**_kw):
        raise AssertionError("probe must not be spawned when skip is set")
    monkeypatch.setattr(leerie, "claude_p", boom)

    res = _run(leerie.filter_satisfied_subtasks(
        plans, tmp_path, st, _CAPS, _MODELS, _EFFORTS))
    assert res is None
    assert len(plans[0]["subtasks"]) == 1


def test_no_criteria_subtask_never_probed(leerie, tmp_path, monkeypatch):
    st = _make_state(leerie, tmp_path / "run")
    plans = [{"domain": "d", "status": "ready",
              "subtasks": [{"id": "feat-001", "title": "no crit",
                            "success_criteria_seed": "  "}]}]

    async def boom(**_kw):
        raise AssertionError("subtask with blank criteria must not be probed")
    monkeypatch.setattr(leerie, "claude_p", boom)

    res = _run(leerie.filter_satisfied_subtasks(
        plans, tmp_path, st, _CAPS, _MODELS, _EFFORTS))
    assert res is None
    assert len(plans[0]["subtasks"]) == 1


def test_probe_crash_fails_safe_keeps_subtask(leerie, tmp_path, monkeypatch):
    st = _make_state(leerie, tmp_path / "run")
    plans = [{"domain": "d", "status": "ready",
              "subtasks": [_sub("feat-001"), _sub("feat-002")]}]
    _patch_probe(leerie, monkeypatch, {
        "feat-001": "CRASH",
        "feat-002": {"satisfied": True, "evidence": "done"},
    })
    res = _run(leerie.filter_satisfied_subtasks(
        plans, tmp_path, st, _CAPS, _MODELS, _EFFORTS))
    assert res is None
    surviving = [s["id"] for s in plans[0]["subtasks"]]
    # feat-001 crashed → kept (fail-safe); feat-002 satisfied → dropped
    assert surviving == ["feat-001"]
    assert "feat-002" in st.data["dropped_subtasks"]
    assert "feat-001" not in st.data.get("dropped_subtasks", {})


def test_budget_exhaustion_propagates_not_swallowed(leerie, tmp_path,
                                                    monkeypatch):
    """bump_workers' WorkerError (budget exhaustion) is the hard backstop —
    it must propagate OUT of filter_satisfied_subtasks, NOT be swallowed by
    the probe-crash fail-safe. Otherwise a run past its worker budget would
    silently continue. (Contrast: a claude_p crash IS caught → subtask
    survives.)"""
    st = _make_state(leerie, tmp_path / "run")
    plans = [{"domain": "d", "status": "ready",
              "subtasks": [_sub("feat-001"), _sub("feat-002")]}]
    # max_total_workers=0 → the first bump_workers() raises WorkerError.
    caps = {"max_parallel": 4, "max_total_workers": 0}
    # claude_p should never be reached (bump_workers raises first); make it
    # loud if it is.
    async def unreached(**_kw):
        raise AssertionError("claude_p reached despite budget exhaustion")
    monkeypatch.setattr(leerie, "claude_p", unreached)

    with pytest.raises(leerie.WorkerError):
        _run(leerie.filter_satisfied_subtasks(
            plans, tmp_path, st, caps, _MODELS, _EFFORTS))
    # No drops recorded — the run aborts rather than silently keeping/dropping.
    assert "dropped_subtasks" not in st.data


# ---------------------------------------------------------------------------
# schema
# ---------------------------------------------------------------------------

def _validate(leerie, instance: dict) -> None:
    """Validate using jsonschema when available; otherwise fall back to
    structural assertions that mirror what the schema declares. Tests must
    pass in both modes so CI without jsonschema installed still catches
    drift."""
    schema = leerie.SCHEMAS["satisfied_probe"]
    if HAS_JSONSCHEMA:
        jsonschema.validate(instance, schema)
        return
    for k in schema["required"]:
        assert k in instance, f"missing required field {k!r}"
    assert isinstance(instance["satisfied"], bool)
    assert isinstance(instance["evidence"], str)
    if "checked" in instance:
        assert isinstance(instance["checked"], list)
        for path in instance["checked"]:
            assert isinstance(path, str)


def test_schema_accepts_good_payload(leerie):
    _validate(leerie, {"satisfied": True, "evidence": "cited",
                       "checked": ["a.py"]})
    # checked is optional
    _validate(leerie, {"satisfied": False, "evidence": "missing"})


def test_schema_rejects_malformed(leerie):
    if not HAS_JSONSCHEMA:
        pytest.skip("jsonschema not available; rejection requires a validator")
    schema = leerie.SCHEMAS["satisfied_probe"]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"evidence": "no satisfied field"}, schema)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"satisfied": "yes", "evidence": "x"}, schema)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(
            {"satisfied": True, "evidence": "x", "extra": 1}, schema)
