"""Tests for fit_judge and splitter model and effort resolution.

fit_judge and splitter are main-loop judgment workers in WORKER_TYPES.
They follow the standard per-worker model/effort resolution chain:
  1. --model-fit_judge / --model-splitter CLI flag
  2. --model CLI flag (global)
  3. LEERIE_MODEL_FIT_JUDGE / LEERIE_MODEL_SPLITTER env var
  4. LEERIE_MODEL env var
  5. model_fit_judge / model_splitter in leerie.toml
  6. model in leerie.toml
  7. MODEL_DEFAULT_PER_WORKER[...] — absent for both → falls to MODEL_DEFAULT
  8. MODEL_DEFAULT ("opus")

Effort for both (judgment workers):
  1. --effort-fit_judge / --effort-splitter CLI flag
  2. --effort CLI flag (global)
  3. LEERIE_EFFORT_FIT_JUDGE / LEERIE_EFFORT_SPLITTER env var
  4. LEERIE_EFFORT env var
  5. effort_fit_judge / effort_splitter in leerie.toml
  6. effort in leerie.toml
  7. EFFORT_DEFAULT_PER_WORKER["fit_judge"] / ["splitter"] → "high"

Mirrors test_resolve_dep_capture_model.py fixture patterns.
"""
from __future__ import annotations

import argparse

import pytest


# The full WORKER_TYPES including the new workers.
_WORKER_TYPES = ("classifier", "planner", "reconciler", "plan_overlap_judge",
                 "satisfied_probe", "provision", "implementer", "integrator",
                 "conformer", "fit_judge", "splitter")


def ns(**overrides):
    """Build an argparse.Namespace matching resolve_models / resolve_efforts
    expectations: global model/effort and per-worker model_<w>/effort_<w>
    for all WORKER_TYPES including fit_judge and splitter."""
    base: dict = {
        "model": None,
        "effort": None,
        **{f"model_{w}": None for w in _WORKER_TYPES},
        **{f"effort_{w}": None for w in _WORKER_TYPES},
    }
    base.update(overrides)
    return argparse.Namespace(**base)


@pytest.fixture
def repo_root(tmp_path, monkeypatch):
    """Empty repo root with all LEERIE_MODEL* and LEERIE_EFFORT* env vars unset."""
    monkeypatch.delenv("LEERIE_MODEL", raising=False)
    monkeypatch.delenv("LEERIE_EFFORT", raising=False)
    for w in _WORKER_TYPES:
        monkeypatch.delenv(f"LEERIE_MODEL_{w.upper()}", raising=False)
        monkeypatch.delenv(f"LEERIE_EFFORT_{w.upper()}", raising=False)
    return tmp_path


# ---------------------------------------------------------------------------
# fit_judge — model defaults
# ---------------------------------------------------------------------------

def test_fit_judge_model_default_is_opus(leerie, repo_root):
    """fit_judge is absent from MODEL_DEFAULT_PER_WORKER → falls to MODEL_DEFAULT
    ('opus'), the judgment-worker global."""
    models = leerie.resolve_models(repo_root, ns())
    assert models["fit_judge"] == "opus"
    assert "fit_judge" not in leerie.MODEL_DEFAULT_PER_WORKER
    assert leerie.MODEL_DEFAULT == "opus"


def test_fit_judge_model_per_worker_cli(leerie, repo_root):
    models = leerie.resolve_models(repo_root, ns(model_fit_judge="haiku"))
    assert models["fit_judge"] == "haiku"


def test_fit_judge_model_global_cli_beats_default(leerie, repo_root):
    models = leerie.resolve_models(repo_root, ns(model="sonnet"))
    assert models["fit_judge"] == "sonnet"


def test_fit_judge_model_per_cli_beats_global_cli(leerie, repo_root):
    models = leerie.resolve_models(
        repo_root, ns(model="sonnet", model_fit_judge="haiku"))
    assert models["fit_judge"] == "haiku"


def test_fit_judge_model_per_env_beats_global_env(leerie, repo_root, monkeypatch):
    monkeypatch.setenv("LEERIE_MODEL", "haiku")
    monkeypatch.setenv("LEERIE_MODEL_FIT_JUDGE", "sonnet")
    models = leerie.resolve_models(repo_root, ns())
    assert models["fit_judge"] == "sonnet"


def test_fit_judge_model_global_toml_beats_default(leerie, repo_root):
    (repo_root / "leerie.toml").write_text("model = haiku\n")
    models = leerie.resolve_models(repo_root, ns())
    assert models["fit_judge"] == "haiku"


def test_fit_judge_model_per_toml_beats_global_toml(leerie, repo_root):
    (repo_root / "leerie.toml").write_text(
        "model = haiku\nmodel_fit_judge = sonnet\n")
    models = leerie.resolve_models(repo_root, ns())
    assert models["fit_judge"] == "sonnet"


# ---------------------------------------------------------------------------
# splitter — model defaults
# ---------------------------------------------------------------------------

def test_splitter_model_default_is_opus(leerie, repo_root):
    """splitter is absent from MODEL_DEFAULT_PER_WORKER → falls to MODEL_DEFAULT."""
    models = leerie.resolve_models(repo_root, ns())
    assert models["splitter"] == "opus"
    assert "splitter" not in leerie.MODEL_DEFAULT_PER_WORKER


def test_splitter_model_per_worker_cli(leerie, repo_root):
    models = leerie.resolve_models(repo_root, ns(model_splitter="haiku"))
    assert models["splitter"] == "haiku"


def test_splitter_model_global_cli_beats_default(leerie, repo_root):
    models = leerie.resolve_models(repo_root, ns(model="sonnet"))
    assert models["splitter"] == "sonnet"


# ---------------------------------------------------------------------------
# fit_judge — effort defaults
# ---------------------------------------------------------------------------

def test_fit_judge_effort_default_is_high(leerie, repo_root):
    """fit_judge is a judgment worker — EFFORT_DEFAULT_PER_WORKER is 'high'."""
    efforts = leerie.resolve_efforts(repo_root, ns())
    assert efforts["fit_judge"] == "high"
    assert leerie.EFFORT_DEFAULT_PER_WORKER.get("fit_judge") == "high"


def test_fit_judge_effort_per_worker_cli(leerie, repo_root):
    efforts = leerie.resolve_efforts(repo_root, ns(effort_fit_judge="max"))
    assert efforts["fit_judge"] == "max"


def test_fit_judge_effort_global_cli_beats_default(leerie, repo_root):
    efforts = leerie.resolve_efforts(repo_root, ns(effort="low"))
    assert efforts["fit_judge"] == "low"


def test_fit_judge_effort_per_cli_beats_global_cli(leerie, repo_root):
    efforts = leerie.resolve_efforts(
        repo_root, ns(effort="low", effort_fit_judge="max"))
    assert efforts["fit_judge"] == "max"


def test_fit_judge_effort_global_env_beats_default(leerie, repo_root, monkeypatch):
    monkeypatch.setenv("LEERIE_EFFORT", "xhigh")
    efforts = leerie.resolve_efforts(repo_root, ns())
    assert efforts["fit_judge"] == "xhigh"


def test_fit_judge_effort_global_toml_beats_default(leerie, repo_root):
    (repo_root / "leerie.toml").write_text("effort = medium\n")
    efforts = leerie.resolve_efforts(repo_root, ns())
    assert efforts["fit_judge"] == "medium"


# ---------------------------------------------------------------------------
# splitter — effort defaults
# ---------------------------------------------------------------------------

def test_splitter_effort_default_is_high(leerie, repo_root):
    """splitter is a judgment worker — EFFORT_DEFAULT_PER_WORKER is 'high'."""
    efforts = leerie.resolve_efforts(repo_root, ns())
    assert efforts["splitter"] == "high"
    assert leerie.EFFORT_DEFAULT_PER_WORKER.get("splitter") == "high"


def test_splitter_effort_per_worker_cli(leerie, repo_root):
    efforts = leerie.resolve_efforts(repo_root, ns(effort_splitter="max"))
    assert efforts["splitter"] == "max"


def test_splitter_effort_global_cli_beats_default(leerie, repo_root):
    efforts = leerie.resolve_efforts(repo_root, ns(effort="low"))
    assert efforts["splitter"] == "low"


# ---------------------------------------------------------------------------
# Isolation — overrides don't bleed to other workers
# ---------------------------------------------------------------------------

def test_fit_judge_model_override_isolated(leerie, repo_root):
    """A per-worker override for fit_judge doesn't change other workers."""
    models = leerie.resolve_models(repo_root, ns(model_fit_judge="haiku"))
    assert models["fit_judge"] == "haiku"
    assert models["planner"] == "opus"
    assert models["implementer"] == "sonnet"


def test_splitter_effort_override_isolated(leerie, repo_root):
    """A per-worker effort override for splitter doesn't change other workers."""
    efforts = leerie.resolve_efforts(repo_root, ns(effort_splitter="max"))
    assert efforts["splitter"] == "max"
    assert efforts["planner"] == "high"
    assert efforts["implementer"] is None


# ---------------------------------------------------------------------------
# Structural / wiring checks
# ---------------------------------------------------------------------------

def test_fit_judge_in_worker_types(leerie):
    """fit_judge must be in WORKER_TYPES to participate in model resolution."""
    assert "fit_judge" in leerie.WORKER_TYPES


def test_splitter_in_worker_types(leerie):
    """splitter must be in WORKER_TYPES to participate in model resolution."""
    assert "splitter" in leerie.WORKER_TYPES


def test_fit_judge_not_in_model_default_per_worker(leerie):
    assert "fit_judge" not in leerie.MODEL_DEFAULT_PER_WORKER


def test_splitter_not_in_model_default_per_worker(leerie):
    assert "splitter" not in leerie.MODEL_DEFAULT_PER_WORKER


def test_fit_judge_in_effort_default_per_worker(leerie):
    assert "fit_judge" in leerie.EFFORT_DEFAULT_PER_WORKER
    assert leerie.EFFORT_DEFAULT_PER_WORKER["fit_judge"] == "high"


def test_splitter_in_effort_default_per_worker(leerie):
    assert "splitter" in leerie.EFFORT_DEFAULT_PER_WORKER
    assert leerie.EFFORT_DEFAULT_PER_WORKER["splitter"] == "high"
