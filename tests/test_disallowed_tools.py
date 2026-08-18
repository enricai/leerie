"""Coupling tests for DISALLOWED_TOOLS — the hard-deny list passed via
--disallowedTools to every worker.

Unlike --allowedTools (permission-tier, bypassed by
--dangerously-skip-permissions), --disallowedTools removes tools from
the model's context entirely.  This test pins the deny list contents
and confirms the flag is wired into claude_p's command builder.
"""
from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
LEERIE_PY = REPO_ROOT / "orchestrator" / "leerie.py"

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
