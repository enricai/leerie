"""Coupling test: the leerie launcher's CACHE_MOUNTS array contains the Ruby
bundle cache volume mount and BUNDLE_PATH env var, and the host cache dir is
created (mkdir -p) before the array is defined.

This test reads the launcher source text directly — no subprocess execution —
so any refactor that silently drops the bundle lines causes an immediate test
failure rather than a silent regression where every `bundle install` downloads
and compiles gems from scratch.

`BUNDLE_PATH` is not part of the `CACHE_MOUNTS=( ... )` array literal itself:
since commit 241259b (out-of-repo dependency bake, DESIGN §6½), the correct
value depends on whether the per-repo Dockerfile actually baked gems to
/opt/bundle, so it's appended conditionally via `CACHE_MOUNTS+=(...)` in an
if/else AFTER the literal closes — not something a static array can express.
`_extract_cache_mounts_block()` therefore also scoops up any
`CACHE_MOUNTS+=( ... )` append blocks that directly follow the literal
(tolerating the if/else/fi control-flow lines between them), so both the
/opt/bundle and cache-dir BUNDLE_PATH values are visible to the tests below.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER_PATH = REPO_ROOT / "leerie"

_BUNDLE_VOLUME_TARGET = "/home/leerie/.cache/leerie/bundle"
_BUNDLE_PATH_ENV = "BUNDLE_PATH=/home/leerie/.cache/leerie/bundle"
_BUNDLE_PATH_BAKED_ENV = "BUNDLE_PATH=/opt/bundle"
_BUNDLE_MKDIR = "$HOME/.cache/leerie/bundle"
_CACHE_MOUNTS_OPEN = "CACHE_MOUNTS=("

# Matches a `CACHE_MOUNTS+=( ... )` append — either a single physical line
# (as leerie's BUNDLE_PATH if/else arms are: `  CACHE_MOUNTS+=(-e "...")`,
# indented inside the if/else body) or a multi-line block closed by `)` on
# its own line, mirroring the array-literal closing convention. re.MULTILINE
# so `^` anchors to start of line (tolerating leading indentation before
# `CACHE_MOUNTS+=`, and guarding against matching the token inside a
# comment, which would need a `#` before it on the same line — not excluded
# here since no such comment currently exists, but the anchor at least keeps
# matches to real statement starts).
_CACHE_MOUNTS_APPEND_RE = re.compile(
    r"^[ \t]*CACHE_MOUNTS\+=\((?:[^\n)]*\)|.*?\n\))\n", re.MULTILINE | re.DOTALL)


def _launcher_text() -> str:
    return LAUNCHER_PATH.read_text()


def _extract_cache_mounts_block(text: str) -> str:
    """Return the CACHE_MOUNTS=( ... ) array literal PLUS any
    CACHE_MOUNTS+=( ... ) append blocks that directly follow it (skipping
    over intervening non-append text such as if/else/fi control flow and
    comments) — the array's true runtime contents span both."""
    start = text.index(_CACHE_MOUNTS_OPEN)
    # Walk forward to find the closing ')' on its own line.
    end = text.index("\n)\n", start)
    block = text[start : end + 3]

    tail = text[end + 3:]
    for m in _CACHE_MOUNTS_APPEND_RE.finditer(tail):
        block += m.group(0)

    return block


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

    # The entry must be a -e flag (environment variable, not a comment) —
    # either directly (a line inside the CACHE_MOUNTS=( ... ) literal starts
    # with -e) or via a CACHE_MOUNTS+=(-e "...") append (DESIGN §6½'s
    # conditional BUNDLE_PATH arms), which starts with `CACHE_MOUNTS+=(-e`
    # rather than a bare `-e` token.
    env_lines = [
        line.strip()
        for line in block.splitlines()
        if _BUNDLE_PATH_ENV in line and not line.strip().startswith("#")
    ]
    assert any(
        line.startswith("-e") or line.startswith('CACHE_MOUNTS+=(-e')
        for line in env_lines
    ), (
        f"Expected a '-e ...' or 'CACHE_MOUNTS+=(-e ...)' line containing "
        f"'{_BUNDLE_PATH_ENV}'. Matching lines: {env_lines}"
    )


def test_both_bundle_path_arms_visible_to_extraction():
    """BUNDLE_PATH is appended conditionally (CACHE_MOUNTS+=(...) in an
    if/else, DESIGN §6½ out-of-repo bake) rather than living in the
    CACHE_MOUNTS=( ... ) array literal itself. Pin that the extraction
    helper reaches past the `if` arm into the `else` arm too — a
    regression here (e.g. stopping at the first CACHE_MOUNTS+=(...)
    block) would pass test_bundle_path_env_in_cache_mounts vacuously if
    only the first arm happened to match, while silently missing the
    other."""
    text = _launcher_text()
    block = _extract_cache_mounts_block(text)

    assert _BUNDLE_PATH_BAKED_ENV in block, (
        f"Expected the baked-gems arm '{_BUNDLE_PATH_BAKED_ENV}' inside the "
        f"extracted block (the `if` arm of the BUNDLE_PATH conditional). "
        f"Got block:\n{block}"
    )
    assert _BUNDLE_PATH_ENV in block, (
        f"Expected the cache-dir arm '{_BUNDLE_PATH_ENV}' inside the "
        f"extracted block (the `else` arm of the BUNDLE_PATH conditional). "
        f"Got block:\n{block}"
    )
