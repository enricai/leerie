"""Tool-surface self-reporting: a CLI upgrade that ships a new tool name, or
any mcp__* tool leaking past --strict-mcp-config, must produce a visible
advisory log line rather than silently drifting (DESIGN §12 central
principle applied to the CLI's own tool list).

The latch lives inside `_invoke`'s nested `_read_stream` closure (mirroring
the rate_limit_event latch immediately above it — see the comment there for
why it must live in `_read_stream` rather than `_summarize_stream_event`: it
must fire even at quiet verbosity, where the summarizer is never consulted).
Driving `_read_stream` directly requires a real subprocess, so the wiring is
pinned by source-coupling (mirroring
`test_rejected_payload_logging.py::test_read_stream_latches_and_emits_the_payload`),
and the pure classification logic — "which names in a tools array count as
unexpected" — is pinned behaviorally against `KNOWN_TOOLS` directly.
"""
from __future__ import annotations

import inspect


# ----- pure classification (behavioral, no subprocess needed) ---------------

def _flagged(leerie, tools):
    """Reproduce the exact filter _read_stream applies, so this stays a
    behavioral pin on the *logic* rather than a restatement of it."""
    return sorted(
        name for name in tools
        if name not in leerie.KNOWN_TOOLS or name.startswith("mcp__"))


def test_known_tools_surface_is_never_flagged(leerie):
    known = sorted(leerie.KNOWN_TOOLS)[:5]
    assert _flagged(leerie, known) == []


def test_structured_output_alone_is_never_flagged(leerie):
    assert _flagged(leerie, ["StructuredOutput"]) == []


def test_unknown_tool_name_is_flagged(leerie):
    assert _flagged(leerie, ["Read", "SuperNewTool"]) == ["SuperNewTool"]


def test_mcp_prefixed_tool_is_flagged_even_if_hypothetically_known(leerie):
    # mcp__* must be flagged regardless of KNOWN_TOOLS membership — it is a
    # distinct signal (a stale --strict-mcp-config leak), not merely an
    # unrecognized name.
    assert _flagged(leerie, ["mcp__something__tool"]) == [
        "mcp__something__tool"]


def test_empty_tools_array_flags_nothing(leerie):
    assert _flagged(leerie, []) == []


# ----- _bare_tool_names parsing (direct, not via KNOWN_TOOLS) ---------------

def test_bare_tool_names_plain_comma_separated_list(leerie):
    assert leerie._bare_tool_names("Read,Write,Bash") == {"Read", "Write", "Bash"}


def test_bare_tool_names_strips_allow_pattern_suffix(leerie):
    assert leerie._bare_tool_names("Bash(git:*)") == {"Bash"}
    assert leerie._bare_tool_names("Read,Bash(git:*),Write") == {
        "Read", "Bash", "Write"}


def test_bare_tool_names_single_entry_no_comma(leerie):
    assert leerie._bare_tool_names("Read") == {"Read"}


def test_bare_tool_names_empty_string(leerie):
    assert leerie._bare_tool_names("") == {""}


# ----- wiring (source-coupling, mirrors test_rejected_payload_logging.py) ---

def test_read_stream_latches_the_tool_surface(leerie):
    src = inspect.getsource(leerie._invoke)
    assert "unexpected_tools_warned" in src
    assert 'sub == "init"' in src
    assert "KNOWN_TOOLS" in src
    assert 'startswith("mcp__")' in src


def test_declared_nonlocal_so_the_latch_survives_events(leerie):
    """Without `nonlocal`, the flag would rebind to a fresh local per call
    and the warning could re-fire on every subsequent system/init event."""
    src = inspect.getsource(leerie._invoke)
    nl = [ln for ln in src.splitlines()
          if ln.strip().startswith("nonlocal")
          and "unexpected_tools_warned" in ln]
    assert nl, "unexpected_tools_warned must be declared nonlocal in _read_stream"


def test_latch_is_gated_on_the_flag_so_it_fires_at_most_once(leerie):
    src = inspect.getsource(leerie._invoke)
    idx = src.index('sub == "init"')
    # the enclosing `if` must consult the flag before proceeding
    window = src[idx:idx + 100]
    assert "not unexpected_tools_warned" in window


def test_warning_is_advisory_only_no_raise(leerie):
    """The block around the warning must contain no `raise` — a drifted
    tool surface must never turn into a killed worker."""
    src = inspect.getsource(leerie._invoke)
    idx = src.index('sub == "init"')
    # bound the scan to this if-block, not the whole function
    end = src.index("# PID-exhaustion detection", idx)
    block = src[idx:end]
    assert "raise" not in block
