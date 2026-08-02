"""Coupling test: launcher-only verbs must have a guard in the
REWRITTEN_ARGS filter loop so a misplaced verb errors instead of
leaking to the orchestrator's argparse.

The verb dispatch (``case "${1:-}" in``) handles verbs when they appear
as ``$1`` and ``exit``s before REWRITTEN_ARGS runs.  But if a verb
appears in a non-$1 position (e.g. ``leerie <id> --finalize --runtime
fly`` instead of ``leerie finalize <id> --runtime fly``), the verb
dispatch does not match and the token falls through to the
REWRITTEN_ARGS loop.  Without a guard arm, the ``*)`` default forwards
it to the orchestrator, which rejects it with "unrecognized arguments".

The guard arm emits an actionable error and ``exit 1``s.

Dual-purpose verbs that the orchestrator also handles are excluded:
``list`` (falls through to orchestrator on non-fly path), ``status``
(orchestrator uses as ``list`` filter), ``version`` (handled by
argparse version action), ``resume`` (already forwarded with special
``_prev_was_resume`` handling).
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LEERIE_BASH = REPO_ROOT / "leerie"

# Verbs intentionally NOT in the guard — handled by the orchestrator
# too, or forwarded with special logic.
_DUAL_PURPOSE_VERBS: frozenset[str] = frozenset({
    "list",
    "status",
    "version",
    "resume",
})

# Top-level verb dispatch arms. Extracted from the ``case "${1:-}" in``
# block by matching 2-space-indented patterns (the top-level arms) and
# ignoring deeper-indented sub-case arms like those inside ``config)``.
_TOP_LEVEL_ARM_RE = re.compile(r'^  ([\w|*-]+)\)\s*$', re.MULTILINE)


def _extract_verb_dispatch_verbs() -> set[str]:
    """Extract verb names from the top-level verb dispatch case statement.

    Only captures 2-space-indented arms (top-level); skips nested
    sub-case arms (4+ space indent) and wildcard patterns (``*)``)."""
    src = LEERIE_BASH.read_text()
    # Find the second `case "${1:-}" in` — that's the verb dispatch.
    matches = list(re.finditer(r'^case "\$\{1:-\}" in$', src, re.MULTILINE))
    assert len(matches) >= 2, (
        "expected at least two `case \"${1:-}\" in` blocks in the launcher"
    )
    dispatch_start = matches[1].start()
    # Find the matching esac (top-level, no leading whitespace).
    esac_match = re.search(r'^esac$', src[dispatch_start:], re.MULTILINE)
    assert esac_match, "could not find `esac` closing the verb dispatch"
    dispatch_block = src[dispatch_start:dispatch_start + esac_match.end()]

    verbs: set[str] = set()
    for m in _TOP_LEVEL_ARM_RE.finditer(dispatch_block):
        pattern = m.group(1)
        if pattern == "*":
            continue
        for token in pattern.split("|"):
            token = token.strip()
            if token and token != "*":
                verbs.add(token)
    return verbs


def _extract_rewritten_args_guard_verbs() -> set[str]:
    """Extract verb names from the REWRITTEN_ARGS filter's verb guard arm.

    The guard arm is identified by the "is a verb and must be the first
    argument" error message it emits — this is unique to the guard."""
    src = LEERIE_BASH.read_text()
    # Find the error message that uniquely identifies the guard arm.
    marker = "is a verb and must be the first argument"
    marker_pos = src.find(marker)
    assert marker_pos != -1, (
        f"could not find the verb guard arm marker "
        f"({marker!r}) in the launcher"
    )
    # Search backwards from the marker to find the case pattern line.
    preceding = src[:marker_pos]
    all_matches = list(re.finditer(
        r'^\s+((?:[\w-]+\|)*(?:[\w-]+))\)\s*$',
        preceding,
        re.MULTILINE,
    ))
    assert all_matches, (
        "could not find a case pattern before the verb guard marker"
    )
    pattern = all_matches[-1].group(1)
    return {t.strip() for t in pattern.split("|") if t.strip()}


def test_verb_guard_covers_dispatch_verbs() -> None:
    """Every launcher-only verb from the verb dispatch must appear in
    the REWRITTEN_ARGS guard arm.  Omission means a misplaced verb
    silently leaks to the orchestrator."""
    dispatch_verbs = _extract_verb_dispatch_verbs()
    guard_verbs = _extract_rewritten_args_guard_verbs()

    # The guard should cover all dispatch verbs except dual-purpose ones.
    launcher_only = dispatch_verbs - _DUAL_PURPOSE_VERBS
    missing = launcher_only - guard_verbs
    assert not missing, (
        f"verb dispatch declares {sorted(missing)!r} but the "
        f"REWRITTEN_ARGS guard arm does not include them. A misplaced "
        f"verb (e.g. `leerie <task> {sorted(missing)[0]}`) will leak to "
        f"the orchestrator's argparse. Add the verb(s) to the guard arm "
        f"in `leerie` (the combined case pattern between --auto-finalize "
        f"and --resume in the REWRITTEN_ARGS filter)."
    )


def test_verb_guard_has_no_stale_entries() -> None:
    """Every verb in the REWRITTEN_ARGS guard must still exist in the
    verb dispatch.  Stale entries are harmless but confusing."""
    dispatch_verbs = _extract_verb_dispatch_verbs()
    guard_verbs = _extract_rewritten_args_guard_verbs()

    stale = guard_verbs - dispatch_verbs
    assert not stale, (
        f"REWRITTEN_ARGS guard arm includes {sorted(stale)!r} but the "
        f"verb dispatch no longer declares them. Remove from the guard "
        f"arm in `leerie`."
    )
