"""Coupling test: the launcher's `_value_flags` list must include every
value-taking flag declared in the orchestrator's argparse.

The launcher's task-extraction loop (added by the Round 6 bootstrap-
resume task-recovery work) walks `$@` looking for the orchestrator's
single positional `task`. To identify it, it must skip the value of any
`--flag` that takes one. The list lives inline in the bash launcher
because it runs on the host before any Python is loaded.

If a value-taking flag is added to the orchestrator's argparse and not
mirrored into the launcher's list, the task-extractor will misclassify
that flag's value as the positional `task` — and persist the wrong
string to `.leerie/runs/<bootstrap>/task.txt` on first launch. The
mistake silently surfaces on the next bootstrap-stage resume as a wrong
or shifted task argument.

Drift is mechanical and catchable here. This test fails loudly the
moment the two layers diverge.

Per-worker `--model-<W>` and `--effort-<W>` flags are handled by the
launcher via a prefix match (`--model-*` / `--effort-*`) rather than
enumeration, so this test does NOT require those specific names in
`_value_flags` — only the prefix patterns.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LEERIE_PY = REPO_ROOT / "orchestrator" / "leerie.py"
LEERIE_BASH = REPO_ROOT / "leerie"


# --- launcher side: extract _value_flags from the bash source ----------

def _extract_launcher_value_flags() -> set[str]:
    """Parse the literal `_value_flags=" --foo --bar ..."` assignment
    in the launcher and return the set of flag names. Includes the
    backslash-newline line continuations bash collapses at parse time.
    """
    src = LEERIE_BASH.read_text()
    # Match `_value_flags=" ... "` allowing literal newlines (bash collapses
    # \-newline inside double quotes, but the source still spans multiple
    # lines for readability). Use re.DOTALL so . matches newlines.
    m = re.search(r'_value_flags="\s*([^"]+?)"', src, flags=re.DOTALL)
    assert m, ("could not find `_value_flags=\"...\"` assignment in "
               f"{LEERIE_BASH}; the task-extraction block was moved or "
               "renamed — update this test or restore the marker")
    # Collapse \-newline continuations and whitespace; split on spaces.
    raw = m.group(1).replace("\\\n", " ")
    return {w for w in raw.split() if w.startswith("--")}


# --- orchestrator side: extract value-taking flags from argparse -------

# argparse "actions" that mean the flag does NOT take a value. Anything
# else (default `store`, `append`, etc.) DOES take a value.
_BOOLEAN_ACTIONS = frozenset({
    "store_true", "store_false", "count", "help", "version",
})


def _extract_orchestrator_value_flags() -> set[str]:
    """Parse `orchestrator/leerie.py` for every
    `ap.add_argument("--flag", ...)` (or `_grp.add_argument(...)`) and
    return the set of flag names whose declared `action=` is NOT in
    `_BOOLEAN_ACTIONS`. Skips per-worker flags built dynamically inside
    a for-loop (those are handled in the launcher via the
    `--model-*`/`--effort-*` prefix patterns, not by enumeration).
    """
    tree = ast.parse(LEERIE_PY.read_text())
    value_flags: set[str] = set()

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        # Look for any `<something>.add_argument(...)` call.
        if not (isinstance(node.func, ast.Attribute)
                and node.func.attr == "add_argument"):
            continue
        # First positional must be a string literal starting with `--`.
        # Skip dynamic f-string flags like `f"--model-{_w}"` — those are
        # handled by the launcher's prefix patterns.
        if not node.args:
            continue
        first = node.args[0]
        if not (isinstance(first, ast.Constant)
                and isinstance(first.value, str)
                and first.value.startswith("--")):
            continue
        flag = first.value
        # Inspect `action=` kwarg, if any.
        action = None
        for kw in node.keywords:
            if kw.arg == "action" and isinstance(kw.value, ast.Constant):
                action = kw.value.value
                break
        if action in _BOOLEAN_ACTIONS:
            continue
        value_flags.add(flag)
    return value_flags


# --- the coupling assertions ------------------------------------------

# Launcher-only flags consumed by the bash launcher BEFORE REWRITTEN_ARGS
# is built — these never reach the orchestrator's argparse, so they are
# not in the orchestrator's value-taker set, but the launcher needs to
# know about them so its task-extractor doesn't misclassify their values.
# All are boolean today (no value); listed here for completeness so a
# future value-taking launcher-only flag can be added explicitly.
_LAUNCHER_ONLY_BOOLEAN_FLAGS: frozenset[str] = frozenset({
    "--no-runtime-install",
    "--no-auto-publish",
    "--local-build",
    "--no-re-seed",
    "--force",
    # --chain-id is value-taking but launcher-only: the wave sequencer
    # passes it through to each per-job ./leerie invocation, which
    # records it via update_run_json after host_finalize. The
    # orchestrator never reads it.
    "--chain-id",
})


def test_launcher_value_flags_covers_orchestrator_argparse() -> None:
    """Every value-taking flag declared in the orchestrator's argparse
    must appear in the launcher's `_value_flags` list (or be covered by
    a prefix pattern). Drift means the launcher's task-extractor will
    misclassify the flag's value as the task positional and persist the
    wrong string to `.leerie/runs/<bootstrap>/task.txt`.

    Per-worker `--model-<W>` / `--effort-<W>` are handled by the
    `--model-*`/`--effort-*` case-pattern in the launcher loop, not by
    enumeration — they are excluded from this assertion via the same
    prefix check below."""
    launcher_flags = _extract_launcher_value_flags()
    orchestrator_flags = _extract_orchestrator_value_flags()

    # Drop per-worker flags from the comparison; they're handled by the
    # launcher's prefix-pattern arm rather than its enumerated list.
    enumerated_orch = {
        f for f in orchestrator_flags
        if not (f.startswith("--model-") or f.startswith("--effort-"))
    }

    missing = enumerated_orch - launcher_flags
    assert not missing, (
        f"orchestrator's argparse declares value-taking flags "
        f"{sorted(missing)!r} that are not in the launcher's "
        f"`_value_flags` list. The launcher's task-extractor (in "
        f"`leerie`, the `--- extract the task positional` block) will "
        f"misclassify these flags' VALUES as the positional `task` and "
        f"persist the wrong string to "
        f"`.leerie/runs/<bootstrap>/task.txt`. Add the flag(s) to the "
        f"`_value_flags` literal in `leerie`."
    )


def test_launcher_value_flags_has_no_stale_entries() -> None:
    """The other direction: every flag in the launcher's `_value_flags`
    list must still exist in the orchestrator (or be a documented
    launcher-only flag). Drift here means a flag was removed from the
    orchestrator but the launcher still treats its old name as
    value-taking — harmless for orchestrator argv but confusing if
    someone reads the launcher and looks for the flag upstream."""
    launcher_flags = _extract_launcher_value_flags()
    orchestrator_flags = _extract_orchestrator_value_flags()

    stale = launcher_flags - orchestrator_flags - _LAUNCHER_ONLY_BOOLEAN_FLAGS
    assert not stale, (
        f"launcher's `_value_flags` lists {sorted(stale)!r}, but the "
        f"orchestrator no longer declares them (or they were never "
        f"there). Remove from `_value_flags` in `leerie`, or — if the "
        f"flag is launcher-only and value-taking — extend this test's "
        f"`_LAUNCHER_ONLY_BOOLEAN_FLAGS` allowlist with an explanation."
    )


def test_per_worker_prefix_patterns_present() -> None:
    """The launcher must include the `--model-*` / `--effort-*` prefix
    patterns in its value-skip block so per-worker overrides are
    recognized. If someone refactors the launcher and drops these, the
    task-extractor will treat the worker name as the task."""
    src = LEERIE_BASH.read_text()
    assert "--model-*" in src and "--effort-*" in src, (
        "launcher must include `--model-*` and `--effort-*` patterns "
        "in the task-extractor's value-skip case so per-worker flags "
        "like `--model-implementer opus` don't trip extraction"
    )
