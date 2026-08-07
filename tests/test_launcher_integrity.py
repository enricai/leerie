"""The `leerie` launcher parses — nothing else in CI checks that.

Verified when this file was written:

- `.github/workflows/shellcheck.yml` lints `scripts/*.sh` only. The launcher has
  no `.sh` extension and does not live in `scripts/`, so it is not covered.
- `.github/workflows/syntax.yml` AST-parses `orchestrator/leerie.py` and
  `tests/**/*.py` — Python only.
- No test runs shellcheck. Every "shellcheck" occurrence under `tests/` is prose
  in a docstring describing this gap, most explicitly
  `test_ec2_launcher_finalize.py`: *"Nothing else covers this: CI's shellcheck
  job lints `scripts/...`"*.

So a `bash -n`-level syntax error in the launcher would ship green were it not
for this file. That check previously lived as one method inside
`test_leerie_commit.py`, whose subject is a single state field — coverage that
existed incidentally, and would have vanished silently the moment anyone
restructured that file. It has a home of its own now.

**This is not sufficient, and the shortfall is documented rather than implied.**
`bash -n` does not catch the backtick class: a balanced pair inside what reads as
a comment is parsed by bash as command substitution, printing a spurious syntax
error and silently dropping that comment's text from the script actually sent to
a remote machine. leerie has shipped that defect once (see CLAUDE.md, and
`tests/test_bedrock_bearer_token.py::test_child_env_heredoc_body_has_no_backtick_characters`,
which guards one heredoc body against it specifically). It was caught by diffing
`shellcheck -x leerie` output, with `bash -n` clean throughout. Linting the whole
launcher with shellcheck is the real fix; it needs a measured baseline of
pre-existing findings first, which is why it is not attempted here.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = REPO_ROOT / "leerie"
TESTS_DIR = Path(__file__).resolve().parent

# What a launcher syntax check looks like, whoever writes it. Matched against
# test sources so the guard below finds the check wherever it lives rather than
# hard-coding this file's name — the same derive-don't-enumerate discipline as
# `tests/launcher_blocks.py`.
_BASH_N = re.compile(r'"bash",\s*"-n"')


def test_launcher_parses() -> None:
    """`bash -n leerie` — the launcher is a 7k-line script CI never lints."""
    r = subprocess.run(["bash", "-n", str(LAUNCHER)],
                       capture_output=True, text=True)
    assert r.returncode == 0, f"leerie does not parse:\n{r.stderr}"


def _files_checking_launcher_syntax() -> list[str]:
    """Test files that run `bash -n` against the launcher specifically.

    Requires both the invocation shape and a reference to the launcher path in
    the same file — `container-entry.sh` is also `bash -n`-checked elsewhere,
    and that must not be mistaken for coverage of the launcher.
    """
    out: list[str] = []
    for path in sorted(TESTS_DIR.rglob("*.py")):
        text = path.read_text(errors="replace")
        if _BASH_N.search(text) and 'LAUNCHER' in text or (
                _BASH_N.search(text) and '"leerie"' in text):
            out.append(path.name)
    return out


def test_launcher_syntax_check_is_not_silently_dropped() -> None:
    """Someone must still be running `bash -n` on the launcher.

    Derived by scanning `tests/` rather than asserting on this file's own name,
    so moving the check again is fine and deleting it is not.
    """
    checkers = _files_checking_launcher_syntax()
    assert checkers, (
        "no test runs `bash -n` against the launcher any more. CI does not "
        "check it either — shellcheck.yml lints scripts/*.sh and syntax.yml is "
        "Python-only — so a syntax error in `leerie` would now ship green.")


def test_the_scan_finds_this_file() -> None:
    """Anti-vacuity: a scan matching nothing would certify coverage forever."""
    assert Path(__file__).name in _files_checking_launcher_syntax(), (
        "the launcher-syntax-check scan does not find this file, so its result "
        "above is meaningless — the scan is broken, not the coverage")
