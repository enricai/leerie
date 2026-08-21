"""The `--profile`/`--region` creds-args block has exactly one implementation.

`accept-blocked`, `accept-integration`, `stop`, `kill --runtime ec2`,
`finalize --runtime ec2`, and the main `RUNTIME=ec2` dispatch each used to
carry a byte-identical, independently-copied two-line block:

    [ -n "${LEERIE_AWS_PROFILE:-}" ] && <prefix>_aws_creds_args+=(--profile "$LEERIE_AWS_PROFILE")
    [ -n "${LEERIE_AWS_REGION:-}" ] && <prefix>_aws_creds_args+=(--region "$LEERIE_AWS_REGION")

differing only in the local array variable's per-site prefix. They are now
calls to one shared `_leerie_aws_creds_args_tokens` function. This is the
same defect class as `tests/test_no_duplicate_ec2_knob.py` (`_resolve_ec2_knob`)
and `tests/launcher_blocks.py` (launch-block derivation, PRs #180-#183):
without a guard, a future edit can reintroduce a 7th hand-copied block and
nothing would notice, since the launcher would still work — the six sites
would just silently stop sharing the fix the next time this precedence logic
changes.
"""
from __future__ import annotations

import re
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
LAUNCHER = TESTS_DIR.parent / "leerie"

# The duplicated line's shape, anchored to the START OF A LINE (mirroring
# test_no_duplicate_ec2_knob.py's marker discipline: a real reproduction of
# the old inline form sits at the caller's indentation, column 0 of the
# logical line; a legitimate reference -- e.g. this very docstring, or a
# comment describing the old shape -- is never a bare source line by itself
# at that anchor. re.MULTILINE with a leading `\s*` tolerates the callers'
# differing indentation (top-level dispatch vs. nested arms) while still
# requiring the line begin with the literal guard.)
_DUP_LINE_RE = re.compile(
    r'^[ \t]*\[ -n "\$\{LEERIE_AWS_PROFILE:-\}" \] && \w+\+=\(--profile "\$LEERIE_AWS_PROFILE"\)$',
    re.MULTILINE,
)

_FUNC_MARKER = "_leerie_aws_creds_args_tokens() {"
_CALL_MARKER = "< <(_leerie_aws_creds_args_tokens)"


def _count_dup_lines(text: str) -> int:
    return len(_DUP_LINE_RE.findall(text))


def test_no_duplicated_inline_block_remains() -> None:
    """Only the shared function's own body may contain this line shape."""
    src = LAUNCHER.read_text()
    n = _count_dup_lines(src)
    assert n <= 1, (
        f"found {n} copies of the inline "
        f'`[ -n "${{LEERIE_AWS_PROFILE:-}}" ] && ...` guard line in leerie, '
        f"expected at most 1 (inside _leerie_aws_creds_args_tokens itself). "
        f"A caller should invoke the shared function instead of "
        f"re-inlining this precedence logic."
    )


def test_the_scan_can_detect_a_planted_reproduction() -> None:
    """Anti-vacuity: a scan that finds nothing certifies everything.

    Prove the regex actually fires on the exact shape a careless future
    edit would reintroduce, using a fresh variable prefix so it cannot be
    confused with a real call site.
    """
    planted = (
        '      _planted_aws_creds_args=()\n'
        '      [ -n "${LEERIE_AWS_PROFILE:-}" ] && '
        '_planted_aws_creds_args+=(--profile "$LEERIE_AWS_PROFILE")\n'
        '      [ -n "${LEERIE_AWS_REGION:-}" ] && '
        '_planted_aws_creds_args+=(--region "$LEERIE_AWS_REGION")\n'
    )
    real_src = LAUNCHER.read_text()
    assert _count_dup_lines(real_src + planted) == _count_dup_lines(real_src) + 1, (
        "the duplicate-line scan did not detect a planted reproduction of "
        "the old inline block -- the guard above would pass vacuously"
    )


def test_shared_function_defined_exactly_once() -> None:
    src = LAUNCHER.read_text()
    assert src.count(_FUNC_MARKER) == 1, (
        f"expected exactly one `{_FUNC_MARKER}` definition in leerie, found "
        f"{src.count(_FUNC_MARKER)}"
    )


def test_shared_function_called_at_all_six_sites() -> None:
    src = LAUNCHER.read_text()
    # The definition's own usage-example comment also contains the call
    # marker, so the call-site count is (occurrences - 1 for the def body's
    # doc comment - 1 for the marker's own appearance inside the function
    # name... in practice: total occurrences of the call marker minus the
    # one that appears in the docstring-style comment above the def).
    total = src.count(_CALL_MARKER)
    # One occurrence lives in the usage-comment directly above the
    # function definition; the rest are real call sites.
    comment_occurrences = src.count(f"#     {_CALL_MARKER}")
    call_sites = total - comment_occurrences
    assert call_sites == 6, (
        f"expected 6 real call sites of `_leerie_aws_creds_args_tokens` "
        f"(accept-blocked, accept-integration, stop, kill --runtime ec2, "
        f"finalize --runtime ec2, main RUNTIME=ec2 dispatch), found "
        f"{call_sites} (total occurrences of the call marker: {total}, "
        f"comment occurrences: {comment_occurrences})"
    )


def test_no_test_file_reimplements_the_block() -> None:
    """No test should hand-copy the old inline shape to fake coverage."""
    strays: dict[str, int] = {}
    for path in sorted(TESTS_DIR.rglob("*.py")):
        if path.name == Path(__file__).name:
            continue
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        n = _count_dup_lines(text)
        if n:
            strays[path.name] = n
    assert not strays, (
        f"{sorted(strays)} contain the old inline AWS creds-args block "
        f"shape -- tests should exercise the real launcher, not a "
        f"hand-copied reproduction of retired logic"
    )
