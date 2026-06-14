"""Coupling test: the launcher's `UUID_PATTERN` constant is the single
source of truth for the 8-4-4-4-12 hex pattern used by every chain-
scoped verb arm.

The launcher (`leerie`) parses UUIDs in seven places (`--stop`,
`--kill`, `--finalize`, `--resume`, `--status`, `--attach`, and the
`--chain --chain-id` flag). Before the v8 audit DRY refactor, each
arm carried its own inline copy of the same regex; a format change
required seven coordinated edits, easy to miss one. v8 extracted the
pattern into a single top-of-file `UUID_PATTERN` constant.

If a future change drops the constant reference at any call site
(reintroducing an inline regex copy, or worse, a typo'd inline
copy that silently accepts non-UUIDs), this test fails loudly so
the drift is caught at commit time rather than in production.
"""
from __future__ import annotations

import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = REPO_ROOT / "leerie"

# The canonical 8-4-4-4-12 hex pattern. Anchored so a "looks-like-
# UUID" hex prefix can't match.
EXPECTED_PATTERN = (
    "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


def test_uuid_pattern_declared_exactly_once() -> None:
    """The `UUID_PATTERN` assignment line appears exactly once in the
    launcher. A second declaration would be a copy-paste mistake; zero
    declarations would mean the constant was deleted."""
    src = LAUNCHER.read_text()
    declarations = re.findall(
        r"^(?:export\s+)?UUID_PATTERN=",
        src,
        re.MULTILINE,
    )
    assert len(declarations) == 1, (
        f"expected exactly one UUID_PATTERN declaration in the launcher, "
        f"found {len(declarations)}"
    )


def test_uuid_pattern_value_is_canonical() -> None:
    """The pattern's value is the canonical 8-4-4-4-12 hex regex.
    Anchored at both ends so a UUID-shaped prefix of a longer string
    can't pass validation."""
    src = LAUNCHER.read_text()
    match = re.search(
        r"^(?:export\s+)?UUID_PATTERN=(['\"])(?P<value>.*?)\1\s*$",
        src,
        re.MULTILINE,
    )
    assert match, "UUID_PATTERN declaration not found"
    assert match.group("value") == EXPECTED_PATTERN, (
        f"UUID_PATTERN value drifted from canonical form.\n"
        f"  expected: {EXPECTED_PATTERN!r}\n"
        f"  actual:   {match.group('value')!r}"
    )


def test_uuid_pattern_used_at_expected_call_site_count() -> None:
    """Every chain-scoped UUID check uses the constant. Counts the
    `grep -qiE "$UUID_PATTERN"` invocations and asserts the expected
    minimum count (7 today: --stop, --kill, --finalize, --resume,
    --status, --attach, --chain-id).

    Uses >= rather than == so adding a new chain-scoped verb that
    consumes UUID_PATTERN doesn't break this test; only DECREASING
    the count (which would mean a call site got an inline regex copy
    or got deleted entirely) is a failure."""
    src = LAUNCHER.read_text()
    occurrences = re.findall(
        r'grep\s+-qiE\s+"\$UUID_PATTERN"',
        src,
    )
    assert len(occurrences) >= 7, (
        f"expected at least 7 `grep -qiE \"$UUID_PATTERN\"` call sites in "
        f"the launcher (--stop, --kill, --finalize, --resume, --status, "
        f"--attach, --chain --chain-id); found {len(occurrences)}.\n"
        f"A site may have been replaced with an inline regex copy, "
        f"defeating the v8 DRY refactor."
    )


def test_no_inline_uuid_regex_copies() -> None:
    """No raw `^[0-9a-f]{8}-...{12}$` literal remains in the launcher
    outside the UUID_PATTERN declaration itself. A bare inline copy
    is a regression — the v8 DRY refactor extracted the pattern
    precisely to avoid the maintenance burden of editing seven
    coordinated copies on every change."""
    src = LAUNCHER.read_text()
    # Match the full anchored canonical literal regardless of the
    # quoting style. Strip the UUID_PATTERN= declaration line first,
    # since that's the one expected occurrence.
    src_minus_declaration = re.sub(
        r"^(?:export\s+)?UUID_PATTERN=['\"].*?['\"]\s*$",
        "",
        src,
        flags=re.MULTILINE,
    )
    inline_copies = re.findall(
        r"\[0-9a-f\]\{8\}-\[0-9a-f\]\{4\}-\[0-9a-f\]\{4\}-\[0-9a-f\]\{4\}-\[0-9a-f\]\{12\}",
        src_minus_declaration,
    )
    assert not inline_copies, (
        f"found {len(inline_copies)} inline UUID regex copies in the "
        f"launcher (other than the UUID_PATTERN declaration). The v8 "
        f"DRY refactor extracted this pattern into a single constant; "
        f"a copy means the constant got bypassed somewhere."
    )
