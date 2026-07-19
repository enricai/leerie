"""Tests for resolve_worker_pids_max() and the worker_pids_max cap.

Covers the CLI flag → env var → per-repo file → DEFAULT_CAPS resolution
order, positive-int validation, and the die() path for invalid values.
The cap bounds fork/clone in each claude -p worker subtree (cgroup v2
pids.max); the default was raised from 256 to 1024 so a legitimate
subprocess-heavy conformance run (e.g. leerie's own full test suite) no
longer bursts past the cap and wedges the worker, and then from 1024 to
2048 after a conformer exhausted 1024 by running the full suite twice in
one round. Mirrors the structure of test_resolve_max_workers.py.

Override values in this file must differ from the default, or the test
passes vacuously against a resolver that ignores its input entirely.
"""
from __future__ import annotations

import pytest


@pytest.fixture
def repo_root(tmp_path, monkeypatch):
    """An empty repo-root directory with LEERIE_WORKER_PIDS_MAX unset."""
    monkeypatch.delenv("LEERIE_WORKER_PIDS_MAX", raising=False)
    return tmp_path


def test_default_cap_is_2048(leerie):
    assert leerie.DEFAULT_CAPS["worker_pids_max"] == 2048


def test_default_when_nothing_set(leerie, repo_root):
    assert leerie.resolve_worker_pids_max(repo_root) == 2048


def test_file_value(leerie, repo_root):
    (repo_root / "leerie.toml").write_text("worker_pids_max = 4096\n")
    assert leerie.resolve_worker_pids_max(repo_root) == 4096


def test_env_value(leerie, repo_root, monkeypatch):
    monkeypatch.setenv("LEERIE_WORKER_PIDS_MAX", "512")
    assert leerie.resolve_worker_pids_max(repo_root) == 512


def test_env_wins_over_file(leerie, repo_root, monkeypatch):
    (repo_root / "leerie.toml").write_text("worker_pids_max = 4096\n")
    monkeypatch.setenv("LEERIE_WORKER_PIDS_MAX", "512")
    assert leerie.resolve_worker_pids_max(repo_root) == 512


def test_cli_wins_over_env_and_file(leerie, repo_root, monkeypatch):
    # All three tiers hold distinct values, so the assertion can only pass
    # if CLI genuinely won — not if the file or env value leaked through.
    (repo_root / "leerie.toml").write_text("worker_pids_max = 8192\n")
    monkeypatch.setenv("LEERIE_WORKER_PIDS_MAX", "512")
    assert leerie.resolve_worker_pids_max(repo_root, cli_value=4096) == 4096


def test_cli_none_falls_back(leerie, repo_root, monkeypatch):
    monkeypatch.setenv("LEERIE_WORKER_PIDS_MAX", "512")
    assert leerie.resolve_worker_pids_max(repo_root, cli_value=None) == 512


def test_bad_env_value_dies(leerie, repo_root, monkeypatch, capsys):
    monkeypatch.setenv("LEERIE_WORKER_PIDS_MAX", "not-a-number")
    with pytest.raises(SystemExit) as exc:
        leerie.resolve_worker_pids_max(repo_root)
    assert exc.value.code != 0
    err = capsys.readouterr().err
    assert "not a positive integer" in err


def test_zero_env_value_dies(leerie, repo_root, monkeypatch, capsys):
    monkeypatch.setenv("LEERIE_WORKER_PIDS_MAX", "0")
    with pytest.raises(SystemExit) as exc:
        leerie.resolve_worker_pids_max(repo_root)
    assert exc.value.code != 0
    err = capsys.readouterr().err
    assert "not a positive integer" in err


def test_negative_env_value_dies(leerie, repo_root, monkeypatch, capsys):
    monkeypatch.setenv("LEERIE_WORKER_PIDS_MAX", "-3")
    with pytest.raises(SystemExit) as exc:
        leerie.resolve_worker_pids_max(repo_root)
    assert exc.value.code != 0


def test_bad_file_value_dies(leerie, repo_root, capsys):
    (repo_root / "leerie.toml").write_text("worker_pids_max = bogus\n")
    with pytest.raises(SystemExit) as exc:
        leerie.resolve_worker_pids_max(repo_root)
    assert exc.value.code != 0
    err = capsys.readouterr().err
    assert "not a positive integer" in err


def test_zero_file_value_dies(leerie, repo_root, capsys):
    (repo_root / "leerie.toml").write_text("worker_pids_max = 0\n")
    with pytest.raises(SystemExit) as exc:
        leerie.resolve_worker_pids_max(repo_root)
    assert exc.value.code != 0


def test_empty_env_treated_as_unset(leerie, repo_root, monkeypatch):
    monkeypatch.setenv("LEERIE_WORKER_PIDS_MAX", "")
    assert leerie.resolve_worker_pids_max(repo_root) == 2048


def test_whitespace_only_env_treated_as_unset(leerie, repo_root, monkeypatch):
    monkeypatch.setenv("LEERIE_WORKER_PIDS_MAX", "   ")
    assert leerie.resolve_worker_pids_max(repo_root) == 2048
