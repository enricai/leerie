"""Executable test: HOST_UID extraction from /proc/self/uid_map.

container-entry.sh's rootless branch anchors the leerie.slice cgroup at
the systemd-delegated user slice
(/sys/fs/cgroup/user.slice/user-<HOST_UID>.slice/user@<HOST_UID>.service),
which requires correctly extracting the real host UID rootlesskit mapped
container UID 0 to. That extraction (the second field of uid_map's first
line) is pinned here against real-world uid_map shapes rather than just
source-text-matched, since a subtly wrong awk expression would silently
point the broker at a nonexistent (or wrong-owner) cgroup path.
"""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ENTRY_SH = REPO_ROOT / "scripts" / "container-entry.sh"
_UID_MAP_PATH = "/proc/self/uid_map"


def _extract_host_uid_expr() -> str:
    """Pull the exact awk expression container-entry.sh uses for HOST_UID
    out of the real source, so this test breaks if it's ever edited."""
    text = ENTRY_SH.read_text()
    marker = 'HOST_UID="$(awk '
    start = text.index(marker) + len('HOST_UID="$(')
    end = text.index(")\"", start)
    expr = text[start:end]
    assert _UID_MAP_PATH in expr, (
        "expected the real container-entry.sh source to read "
        f"{_UID_MAP_PATH}; extraction marker may be stale"
    )
    return expr


def _run_extraction(uid_map_content: str) -> str:
    awk_expr = _extract_host_uid_expr()
    with tempfile.NamedTemporaryFile("w", suffix=".uid_map") as f:
        f.write(uid_map_content)
        f.flush()
        # Substitute the real (unreadable in a test sandbox) uid_map path
        # with our fixture file — the awk expression itself is untouched.
        cmd = awk_expr.replace(_UID_MAP_PATH, f.name)
        result = subprocess.run(["sh", "-c", cmd],
                                 capture_output=True, text=True, check=True)
    return result.stdout.strip()


def test_expression_present_in_container_entry():
    assert "HOST_UID=" in ENTRY_SH.read_text()


def test_extracts_uid_from_single_line_map():
    """Typical rootlesskit shape: one line mapping container UID 0 to a
    single host UID."""
    assert _run_extraction("         0       1000          1\n") == "1000"


def test_extracts_uid_ignoring_subuid_range_line():
    """Real uid_map has a second line for the subuid range (container UIDs
    1..65535 -> a large subuid block) — only the first line's second field
    (root's own mapping) is HOST_UID."""
    content = "         0       1000          1\n         1     100000      65536\n"
    assert _run_extraction(content) == "1000"


def test_extracts_large_uid():
    assert _run_extraction("         0      54321          1\n") == "54321"
