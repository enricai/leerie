"""Shared extractor helpers reused across tests/*.py.

Single-owner discipline (see CLAUDE.md's `launcher_blocks.py`/`ec2_stub.py`
precedent): each helper here previously existed as byte-identical copies in
every consuming test file, which is the drift risk this module exists to
remove.
"""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ENTRY_SH = REPO_ROOT / "scripts" / "container-entry.sh"


def _entry_sh_rootful_block() -> str:
    text = ENTRY_SH.read_text()
    marker = 'if [ "$ROOTLESS" != "true" ] && getent passwd leerie'
    start = text.index(marker)
    end = text.index("\nfi", start)
    return text[start:end]
