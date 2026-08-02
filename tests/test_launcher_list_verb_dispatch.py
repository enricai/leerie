"""Regression test for the bare `list` verb's REWRITTEN_ARGS translation.

Bug (found 2026-08-02, reproduced live against a real container build):
plain `leerie list` — the most common invocation, no flags at all — was
completely broken on the bare-verb-conversion branch. It always died with
"error: 'list' is a verb and must be the first argument."

Root cause: the `list)` case arm's non-exiting fallback paths (plain
`list`, or `list --runtime fly` when flyctl is unavailable/fails) restore
the original argv via `set -- "${_list_rest[@]}"`, which puts the literal
`list` token back at position 1 of `$@`, then fall through past the
top-level verb-dispatch `case` block. Execution continues into the
REWRITTEN_ARGS filter loop (~line 5354), which has a guard arm — shared
by every OTHER launcher-only verb — that rejects any of those verb tokens
wherever they appear in `$@`: "'<verb>' is a verb and must be the first
argument." That guard has no concept of "this is $1 by construction, not
a misplaced verb" for `list`, unlike `resume`, which is special-cased by
POSITION (index 1) before the guard runs and translated into the
`--resume` flag the orchestrator's argparse actually understands.

`list` had no equivalent special case — despite the case arm's own
comment (`leerie:1197`) claiming "the orchestrator's argparse handles
plain `list`," which was never true (the orchestrator only ever accepted
`--list`, a flag, never a bare positional `list`). The bug shipped
undetected in part because `tests/test_launcher_verb_filter.py` had
already carved `list` out of its dispatch-verb-vs-guard-verb parity check
as a documented "dual-purpose" exception, on the same false premise.

Fix: `list` is now special-cased by index (mirroring `resume`) in the
REWRITTEN_ARGS loop, translating bare `list` at position 1 into `--list`.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = REPO_ROOT / "leerie"

# Reproduces the launcher's REWRITTEN_ARGS loop's `resume`/`list` index-1
# special cases in isolation (mirrors test_launcher_seed_depth_resolution.py's
# _LAUNCHER_SEED_BLOCK pattern) — driving the real launcher end-to-end would
# require a full container build; this isolates just the translation logic
# and pins it against the live source via test_block_present_in_launcher.
_REWRITTEN_ARGS_LIST_BLOCK = r"""
REWRITTEN_ARGS=()
_rw_argi=0
for arg in "$@"; do
  _rw_argi=$((_rw_argi + 1))
  if [ "$_rw_argi" -eq 1 ] && [ "$arg" = "resume" ]; then
    REWRITTEN_ARGS+=("--resume")
    continue
  fi
  if [ "$_rw_argi" -eq 1 ] && [ "$arg" = "list" ]; then
    REWRITTEN_ARGS+=("--list")
    continue
  fi
  REWRITTEN_ARGS+=("$arg")
done
"""


def _rewritten_args(*args: str) -> list[str]:
    script = (
        "set -euo pipefail\n"
        f"{_REWRITTEN_ARGS_LIST_BLOCK}\n"
        'printf "%s\\n" "${REWRITTEN_ARGS[@]}"\n'
    )
    result = subprocess.run(
        ["bash", "-c", script, "bash", *args],
        capture_output=True, text=True, env=os.environ,
    )
    assert result.returncode == 0, f"unexpected failure: {result.stderr}"
    return result.stdout.splitlines()


def test_bare_list_translates_to_dash_dash_list():
    """The reported bug, reproduced and pinned: bare `list` at position 1
    must become `--list`, not leak through as the literal verb token."""
    assert _rewritten_args("list") == ["--list"]


def test_bare_list_with_trailing_args_preserves_them():
    """`list --runtime fly` (as restored by the case-arm's flyctl-failure
    fallback) must translate only the verb; trailing args pass through."""
    assert _rewritten_args("list", "--runtime", "fly") == (
        ["--list", "--runtime", "fly"]
    )


def test_list_only_translated_at_position_one():
    """A later positional/value equal to the literal word "list" must
    never be mistaken for the verb (same discipline as `resume`)."""
    assert _rewritten_args("--answers", "list") == ["--answers", "list"]


def test_resume_still_translates_alongside_list_fix():
    """Regression guard: adding the `list` special case must not disturb
    the pre-existing `resume` translation it was modeled on."""
    assert _rewritten_args("resume") == ["--resume"]


def test_block_present_in_launcher():
    """Coupling test: the reproduced block must stay in lockstep with the
    launcher's real REWRITTEN_ARGS loop."""
    src = LAUNCHER.read_text()
    assert (
        'if [ "$_rw_argi" -eq 1 ] && [ "$arg" = "resume" ]; then\n'
        '    REWRITTEN_ARGS+=("--resume")'
    ) in src, "Launcher's resume index-1 special case has drifted."
    assert (
        'if [ "$_rw_argi" -eq 1 ] && [ "$arg" = "list" ]; then\n'
        '    REWRITTEN_ARGS+=("--list")'
    ) in src, (
        "Launcher's list index-1 special case is missing or has drifted — "
        "this is the fix for the bare `leerie list` dispatch bug."
    )
    # The list special case must run BEFORE the general verb-rejection
    # guard arm, or it never gets a chance to fire.
    list_fix_pos = src.index('[ "$arg" = "list" ]')
    guard_pos = src.index("is a verb and must be the first argument")
    assert list_fix_pos < guard_pos, (
        "The list index-1 special case must precede the verb-rejection "
        "guard arm in the REWRITTEN_ARGS loop, or bare `list` still hits "
        "the guard and dies."
    )
