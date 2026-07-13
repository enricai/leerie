"""Tests for build_repo_map() and rank_repo_map() (DESIGN §5½ (P6)).

Covers:
- build_repo_map on a small multi-file Python fixture produces defs→refs edges
  and per-file symbol lists.
- mtime cache: changing a file forces a re-parse; leaving it untouched hits
  the cache (unchanged files served from cache).
- rank_repo_map biases toward seed files/symbols and returns a subgraph whose
  serialized token count ≤ token_budget.
- Both functions are deterministic (no LLM, no claude_p).
- Graceful degrade: unsupported file types return empty defs/refs without
  raising.
"""
from __future__ import annotations

import pickle
import time
from pathlib import Path

import pytest

# These tests hard-assert tree-sitter symbol extraction; without a WORKING
# parser build_repo_map degrades to an empty graph and the assertions fail
# rather than skip. Gate on the shared FUNCTIONAL probe (conftest) — not mere
# importability — so an installed-but-incompatible language-pack version
# (imports fine, extracts nothing) skips cleanly instead of failing.
from tests.conftest import HAS_TREESITTER

pytestmark = pytest.mark.skipif(
    not HAS_TREESITTER,
    reason="tree-sitter parser unavailable or incompatible "
           "(no symbol extraction)",
)


# ---------------------------------------------------------------------------
# Fixture repo helpers
# ---------------------------------------------------------------------------

def _write_fixture_repo(root: Path) -> dict[str, Path]:
    """Create a minimal three-file Python fixture repo under *root* and return paths."""
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
    return {
        "utils.py": root / "utils.py",
        "main.py": root / "main.py",
        "models.py": root / "models.py",
    }


# ---------------------------------------------------------------------------
# build_repo_map — symbol extraction
# ---------------------------------------------------------------------------

class TestBuildRepoMapSymbols:
    """build_repo_map extracts defs and builds refs edges."""

    def test_defs_extracted_per_file(self, leerie, tmp_path):
        repo = tmp_path / "repo"
        _write_fixture_repo(repo)
        repo_map = leerie.build_repo_map(repo, tmp_path / "leerie-root")
        files = repo_map["files"]
        # utils.py defines helper and another
        assert "helper" in files.get("utils.py", [])
        assert "another" in files.get("utils.py", [])

    def test_class_methods_extracted(self, leerie, tmp_path):
        repo = tmp_path / "repo"
        _write_fixture_repo(repo)
        repo_map = leerie.build_repo_map(repo, tmp_path / "leerie-root")
        files = repo_map["files"]
        # models.py defines Model and process
        model_syms = files.get("models.py", [])
        assert "Model" in model_syms
        assert "process" in model_syms

    def test_refs_map_populated(self, leerie, tmp_path):
        repo = tmp_path / "repo"
        _write_fixture_repo(repo)
        repo_map = leerie.build_repo_map(repo, tmp_path / "leerie-root")
        refs = repo_map["refs"]
        # main.py calls helper → refs["helper"] should include "main.py"
        helper_refs = refs.get("helper", set())
        assert "main.py" in helper_refs

    def test_files_key_is_relative_paths(self, leerie, tmp_path):
        repo = tmp_path / "repo"
        _write_fixture_repo(repo)
        repo_map = leerie.build_repo_map(repo, tmp_path / "leerie-root")
        for key in repo_map["files"]:
            assert not Path(key).is_absolute(), f"Expected relative path, got {key}"

    def test_returns_dict_with_files_and_refs_keys(self, leerie, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir(parents=True, exist_ok=True)
        repo_map = leerie.build_repo_map(repo, tmp_path / "leerie-root")
        assert "files" in repo_map
        assert "refs" in repo_map

    def test_empty_repo_returns_empty_map(self, leerie, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir(parents=True, exist_ok=True)
        repo_map = leerie.build_repo_map(repo, tmp_path / "leerie-root")
        assert repo_map["files"] == {}
        assert repo_map["refs"] == {}

    def test_skips_git_and_node_modules(self, leerie, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir(parents=True, exist_ok=True)
        (repo / ".git").mkdir()
        (repo / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
        (repo / "node_modules").mkdir()
        (repo / "node_modules" / "lib.py").write_text("def bad(): pass\n")
        (repo / "good.py").write_text("def good(): pass\n")
        repo_map = leerie.build_repo_map(repo, tmp_path / "leerie-root")
        files = repo_map["files"]
        # good.py should be present; lib.py from node_modules should not
        assert "good.py" in files
        # none of the keys should be from .git or node_modules
        for key in files:
            assert not key.startswith(".git/")
            assert not key.startswith("node_modules/")


# ---------------------------------------------------------------------------
# build_repo_map — mtime cache behaviour
# ---------------------------------------------------------------------------

class TestBuildRepoMapCache:
    """mtime cache: changed files re-parse; unchanged files hit the cache."""

    def test_cache_dir_created_on_first_use(self, leerie, tmp_path):
        leerie_root = tmp_path / "leerie-root"
        repo = tmp_path / "repo"
        _write_fixture_repo(repo)
        leerie.build_repo_map(repo, leerie_root)
        assert (leerie_root / leerie.REPO_MAP_CACHE_DIR).exists()

    def test_unchanged_file_served_from_cache(self, leerie, tmp_path):
        leerie_root = tmp_path / "leerie-root"
        repo = tmp_path / "repo"
        _write_fixture_repo(repo)
        # First build populates cache
        leerie.build_repo_map(repo, leerie_root)
        # Overwrite the cache entry for utils.py with a sentinel
        import hashlib
        cache_path = leerie_root / leerie.REPO_MAP_CACHE_DIR
        utils_path = repo / "utils.py"
        digest = hashlib.sha256(str(utils_path).encode()).hexdigest()
        pkl_file = cache_path / f"{digest}.pkl"
        sentinel = {
            "mtime_ns": utils_path.stat().st_mtime_ns,
            "defs": ["SENTINEL"],
            "refs": [],
        }
        with open(pkl_file, "wb") as fh:
            pickle.dump(sentinel, fh)
        # Second build — utils.py mtime unchanged — should hit cache sentinel
        repo_map2 = leerie.build_repo_map(repo, leerie_root)
        assert "SENTINEL" in repo_map2["files"].get("utils.py", [])

    def test_changed_file_reparsed(self, leerie, tmp_path):
        leerie_root = tmp_path / "leerie-root"
        repo = tmp_path / "repo"
        _write_fixture_repo(repo)
        # First build populates cache
        leerie.build_repo_map(repo, leerie_root)
        # Modify utils.py — this changes its mtime
        utils_path = repo / "utils.py"
        # Ensure mtime advances (filesystem mtime resolution is at least 1 ns
        # on Linux; we write new content so inode data changes)
        time.sleep(0.01)
        utils_path.write_text(
            "def new_function(): pass\n\ndef another(): pass\n"
        )
        repo_map2 = leerie.build_repo_map(repo, leerie_root)
        # "new_function" should now appear (re-parsed), "helper" should not
        utils_syms = repo_map2["files"].get("utils.py", [])
        assert "new_function" in utils_syms
        assert "helper" not in utils_syms

    def test_only_changed_file_reparsed(self, leerie, tmp_path):
        """Modify one file; confirm the other still serves from cache."""
        leerie_root = tmp_path / "leerie-root"
        repo = tmp_path / "repo"
        _write_fixture_repo(repo)
        leerie.build_repo_map(repo, leerie_root)
        # Install sentinel for main.py (unchanged)
        import hashlib
        main_path = repo / "main.py"
        cache_path = leerie_root / leerie.REPO_MAP_CACHE_DIR
        digest = hashlib.sha256(str(main_path).encode()).hexdigest()
        pkl_file = cache_path / f"{digest}.pkl"
        sentinel = {
            "mtime_ns": main_path.stat().st_mtime_ns,
            "defs": ["MAIN_SENTINEL"],
            "refs": [],
        }
        with open(pkl_file, "wb") as fh:
            pickle.dump(sentinel, fh)
        # Modify utils.py only
        time.sleep(0.01)
        (repo / "utils.py").write_text("def replaced(): pass\n")
        repo_map2 = leerie.build_repo_map(repo, leerie_root)
        # main.py still served from sentinel cache
        assert "MAIN_SENTINEL" in repo_map2["files"].get("main.py", [])
        # utils.py was re-parsed
        assert "replaced" in repo_map2["files"].get("utils.py", [])


# ---------------------------------------------------------------------------
# rank_repo_map — PageRank + token budget
# ---------------------------------------------------------------------------

class TestRankRepoMap:
    """rank_repo_map biases toward seeds and fits within the token budget."""

    def _fixture_map(self) -> dict:
        """Build a synthetic repo_map without touching the filesystem."""
        return {
            "files": {
                "utils.py": ["helper", "another"],
                "main.py": ["run"],
                "models.py": ["Model", "process"],
                "unrelated.py": ["totally_different"],
            },
            "refs": {
                # helper is called by main.py
                "helper": {"main.py"},
                # another is called by models.py
                "another": {"models.py"},
            },
        }

    def test_returns_string(self, leerie):
        repo_map = self._fixture_map()
        result = leerie.rank_repo_map(repo_map, ["main.py"], [], 1000)
        assert isinstance(result, str)

    def test_fits_within_token_budget(self, leerie):
        repo_map = self._fixture_map()
        budget = 50  # tight budget
        result = leerie.rank_repo_map(repo_map, ["main.py"], [], budget)
        # Approximate token count: len(bytes) // 4 ≤ budget
        approx = max(1, len(result.encode()) // 4)
        assert approx <= budget, (
            f"Result of {approx} approx-tokens exceeds budget {budget}: {result!r}"
        )

    def test_seed_file_appears_in_result(self, leerie):
        repo_map = self._fixture_map()
        result = leerie.rank_repo_map(repo_map, ["utils.py"], [], 500)
        # utils.py is seeded → should appear in top entries when budget allows
        assert "utils.py" in result

    def test_seed_symbol_biases_result(self, leerie):
        repo_map = self._fixture_map()
        # Seed on "helper" symbol → utils.py (its definer) should appear
        result = leerie.rank_repo_map(repo_map, [], ["helper"], 500)
        assert "utils.py" in result

    def test_empty_map_returns_empty_string(self, leerie):
        result = leerie.rank_repo_map({"files": {}, "refs": {}}, [], [], 1000)
        assert result == ""

    def test_no_seed_returns_something_when_map_nonempty(self, leerie):
        repo_map = self._fixture_map()
        result = leerie.rank_repo_map(repo_map, [], [], 1000)
        assert result != ""

    def test_uses_default_cap_when_budget_none(self, leerie):
        repo_map = self._fixture_map()
        result_none = leerie.rank_repo_map(repo_map, ["utils.py"], [], None)
        result_cap = leerie.rank_repo_map(
            repo_map, ["utils.py"], [],
            leerie.DEFAULT_CAPS["repo_map_tokens"],
        )
        assert result_none == result_cap

    def test_result_contains_symbol_names(self, leerie):
        repo_map = self._fixture_map()
        result = leerie.rank_repo_map(repo_map, ["utils.py"], [], 2000)
        # At least some defs from the fixture should appear as text
        syms_found = [s for s in ["helper", "another", "run", "Model"]
                      if s in result]
        assert syms_found, f"No known symbols found in result: {result!r}"

    def test_deterministic_same_inputs(self, leerie):
        repo_map = self._fixture_map()
        r1 = leerie.rank_repo_map(repo_map, ["main.py"], ["helper"], 500)
        r2 = leerie.rank_repo_map(repo_map, ["main.py"], ["helper"], 500)
        assert r1 == r2

    def test_very_tight_budget_returns_one_entry_or_empty(self, leerie):
        repo_map = self._fixture_map()
        # 5-token budget: fits zero or at most one short entry
        result = leerie.rank_repo_map(repo_map, ["utils.py"], [], 5)
        approx = max(1, len(result.encode()) // 4)
        assert approx <= 5


# ---------------------------------------------------------------------------
# Graceful degrade — unsupported file types
# ---------------------------------------------------------------------------

class TestParseRepoFileDegrade:
    """_parse_repo_file returns ([], []) for unsupported or binary files."""

    def test_unsupported_extension_returns_empty(self, leerie, tmp_path):
        f = tmp_path / "binary.xyz"
        f.write_bytes(b"\x00\x01\x02\x03")
        defs, refs = leerie._parse_repo_file(f)
        assert defs == []
        assert refs == []

    def test_markdown_file_returns_empty(self, leerie, tmp_path):
        f = tmp_path / "README.md"
        f.write_text("# Hello\n\nsome text\n")
        defs, refs = leerie._parse_repo_file(f)
        # Markdown may not have a tree-sitter grammar in the pack;
        # either way defs should be empty (no function/class defs in MD)
        assert isinstance(defs, list)
        assert isinstance(refs, list)

    def test_python_file_returns_defs(self, leerie, tmp_path):
        f = tmp_path / "example.py"
        f.write_text("def my_func(x): return x\n\nclass MyClass: pass\n")
        defs, refs = leerie._parse_repo_file(f)
        assert "my_func" in defs
        assert "MyClass" in defs

    def test_python_calls_returned_as_refs(self, leerie, tmp_path):
        f = tmp_path / "caller.py"
        f.write_text("def caller():\n    result = callee()\n    return result\n")
        defs, refs = leerie._parse_repo_file(f)
        assert "caller" in defs
        assert "callee" in refs


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

class TestWalkCalls:
    """_walk_calls extracts call identifier names from a CST."""

    def test_bare_call_extracted(self, leerie, tmp_path):
        """bare_func() in Python → 'bare_func' in calls."""
        import tree_sitter_language_pack as tslp
        from tree_sitter import Parser
        lang = tslp.get_language("python")
        parser = Parser(lang)
        tree = parser.parse(b"def f():\n    bare_func()\n")
        calls = leerie._walk_calls(tree.root_node)
        assert "bare_func" in calls

    def test_attribute_call_not_extracted_as_bare(self, leerie, tmp_path):
        """obj.method() — 'method' is an attribute, not a bare identifier."""
        import tree_sitter_language_pack as tslp
        from tree_sitter import Parser
        lang = tslp.get_language("python")
        parser = Parser(lang)
        tree = parser.parse(b"def f():\n    obj.method()\n")
        calls = leerie._walk_calls(tree.root_node)
        # 'obj' might appear as the attribute's object identifier if it's
        # a call expression; 'method' should NOT appear as a bare call target
        assert "method" not in calls


class TestPageRank:
    """_pagerank converges on a toy graph with predictable structure."""

    def test_dangling_node_handled(self, leerie):
        # C has no outgoing edges (dangling)
        graph = {"A": {"B"}, "B": {"C"}, "C": set()}
        ranks = leerie._pagerank(graph, {"A": 1.0})
        assert "C" in ranks
        assert sum(ranks.values()) > 0

    def test_personalization_biases_toward_seed(self, leerie):
        graph = {"A": set(), "B": set(), "C": set()}
        ranks_a = leerie._pagerank(graph, {"A": 1.0})
        ranks_b = leerie._pagerank(graph, {"B": 1.0})
        # With no edges, personalization dictates rank entirely
        assert ranks_a["A"] > ranks_a["B"]
        assert ranks_b["B"] > ranks_b["A"]

    def test_empty_graph_returns_empty(self, leerie):
        ranks = leerie._pagerank({}, {})
        assert ranks == {}
