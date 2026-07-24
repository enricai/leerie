"""Tests for the sub-file partition helpers (DESIGN §5½ (P1) *Sub-file*).

Covers the two deterministic tiers and the coverage backstop:
  - partition_lines()            — tier 2: contiguous line-window tiling
  - partition_symbols_by_line()  — tier 1: function-boundary tiling of [1, EOF]
  - _check_intra_file_surface()  — zero-tolerance coverage/overlap backstop

Pure functions, no LLM, no tree-sitter (ranges are passed in directly), so these
run on any host. The by-construction guarantee (100% coverage, 0 overlap) is the
whole point — every test asserts the union tiles the range exactly.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
LEERIE_PY = REPO_ROOT / "orchestrator" / "leerie.py"


@pytest.fixture(scope="session")
def leerie():
    spec = importlib.util.spec_from_file_location("leerie", LEERIE_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _tiles_exactly(regions, lo, hi):
    """True iff `regions` cover [lo, hi] with no gap and no overlap."""
    covered = set()
    for a, b in regions:
        for ln in range(a, b + 1):
            if ln in covered:
                return False  # overlap
            covered.add(ln)
    return covered == set(range(lo, hi + 1))


# ---------------------------------------------------------------------------
# partition_lines — tier 2
# ---------------------------------------------------------------------------

def test_partition_lines_exact_multiple(leerie):
    w = leerie.partition_lines(1, 600, 200)
    assert w == [(1, 200), (201, 400), (401, 600)]
    assert _tiles_exactly(w, 1, 600)


def test_partition_lines_partial_last(leerie):
    w = leerie.partition_lines(1, 500, 200)
    assert w == [(1, 200), (201, 400), (401, 500)]
    assert _tiles_exactly(w, 1, 500)


def test_partition_lines_offset_range(leerie):
    # A region not starting at line 1 (the tier-2 re-entry case).
    w = leerie.partition_lines(5113, 6846, 700)
    assert _tiles_exactly(w, 5113, 6846)
    assert all((b - a + 1) <= 700 for a, b in w)


def test_partition_lines_single_window(leerie):
    assert leerie.partition_lines(1, 100, 500) == [(1, 100)]


def test_partition_lines_empty_range(leerie):
    assert leerie.partition_lines(10, 5, 200) == []


def test_partition_lines_degenerate_span(leerie):
    # max_span < 1 → whole range as one window (mirrors partition_files guard).
    assert leerie.partition_lines(1, 100, 0) == [(1, 100)]


def test_partition_lines_span_one(leerie):
    w = leerie.partition_lines(1, 3, 1)
    assert w == [(1, 1), (2, 2), (3, 3)]
    assert _tiles_exactly(w, 1, 3)


# ---------------------------------------------------------------------------
# partition_symbols_by_line — tier 1
# ---------------------------------------------------------------------------

def test_symbols_tile_whole_file(leerie):
    # Three small functions in a 300-line file, cap comfortably above total.
    ranges = [("a", 10, 40), ("b", 100, 150), ("c", 200, 260)]
    regions = leerie.partition_symbols_by_line(ranges, 300, 700)
    assert regions == [(1, 300)]  # all fit in one region
    assert _tiles_exactly(regions, 1, 300)


def test_symbols_split_when_over_cap(leerie):
    # Functions that force >1 region when max_span is small.
    ranges = [("a", 1, 100), ("b", 101, 200), ("c", 201, 300),
              ("d", 301, 400)]
    regions = leerie.partition_symbols_by_line(ranges, 400, 150)
    assert len(regions) >= 2
    assert _tiles_exactly(regions, 1, 400)
    # Leading region absorbs the file start; final region reaches EOF.
    assert regions[0][0] == 1
    assert regions[-1][1] == 400


def test_symbols_gap_attaches_to_preceding(leerie):
    # A large inter-symbol gap must not create an uncovered hole.
    ranges = [("a", 10, 20), ("b", 500, 520)]
    regions = leerie.partition_symbols_by_line(ranges, 600, 700)
    assert _tiles_exactly(regions, 1, 600)


def test_symbols_giant_function_is_own_region(leerie):
    # The 1,733-line-function case: one symbol whose span alone exceeds the cap
    # must come back as its own (oversized) region — tier 2 handles it later.
    ranges = [("small1", 1, 100), ("giant", 200, 1900), ("small2", 1901, 1950)]
    regions = leerie.partition_symbols_by_line(ranges, 2000, 700)
    assert _tiles_exactly(regions, 1, 2000)
    # Exactly one region should exceed the cap (the giant), proving function
    # boundaries were respected rather than force-merged.
    oversized = [(a, b) for a, b in regions if (b - a + 1) > 700]
    assert len(oversized) == 1


def test_symbols_no_ranges_returns_whole_file(leerie):
    # No usable ranges → single whole-file region (caller falls back to tier 2).
    assert leerie.partition_symbols_by_line([], 500, 700) == [(1, 500)]


def test_symbols_ignores_out_of_bounds_ranges(leerie):
    # A range past EOF (stale/garbage) is dropped, not trusted.
    ranges = [("ok", 1, 50), ("bad", 900, 1200)]
    regions = leerie.partition_symbols_by_line(ranges, 500, 700)
    assert _tiles_exactly(regions, 1, 500)


def test_symbols_empty_file(leerie):
    assert leerie.partition_symbols_by_line([], 0, 700) == []


# ---------------------------------------------------------------------------
# compose: tier 1 then tier 2 on the oversized region
# ---------------------------------------------------------------------------

def test_compose_tier1_then_tier2_tiles_file(leerie):
    """A file with one giant function among small ones: tier-1 isolates the
    giant as its own region, tier-2 splits THAT region, and the full set of
    final windows still tiles [1, EOF] exactly — the end-to-end guarantee."""
    total = 2000
    ranges = [("s1", 1, 100), ("giant", 200, 1900), ("s2", 1901, 1950)]
    cap = 700
    tier1 = leerie.partition_symbols_by_line(ranges, total, cap)
    final = []
    for lo, hi in tier1:
        if (hi - lo + 1) > cap:
            final.extend(leerie.partition_lines(lo, hi, cap))
        else:
            final.append((lo, hi))
    assert _tiles_exactly(final, 1, total)
    assert all((b - a + 1) <= cap for a, b in final)


# ---------------------------------------------------------------------------
# _check_intra_file_surface — zero-tolerance backstop
# ---------------------------------------------------------------------------

def test_surface_ok_on_exact_tiling(leerie):
    assert leerie._check_intra_file_surface([(1, 200), (201, 400)], 400) == []


def test_surface_flags_gap(leerie):
    issues = leerie._check_intra_file_surface([(1, 100), (201, 400)], 400)
    assert any("INTRA_FILE_UNCOVERED" in i for i in issues)


def test_surface_flags_overlap(leerie):
    issues = leerie._check_intra_file_surface([(1, 250), (200, 400)], 400)
    assert any("INTRA_FILE_OVERLAP" in i for i in issues)


def test_surface_flags_out_of_range(leerie):
    issues = leerie._check_intra_file_surface([(1, 500)], 400)
    assert any("INTRA_FILE_OUT_OF_RANGE" in i for i in issues)


def test_surface_empty_file_no_issues(leerie):
    assert leerie._check_intra_file_surface([], 0) == []
