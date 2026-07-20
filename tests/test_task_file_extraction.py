"""Tests for task-referenced file extraction and coverage checking.

Covers ``glob_task_references``, ``extract_task_file_structure``,
``check_task_file_coverage``, and ``_format_task_file_structure``.
"""
from __future__ import annotations

from pathlib import Path


# --- glob_task_references ------------------------------------------------ #

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
        assert leerie.glob_task_references(
            "fix the login bug", tmp_path) == []

    def test_explicit_md_file(self, leerie, tmp_path):
        (tmp_path / "spec.md").write_text("# Spec\n")
        result = leerie.glob_task_references(
            "implement everything in spec.md", tmp_path)
        assert len(result) == 1
        assert result[0].name == "spec.md"

    def test_glob_pattern(self, leerie, tmp_path):
        (tmp_path / "plan-a.md").write_text("# A\n")
        (tmp_path / "plan-b.md").write_text("# B\n")
        (tmp_path / "plan-c.txt").write_text("C\n")
        result = leerie.glob_task_references(
            "check plan-*.md files", tmp_path)
        assert len(result) == 2

    def test_yaml_file(self, leerie, tmp_path):
        (tmp_path / "tasks.yaml").write_text("- id: t1\n")
        result = leerie.glob_task_references(
            "complete tasks.yaml", tmp_path)
        assert len(result) == 1

    def test_nonexistent_file(self, leerie, tmp_path):
        result = leerie.glob_task_references(
            "fix missing.py", tmp_path)
        assert result == []

    def test_brace_expansion(self, leerie, tmp_path):
        (tmp_path / "plan.md").write_text("# Plan\n")
        (tmp_path / "plan.yaml").write_text("- id: t1\n")
        (tmp_path / "plan.txt").write_text("ignored\n")
        result = leerie.glob_task_references(
            "check plan.{md,yaml}", tmp_path)
        assert len(result) == 2
        names = {p.name for p in result}
        assert names == {"plan.md", "plan.yaml"}

    def test_brace_expansion_with_glob(self, leerie, tmp_path):
        (tmp_path / "spec-a.md").write_text("# A\n")
        (tmp_path / "spec-b.yaml").write_text("- id: b\n")
        result = leerie.glob_task_references(
            "check spec-*.{md,yaml}", tmp_path)
        assert len(result) == 2

    def test_deduplication(self, leerie, tmp_path):
        (tmp_path / "spec.md").write_text("# Spec\n")
        result = leerie.glob_task_references(
            "check spec.md and also spec.md again", tmp_path)
        assert len(result) == 1


# --- extract_task_file_structure ---------------------------------------- #

class TestExtractTaskFileStructure:
    def test_no_files_returns_none(self, leerie, tmp_path):
        assert leerie.extract_task_file_structure(
            "fix the bug", tmp_path) is None

    def test_markdown_headings_h3_plus(self, leerie, tmp_path):
        (tmp_path / "spec.md").write_text(
            "# Overview\n## Details\n### Deep\n#### Deeper\nSome text\n")
        items = leerie.extract_task_file_structure(
            "check spec.md", tmp_path)
        assert items is not None
        assert not any("Overview" in i for i in items)
        assert not any("Details" in i for i in items)
        assert any("Deep" in i for i in items)
        assert any("Deeper" in i for i in items)

    def test_numbered_items(self, leerie, tmp_path):
        (tmp_path / "plan.md").write_text(
            "1. First step\n2. Second step\nNot numbered\n")
        items = leerie.extract_task_file_structure(
            "implement plan.md", tmp_path)
        assert items is not None
        assert any("First step" in i for i in items)
        assert any("Second step" in i for i in items)

    def test_toc_links_excluded(self, leerie, tmp_path):
        (tmp_path / "spec.md").write_text(
            "1. [Overview](#overview)\n2. Real item\n"
            "3. [Details](#details)\n")
        items = leerie.extract_task_file_structure(
            "review spec.md", tmp_path)
        assert items is not None
        assert not any("Overview" in i for i in items)
        assert not any("Details" in i for i in items)
        assert any("Real item" in i for i in items)

    def test_yaml_list_ids(self, leerie, tmp_path):
        (tmp_path / "jobs.yaml").write_text(
            "- id: wave-0\n  name: first\n- id: wave-1\n  name: second\n")
        items = leerie.extract_task_file_structure(
            "complete jobs.yaml", tmp_path)
        assert items is not None
        assert any("wave-0" in i for i in items)
        assert any("wave-1" in i for i in items)

    def test_yaml_top_level_keys(self, leerie, tmp_path):
        (tmp_path / "config.yaml").write_text(
            "database:\n  host: localhost\nredis:\n  port: 6379\n")
        items = leerie.extract_task_file_structure(
            "audit config.yaml", tmp_path)
        assert items is not None
        assert any("database" in i for i in items)
        assert any("redis" in i for i in items)

    def test_empty_file_returns_none(self, leerie, tmp_path):
        (tmp_path / "empty.md").write_text("")
        assert leerie.extract_task_file_structure(
            "check empty.md", tmp_path) is None


# --- check_task_file_coverage ------------------------------------------- #

class TestCheckTaskFileCoverage:
    def test_full_coverage(self, leerie):
        extracted = ["spec.md: Overview", "spec.md: Details"]
        subtasks = [
            {"title": "Fix overview", "intent": "Overview fix",
             "investigation_notes": ""},
            {"title": "Fix details", "intent": "Details fix",
             "investigation_notes": ""},
        ]
        assert leerie.check_task_file_coverage(extracted, subtasks) == []

    def test_low_coverage(self, leerie):
        extracted = ["spec.md: A", "spec.md: B", "spec.md: C",
                     "spec.md: D"]
        subtasks = [{"title": "Fix A", "intent": "A",
                     "investigation_notes": ""}]
        issues = leerie.check_task_file_coverage(extracted, subtasks)
        assert any("LOW_COVERAGE" in i for i in issues)

    def test_empty_extracted(self, leerie):
        assert leerie.check_task_file_coverage([], []) == []

    def test_just_above_threshold(self, leerie):
        extracted = ["a: X", "a: Y"]
        subtasks = [{"title": "covers X", "intent": "X",
                     "investigation_notes": ""}]
        # 1/2 uncovered = 50%, which is the threshold — should not fire
        assert leerie.check_task_file_coverage(extracted, subtasks) == []

    def test_over_threshold(self, leerie):
        extracted = ["a: X", "a: Y", "a: Z"]
        subtasks = [{"title": "covers X", "intent": "X",
                     "investigation_notes": ""}]
        # 2/3 uncovered = 66% > 50%
        issues = leerie.check_task_file_coverage(extracted, subtasks)
        assert any("LOW_COVERAGE" in i for i in issues)

    def test_skipped_when_too_many_items(self, leerie):
        extracted = [f"spec.md: item-{i}" for i in range(51)]
        subtasks = [{"title": "fix one", "intent": "item-0",
                     "investigation_notes": ""}]
        assert leerie.check_task_file_coverage(extracted, subtasks) == []

    def test_gates_when_under_cap(self, leerie):
        extracted = [f"spec.md: item-{i}" for i in range(50)]
        subtasks = [{"title": "fix one", "intent": "item-0",
                     "investigation_notes": ""}]
        issues = leerie.check_task_file_coverage(extracted, subtasks)
        assert any("LOW_COVERAGE" in i for i in issues)

    def test_uncoverable_convention_items_excluded(self, leerie):
        # 2026-07-19 incident shape: CLAUDE.md headings that are
        # coding-standard imperatives (backtick-quoted command + MUST)
        # cannot appear verbatim in a subtask title/intent, so they must
        # not count toward the coverage ratio at all.
        extracted = [
            "CLAUDE.md: Run `pnpm run lint:fix` - MUST pass with no errors",
            "CLAUDE.md: Run `pnpm run build` - MUST succeed",
            "CLAUDE.md: Confirm TypeScript strict mode compliance",
        ]
        subtasks = [{"title": "Confirm TypeScript strict mode compliance",
                     "intent": "", "investigation_notes": ""}]
        # Only one coverable item (the non-imperative heading) remains,
        # and it's covered — no LOW_COVERAGE despite 2/3 raw items being
        # unmatchable by construction.
        assert leerie.check_task_file_coverage(extracted, subtasks) == []

    def test_all_uncoverable_yields_no_issue(self, leerie):
        extracted = [
            "CLAUDE.md: Run `pnpm run lint:fix` - MUST pass with no errors",
            "CLAUDE.md: Run `pnpm run build` - MUST succeed",
        ]
        subtasks = [{"title": "unrelated", "intent": "",
                     "investigation_notes": ""}]
        assert leerie.check_task_file_coverage(extracted, subtasks) == []

    def test_uncoverable_items_dont_inflate_denominator_for_real_gaps(
            self, leerie):
        # A genuine coverage gap among the coverable items still fires,
        # with the ratio computed over the coverable subset only.
        extracted = [
            "CLAUDE.md: Run `pnpm run lint:fix` - MUST pass with no errors",
            "CLAUDE.md: Real spec item A",
            "CLAUDE.md: Real spec item B",
        ]
        subtasks = [{"title": "unrelated", "intent": "",
                     "investigation_notes": ""}]
        issues = leerie.check_task_file_coverage(extracted, subtasks)
        assert any("LOW_COVERAGE: 2/2" in i for i in issues)

    def test_backtick_without_must_still_coverable(self, leerie):
        # Backticks alone (no MUST) don't make an item uncoverable — only
        # the combination is the uncoverable-by-construction signature.
        extracted = ["spec.md: Run `pnpm test`", "spec.md: B", "spec.md: C"]
        subtasks = [{"title": "unrelated", "intent": "",
                     "investigation_notes": ""}]
        issues = leerie.check_task_file_coverage(extracted, subtasks)
        assert any("LOW_COVERAGE: 3/3" in i for i in issues)


class TestIsUncoverableConventionItem:
    def test_backtick_and_must(self, leerie):
        assert leerie._is_uncoverable_convention_item(
            "Run `pnpm run lint:fix` - MUST pass with no errors") is True

    def test_backtick_only(self, leerie):
        assert leerie._is_uncoverable_convention_item(
            "Run `pnpm test`") is False

    def test_must_only(self, leerie):
        assert leerie._is_uncoverable_convention_item(
            "This MUST pass") is False

    def test_neither(self, leerie):
        assert leerie._is_uncoverable_convention_item(
            "Plain heading") is False

    def test_lowercase_must_not_matched(self, leerie):
        # "MUST" as a rule-imperative marker is case-sensitive on purpose
        # — lowercase "must" appears in ordinary prose too often to be a
        # reliable uncoverable-by-construction signal.
        assert leerie._is_uncoverable_convention_item(
            "Run `pnpm test` - it must pass") is False


# --- _dedup_frozen_coverage_issues --------------------------------------- #

class TestDedupFrozenCoverageIssues:
    def test_first_occurrence_passes_through(self, leerie):
        seen: set[str] = set()
        issues = ["LOW_COVERAGE: 15/15 items from task-referenced files "
                  "not mentioned in plan. Sample: [...]"]
        result = leerie._dedup_frozen_coverage_issues(issues, seen)
        assert result == issues
        assert seen == {"LOW_COVERAGE: 15/15"}

    def test_repeated_ratio_dropped(self, leerie):
        # Simulates the incident: the same 15/15 ratio fires every round.
        seen: set[str] = set()
        issue = ("LOW_COVERAGE: 15/15 items from task-referenced files "
                 "not mentioned in plan. Sample: [...]")
        first = leerie._dedup_frozen_coverage_issues([issue], seen)
        second = leerie._dedup_frozen_coverage_issues([issue], seen)
        third = leerie._dedup_frozen_coverage_issues([issue], seen)
        assert first == [issue]
        assert second == []
        assert third == []

    def test_changed_ratio_still_fires(self, leerie):
        # A ratio that genuinely improves (or worsens) round over round is
        # real signal and must still reach the planner as feedback.
        seen: set[str] = set()
        issue_a = ("LOW_COVERAGE: 15/15 items from task-referenced files "
                   "not mentioned in plan. Sample: [...]")
        issue_b = ("LOW_COVERAGE: 8/15 items from task-referenced files "
                   "not mentioned in plan. Sample: [...]")
        first = leerie._dedup_frozen_coverage_issues([issue_a], seen)
        second = leerie._dedup_frozen_coverage_issues([issue_b], seen)
        assert first == [issue_a]
        assert second == [issue_b]

    def test_empty_input(self, leerie):
        seen: set[str] = set()
        assert leerie._dedup_frozen_coverage_issues([], seen) == []
        assert seen == set()

    def test_mutates_seen_in_place(self, leerie):
        seen: set[str] = set()
        issue = ("LOW_COVERAGE: 4/6 items from task-referenced files "
                 "not mentioned in plan. Sample: [...]")
        leerie._dedup_frozen_coverage_issues([issue], seen)
        assert "LOW_COVERAGE: 4/6" in seen


# --- _format_task_file_structure ---------------------------------------- #

class TestFormatTaskFileStructure:
    def test_format(self, leerie):
        items = ["spec.md: Overview", "spec.md: Details"]
        result = leerie._format_task_file_structure(items)
        assert "mechanically extracted" in result
        assert "Overview" in result
        assert "coverage checklist" in result
