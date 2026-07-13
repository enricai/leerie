"""Unit tests for build_repo_map(): symbol graph and mtime cache (DESIGN §5½ (P6)).

Pins the three criteria from the subtask spec:
1. A fixture repo yields a symbol graph with expected def→ref edges.
2. A second call after touching one file re-parses only that file
   (asserted via a sentinel cache-hit marker — unchanged files stay
   cached, changed file is re-parsed).
3. An unparseable/empty file degrades gracefully (no crash).

Uses a HAS_TREESITTER gate so CI without the parser installed skips
the tree-sitter-dependent tests rather than failing (mirrors the
HAS_JSONSCHEMA gate in test_dep_capture_schema.py).
"""
from __future__ import annotations

import hashlib
import pickle
import time
import types
from pathlib import Path

import pytest

# Gate on the shared FUNCTIONAL probe (conftest) — not mere importability —
# so an installed-but-incompatible language-pack version (imports fine,
# extracts nothing) skips cleanly instead of failing.
from tests.conftest import HAS_TREESITTER

pytestmark = pytest.mark.skipif(
    not HAS_TREESITTER,
    reason="tree-sitter parser unavailable or incompatible "
           "(no symbol extraction)",
)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _write_fixture(root: Path) -> None:
    """Write a minimal three-file Python fixture repo under *root*."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "utils.py").write_text(
        "def helper(x):\n    return x * 2\n\ndef another():\n    pass\n"
    )
    (root / "main.py").write_text(
        "from utils import helper\n\ndef run():\n    return helper(1)\n"
    )
    (root / "models.py").write_text(
        "class Model:\n    def process(self, data):\n        return another(data)\n"
    )


def _pkl_path(leerie_root: Path, abs_path: Path, leerie: types.ModuleType) -> Path:
    """Return the cache .pkl path for *abs_path*, mirroring build_repo_map internals."""
    digest = hashlib.sha256(str(abs_path).encode()).hexdigest()
    return leerie_root / leerie.REPO_MAP_CACHE_DIR / f"{digest}.pkl"


def _write_sentinel(pkl: Path, mtime_ns: int, defs: list[str]) -> None:
    """Write a sentinel cache entry so the next build_repo_map call returns it."""
    pkl.parent.mkdir(parents=True, exist_ok=True)
    with open(pkl, "wb") as fh:
        pickle.dump({"mtime_ns": mtime_ns, "defs": defs, "refs": []}, fh)


# ---------------------------------------------------------------------------
# Criterion 1: symbol graph — def→ref edges
# ---------------------------------------------------------------------------

class TestSymbolGraph:
    """build_repo_map on a fixture repo produces a populated symbol/ref graph."""

    def test_defs_extracted(self, leerie, tmp_path):
        repo = tmp_path / "repo"
        _write_fixture(repo)
        repo_map = leerie.build_repo_map(repo, tmp_path / "lr")
        assert "helper" in repo_map["files"].get("utils.py", [])
        assert "another" in repo_map["files"].get("utils.py", [])

    def test_class_defs_extracted(self, leerie, tmp_path):
        repo = tmp_path / "repo"
        _write_fixture(repo)
        repo_map = leerie.build_repo_map(repo, tmp_path / "lr")
        model_syms = repo_map["files"].get("models.py", [])
        assert "Model" in model_syms
        assert "process" in model_syms

    def test_ref_edge_caller_to_callee(self, leerie, tmp_path):
        """main.py calls helper → refs["helper"] must include main.py."""
        repo = tmp_path / "repo"
        _write_fixture(repo)
        repo_map = leerie.build_repo_map(repo, tmp_path / "lr")
        helper_callers = repo_map["refs"].get("helper", set())
        assert "main.py" in helper_callers, (
            f"Expected main.py in refs['helper'], got {helper_callers}"
        )

    def test_result_has_files_and_refs_keys(self, leerie, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir(parents=True, exist_ok=True)
        repo_map = leerie.build_repo_map(repo, tmp_path / "lr")
        assert "files" in repo_map
        assert "refs" in repo_map

    def test_file_keys_are_relative(self, leerie, tmp_path):
        repo = tmp_path / "repo"
        _write_fixture(repo)
        repo_map = leerie.build_repo_map(repo, tmp_path / "lr")
        for key in repo_map["files"]:
            assert not Path(key).is_absolute(), f"absolute path in files: {key!r}"


# ---------------------------------------------------------------------------
# Criterion 2: mtime cache — only changed file re-parses
# ---------------------------------------------------------------------------

class TestMtimeCache:
    """Changed files are re-parsed; unchanged files are served from cache."""

    def test_cache_dir_created(self, leerie, tmp_path):
        repo = tmp_path / "repo"
        _write_fixture(repo)
        leerie_root = tmp_path / "lr"
        leerie.build_repo_map(repo, leerie_root)
        assert (leerie_root / leerie.REPO_MAP_CACHE_DIR).is_dir()

    def test_unchanged_file_served_from_sentinel(self, leerie, tmp_path):
        """Inject a sentinel for utils.py; a second call with unchanged mtime
        must return the sentinel — confirming the cache was hit."""
        repo = tmp_path / "repo"
        _write_fixture(repo)
        leerie_root = tmp_path / "lr"
        # First call populates cache
        leerie.build_repo_map(repo, leerie_root)
        # Overwrite utils.py cache entry with sentinel
        utils_path = repo / "utils.py"
        pkl = _pkl_path(leerie_root, utils_path, leerie)
        _write_sentinel(pkl, utils_path.stat().st_mtime_ns, ["SENTINEL"])
        # Second call — mtime unchanged → cache hit → sentinel returned
        repo_map2 = leerie.build_repo_map(repo, leerie_root)
        assert "SENTINEL" in repo_map2["files"].get("utils.py", []), (
            "Expected cache sentinel returned for unchanged utils.py"
        )

    def test_changed_file_reparsed(self, leerie, tmp_path):
        """Modify utils.py so its mtime advances; build_repo_map must re-parse it."""
        repo = tmp_path / "repo"
        _write_fixture(repo)
        leerie_root = tmp_path / "lr"
        leerie.build_repo_map(repo, leerie_root)
        # Advance mtime by writing new content
        time.sleep(0.01)
        (repo / "utils.py").write_text("def new_fn(): pass\n")
        repo_map2 = leerie.build_repo_map(repo, leerie_root)
        utils_syms = repo_map2["files"].get("utils.py", [])
        assert "new_fn" in utils_syms
        assert "helper" not in utils_syms

    def test_only_changed_file_reparsed_other_hits_cache(self, leerie, tmp_path):
        """Modify utils.py only; main.py must still be served from its sentinel."""
        repo = tmp_path / "repo"
        _write_fixture(repo)
        leerie_root = tmp_path / "lr"
        leerie.build_repo_map(repo, leerie_root)
        # Inject sentinel for main.py (unchanged)
        main_path = repo / "main.py"
        pkl = _pkl_path(leerie_root, main_path, leerie)
        _write_sentinel(pkl, main_path.stat().st_mtime_ns, ["MAIN_SENTINEL"])
        # Modify only utils.py
        time.sleep(0.01)
        (repo / "utils.py").write_text("def replaced(): pass\n")
        repo_map2 = leerie.build_repo_map(repo, leerie_root)
        # main.py → sentinel (cache hit); utils.py → fresh parse
        assert "MAIN_SENTINEL" in repo_map2["files"].get("main.py", [])
        assert "replaced" in repo_map2["files"].get("utils.py", [])


# ---------------------------------------------------------------------------
# Criterion 3: graceful degrade for unparseable/empty files
# ---------------------------------------------------------------------------

class TestGracefulDegrade:
    """Unparseable or empty files must not crash build_repo_map."""

    def test_empty_file_no_crash(self, leerie, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir(parents=True, exist_ok=True)
        (repo / "empty.py").write_text("")
        # Must not raise
        repo_map = leerie.build_repo_map(repo, tmp_path / "lr")
        assert "files" in repo_map

    def test_binary_file_no_crash(self, leerie, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir(parents=True, exist_ok=True)
        (repo / "blob.xyz").write_bytes(b"\x00\x01\x02\x03\xff\xfe")
        repo_map = leerie.build_repo_map(repo, tmp_path / "lr")
        assert "files" in repo_map

    def test_empty_repo_returns_empty_graph(self, leerie, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir(parents=True, exist_ok=True)
        repo_map = leerie.build_repo_map(repo, tmp_path / "lr")
        assert repo_map["files"] == {}
        assert repo_map["refs"] == {}

    def test_skip_dirs_no_crash(self, leerie, tmp_path):
        """node_modules and .git are skipped; function must not crash."""
        repo = tmp_path / "repo"
        repo.mkdir(parents=True, exist_ok=True)
        (repo / ".git").mkdir()
        (repo / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
        (repo / "node_modules").mkdir()
        (repo / "node_modules" / "lib.py").write_text("def bad(): pass\n")
        (repo / "good.py").write_text("def good(): pass\n")
        repo_map = leerie.build_repo_map(repo, tmp_path / "lr")
        assert "good.py" in repo_map["files"]
        for key in repo_map["files"]:
            assert not key.startswith(".git/")
            assert not key.startswith("node_modules/")
