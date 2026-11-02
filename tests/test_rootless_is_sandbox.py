"""Source-text pin: rootless entrypoint remaps UID via unshare.

Claude Code rejects --dangerously-skip-permissions from UID 0. The
rootless path in container-entry.sh uses unshare --user to remap outer
UID 0 to the leerie user in a nested user namespace, so the
orchestrator runs as non-root and the flag is accepted.
"""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ENTRY_SH = REPO_ROOT / "scripts" / "container-entry.sh"


def _orchestrator_rootless_block() -> str:
    """Return the rootless if-block that launches the orchestrator
    (the one referencing leerie.py, not the Fly idle sleep block)."""
    src = ENTRY_SH.read_text()
    marker = "python3 /opt/leerie-image/orchestrator/leerie.py"
    py_pos = src.index(marker)
    block_start_marker = 'if [ "$ROOTLESS" = "true" ]; then'
    start = src.rindex(block_start_marker, 0, py_pos)
    end = src.index("\nfi", start) + len("\nfi")
    return src[start:end]


def test_rootless_uses_unshare():
    """The rootless exec must use unshare --user to remap UID 0 to
    a non-root user so Claude Code accepts --dangerously-skip-permissions."""
    block = _orchestrator_rootless_block()
    assert "unshare --user" in block, (
        "rootless entrypoint must use unshare --user — without it, "
        "Claude Code rejects --dangerously-skip-permissions from UID 0"
    )


def test_unshare_maps_leerie_uid():
    """unshare must map to the leerie user's UID/GID, not a hardcoded value."""
    block = _orchestrator_rootless_block()
    assert "--map-user=" in block, (
        "unshare must use --map-user to remap to leerie's UID"
    )
    assert "--map-group=" in block, (
        "unshare must use --map-group to remap to leerie's GID"
    )


def test_unshare_precedes_python():
    """unshare must appear before the orchestrator's python3 in the exec
    chain (the broker's `--spawn`, which hands the chain its descriptor,
    runs before both)."""
    block = _orchestrator_rootless_block()
    unshare_pos = block.index("unshare")
    exec_pos = block.index("python3 /opt/leerie-image/orchestrator/leerie.py")
    assert unshare_pos < exec_pos, (
        "unshare must precede the python3 invocation"
    )
