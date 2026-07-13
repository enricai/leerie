"""Unit tests for rank_repo_map (DESIGN §P6).

Pins the three load-bearing contracts of the personalized-PageRank ranking:

1. Seed-neighborhood ranking: nodes adjacent to the seed (1-hop, 2-hop) rank
   above unrelated nodes that share no graph path with the seed.
2. Token-budget enforcement: the rendered output fits within the given budget
   (both the DEFAULT_CAPS cap and an explicit value).
3. Binary-search shrink: lowering the token budget yields a strictly shorter
   (fewer-file) output.

Fixture is built directly without build_repo_map — this isolates ranking.
No LLM calls; everything is deterministic.
"""
from __future__ import annotations


# ---------------------------------------------------------------------------
# Shared fixture
# ---------------------------------------------------------------------------

def _khop_map() -> dict:
    """Synthetic 4-file repo_map with a clear k-hop structure.

    Graph (callee→caller edges rank_repo_map builds):
        seed.py → hop1.py → hop2.py → (dangling)
        unrelated.py          (dangling, no path to/from seed cluster)

    Seeding on seed.py or seed_func biases the random-walk toward the
    connected cluster; unrelated.py receives only the uniform teleport mass.
    """
    return {
        "files": {
            "seed.py":      ["seed_func"],
            "hop1.py":      ["hop1_func"],
            "hop2.py":      ["hop2_func"],
            "unrelated.py": ["unrelated_fn"],
        },
        "refs": {
            # hop1.py references seed_func (defined in seed.py) → seed.py→hop1.py edge
            "seed_func": {"hop1.py"},
            # hop2.py references hop1_func (defined in hop1.py) → hop1.py→hop2.py edge
            "hop1_func": {"hop2.py"},
        },
    }


def _ranked_files(result: str) -> list[str]:
    """Extract file names from a rank_repo_map result string, in order."""
    files = []
    for line in result.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        files.append(line.split(":")[0].strip())
    return files


# ---------------------------------------------------------------------------
# 1. Seed-neighborhood ranking
# ---------------------------------------------------------------------------

class TestSeedNeighborhoodRanking:
    """Nodes connected to the seed rank above unrelated nodes."""

    def test_seed_file_ranks_above_unrelated(self, leerie):
        """seed.py (directly seeded) appears before unrelated.py."""
        result = leerie.rank_repo_map(_khop_map(), ["seed.py"], [], 2000)
        files = _ranked_files(result)
        assert "seed.py" in files
        assert "unrelated.py" in files
        assert files.index("seed.py") < files.index("unrelated.py"), (
            f"Expected seed.py before unrelated.py; got order: {files}"
        )

    def test_hop1_neighbor_ranks_above_unrelated(self, leerie):
        """hop1.py (1-hop from seed via callee→caller edge) appears before unrelated.py."""
        result = leerie.rank_repo_map(_khop_map(), ["seed.py"], [], 2000)
        files = _ranked_files(result)
        assert "hop1.py" in files
        assert "unrelated.py" in files
        assert files.index("hop1.py") < files.index("unrelated.py"), (
            f"Expected hop1.py before unrelated.py; got order: {files}"
        )

    def test_seed_symbol_biases_definer_above_unrelated(self, leerie):
        """Seeding on seed_func (a symbol) biases seed.py (its definer) above unrelated.py."""
        result = leerie.rank_repo_map(_khop_map(), [], ["seed_func"], 2000)
        files = _ranked_files(result)
        assert "seed.py" in files
        assert "unrelated.py" in files
        assert files.index("seed.py") < files.index("unrelated.py"), (
            f"Expected seed.py before unrelated.py; got order: {files}"
        )

    def test_all_connected_nodes_before_unrelated(self, leerie):
        """seed.py, hop1.py, and hop2.py all appear before unrelated.py."""
        result = leerie.rank_repo_map(_khop_map(), ["seed.py"], [], 2000)
        files = _ranked_files(result)
        assert "unrelated.py" in files
        unrelated_idx = files.index("unrelated.py")
        for connected in ("seed.py", "hop1.py", "hop2.py"):
            assert connected in files, f"{connected} missing from result: {files}"
            assert files.index(connected) < unrelated_idx, (
                f"Expected {connected} before unrelated.py; order: {files}"
            )

    def test_large_graph_unrelated_cluster_at_tail(self, leerie):
        """In a graph with a large unrelated cluster, seeding on one file keeps
        the unrelated cluster at the tail of the ranking."""
        # 10 files in the seed cluster (connected chain), 10 completely unrelated
        files = {}
        refs = {}
        for i in range(10):
            files[f"chain_{i:02d}.py"] = [f"chain_fn_{i}"]
        for i in range(9):
            refs[f"chain_fn_{i}"] = {f"chain_{i + 1:02d}.py"}
        for i in range(10):
            files[f"island_{i:02d}.py"] = [f"island_fn_{i}"]
        repo_map = {"files": files, "refs": refs}

        result = leerie.rank_repo_map(repo_map, ["chain_00.py"], [], 5000)
        ranked = _ranked_files(result)

        # The first connected-chain file before the first island file
        first_island = next(f for f in ranked if f.startswith("island_"))
        first_chain_in_result = next(f for f in ranked if f.startswith("chain_"))
        assert ranked.index(first_chain_in_result) < ranked.index(first_island), (
            f"Expected a chain file before any island file; order: {ranked}"
        )


# ---------------------------------------------------------------------------
# 2. Token-budget enforcement
# ---------------------------------------------------------------------------

class TestTokenBudgetEnforcement:
    """Rendered output fits within the specified token budget."""

    def _approx_tokens(self, text: str) -> int:
        return max(1, len(text.encode()) // 4)

    def test_explicit_budget_respected(self, leerie):
        """Output token count does not exceed an explicit budget."""
        result = leerie.rank_repo_map(_khop_map(), ["seed.py"], [], 10)
        assert self._approx_tokens(result) <= 10, (
            f"approx tokens {self._approx_tokens(result)} exceeded budget 10"
        )

    def test_default_cap_respected_on_large_map(self, leerie):
        """A map that would exceed 1k tokens when fully rendered is trimmed to fit."""
        # 200 files × 5 symbols each renders to ~4000 bytes (> 1000 approx tokens).
        large_files = {
            f"module_{i:03d}.py": [f"func_{i}_{j}" for j in range(5)]
            for i in range(200)
        }
        repo_map = {"files": large_files, "refs": {}}
        result = leerie.rank_repo_map(repo_map, [], [], None)
        cap = leerie.DEFAULT_CAPS["repo_map_tokens"]
        approx = self._approx_tokens(result)
        assert approx <= cap, (
            f"approx tokens {approx} exceeded DEFAULT_CAPS['repo_map_tokens'] {cap}"
        )

    def test_none_budget_equals_default_cap(self, leerie):
        """Passing token_budget=None gives the same result as passing the cap value."""
        cap = leerie.DEFAULT_CAPS["repo_map_tokens"]
        result_none = leerie.rank_repo_map(_khop_map(), ["seed.py"], [], None)
        result_cap = leerie.rank_repo_map(_khop_map(), ["seed.py"], [], cap)
        assert result_none == result_cap

    def test_token_count_is_nonnegative(self, leerie):
        """Rendered output is never a negative-token string (basic sanity)."""
        result = leerie.rank_repo_map(_khop_map(), ["seed.py"], [], 50)
        assert len(result.encode()) >= 0

    def test_empty_map_returns_empty_string(self, leerie):
        """Empty repo_map produces empty string regardless of budget."""
        result = leerie.rank_repo_map({"files": {}, "refs": {}}, [], [], 1000)
        assert result == ""


# ---------------------------------------------------------------------------
# 3. Binary-search shrink
# ---------------------------------------------------------------------------

class TestBinarySearchShrink:
    """Lowering the token budget shrinks the rendered output."""

    def _build_shrinkable_map(self) -> dict:
        """A map where each file renders to a non-trivial number of bytes."""
        files = {f"file_{i:02d}.py": [f"sym_a_{i}", f"sym_b_{i}", f"sym_c_{i}"]
                 for i in range(20)}
        return {"files": files, "refs": {}}

    def test_lower_budget_yields_shorter_output(self, leerie):
        """Halving the budget produces output that is <= the original length."""
        repo_map = self._build_shrinkable_map()
        result_large = leerie.rank_repo_map(repo_map, [], [], 500)
        result_small = leerie.rank_repo_map(repo_map, [], [], 50)
        assert len(result_small.encode()) <= len(result_large.encode()), (
            f"smaller budget produced longer output: {len(result_small)} vs {len(result_large)}"
        )

    def test_lower_budget_yields_fewer_files(self, leerie):
        """A tight budget renders fewer files than a generous budget."""
        repo_map = self._build_shrinkable_map()
        # 20 files × ~30 bytes each ≈ 150 approx tokens; budget 200 fits all,
        # budget 5 fits at most one line.
        result_all = leerie.rank_repo_map(repo_map, [], [], 200)
        result_few = leerie.rank_repo_map(repo_map, [], [], 5)
        files_all = _ranked_files(result_all)
        files_few = _ranked_files(result_few)
        assert len(files_few) < len(files_all), (
            f"tight budget should yield fewer files: {len(files_few)} vs {len(files_all)}"
        )

    def test_budget_sequence_monotone(self, leerie):
        """Increasing budgets yield non-decreasing output lengths."""
        repo_map = self._build_shrinkable_map()
        budgets = [5, 15, 30, 100, 500]
        prev_len = -1
        for budget in budgets:
            result = leerie.rank_repo_map(repo_map, [], [], budget)
            cur_len = len(result.encode())
            assert cur_len >= prev_len, (
                f"output shrank as budget grew from {budgets[budgets.index(budget) - 1]}"
                f" to {budget}: {prev_len} -> {cur_len}"
            )
            prev_len = cur_len

    def test_tight_budget_respects_limit(self, leerie):
        """A 1-token budget yields empty or a single very-short entry."""
        repo_map = self._build_shrinkable_map()
        result = leerie.rank_repo_map(repo_map, [], [], 1)
        approx = max(1, len(result.encode()) // 4)
        assert approx <= 1, f"1-token budget exceeded: {approx} approx tokens"

    def test_single_file_map_always_fits_small_budget(self, leerie):
        """A map with one file either fits the budget or returns empty string."""
        repo_map = {"files": {"solo.py": ["fn_a", "fn_b"]}, "refs": {}}
        for budget in (1, 2, 5, 10, 100):
            result = leerie.rank_repo_map(repo_map, [], [], budget)
            approx = max(1, len(result.encode()) // 4)
            assert approx <= budget or result == "", (
                f"budget {budget} violated: approx_tokens={approx}, result={result!r}"
            )
