"""Unit tests for partition_files() — the deterministic chunker (DESIGN §P1).

Verifies the 'complete by construction' invariant: union of chunks == input
set, no file in two chunks, chunk sizes bounded by the target. Named
parametrized cases include the telemetry sweeps (29-file migration, 64-file
date-fns) that drove the design (LLM dropped 14/29; code partition is 100%
coverage by construction).
"""
from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Parametrized invariant sweep
# ---------------------------------------------------------------------------

def _make_files(n: int, prefix: str = "src/f") -> list[str]:
    return [f"{prefix}{i:03d}.py" for i in range(n)]


# (label, files, chunk_size)
CASES = [
    ("empty", [], 8),
    ("single-file", _make_files(1), 8),
    ("eight-files-exact", _make_files(8), 8),
    ("29-file-migration-sweep", _make_files(29, "src/module"), 8),
    ("64-file-date-fns-sweep", [f"src/date-fns/migrate_{i:03d}.ts" for i in range(64)], 8),
    ("chunk-size-1", _make_files(5), 1),
    ("chunk-size-equals-n", _make_files(8), 8),
    ("chunk-size-larger-than-n", _make_files(3), 10),
    ("partial-last-chunk", _make_files(10), 3),
]


@pytest.mark.parametrize("label,files,chunk_size", CASES, ids=[c[0] for c in CASES])
def test_coverage_100_percent(leerie, label, files, chunk_size):
    """Every input file appears in the output exactly once."""
    chunks = leerie.partition_files(files, chunk_size)
    flat = [f for chunk in chunks for f in chunk]
    assert len(flat) == len(files)
    assert sorted(flat) == sorted(files)


@pytest.mark.parametrize("label,files,chunk_size", CASES, ids=[c[0] for c in CASES])
def test_zero_overlap(leerie, label, files, chunk_size):
    """No file appears in more than one chunk."""
    chunks = leerie.partition_files(files, chunk_size)
    seen: set[str] = set()
    for chunk in chunks:
        for f in chunk:
            assert f not in seen, f"{f!r} appears in multiple chunks"
            seen.add(f)


@pytest.mark.parametrize("label,files,chunk_size", CASES, ids=[c[0] for c in CASES])
def test_chunk_size_bounded(leerie, label, files, chunk_size):
    """Every chunk is at most chunk_size (last chunk may be smaller)."""
    chunks = leerie.partition_files(files, chunk_size)
    for chunk in chunks:
        assert len(chunk) <= max(chunk_size, 1)


@pytest.mark.parametrize("label,files,chunk_size", CASES, ids=[c[0] for c in CASES])
def test_order_preserved(leerie, label, files, chunk_size):
    """Files appear in the same order across all chunks as in the input."""
    chunks = leerie.partition_files(files, chunk_size)
    flat = [f for chunk in chunks for f in chunk]
    assert flat == files


# ---------------------------------------------------------------------------
# Structural / edge-case tests
# ---------------------------------------------------------------------------

def test_empty_input_returns_empty_list(leerie):
    assert leerie.partition_files([], 8) == []


def test_single_file_one_chunk(leerie):
    assert leerie.partition_files(["a.py"], 8) == [["a.py"]]


def test_exact_multiple_chunk_count(leerie):
    files = _make_files(16)
    chunks = leerie.partition_files(files, 8)
    assert len(chunks) == 2
    assert chunks[0] == files[:8]
    assert chunks[1] == files[8:]


def test_partial_last_chunk_sizes(leerie):
    files = _make_files(10)
    chunks = leerie.partition_files(files, 8)
    assert len(chunks) == 2
    assert len(chunks[0]) == 8
    assert len(chunks[1]) == 2


def test_chunk_size_degenerate_zero_returns_single_chunk(leerie):
    """chunk_size < 1 returns all files in a single chunk (degenerate guard)."""
    files = ["a.py", "b.py"]
    chunks = leerie.partition_files(files, 0)
    assert chunks == [["a.py", "b.py"]]


def test_chunk_size_1_one_file_per_chunk(leerie):
    files = ["a.py", "b.py", "c.py"]
    chunks = leerie.partition_files(files, 1)
    assert chunks == [["a.py"], ["b.py"], ["c.py"]]


# ---------------------------------------------------------------------------
# Telemetry-case named assertions (documented proof of 'complete by
# construction' for the exact sweeps reported in F1-build-measure.md)
# ---------------------------------------------------------------------------

def test_29_file_migration_sweep_complete(leerie):
    """29-file migration sweep: 100% coverage + 0 overlap + bounded chunks.

    The LLM splitter silently dropped 14/29 files in this exact scenario.
    code-partition is complete by construction.
    """
    files = [f"src/module{i:03d}.ts" for i in range(29)]
    chunks = leerie.partition_files(files, 8)

    flat = [f for chunk in chunks for f in chunk]
    assert sorted(flat) == sorted(files), "coverage: all 29 files must appear"

    seen: set[str] = set()
    for chunk in chunks:
        for f in chunk:
            assert f not in seen, f"{f!r} duplicated across chunks"
            seen.add(f)

    assert all(len(c) <= 8 for c in chunks), "every chunk must be <= 8 files"
    assert len(chunks) == 4, "29 files / 8 = 3 full + 1 partial → 4 chunks"
    assert len(chunks[-1]) == 5, "last chunk has 29 % 8 = 5 files"


def test_64_file_date_fns_sweep_complete(leerie):
    """64-file date-fns sweep: 100% coverage + 0 overlap + bounded chunks.

    This is the dominant telemetry case (84% of affected runs) where leerie
    jammed all 64 files into one subtask, exhausting the worker's budget
    mid-execution. partition_files covers all 64 by construction.
    """
    files = [f"src/date-fns/migrate_{i:03d}.ts" for i in range(64)]
    chunks = leerie.partition_files(files, 8)

    flat = [f for chunk in chunks for f in chunk]
    assert sorted(flat) == sorted(files), "coverage: all 64 files must appear"

    seen: set[str] = set()
    for chunk in chunks:
        for f in chunk:
            assert f not in seen, f"{f!r} duplicated across chunks"
            seen.add(f)

    assert all(len(c) <= 8 for c in chunks), "every chunk must be <= 8 files"
    assert len(chunks) == 8, "64 files / 8 = exactly 8 chunks"
    assert all(len(c) == 8 for c in chunks), "all chunks exactly 8 (64 is exact multiple)"
