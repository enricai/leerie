"""Shared launcher-text extraction helpers for the --log-file wiring tests
(test_log_file_wiring.py, test_log_file_persistence.py). Both files
independently defined byte-identical `_extract_setup_block`,
`_extract_invocation`, and `_extract_reap_tail` -- this module is their
single owner, following the `launcher_blocks.py` / `ec2_stub.py`
single-owner convention documented in CLAUDE.md.

The plain `_extract(start_marker, end_marker)` marker-slicing primitive is
also owned here and imported by `test_log_file_wiring.py`
(`_extract_mkdir_line` needs it directly, not just the three helpers
above). `test_log_file_persistence.py` keeps its own variant rather than
importing this one -- its version routes through a local `_launcher_text()`
wrapper for its own `_extract_resolver()`, so the two are not
byte-identical and folding them together would be a cosmetic rename, not a
dedup.
"""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = REPO_ROOT / "leerie"


def _extract(start_marker: str, end_marker: str) -> str:
    src = LAUNCHER.read_text()
    s = src.index(start_marker)
    e = src.index(end_marker, s) + len(end_marker)
    return src[s:e]


def extract_setup_block() -> str:
    """The `_run_log=...` / `_log_tee_target=...` resolution block. Computed
    independent of `$_run_log` (bugfix-005) so the interactive/-it branch
    can wire it too."""
    return _extract(
        '  _run_log=""\n',
        '  _log_tee_target=""\n  if [ -n "${LEERIE_LOG_FILE_RESOLVED:-}" ]; then\n'
        '    if : >> "$LEERIE_LOG_FILE_RESOLVED" 2>/dev/null; then\n'
        '      _log_tee_target="$LEERIE_LOG_FILE_RESOLVED"\n'
        "    fi\n"
        "  fi\n",
    )


def extract_reap_tail() -> str:
    return _extract("  _reap_tail() {\n", "  }\n")


def extract_invocation() -> str:
    """The full `if [ -n "$_run_log" ]; then ... fi` block: the piped
    tail/tee branch, the `script`-wrapped interactive/-it branch
    (bugfix-005), and the unwrapped fallback."""
    return _extract(
        '  if [ -n "$_run_log" ]; then\n    # Decoupled: nerdctl',
        '    nerdctl run "${_run_argv[@]}" || container_rc=$?\n  fi\n',
    )
