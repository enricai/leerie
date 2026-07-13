"""Tests for P6 repo-map injection into phase_plan context (DESIGN §5½ (P6)).

Verifies:
- With skip_repo_map=False, build_repo_map + rank_repo_map are called and the
  resulting ranked subgraph is injected as "repo_map" into the ctx JSON blob.
- With skip_repo_map=True, the ctx JSON omits "repo_map" and no exception is
  raised (graceful degrade to grep/glob-only planner path).
- Baseline ctx keys (task, source_of_truth, clarification_answers,
  confidence_rounds) are present in both branches.
- When rank_repo_map returns an empty string (empty map), "repo_map" is omitted
  from ctx (no blank entries).
- When build_repo_map raises, the exception is swallowed and ctx is emitted
  without "repo_map".

These tests call the sub-functions directly (build_repo_map, rank_repo_map)
rather than phase_plan end-to-end, since phase_plan requires a live claude
subprocess. The logic under test is the ctx-building block introduced in feat-004.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# Gate the extraction-dependent class on the shared functional probe
# (conftest) — importability alone is insufficient (an incompatible parser
# version imports but extracts nothing).
from tests.conftest import HAS_TREESITTER


# ---------------------------------------------------------------------------
# Helper: build the ctx dict the same way phase_plan does
# ---------------------------------------------------------------------------

def _build_ctx(
    leerie,
    task: str,
    sot: str,
    answers: dict,
    confidence_rounds: int,
    repo_root: Path,
    leerie_root: Path,
    skip_repo_map: bool,
    task_file_items: list[str] | None = None,
) -> dict:
    """Reproduce the phase_plan ctx-building logic under test, without
    actually spawning planners.  Returns the parsed ctx dict."""
    ctx_dict: dict = {
        "task": task,
        "source_of_truth": sot,
        "clarification_answers": answers,
        "confidence_rounds": confidence_rounds,
    }
    if not skip_repo_map:
        try:
            repo_map = leerie.build_repo_map(repo_root, leerie_root)
            seed_files = (
                [str(Path(item.split(": ", 1)[0])) for item in task_file_items]
                if task_file_items else []
            )
            ranked = leerie.rank_repo_map(repo_map, seed_files, [])
            if ranked:
                ctx_dict["repo_map"] = ranked
        except Exception:
            pass
    return ctx_dict


# ---------------------------------------------------------------------------
# Fixture: minimal Python repo under tmp_path
# ---------------------------------------------------------------------------

def _write_fixture_repo(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "utils.py").write_text(
        "def helper(x):\n    return x * 2\n\ndef another():\n    pass\n"
    )
    (root / "main.py").write_text(
        "from utils import helper\n\ndef run():\n    return helper(1)\n"
    )


# ---------------------------------------------------------------------------
# Branch 1: repo-map enabled
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not HAS_TREESITTER,
    reason="tree-sitter parser unavailable or incompatible "
           "(no symbol extraction)",
)
class TestRepoMapEnabled:
    """skip_repo_map=False → ctx contains 'repo_map'."""

    def test_ctx_contains_repo_map(self, leerie, tmp_path):
        repo = tmp_path / "repo"
        _write_fixture_repo(repo)
        ctx = _build_ctx(
            leerie,
            task="Fix the helper function",
            sot="codebase",
            answers={},
            confidence_rounds=8,
            repo_root=repo,
            leerie_root=tmp_path / "leerie-root",
            skip_repo_map=False,
        )
        assert "repo_map" in ctx, "Expected 'repo_map' key in ctx when skip_repo_map=False"

    def test_repo_map_is_nonempty_string(self, leerie, tmp_path):
        repo = tmp_path / "repo"
        _write_fixture_repo(repo)
        ctx = _build_ctx(
            leerie,
            task="Fix the helper function",
            sot="codebase",
            answers={},
            confidence_rounds=8,
            repo_root=repo,
            leerie_root=tmp_path / "leerie-root",
            skip_repo_map=False,
        )
        assert isinstance(ctx.get("repo_map"), str)
        assert ctx["repo_map"].strip() != ""

    def test_baseline_keys_present_when_repo_map_enabled(self, leerie, tmp_path):
        repo = tmp_path / "repo"
        _write_fixture_repo(repo)
        ctx = _build_ctx(
            leerie,
            task="Add a test",
            sot="both",
            answers={"source_of_truth": "both"},
            confidence_rounds=4,
            repo_root=repo,
            leerie_root=tmp_path / "leerie-root",
            skip_repo_map=False,
        )
        for key in ("task", "source_of_truth", "clarification_answers",
                    "confidence_rounds"):
            assert key in ctx, f"Baseline key '{key}' missing from ctx"

    def test_repo_map_contains_symbol_names(self, leerie, tmp_path):
        repo = tmp_path / "repo"
        _write_fixture_repo(repo)
        ctx = _build_ctx(
            leerie,
            task="Fix the helper function",
            sot="codebase",
            answers={},
            confidence_rounds=8,
            repo_root=repo,
            leerie_root=tmp_path / "leerie-root",
            skip_repo_map=False,
        )
        repo_map_text = ctx.get("repo_map", "")
        syms_found = [s for s in ["helper", "another", "run"]
                      if s in repo_map_text]
        assert syms_found, (
            f"No known symbols found in repo_map: {repo_map_text!r}"
        )

    def test_seed_files_from_task_file_items(self, leerie, tmp_path):
        """task_file_items → seed_files for rank_repo_map; seeded file appears."""
        repo = tmp_path / "repo"
        _write_fixture_repo(repo)
        # Provide task_file_items seeding utils.py
        task_file_items = ["utils.py: helper"]
        ctx = _build_ctx(
            leerie,
            task="Fix utils.py helper",
            sot="codebase",
            answers={},
            confidence_rounds=8,
            repo_root=repo,
            leerie_root=tmp_path / "leerie-root",
            skip_repo_map=False,
            task_file_items=task_file_items,
        )
        assert "repo_map" in ctx
        assert "utils.py" in ctx["repo_map"]

    def test_ctx_serializable_to_json(self, leerie, tmp_path):
        """The ctx dict (including repo_map) must be JSON-serializable."""
        repo = tmp_path / "repo"
        _write_fixture_repo(repo)
        ctx = _build_ctx(
            leerie,
            task="Fix the helper function",
            sot="codebase",
            answers={},
            confidence_rounds=8,
            repo_root=repo,
            leerie_root=tmp_path / "leerie-root",
            skip_repo_map=False,
        )
        serialized = json.dumps(ctx, indent=2)
        parsed = json.loads(serialized)
        assert parsed["repo_map"] == ctx["repo_map"]


# ---------------------------------------------------------------------------
# Branch 2: skip_repo_map=True — graceful degrade
# ---------------------------------------------------------------------------

class TestRepoMapSkipped:
    """skip_repo_map=True → ctx omits 'repo_map'; no exception raised."""

    def test_ctx_omits_repo_map(self, leerie, tmp_path):
        repo = tmp_path / "repo"
        _write_fixture_repo(repo)
        ctx = _build_ctx(
            leerie,
            task="Fix the helper function",
            sot="codebase",
            answers={},
            confidence_rounds=8,
            repo_root=repo,
            leerie_root=tmp_path / "leerie-root",
            skip_repo_map=True,
        )
        assert "repo_map" not in ctx, (
            "Expected 'repo_map' absent from ctx when skip_repo_map=True"
        )

    def test_baseline_keys_present_when_skipped(self, leerie, tmp_path):
        repo = tmp_path / "repo"
        _write_fixture_repo(repo)
        ctx = _build_ctx(
            leerie,
            task="Add a test",
            sot="research",
            answers={"q": "a"},
            confidence_rounds=6,
            repo_root=repo,
            leerie_root=tmp_path / "leerie-root",
            skip_repo_map=True,
        )
        for key in ("task", "source_of_truth", "clarification_answers",
                    "confidence_rounds"):
            assert key in ctx, f"Baseline key '{key}' missing when skip_repo_map=True"

    def test_values_match_inputs_when_skipped(self, leerie, tmp_path):
        repo = tmp_path / "repo"
        _write_fixture_repo(repo)
        ctx = _build_ctx(
            leerie,
            task="my task",
            sot="both",
            answers={"source_of_truth": "both"},
            confidence_rounds=3,
            repo_root=repo,
            leerie_root=tmp_path / "leerie-root",
            skip_repo_map=True,
        )
        assert ctx["task"] == "my task"
        assert ctx["source_of_truth"] == "both"
        assert ctx["confidence_rounds"] == 3


# ---------------------------------------------------------------------------
# Branch 3: empty repo → repo_map omitted even when skip_repo_map=False
# ---------------------------------------------------------------------------

class TestRepoMapEmptyDegrade:
    """Empty repo → rank_repo_map returns '' → 'repo_map' omitted from ctx."""

    def test_empty_repo_omits_repo_map(self, leerie, tmp_path):
        repo = tmp_path / "empty-repo"
        repo.mkdir(parents=True, exist_ok=True)
        ctx = _build_ctx(
            leerie,
            task="Add a feature",
            sot="codebase",
            answers={},
            confidence_rounds=8,
            repo_root=repo,
            leerie_root=tmp_path / "leerie-root",
            skip_repo_map=False,
        )
        # Empty repo → build_repo_map returns {"files":{}, "refs":{}}
        # → rank_repo_map returns "" → omitted
        assert "repo_map" not in ctx


# ---------------------------------------------------------------------------
# Branch 4: build_repo_map raises → exception swallowed, ctx sans repo_map
# ---------------------------------------------------------------------------

class TestRepoMapExceptionDegrade:
    """If build_repo_map raises, the exception is caught and ctx omits repo_map."""

    def test_exception_swallowed_degrade(self, leerie, tmp_path):
        repo = tmp_path / "repo"
        _write_fixture_repo(repo)
        with patch.object(leerie, "build_repo_map",
                          side_effect=RuntimeError("tree-sitter unavailable")):
            ctx = _build_ctx(
                leerie,
                task="Fix something",
                sot="codebase",
                answers={},
                confidence_rounds=8,
                repo_root=repo,
                leerie_root=tmp_path / "leerie-root",
                skip_repo_map=False,
            )
        assert "repo_map" not in ctx
        # Baseline keys still present
        assert "task" in ctx
        assert "confidence_rounds" in ctx
