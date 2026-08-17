"""Tests for the dep-capture manifest helpers that feed the DESIGN §6½
manifests-first corpus but are never referenced directly by name in the
existing test suite: _normalize_setup_packages, _read_file_safely,
_sample_workspace_manifests.
"""
from __future__ import annotations

from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# _normalize_setup_packages
# ---------------------------------------------------------------------------

class TestNormalizeSetupPackages:

    def test_order_preserving_dedup(self, leerie):
        result = leerie._normalize_setup_packages(["a", "b", "a", "c", "b"])
        assert result == "a b c"

    def test_space_joins(self, leerie):
        result = leerie._normalize_setup_packages(["libpq-dev", "postgresql"])
        assert result == "libpq-dev postgresql"

    def test_drops_falsy_entries(self, leerie):
        result = leerie._normalize_setup_packages(["a", "", None, "b", ""])
        assert result == "a b"

    def test_empty_list_yields_empty_string(self, leerie):
        assert leerie._normalize_setup_packages([]) == ""

    def test_single_package(self, leerie):
        assert leerie._normalize_setup_packages(["only-one"]) == "only-one"

    def test_preserves_first_seen_order_not_sorted(self, leerie):
        result = leerie._normalize_setup_packages(["zeta", "alpha", "zeta"])
        assert result == "zeta alpha"


# ---------------------------------------------------------------------------
# _read_file_safely
# ---------------------------------------------------------------------------

class TestReadFileSafely:

    def test_reads_full_content_under_budget(self, leerie, tmp_path):
        p = tmp_path / "f.txt"
        p.write_text("hello world")
        assert leerie._read_file_safely(p, 1000) == "hello world"

    def test_missing_file_returns_empty_string(self, leerie, tmp_path):
        p = tmp_path / "does-not-exist.txt"
        assert leerie._read_file_safely(p, 1000) == ""

    def test_missing_file_never_raises(self, leerie, tmp_path):
        p = tmp_path / "nested" / "does-not-exist.txt"
        # Parent dir doesn't exist either — still must not raise.
        try:
            result = leerie._read_file_safely(p, 1000)
        except OSError:
            pytest.fail("_read_file_safely raised OSError instead of swallowing it")
        assert result == ""

    def test_directory_path_returns_empty_string(self, leerie, tmp_path):
        # read_text(errors="replace") never raises UnicodeError in practice
        # (replace mode always succeeds), so the swallow contract's OSError
        # arm is exercised via a path that is a directory, not a file.
        p = tmp_path / "a-directory"
        p.mkdir()
        result = leerie._read_file_safely(p, 1000)
        assert result == ""

    def test_truncates_to_byte_budget(self, leerie, tmp_path):
        p = tmp_path / "f.txt"
        p.write_text("abcdefghij")
        assert leerie._read_file_safely(p, 5) == "abcde"

    def test_budget_larger_than_content_returns_whole_file(self, leerie, tmp_path):
        p = tmp_path / "f.txt"
        p.write_text("short")
        assert leerie._read_file_safely(p, 1000) == "short"


# ---------------------------------------------------------------------------
# _sample_workspace_manifests
# ---------------------------------------------------------------------------

class TestSampleWorkspaceManifests:

    def test_no_workspaces_key_returns_empty(self, leerie, tmp_path):
        pkg_json_text = '{"name": "root", "version": "1.0.0"}'
        result = leerie._sample_workspace_manifests(tmp_path, pkg_json_text, 1000, 10)
        assert result == []

    def test_malformed_json_returns_empty(self, leerie, tmp_path):
        result = leerie._sample_workspace_manifests(tmp_path, "not json{{{", 1000, 10)
        assert result == []

    def test_workspaces_not_a_list_returns_empty(self, leerie, tmp_path):
        pkg_json_text = '{"workspaces": "packages/*"}'
        result = leerie._sample_workspace_manifests(tmp_path, pkg_json_text, 1000, 10)
        assert result == []

    def test_empty_workspaces_list_returns_empty(self, leerie, tmp_path):
        pkg_json_text = '{"workspaces": []}'
        result = leerie._sample_workspace_manifests(tmp_path, pkg_json_text, 1000, 10)
        assert result == []

    def test_real_monorepo_fixture_returns_child_manifests(self, leerie, tmp_path):
        (tmp_path / "packages" / "foo").mkdir(parents=True)
        (tmp_path / "packages" / "bar").mkdir(parents=True)
        (tmp_path / "packages" / "foo" / "package.json").write_text(
            '{"name": "foo", "dependencies": {"left-pad": "1.0.0"}}'
        )
        (tmp_path / "packages" / "bar" / "package.json").write_text(
            '{"name": "bar", "dependencies": {"right-pad": "1.0.0"}}'
        )
        pkg_json_text = '{"workspaces": ["packages/*"]}'
        result = leerie._sample_workspace_manifests(tmp_path, pkg_json_text, 1000, 10)
        assert len(result) == 2
        rel_paths = {rel for rel, _text in result}
        assert rel_paths == {"packages/foo/package.json", "packages/bar/package.json"}
        texts = {rel: text for rel, text in result}
        assert "left-pad" in texts["packages/foo/package.json"]
        assert "right-pad" in texts["packages/bar/package.json"]

    def test_npm_yarn_packages_dict_shape(self, leerie, tmp_path):
        (tmp_path / "libs" / "a").mkdir(parents=True)
        (tmp_path / "libs" / "a" / "package.json").write_text('{"name": "a"}')
        pkg_json_text = '{"workspaces": {"packages": ["libs/*"]}}'
        result = leerie._sample_workspace_manifests(tmp_path, pkg_json_text, 1000, 10)
        assert len(result) == 1
        assert result[0][0] == "libs/a/package.json"

    def test_respects_max_files_cap(self, leerie, tmp_path):
        for i in range(5):
            d = tmp_path / "packages" / f"pkg{i}"
            d.mkdir(parents=True)
            (d / "package.json").write_text(f'{{"name": "pkg{i}"}}')
        pkg_json_text = '{"workspaces": ["packages/*"]}'
        result = leerie._sample_workspace_manifests(tmp_path, pkg_json_text, 1000, 2)
        assert len(result) == 2

    def test_per_file_budget_truncates_child_text(self, leerie, tmp_path):
        d = tmp_path / "packages" / "big"
        d.mkdir(parents=True)
        (d / "package.json").write_text('{"name": "big-package-with-a-long-name"}')
        pkg_json_text = '{"workspaces": ["packages/*"]}'
        result = leerie._sample_workspace_manifests(tmp_path, pkg_json_text, 5, 10)
        assert len(result) == 1
        assert result[0][1] == '{"nam'

    def test_pattern_with_no_matching_children_returns_empty(self, leerie, tmp_path):
        pkg_json_text = '{"workspaces": ["nonexistent/*"]}'
        result = leerie._sample_workspace_manifests(tmp_path, pkg_json_text, 1000, 10)
        assert result == []

    def test_non_string_pattern_is_skipped(self, leerie, tmp_path):
        d = tmp_path / "packages" / "ok"
        d.mkdir(parents=True)
        (d / "package.json").write_text('{"name": "ok"}')
        pkg_json_text = '{"workspaces": [123, "packages/*"]}'
        result = leerie._sample_workspace_manifests(tmp_path, pkg_json_text, 1000, 10)
        assert len(result) == 1
        assert result[0][0] == "packages/ok/package.json"

    def test_dedups_when_multiple_patterns_match_same_child(self, leerie, tmp_path):
        d = tmp_path / "packages" / "shared"
        d.mkdir(parents=True)
        (d / "package.json").write_text('{"name": "shared"}')
        pkg_json_text = '{"workspaces": ["packages/*", "packages/shared"]}'
        result = leerie._sample_workspace_manifests(tmp_path, pkg_json_text, 1000, 10)
        assert len(result) == 1
