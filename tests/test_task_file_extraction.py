"""Tests for task-referenced file resolution.

Covers ``_expand_braces``, ``_glob_task_references``, ``_repo_rel`` and
``_format_task_file_references`` — all pure path arithmetic. leerie names
the files a task points at; the planner and ``task_coverage_judge`` read
them. Nothing here parses their contents, and ``TestProseHarvestAbsent``
pins that the machinery which used to cannot return.
"""
from __future__ import annotations

from pathlib import Path

import pytest


# --- _glob_task_references ------------------------------------------------ #

class TestExpandBraces:
    def test_no_braces(self, leerie):
        assert leerie._expand_braces("foo.md") == ["foo.md"]

    def test_simple_braces(self, leerie):
        result = leerie._expand_braces("spec.{md,yaml}")
        assert sorted(result) == ["spec.md", "spec.yaml"]

    def test_braces_with_glob(self, leerie):
        result = leerie._expand_braces("spec-*.{md,yaml}")
        assert sorted(result) == ["spec-*.md", "spec-*.yaml"]

    def test_three_alternatives(self, leerie):
        result = leerie._expand_braces("f.{a,b,c}")
        assert sorted(result) == ["f.a", "f.b", "f.c"]


class TestGlobTaskReferences:
    def test_no_file_refs(self, leerie, tmp_path):
        assert leerie._glob_task_references(
            "fix the login bug", tmp_path) == []

    def test_explicit_md_file(self, leerie, tmp_path):
        (tmp_path / "spec.md").write_text("# Spec\n")
        result = leerie._glob_task_references(
            "implement everything in spec.md", tmp_path)
        assert len(result) == 1
        assert result[0].name == "spec.md"

    def test_glob_pattern(self, leerie, tmp_path):
        (tmp_path / "plan-a.md").write_text("# A\n")
        (tmp_path / "plan-b.md").write_text("# B\n")
        (tmp_path / "plan-c.txt").write_text("C\n")
        result = leerie._glob_task_references(
            "check plan-*.md files", tmp_path)
        assert len(result) == 2

    def test_yaml_file(self, leerie, tmp_path):
        (tmp_path / "tasks.yaml").write_text("- id: t1\n")
        result = leerie._glob_task_references(
            "complete tasks.yaml", tmp_path)
        assert len(result) == 1

    def test_nonexistent_file(self, leerie, tmp_path):
        result = leerie._glob_task_references(
            "fix missing.py", tmp_path)
        assert result == []

    def test_brace_expansion(self, leerie, tmp_path):
        (tmp_path / "plan.md").write_text("# Plan\n")
        (tmp_path / "plan.yaml").write_text("- id: t1\n")
        (tmp_path / "plan.txt").write_text("ignored\n")
        result = leerie._glob_task_references(
            "check plan.{md,yaml}", tmp_path)
        assert len(result) == 2
        names = {p.name for p in result}
        assert names == {"plan.md", "plan.yaml"}

    def test_brace_expansion_with_glob(self, leerie, tmp_path):
        (tmp_path / "spec-a.md").write_text("# A\n")
        (tmp_path / "spec-b.yaml").write_text("- id: b\n")
        result = leerie._glob_task_references(
            "check spec-*.{md,yaml}", tmp_path)
        assert len(result) == 2

    def test_deduplication(self, leerie, tmp_path):
        (tmp_path / "spec.md").write_text("# Spec\n")
        result = leerie._glob_task_references(
            "check spec.md and also spec.md again", tmp_path)
        assert len(result) == 1


# --- extract_task_file_structure ---------------------------------------- #

class TestFormatTaskFileReferences:
    """The planner is told WHICH files the task names; it reads them itself.

    leerie used to harvest markdown headings and YAML keys out of those
    files with regex (`extract_task_file_structure`), classify the
    harvested prose (`_is_uncoverable_convention_item`), and
    substring-match the result against the plan text
    (`check_task_file_coverage`) — three layers of prose parsing, and the
    mechanism that froze run 2026-07-19 for 33 identical feedback rounds
    on a ratio no planner could move. Coverage of what those files require
    is `task_coverage_judge`'s job (phase 2⅞½); its prompt now tells it to
    read them. See `TestProseHarvestAbsent` below.
    """

    def test_lists_referenced_files(self, leerie, tmp_path):
        (tmp_path / "SPEC.md").write_text("# Spec\n\n### Do the thing\n")
        (tmp_path / "conf.yaml").write_text("key: value\n")
        files = leerie._glob_task_references(
            "implement SPEC.md per conf.yaml", tmp_path)
        out = leerie._format_task_file_references(files, tmp_path)
        assert "SPEC.md" in out and "conf.yaml" in out
        assert "Read each one" in out

    def test_none_when_no_files_referenced(self, leerie, tmp_path):
        files = leerie._glob_task_references("just do the thing", tmp_path)
        assert leerie._format_task_file_references(files, tmp_path) is None

    def test_lists_paths_only_never_file_contents(self, leerie, tmp_path):
        """The whole point: the section names files, it does not digest
        them. A heading inside the file must not appear in the section."""
        (tmp_path / "SPEC.md").write_text(
            "# Spec\n\n### Run `pnpm lint` - MUST pass\n\n1. First item\n")
        files = leerie._glob_task_references("implement SPEC.md", tmp_path)
        out = leerie._format_task_file_references(files, tmp_path)
        assert "pnpm lint" not in out
        assert "First item" not in out
        assert "MUST" not in out

    def test_paths_are_repo_relative(self, leerie, tmp_path):
        sub = tmp_path / "docs"
        sub.mkdir()
        (sub / "DESIGN.md").write_text("# D\n")
        files = leerie._glob_task_references("see docs/DESIGN.md", tmp_path)
        out = leerie._format_task_file_references(files, tmp_path)
        assert "docs/DESIGN.md" in out
        assert str(tmp_path) not in out, "absolute paths leak the sandbox"


class TestRepoRel:
    def test_relative_inside_repo(self, leerie, tmp_path):
        (tmp_path / "a").mkdir()
        f = tmp_path / "a" / "b.md"
        f.write_text("x")
        assert leerie._repo_rel(f, tmp_path) == "a/b.md"

    def test_falls_back_to_name_outside_repo(self, leerie, tmp_path):
        from pathlib import Path
        outside = Path("/etc/hostname")
        assert leerie._repo_rel(outside, tmp_path) == "hostname"


class TestProseHarvestAbsent:
    """Mirrors `TestRegexPathAbsent` in tests/test_capture_deps.py — the
    precedent CLAUDE.md names for a migration off hand-parsing."""

    @pytest.mark.parametrize("sym", [
        "extract_task_file_structure",
        "_is_uncoverable_convention_item",
        "_BACKTICK_SPAN_RE",
        "check_task_file_coverage",
        "_format_task_file_structure",
        "_MAX_COVERAGE_ITEMS",
        "_dedup_frozen_coverage_issues",
    ])
    def test_deleted_symbols_stay_deleted(self, leerie, sym):
        assert not hasattr(leerie, sym), (
            f"{sym} is back — the prose-harvest path, and with it the "
            "2026-07-19 coverage-freeze class, can silently resume")

    def test_planner_loop_has_no_coverage_gate(self, leerie):
        """The LOW_COVERAGE issue is gone from the planner's check loop,
        not merely silenced."""
        import inspect
        src = inspect.getsource(leerie.phase_plan)
        assert "LOW_COVERAGE" not in src
        assert "coverage_ratios" not in src

    def test_reference_section_does_not_read_file_contents(self, leerie):
        """Banned by shape: the helper must not open the files it names."""
        import inspect
        src = inspect.getsource(leerie._format_task_file_references)
        for banned in ("read_text", "open(", "finditer", "re."):
            assert banned not in src, (
                f"_format_task_file_references calls {banned!r} — naming "
                "the files is mechanical, reading them is the planner's job")

    def test_coverage_judge_is_told_to_read_the_files(self):
        """The job did not vanish, it moved. If the judge is not told to
        read referenced files, this migration dropped a check instead of
        relocating it."""
        from pathlib import Path
        text = (Path(__file__).resolve().parent.parent
                / "prompts" / "task_coverage_judge.md").read_text()
        assert "Read the files the task names" in text
        assert "not the codebase" not in text, (
            "the old scoping sentence still tells the judge it has no "
            "files to read")
