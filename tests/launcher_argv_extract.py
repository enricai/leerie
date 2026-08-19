"""Single owner of `_run_argv` extraction from the launcher.

`tests/test_launcher_state_mount.py` and `tests/test_launcher_env_forwarding.py`
both need the real `nerdctl run` argv array construction, extracted verbatim
from `leerie` rather than reproduced — the exact hazard
`tests/test_no_duplicate_launcher_blocks.py`'s docstring names by example
(`test_launcher_state_mount.py` previously shipped a stale, incomplete
reproduction of this block). Keeping one extraction function importable by
both means the two files cannot silently diverge.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = REPO_ROOT / "leerie"


def extract_run_argv() -> str:
    """The real `nerdctl run` argv array, lifted verbatim from the launcher."""
    src = LAUNCHER.read_text()
    m = re.search(r"(  _run_argv=\(\n.*?\n  \)\n)", src, re.DOTALL)
    assert m, "could not locate the _run_argv array in the launcher"
    return m.group(1)
