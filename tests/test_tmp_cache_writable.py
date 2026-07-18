"""Regression pins for the /tmp/.cache world-writable fix.

Bug class (observed live for tree-sitter-language-pack's download-cache
lock, and previously for corepack — see COREPACK_HOME in
docs/IMPLEMENTATION.md): under rootless containerd, container-entry.sh's
privilege drop is `unshare --user --map-user=$(id -u leerie) ...`, which
remaps only outer UID 0 -> inner leerie. A directory explicitly chowned to
leerie's own (non-zero) UID is NOT covered by that single-entry remap, so
it appears owned by nobody/65534 to the remapped process — traversable via
its mode-755 "other" bits, but not writable. Verified live: `mkdir` under
/tmp/.cache/tree-sitter-language-pack failed with EACCES under the real
`unshare --user --map-user=<leerie-uid>` mechanism against the pre-fix
image, and succeeded once /tmp/.cache was made world-writable with the
sticky bit (the same posture /tmp itself already has).

These are source-coupling tests (mirroring tests/test_rootless_host_uid.py):
they pin that both the Dockerfile (build time) and container-entry.sh
(runtime, rootful/Fly path) carry the chmod fix, so a future edit can't
silently drop it and reintroduce the bug. A full container-level
reproduction needs nerdctl + an unprivileged-user-namespace-capable image,
which isn't exercised by the plain pytest suite here.
"""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = REPO_ROOT / "Dockerfile"
ENTRY_SH = REPO_ROOT / "scripts" / "container-entry.sh"


def _dockerfile_tmp_cache_block() -> str:
    text = DOCKERFILE.read_text()
    marker = "RUN chown -R leerie: /tmp/.cache"
    start = text.index(marker)
    end = text.index("\n\n", start)
    return text[start:end]


def _entry_sh_rootful_block() -> str:
    text = ENTRY_SH.read_text()
    marker = 'if [ "$ROOTLESS" != "true" ] && getent passwd leerie'
    start = text.index(marker)
    end = text.index("\nfi", start)
    return text[start:end]


def test_dockerfile_chowns_tmp_cache_to_leerie():
    block = _dockerfile_tmp_cache_block()
    assert "chown -R leerie: /tmp/.cache" in block


def test_dockerfile_makes_tmp_cache_world_writable():
    """The chown alone reintroduces the rootless-remap bug; the world-
    writable + sticky chmod is what actually fixes it."""
    block = _dockerfile_tmp_cache_block()
    assert "chmod -R a+rwX /tmp/.cache" in block
    assert "chmod 1777 /tmp/.cache" in block


def test_dockerfile_chmod_follows_chown():
    """Order matters only in that both must land in the same build layer;
    pin the relative order so a future edit can't silently split them
    across separate RUN steps (defeating the "chmod wins last" intent is
    harmless here, but keeping them together keeps the invariant legible)."""
    block = _dockerfile_tmp_cache_block()
    assert block.index("chown -R leerie: /tmp/.cache") < block.index(
        "chmod -R a+rwX /tmp/.cache")


def test_container_entry_chmod_is_inside_rootful_guard():
    """The runtime safety net must live inside the `ROOTLESS != true`
    branch — applying chown/chmod under rootless would just be reasserting
    ownership/mode on a directory whose remapped visibility the fix
    doesn't control, and rootless already relies on the Dockerfile's
    build-time fix exclusively (see container-entry.sh's own comment)."""
    block = _entry_sh_rootful_block()
    assert "chown -R leerie: /tmp/.cache" in block
    assert "chmod -R a+rwX /tmp/.cache" in block
    assert "chmod 1777 /tmp/.cache" in block


def test_container_entry_chmod_follows_chown():
    block = _entry_sh_rootful_block()
    assert block.index("chown -R leerie: /tmp/.cache") < block.index(
        "chmod -R a+rwX /tmp/.cache")
