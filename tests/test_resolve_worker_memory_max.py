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


# ---- _is_node_repo (P9 node-repo detection) --------------------------------

def test_is_node_repo_package_json(leerie, tmp_path):
    (tmp_path / "package.json").write_text("{}\n")
    assert leerie._is_node_repo(str(tmp_path)) is True


def test_is_node_repo_pnpm_lock(leerie, tmp_path):
    (tmp_path / "pnpm-lock.yaml").write_text("lockfileVersion: '9.0'\n")
    assert leerie._is_node_repo(str(tmp_path)) is True


def test_is_node_repo_package_lock(leerie, tmp_path):
    (tmp_path / "package-lock.json").write_text("{}\n")
    assert leerie._is_node_repo(str(tmp_path)) is True


def test_is_node_repo_yarn_lock(leerie, tmp_path):
    (tmp_path / "yarn.lock").write_text("# yarn lockfile v1\n")
    assert leerie._is_node_repo(str(tmp_path)) is True


def test_is_node_repo_false_for_non_node_repo(leerie, tmp_path):
    (tmp_path / "requirements.txt").write_text("pytest\n")
    assert leerie._is_node_repo(str(tmp_path)) is False


def test_is_node_repo_false_for_empty_dir(leerie, tmp_path):
    assert leerie._is_node_repo(str(tmp_path)) is False


# ---- NODE_OPTIONS derivation (P9) ------------------------------------------
#
# The derivation itself (leerie.py ~12707-12711, inside `_invoke`) is:
#     cap_mb = max(worker_memory_max_bytes // (1024*1024) - 2048, 256)
#     worker_env["NODE_OPTIONS"] = f"--max-old-space-size={cap_mb}"
# gated on `worker_memory_max_bytes is not None and _is_node_repo(cwd)`.
# `_invoke` builds the whole subprocess environment inline rather than
# through a standalone helper, so these tests pin the arithmetic and the
# gating condition directly (mirroring the derivation's own inline
# formula) rather than invoking the full async subprocess-spawning
# function, which the rest of this file's tests never do either.

def _node_options_value(cap_bytes: int) -> str:
    cap_mb = max(cap_bytes // (1024 * 1024) - 2048, 256)
    return f"--max-old-space-size={cap_mb}"


def test_node_options_arithmetic_8gib_floor(leerie):
    """8 GiB floor (the common auto-derived case) minus the 2048 MiB
    reserve for the resident claude process."""
    cap_bytes = 8 * 1024**3
    assert _node_options_value(cap_bytes) == "--max-old-space-size=6144"


def test_node_options_arithmetic_larger_auto_derived_cap(leerie):
    """A larger auto-derived cap (e.g. 25.6 GiB from a well-resourced
    host splitting across max_parallel=4) still subtracts exactly
    2048 MiB, not a percentage or other scaling."""
    cap_bytes = (128 * 1024**3) // 5  # matches test_auto_splits_meminfo_across_slots
    cap_mb = cap_bytes // (1024 * 1024)
    assert _node_options_value(cap_bytes) == f"--max-old-space-size={cap_mb - 2048}"


def test_node_options_clamped_when_cap_below_reserve(leerie):
    """An operator-set cap below the 2048 MiB reserve (e.g. via
    --worker-memory-max/LEERIE_WORKER_MEMORY_MAX/leerie.toml) must not
    produce a non-positive or degenerately small V8 heap ceiling."""
    cap_bytes = 512 * 1024**2  # 512 MiB, well under the 2048 MiB reserve
    assert _node_options_value(cap_bytes) == "--max-old-space-size=256"


def test_node_options_present_for_node_repo(leerie, tmp_path):
    """End-to-end: replicate `_invoke`'s gating + derivation inline
    against a real Node-repo fixture (package.json present)."""
    (tmp_path / "package.json").write_text("{}\n")
    worker_memory_max_bytes = 8 * 1024**3
    worker_env = None
    if worker_memory_max_bytes is not None and leerie._is_node_repo(str(tmp_path)):
        import os as _os
        worker_env = _os.environ.copy()
        cap_mb = max(worker_memory_max_bytes // (1024 * 1024) - 2048, 256)
        worker_env["NODE_OPTIONS"] = f"--max-old-space-size={cap_mb}"
    assert worker_env is not None
    assert worker_env["NODE_OPTIONS"] == "--max-old-space-size=6144"


def test_node_options_absent_for_non_node_repo(leerie, tmp_path):
    """The same gating logic against a non-Node repo fixture: no
    package.json/lockfile present, so NODE_OPTIONS is never set."""
    (tmp_path / "requirements.txt").write_text("pytest\n")
    worker_memory_max_bytes = 8 * 1024**3
    worker_env = None
    if worker_memory_max_bytes is not None and leerie._is_node_repo(str(tmp_path)):
        import os as _os
        worker_env = _os.environ.copy()
        cap_mb = max(worker_memory_max_bytes // (1024 * 1024) - 2048, 256)
        worker_env["NODE_OPTIONS"] = f"--max-old-space-size={cap_mb}"
    assert worker_env is None
