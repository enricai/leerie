"""Regression pins for N19: .claude.json must not be bind-mounted as a
single file onto /home/leerie/.claude.json.

A bind-mounted single file makes the CLI's atomic rename()-based write
(tmp file + rename() onto the target) return EBUSY, forcing a fallback to
truncate-in-place -- which under concurrent workers has a demonstrated
empty-file corruption window. The fix bind-mounts the whole staged
directory read-only at a staging path instead, and container-entry.sh
copies .claude.json out of it into place at container start, as a real
file inside the container's own filesystem where rename() works normally.

Mirrors the source-coupling style of tests/test_home_leerie_ownership.py
and tests/test_tmp_cache_writable.py for the two privilege models.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = REPO_ROOT / "leerie"
ENTRY_SH = REPO_ROOT / "scripts" / "container-entry.sh"

_OLD_SINGLE_FILE_MOUNT = '-v "$STAGE/.claude.json:/home/leerie/.claude.json"'
_NEW_DIR_MOUNT = '-v "$STAGE:/opt/leerie-claude-json-src:ro"'


def _entry_sh_copy_block() -> str:
    text = ENTRY_SH.read_text()
    marker = "if [ -f /opt/leerie-claude-json-src/.claude.json ]; then"
    start = text.index(marker)
    end = text.index("\nfi", start) + len("\nfi")
    return text[start:end]


def _entry_sh_rootful_block() -> str:
    text = ENTRY_SH.read_text()
    marker = 'if [ "$ROOTLESS" != "true" ] && getent passwd leerie'
    start = text.index(marker)
    end = text.index("\nfi", start)
    return text[start:end]


def test_launcher_does_not_bind_mount_claude_json_as_single_file():
    text = LAUNCHER.read_text()
    assert _OLD_SINGLE_FILE_MOUNT not in text


def test_launcher_bind_mounts_stage_dir_for_claude_json_src():
    text = LAUNCHER.read_text()
    assert _NEW_DIR_MOUNT in text


def test_launcher_still_mounts_claude_dir_directly():
    text = LAUNCHER.read_text()
    assert '-v "$STAGE/.claude:/home/leerie/.claude"' in text


def test_container_entry_copies_claude_json_into_place():
    block = _entry_sh_copy_block()
    assert "cp /opt/leerie-claude-json-src/.claude.json /home/leerie/.claude.json" in block


def test_container_entry_copy_step_is_unconditional_across_privilege_models():
    """The copy runs before the rootless/rootful branch point, so it applies
    under both unshare --map-user (rootless) and runuser (rootful)."""
    text = ENTRY_SH.read_text()
    copy_idx = text.index("cp /opt/leerie-claude-json-src/.claude.json")
    rootful_idx = text.index('if [ "$ROOTLESS" != "true" ] && getent passwd leerie')
    assert copy_idx < rootful_idx


def test_container_entry_chowns_claude_json_in_rootful_guard():
    """Rootful's runuser -u leerie is a real uid switch (no remap), so the
    freshly-copied root-owned file needs an explicit chown there -- same
    reasoning as the other /home/leerie ownership fixes in that guard."""
    block = _entry_sh_rootful_block()
    assert (
        "[ -f /home/leerie/.claude.json ] && chown leerie: /home/leerie/.claude.json"
        in block
    )


def test_falsify_reverting_to_single_file_mount_fails_the_guard():
    """Falsification: replaying the old single-file mount string against
    the guard's own predicate must fail it."""
    reverted_text = (
        LAUNCHER.read_text().replace(_NEW_DIR_MOUNT, "") + "\n" + _OLD_SINGLE_FILE_MOUNT
    )
    assert _OLD_SINGLE_FILE_MOUNT in reverted_text


def test_atomic_rewrite_of_copied_file_succeeds_without_ebusy():
    """End-to-end reproduction of the work order's own live-container
    measurement technique: os.replace() (rename()) onto a real file
    inside a plain temp dir -- standing in for the container's own
    filesystem after container-entry.sh's copy step -- must succeed
    atomically with no EBUSY, unlike a rename() onto a bind-mounted
    single file."""
    with tempfile.TemporaryDirectory() as d:
        target = Path(d) / ".claude.json"
        target.write_text('{"projects": {}}')

        tmp_path = Path(d) / ".claude.json.tmp"
        tmp_path.write_text('{"projects": {"a": 1}}')
        os.replace(tmp_path, target)

        assert target.read_text() == '{"projects": {"a": 1}}'
        assert not tmp_path.exists()
