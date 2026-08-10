"""`_resolve_ec2_knob` has exactly one implementation: the launcher's.

The helper runs the CLI > env > `leerie.toml` ladder for the five `--ec2-*`
instance-shape knobs and for leerie's own `--aws-region` / `--aws-profile`.
Two test files exercise it, and both must reach for the REAL one —
`tests/test_resolve_ec2_vars.py` via `_extract_resolve_ec2_knob()`,
`tests/test_resolve_aws_launcher.py` via `_extract_aws_block()`.

`test_resolve_ec2_vars.py` used to embed a hand-written copy of the body.
The tests then ran a string literal defined in that file, so no change to
the launcher could affect them — while its coupling guard pinned the
helper's *name* and the flag/toml-key strings, never its logic. Inverting
the real helper's CLI/env precedence now fails five tests; against the copy
it could not have.

This is the same defect class as `tests/launcher_blocks.py` (launch-block
derivation, PRs #180-#183) and `tests/test_no_duplicate_state_walks.py`
(state-init branch walk). Third instance, same guard shape.

Note the drift here is *silent in the dangerous direction*: a stale copy
keeps passing, so the suite reports the launcher is covered while the
launcher is free to change underneath it.
"""
from __future__ import annotations

import re
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
LAUNCHER = TESTS_DIR.parent / "leerie"

# The definition marker, anchored to the START OF A LINE.
#
# A real reproduction sits inside a bash string literal, where the helper
# opens at column 0. A legitimate *reference* — `src.index("_resolve_ec2_knob()
# {")` in an extractor, or `.count(...)` in a wiring assertion — is always
# preceded by a quote mid-line. Matching the bare token instead flags every
# file that merely searches for the helper, which is a false positive on the
# extractors this guard exists to encourage; and the natural response to a
# false-positive guard is to weaken it, which is how guards die. Same
# prose-vs-implementation distinction `test_no_duplicate_launcher_splitters.py`
# draws with its escaped-regex marker.
_MARKER_RE = re.compile(r"^_resolve_ec2_knob\(\) \{", re.MULTILINE)

# The plain token, for the anti-vacuity check against the launcher (where the
# definition is genuinely at column 0 too, but so is nothing else).
_MARKER = "_resolve_ec2_knob() {"

# What consumers use instead of re-implementing.
_EXTRACTORS = ("_extract_resolve_ec2_knob", "_extract_aws_block")


def _files_defining_the_helper() -> dict[str, int]:
    """Every file under tests/ that DEFINES the helper, and its count."""
    hits: dict[str, int] = {}
    for path in sorted(TESTS_DIR.rglob("*.py")):
        if path.name == Path(__file__).name:
            continue  # this file necessarily names the marker to check it
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        n = len(_MARKER_RE.findall(text))
        if n:
            hits[path.name] = n
    return hits


def test_no_test_file_reimplements_the_helper() -> None:
    strays = _files_defining_the_helper()
    assert not strays, (
        f"{sorted(strays)} define `_resolve_ec2_knob` themselves. Extract it "
        f"from the launcher instead — a hand-written copy is body-blind by "
        f"construction, so the launcher can change underneath it while every "
        f"test keeps passing. See tests/test_resolve_ec2_vars.py's "
        f"`_extract_resolve_ec2_knob`."
    )


def test_the_launcher_actually_defines_it() -> None:
    """Anti-vacuity: a scan whose target no longer exists passes forever.

    If the helper is renamed or refactored away, the test above certifies
    "no duplicates" against a marker that matches nothing. Pin the real
    definition so that fails as a broken scan instead.
    """
    src = LAUNCHER.read_text()
    assert src.count(_MARKER) == 1, (
        f"expected exactly one `{_MARKER}` in the launcher, found "
        f"{src.count(_MARKER)} — the duplicate scan's marker is stale, so "
        f"its result above is meaningless")


def test_consumers_extract_rather_than_reproduce() -> None:
    """A single owner nobody reads from is not deduplication.

    Both known consumers must pull the helper out of the launcher at test
    time. Without this, deleting a consumer's extraction (and its coverage)
    would satisfy the no-duplicates test above perfectly.
    """
    extractors = [
        p.name for p in sorted(TESTS_DIR.rglob("*.py"))
        if p.name != Path(__file__).name
        and any(e in p.read_text(errors="replace") for e in _EXTRACTORS)
    ]
    assert len(extractors) >= 2, (
        f"expected at least the two known consumers "
        f"(test_resolve_ec2_vars.py, test_resolve_aws_launcher.py) to "
        f"extract the helper from the launcher, found {extractors}")
