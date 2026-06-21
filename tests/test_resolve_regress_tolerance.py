"""Tests for resolve_regress_tolerance().

The optional GLOBAL --regress tolerance override (DESIGN §14). Precedence
CLI > env (LEERIE_REGRESS_TOLERANCE) > leerie.toml (regress_tolerance) >
None, where None means "use each call_type's own manifest tolerance". Bad
floats and out-of-range values die() at startup. Mirrors the coverage the
other resolvers get (resolve_runtime, resolve_models).
"""
from __future__ import annotations

import pytest


def _clear_env(leerie, monkeypatch):
    monkeypatch.delenv(leerie.REGRESS_TOLERANCE_ENV, raising=False)


# --- precedence ----------------------------------------------------------

def test_cli_value_wins_over_env_and_toml(leerie, tmp_path, monkeypatch):
    monkeypatch.setenv(leerie.REGRESS_TOLERANCE_ENV, "0.30")
    (tmp_path / leerie.REGRESS_TOLERANCE_FILE).write_text("regress_tolerance = 0.40\n")
    assert leerie.resolve_regress_tolerance(tmp_path, 0.10) == 0.10


def test_env_used_when_no_cli(leerie, tmp_path, monkeypatch):
    monkeypatch.setenv(leerie.REGRESS_TOLERANCE_ENV, "0.25")
    (tmp_path / leerie.REGRESS_TOLERANCE_FILE).write_text("regress_tolerance = 0.40\n")
    assert leerie.resolve_regress_tolerance(tmp_path, None) == 0.25


def test_toml_used_when_no_cli_or_env(leerie, tmp_path, monkeypatch):
    _clear_env(leerie, monkeypatch)
    (tmp_path / leerie.REGRESS_TOLERANCE_FILE).write_text("regress_tolerance = 0.18\n")
    assert leerie.resolve_regress_tolerance(tmp_path, None) == 0.18


def test_none_when_nothing_set(leerie, tmp_path, monkeypatch):
    _clear_env(leerie, monkeypatch)
    assert leerie.resolve_regress_tolerance(tmp_path, None) is None


def test_blank_env_falls_through_to_toml(leerie, tmp_path, monkeypatch):
    # An empty/whitespace env var is ignored (stripped to "") and resolution
    # falls through to the toml value rather than die()ing on float("").
    monkeypatch.setenv(leerie.REGRESS_TOLERANCE_ENV, "   ")
    (tmp_path / leerie.REGRESS_TOLERANCE_FILE).write_text("regress_tolerance = 0.12\n")
    assert leerie.resolve_regress_tolerance(tmp_path, None) == 0.12


# --- boundaries (inclusive [0,1]) ---------------------------------------

@pytest.mark.parametrize("val", [0.0, 1.0, 0.5])
def test_in_range_values_accepted(leerie, tmp_path, monkeypatch, val):
    _clear_env(leerie, monkeypatch)
    assert leerie.resolve_regress_tolerance(tmp_path, val) == val


# --- die() paths ---------------------------------------------------------

def test_non_float_env_dies(leerie, tmp_path, monkeypatch):
    _clear_env(leerie, monkeypatch)
    monkeypatch.setenv(leerie.REGRESS_TOLERANCE_ENV, "notafloat")
    with pytest.raises(SystemExit):
        leerie.resolve_regress_tolerance(tmp_path, None)


def test_non_float_toml_dies(leerie, tmp_path, monkeypatch):
    _clear_env(leerie, monkeypatch)
    (tmp_path / leerie.REGRESS_TOLERANCE_FILE).write_text("regress_tolerance = wat\n")
    with pytest.raises(SystemExit):
        leerie.resolve_regress_tolerance(tmp_path, None)


@pytest.mark.parametrize("val", [-0.01, 1.01, 2.0])
def test_out_of_range_dies(leerie, tmp_path, monkeypatch, val):
    _clear_env(leerie, monkeypatch)
    with pytest.raises(SystemExit):
        leerie.resolve_regress_tolerance(tmp_path, val)
