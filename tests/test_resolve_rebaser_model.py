"""Tests for rebaser model and effort resolution.

rebaser is a main-loop-adjacent judgment worker in WORKER_TYPES (DESIGN §6
*Finalization* "Rebase-onto-base before push" — a scoped, fully-agentic
exception to §12). It follows the standard per-worker model/effort
resolution chain, mirroring integrator's own resolution exactly:
  1. --model-rebaser / --effort-rebaser CLI flag
  2. --model / --effort CLI flag (global)
  3. LEERIE_MODEL_REBASER / LEERIE_EFFORT_REBASER env var
  4. LEERIE_MODEL / LEERIE_EFFORT env var
  5. model_rebaser / effort_rebaser in leerie.toml
  6. model / effort in leerie.toml
  7. MODEL_DEFAULT_PER_WORKER — absent → MODEL_DEFAULT ("sonnet")
     EFFORT_DEFAULT_PER_WORKER["rebaser"] → "medium"

Mirrors test_resolve_fit_judge_model.py's structure and fixtures.
"""
from __future__ import annotations

import argparse

import pytest


def ns(**overrides):
    base: dict = {
        "model": None,
        "effort": None,
        "model_rebaser": None,
        "effort_rebaser": None,
        "model_integrator": None,
        "effort_integrator": None,
        "model_planner": None,
        "effort_planner": None,
        "model_implementer": None,
        "effort_implementer": None,
    }
    base.update(overrides)
    return argparse.Namespace(**base)


@pytest.fixture
def repo_root(tmp_path, monkeypatch):
    monkeypatch.delenv("LEERIE_MODEL", raising=False)
    monkeypatch.delenv("LEERIE_EFFORT", raising=False)
    monkeypatch.delenv("LEERIE_MODEL_REBASER", raising=False)
    monkeypatch.delenv("LEERIE_EFFORT_REBASER", raising=False)
    return tmp_path


# --- model defaults ---------------------------------------------------------

def test_rebaser_model_default_is_sonnet(leerie, repo_root):
    """rebaser is absent from MODEL_DEFAULT_PER_WORKER → falls to
    MODEL_DEFAULT ('sonnet'), matching integrator's own resolution."""
    models = leerie.resolve_models(repo_root, ns())
    assert models["rebaser"] == "sonnet"
    assert "rebaser" not in leerie.MODEL_DEFAULT_PER_WORKER
    assert leerie.MODEL_DEFAULT == "sonnet"


def test_rebaser_model_per_worker_cli(leerie, repo_root):
    models = leerie.resolve_models(repo_root, ns(model_rebaser="haiku"))
    assert models["rebaser"] == "haiku"


def test_rebaser_model_global_cli_beats_default(leerie, repo_root):
    models = leerie.resolve_models(repo_root, ns(model="opus"))
    assert models["rebaser"] == "opus"


def test_rebaser_model_per_cli_beats_global_cli(leerie, repo_root):
    models = leerie.resolve_models(
        repo_root, ns(model="opus", model_rebaser="haiku"))
    assert models["rebaser"] == "haiku"


def test_rebaser_model_per_env_beats_global_env(leerie, repo_root, monkeypatch):
    monkeypatch.setenv("LEERIE_MODEL", "haiku")
    monkeypatch.setenv("LEERIE_MODEL_REBASER", "opus")
    models = leerie.resolve_models(repo_root, ns())
    assert models["rebaser"] == "opus"


def test_rebaser_model_global_toml_beats_default(leerie, repo_root):
    (repo_root / "leerie.toml").write_text("model = haiku\n")
    models = leerie.resolve_models(repo_root, ns())
    assert models["rebaser"] == "haiku"


def test_rebaser_model_per_toml_beats_global_toml(leerie, repo_root):
    (repo_root / "leerie.toml").write_text(
        "model = haiku\nmodel_rebaser = opus\n")
    models = leerie.resolve_models(repo_root, ns())
    assert models["rebaser"] == "opus"


# --- effort defaults --------------------------------------------------------

def test_rebaser_effort_default_is_medium(leerie, repo_root):
    """rebaser gets integrator's same 'medium' judgment-tier effort —
    it decides abort-vs-resolve per conflict, not just resolution content."""
    efforts = leerie.resolve_efforts(repo_root, ns())
    assert efforts["rebaser"] == "medium"
    assert leerie.EFFORT_DEFAULT_PER_WORKER["rebaser"] == "medium"
    assert leerie.EFFORT_DEFAULT_PER_WORKER["rebaser"] == \
        leerie.EFFORT_DEFAULT_PER_WORKER["integrator"]


def test_rebaser_effort_per_worker_cli(leerie, repo_root):
    efforts = leerie.resolve_efforts(repo_root, ns(effort_rebaser="max"))
    assert efforts["rebaser"] == "max"


def test_rebaser_effort_global_cli_beats_default(leerie, repo_root):
    efforts = leerie.resolve_efforts(repo_root, ns(effort="low"))
    assert efforts["rebaser"] == "low"


def test_rebaser_effort_per_cli_beats_global_cli(leerie, repo_root):
    efforts = leerie.resolve_efforts(
        repo_root, ns(effort="low", effort_rebaser="max"))
    assert efforts["rebaser"] == "max"


def test_rebaser_effort_global_env_beats_default(leerie, repo_root, monkeypatch):
    monkeypatch.setenv("LEERIE_EFFORT", "xhigh")
    efforts = leerie.resolve_efforts(repo_root, ns())
    assert efforts["rebaser"] == "xhigh"


def test_rebaser_effort_global_toml_beats_default(leerie, repo_root):
    (repo_root / "leerie.toml").write_text("effort = low\n")
    efforts = leerie.resolve_efforts(repo_root, ns())
    assert efforts["rebaser"] == "low"


# --- isolation ---------------------------------------------------------------

def test_rebaser_model_override_isolated(leerie, repo_root):
    models = leerie.resolve_models(repo_root, ns(model_rebaser="haiku"))
    assert models["rebaser"] == "haiku"
    assert models["integrator"] == "sonnet"
    assert models["planner"] == "sonnet"


def test_rebaser_effort_override_isolated(leerie, repo_root):
    efforts = leerie.resolve_efforts(repo_root, ns(effort_rebaser="max"))
    assert efforts["rebaser"] == "max"
    assert efforts["integrator"] == "medium"
    assert efforts["implementer"] == "low"


# --- structural / wiring checks ----------------------------------------------

def test_rebaser_in_worker_types(leerie):
    assert "rebaser" in leerie.WORKER_TYPES


def test_rebaser_not_in_model_default_per_worker(leerie):
    assert "rebaser" not in leerie.MODEL_DEFAULT_PER_WORKER


def test_rebaser_in_effort_default_per_worker(leerie):
    assert "rebaser" in leerie.EFFORT_DEFAULT_PER_WORKER
    assert leerie.EFFORT_DEFAULT_PER_WORKER["rebaser"] == "medium"
