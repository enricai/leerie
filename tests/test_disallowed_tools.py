"""Coupling tests for DISALLOWED_TOOLS — the hard-deny list passed via
--disallowedTools to every worker.

Unlike --allowedTools (permission-tier, bypassed by
--dangerously-skip-permissions), --disallowedTools removes tools from
the model's context entirely.  This test pins the deny list contents
and confirms the flag is wired into claude_p's command builder.
"""
from __future__ import annotations

import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
LEERIE_PY = REPO_ROOT / "orchestrator" / "leerie.py"
OBSERVED_TOOLS_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "observed_tools.json"

REQUIRED_DENIALS = {
    "Agent", "SendMessage",
    "ScheduleWakeup",
    "CronCreate", "CronDelete", "CronList",
    "RemoteTrigger", "PushNotification",
    "Workflow", "ReportFindings", "Skill", "Monitor",
    "TaskCreate", "TaskGet", "TaskList", "TaskUpdate", "TaskOutput", "TaskStop",
    "ListAgents", "EnterWorktree", "ExitWorktree", "DesignSync",
    # ToolSearch is a judgment call, not strictly required like the rest —
    # denied here for consistency with the other autonomy/spawn-shaped tools.
    "ToolSearch",
}


def test_disallowed_tools_contains_required_denials(leerie):
    entries = {e.strip() for e in leerie.DISALLOWED_TOOLS.split(",")}
    missing = REQUIRED_DENIALS - entries
    assert not missing, (
        f"DISALLOWED_TOOLS must deny {missing} — these tools spawn "
        "untracked parallel work or set timers the orchestrator cannot track"
    )


def test_disallowed_tools_wired_into_claude_p():
    """The claude_p command builder must pass --disallowedTools."""
    src = LEERIE_PY.read_text()
    start = src.index("async def claude_p(")
    end = src.index("\nasync def ", start + 1)
    body = src[start:end]
    assert '"--disallowedTools"' in body, (
        "claude_p must pass --disallowedTools to the CLI"
    )
    assert "DISALLOWED_TOOLS" in body, (
        "claude_p must reference the DISALLOWED_TOOLS constant"
    )


def test_observed_tools_fixture_is_fully_partitioned(leerie):
    """The converse of REQUIRED_DENIALS: rather than pinning that specific
    names remain denied, assert every tool name leerie has ever observed
    in the wild is covered by KNOWN_TOOLS | DISALLOWED_TOOLS. A CLI-shipped
    tool that lands in neither set must fail this test instead of passing
    silently — the gap REQUIRED_DENIALS alone cannot catch, since it only
    fails on a name's *removal*, never on a new name going unclassified.
    """
    observed = json.loads(OBSERVED_TOOLS_FIXTURE.read_text())

    # Anti-vacuity: an emptied fixture must not vacuously pass.
    assert observed, "observed_tools.json fixture must not be empty"
    assert "Agent" in observed, (
        "fixture must include 'Agent' (a known-denied name) as a sanity check"
    )
    assert "Read" in observed, (
        "fixture must include 'Read' (a known-allowed name) as a sanity check"
    )

    disallowed = {e.strip() for e in leerie.DISALLOWED_TOOLS.split(",")}
    covered = leerie.KNOWN_TOOLS | disallowed

    unclassified = sorted(set(observed) - covered)
    assert not unclassified, (
        f"observed tool(s) {unclassified} are in neither leerie.KNOWN_TOOLS "
        "nor leerie.DISALLOWED_TOOLS — a CLI-shipped tool has gone "
        "unclassified"
    )
