"""`tests/source_strip.py` is the single owner of comment/docstring stripping.

Ten test files carried a copy, every copy shared three defects, and every copy
answered the resulting `SyntaxError` by returning the input unchanged — so five
files reading `orchestrator/leerie.py` had been scanning the very prose they
were written to exclude, with nothing failing.

This file pins the owner's contract and keeps it the only implementation. It is
one of the family CLAUDE.md records — `tests/test_no_duplicate_state_walks.py`,
`tests/test_no_duplicate_launcher_blocks.py`, `tests/launcher_blocks.py` — and
for the sharper of the two reasons those give: a drifted copy here does not
produce a *wrong* answer, it produces a *vacuous* one.
"""
from __future__ import annotations

import ast
import inspect
import warnings
from pathlib import Path

import pytest

from tests.source_strip import code_only, shell_code_only, strip_comments

REPO_ROOT = Path(__file__).resolve().parent.parent
OWNER = REPO_ROOT / "tests" / "source_strip.py"
PY_SURFACE = (
    [REPO_ROOT / "orchestrator" / "leerie.py"]
    + sorted((REPO_ROOT / "chain").rglob("*.py"))
    + sorted((REPO_ROOT / "scripts").rglob("*.py"))
)


# ===========================================================================
# The three defects, each pinned by the shape that exposed it
# ===========================================================================

def test_a_backslash_continuation_survives():
    """Defect 1. A `\\` is not a token, so a token re-emitter drops it and the
    statement comes back cut in half. `leerie.py:4312` is one, which is why
    the whole module failed to parse and every copy silently degraded."""
    ns: dict = {}
    exec(compile(code_only("x = 1 + \\\n    2\n"), "<t>", "exec"), ns)
    assert ns["x"] == 3, "the continuation was dropped and the statement split"


def test_an_fstring_survives():
    """Defect 2. Since 3.12 an f-string tokenizes into START/MIDDLE/expression
    parts, and re-emitting those by column arithmetic does not reproduce the
    original — measured, `f"{_id:<{w_id}}"` came back as `f"{ :<{w_id}}"`."""
    src = 'w_id = 3\n_id = "a"\nfmt = f"{_id:<{w_id}}  {_id!r}"\n'
    assert code_only(src) == src            # nothing to strip, nothing changed
    ast.parse(code_only(src))


def test_a_docstring_is_removed_by_position_not_by_substring():
    """Defect 3. `text.replace(doc, "", 1)` deletes that text ANYWHERE, so a
    docstring whose wording also appears in code eats the code instead."""
    src = 'def f():\n    "total"\n    return "total"\n'
    out = code_only(src)
    assert 'return "total"' in out, out
    assert out.count("total") == 1, out


def test_a_non_ascii_docstring_does_not_overrun():
    """`ast` reports columns in UTF-8 BYTES and `tokenize` in characters.

    Mixing them is silent and off-by-N-per-non-ASCII-character:
    `chain/__init__.py`'s em dash made `end_col_offset` overshoot the line by
    two and blank the first two characters of the statement below.
    """
    src = '"""chain — a subsystem."""\n__version__ = "0.1.0"\n'
    out = code_only(src)
    assert '__version__ = "0.1.0"' in out, out
    ast.parse(out)


def test_it_raises_rather_than_returning_the_input():
    """The fail-open that made all of the above invisible.

    Returning `src` on failure is indistinguishable from a successful strip at
    every call site, so a caller cannot tell "stripped" from "gave up".
    """
    with pytest.raises(SyntaxError):
        code_only("def f(:\n")


# ===========================================================================
# The contract every caller relies on
# ===========================================================================

@pytest.mark.parametrize("path", PY_SURFACE, ids=lambda p: p.name)
def test_the_result_still_parses(path):
    """A stripper whose output does not parse is the fail-open's precondition."""
    src = path.read_text()
    for fn in (strip_comments, code_only):
        ast.parse(fn(src))


@pytest.mark.parametrize("path", PY_SURFACE, ids=lambda p: p.name)
def test_positions_are_preserved(path):
    """Length and line count unchanged, so a reported line number is the real
    one and independently-computed spans stay valid."""
    src = path.read_text()
    for fn in (strip_comments, code_only):
        out = fn(src)
        assert len(out) == len(src), fn.__name__
        assert out.count("\n") == src.count("\n"), fn.__name__


def test_it_actually_strips():
    """Anti-vacuity. Every assertion above is satisfied by a function that
    returns its input — which is exactly what the broken copies did."""
    src = (REPO_ROOT / "orchestrator" / "leerie.py").read_text()
    out = code_only(src)
    assert "Scoped replacement for a bare" not in out, "docstring survived"
    assert "gettext-translated" not in out, "comment survived"
    assert "def _prune_leerie_worktrees(" in out, "code was destroyed"


def test_a_hash_inside_a_string_is_not_a_comment():
    src = 'x = "# not a comment"  # this one is\n'
    out = strip_comments(src)
    assert "# not a comment" in out
    assert "this one is" not in out


def test_nested_getsource_is_dedented():
    """`inspect.getsource` of a nested function carries the enclosing
    indentation and would not parse. Every caller passes exactly that."""
    def outer():
        def inner():
            """doc"""
            return 1
        return inner
    ast.parse(code_only(inspect.getsource(outer())))


def test_shell_stripper_removes_a_trailing_comment():
    assert "never here" not in shell_code_only('run "$X"  # never here\n')


def test_shell_stripper_leaves_a_parameter_expansion_alone():
    assert "${VAR#prefix}" in shell_code_only('echo "${VAR#prefix}"\n')


# ===========================================================================
# Single owner
# ===========================================================================

def _definers() -> list[str]:
    """Test files defining their own stripper instead of importing one."""
    out = []
    for path in sorted((REPO_ROOT / "tests").glob("*.py")):
        if path == OWNER:
            continue
        try:
            # Reading every test file surfaces other files' SyntaxWarnings,
            # at least one deliberate and documented as not-to-be-fixed
            # (`test_ec2_seed_repo.py`'s `\/`). Same suppression as
            # `test_launcher_integrity.py`.
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                tree = ast.parse(path.read_text())
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if (isinstance(node, ast.FunctionDef)
                    and node.name.lstrip("_") in
                    ("code_only", "strip_comments", "shell_code_only")):
                out.append(f"{path.name}:{node.lineno}:{node.name}")
    return out


def test_no_test_file_defines_its_own_stripper():
    """Two copies of a RULE drift the way two copies of a list do, and this
    rule's drift is invisible: a broken copy returns the unstripped input and
    every assertion built on it passes."""
    offenders = _definers()
    assert not offenders, (
        "import from tests.source_strip instead:\n  " + "\n  ".join(offenders))


def test_the_owner_has_real_importers():
    """Anti-vacuity for the guard above: a scan finding no definitions would
    certify "single owner" even if nothing imported the owner either."""
    importers = [p.name for p in sorted((REPO_ROOT / "tests").glob("*.py"))
                 if "from tests.source_strip import" in p.read_text()
                 and p != OWNER]
    assert len(importers) >= 8, importers


def test_the_scan_can_find_a_definition(tmp_path, monkeypatch):
    """Anti-vacuity for the scan itself: a predicate that matches nothing
    reports "no duplicates" forever."""
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text(
        "def _code_only(src):\n    return src\n")
    monkeypatch.setattr("tests.test_source_strip.REPO_ROOT", tmp_path)
    monkeypatch.setattr("tests.test_source_strip.OWNER",
                        tmp_path / "tests" / "source_strip.py")
    assert _definers() == ["test_x.py:1:_code_only"]
