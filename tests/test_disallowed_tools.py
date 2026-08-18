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
    # `Task` is the LIVE CLI's subagent-spawning tool; `Agent` above is the
    # retired name. Until this entry existed, CLAUDE.md's "No subagent
    # spawning" invariant was enforced only against a name current builds no
    # longer ship. Measured in the preflight smoke test's own then-uncontained
    # surface; contained workers never reported it, so this is
    # defense-in-depth rather than a fix for an observed leak.
    "Task",
    # Inert once --strict-mcp-config leaves zero servers; denied so the
    # surface stays enumerable rather than partly unclassified.
    "ListMcpResourcesTool", "ReadMcpResourceTool", "ReadMcpResourceDirTool",
}

# NotebookEdit is deliberately NOT here — see ACT_TOOLS in leerie.py. It was
# briefly denied and reverted: the deny list is a single global constant, so
# denying a writer strips notebook editing from every acting worker in every
# user repo, while judgment workers (autonomous=False) do not carry
# --dangerously-skip-permissions by default and so are already held by
# --allowedTools. Bash/Write/Edit stay allowed regardless, so the deny never
# produced the read-only property it was justified by.
NOT_DENIED = {"NotebookEdit", "Bash", "Write", "Edit"}


def test_disallowed_tools_contains_required_denials(leerie):
    entries = {e.strip() for e in leerie.DISALLOWED_TOOLS.split(",")}
    missing = REQUIRED_DENIALS - entries
    assert not missing, (
        f"DISALLOWED_TOOLS must deny {missing} — these tools spawn "
        "untracked parallel work or set timers the orchestrator cannot track"
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
    # The two names this fixture was blind to until 2026-08-18: both were in
    # the live surface of every worker while the partition test reported full
    # coverage, because the fixture itself never listed them. Without these
    # rows, removing either from DISALLOWED_TOOLS leaves the suite green.
    assert "Task" in observed, (
        "fixture must include 'Task' — the live CLI's subagent-spawning tool"
    )
    assert "NotebookEdit" in observed, (
        "fixture must include 'NotebookEdit' — observed in every worker's "
        "reported surface"
    )
    # mcp__* names are deliberately absent: the partition is over BARE tool
    # names, and MCP exposure is cut off by --strict-mcp-config plus the
    # latch's own startswith("mcp__") rule. Admitting them here would make
    # this test permanently unsatisfiable and invite "fixing" it by deleting
    # names.
    assert not [t for t in observed if t.startswith("mcp__")], (
        "observed_tools.json must carry bare tool names only"
    )

    disallowed = {e.strip() for e in leerie.DISALLOWED_TOOLS.split(",")}
    covered = leerie.KNOWN_TOOLS | disallowed

    unclassified = sorted(set(observed) - covered)
    assert not unclassified, (
        f"observed tool(s) {unclassified} are in neither leerie.KNOWN_TOOLS "
        "nor leerie.DISALLOWED_TOOLS — a CLI-shipped tool has gone "
        "unclassified"
    )


def test_plain_file_writers_are_not_denied(leerie):
    """A writer on the deny list costs every acting worker in every user repo
    and buys nothing by default — `--allowedTools` already holds judgment
    workers, which are `autonomous=False`. Pinned so `NotebookEdit` is not
    re-added on the reasoning that was tried and reverted."""
    denied = {e.strip() for e in leerie.DISALLOWED_TOOLS.split(",")}
    wrongly_denied = NOT_DENIED & denied
    assert not wrongly_denied, (
        f"{sorted(wrongly_denied)} are plain file writers; denying them "
        "globally removes the capability from implementer/integrator/"
        "conformer against arbitrary user repos. See ACT_TOOLS.")


def test_notebook_edit_is_classified_as_an_act_tool(leerie):
    """Not denied, but still classified — otherwise it drops out of
    KNOWN_TOOLS and the observed-surface partition below reports it as an
    unclassified CLI tool."""
    assert "NotebookEdit" in {e.split("(", 1)[0]
                              for e in leerie.ACT_TOOLS.split(",")}
    assert "NotebookEdit" in leerie.KNOWN_TOOLS
