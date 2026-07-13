"""Tests for fit_judge and splitter model and effort resolution.

fit_judge and splitter are main-loop judgment workers registered in WORKER_TYPES.
They follow the standard per-worker model/effort resolution chain:
  1. --model-fit_judge / --model-splitter CLI flag  (per-worker CLI)
  2. --model CLI flag                               (global CLI)
  3. LEERIE_MODEL_FIT_JUDGE / LEERIE_MODEL_SPLITTER (per-worker env)
  4. LEERIE_MODEL env var                           (global env)
  5. model_fit_judge / model_splitter in leerie.toml (per-worker TOML)
  6. model in leerie.toml                           (global TOML)
  7. MODEL_DEFAULT_PER_WORKER[...] — absent for both → falls to MODEL_DEFAULT
  8. MODEL_DEFAULT ("opus")

Effort for both (judgment workers):
  1. --effort-fit_judge / --effort-splitter CLI flag
  2. --effort CLI flag
  3. LEERIE_EFFORT_FIT_JUDGE / LEERIE_EFFORT_SPLITTER env var
  4. LEERIE_EFFORT env var
  5. effort_fit_judge / effort_splitter in leerie.toml
  6. effort in leerie.toml
  7. EFFORT_DEFAULT_PER_WORKER["fit_judge"] / ["splitter"] → "high"

Mirrors test_resolve_models.py / test_resolve_efforts.py.
"""
from __future__ import annotations

import argparse

import pytest


_WORKER_TYPES = (
    "classifier", "planner", "reconciler", "plan_overlap_judge",
    "satisfied_probe", "provision", "implementer", "integrator",
    "conformer", "fit_judge", "splitter",
)


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
# WORKER_TYPES membership
# ---------------------------------------------------------------------------

def test_fit_judge_in_worker_types(leerie):
    assert "fit_judge" in leerie.WORKER_TYPES


def test_splitter_in_worker_types(leerie):
    assert "splitter" in leerie.WORKER_TYPES


# ---------------------------------------------------------------------------
# MODEL_DEFAULT_PER_WORKER — both absent (fall through to MODEL_DEFAULT)
# ---------------------------------------------------------------------------

def test_fit_judge_not_in_model_default_per_worker(leerie):
    assert "fit_judge" not in leerie.MODEL_DEFAULT_PER_WORKER


def test_splitter_not_in_model_default_per_worker(leerie):
    assert "splitter" not in leerie.MODEL_DEFAULT_PER_WORKER


# ---------------------------------------------------------------------------
# EFFORT_DEFAULT_PER_WORKER — both present at "high"
# ---------------------------------------------------------------------------

def test_fit_judge_in_effort_default_per_worker(leerie):
    assert leerie.EFFORT_DEFAULT_PER_WORKER.get("fit_judge") == "high"


def test_splitter_in_effort_default_per_worker(leerie):
    assert leerie.EFFORT_DEFAULT_PER_WORKER.get("splitter") == "high"


# ---------------------------------------------------------------------------
# fit_judge — model resolution precedence
# ---------------------------------------------------------------------------

def test_fit_judge_model_default_is_opus(leerie, repo_root):
    """fit_judge absent from MODEL_DEFAULT_PER_WORKER → falls to MODEL_DEFAULT ('opus')."""
    models = leerie.resolve_models(repo_root, ns())
    assert models["fit_judge"] == "opus"
    assert leerie.MODEL_DEFAULT == "opus"


def test_fit_judge_model_global_toml(leerie, repo_root):
    (repo_root / "leerie.toml").write_text("model = haiku\n")
    assert leerie.resolve_models(repo_root, ns())["fit_judge"] == "haiku"


def test_fit_judge_model_per_toml_beats_global_toml(leerie, repo_root):
    (repo_root / "leerie.toml").write_text("model = haiku\nmodel_fit_judge = sonnet\n")
    assert leerie.resolve_models(repo_root, ns())["fit_judge"] == "sonnet"


def test_fit_judge_model_global_env_beats_toml(leerie, repo_root, monkeypatch):
    (repo_root / "leerie.toml").write_text("model = haiku\n")
    monkeypatch.setenv("LEERIE_MODEL", "opus")
    assert leerie.resolve_models(repo_root, ns())["fit_judge"] == "opus"


def test_fit_judge_model_per_env_beats_global_env(leerie, repo_root, monkeypatch):
    monkeypatch.setenv("LEERIE_MODEL", "haiku")
    monkeypatch.setenv("LEERIE_MODEL_FIT_JUDGE", "sonnet")
    assert leerie.resolve_models(repo_root, ns())["fit_judge"] == "sonnet"


def test_fit_judge_model_global_cli_beats_env(leerie, repo_root, monkeypatch):
    monkeypatch.setenv("LEERIE_MODEL", "haiku")
    assert leerie.resolve_models(repo_root, ns(model="opus"))["fit_judge"] == "opus"


def test_fit_judge_model_per_cli_beats_global_cli(leerie, repo_root):
    models = leerie.resolve_models(repo_root, ns(model="sonnet", model_fit_judge="haiku"))
    assert models["fit_judge"] == "haiku"


# ---------------------------------------------------------------------------
# splitter — model resolution precedence
# ---------------------------------------------------------------------------

def test_splitter_model_default_is_opus(leerie, repo_root):
    assert leerie.resolve_models(repo_root, ns())["splitter"] == "opus"


def test_splitter_model_global_toml(leerie, repo_root):
    (repo_root / "leerie.toml").write_text("model = haiku\n")
    assert leerie.resolve_models(repo_root, ns())["splitter"] == "haiku"


def test_splitter_model_per_toml_beats_global_toml(leerie, repo_root):
    (repo_root / "leerie.toml").write_text("model = haiku\nmodel_splitter = sonnet\n")
    assert leerie.resolve_models(repo_root, ns())["splitter"] == "sonnet"


def test_splitter_model_per_env_beats_global_env(leerie, repo_root, monkeypatch):
    monkeypatch.setenv("LEERIE_MODEL", "haiku")
    monkeypatch.setenv("LEERIE_MODEL_SPLITTER", "sonnet")
    assert leerie.resolve_models(repo_root, ns())["splitter"] == "sonnet"


def test_splitter_model_per_cli_beats_global_cli(leerie, repo_root):
    models = leerie.resolve_models(repo_root, ns(model="sonnet", model_splitter="haiku"))
    assert models["splitter"] == "haiku"


# ---------------------------------------------------------------------------
# fit_judge — effort resolution precedence
# ---------------------------------------------------------------------------

def test_fit_judge_effort_default_is_high(leerie, repo_root):
    """fit_judge is a judgment worker — EFFORT_DEFAULT_PER_WORKER["fit_judge"] = 'high'."""
    efforts = leerie.resolve_efforts(repo_root, ns())
    assert efforts["fit_judge"] == "high"


def test_fit_judge_effort_global_toml(leerie, repo_root):
    (repo_root / "leerie.toml").write_text("effort = medium\n")
    assert leerie.resolve_efforts(repo_root, ns())["fit_judge"] == "medium"


def test_fit_judge_effort_per_toml_beats_global_toml(leerie, repo_root):
    (repo_root / "leerie.toml").write_text("effort = medium\neffort_fit_judge = max\n")
    assert leerie.resolve_efforts(repo_root, ns())["fit_judge"] == "max"


def test_fit_judge_effort_global_env_beats_toml(leerie, repo_root, monkeypatch):
    (repo_root / "leerie.toml").write_text("effort = medium\n")
    monkeypatch.setenv("LEERIE_EFFORT", "xhigh")
    assert leerie.resolve_efforts(repo_root, ns())["fit_judge"] == "xhigh"


def test_fit_judge_effort_per_env_beats_global_env(leerie, repo_root, monkeypatch):
    monkeypatch.setenv("LEERIE_EFFORT", "low")
    monkeypatch.setenv("LEERIE_EFFORT_FIT_JUDGE", "max")
    assert leerie.resolve_efforts(repo_root, ns())["fit_judge"] == "max"


def test_fit_judge_effort_global_cli_beats_env(leerie, repo_root, monkeypatch):
    monkeypatch.setenv("LEERIE_EFFORT", "low")
    assert leerie.resolve_efforts(repo_root, ns(effort="xhigh"))["fit_judge"] == "xhigh"


def test_fit_judge_effort_per_cli_beats_global_cli(leerie, repo_root):
    efforts = leerie.resolve_efforts(repo_root, ns(effort="low", effort_fit_judge="max"))
    assert efforts["fit_judge"] == "max"


# ---------------------------------------------------------------------------
# splitter — effort resolution precedence
# ---------------------------------------------------------------------------

def test_splitter_effort_default_is_high(leerie, repo_root):
    assert leerie.resolve_efforts(repo_root, ns())["splitter"] == "high"


def test_splitter_effort_global_toml(leerie, repo_root):
    (repo_root / "leerie.toml").write_text("effort = medium\n")
    assert leerie.resolve_efforts(repo_root, ns())["splitter"] == "medium"


def test_splitter_effort_per_env_beats_global_env(leerie, repo_root, monkeypatch):
    monkeypatch.setenv("LEERIE_EFFORT", "low")
    monkeypatch.setenv("LEERIE_EFFORT_SPLITTER", "max")
    assert leerie.resolve_efforts(repo_root, ns())["splitter"] == "max"


def test_splitter_effort_per_cli_beats_global_cli(leerie, repo_root):
    efforts = leerie.resolve_efforts(repo_root, ns(effort="low", effort_splitter="max"))
    assert efforts["splitter"] == "max"


# ---------------------------------------------------------------------------
# Isolation — overrides don't bleed to adjacent workers
# ---------------------------------------------------------------------------

def test_fit_judge_model_override_isolated(leerie, repo_root):
    """A per-worker model override for fit_judge doesn't affect planner or implementer."""
    models = leerie.resolve_models(repo_root, ns(model_fit_judge="haiku"))
    assert models["fit_judge"] == "haiku"
    assert models["planner"] == "opus"
    assert models["implementer"] == "sonnet"


def test_splitter_model_override_isolated(leerie, repo_root):
    models = leerie.resolve_models(repo_root, ns(model_splitter="haiku"))
    assert models["splitter"] == "haiku"
    assert models["planner"] == "opus"


def test_fit_judge_effort_override_isolated(leerie, repo_root):
    """A per-worker effort override for fit_judge doesn't affect planner or implementer."""
    efforts = leerie.resolve_efforts(repo_root, ns(effort_fit_judge="max"))
    assert efforts["fit_judge"] == "max"
    assert efforts["planner"] == "high"
    assert efforts["implementer"] is None


def test_splitter_effort_override_isolated(leerie, repo_root):
    efforts = leerie.resolve_efforts(repo_root, ns(effort_splitter="max"))
    assert efforts["splitter"] == "max"
    assert efforts["planner"] == "high"
