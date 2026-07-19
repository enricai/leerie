"""Tests for the log-command extraction + _DEPCAP_* byte-budget gate.

Covers _extract_depcap_commands specifically: the deterministic function
that assembles the dep_capture worker's command slice by pulling every Bash
tool_use block from the run's JSONL logs, deduplicating (newest-first), and
stopping admission once _DEPCAP_TOTAL_BUDGET bytes are consumed. Non-Bash
blocks and malformed lines are silently skipped.

Distinct from test_capture_deps.py, which tests the full capture_repo_deps
integration. This file focuses entirely on the extraction+budget unit.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# JSONL fixture helpers
# ---------------------------------------------------------------------------

def _write_log(log_dir: Path, commands: list[str], fname: str) -> None:
    """Write a synthetic JSONL log in the _iter_log_tool_use shape."""
    log_dir.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    for i, cmd in enumerate(commands):
        event = {
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "name": "Bash",
                        "id": f"tid-{i}",
                        "input": {"command": cmd},
                    }
                ]
            }
        }
        lines.append(json.dumps(event))
    (log_dir / fname).write_text("\n".join(lines) + "\n")


def _write_non_bash_log(log_dir: Path, fname: str) -> None:
    """Write a JSONL log containing only non-Bash tool_use blocks."""
    log_dir.mkdir(parents=True, exist_ok=True)
    event = {
        "message": {
            "content": [
                {
                    "type": "tool_use",
                    "name": "Read",
                    "id": "read-1",
                    "input": {"file_path": "/should/not/appear"},
                }
            ]
        }
    }
    (log_dir / fname).write_text(json.dumps(event) + "\n")


# ---------------------------------------------------------------------------
# Basic extraction
# ---------------------------------------------------------------------------

class TestExtractDepcapCommandsBasic:
    """Core extraction: Bash commands parsed from JSONL logs."""

    def test_single_command_extracted(self, leerie, tmp_path):
        log_dir = tmp_path / "logs"
        _write_log(log_dir, ["pip install -r requirements.txt"], "w-001.log")
        text, hit_ceiling = leerie._extract_depcap_commands(log_dir)
        assert "pip install -r requirements.txt" in text
        assert not hit_ceiling

    def test_multiple_commands_all_extracted(self, leerie, tmp_path):
        log_dir = tmp_path / "logs"
        cmds = [
            "sudo apt-get install -y postgresql",
            "pip install -r requirements.txt",
            "npm install",
        ]
        _write_log(log_dir, cmds, "w-001.log")
        text, hit_ceiling = leerie._extract_depcap_commands(log_dir)
        for cmd in cmds:
            assert cmd in text
        assert not hit_ceiling

    def test_return_type_is_tuple_str_bool(self, leerie, tmp_path):
        log_dir = tmp_path / "logs"
        _write_log(log_dir, ["echo hello"], "w-001.log")
        result = leerie._extract_depcap_commands(log_dir)
        assert isinstance(result, tuple) and len(result) == 2
        text, hit_ceiling = result
        assert isinstance(text, str)
        assert isinstance(hit_ceiling, bool)

    def test_empty_log_dir_returns_empty_string(self, leerie, tmp_path):
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        text, hit_ceiling = leerie._extract_depcap_commands(log_dir)
        assert text == ""
        assert not hit_ceiling

    def test_missing_log_dir_returns_empty_string(self, leerie, tmp_path):
        log_dir = tmp_path / "nonexistent-logs"
        text, hit_ceiling = leerie._extract_depcap_commands(log_dir)
        assert text == ""
        assert not hit_ceiling


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

class TestExtractDepcapCommandsDedup:
    """Deduplication: identical commands appear at most once in the output."""

    def test_duplicate_commands_within_one_file(self, leerie, tmp_path):
        log_dir = tmp_path / "logs"
        cmd = "apt-get install -y curl"
        _write_log(log_dir, [cmd, cmd], "w-001.log")
        text, _ = leerie._extract_depcap_commands(log_dir)
        assert text.count(cmd) == 1

    def test_duplicate_commands_across_multiple_files(self, leerie, tmp_path):
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        cmd = "pip install -r requirements.txt"
        for i in range(3):
            _write_log(log_dir, [cmd], f"worker-00{i}.log")
        text, _ = leerie._extract_depcap_commands(log_dir)
        assert text.count(cmd) == 1

    def test_distinct_commands_both_appear(self, leerie, tmp_path):
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        _write_log(log_dir, ["apt-get install -y git"], "w-001.log")
        _write_log(log_dir, ["pip install -r requirements.txt"], "w-002.log")
        text, _ = leerie._extract_depcap_commands(log_dir)
        assert "apt-get install -y git" in text
        assert "pip install -r requirements.txt" in text


# ---------------------------------------------------------------------------
# Non-Bash blocks and malformed lines are ignored
# ---------------------------------------------------------------------------

class TestExtractDepcapCommandsFiltering:
    """Filtering: only Bash tool_use blocks contribute to output."""

    def test_read_tool_use_ignored(self, leerie, tmp_path):
        log_dir = tmp_path / "logs"
        _write_non_bash_log(log_dir, "w-001.log")
        text, _ = leerie._extract_depcap_commands(log_dir)
        assert "/should/not/appear" not in text
        assert text == ""

    def test_mixed_bash_and_non_bash(self, leerie, tmp_path):
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        lines: list[str] = []
        # Bash block
        lines.append(json.dumps({
            "message": {
                "content": [{
                    "type": "tool_use",
                    "name": "Bash",
                    "id": "b-1",
                    "input": {"command": "pip install flask"},
                }]
            }
        }))
        # Read block — should be filtered
        lines.append(json.dumps({
            "message": {
                "content": [{
                    "type": "tool_use",
                    "name": "Read",
                    "id": "r-1",
                    "input": {"file_path": "/some/file"},
                }]
            }
        }))
        (log_dir / "w-001.log").write_text("\n".join(lines) + "\n")
        text, _ = leerie._extract_depcap_commands(log_dir)
        assert "pip install flask" in text
        assert "/some/file" not in text

    def test_malformed_json_line_skipped(self, leerie, tmp_path):
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        valid = json.dumps({
            "message": {
                "content": [{
                    "type": "tool_use",
                    "name": "Bash",
                    "id": "ok-1",
                    "input": {"command": "pip install valid-pkg"},
                }]
            }
        })
        (log_dir / "w-001.log").write_text("THIS IS NOT JSON\n" + valid + "\n")
        text, _ = leerie._extract_depcap_commands(log_dir)
        assert "pip install valid-pkg" in text

    def test_empty_command_string_excluded(self, leerie, tmp_path):
        """A Bash block with an empty command string contributes nothing."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        event = json.dumps({
            "message": {
                "content": [{
                    "type": "tool_use",
                    "name": "Bash",
                    "id": "empty-1",
                    "input": {"command": ""},
                }]
            }
        })
        (log_dir / "w-001.log").write_text(event + "\n")
        text, _ = leerie._extract_depcap_commands(log_dir)
        assert text == ""

    def test_non_dict_message_skipped(self, leerie, tmp_path):
        """A line where 'message' is a string (not a dict) is skipped silently."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        (log_dir / "w-001.log").write_text(
            json.dumps({"message": "not a dict"}) + "\n"
        )
        text, _ = leerie._extract_depcap_commands(log_dir)
        assert text == ""


# ---------------------------------------------------------------------------
# Byte-budget gate (_DEPCAP_TOTAL_BUDGET)
# ---------------------------------------------------------------------------

class TestExtractDepcapCommandsBudget:
    """Budget gate: commands exceeding _DEPCAP_TOTAL_BUDGET are truncated."""

    def test_ceiling_not_hit_when_commands_fit(self, leerie, tmp_path):
        log_dir = tmp_path / "logs"
        _write_log(log_dir, ["apt-get install -y curl"], "w-001.log")
        _, hit_ceiling = leerie._extract_depcap_commands(log_dir)
        assert not hit_ceiling

    def test_ceiling_hit_when_budget_exceeded(self, leerie, tmp_path):
        """Under a small budget, hit_ceiling=True and output is truncated."""
        log_dir = tmp_path / "logs"
        # Install-shaped commands (the only kind the filter keeps), each distinct
        # so dedup does not collapse them and their bytes sum past the budget.
        pad = "x" * 1000
        cmds = [f"pip install pkg{i}-{pad}" for i in range(100)]
        _write_log(log_dir, cmds, "w-001.log")
        orig = leerie._DEPCAP_TOTAL_BUDGET
        try:
            leerie._DEPCAP_TOTAL_BUDGET = 1500
            text, hit_ceiling = leerie._extract_depcap_commands(log_dir)
            assert hit_ceiling
            # Not all 100 commands can fit in 1500 bytes
            for i in range(100):
                if f"pip install pkg{i}-{pad}" not in text:
                    break
            else:
                pytest.fail("Expected some commands to be truncated")
        finally:
            leerie._DEPCAP_TOTAL_BUDGET = orig

    def test_generous_budget_returns_all_commands(self, leerie, tmp_path):
        """With a generous budget, all commands are admitted and hit_ceiling=False."""
        log_dir = tmp_path / "logs"
        cmds = [f"apt-get install -y pkg{i}" for i in range(20)]
        _write_log(log_dir, cmds, "w-001.log")
        orig = leerie._DEPCAP_TOTAL_BUDGET
        try:
            leerie._DEPCAP_TOTAL_BUDGET = 10_000_000  # 10 MB
            text, hit_ceiling = leerie._extract_depcap_commands(log_dir)
            for cmd in cmds:
                assert cmd in text
            assert not hit_ceiling
        finally:
            leerie._DEPCAP_TOTAL_BUDGET = orig

    def test_budget_exactly_one_byte_short_triggers_ceiling(self, leerie, tmp_path):
        """Budget set to one byte less than needed for two commands triggers ceiling."""
        log_dir = tmp_path / "logs"
        cmd_a = "pip install flask"
        cmd_b = "pip install django"
        _write_log(log_dir, [cmd_a, cmd_b], "w-001.log")
        # Calculate exact bytes for cmd_a including separator
        cost_a = len((cmd_a + "\n---\n").encode())
        cost_b = len((cmd_b + "\n---\n").encode())
        # Budget that fits cmd_a but not cmd_b
        tight_budget = cost_a + cost_b - 1
        orig = leerie._DEPCAP_TOTAL_BUDGET
        try:
            leerie._DEPCAP_TOTAL_BUDGET = tight_budget
            text, hit_ceiling = leerie._extract_depcap_commands(log_dir)
            assert hit_ceiling
            assert cmd_a in text
            assert cmd_b not in text
        finally:
            leerie._DEPCAP_TOTAL_BUDGET = orig

    def test_budget_exactly_fits_all_commands_no_ceiling(self, leerie, tmp_path):
        """Budget set to exactly the sum of all commands passes with hit_ceiling=False."""
        log_dir = tmp_path / "logs"
        cmds = ["pip install flask", "npm install"]
        _write_log(log_dir, cmds, "w-001.log")
        # Compute the exact required bytes for all commands
        total_needed = sum(len((c + "\n---\n").encode()) for c in cmds)
        orig = leerie._DEPCAP_TOTAL_BUDGET
        try:
            leerie._DEPCAP_TOTAL_BUDGET = total_needed
            text, hit_ceiling = leerie._extract_depcap_commands(log_dir)
            assert not hit_ceiling
            for cmd in cmds:
                assert cmd in text
        finally:
            leerie._DEPCAP_TOTAL_BUDGET = orig

    def test_zero_budget_triggers_ceiling_immediately(self, leerie, tmp_path):
        """A zero budget means even a single command triggers hit_ceiling=True."""
        log_dir = tmp_path / "logs"
        _write_log(log_dir, ["apt-get install -y curl"], "w-001.log")
        orig = leerie._DEPCAP_TOTAL_BUDGET
        try:
            leerie._DEPCAP_TOTAL_BUDGET = 0
            text, hit_ceiling = leerie._extract_depcap_commands(log_dir)
            assert hit_ceiling
            assert text == ""
        finally:
            leerie._DEPCAP_TOTAL_BUDGET = orig

    def test_budget_applied_after_dedup(self, leerie, tmp_path):
        """Dedup runs before budget; duplicate commands count only once."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        cmd = "pip install -r requirements.txt"
        # Write the same command 10 times across two files
        _write_log(log_dir, [cmd] * 5, "w-001.log")
        _write_log(log_dir, [cmd] * 5, "w-002.log")
        # Budget large enough for one copy but not for ten
        cost = len((cmd + "\n---\n").encode())
        orig = leerie._DEPCAP_TOTAL_BUDGET
        try:
            leerie._DEPCAP_TOTAL_BUDGET = cost  # exactly enough for one copy
            text, hit_ceiling = leerie._extract_depcap_commands(log_dir)
            # After dedup there is only one copy; it should fit exactly
            assert not hit_ceiling
            assert cmd in text
        finally:
            leerie._DEPCAP_TOTAL_BUDGET = orig


# ---------------------------------------------------------------------------
# Newest-first ordering
# ---------------------------------------------------------------------------

class TestExtractDepcapCommandsOrdering:
    """Newest-first: log files are iterated in reverse-name order."""

    def test_files_processed_newest_first(self, leerie, tmp_path):
        """With a tight budget, the newest file's commands are preferred."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        # "newer" file (lexicographically last) has a distinct command
        _write_log(log_dir, ["apt-get install -y git"], "w-001.log")
        _write_log(log_dir, ["apt-get install -y curl"], "w-002.log")
        cmd_001 = "apt-get install -y git"
        cmd_002 = "apt-get install -y curl"
        # Budget for only one command
        cost_002 = len((cmd_002 + "\n---\n").encode())
        orig = leerie._DEPCAP_TOTAL_BUDGET
        try:
            leerie._DEPCAP_TOTAL_BUDGET = cost_002
            text, hit_ceiling = leerie._extract_depcap_commands(log_dir)
            # w-002.log is processed first (lexicographically newest)
            assert cmd_002 in text
            assert cmd_001 not in text
            assert hit_ceiling
        finally:
            leerie._DEPCAP_TOTAL_BUDGET = orig


# ---------------------------------------------------------------------------
# Guard-value-that-cannot-guard regression (bugfix-004)
# ---------------------------------------------------------------------------

def test_depcap_budgets_not_argv_bound_by_source(leerie):
    """dep_capture's user_prompt (manifests_text + commands_text, bounded by
    _DEPCAP_MANIFEST_TOTAL_BUDGET + _DEPCAP_TOTAL_BUDGET) reaches `claude -p`
    via claude_p's stdin transport (bugfix-001), not argv — so the combined
    ~430 KB budget is a token/context sanity check, not a guard against
    Linux's 131,071-byte MAX_ARG_STRLEN. Pin the comment no longer claims an
    argv-size guarantee it cannot make, via source inspection (mirrors
    test_dep_capture_wiring.py's inspect.getsource discipline)."""
    import inspect

    src = inspect.getsource(leerie)
    depcap_idx = src.index("_DEPCAP_TOTAL_BUDGET = 307200")
    comment_block = src[max(0, depcap_idx - 700):depcap_idx]
    assert "stdin" in comment_block, (
        "the _DEPCAP_TOTAL_BUDGET comment must state the payload travels "
        "over stdin, not argv, so a reader does not assume it defends an "
        "argv ceiling")
    assert "MAX_ARG_STRLEN" in comment_block


def test_depcap_total_budget_value_unchanged_since_incident(leerie):
    """Regression pin for the incident's specific claim: _DEPCAP_TOTAL_BUDGET
    (300 KB) is 2.34x the real 131,072-byte MAX_ARG_STRLEN ceiling. Since the
    payload is stdin-transported (not argv-bound), the fix is documentation,
    not a value shrink — this pins the value is unchanged and the module
    exposes MAX_ARG_STRLEN nowhere as a guard on this constant."""
    assert leerie._DEPCAP_TOTAL_BUDGET == 307200
    assert leerie._DEPCAP_MANIFEST_TOTAL_BUDGET == 131072
    # Combined corpus is well over the per-argument argv ceiling — provably
    # fine only because it never lands on argv.
    combined = leerie._DEPCAP_TOTAL_BUDGET + leerie._DEPCAP_MANIFEST_TOTAL_BUDGET
    assert combined > 131_071, (
        "if this ever drops back under MAX_ARG_STRLEN, double-check whether "
        "the comment's stdin-transport rationale is still accurate")
