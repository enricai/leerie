"""Regression pins for /home/leerie/.local, .cache, .gnupg ownership.

Rootless containerd's privilege drop (`unshare --user
--map-user=$(id -u leerie)`) is a single-entry UID map: outer 0 -> inner
leerie. A directory chowned to leerie's literal UID has no entry in that
map and is unwritable to the remapped process; a root-owned directory
maps correctly. These dirs must therefore stay root-owned at image build
time, with the rootful path (a real `runuser -u leerie` switch, which
needs literal ownership) chowning them at runtime instead.
"""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = REPO_ROOT / "Dockerfile"
ENTRY_SH = REPO_ROOT / "scripts" / "container-entry.sh"


def _dockerfile_home_leerie_block() -> str:
    text = DOCKERFILE.read_text()
    marker = "RUN mkdir -p /home/leerie/.local/share/mise"
    end_marker = "chmod 700 /home/leerie/.gnupg"
    start = text.index(marker)
    end = text.index(end_marker, start) + len(end_marker)
    return text[start:end]


def _entry_sh_rootful_block() -> str:
    text = ENTRY_SH.read_text()
    marker = 'if [ "$ROOTLESS" != "true" ] && getent passwd leerie'
    start = text.index(marker)
    end = text.index("\nfi", start)
    return text[start:end]


def test_dockerfile_does_not_chown_home_leerie_subdirs():
    block = _dockerfile_home_leerie_block()
    assert "chown" not in block


def test_dockerfile_still_locks_down_gnupg_mode():
    block = _dockerfile_home_leerie_block()
    assert "chmod 700 /home/leerie/.gnupg" in block


def test_container_entry_chowns_home_leerie_subdirs_in_rootful_guard():
    block = _entry_sh_rootful_block()
    assert "chown leerie: /home/leerie " in block or "chown leerie: /home/leerie\n" in block
    assert "chown -R leerie: /home/leerie/.local /home/leerie/.cache /home/leerie/.gnupg" in block


def test_tmp_cache_fix_untouched_in_rootful_guard():
    """The unrelated /tmp/.cache world-writable fix must still be present
    and unchanged by this change."""
    block = _entry_sh_rootful_block()
    assert "chown -R leerie: /tmp/.cache" in block
    assert "chmod -R a+rwX /tmp/.cache" in block
    assert "chmod 1777 /tmp/.cache" in block
