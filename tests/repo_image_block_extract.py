"""Shared _extract_block helper for the per-repo image test harnesses.

Used by test_launcher_per_repo_image.py, test_fly_per_repo_image.py, and
test_build_repo_image.py, whose own copies were verified byte-identical
(AST-diff, body minus docstring). test_no_verify_push_env_seed.py and
test_resolve_log_file.py define their own distinct _extract_block and are
deliberately not consolidated here.
"""
from __future__ import annotations


def extract_block(text: str, start_marker: str, end_marker: str) -> str:
    """Return the text between start_marker and end_marker (exclusive)."""
    s = text.index(start_marker)
    e = text.index(end_marker, s)
    return text[s:e]
