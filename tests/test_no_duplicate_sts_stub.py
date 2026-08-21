"""The minimal argv-only `aws sts get-caller-identity` stub has exactly
one implementation: tests/ec2_stub.py's `make_sts_only_stub_aws`.

tests/test_ec2_lib_sh.py::_stub_aws and
tests/test_ec2_launcher_credentials.py::_stub_aws_recording_region used to
each hand-write a near-identical bash stub — write an `aws` binary that
logs argv to `aws.log` and special-cases `sts get-caller-identity` — the
second file's own docstring already said it mirrored the first. Both are
now thin wrappers delegating to `tests/ec2_stub.py::make_sts_only_stub_aws`,
the single-owner module this repo already uses for this class of fixture.

Same defect class and guard shape as
`tests/test_no_duplicate_ec2_sidecar_helper.py` (`write_ec2_sidecar`) and
`tests/test_no_duplicate_ec2_knob.py` (`_resolve_ec2_knob`): a hand-copied
fixture helper is body-blind by construction, so a third file could
silently reintroduce a copy that drifts from the shared one without any
test noticing — unless something scans for it.
"""
from __future__ import annotations

import re
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
OWNER = TESTS_DIR / "ec2_stub.py"

# The marker matches the distinctive *shape* of this specific stub: the
# `[ "$2" = "get-caller-identity" ]; then` condition, followed (within a
# short window) by an unconditional `exit 0` fallthrough for every other
# command. That fallthrough is what makes this shape a full reimplementation
# of the minimal argv-logging stub, and is deliberately what distinguishes
# it from tests/test_ec2_e2e_provision.py's `_break_sts_get_caller_identity`
# — a legitimately different, narrower helper that shares the same `if`
# condition text (a reasonable shell fragment to reach for independently)
# but execs the real stateful stub for every other command instead of
# falling through to a bare `exit 0`. It never appears in prose (every
# prose mention of "sts get-caller-identity" above reads without the
# trailing `]; then ... exit 0` shell syntax), so it needs no start-of-line
# anchor the way a `def foo(` marker does.
_MARKER_RE = re.compile(
    r'get-caller-identity[\'"]?\s*\]\s*;\s*then.{0,150}?\bfi\b.{0,60}?exit 0',
    re.DOTALL,
)


def _files_defining_the_stub_body() -> dict[str, int]:
    """Every file under tests/ that embeds the stub's shell body."""
    hits: dict[str, int] = {}
    for path in sorted(TESTS_DIR.rglob("*.py")):
        if path == OWNER or path.name == Path(__file__).name:
            continue
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        n = len(_MARKER_RE.findall(text))
        if n:
            hits[path.name] = n
    return hits


def test_no_test_file_reimplements_the_sts_stub() -> None:
    strays = _files_defining_the_stub_body()
    assert not strays, (
        f"{sorted(strays)} embed a hand-written `sts get-caller-identity` "
        f"stub body themselves. Import `make_sts_only_stub_aws` from "
        f"tests/ec2_stub.py instead of copying it — a hand-copied fixture "
        f"is body-blind by construction and can silently drift from the "
        f"shared version."
    )


def test_the_owner_actually_defines_it() -> None:
    """Anti-vacuity: a scan whose target no longer exists passes forever.

    If the stub body is refactored away in tests/ec2_stub.py, the test
    above certifies "no duplicates" against a marker that matches
    nothing. Pin the real definition so that fails as a broken scan
    instead.
    """
    src = OWNER.read_text()
    assert "def make_sts_only_stub_aws(" in src, (
        "expected tests/ec2_stub.py to define `make_sts_only_stub_aws` — "
        "the duplicate scan's target is missing, so its result above is "
        "meaningless"
    )
    n = len(_MARKER_RE.findall(src))
    assert n == 1, (
        f"expected exactly one embedded `sts get-caller-identity` stub "
        f"body in tests/ec2_stub.py, found {n} — the duplicate scan's "
        f"marker is stale, so its result above is meaningless"
    )


def test_scan_can_detect_a_planted_reproduction(tmp_path: Path) -> None:
    """Anti-vacuity: prove the scan actually fires on a reproduction, not
    just that it reports zero on the current (already-fixed) tree.

    Runs the real scan logic against a throwaway directory containing a
    planted copy, so a future edit that quietly breaks the regex fails
    loudly here instead of silently degrading the guard above into one
    that can never fire.
    """
    planted = tmp_path / "test_fake_sts_stub_copy.py"
    planted.write_text(
        "def _stub_aws(aws_dir):\n"
        "    stub.write_text(\n"
        '        \'if [ "$1" = "sts" ] && [ "$2" = "get-caller-identity" ]; then\\n\'\n'
        '        "  exit 0\\n"\n'
        '        "fi\\n"\n'
        '        "exit 0\\n"\n'
        "    )\n"
    )
    hits = {
        path.name: n
        for path in sorted(tmp_path.rglob("*.py"))
        for n in [len(_MARKER_RE.findall(path.read_text(errors="replace")))]
        if n
    }
    assert hits == {"test_fake_sts_stub_copy.py": 1}, hits
