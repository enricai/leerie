"""Tests for adherence_judge model and effort resolution.

adherence_judge is a main-loop judgment worker in WORKER_TYPES. It follows
the standard per-worker model/effort resolution chain:
  1. --model-adherence_judge CLI flag
  2. --model CLI flag (global)
  3. LEERIE_MODEL_ADHERENCE_JUDGE env var
  4. LEERIE_MODEL env var
  5. model_adherence_judge in leerie.toml
  6. model in leerie.toml
  7. MODEL_DEFAULT_PER_WORKER["adherence_judge"] — absent → falls to MODEL_DEFAULT
  8. MODEL_DEFAULT ("opus")

Effort:
  1. --effort-adherence_judge CLI flag
  2. --effort CLI flag (global)
  3. LEERIE_EFFORT_ADHERENCE_JUDGE env var
  4. LEERIE_EFFORT env var
  5. effort_adherence_judge in leerie.toml
  6. effort in leerie.toml
  7. EFFORT_DEFAULT_PER_WORKER["adherence_judge"] → "medium"

The opus default is not merely a convention here — it is empirically
required (DESIGN §5/§8 investigation): sonnet false-positived a legitimate
plan and an opus understanding-framed judge rubber-stamped the incident;
only the ADHERENCE frame on opus was validated clean.

Mirrors test_resolve_fit_judge_model.py fixture patterns.
"""
from __future__ import annotations

import argparse

import pytest


# The full WORKER_TYPES including the new worker.
_WORKER_TYPES = ("classifier", "planner", "reconciler", "plan_overlap_judge",
                 "satisfied_probe", "provision", "implementer", "integrator",
                 "conformer", "fit_judge", "splitter", "adherence_judge")


def ns(**overrides):
    """Build an argparse.Namespace matching resolve_models / resolve_efforts
    expectations: global model/effort and per-worker model_<w>/effort_<w>
    for all WORKER_TYPES including adherence_judge."""
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
# adherence_judge — model defaults
# ---------------------------------------------------------------------------

def test_adherence_judge_model_default_is_opus(leerie, repo_root):
    """adherence_judge is absent from MODEL_DEFAULT_PER_WORKER → falls to
    MODEL_DEFAULT ('opus'), the judgment-worker global."""
    models = leerie.resolve_models(repo_root, ns())
    assert models["adherence_judge"] == "opus"
    assert "adherence_judge" not in leerie.MODEL_DEFAULT_PER_WORKER
    assert leerie.MODEL_DEFAULT == "opus"


def test_adherence_judge_model_per_worker_cli(leerie, repo_root):
    models = leerie.resolve_models(repo_root, ns(model_adherence_judge="haiku"))
    assert models["adherence_judge"] == "haiku"


def test_adherence_judge_model_global_cli_beats_default(leerie, repo_root):
    models = leerie.resolve_models(repo_root, ns(model="sonnet"))
    assert models["adherence_judge"] == "sonnet"


def test_adherence_judge_model_per_cli_beats_global_cli(leerie, repo_root):
    models = leerie.resolve_models(
        repo_root, ns(model="sonnet", model_adherence_judge="haiku"))
    assert models["adherence_judge"] == "haiku"


def test_adherence_judge_model_per_env_beats_global_env(leerie, repo_root, monkeypatch):
    monkeypatch.setenv("LEERIE_MODEL", "haiku")
    monkeypatch.setenv("LEERIE_MODEL_ADHERENCE_JUDGE", "sonnet")
    models = leerie.resolve_models(repo_root, ns())
    assert models["adherence_judge"] == "sonnet"


def test_adherence_judge_model_global_toml_beats_default(leerie, repo_root):
    (repo_root / "leerie.toml").write_text("model = haiku\n")
    models = leerie.resolve_models(repo_root, ns())
    assert models["adherence_judge"] == "haiku"


def test_adherence_judge_model_per_toml_beats_global_toml(leerie, repo_root):
    (repo_root / "leerie.toml").write_text(
        "model = haiku\nmodel_adherence_judge = sonnet\n")
    models = leerie.resolve_models(repo_root, ns())
    assert models["adherence_judge"] == "sonnet"


# ---------------------------------------------------------------------------
# adherence_judge — effort defaults
# ---------------------------------------------------------------------------

def test_adherence_judge_effort_default_is_medium(leerie, repo_root):
    """adherence_judge is a judgment worker — EFFORT_DEFAULT_PER_WORKER is 'medium'
    (lowered from 'high' post-Opus-5; see IMPLEMENTATION.md §2)."""
    efforts = leerie.resolve_efforts(repo_root, ns())
    assert efforts["adherence_judge"] == "medium"
    assert leerie.EFFORT_DEFAULT_PER_WORKER.get("adherence_judge") == "medium"


def test_adherence_judge_effort_per_worker_cli(leerie, repo_root):
    efforts = leerie.resolve_efforts(repo_root, ns(effort_adherence_judge="max"))
    assert efforts["adherence_judge"] == "max"


def test_adherence_judge_effort_global_cli_beats_default(leerie, repo_root):
    efforts = leerie.resolve_efforts(repo_root, ns(effort="low"))
    assert efforts["adherence_judge"] == "low"


def test_adherence_judge_effort_per_cli_beats_global_cli(leerie, repo_root):
    efforts = leerie.resolve_efforts(
        repo_root, ns(effort="low", effort_adherence_judge="max"))
    assert efforts["adherence_judge"] == "max"


def test_adherence_judge_effort_global_env_beats_default(leerie, repo_root, monkeypatch):
    monkeypatch.setenv("LEERIE_EFFORT", "xhigh")
    efforts = leerie.resolve_efforts(repo_root, ns())
    assert efforts["adherence_judge"] == "xhigh"


def test_adherence_judge_effort_global_toml_beats_default(leerie, repo_root):
    (repo_root / "leerie.toml").write_text("effort = medium\n")
    efforts = leerie.resolve_efforts(repo_root, ns())
    assert efforts["adherence_judge"] == "medium"


# ---------------------------------------------------------------------------
# Isolation — overrides don't bleed to other workers
# ---------------------------------------------------------------------------

def test_adherence_judge_model_override_isolated(leerie, repo_root):
    """A per-worker override for adherence_judge doesn't change other workers."""
    models = leerie.resolve_models(repo_root, ns(model_adherence_judge="haiku"))
    assert models["adherence_judge"] == "haiku"
    assert models["planner"] == "opus"
    assert models["implementer"] == "sonnet"


def test_adherence_judge_effort_override_isolated(leerie, repo_root):
    """A per-worker effort override for adherence_judge doesn't change
    other workers."""
    efforts = leerie.resolve_efforts(repo_root, ns(effort_adherence_judge="max"))
    assert efforts["adherence_judge"] == "max"
    assert efforts["planner"] == "medium"
    assert efforts["implementer"] is None


# ---------------------------------------------------------------------------
# Structural / wiring checks
# ---------------------------------------------------------------------------

def test_adherence_judge_in_worker_types(leerie):
    """adherence_judge must be in WORKER_TYPES to participate in model
    resolution."""
    assert "adherence_judge" in leerie.WORKER_TYPES


def test_adherence_judge_not_in_model_default_per_worker(leerie):
    assert "adherence_judge" not in leerie.MODEL_DEFAULT_PER_WORKER


def test_adherence_judge_in_effort_default_per_worker(leerie):
    assert "adherence_judge" in leerie.EFFORT_DEFAULT_PER_WORKER
    assert leerie.EFFORT_DEFAULT_PER_WORKER["adherence_judge"] == "medium"
