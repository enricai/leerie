"""Tests for resolve_worker_timeout_sec() and resolve_worker_timeout_explicit().

Covers the CLI flag → env var → per-repo file → DEFAULT_CAPS resolution
order, positive-int validation, and the die() path for invalid values.
Mirrors the structure of test_resolve_worker_pids_max.py.

`worker_timeout_sec` is the GLOBAL per-worker wall-clock ceiling. It is
distinct from `resolve_worker_timeout(worker, caps)`, which picks the
ceiling for ONE worker out of `TIMEOUT_DEFAULT_PER_WORKER` — that table's
behaviour lives in test_worker_duration_distribution.py. What this file
owns is the value's own resolution, plus the separate explicitness signal
the table's bypass keys on.

Explicitness needs its own function because `_resolve_positive_int_pref`
returns a plain `int`: "5400 because the operator asked" and "5400 because
nothing was set" are indistinguishable downstream, which made an explicit
`--worker-timeout 5400` a silent no-op.

Override values in this file must differ from the default, or the test
passes vacuously against a resolver that ignores its input entirely.
"""
from __future__ import annotations

import pytest


@pytest.fixture
def repo_root(tmp_path, monkeypatch):
    """An empty repo-root directory with LEERIE_WORKER_TIMEOUT unset."""
    monkeypatch.delenv("LEERIE_WORKER_TIMEOUT", raising=False)
    return tmp_path


def test_default_cap_is_5400(leerie):
    assert leerie.DEFAULT_CAPS["worker_timeout_sec"] == 5400


def test_env_var_name(leerie):
    assert leerie.WORKER_TIMEOUT_ENV == "LEERIE_WORKER_TIMEOUT"


def test_file_is_the_shared_leerie_toml(leerie):
    assert leerie.WORKER_TIMEOUT_FILE == leerie.SOURCE_OF_TRUTH_FILE


# --- resolution order -------------------------------------------------------

def test_default_when_nothing_set(leerie, repo_root):
    assert (leerie.resolve_worker_timeout_sec(repo_root, None)
            == leerie.DEFAULT_CAPS["worker_timeout_sec"])


def test_file_value(leerie, repo_root):
    (repo_root / leerie.WORKER_TIMEOUT_FILE).write_text(
        "worker_timeout_sec = 1500\n")
    assert leerie.resolve_worker_timeout_sec(repo_root, None) == 1500


def test_env_value(leerie, repo_root, monkeypatch):
    monkeypatch.setenv("LEERIE_WORKER_TIMEOUT", "1200")
    assert leerie.resolve_worker_timeout_sec(repo_root, None) == 1200


def test_env_wins_over_file(leerie, repo_root, monkeypatch):
    (repo_root / leerie.WORKER_TIMEOUT_FILE).write_text(
        "worker_timeout_sec = 1500\n")
    monkeypatch.setenv("LEERIE_WORKER_TIMEOUT", "1200")
    assert leerie.resolve_worker_timeout_sec(repo_root, None) == 1200


def test_cli_wins_over_env_and_file(leerie, repo_root, monkeypatch):
    (repo_root / leerie.WORKER_TIMEOUT_FILE).write_text(
        "worker_timeout_sec = 1500\n")
    monkeypatch.setenv("LEERIE_WORKER_TIMEOUT", "1200")
    assert leerie.resolve_worker_timeout_sec(repo_root, 900) == 900


def test_cli_none_falls_back(leerie, repo_root, monkeypatch):
    monkeypatch.setenv("LEERIE_WORKER_TIMEOUT", "1200")
    assert leerie.resolve_worker_timeout_sec(repo_root, None) == 1200


# --- validation -------------------------------------------------------------

def test_bad_env_value_dies(leerie, repo_root, monkeypatch):
    monkeypatch.setenv("LEERIE_WORKER_TIMEOUT", "not-a-number")
    with pytest.raises(SystemExit):
        leerie.resolve_worker_timeout_sec(repo_root, None)


def test_zero_env_value_dies(leerie, repo_root, monkeypatch):
    monkeypatch.setenv("LEERIE_WORKER_TIMEOUT", "0")
    with pytest.raises(SystemExit):
        leerie.resolve_worker_timeout_sec(repo_root, None)


def test_negative_env_value_dies(leerie, repo_root, monkeypatch):
    monkeypatch.setenv("LEERIE_WORKER_TIMEOUT", "-30")
    with pytest.raises(SystemExit):
        leerie.resolve_worker_timeout_sec(repo_root, None)


def test_bad_file_value_dies(leerie, repo_root):
    (repo_root / leerie.WORKER_TIMEOUT_FILE).write_text(
        "worker_timeout_sec = soon\n")
    with pytest.raises(SystemExit):
        leerie.resolve_worker_timeout_sec(repo_root, None)


def test_zero_file_value_dies(leerie, repo_root):
    (repo_root / leerie.WORKER_TIMEOUT_FILE).write_text(
        "worker_timeout_sec = 0\n")
    with pytest.raises(SystemExit):
        leerie.resolve_worker_timeout_sec(repo_root, None)


def test_empty_env_treated_as_unset(leerie, repo_root, monkeypatch):
    monkeypatch.setenv("LEERIE_WORKER_TIMEOUT", "")
    assert (leerie.resolve_worker_timeout_sec(repo_root, None)
            == leerie.DEFAULT_CAPS["worker_timeout_sec"])


def test_whitespace_only_env_treated_as_unset(leerie, repo_root, monkeypatch):
    monkeypatch.setenv("LEERIE_WORKER_TIMEOUT", "   ")
    assert (leerie.resolve_worker_timeout_sec(repo_root, None)
            == leerie.DEFAULT_CAPS["worker_timeout_sec"])


# --- explicitness -----------------------------------------------------------
#
# The table's bypass keys on this, not on the resolved value, so these are
# the tests that keep `--worker-timeout 5400` from becoming a no-op again.

def test_not_explicit_when_nothing_set(leerie, repo_root):
    assert leerie.resolve_worker_timeout_explicit(repo_root, None) is False


def test_explicit_via_cli(leerie, repo_root):
    assert leerie.resolve_worker_timeout_explicit(repo_root, 900) is True


def test_explicit_via_cli_even_at_the_default(leerie, repo_root):
    """The case that motivated separating value from explicitness."""
    default = leerie.DEFAULT_CAPS["worker_timeout_sec"]
    assert leerie.resolve_worker_timeout_explicit(repo_root, default) is True


def test_explicit_via_env(leerie, repo_root, monkeypatch):
    monkeypatch.setenv("LEERIE_WORKER_TIMEOUT", "1200")
    assert leerie.resolve_worker_timeout_explicit(repo_root, None) is True


def test_explicit_via_env_even_at_the_default(leerie, repo_root, monkeypatch):
    monkeypatch.setenv(
        "LEERIE_WORKER_TIMEOUT",
        str(leerie.DEFAULT_CAPS["worker_timeout_sec"]))
    assert leerie.resolve_worker_timeout_explicit(repo_root, None) is True


def test_explicit_via_file(leerie, repo_root):
    (repo_root / leerie.WORKER_TIMEOUT_FILE).write_text(
        "worker_timeout_sec = 1500\n")
    assert leerie.resolve_worker_timeout_explicit(repo_root, None) is True


def test_empty_env_is_not_explicit(leerie, repo_root, monkeypatch):
    """Matches the resolver, which treats an empty value as unset — the two
    must agree or an empty env var bypasses the table while resolving to the
    default."""
    monkeypatch.setenv("LEERIE_WORKER_TIMEOUT", "")
    assert leerie.resolve_worker_timeout_explicit(repo_root, None) is False


def test_explicitness_agrees_with_the_resolver_on_every_tier(leerie, repo_root,
                                                             monkeypatch):
    """Guard-the-guard: the two functions walk the same three tiers
    separately, so they can drift. Whenever a tier changes the resolved
    value, explicitness must also be True."""
    default = leerie.DEFAULT_CAPS["worker_timeout_sec"]
    cases = [
        ("cli", lambda: None, 900),
        ("env", lambda: monkeypatch.setenv("LEERIE_WORKER_TIMEOUT", "1200"), None),
        ("file", lambda: (repo_root / leerie.WORKER_TIMEOUT_FILE).write_text(
            "worker_timeout_sec = 1500\n"), None),
    ]
    for label, setup, cli in cases:
        monkeypatch.delenv("LEERIE_WORKER_TIMEOUT", raising=False)
        cfg = repo_root / leerie.WORKER_TIMEOUT_FILE
        if cfg.exists():
            cfg.unlink()
        setup()
        value = leerie.resolve_worker_timeout_sec(repo_root, cli)
        explicit = leerie.resolve_worker_timeout_explicit(repo_root, cli)
        assert value != default, f"{label}: test setup did not change the value"
        assert explicit is True, (
            f"{label}: the value changed but explicitness reported False — "
            "the two resolvers have drifted")
