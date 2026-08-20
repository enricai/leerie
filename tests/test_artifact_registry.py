"""Tests for the artifact_registry worker + phase (DESIGN §5 *Artifact-registry
worker*) — the pre-planning shared-vocabulary aid (Fix 4).

Covers:
  - SCHEMAS["artifact_registry"] structural contract (required fields, item
    shape, minLength guards, valid/invalid acceptance)
  - worker registration (WORKER_TYPES; absent from MODEL_DEFAULT_PER_WORKER so
    it resolves sonnet; EFFORT_DEFAULT_PER_WORKER entry "medium"; prompt exists)
  - model/effort resolution precedence
  - phase_artifact_registry behavior: returns the artifacts list, drops
    malformed items, degrades to [] on worker crash, is best-effort (no die)
  - phase_plan injects a non-empty registry into every planner's ctx and omits
    it when empty
  - the artifact_registry state key round-trips through State.save()/load()
  - source-coupling: the checkpoint block persists after computing, before
    plans_after_plan

Mirrors test_fit_judge_schema.py / test_resolve_fit_judge_model.py patterns.
"""
from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

from tests.conftest import _run

try:
    import jsonschema  # type: ignore
    HAS_JSONSCHEMA = True
except ImportError:
    HAS_JSONSCHEMA = False


# --------------------------------------------------------------------------
# schema
# --------------------------------------------------------------------------

def _validate(leerie, instance: dict) -> None:
    schema = leerie.SCHEMAS["artifact_registry"]
    if HAS_JSONSCHEMA:
        jsonschema.validate(instance, schema)
        return
    for k in schema["required"]:
        assert k in instance, f"missing required field {k!r}"
    assert isinstance(instance["artifacts"], list)
    for a in instance["artifacts"]:
        for k in ("description", "tag", "path"):
            assert k in a and isinstance(a[k], str) and a[k]


def test_schema_exists_and_shape(leerie):
    assert "artifact_registry" in leerie.SCHEMAS
    schema = leerie.SCHEMAS["artifact_registry"]
    assert schema["type"] == "object"
    assert schema["required"] == ["artifacts"]
    item = schema["properties"]["artifacts"]["items"]
    assert set(item["required"]) == {"description", "tag", "path"}
    for k in ("description", "tag", "path"):
        assert item["properties"][k].get("minLength") == 1


def test_schema_accepts_good_instance(leerie):
    _validate(leerie, {"artifacts": [
        {"description": "scroll-reveal hook", "tag": "scroll-reveal-hook",
         "path": "src/hooks/use-scroll-reveal.ts"},
    ]})


def test_schema_accepts_empty_artifacts(leerie):
    _validate(leerie, {"artifacts": []})


@pytest.mark.skipif(not HAS_JSONSCHEMA, reason="needs a validator")
def test_schema_rejects_missing_tag(leerie):
    with pytest.raises(jsonschema.ValidationError):
        _validate(leerie, {"artifacts": [
            {"description": "x", "path": "src/x.ts"}]})


@pytest.mark.skipif(not HAS_JSONSCHEMA, reason="needs a validator")
def test_schema_rejects_empty_string_path(leerie):
    with pytest.raises(jsonschema.ValidationError):
        _validate(leerie, {"artifacts": [
            {"description": "x", "tag": "t", "path": ""}]})


# --------------------------------------------------------------------------
# registration
# --------------------------------------------------------------------------

def test_in_worker_types(leerie):
    assert "artifact_registry" in leerie.WORKER_TYPES


def test_absent_from_model_default_per_worker(leerie):
    # → resolves to sonnet via the global MODEL_DEFAULT fallback.
    assert "artifact_registry" not in leerie.MODEL_DEFAULT_PER_WORKER


def test_effort_default_is_medium(leerie):
    assert leerie.EFFORT_DEFAULT_PER_WORKER["artifact_registry"] == "medium"


def test_prompt_file_exists(leerie):
    root = Path(inspect.getfile(leerie)).resolve().parent.parent
    assert (root / "prompts" / "artifact_registry.md").is_file()


def test_state_field_declared(leerie):
    assert "artifact_registry" in leerie.STATE_FIELDS


# --------------------------------------------------------------------------
# model / effort resolution
# --------------------------------------------------------------------------

@pytest.fixture
def repo_root(tmp_path):
    return tmp_path


def _ns(leerie, **over):
    import argparse
    base = {f"model_{w}": None for w in leerie.WORKER_TYPES}
    base.update({f"effort_{w}": None for w in leerie.WORKER_TYPES})
    base.update(model=None, effort=None)
    base.update(over)
    return argparse.Namespace(**base)


def test_model_default_is_sonnet(leerie, repo_root):
    models = leerie.resolve_models(repo_root, _ns(leerie))
    assert models["artifact_registry"] == leerie.MODEL_DEFAULT == "sonnet"


def test_model_per_worker_cli(leerie, repo_root):
    models = leerie.resolve_models(
        repo_root, _ns(leerie, model_artifact_registry="haiku"))
    assert models["artifact_registry"] == "haiku"


def test_effort_default_medium_resolved(leerie, repo_root):
    efforts = leerie.resolve_efforts(repo_root, _ns(leerie))
    assert efforts["artifact_registry"] == "medium"


# --------------------------------------------------------------------------
# phase behavior
# --------------------------------------------------------------------------

_CAPS = {"max_parallel": 4, "max_total_workers": 999, "judgment_check_rounds": 3}
_MODELS = {"artifact_registry": "sonnet"}
_EFFORTS = {"artifact_registry": "medium"}


def _make_state(leerie, run_dir: Path):
    st = leerie.State.__new__(leerie.State)
    st.run_id = "test-artifact-registry"
    st.run_dir = run_dir
    st.path = run_dir / "state.json"
    st.leerie_root = run_dir / "leerie-root"
    st.data = {"telemetry": {"calls": 0, "cost_usd": 0.0,
                             "input_tokens": 0, "output_tokens": 0},
               "verbosity": "quiet", "worker_count": 0,
               "skip_repo_map": True,
               # Judgment workers now run in a disposable worktree
               # (DESIGN §12 *Judgment-worker isolation*); `_judgment_cwd`
               # raises rather than silently falling back to the real
               # checkout, so every State fixture must seed the path.
               "planning_worktree": str(run_dir / "worktrees" / "planning")}
    run_dir.mkdir(parents=True, exist_ok=True)
    st.path.write_text("{}")
    return st



def test_phase_returns_artifacts(leerie, tmp_path, monkeypatch):
    st = _make_state(leerie, tmp_path / "run")

    async def fake_claude_p(**_kw):
        return {"artifacts": [
            {"description": "hook", "tag": "io-hook", "path": "src/io.ts"},
        ]}
    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    out = _run(leerie.phase_artifact_registry(
        "task", st, _CAPS, _MODELS, _EFFORTS))
    assert out == [{"description": "hook", "tag": "io-hook",
                    "path": "src/io.ts"}]


def test_phase_drops_malformed_items(leerie, tmp_path, monkeypatch):
    st = _make_state(leerie, tmp_path / "run")

    async def fake_claude_p(**_kw):
        return {"artifacts": [
            {"description": "ok", "tag": "t", "path": "p"},
            {"description": "no-tag", "path": "p2"},   # dropped (no tag)
            {"description": "no-path", "tag": "t3"},    # dropped (no path)
        ]}
    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    out = _run(leerie.phase_artifact_registry(
        "task", st, _CAPS, _MODELS, _EFFORTS))
    assert [a["tag"] for a in out] == ["t"]


def test_phase_degrades_to_empty_on_crash(leerie, tmp_path, monkeypatch):
    """Worker crashes every round → returns [] (non-fatal, no die)."""
    st = _make_state(leerie, tmp_path / "run")

    async def crashing(**_kw):
        raise leerie.WorkerError("boom")
    monkeypatch.setattr(leerie, "claude_p", crashing)
    out = _run(leerie.phase_artifact_registry(
        "task", st, _CAPS, _MODELS, _EFFORTS))
    assert out == []


# --------------------------------------------------------------------------
# repo-map grounding: --skip-repo-map interaction (the one behavioral branch
# every existing phase-behavior test above never exercises, since
# _make_state always seeds skip_repo_map=True)
# --------------------------------------------------------------------------

def _make_state_repo_map_enabled(leerie, run_dir: Path):
    st = _make_state(leerie, run_dir)
    st.data["skip_repo_map"] = False
    return st


def test_phase_builds_repo_map_when_not_skipped(leerie, tmp_path, monkeypatch):
    """skip_repo_map=False → _build_repo_map/_rank_repo_map are called, and a
    non-empty ranked map is folded into the ctx JSON handed to the worker."""
    st = _make_state_repo_map_enabled(leerie, tmp_path / "run")
    calls = []

    def fake_build(repo_root, leerie_root):
        calls.append((repo_root, leerie_root))
        return {"fake": "graph"}

    def fake_rank(repo_map, seed_files, seed_symbols):
        assert repo_map == {"fake": "graph"}
        return "ranked-repo-map-text"

    captured = {}

    async def fake_claude_p(**kw):
        captured["user_prompt"] = kw["user_prompt"]
        return {"artifacts": []}

    monkeypatch.setattr(leerie, "_build_repo_map", fake_build)
    monkeypatch.setattr(leerie, "_rank_repo_map", fake_rank)
    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    _run(leerie.phase_artifact_registry("task", st, _CAPS, _MODELS, _EFFORTS))
    assert len(calls) == 1
    assert calls[0][0] == Path(__import__("os").getcwd())
    assert calls[0][1] == st.leerie_root
    assert "ranked-repo-map-text" in captured["user_prompt"]


def test_phase_skips_repo_map_build_when_skipped(leerie, tmp_path, monkeypatch):
    """skip_repo_map=True (the default in _make_state) → _build_repo_map is
    never called at all."""
    st = _make_state(leerie, tmp_path / "run")
    called = []
    monkeypatch.setattr(
        leerie, "_build_repo_map",
        lambda *a, **k: called.append(1) or {})

    async def fake_claude_p(**_kw):
        return {"artifacts": []}
    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    _run(leerie.phase_artifact_registry("task", st, _CAPS, _MODELS, _EFFORTS))
    assert called == []


def test_phase_omits_repo_map_key_when_ranked_empty(leerie, tmp_path, monkeypatch):
    """An empty ranked map (e.g. no source files) must not add a `repo_map`
    key to the ctx JSON — mirrors phase_plan's own degrade."""
    st = _make_state_repo_map_enabled(leerie, tmp_path / "run")
    monkeypatch.setattr(leerie, "_build_repo_map", lambda *a, **k: {})
    monkeypatch.setattr(leerie, "_rank_repo_map", lambda *a, **k: "")

    captured = {}

    async def fake_claude_p(**kw):
        captured["user_prompt"] = kw["user_prompt"]
        return {"artifacts": []}
    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    _run(leerie.phase_artifact_registry("task", st, _CAPS, _MODELS, _EFFORTS))
    assert '"repo_map"' not in captured["user_prompt"]


def test_phase_degrades_silently_on_repo_map_exception(leerie, tmp_path, monkeypatch):
    """A crashing _build_repo_map must not propagate — the phase degrades
    silently and the worker still runs on the task alone (never die()s)."""
    st = _make_state_repo_map_enabled(leerie, tmp_path / "run")

    def raising(*_a, **_k):
        raise RuntimeError("tree-sitter blew up")
    monkeypatch.setattr(leerie, "_build_repo_map", raising)

    captured = {}

    async def fake_claude_p(**kw):
        captured["user_prompt"] = kw["user_prompt"]
        return {"artifacts": [
            {"description": "hook", "tag": "io-hook", "path": "src/io.ts"}]}
    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    out = _run(leerie.phase_artifact_registry(
        "task", st, _CAPS, _MODELS, _EFFORTS))
    assert out == [{"description": "hook", "tag": "io-hook",
                    "path": "src/io.ts"}]
    assert '"repo_map"' not in captured["user_prompt"]


# --------------------------------------------------------------------------
# injection wiring + resume round-trip
# --------------------------------------------------------------------------

def test_phase_plan_injects_registry_into_ctx(leerie):
    """Source-coupling: phase_plan reads st.data['artifact_registry'] and adds
    it to ctx_dict only when non-empty."""
    src = inspect.getsource(leerie.phase_plan)
    assert 'st.data.get("artifact_registry")' in src
    assert 'ctx_dict["artifact_registry"]' in src


def test_run_phases_checkpoints_registry_before_plan(leerie):
    """Source-coupling: the registry checkpoint computes + saves after classify
    and before the plans_after_plan block (resume-cursor ordering)."""
    src = inspect.getsource(leerie._run_phases)
    i_reg = src.index('"artifact_registry" not in st.data')
    i_reg_save = src.index("st.data[\"artifact_registry\"] =")
    i_plan = src.index('"plans_after_plan" not in st.data')
    assert i_reg < i_plan
    assert i_reg_save < i_plan
    # gather_answers (end of classify block) precedes the registry checkpoint.
    assert src.index("gather_answers(st, supplied)") < i_reg


def test_state_key_round_trips(leerie, tmp_path):
    st = _make_state(leerie, tmp_path / "run")
    st.data["artifact_registry"] = [
        {"description": "hook", "tag": "io-hook", "path": "src/io.ts"}]
    st.save()
    reloaded = json.loads(st.path.read_text())
    assert reloaded["artifact_registry"] == st.data["artifact_registry"]
