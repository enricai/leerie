"""`build_ec2_launcher_env` has exactly one implementation: tests/ec2_stub.py's.

tests/test_ec2_launcher_readonly_verbs.py and tests/test_ec2_launcher_stop.py
each used to redefine a near-identical `_env(tmp_path, aws_dir)` (builds the
minimal PATH/USER_REPO/LEERIE_REPO/HOME/LEERIE_STATE_HOST_DIR/
LEERIE_STATE_DIR/AWS_* env dict a `leerie` launcher subprocess needs against
the stubbed `aws` binary), differing only in extras: readonly_verbs.py
additionally created a `remote-work` dir and returned a 3-tuple; stop.py
accepted an optional pre-built `home` Path and returned a 2-tuple.
Consolidated into tests/ec2_stub.py's `build_ec2_launcher_env`, following the
same single-owner convention as `write_ec2_sidecar`
(tests/test_no_duplicate_ec2_sidecar_helper.py).

This is the same defect class, and the same guard shape, as
`tests/test_no_duplicate_ec2_sidecar_helper.py`,
`tests/test_no_duplicate_ec2_knob.py`, and
`tests/test_no_duplicate_launcher_splitters.py`: a hand-copied fixture
helper is body-blind by construction, so a third file could silently
reintroduce a copy that drifts from the shared one without any test
noticing — unless something scans for it.
"""
from __future__ import annotations

import re
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
OWNER = TESTS_DIR / "ec2_stub.py"

# Anchored to the START OF A LINE: a real (re)definition of the helper
# opens at column 0, while a legitimate reference — an import, a call —
# is never preceded by nothing but whitespace at the start of a line in
# that exact shape. Mirrors test_no_duplicate_ec2_sidecar_helper.py's
# discipline. Matches both the retired `_env(` name and the current
# `build_ec2_launcher_env(` name, so a partial revert (renaming back
# without restoring the shared owner) is also caught.
_MARKER_RE = re.compile(
    r"^def (?:_env|build_ec2_launcher_env)\(", re.MULTILINE
)


def _files_defining_the_helper() -> dict[str, int]:
    """Every file under tests/ that DEFINES the helper, and its count."""
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


def test_no_test_file_reimplements_the_helper() -> None:
    strays = _files_defining_the_helper()
    assert not strays, (
        f"{sorted(strays)} define an env-builder helper themselves. Import "
        f"build_ec2_launcher_env from tests/ec2_stub.py instead of copying "
        f"it — a hand-copied fixture is body-blind by construction and can "
        f"silently drift from the shared version."
    )


def test_the_owner_actually_defines_it() -> None:
    """Anti-vacuity: a scan whose target no longer exists passes forever.

    If the helper is renamed or refactored away in tests/ec2_stub.py, the
    test above certifies "no duplicates" against a marker that matches
    nothing. Pin the real definition so that fails as a broken scan
    instead.
    """
    src = OWNER.read_text()
    n = len(_MARKER_RE.findall(src))
    assert n == 1, (
        f"expected exactly one env-builder definition in "
        f"tests/ec2_stub.py, found {n} — the duplicate scan's marker is "
        f"stale, so its result above is meaningless")


def test_scan_can_detect_a_planted_reproduction(tmp_path: Path) -> None:
    """Anti-vacuity: prove the scan actually fires on a reproduction,
    not just that it reports zero on the current (already-fixed) tree.
    """
    planted = tmp_path / "test_fake_ec2_env_copy.py"
    planted.write_text(
        "def _env(tmp_path, aws_dir):\n"
        "    pass\n"
    )
    hits = {
        path.name: n
        for path in sorted(tmp_path.rglob("*.py"))
        for n in [len(_MARKER_RE.findall(path.read_text(errors="replace")))]
        if n
    }
    assert hits == {"test_fake_ec2_env_copy.py": 1}, hits
