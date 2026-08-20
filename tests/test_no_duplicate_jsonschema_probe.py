"""The `HAS_JSONSCHEMA` availability probe has exactly one owner: conftest.py.

Before the test-002 migration, `HAS_JSONSCHEMA = True` (with its matching
`except ImportError: HAS_JSONSCHEMA = False` fallback) was hand-copied into
18 test files instead of imported from `tests/conftest.py`. Each copy was
body-blind by construction: it answered "is jsonschema installed" correctly,
but a change to the real probe's logic in conftest.py could not reach any
of them. Same defect class as `tests/test_no_duplicate_launcher_blocks.py`
and `tests/test_no_duplicate_ec2_knob.py` — a single-owner guard, not a
correctness check on the probe itself.

Per CLAUDE.md's documented discipline for that guard shape, a scan that
never finds a planted duplicate would certify "no duplicates" forever. This
file's anti-vacuity control deliberately writes a second `HAS_JSONSCHEMA =
True` definition to a throwaway file under `tests/` and asserts the scan
flags it, then removes the file.
"""
from __future__ import annotations

import re
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
CONFTEST = TESTS_DIR / "conftest.py"

# Anchored to a bare assignment at the start of a (possibly indented) line
# — conftest.py's real definition sits inside a `try:` block. A legitimate
# reference (e.g. `from tests.conftest import HAS_JSONSCHEMA`, or a
# docstring/comment mentioning the name) never has the literal `= True`
# suffix at the start of its line.
_MARKER_RE = re.compile(r"^[ \t]*HAS_JSONSCHEMA = True", re.MULTILINE)
_MARKER = "HAS_JSONSCHEMA = True"


def _files_defining_the_marker() -> dict[str, int]:
    """Every file under tests/ that DEFINES `HAS_JSONSCHEMA = True`, and its count."""
    hits: dict[str, int] = {}
    for path in sorted(TESTS_DIR.rglob("*.py")):
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        n = len(_MARKER_RE.findall(text))
        if n:
            hits[path.name] = n
    return hits


def test_marker_defined_exactly_once_in_conftest() -> None:
    hits = _files_defining_the_marker()
    assert hits == {"conftest.py": 1}, (
        f"expected `HAS_JSONSCHEMA = True` to be defined exactly once, in "
        f"tests/conftest.py; found {hits}. A test file should `from "
        f"tests.conftest import HAS_JSONSCHEMA` instead of redefining the "
        f"probe — a hand-copied probe cannot see a future change to the "
        f"real one in conftest.py."
    )


def test_conftest_actually_defines_it() -> None:
    """Anti-vacuity: a scan whose target no longer exists passes forever.

    If the probe is renamed or refactored away in conftest.py, the test
    above would certify "no duplicates" against a marker that matches
    nothing. Pin the real definition so that fails as a broken scan instead.
    """
    src = CONFTEST.read_text()
    assert src.count(_MARKER) == 1, (
        f"expected exactly one `{_MARKER}` in tests/conftest.py, found "
        f"{src.count(_MARKER)} — the duplicate scan's marker is stale, so "
        f"its result above is meaningless")


def test_scan_flags_a_planted_duplicate() -> None:
    """Anti-vacuity: prove the scan actually fires on a real duplicate.

    Plants a throwaway file under tests/ carrying a second `HAS_JSONSCHEMA
    = True` definition, re-runs the scan, and confirms it is caught —
    then removes the file. Without this, the scan above could be silently
    broken (e.g. matching nothing at all) and still report "no duplicates".
    """
    plant = TESTS_DIR / "_plant_jsonschema_probe_duplicate.py"
    assert not plant.exists()
    try:
        plant.write_text("HAS_JSONSCHEMA = True\n")
        hits = _files_defining_the_marker()
        assert hits.get(plant.name) == 1, (
            "planted duplicate was not detected by the scan — the scan is "
            "broken and its 'no duplicates' result above is meaningless"
        )
        assert len(hits) == 2, (
            f"expected exactly the real owner plus the plant to be flagged, "
            f"found {hits}"
        )
    finally:
        plant.unlink(missing_ok=True)
