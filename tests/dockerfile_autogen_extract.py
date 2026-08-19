"""Shared extraction of the per-repo derived-image block from the launcher.

Single owner for `_extract_autogen_block`, previously duplicated
byte-identically in tests/test_dockerfile_autogen.py and
tests/test_dockerfile_bake_from_capture.py.
"""


def extract_autogen_block(text: str) -> str:
    """Extract the per-repo image block from the launcher verbatim."""
    marker_start = "# --- per-repo derived image (local nerdctl) "
    marker_end = "\n# --- translate --inspect-dir paths"
    s = text.index(marker_start)
    e = text.index(marker_end, s)
    return text[s:e]
