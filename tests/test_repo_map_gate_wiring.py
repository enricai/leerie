"""Source-coupling pins for the HAS_TREESITTER gate wiring (DESIGN §12
"Finding A" gating fix).

Pins the coupling that makes the 19 host-sensitive tree-sitter tests skip
cleanly instead of failing on an incompatible host:
  1. conftest._has_treesitter() delegates to leerie's own
     _tree_sitter_extraction_works() functional probe — not a bare
     ImportError-only check — so an installed-but-incompatible
     language-pack version (imports fine, extracts nothing) is caught.
  2. conftest exposes a module-level HAS_TREESITTER bool.
  3. Each of the three extraction-dependent test modules
     (test_build_repo_map.py, test_repo_map.py,
     test_phase_plan_repo_map_ctx.py) imports HAS_TREESITTER from
     tests.conftest AND gates on it via a skipif (module- or
     class-level).

A silent regression here — reverting to an ImportError-only gate, or
dropping the skipif from one file — re-introduces the exact 19-test
failure on incompatible hosts with no other signal. Mirrors the
inspect.getsource/AST source-coupling convention used elsewhere in the
suite (test_dep_capture_wiring.py, test_phase_plan_recursion_wiring.py).
"""
from __future__ import annotations

import inspect
from pathlib import Path

import tests.conftest as conftest

REPO_ROOT = Path(__file__).resolve().parent.parent
TESTS_DIR = REPO_ROOT / "tests"

GATED_MODULES = [
    "test_build_repo_map.py",
    "test_repo_map.py",
    "test_phase_plan_repo_map_ctx.py",
]


def test_has_treesitter_delegates_to_functional_probe():
    """conftest._has_treesitter() must reference
    _tree_sitter_extraction_works — proving it delegates to the functional
    probe rather than relying on mere importability (a bare ImportError
    check would not catch an installed-but-incompatible language-pack
    version, which imports fine yet extracts nothing)."""
    src = inspect.getsource(conftest._has_treesitter)
    assert "_tree_sitter_extraction_works" in src, (
        "conftest._has_treesitter() must delegate to "
        "leerie._tree_sitter_extraction_works() (the functional probe), "
        "not a bare import check"
    )


def test_has_treesitter_is_module_level_bool():
    """conftest must expose a module-level HAS_TREESITTER bool, evaluated
    once at collection, for extraction-dependent modules to import."""
    assert hasattr(conftest, "HAS_TREESITTER"), (
        "tests/conftest.py must expose a module-level HAS_TREESITTER"
    )
    assert isinstance(conftest.HAS_TREESITTER, bool)


class TestGatedModulesImportAndSkip:
    """Each extraction-dependent test module must import HAS_TREESITTER
    from tests.conftest and gate on it via a skipif somewhere in the file
    (module-level pytestmark or a class-level decorator — some modules
    only gate the extraction-dependent class, not the whole module, so we
    assert presence rather than a specific location)."""

    def _read(self, filename: str) -> str:
        path = TESTS_DIR / filename
        assert path.exists(), f"expected test module {path} to exist"
        return path.read_text()

    def test_import_and_skipif_present(self):
        for filename in GATED_MODULES:
            text = self._read(filename)
            assert "from tests.conftest import HAS_TREESITTER" in text, (
                f"{filename} must import HAS_TREESITTER from tests.conftest"
            )
            assert "skipif" in text, (
                f"{filename} must gate extraction-dependent tests with a "
                f"pytest.mark.skipif"
            )
            assert "not HAS_TREESITTER" in text, (
                f"{filename}'s skipif must reference HAS_TREESITTER "
                f"(expected a 'not HAS_TREESITTER' condition)"
            )
