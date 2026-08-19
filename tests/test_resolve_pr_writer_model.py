"""Tests for pr_writer model and effort resolution.

pr_writer is a finalize-time worker — not in WORKER_TYPES — with its own
dedicated CLI flag / env var / toml key, mirroring judge/heal's shape rather
than dep_capture's (which is deliberately env-var-only). Model precedence
(highest first):
  1. --pr-writer-model CLI flag
  2. LEERIE_MODEL_PR_WRITER env var
  3. --model CLI flag (global)
  4. LEERIE_MODEL env var (global)
  5. model_pr_writer in leerie.toml
  6. model in leerie.toml (global)
  7. MODEL_DEFAULT_PER_WORKER["pr_writer"] ("sonnet")

Effort precedence (pr_writer has no dedicated --effort-pr-writer flag or env
var — only the global rungs and its own EFFORT_DEFAULT_PER_WORKER entry):
  1. --effort CLI flag (global)
  2. LEERIE_EFFORT env var (global)
  3. effort in leerie.toml (global)
  4. EFFORT_DEFAULT_PER_WORKER["pr_writer"] ("medium")

Before this file, resolve_models()'s dedicated pr_writer_cli/pr_writer_env/
pr_writer_file chain (leerie.py's resolve_models(), the
`models["pr_writer"] = (pr_writer_cli or pr_writer_env or ...)` block) was
covered nowhere in the suite — test_resolve_models.py's WORKERS tuple and
DEFAULTS dict exclude it (it iterates WORKER_TYPES only), and
test_resolve_dep_capture_model.py covers only dep_capture. Mirrors
test_resolve_dep_capture_model.py's fixture/ns() pattern.
"""
from __future__ import annotations

import argparse

import pytest


def ns(**overrides):
    """Build a minimal argparse.Namespace with model/effort globals and
    pr_writer_model defaulted to None (the argparse default when the flag
    isn't passed). resolve_models()/resolve_efforts() only read
    getattr(args, ..., None), so no other worker's attrs are required."""
    base: dict = {"model": None, "effort": None, "pr_writer_model": None}
    base.update(overrides)
    return argparse.Namespace(**base)


@pytest.fixture
def repo_root(tmp_path, monkeypatch):
    """Empty repo root with every relevant env var unset."""
    monkeypatch.delenv("LEERIE_MODEL", raising=False)
    monkeypatch.delenv("LEERIE_MODEL_PR_WRITER", raising=False)
    monkeypatch.delenv("LEERIE_EFFORT", raising=False)
    return tmp_path


# ---------------------------------------------------------------------------
# Model resolution
# ---------------------------------------------------------------------------

def test_default_is_sonnet(leerie, repo_root):
    models = leerie.resolve_models(repo_root, ns())
    assert models["pr_writer"] == "sonnet"
    assert leerie.MODEL_DEFAULT_PER_WORKER.get("pr_writer") == "sonnet"


def test_global_env_applies(leerie, repo_root, monkeypatch):
    monkeypatch.setenv("LEERIE_MODEL", "opus")
    models = leerie.resolve_models(repo_root, ns())
    assert models["pr_writer"] == "opus"


def test_dedicated_env_wins_over_global_env(leerie, repo_root, monkeypatch):
    monkeypatch.setenv("LEERIE_MODEL", "haiku")
    monkeypatch.setenv("LEERIE_MODEL_PR_WRITER", "opus")
    models = leerie.resolve_models(repo_root, ns())
    assert models["pr_writer"] == "opus"


def test_global_toml_applies(leerie, repo_root):
    (repo_root / "leerie.toml").write_text("model = opus\n")
    models = leerie.resolve_models(repo_root, ns())
    assert models["pr_writer"] == "opus"


def test_dedicated_toml_wins_over_global_toml(leerie, repo_root):
    (repo_root / "leerie.toml").write_text(
        "model = opus\nmodel_pr_writer = haiku\n")
    models = leerie.resolve_models(repo_root, ns())
    assert models["pr_writer"] == "haiku"


def test_dedicated_env_wins_over_dedicated_toml(leerie, repo_root, monkeypatch):
    (repo_root / "leerie.toml").write_text("model_pr_writer = haiku\n")
    monkeypatch.setenv("LEERIE_MODEL_PR_WRITER", "opus")
    models = leerie.resolve_models(repo_root, ns())
    assert models["pr_writer"] == "opus"


def test_global_cli_beats_dedicated_env_and_toml(leerie, repo_root, monkeypatch):
    # A quirk of the dedicated chain, pinned deliberately: pr_writer's own
    # dedicated env rung sits ABOVE the global CLI rung (unlike the
    # per-worker WORKER_TYPES loop, where per-worker env sits BELOW global
    # CLI). Confirm the actual shipped precedence rather than assuming
    # parity with the WORKER_TYPES chain.
    (repo_root / "leerie.toml").write_text("model_pr_writer = haiku\n")
    monkeypatch.setenv("LEERIE_MODEL_PR_WRITER", "opus")
    models = leerie.resolve_models(repo_root, ns(model="sonnet"))
    assert models["pr_writer"] == "opus"


def test_dedicated_cli_beats_everything(leerie, repo_root, monkeypatch):
    (repo_root / "leerie.toml").write_text(
        "model = haiku\nmodel_pr_writer = haiku\n")
    monkeypatch.setenv("LEERIE_MODEL", "haiku")
    monkeypatch.setenv("LEERIE_MODEL_PR_WRITER", "haiku")
    models = leerie.resolve_models(
        repo_root, ns(model="haiku", pr_writer_model="opus"))
    assert models["pr_writer"] == "opus"


def test_bad_dedicated_env_dies(leerie, repo_root, monkeypatch, capsys):
    monkeypatch.setenv("LEERIE_MODEL_PR_WRITER", "gpt5")
    with pytest.raises(SystemExit) as exc:
        leerie.resolve_models(repo_root, ns())
    assert exc.value.code != 0
    err = capsys.readouterr().err
    assert "LEERIE_MODEL_PR_WRITER" in err
    assert "gpt5" in err


def test_bad_dedicated_toml_dies(leerie, repo_root, capsys):
    (repo_root / "leerie.toml").write_text("model_pr_writer = bogus\n")
    with pytest.raises(SystemExit) as exc:
        leerie.resolve_models(repo_root, ns())
    assert exc.value.code != 0
    err = capsys.readouterr().err
    assert "model_pr_writer" in err
    assert "bogus" in err


# ---------------------------------------------------------------------------
# Effort resolution
# ---------------------------------------------------------------------------

def test_effort_default_is_medium(leerie, repo_root):
    efforts = leerie.resolve_efforts(repo_root, ns())
    assert efforts["pr_writer"] == "medium"
    assert leerie.EFFORT_DEFAULT_PER_WORKER["pr_writer"] == "medium"


def test_effort_global_env_applies(leerie, repo_root, monkeypatch):
    monkeypatch.setenv("LEERIE_EFFORT", "max")
    efforts = leerie.resolve_efforts(repo_root, ns())
    assert efforts["pr_writer"] == "max"


def test_effort_global_cli_beats_env(leerie, repo_root, monkeypatch):
    monkeypatch.setenv("LEERIE_EFFORT", "max")
    efforts = leerie.resolve_efforts(repo_root, ns(effort="low"))
    assert efforts["pr_writer"] == "low"


def test_effort_global_toml_beats_default(leerie, repo_root):
    (repo_root / "leerie.toml").write_text("effort = high\n")
    efforts = leerie.resolve_efforts(repo_root, ns())
    assert efforts["pr_writer"] == "high"
