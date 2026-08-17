"""Tests for two leaf BLT helpers with no direct coverage elsewhere:
_package_json_scripts (package.json `scripts` map extraction) and
_is_blt_feedback_warning (the CRITIC-pattern feedback injection gate).
"""
from __future__ import annotations

import json


class TestPackageJsonScripts:
    def test_well_formed_file(self, leerie, tmp_path):
        (tmp_path / "package.json").write_text(
            json.dumps({"scripts": {"build": "tsc", "test": "vitest run"}}))
        assert leerie._package_json_scripts(tmp_path) == {
            "build": "tsc", "test": "vitest run"}

    def test_missing_file(self, leerie, tmp_path):
        assert leerie._package_json_scripts(tmp_path) == {}

    def test_invalid_json(self, leerie, tmp_path):
        (tmp_path / "package.json").write_text("{not valid json")
        assert leerie._package_json_scripts(tmp_path) == {}

    def test_scripts_value_not_a_dict(self, leerie, tmp_path):
        (tmp_path / "package.json").write_text(
            json.dumps({"scripts": ["build", "test"]}))
        assert leerie._package_json_scripts(tmp_path) == {}

    def test_scripts_key_absent(self, leerie, tmp_path):
        (tmp_path / "package.json").write_text(json.dumps({"name": "x"}))
        assert leerie._package_json_scripts(tmp_path) == {}

    def test_top_level_not_a_dict(self, leerie, tmp_path):
        (tmp_path / "package.json").write_text(json.dumps(["a", "b"]))
        assert leerie._package_json_scripts(tmp_path) == {}

    def test_non_string_script_values_dropped(self, leerie, tmp_path):
        (tmp_path / "package.json").write_text(
            json.dumps({"scripts": {"build": "tsc", "bad": 123}}))
        assert leerie._package_json_scripts(tmp_path) == {"build": "tsc"}


class TestIsBltFeedbackWarning:
    def test_matches_auto_backgrounded_marker(self, leerie):
        assert leerie._is_blt_feedback_warning(
            "round-1: worker's invocation auto-backgrounded and was "
            "followed by a retry")

    def test_matches_ran_the_full_marker(self, leerie):
        assert leerie._is_blt_feedback_warning(
            "round-1: ran the full BUILD axis 2 time(s) (see log)")

    def test_unrelated_warning_returns_false(self, leerie):
        assert not leerie._is_blt_feedback_warning(
            "round-1: some other advisory warning entirely")

    def test_empty_string_returns_false(self, leerie):
        assert not leerie._is_blt_feedback_warning("")
