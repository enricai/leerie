"""The `leerie` launcher parses — nothing else in CI checks that.

Verified when this file was written:

- `.github/workflows/shellcheck.yml` lints `scripts/*.sh` and
  `scripts/remote/*.sh` (widened 2026-08-18 — it was single-level, so the 19
  remote scripts fired the trigger and were never linted). The launcher has
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

import ast
import subprocess
import warnings
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = REPO_ROOT / "leerie"
TESTS_DIR = Path(__file__).resolve().parent

# Names that, appearing inside the SAME argv list as `bash -n`, identify the
# launcher as the thing being checked.
_LAUNCHER_REFS = ("LAUNCHER", "leerie")


def test_launcher_parses() -> None:
    """`bash -n leerie` — the launcher is a 7k-line script CI never lints."""
    r = subprocess.run(["bash", "-n", str(LAUNCHER)],
                       capture_output=True, text=True)
    assert r.returncode == 0, f"leerie does not parse:\n{r.stderr}"


def _checks_launcher_syntax(argv: ast.expr) -> bool:
    """True when `argv` is a list literal running `bash -n` on the launcher.

    Both facts must hold *within the same list*. An earlier version tested for
    `bash -n` and a launcher reference anywhere in the same file, which is not
    evidence the two are connected: a file running `bash -n` against something
    else while separately mentioning `LAUNCHER` would have counted as coverage,
    letting the guard below pass while the real check was gone — precisely the
    failure it exists to catch. `container-entry.sh` is `bash -n`-checked in
    `test_container_entry_run_id.py` and must not be mistaken for the launcher.
    """
    if not isinstance(argv, ast.List):
        return False
    literals = {e.value for e in argv.elts
                if isinstance(e, ast.Constant) and isinstance(e.value, str)}
    if not {"bash", "-n"} <= literals:
        return False
    return any(ref in ast.unparse(argv) for ref in _LAUNCHER_REFS)


def _files_checking_launcher_syntax() -> list[str]:
    """Test files that run `bash -n` against the launcher specifically.

    Structural rather than textual, matching what this repo reaches for when
    the *shape* of a call is the thing being asserted (`test_state_fields`'s
    `st.data` write sweep, `test_claude_p_call_sites`, the `args.resume` branch
    walk in `test_leerie_commit.py`).
    """
    out: list[str] = []
    for path in sorted(TESTS_DIR.rglob("*.py")):
        try:
            # Warnings suppressed: parsing every test file surfaces other
            # files' SyntaxWarnings, at least one of which is deliberate and
            # documented as not-to-be-fixed (test_ec2_seed_repo.py's `\/`,
            # where the surrounding bash relies on Python collapsing the
            # escape). CI's syntax.yml already parses them all; this guard
            # should not re-report someone else's intentional choice.
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                tree = ast.parse(path.read_text(errors="replace"),
                                 filename=str(path))
        except SyntaxError:  # not ours to police; syntax.yml covers it
            continue
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and node.args
                    and getattr(node.func, "attr", getattr(node.func, "id", None))
                    == "run"
                    and _checks_launcher_syntax(node.args[0])):
                out.append(path.name)
                break
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
