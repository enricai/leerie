"""Unit tests for the repo-map rendering and safety leaf helpers (DESIGN §5½
(P6)): _is_same_document, _render_repo_map_subgraph, _count_tokens_approx,
_resolves_under.

Their siblings (_rank_repo_map, _build_repo_map) are extensively covered in
tests/test_repo_map.py; these four are not referenced by name anywhere else
in the suite.
"""
from __future__ import annotations

from pathlib import Path


# ---------------------------------------------------------------------------
# _is_same_document
# ---------------------------------------------------------------------------

class TestIsSameDocument:
    def test_true_for_exact_content_match(self, leerie, tmp_path):
        text = "hello world\nline two"
        f = tmp_path / "task.md"
        f.write_text(text)
        assert leerie._is_same_document(f, len(text.encode()), text) is True

    def test_true_modulo_surrounding_whitespace(self, leerie, tmp_path):
        text = "hello world"
        f = tmp_path / "task.md"
        f.write_text("  \n" + text + "\n\n  ")
        assert leerie._is_same_document(
            f, len(text.encode()), text
        ) is True

    def test_false_on_size_mismatch_beyond_threshold(self, leerie, tmp_path):
        text = "short"
        f = tmp_path / "task.md"
        # far longer than `text`, well past the documented 8-byte pre-check
        f.write_text("short" + "x" * 100)
        assert leerie._is_same_document(
            f, len(text.encode()), text
        ) is False

    def test_size_precheck_prevents_reading_when_mismatched(
        self, leerie, tmp_path, monkeypatch
    ):
        text = "short"
        f = tmp_path / "task.md"
        f.write_text("short" + "x" * 100)

        def _boom(*a, **kw):
            raise AssertionError("read_text should not be called")

        monkeypatch.setattr(Path, "read_text", _boom, raising=True)
        assert leerie._is_same_document(
            f, len(text.encode()), text
        ) is False

    def test_false_for_different_content_same_size(self, leerie, tmp_path):
        text = "aaaa"
        f = tmp_path / "task.md"
        f.write_text("bbbb")
        assert leerie._is_same_document(
            f, len(text.encode()), text
        ) is False

    def test_false_on_missing_file(self, leerie, tmp_path):
        text = "hello"
        f = tmp_path / "missing.md"
        assert leerie._is_same_document(
            f, len(text.encode()), text
        ) is False


# ---------------------------------------------------------------------------
# _render_repo_map_subgraph
# ---------------------------------------------------------------------------

class TestRenderRepoMapSubgraph:
    def _repo_map(self):
        return {
            "files": {
                "a.py": ["FooA", "BarA"],
                "b.py": ["FooB"],
                "c.py": [],  # no defs — must be omitted
                "d.py": ["FooD"],
            },
            "refs": {},
        }

    def test_one_line_per_ranked_file_with_symbols(self, leerie):
        repo_map = self._repo_map()
        ranked = [("a.py", 3.0), ("b.py", 2.0), ("d.py", 1.0)]
        out = leerie._render_repo_map_subgraph(repo_map, ranked, max_files=10)
        lines = out.split("\n")
        assert lines == [
            "a.py: FooA, BarA",
            "b.py: FooB",
            "d.py: FooD",
        ]

    def test_honors_max_files(self, leerie):
        repo_map = self._repo_map()
        ranked = [("a.py", 3.0), ("b.py", 2.0), ("d.py", 1.0)]
        out = leerie._render_repo_map_subgraph(repo_map, ranked, max_files=1)
        assert out == "a.py: FooA, BarA"

    def test_top_ranked_first(self, leerie):
        repo_map = self._repo_map()
        # ranked_files is already in rank order; the renderer must not
        # re-sort it.
        ranked = [("b.py", 9.0), ("a.py", 1.0)]
        out = leerie._render_repo_map_subgraph(repo_map, ranked, max_files=10)
        assert out.split("\n") == ["b.py: FooB", "a.py: FooA, BarA"]

    def test_files_with_no_defs_are_omitted(self, leerie):
        repo_map = self._repo_map()
        ranked = [("c.py", 5.0), ("a.py", 1.0)]
        out = leerie._render_repo_map_subgraph(repo_map, ranked, max_files=10)
        assert "c.py" not in out
        assert out == "a.py: FooA, BarA"

    def test_file_not_in_files_map_is_skipped(self, leerie):
        repo_map = self._repo_map()
        ranked = [("nowhere.py", 5.0), ("a.py", 1.0)]
        out = leerie._render_repo_map_subgraph(repo_map, ranked, max_files=10)
        assert out == "a.py: FooA, BarA"

    def test_empty_ranked_files_yields_empty_string(self, leerie):
        repo_map = self._repo_map()
        out = leerie._render_repo_map_subgraph(repo_map, [], max_files=10)
        assert out == ""


# ---------------------------------------------------------------------------
# _count_tokens_approx
# ---------------------------------------------------------------------------

class TestCountTokensApprox:
    def test_approximates_four_bytes_per_token(self, leerie):
        text = "x" * 400
        assert leerie._count_tokens_approx(text) == 100

    def test_never_returns_zero_for_empty_string(self, leerie):
        assert leerie._count_tokens_approx("") == 1

    def test_never_returns_zero_for_tiny_string(self, leerie):
        assert leerie._count_tokens_approx("ab") == 1

    def test_uses_encoded_byte_length_not_char_length(self, leerie):
        # multi-byte UTF-8 chars: byte length > char length
        text = "é" * 8  # 2 bytes each in UTF-8 -> 16 bytes -> 4 tokens
        assert leerie._count_tokens_approx(text) == 4


# ---------------------------------------------------------------------------
# _resolves_under
# ---------------------------------------------------------------------------

class TestResolvesUnder:
    def test_true_for_relative_path_inside_root(self, leerie, tmp_path):
        root = tmp_path / "repo"
        root.mkdir()
        (root / "sub").mkdir()
        assert leerie._resolves_under("sub/file.py", root) is True

    def test_true_for_absolute_path_inside_root(self, leerie, tmp_path):
        root = tmp_path / "repo"
        root.mkdir()
        target = root / "sub" / "file.py"
        assert leerie._resolves_under(str(target), root) is True

    def test_false_for_path_escaping_root_via_dotdot(self, leerie, tmp_path):
        root = tmp_path / "repo"
        root.mkdir()
        outside = tmp_path / "elsewhere"
        outside.mkdir()
        assert leerie._resolves_under("../elsewhere/x.py", root) is False

    def test_false_for_absolute_path_outside_root(self, leerie, tmp_path):
        root = tmp_path / "repo"
        root.mkdir()
        outside = tmp_path / "elsewhere" / "x.py"
        assert leerie._resolves_under(str(outside), root) is False

    def test_symlinked_decoy_escaping_root_returns_false(
        self, leerie, tmp_path
    ):
        root = tmp_path / "repo"
        root.mkdir()
        outside_dir = tmp_path / "outside"
        outside_dir.mkdir()
        secret = outside_dir / "secret.txt"
        secret.write_text("nope")

        decoy = root / "decoy.txt"
        decoy.symlink_to(secret)

        assert leerie._resolves_under("decoy.txt", root) is False

    def test_symlink_target_inside_root_returns_true(self, leerie, tmp_path):
        root = tmp_path / "repo"
        root.mkdir()
        real = root / "real.txt"
        real.write_text("hi")
        link = root / "link.txt"
        link.symlink_to(real)

        assert leerie._resolves_under("link.txt", root) is True

    def test_false_on_value_error(self, leerie, tmp_path, monkeypatch):
        root = tmp_path / "repo"
        root.mkdir()

        def _boom(*a, **kw):
            raise ValueError("boom")

        monkeypatch.setattr(Path, "resolve", _boom, raising=True)
        assert leerie._resolves_under("x.py", root) is False

    def test_false_on_os_error(self, leerie, tmp_path, monkeypatch):
        root = tmp_path / "repo"
        root.mkdir()

        def _boom(*a, **kw):
            raise OSError("boom")

        monkeypatch.setattr(Path, "resolve", _boom, raising=True)
        assert leerie._resolves_under("x.py", root) is False
