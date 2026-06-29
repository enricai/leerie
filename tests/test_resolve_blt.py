"""Tests for _load_blt_config() and resolve_blt().

Covers: no config file → full inference fallthrough; all-3-keys config →
declared values used with no inference; partial config → declared key wins
others inferred; empty-string value as "not applicable" not a fallthrough;
config overrides inference even when inference returns a value.
Also covers _load_blt_config returning None when absent and dict of
only-present keys otherwise, including setup_packages.
"""
from __future__ import annotations

from pathlib import Path


# --- _load_blt_config ---

def test_load_blt_config_absent_returns_none(leerie, tmp_path):
    result = leerie._load_blt_config(tmp_path)
    assert result is None


def test_load_blt_config_empty_file_returns_empty_dict(leerie, tmp_path):
    leerie_dir = tmp_path / ".leerie"
    leerie_dir.mkdir()
    (leerie_dir / "config.toml").write_text("# comment only\n")
    result = leerie._load_blt_config(tmp_path)
    assert result == {}


def test_load_blt_config_all_three_keys(leerie, tmp_path):
    leerie_dir = tmp_path / ".leerie"
    leerie_dir.mkdir()
    (leerie_dir / "config.toml").write_text(
        'build = "make build"\n'
        'lint = "ruff check ."\n'
        'test = "pytest"\n'
    )
    result = leerie._load_blt_config(tmp_path)
    assert result == {"build": "make build", "lint": "ruff check .", "test": "pytest"}


def test_load_blt_config_partial_keys_only_present_returned(leerie, tmp_path):
    leerie_dir = tmp_path / ".leerie"
    leerie_dir.mkdir()
    (leerie_dir / "config.toml").write_text('test = "pytest -x"\n')
    result = leerie._load_blt_config(tmp_path)
    # Only 'test' key present — build and lint must NOT appear in result
    assert result == {"test": "pytest -x"}
    assert "build" not in result
    assert "lint" not in result


def test_load_blt_config_setup_packages_included(leerie, tmp_path):
    leerie_dir = tmp_path / ".leerie"
    leerie_dir.mkdir()
    (leerie_dir / "config.toml").write_text(
        'test = "pytest"\n'
        'setup_packages = "libvips-dev fonts-noto"\n'
    )
    result = leerie._load_blt_config(tmp_path)
    assert result == {"test": "pytest", "setup_packages": "libvips-dev fonts-noto"}


def test_load_blt_config_empty_string_value_preserved(leerie, tmp_path):
    leerie_dir = tmp_path / ".leerie"
    leerie_dir.mkdir()
    (leerie_dir / "config.toml").write_text('build = ""\n')
    result = leerie._load_blt_config(tmp_path)
    assert result == {"build": ""}


# --- resolve_blt ---

def test_resolve_blt_no_config_full_inference(leerie, tmp_path):
    """No .leerie/config.toml — resolve_blt falls through entirely to inference."""
    # tmp_path has no special files → all axes infer to empty string
    result = leerie.resolve_blt(tmp_path)
    assert result == {"build": "", "lint": "", "test": ""}


def test_resolve_blt_no_config_inference_values_used(leerie, tmp_path):
    """No config file but an inferrable repo — inference result is returned."""
    (tmp_path / "pyproject.toml").write_text("[tool]\nname='x'\n")
    result = leerie.resolve_blt(tmp_path)
    assert result["test"] == "pytest"
    assert result["build"] == ""
    assert result["lint"] == ""


def test_resolve_blt_all_axes_declared_no_inference(leerie, tmp_path):
    """All three BLT axes declared — no inference fallthrough."""
    leerie_dir = tmp_path / ".leerie"
    leerie_dir.mkdir()
    (leerie_dir / "config.toml").write_text(
        'build = "my-build"\n'
        'lint = "my-lint"\n'
        'test = "my-test"\n'
    )
    # Also add a pyproject.toml that inference would pick up, to confirm
    # declared values win unconditionally.
    (tmp_path / "pyproject.toml").write_text("[tool]\nname='x'\n")
    result = leerie.resolve_blt(tmp_path)
    assert result == {"build": "my-build", "lint": "my-lint", "test": "my-test"}


def test_resolve_blt_partial_config_declared_wins_others_inferred(leerie, tmp_path):
    """Only test declared — test comes from config, build/lint from inference."""
    leerie_dir = tmp_path / ".leerie"
    leerie_dir.mkdir()
    (leerie_dir / "config.toml").write_text('test = "my-test-suite"\n')
    (tmp_path / "pyproject.toml").write_text("[tool]\nname='x'\n")
    result = leerie.resolve_blt(tmp_path)
    # Declared test wins
    assert result["test"] == "my-test-suite"
    # Non-declared axes fall through to inference
    assert result["build"] == ""
    assert result["lint"] == ""


def test_resolve_blt_empty_string_is_not_applicable_not_fallthrough(leerie, tmp_path):
    """A declared empty string means 'not applicable', not a fallthrough to inference."""
    leerie_dir = tmp_path / ".leerie"
    leerie_dir.mkdir()
    (leerie_dir / "config.toml").write_text('test = ""\n')
    # pyproject.toml would make inference return "pytest" — must be suppressed
    (tmp_path / "pyproject.toml").write_text("[tool]\nname='x'\n")
    result = leerie.resolve_blt(tmp_path)
    assert result["test"] == ""  # declared empty string preserved


def test_resolve_blt_config_overrides_inference_when_inference_returns_value(leerie, tmp_path):
    """Config takes precedence even when inference would return a non-empty value."""
    leerie_dir = tmp_path / ".leerie"
    leerie_dir.mkdir()
    (leerie_dir / "config.toml").write_text('test = "bundle exec rspec"\n')
    # rails repo — inference would pick "bin/rails test"
    (tmp_path / "Gemfile.lock").write_text("GEM\n  specs:\n")
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "rails").write_text("#!/usr/bin/env ruby\n")
    result = leerie.resolve_blt(tmp_path)
    assert result["test"] == "bundle exec rspec"
