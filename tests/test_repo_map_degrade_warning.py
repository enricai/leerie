"""G6 — make silent P6 degradation visible (DESIGN §12 "no silent
under-coverage").

When build_repo_map() finds source files but produces an EMPTY symbol graph
(tree-sitter unavailable or its API incompatible — e.g. a language-pack
version without process()), the whole P6 layer silently becomes a no-op the
planner cannot detect. build_repo_map() must emit exactly ONE warning per
process in that case, and stay quiet for a genuinely empty/non-code repo.

These tests force the empty-graph condition by stubbing _parse_repo_file, so
they run regardless of whether tree-sitter is installed (they test the degrade
path itself, not extraction).
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import patch

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
LEERIE_PY = REPO_ROOT / "orchestrator" / "leerie.py"


@pytest.fixture(scope="session")
def leerie():
    spec = importlib.util.spec_from_file_location("leerie", LEERIE_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(autouse=True)
def _reset_warn_latch(leerie):
    """The once-per-process latch is module global; reset around each test."""
    leerie._repo_map_empty_warned = False
    yield
    leerie._repo_map_empty_warned = False


def _empty_parse(path):
    return [], []


def test_warns_when_source_files_but_empty_graph(leerie, tmp_path, capsys):
    (tmp_path / "app.py").write_text("def x():\n    pass\n")
    (tmp_path / "b.ts").write_text("function y(){}\n")
    lr = tmp_path / "leerie-root"
    with patch.object(leerie, "_parse_repo_file", new=_empty_parse):
        rm = leerie.build_repo_map(tmp_path, lr)
    assert rm["files"] == {}
    out = capsys.readouterr().out
    assert "repo-map is empty" in out
    assert "tree-sitter" in out


def test_no_warning_for_non_code_repo(leerie, tmp_path, capsys):
    # Only non-source files → legitimate empty graph, must stay quiet.
    (tmp_path / "README.md").write_text("# hi\n")
    (tmp_path / "data.json").write_text("{}\n")
    lr = tmp_path / "leerie-root"
    with patch.object(leerie, "_parse_repo_file", new=_empty_parse):
        rm = leerie.build_repo_map(tmp_path, lr)
    assert rm["files"] == {}
    assert "repo-map is empty" not in capsys.readouterr().out


def test_warns_only_once_per_process(leerie, tmp_path, capsys):
    (tmp_path / "app.py").write_text("def x():\n    pass\n")
    lr = tmp_path / "leerie-root"
    with patch.object(leerie, "_parse_repo_file", new=_empty_parse):
        leerie.build_repo_map(tmp_path, lr)
        first = capsys.readouterr().out
        leerie.build_repo_map(tmp_path, lr)
        second = capsys.readouterr().out
    assert "repo-map is empty" in first
    assert "repo-map is empty" not in second


def test_no_false_warning_when_parser_works_but_repo_symbolless(
        leerie, tmp_path, capsys):
    """No false positive: if tree-sitter WORKS (functional probe extracts a
    symbol) but the repo genuinely has no extractable symbols, stay quiet —
    an empty graph is legitimate, not a broken parser. The walk returns empty
    while the probe (a separate _parse_repo_file call on a known snippet)
    succeeds."""
    (tmp_path / "commentonly.py").write_text("# no symbols here\n")
    lr = tmp_path / "leerie-root"

    # Walk yields nothing for the repo's files, but the functional probe's
    # own snippet DOES extract its symbol → parser is fine → no warning.
    def _parse(path):
        if path.name == "_probe.py":
            return ["_probe_sym"], []
        return [], []

    with patch.object(leerie, "_parse_repo_file", new=_parse):
        rm = leerie.build_repo_map(tmp_path, lr)
    assert rm["files"] == {}
    assert "repo-map is empty" not in capsys.readouterr().out
