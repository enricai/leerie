"""Tests for dep_capture model and effort resolution.

dep_capture is a post-run skill worker — not in WORKER_TYPES. Its model override
is env-var-only: there is NO --model-dep-capture CLI flag and NO model_dep_capture
leerie.toml key (both were removed as dead slots). Model precedence (highest first):
  1. LEERIE_MODEL_DEP_CAPTURE env var
  2. --model CLI flag (global)
  3. LEERIE_MODEL env var (global)
  4. model in leerie.toml (global)
  5. MODEL_DEFAULT ("sonnet") — dep_capture is absent from MODEL_DEFAULT_PER_WORKER

Effort precedence (highest first):
  1. --effort CLI flag (global — no per-worker effort flag for dep_capture)
  2. LEERIE_EFFORT env var (global)
  3. effort in leerie.toml (global)
  4. EFFORT_DEFAULT_PER_WORKER["dep_capture"] ("medium")

Mirrors test_resolve_models.py / test_resolve_efforts.py fixture patterns.
"""
from __future__ import annotations

import argparse

import pytest


# WORKER_TYPES slice used for the ns() helper.
_WORKER_TYPES = ("classifier", "planner", "reconciler", "plan_overlap_judge",
                 "satisfied_probe", "provision", "implementer", "integrator",
                 "conformer")


def ns(**overrides):
    """Build an argparse.Namespace matching resolve_models / resolve_efforts
    expectations: global `model`/`effort` and per-worker `model_<w>`/`effort_<w>`
    for WORKER_TYPES. Deliberately has NO `dep_capture_model` attribute —
    dep_capture's model override is env-var-only (the CLI slot was removed)."""
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
    monkeypatch.delenv("LEERIE_MODEL_DEP_CAPTURE", raising=False)
    monkeypatch.delenv("LEERIE_EFFORT", raising=False)
    monkeypatch.delenv("LEERIE_EFFORT_DEP_CAPTURE", raising=False)
    for w in _WORKER_TYPES:
        monkeypatch.delenv(f"LEERIE_MODEL_{w.upper()}", raising=False)
        monkeypatch.delenv(f"LEERIE_EFFORT_{w.upper()}", raising=False)
    return tmp_path


# ---------------------------------------------------------------------------
# Model — default
# ---------------------------------------------------------------------------

def test_dep_capture_model_default_is_sonnet(leerie, repo_root):
    """dep_capture is absent from MODEL_DEFAULT_PER_WORKER, so resolve_models
    falls through to MODEL_DEFAULT ('sonnet')."""
    models = leerie.resolve_models(repo_root, ns())
    assert models["dep_capture"] == "sonnet"
    assert "dep_capture" not in leerie.MODEL_DEFAULT_PER_WORKER
    assert leerie.MODEL_DEFAULT == "sonnet"


# ---------------------------------------------------------------------------
# Model — per-worker env var (highest priority)
# ---------------------------------------------------------------------------

def test_dep_capture_model_env_beats_global_env(leerie, repo_root, monkeypatch):
    monkeypatch.setenv("LEERIE_MODEL", "haiku")
    monkeypatch.setenv("LEERIE_MODEL_DEP_CAPTURE", "sonnet")
    models = leerie.resolve_models(repo_root, ns())
    assert models["dep_capture"] == "sonnet"


def test_dep_capture_model_env_beats_global_toml(leerie, repo_root, monkeypatch):
    (repo_root / "leerie.toml").write_text("model = haiku\n")
    monkeypatch.setenv("LEERIE_MODEL_DEP_CAPTURE", "sonnet")
    models = leerie.resolve_models(repo_root, ns())
    assert models["dep_capture"] == "sonnet"


def test_dep_capture_model_env_beats_global_cli(leerie, repo_root, monkeypatch):
    """dep_capture's per-worker env var outranks even the global --model flag
    (post-run skill workers rank per-worker env above global CLI)."""
    monkeypatch.setenv("LEERIE_MODEL_DEP_CAPTURE", "haiku")
    models = leerie.resolve_models(repo_root, ns(model="sonnet"))
    assert models["dep_capture"] == "haiku"


# ---------------------------------------------------------------------------
# Model — global CLI (top override once per-worker env is unset)
# ---------------------------------------------------------------------------

def test_dep_capture_model_global_cli_beats_global_env(leerie, repo_root, monkeypatch):
    monkeypatch.setenv("LEERIE_MODEL", "haiku")
    models = leerie.resolve_models(repo_root, ns(model="sonnet"))
    assert models["dep_capture"] == "sonnet"


# ---------------------------------------------------------------------------
# Model — global TOML (below all env/CLI)
# ---------------------------------------------------------------------------

def test_dep_capture_model_global_toml_beats_default(leerie, repo_root):
    (repo_root / "leerie.toml").write_text("model = haiku\n")
    models = leerie.resolve_models(repo_root, ns())
    assert models["dep_capture"] == "haiku"


# ---------------------------------------------------------------------------
# Model — env-var-only: no CLI flag, no per-worker TOML key
# ---------------------------------------------------------------------------

def test_dep_capture_model_no_cli_flag(leerie, repo_root):
    """There is no --model-dep-capture flag: a stray args.dep_capture_model
    attribute must NOT influence resolution (the dead CLI slot was removed)."""
    models = leerie.resolve_models(
        repo_root, ns(dep_capture_model="opus"))
    assert models["dep_capture"] == "sonnet"  # falls through to MODEL_DEFAULT


def test_dep_capture_model_no_toml_key(leerie, repo_root):
    """A model_dep_capture key in leerie.toml is NOT honored (dead TOML slot
    removed) — only the global `model` key applies to dep_capture."""
    (repo_root / "leerie.toml").write_text("model_dep_capture = opus\n")
    models = leerie.resolve_models(repo_root, ns())
    assert models["dep_capture"] == "sonnet"  # model_dep_capture ignored


# ---------------------------------------------------------------------------
# Model — full precedence walkthrough
# ---------------------------------------------------------------------------

def test_dep_capture_model_full_precedence(leerie, repo_root, monkeypatch):
    """Exercise each rung of the env-var-only precedence chain in order:
    per-worker env > global CLI > global env > global TOML > MODEL_DEFAULT.
    The per-worker env slot (rung 1) ranks above global CLI (rung 2)."""
    cfg = repo_root / "leerie.toml"

    # rung 5: MODEL_DEFAULT ("sonnet") — no overrides
    assert leerie.resolve_models(repo_root, ns())["dep_capture"] == "sonnet"

    # rung 4: global TOML beats default
    cfg.write_text("model = haiku\n")
    assert leerie.resolve_models(repo_root, ns())["dep_capture"] == "haiku"

    # rung 3: global env beats TOML
    monkeypatch.setenv("LEERIE_MODEL", "opus")
    assert leerie.resolve_models(repo_root, ns())["dep_capture"] == "opus"

    # rung 2: global CLI beats global env (per-worker env still unset)
    assert leerie.resolve_models(
        repo_root, ns(model="sonnet"))["dep_capture"] == "sonnet"

    # rung 1: per-worker env beats global CLI
    monkeypatch.setenv("LEERIE_MODEL_DEP_CAPTURE", "haiku")
    assert leerie.resolve_models(
        repo_root, ns(model="sonnet"))["dep_capture"] == "haiku"


# ---------------------------------------------------------------------------
# Model — isolation (dep_capture override doesn't bleed to other workers)
# ---------------------------------------------------------------------------

def test_dep_capture_model_override_isolated(leerie, repo_root, monkeypatch):
    """A per-worker env override for dep_capture doesn't change other workers."""
    monkeypatch.setenv("LEERIE_MODEL_DEP_CAPTURE", "haiku")
    models = leerie.resolve_models(repo_root, ns())
    assert models["dep_capture"] == "haiku"
    # Other workers stay at their defaults.
    assert models["planner"] == "sonnet"
    assert models["implementer"] == "sonnet"


# ---------------------------------------------------------------------------
# Effort — default
# ---------------------------------------------------------------------------

def test_dep_capture_effort_default_is_medium(leerie, repo_root):
    """dep_capture is a judgment worker — EFFORT_DEFAULT_PER_WORKER["dep_capture"]
    is "medium" (lowered from "high" post-Opus-5), so resolve_efforts returns
    "medium" with no overrides."""
    efforts = leerie.resolve_efforts(repo_root, ns())
    assert efforts["dep_capture"] == "medium"
    assert leerie.EFFORT_DEFAULT_PER_WORKER.get("dep_capture") == "medium"


# ---------------------------------------------------------------------------
# Effort — global CLI override
# ---------------------------------------------------------------------------

def test_dep_capture_effort_global_cli_overrides_default(leerie, repo_root):
    efforts = leerie.resolve_efforts(repo_root, ns(effort="max"))
    assert efforts["dep_capture"] == "max"


def test_dep_capture_effort_global_cli_overrides_env(leerie, repo_root, monkeypatch):
    monkeypatch.setenv("LEERIE_EFFORT", "low")
    efforts = leerie.resolve_efforts(repo_root, ns(effort="xhigh"))
    assert efforts["dep_capture"] == "xhigh"


# ---------------------------------------------------------------------------
# Effort — global env override
# ---------------------------------------------------------------------------

def test_dep_capture_effort_global_env_overrides_default(leerie, repo_root, monkeypatch):
    monkeypatch.setenv("LEERIE_EFFORT", "max")
    efforts = leerie.resolve_efforts(repo_root, ns())
    assert efforts["dep_capture"] == "max"


def test_dep_capture_effort_global_env_overrides_toml(leerie, repo_root, monkeypatch):
    (repo_root / "leerie.toml").write_text("effort = low\n")
    monkeypatch.setenv("LEERIE_EFFORT", "max")
    efforts = leerie.resolve_efforts(repo_root, ns())
    assert efforts["dep_capture"] == "max"


# ---------------------------------------------------------------------------
# Effort — global TOML override
# ---------------------------------------------------------------------------

def test_dep_capture_effort_global_toml_overrides_default(leerie, repo_root):
    (repo_root / "leerie.toml").write_text("effort = low\n")
    efforts = leerie.resolve_efforts(repo_root, ns())
    assert efforts["dep_capture"] == "low"


# ---------------------------------------------------------------------------
# Effort — full precedence walkthrough (global only — no per-worker effort flag)
# ---------------------------------------------------------------------------

def test_dep_capture_effort_full_precedence(leerie, repo_root, monkeypatch):
    """dep_capture has no per-worker effort CLI flag; precedence is:
    global CLI > global env > global TOML > EFFORT_DEFAULT_PER_WORKER ("medium")."""
    cfg = repo_root / "leerie.toml"

    # rung 4: EFFORT_DEFAULT_PER_WORKER ("medium")
    assert leerie.resolve_efforts(repo_root, ns())["dep_capture"] == "medium"

    # rung 3: global TOML beats default
    cfg.write_text("effort = low\n")
    assert leerie.resolve_efforts(repo_root, ns())["dep_capture"] == "low"

    # rung 2: global env beats TOML
    monkeypatch.setenv("LEERIE_EFFORT", "max")
    assert leerie.resolve_efforts(repo_root, ns())["dep_capture"] == "max"

    # rung 1: global CLI beats env
    assert leerie.resolve_efforts(
        repo_root, ns(effort="medium"))["dep_capture"] == "medium"


# ---------------------------------------------------------------------------
# Effort — isolation
# ---------------------------------------------------------------------------

def test_dep_capture_effort_override_isolated(leerie, repo_root, monkeypatch):
    """A global effort override that changes dep_capture also changes other
    workers. Pins that dep_capture participates in the global chain."""
    monkeypatch.setenv("LEERIE_EFFORT", "low")
    efforts = leerie.resolve_efforts(repo_root, ns())
    assert efforts["dep_capture"] == "low"
    # Other judgment workers also follow the global override.
    assert efforts["planner"] == "low"


# ---------------------------------------------------------------------------
# Structural / wiring checks
# ---------------------------------------------------------------------------

def test_dep_capture_not_in_worker_types(leerie):
    """dep_capture is a post-run skill worker — must NOT appear in WORKER_TYPES."""
    assert "dep_capture" not in leerie.WORKER_TYPES


def test_dep_capture_model_env_var_constant(leerie):
    """MODEL_DEP_CAPTURE_ENV must be 'LEERIE_MODEL_DEP_CAPTURE' — pins the
    env-var name so a rename doesn't silently break user configs."""
    assert leerie.MODEL_DEP_CAPTURE_ENV == "LEERIE_MODEL_DEP_CAPTURE"


def test_dep_capture_effort_in_judgment_set(leerie):
    """dep_capture must be in EFFORT_DEFAULT_PER_WORKER alongside other
    judgment workers (classifier, planner, etc.)."""
    assert "dep_capture" in leerie.EFFORT_DEFAULT_PER_WORKER
    assert leerie.EFFORT_DEFAULT_PER_WORKER["dep_capture"] == "medium"
