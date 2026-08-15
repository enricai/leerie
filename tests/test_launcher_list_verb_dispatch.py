"""Regression test for the bare `list`/`resume` verbs reaching the
orchestrator correctly at position 1 of `$@`.

Bug history (found 2026-08-02, reproduced live against a real container
build): plain `leerie list` — the most common invocation, no flags at
all — was completely broken on the bare-verb-conversion branch. It
always died with "error: 'list' is a verb and must be the first
argument," because the `list)` case arm's non-exiting fallback paths
(plain `list`, or `list --runtime fly` when flyctl is unavailable/fails)
restore the original argv via `set -- "${_list_rest[@]}"`, putting the
literal `list` token back at position 1 of `$@` and falling through past
the top-level verb-dispatch `case` block into the REWRITTEN_ARGS filter
loop — which had (at the time) a guard arm rejecting any launcher-only
verb token wherever it appeared in `$@`, with no concept of "this is
$1 by construction, not a misplaced verb."

That bug was first patched by special-casing `resume`/`list` by index
(translating the bare word into itself, a no-op disguised as a fix) so
they could slip past the guard. That patch was itself built on a false
premise: it assumed orchestrator/leerie.py's argparse could only ever
understand `--resume`/`--list` as dash-flags, never bare positionals —
which was ALSO never true going forward, once orchestrator/leerie.py's
argparse was taught to parse `resume`/`list` as real bare leading verbs
(see `main()`'s `is_resume`/`is_list` pre-scan, popped off `sys.argv[1]`
before `ap.parse_args()` runs). With that fix in place, the REWRITTEN_ARGS
loop no longer needs to special-case anything for these two verbs: at
position 1 they simply need to reach the catch-all `*)` arm (which
appends them unmodified to REWRITTEN_ARGS) instead of the verb-rejection
guard. The fix is a `resume|list)` arm in that guard that checks index
and forwards at position 1, rejecting only a genuine non-$1 occurrence.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

# Single owner of the guard-arm extraction; see `test_block_present_in_launcher`
# for why this file consumes it rather than pinning the verb list as a literal.
from tests.test_launcher_verb_filter import _extract_rewritten_args_guard_verbs

REPO_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = REPO_ROOT / "leerie"

# Reproduces the launcher's REWRITTEN_ARGS loop's `resume`/`list` guard
# arm in isolation (mirrors test_launcher_seed_depth_resolution.py's
# _LAUNCHER_SEED_BLOCK pattern) — driving the real launcher end-to-end
# would require a full container build; this isolates just the dispatch
# logic and pins it against the live source via test_block_present_in_launcher.
_REWRITTEN_ARGS_LIST_BLOCK = r"""
REWRITTEN_ARGS=()
_rw_argi=0
for arg in "$@"; do
  _rw_argi=$((_rw_argi + 1))
  case "$arg" in
    finalize|stop|kill|re-seed|accept-blocked|attach|chain|group|config|status|version)
      echo "error: '$arg' is a verb and must be the first argument." >&2
      exit 1
      ;;
    resume|list)
      if [ "$_rw_argi" -eq 1 ]; then
        REWRITTEN_ARGS+=("$arg")
      else
        echo "error: '$arg' is a verb and must be the first argument." >&2
        exit 1
      fi
      ;;
    *)
      REWRITTEN_ARGS+=("$arg")
      ;;
  esac
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


def test_bare_list_passes_through_unmodified():
    """The reported bug, reproduced and pinned: bare `list` at position 1
    must reach the orchestrator, not be rejected as a misplaced verb."""
    assert _rewritten_args("list") == ["list"]


def test_bare_list_with_trailing_args_preserves_them():
    """`list --runtime fly` (as restored by the case-arm's flyctl-failure
    fallback) must pass through unmodified, trailing args included."""
    assert _rewritten_args("list", "--runtime", "fly") == (
        ["list", "--runtime", "fly"]
    )


def test_list_rejected_when_not_at_position_one():
    """A later positional/value equal to the literal word "list" must
    still be rejected as a misplaced verb (same discipline as `resume`)."""
    result = subprocess.run(
        ["bash", "-c",
         "set -euo pipefail\n" + _REWRITTEN_ARGS_LIST_BLOCK,
         "bash", "--answers", "list"],
        capture_output=True, text=True, env=os.environ,
    )
    assert result.returncode != 0
    assert "must be the first argument" in result.stderr


def test_resume_passes_through_unmodified():
    """Regression guard: `resume`'s pass-through must not disturb the
    `list` fix it was modeled on."""
    assert _rewritten_args("resume") == ["resume"]


def test_resume_with_positional_run_id_passes_through():
    """`resume <run-id>` must reach the orchestrator with the run-id
    still a bare positional — no rewrite into `--run-id <run-id>`; the
    orchestrator's argparse still declares --run-id as a flag, but the
    launcher no longer needs to translate the positional into it."""
    assert _rewritten_args("resume", "abc123") == ["resume", "abc123"]


def test_block_present_in_launcher():
    """Coupling test: the reproduced block must stay in lockstep with the
    launcher's real REWRITTEN_ARGS loop."""
    src = LAUNCHER.read_text()
    assert (
        'resume|list)\n'
        '      # Unlike the other launcher verbs above, `resume` and `list` can'
    ) in src, (
        "Launcher's resume/list position-1 pass-through guard arm is "
        "missing or has drifted."
    )
    # The resume|list arm must precede (or otherwise not be shadowed by)
    # the general verb-rejection guard arm matching every OTHER verb, or
    # bare `resume`/`list` at position 1 never gets a chance to pass.
    assert "resume|list)" in src
    # What this actually needs to be true is that the other-verbs arm does not
    # claim `resume`/`list`. It used to assert that by pinning the arm's whole
    # membership list as a literal — which failed the moment a legitimate new
    # verb was added (`prune`), and whose two follow-up assertions split the
    # literal DEFINED IN THIS FILE rather than the launcher's, so they held no
    # matter what the launcher said. Derive the arm instead; the extraction has
    # one owner (tests/test_launcher_verb_filter.py) and two consumers.
    other_verbs = _extract_rewritten_args_guard_verbs()
    assert other_verbs, "the other-launcher-verbs guard arm is missing"
    assert "resume" not in other_verbs, (
        "`resume` must not be in the reject-everything arm — it has its own "
        "position-1 pass-through arm")
    assert "list" not in other_verbs, (
        "`list` must not be in the reject-everything arm — it has its own "
        "position-1 pass-through arm")
