"""Coupling test for --strict-mcp-config — cuts MCP tool exposure off at
the source, unconditionally for every worker, independent of and prior to
any mcp__* denylist enumeration (bugfix-001 / feat-001, merged).

Measured: with the flag, mcp_servers becomes [] and MCP tool count drops
from 46 to 0 (CLI 2.1.234), regardless of what .claude.json seeding copies
into the container.
"""
from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
LEERIE_PY = REPO_ROOT / "orchestrator" / "leerie.py"


def _claude_p_body() -> str:
    src = LEERIE_PY.read_text()
    start = src.index("async def claude_p(")
    end = src.index("\nasync def ", start + 1)
    return src[start:end]


def test_strict_mcp_config_wired_into_claude_p():
    """The claude_p command builder must pass --strict-mcp-config
    unconditionally — not gated behind skip_perms/effort/any other
    per-worker condition."""
    body = _claude_p_body()
    assert '"--strict-mcp-config"' in body, (
        "claude_p must pass --strict-mcp-config to the CLI"
    )


def test_no_mcp_config_flag_ever_passed():
    """--strict-mcp-config with a stray --mcp-config would strand a
    worker with server config it can't use as strictly as intended —
    claude_p must never pass --mcp-config."""
    body = _claude_p_body()
    assert '"--mcp-config"' not in body, (
        "claude_p must never pass --mcp-config alongside --strict-mcp-config"
    )
