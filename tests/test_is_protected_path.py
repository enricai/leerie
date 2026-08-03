"""Tests for _is_protected_path() — the rule that gates which paths an
implementer may write to.

DESIGN §9: `.leerie/` and `.git/` are coordination-only. Inside
`.claude/`, the three documented user-deliverable subtrees (`agents/`,
`commands/`, `skills/`) are exempt because leerie's own self-healing
skill instructs consumers to write subagent files at
`.claude/agents/<name>.md`. Top-level `.claude/` files (`settings.json`,
`settings.local.json`) stay protected — they are coordination/config,
not deliverable customizations.
"""
from __future__ import annotations


def test_leerie_path_is_protected(leerie):
    assert leerie._is_protected_path(".leerie/state.json")
    assert leerie._is_protected_path(".leerie/runs/feat-x-abc/state.json")


def test_git_path_is_protected(leerie):
    assert leerie._is_protected_path(".git/HEAD")
    assert leerie._is_protected_path(".git/refs/heads/main")


def test_claude_settings_json_is_protected(leerie):
    assert leerie._is_protected_path(".claude/settings.json")
    assert leerie._is_protected_path(".claude/settings.local.json")


def test_claude_top_level_files_are_protected(leerie):
    # Any unexpected top-level file under .claude/ stays protected by
    # default — the carve-out is for the three documented subtrees, not
    # for arbitrary new files.
    assert leerie._is_protected_path(".claude/some-new-config.json")
    assert leerie._is_protected_path(".claude/cache.db")


def test_claude_agents_is_a_deliverable(leerie):
    # The barnacle failure case: a subagent at .claude/agents/ is the
    # documented Claude Code location. Must be writable.
    assert not leerie._is_protected_path(
        ".claude/agents/recon-flow-patch-generator.md")
    assert not leerie._is_protected_path(".claude/agents/my-helper.md")


def test_claude_commands_is_a_deliverable(leerie):
    assert not leerie._is_protected_path(".claude/commands/my-command.md")
    assert not leerie._is_protected_path(
        ".claude/commands/sub/nested-cmd.md")


def test_claude_skills_is_a_deliverable(leerie):
    assert not leerie._is_protected_path(
        ".claude/skills/my-skill/SKILL.md")
    assert not leerie._is_protected_path(
        ".claude/skills/llm-self-heal/SKILL.md")


def test_normal_source_path_is_unprotected(leerie):
    assert not leerie._is_protected_path("src/main.py")
    assert not leerie._is_protected_path("docs/DESIGN.md")
    assert not leerie._is_protected_path("CLAUDE.md")
    assert not leerie._is_protected_path("leerie.toml")


def test_exact_prefix_required_not_substring(leerie):
    # `_is_protected_path` matches on prefix, not substring. A source file
    # that happens to contain ".claude" in its name (not at the start)
    # is unrelated.
    assert not leerie._is_protected_path("src/dot-claude-helper.py")
    assert not leerie._is_protected_path("docs/about-.leerie.md")


def test_claude_root_path_is_protected(leerie):
    # The bare ".claude/" prefix without a subtree (which shouldn't occur
    # as a real path but is structurally possible) stays protected.
    assert leerie._is_protected_path(".claude/")
