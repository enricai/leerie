"""Coupling test: the leerie launcher's CACHE_MOUNTS array contains the Ruby
bundle cache volume mount and BUNDLE_PATH env var, and the host cache dir is
created (mkdir -p) before the array is defined.

This test reads the launcher source text directly — no subprocess execution —
so any refactor that silently drops the bundle lines causes an immediate test
failure rather than a silent regression where every `bundle install` downloads
and compiles gems from scratch.
"""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER_PATH = REPO_ROOT / "leerie"

_BUNDLE_VOLUME_TARGET = "/home/leerie/.cache/leerie/bundle"
_BUNDLE_PATH_ENV = "BUNDLE_PATH=/home/leerie/.cache/leerie/bundle"
_BUNDLE_MKDIR = "$HOME/.cache/leerie/bundle"
_CACHE_MOUNTS_OPEN = "CACHE_MOUNTS=("


def _launcher_text() -> str:
    return LAUNCHER_PATH.read_text()


def _extract_cache_mounts_block(text: str) -> str:
    """Return the text of the CACHE_MOUNTS=( ... ) array, inclusive of delimiters."""
    start = text.index(_CACHE_MOUNTS_OPEN)
    # Walk forward to find the closing ')' on its own line.
    end = text.index("\n)\n", start)
    return text[start : end + 3]


# ---------------------------------------------------------------------------
# mkdir guard: host dir created before CACHE_MOUNTS block
# ---------------------------------------------------------------------------


def test_bundle_mkdir_present_before_cache_mounts():
    """$HOME/.cache/leerie/bundle is mkdir-p'd before CACHE_MOUNTS is defined."""
    text = _launcher_text()

    assert _BUNDLE_MKDIR in text, (
        f"Expected '{_BUNDLE_MKDIR}' in launcher source — "
        "the host bundle cache directory is never created."
    )

    mkdir_pos = text.index(_BUNDLE_MKDIR)
    cache_mounts_pos = text.index(_CACHE_MOUNTS_OPEN)

    assert mkdir_pos < cache_mounts_pos, (
        f"mkdir of '{_BUNDLE_MKDIR}' (pos {mkdir_pos}) must appear "
        f"before CACHE_MOUNTS=( (pos {cache_mounts_pos})"
    )


# ---------------------------------------------------------------------------
# CACHE_MOUNTS content: volume mount
# ---------------------------------------------------------------------------


def test_bundle_volume_mount_in_cache_mounts():
    """-v ...:/home/leerie/.cache/leerie/bundle appears inside CACHE_MOUNTS."""
    text = _launcher_text()
    block = _extract_cache_mounts_block(text)

    assert _BUNDLE_VOLUME_TARGET in block, (
        f"Expected a volume mount targeting '{_BUNDLE_VOLUME_TARGET}' inside "
        f"CACHE_MOUNTS. Got block:\n{block}"
    )

    # The entry must be a -v flag (not an -e env or incidental comment).
    volume_lines = [
        line.strip()
        for line in block.splitlines()
        if _BUNDLE_VOLUME_TARGET in line and not line.strip().startswith("#")
    ]
    assert any(line.startswith("-v") for line in volume_lines), (
        f"Expected a '-v ...' line containing '{_BUNDLE_VOLUME_TARGET}'. "
        f"Matching lines: {volume_lines}"
    )


# ---------------------------------------------------------------------------
# CACHE_MOUNTS content: BUNDLE_PATH env var
# ---------------------------------------------------------------------------


def test_bundle_path_env_in_cache_mounts():
    """-e BUNDLE_PATH=.../bundle appears inside CACHE_MOUNTS."""
    text = _launcher_text()
    block = _extract_cache_mounts_block(text)

    assert _BUNDLE_PATH_ENV in block, (
        f"Expected '{_BUNDLE_PATH_ENV}' inside CACHE_MOUNTS. "
        f"Got block:\n{block}"
    )

    # The entry must be a -e flag (environment variable, not a comment).
    env_lines = [
        line.strip()
        for line in block.splitlines()
        if _BUNDLE_PATH_ENV in line and not line.strip().startswith("#")
    ]
    assert any(line.startswith("-e") for line in env_lines), (
        f"Expected a '-e ...' line containing '{_BUNDLE_PATH_ENV}'. "
        f"Matching lines: {env_lines}"
    )
