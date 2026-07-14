"""Tests for _parse_memory_size, _auto_worker_memory_max, and
resolve_worker_memory_max — the resolver chain for the per-worker
cgroup memory cap.

Covers:
- Memory-size parsing: K/M/G/T suffixes, bare bytes, garbage rejected.
- Auto-derivation from /proc/meminfo (mocked) — splits VM ram across
  max_parallel+1 slots, floored at 8 GiB (build + resident claude
  measured peak ~6.3 GiB).
- Resolution order: CLI > env > leerie.toml > auto.
- die() paths for invalid env / file values.
"""
from __future__ import annotations

import pytest


# ---- _parse_memory_size ---------------------------------------------------

def test_parse_memory_size_bytes(leerie):
    assert leerie._parse_memory_size("1024", "ctx") == 1024


def test_parse_memory_size_kib(leerie):
    assert leerie._parse_memory_size("4K", "ctx") == 4 * 1024


def test_parse_memory_size_mib(leerie):
    assert leerie._parse_memory_size("512M", "ctx") == 512 * 1024**2


def test_parse_memory_size_gib(leerie):
    assert leerie._parse_memory_size("4G", "ctx") == 4 * 1024**3


def test_parse_memory_size_tib(leerie):
    assert leerie._parse_memory_size("1T", "ctx") == 1024**4


def test_parse_memory_size_lowercase_suffix(leerie):
    assert leerie._parse_memory_size("2g", "ctx") == 2 * 1024**3


def test_parse_memory_size_whitespace_tolerated(leerie):
    assert leerie._parse_memory_size("  256M  ", "ctx") == 256 * 1024**2


def test_parse_memory_size_empty_dies(leerie):
    with pytest.raises(SystemExit):
        leerie._parse_memory_size("", "ctx")


def test_parse_memory_size_garbage_dies(leerie):
    with pytest.raises(SystemExit):
        leerie._parse_memory_size("4XYZ", "ctx")


def test_parse_memory_size_negative_dies(leerie):
    with pytest.raises(SystemExit):
        leerie._parse_memory_size("-4G", "ctx")


def test_parse_memory_size_zero_dies(leerie):
    with pytest.raises(SystemExit):
        leerie._parse_memory_size("0", "ctx")


def test_parse_memory_size_fractional_rejected(leerie):
    """We reject '1.5G' rather than rounding silently. The user can
    write '1536M' if they need fractional values."""
    with pytest.raises(SystemExit):
        leerie._parse_memory_size("1.5G", "ctx")


# ---- _auto_worker_memory_max ---------------------------------------------

def test_auto_splits_meminfo_across_slots(leerie, monkeypatch, tmp_path):
    """Synthesize a /proc/meminfo with 128 GiB total. With max_parallel=4
    the per-worker share is 128 / 5 = 25.6 GiB; that's above the 8 GiB
    floor, so the even split wins."""
    meminfo = tmp_path / "meminfo"
    meminfo.write_text(f"MemTotal:       {128 * 1024 * 1024} kB\n")
    import builtins
    real_open = builtins.open

    def fake_open(path, *a, **kw):
        if str(path) == "/proc/meminfo":
            return real_open(meminfo, *a, **kw)
        return real_open(path, *a, **kw)
    monkeypatch.setattr("builtins.open", fake_open)
    result = leerie._auto_worker_memory_max(max_parallel=4)
    expected = (128 * 1024**3) // 5
    assert result == expected


def test_auto_floors_at_8gib(leerie, monkeypatch, tmp_path):
    """With a 16 GiB VM / max_parallel=5, the even split (16 / 6 ≈
    2.67 GiB) is well under the measured 6.3 GiB build+claude peak
    (see docstring on _auto_worker_memory_max), so the 8 GiB floor
    wins — the fix for build-running workers cgroup-OOMing under the
    old 4 GiB clamp."""
    meminfo = tmp_path / "meminfo"
    meminfo.write_text(f"MemTotal:       {16 * 1024 * 1024} kB\n")
    import builtins
    real_open = builtins.open

    def fake_open(path, *a, **kw):
        if str(path) == "/proc/meminfo":
            return real_open(meminfo, *a, **kw)
        return real_open(path, *a, **kw)
    monkeypatch.setattr("builtins.open", fake_open)
    assert leerie._auto_worker_memory_max(max_parallel=5) == 8 * 1024**3


def test_auto_fallback_when_meminfo_missing(leerie, monkeypatch):
    """No /proc/meminfo on macOS host (where the test suite usually
    runs). Auto returns the 2 GiB fallback."""
    import builtins
    real_open = builtins.open

    def fake_open(path, *a, **kw):
        if str(path) == "/proc/meminfo":
            raise FileNotFoundError(2, "No such file", "/proc/meminfo")
        return real_open(path, *a, **kw)
    monkeypatch.setattr("builtins.open", fake_open)
    assert leerie._auto_worker_memory_max(max_parallel=4) == 2 * 1024**3


# ---- resolve_worker_memory_max --------------------------------------------

@pytest.fixture
def repo_root(tmp_path, monkeypatch):
    monkeypatch.delenv("LEERIE_WORKER_MEMORY_MAX", raising=False)
    return tmp_path


def test_cli_value_wins(leerie, repo_root, monkeypatch):
    (repo_root / "leerie.toml").write_text("worker_memory_max = 1G\n")
    monkeypatch.setenv("LEERIE_WORKER_MEMORY_MAX", "2G")
    assert leerie.resolve_worker_memory_max(
        repo_root, max_parallel=4, cli_value="4G") == 4 * 1024**3


def test_env_wins_over_file(leerie, repo_root, monkeypatch):
    (repo_root / "leerie.toml").write_text("worker_memory_max = 1G\n")
    monkeypatch.setenv("LEERIE_WORKER_MEMORY_MAX", "2G")
    assert leerie.resolve_worker_memory_max(
        repo_root, max_parallel=4) == 2 * 1024**3


def test_file_used_when_cli_and_env_unset(leerie, repo_root):
    (repo_root / "leerie.toml").write_text("worker_memory_max = 1G\n")
    assert leerie.resolve_worker_memory_max(
        repo_root, max_parallel=4) == 1024**3


def test_auto_fallback_when_nothing_set(leerie, repo_root):
    """No CLI, no env, no file — auto-derive from /proc/meminfo (or
    its 2 GiB fallback on macOS)."""
    result = leerie.resolve_worker_memory_max(repo_root, max_parallel=4)
    assert result > 0


def test_garbage_env_dies(leerie, repo_root, monkeypatch):
    monkeypatch.setenv("LEERIE_WORKER_MEMORY_MAX", "garbage")
    with pytest.raises(SystemExit):
        leerie.resolve_worker_memory_max(repo_root, max_parallel=4)


def test_garbage_file_dies(leerie, repo_root):
    (repo_root / "leerie.toml").write_text("worker_memory_max = garbage\n")
    with pytest.raises(SystemExit):
        leerie.resolve_worker_memory_max(repo_root, max_parallel=4)
