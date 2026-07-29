"""Model/effort resolution for the three independent adversarial verifiers
(DESIGN §8): classification_judge, wiring_judge, provision_judge.

Each is a main-loop judgment worker in WORKER_TYPES following the standard
per-worker resolution chain. Model defaults to opus (absent from
MODEL_DEFAULT_PER_WORKER → MODEL_DEFAULT); effort defaults to "medium".

Mirrors test_resolve_fit_judge_model.py's fixture patterns.
"""
from __future__ import annotations

import argparse

import pytest

_NEW = ("classification_judge", "wiring_judge", "provision_judge")

# Full WORKER_TYPES so ns() populates every model_<w>/effort_<w> attr.
_WORKER_TYPES = (
    "classifier", "planner", "reconciler", "plan_overlap_judge",
    "satisfied_probe", "provision", "implementer", "integrator",
    "conformer", "fit_judge", "splitter", "adherence_judge",
    "classification_judge", "wiring_judge", "provision_judge",
)


def ns(**overrides):
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
    monkeypatch.delenv("LEERIE_MODEL", raising=False)
    monkeypatch.delenv("LEERIE_EFFORT", raising=False)
    for w in _WORKER_TYPES:
        monkeypatch.delenv(f"LEERIE_MODEL_{w.upper()}", raising=False)
        monkeypatch.delenv(f"LEERIE_EFFORT_{w.upper()}", raising=False)
    return tmp_path


# --- structural wiring -----------------------------------------------------

@pytest.mark.parametrize("w", _NEW)
def test_in_worker_types(leerie, w):
    assert w in leerie.WORKER_TYPES


@pytest.mark.parametrize("w", _NEW)
def test_not_in_model_default_per_worker(leerie, w):
    assert w not in leerie.MODEL_DEFAULT_PER_WORKER


@pytest.mark.parametrize("w", _NEW)
def test_effort_default_entry_is_medium(leerie, w):
    assert leerie.EFFORT_DEFAULT_PER_WORKER.get(w) == "medium"


# --- model resolution ------------------------------------------------------

@pytest.mark.parametrize("w", _NEW)
def test_model_default_is_opus(leerie, repo_root, w):
    models = leerie.resolve_models(repo_root, ns())
    assert models[w] == "opus"


@pytest.mark.parametrize("w", _NEW)
def test_per_worker_cli_flag_wins(leerie, repo_root, w):
    models = leerie.resolve_models(repo_root, ns(**{f"model_{w}": "haiku"}))
    assert models[w] == "haiku"
    # Isolation: the override must not bleed to another worker.
    assert models["planner"] == "opus"


@pytest.mark.parametrize("w", _NEW)
def test_global_cli_flag_applies(leerie, repo_root, w):
    models = leerie.resolve_models(repo_root, ns(model="sonnet"))
    assert models[w] == "sonnet"


@pytest.mark.parametrize("w", _NEW)
def test_per_worker_env_beats_global_env(leerie, repo_root, monkeypatch, w):
    monkeypatch.setenv("LEERIE_MODEL", "sonnet")
    monkeypatch.setenv(f"LEERIE_MODEL_{w.upper()}", "haiku")
    models = leerie.resolve_models(repo_root, ns())
    assert models[w] == "haiku"


@pytest.mark.parametrize("w", _NEW)
def test_model_toml_key(leerie, repo_root, w):
    (repo_root / "leerie.toml").write_text(f'model_{w} = "haiku"\n')
    models = leerie.resolve_models(repo_root, ns())
    assert models[w] == "haiku"


# --- effort resolution -----------------------------------------------------

@pytest.mark.parametrize("w", _NEW)
def test_effort_default_is_medium(leerie, repo_root, w):
    efforts = leerie.resolve_efforts(repo_root, ns())
    assert efforts[w] == "medium"


@pytest.mark.parametrize("w", _NEW)
def test_effort_per_worker_cli_wins(leerie, repo_root, w):
    efforts = leerie.resolve_efforts(repo_root, ns(**{f"effort_{w}": "max"}))
    assert efforts[w] == "max"
    assert efforts["planner"] == "medium"


@pytest.mark.parametrize("w", _NEW)
def test_effort_global_cli_applies(leerie, repo_root, w):
    efforts = leerie.resolve_efforts(repo_root, ns(effort="high"))
    assert efforts[w] == "high"
