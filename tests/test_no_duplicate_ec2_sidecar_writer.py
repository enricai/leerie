"""`write_ec2_sidecar` has exactly one implementation: tests/ec2_stub.py's.

Four EC2 launcher test files — test_ec2_bash32_portability.py,
test_ec2_launcher_readonly_verbs.py, test_ec2_launcher_resume.py,
test_ec2_launcher_stop.py — used to each redefine a near-identical
`_write_ec2_sidecar` (writes an `ec2-instance.json` + `run.json` fixture
pair), differing only in optional trailing kwargs (`with_host_state`;
`paused_at`/`pause_reason`). Consolidated into `tests/ec2_stub.py`'s
`write_ec2_sidecar`, the module this repo already uses as the single owner
for EC2-launcher-test scaffolding (`_stub_aws`, `seed_running_instance`,
`read_state`/`read_log`).

This is the same defect class, and the same guard shape, as
`tests/test_no_duplicate_ec2_knob.py` (`_resolve_ec2_knob`) and
`tests/test_no_duplicate_launcher_splitters.py` (`launch_env_blocks`):
a hand-copied fixture helper is body-blind by construction, so a fifth
file could silently reintroduce a copy that drifts from the shared one
without any test noticing — unless something scans for it.
"""
from __future__ import annotations

import re
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
OWNER = TESTS_DIR / "ec2_stub.py"

# Anchored to the START OF A LINE: a real (re)definition of the helper
# opens at column 0, while a legitimate reference — an import, a call —
# is never preceded by nothing but whitespace at the start of a line in
# that exact shape. Mirrors test_no_duplicate_ec2_knob.py's discipline.
_MARKER_RE = re.compile(r"^def write_ec2_sidecar\(", re.MULTILINE)


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
        f"{sorted(strays)} define `write_ec2_sidecar` themselves. Import "
        f"it from tests/ec2_stub.py instead of copying it — a hand-copied "
        f"fixture is body-blind by construction and can silently drift "
        f"from the shared version."
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
        f"expected exactly one `def write_ec2_sidecar(` in tests/ec2_stub.py, "
        f"found {n} — the duplicate scan's marker is stale, so its result "
        f"above is meaningless")


def test_scan_can_detect_a_planted_reproduction(tmp_path: Path) -> None:
    """Anti-vacuity: prove the scan actually fires on a reproduction,
    not just that it reports zero on the current (already-fixed) tree.

    Runs the real scan logic against a throwaway directory containing a
    planted copy, so a future edit that quietly breaks the regex (e.g.
    by requiring a signature shape the real definition doesn't have)
    fails loudly here instead of silently degrading the guard above into
    one that can never fire.
    """
    planted = tmp_path / "test_fake_ec2_sidecar_copy.py"
    planted.write_text(
        "def write_ec2_sidecar(run_dir, run_id, instance_id):\n"
        "    pass\n"
    )
    hits = {
        path.name: n
        for path in sorted(tmp_path.rglob("*.py"))
        for n in [len(_MARKER_RE.findall(path.read_text(errors="replace")))]
        if n
    }
    assert hits == {"test_fake_ec2_sidecar_copy.py": 1}, hits
