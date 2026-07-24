"""Unit tests for check_prescribed_command_coverage() — the deterministic
PRIMARY layer of the instruction-adherence gate (DESIGN: instruction-
adherence is code-enforced, sibling to §12 "prompts advisory, code
enforces").

Pure JSON→verdict set logic over two structured JSON arrays
(`prescribed_procedure.commands` and each subtask's `runs_commands`) — no
NL parsing anywhere in this file or the function under test. Matching is
normalized (lowercased, stopword-filtered) token-overlap, not exact string
equality, because the planner emits `runs_commands` as a paraphrase of the
prescribed command (B4-validated, e.g. "barnacle recon browser" for the
task's "recon browser").
"""
from __future__ import annotations


def _subtask(sid: str, runs_commands: list[str] | None = None) -> dict:
    return {"id": sid, "runs_commands": runs_commands or []}


def test_incident_shape_fires(leerie):
    """The motivating incident: prescribed=[recon browser, recon generate],
    but no subtask's runs_commands covers either — both must fire."""
    prescribed = {
        "is_prescribed": True,
        "commands": ["recon browser", "recon generate"],
        "forbid_manual": True,
        "evidence": "user said 'your ONLY job is to run recon browser "
                     "then recon generate'",
    }
    subtasks = [
        _subtask("feat-001", ["write contract.ts"]),
        _subtask("feat-002", ["write browser-flow.ts"]),
    ]
    issues = leerie.check_prescribed_command_coverage(prescribed, subtasks)
    assert len(issues) == 2
    assert all(i.startswith("PRESCRIBED_CMD_UNRUN:") for i in issues)
    assert any("recon browser" in i for i in issues)
    assert any("recon generate" in i for i in issues)


def test_goal_only_task_is_silent(leerie):
    """A goal-only task (prescribed=[] / is_prescribed=false) must never
    fire — 0 false positives by construction."""
    assert leerie.check_prescribed_command_coverage(
        {"is_prescribed": False, "commands": []}, []) == []
    assert leerie.check_prescribed_command_coverage(
        {"is_prescribed": True, "commands": []},
        [_subtask("feat-001", ["do something unrelated"])]) == []
    # Absent prescribed_procedure entirely (None) — also silent.
    assert leerie.check_prescribed_command_coverage(None, []) == []
    assert leerie.check_prescribed_command_coverage({}, []) == []


def test_paraphrase_coverage_is_silent(leerie):
    """B4-validated: the planner emits a normalized paraphrase, not the
    literal prescribed string. 'barnacle recon browser' covers 'recon
    browser' via salient token overlap — no NL parsing, pure token-set
    matching."""
    prescribed = {
        "is_prescribed": True,
        "commands": ["recon browser"],
        "forbid_manual": True,
        "evidence": "user prescribed the recon browser step",
    }
    subtasks = [_subtask("feat-001", ["barnacle recon browser"])]
    assert leerie.check_prescribed_command_coverage(prescribed, subtasks) == []


def test_all_commands_covered_is_silent(leerie):
    prescribed = {
        "is_prescribed": True,
        "commands": ["recon browser", "recon generate"],
        "forbid_manual": True,
        "evidence": "explicit two-step procedure",
    }
    subtasks = [
        _subtask("feat-001", ["run recon browser in a loop"]),
        _subtask("feat-002", ["run recon generate"]),
    ]
    assert leerie.check_prescribed_command_coverage(prescribed, subtasks) == []


def test_partial_coverage_fires_only_for_uncovered_command(leerie):
    prescribed = {
        "is_prescribed": True,
        "commands": ["recon browser", "recon generate"],
        "forbid_manual": True,
        "evidence": "explicit two-step procedure",
    }
    subtasks = [_subtask("feat-001", ["run recon browser in a loop"])]
    issues = leerie.check_prescribed_command_coverage(prescribed, subtasks)
    assert len(issues) == 1
    assert "recon generate" in issues[0]
    assert "recon browser" not in issues[0]


def test_no_subtasks_at_all_fires_for_every_prescribed_command(leerie):
    prescribed = {
        "is_prescribed": True,
        "commands": ["foo:build", "foo:generate"],
        "forbid_manual": True,
        "evidence": "explicit two-step procedure",
    }
    issues = leerie.check_prescribed_command_coverage(prescribed, [])
    assert len(issues) == 2


def test_runs_commands_missing_or_empty_on_subtasks_is_tolerated(leerie):
    """A subtask with no runs_commands field (most subtasks) must not
    crash the coverage check — it simply contributes no coverage."""
    prescribed = {
        "is_prescribed": True,
        "commands": ["recon generate"],
        "forbid_manual": True,
        "evidence": "explicit procedure",
    }
    subtasks = [{"id": "feat-001"}, _subtask("feat-002", [])]
    issues = leerie.check_prescribed_command_coverage(prescribed, subtasks)
    assert len(issues) == 1
    assert "recon generate" in issues[0]


def test_non_string_or_blank_commands_are_skipped_not_crashed(leerie):
    prescribed = {
        "is_prescribed": True,
        "commands": ["recon generate", "", "   "],
        "forbid_manual": True,
        "evidence": "explicit procedure",
    }
    issues = leerie.check_prescribed_command_coverage(prescribed, [])
    assert len(issues) == 1
    assert "recon generate" in issues[0]


def test_case_insensitive_matching(leerie):
    prescribed = {
        "is_prescribed": True,
        "commands": ["Recon Generate"],
        "forbid_manual": True,
        "evidence": "explicit procedure",
    }
    subtasks = [_subtask("feat-001", ["RECON GENERATE plugin"])]
    assert leerie.check_prescribed_command_coverage(prescribed, subtasks) == []


def test_unrelated_shared_stopword_does_not_falsely_cover(leerie):
    """A command sharing only a stopword (e.g. 'the') with runs_commands
    must not be considered covered — coverage requires a SALIENT token
    overlap, not any-token overlap."""
    prescribed = {
        "is_prescribed": True,
        "commands": ["recon generate"],
        "forbid_manual": True,
        "evidence": "explicit procedure",
    }
    subtasks = [_subtask("feat-001", ["write the contract for the plugin"])]
    issues = leerie.check_prescribed_command_coverage(prescribed, subtasks)
    assert len(issues) == 1
