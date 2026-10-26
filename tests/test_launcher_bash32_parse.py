"""The launcher must parse under bash 3.2 (macOS /bin/bash).

CI is ubuntu-latest (bash 5.x), so it cannot catch a construct that only the
old parser mis-scans. This one sat in the launcher unnoticed: `/bin/bash -n
leerie` failed with `syntax error near unexpected token '('` at a line that
was itself fine. Bash 3.2 pre-scans a `$( ... )` body for quotes without
understanding heredocs, so an apostrophe in a heredoc body nested in `$(`
(here a Python comment, "the image's PATH") opens a phantom single-quoted
string and inverts the quote parity of everything after it, far from the
cause.

Two guards, because only one of them can run on CI:

- a static scan, run everywhere: no apostrophe in the body of a heredoc that
  is nested in `$(`;
- `/bin/bash -n leerie`, run only where `/bin/bash` is 3.2 (the same skip
  convention as tests/test_ec2_bash32_portability.py).
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from tests.test_ec2_bash32_portability import SYSTEM_BASH, _requires_bash32

REPO_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = REPO_ROOT / "leerie"

_HEREDOC_OPEN = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")


def _nested_heredoc_bodies() -> list[tuple[int, str, list[tuple[int, str]]]]:
    """(opener line, delimiter, [(line number, text)]) for each heredoc whose
    opener sits inside an unclosed `$(`."""
    lines = LAUNCHER.read_text().split("\n")
    found = []
    i = 0
    while i < len(lines):
        m = _HEREDOC_OPEN.search(lines[i])
        if m and not lines[i].lstrip().startswith("#"):
            word = m.group(2)
            j = i + 1
            while j < len(lines) and lines[j].strip() != word:
                j += 1
            context = " ".join(lines[max(0, i - 2):i + 1])
            if context.count("$(") > context.count(")") or "$(cat <<" in lines[i]:
                body = [(n + 1, lines[n]) for n in range(i + 1, j)]
                found.append((i + 1, word, body))
            i = j
        i += 1
    return found


def test_scan_finds_the_nested_heredocs():
    """Anti-vacuity: a scan that matches nothing passes forever. The launcher
    holds five heredocs nested in `$(` today."""
    assert len(_nested_heredoc_bodies()) >= 4


def test_no_apostrophe_in_a_heredoc_nested_in_command_substitution():
    offenders = [
        f"leerie:{n}: {text.strip()[:70]}"
        for _opener, _word, body in _nested_heredoc_bodies()
        for n, text in body if "'" in text
    ]
    assert not offenders, (
        "an apostrophe in a heredoc body nested in $( ... ) breaks the parse "
        "under bash 3.2 (macOS); reword it:\n  " + "\n  ".join(offenders))


def test_launcher_parses_under_bash32():
    _requires_bash32()
    r = subprocess.run([str(SYSTEM_BASH), "-n", str(LAUNCHER)],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
