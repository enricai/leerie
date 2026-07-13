"""Shared pytest fixtures for the leerie test suite.

leerie.py is a single script (no package), so we load it once as a
module via importlib and expose it to every test via the `leerie`
fixture.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LEERIE_PY = REPO_ROOT / "orchestrator" / "leerie.py"


@pytest.fixture(scope="session")
def leerie():
    """The leerie module loaded from orchestrator/leerie.py."""
    spec = importlib.util.spec_from_file_location("leerie", LEERIE_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _has_treesitter() -> bool:
    """True only if the installed tree-sitter stack can actually extract a
    symbol. Delegates to leerie's own `_tree_sitter_extraction_works()`
    functional probe (mere importability is insufficient — an
    installed-but-incompatible language-pack version imports fine yet extracts
    nothing). Shared here so extraction-dependent repo-map test modules gate
    on it without duplication."""
    spec = importlib.util.spec_from_file_location("_leerie_ts_probe", LEERIE_PY)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
        return mod._tree_sitter_extraction_works()
    except Exception:
        return False


# Evaluated once at collection; extraction-dependent repo-map test modules
# import this to skip cleanly on hosts without a working parser.
HAS_TREESITTER = _has_treesitter()
