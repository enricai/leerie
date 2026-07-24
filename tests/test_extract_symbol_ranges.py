"""Tests for _extract_symbol_ranges() (DESIGN §5½ (P1) *Sub-file*).

The tier-1 function-boundary partition reads per-symbol line ranges from
tree-sitter's structure API (`item.span.start_line`/`end_line`) — the data the
repo-map's `_parse_repo_file` deliberately discards. These tests verify the
dedicated extractor returns sane, ordered, in-bounds ranges on a real file.

Gated on the shared HAS_TREESITTER functional probe (conftest), mirroring
test_build_repo_map.py: an absent/incompatible parser skips cleanly rather than
failing (the sub-file splitter itself falls back to tier-2 line-windows in that
case, covered host-independently in test_recursive_decompose.py).
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from tests.conftest import HAS_TREESITTER

pytestmark = pytest.mark.skipif(
    not HAS_TREESITTER,
    reason="tree-sitter parser unavailable or incompatible (no ranges)",
)


REPO_ROOT = Path(__file__).resolve().parent.parent
LEERIE_PY = REPO_ROOT / "orchestrator" / "leerie.py"


@pytest.fixture(scope="session")
def leerie():
    spec = importlib.util.spec_from_file_location("leerie", LEERIE_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_extracts_ordered_in_bounds_ranges(leerie, tmp_path):
    f = tmp_path / "mod.py"
    f.write_text(
        "def first():\n"        # line 1
        "    return 1\n"        # line 2
        "\n"                    # line 3
        "def second():\n"       # line 4
        "    x = 1\n"           # line 5
        "    return x\n"        # line 6
        "\n"                    # line 7
        "def third():\n"        # line 8
        "    return 3\n"        # line 9
    )
    total = f.read_text().count("\n") + 1
    ranges = leerie._extract_symbol_ranges(f)

    names = [r[0] for r in ranges]
    assert "first" in names and "second" in names and "third" in names
    # Sorted by start line, and every range is within [1, total].
    starts = [r[1] for r in ranges]
    assert starts == sorted(starts)
    for _name, s, e in ranges:
        assert 1 <= s <= e <= total
    # `second` spans more than one line (a multi-line body).
    second = next(r for r in ranges if r[0] == "second")
    assert second[2] > second[1]


def test_unsupported_extension_returns_empty(leerie, tmp_path):
    f = tmp_path / "data.unknownext"
    f.write_text("nothing parseable here\n")
    assert leerie._extract_symbol_ranges(f) == []


def test_missing_file_returns_empty(leerie, tmp_path):
    assert leerie._extract_symbol_ranges(tmp_path / "nope.py") == []
