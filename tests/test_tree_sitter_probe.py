"""Unit tests for _tree_sitter_extraction_works() (DESIGN §12 "Finding A"
gating fix).

This functional probe is the single predicate the whole gating fix rests
on: build_repo_map's degrade-warning check (G6) and every HAS_TREESITTER
skip gate (tests/conftest.py) call it to distinguish a broken/incompatible
parser from a legitimately symbol-less repo. A regression that breaks the
probe would either silently un-skip the 19 host-sensitive tree-sitter tests
on an incompatible host, or falsely skip them on a healthy one.

Branches (2) and (3) stub _parse_repo_file (mirrors the pattern in
test_repo_map_degrade_warning.py) so they run host-independently. Branch
(1) exercises the real, unstubbed _parse_repo_file and is gated on
HAS_TREESITTER so it skips (not fails) on hosts without a working parser.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from tests.conftest import HAS_TREESITTER


@pytest.mark.skipif(
    not HAS_TREESITTER,
    reason="tree-sitter parser unavailable or incompatible "
           "(no symbol extraction)",
)
def test_returns_true_when_parser_extracts_probe_symbol(leerie):
    """On a host with a working tree-sitter stack, the probe's own
    '_probe_sym' snippet round-trips through the real _parse_repo_file."""
    assert leerie._tree_sitter_extraction_works() is True


def test_returns_false_when_parse_repo_file_raises(leerie):
    """Host-independent: simulates the installed-but-incompatible parser
    scenario (e.g. language-pack 0.9.0 lacking process()) where
    _parse_repo_file's internals would raise. The probe's own except clause
    must swallow it and return False, not propagate."""
    def _raise(path):
        raise AttributeError("module 'tree_sitter_language_pack' has no "
                              "attribute 'process'")

    with patch.object(leerie, "_parse_repo_file", new=_raise):
        assert leerie._tree_sitter_extraction_works() is False


def test_returns_false_when_parse_repo_file_extracts_nothing(leerie):
    """Host-independent: simulates a parser that imports fine and returns
    cleanly but extracts no symbols (_parse_repo_file's own graceful-degrade
    contract). The probe must treat an empty defs list as non-functional."""
    def _empty_parse(path):
        return [], []

    with patch.object(leerie, "_parse_repo_file", new=_empty_parse):
        assert leerie._tree_sitter_extraction_works() is False
