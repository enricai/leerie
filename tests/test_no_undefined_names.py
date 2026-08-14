"""No name in this repo's Python may be undefined at the point it is read.

`ast.parse` — CLAUDE.md's task-completion static check, and the whole of
`.github/workflows/syntax.yml` — cannot see this class: an undefined name is
syntactically perfect and fails only when the line executes. v0.20.0 shipped
`NameError: name 'repo_root' is not defined` in the fresh-run branch of
`_run_phases`, which the suite could not catch either (no test executed that
branch; see `tests/test_run_phases_fresh_init.py`), and which a one-second
scan of this shape catches anywhere in the module regardless of coverage.

Implemented on stdlib `symtable` rather than adding `ruff`/`pyflakes`:
CLAUDE.md's stance is that pytest is the sole dev dependency, and a check that
lives in the suite runs on every contributor's machine rather than only in CI.
The rule is the same one ruff's F821 applies — a symbol that is *referenced*,
resolves to *global* scope, and is bound neither at module level nor in
`builtins` will raise `NameError` when that line runs.

Measured when this file landed: 1 finding in `orchestrator/leerie.py` — the
v0.20.0 defect — and 0 everywhere else it looks. Stated without a file count
on purpose: that number moves with every test added, so a figure written here
is stale by the next commit and says nothing the scan does not re-derive.
"""

from __future__ import annotations

import builtins
import symtable
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

# Bound by the interpreter itself at module execution, so they are never
# assigned in source and symtable cannot see them. Not a general escape
# hatch — every entry here is injected by CPython's import machinery.
_INTERPRETER_GLOBALS = frozenset({
    "__file__", "__name__", "__doc__", "__package__", "__spec__",
    "__loader__", "__builtins__", "__debug__",
})


def undefined_names(source: str, filename: str) -> list[tuple[str, str]]:
    """Return `(scope_path, name)` for every name that would raise
    `NameError` when its line executes.

    A file that does not parse is reported as a `<syntaxerror>` scope rather
    than raising: this runs over every `.py` in the repo, and an escaping
    `SyntaxError` fails the whole scan as a bare traceback that names no file.
    """
    try:
        top = symtable.symtable(source, filename, "exec")
    except SyntaxError as e:
        return [("<syntaxerror>", f"{e.msg} (line {e.lineno})")]

    bound = set(dir(builtins)) | set(_INTERPRETER_GLOBALS)
    for sym in top.get_symbols():
        # `is_namespace()` covers `def`/`class`; the other two cover
        # assignment and every form of import.
        if sym.is_assigned() or sym.is_imported() or sym.is_namespace():
            bound.add(sym.get_name())

    # A `global X` + assignment inside any function binds the module-level
    # name even when X appears at module scope nowhere else, so a scan that
    # collects only module-scope bindings false-positives on every read of it.
    # Collected in a pre-pass because the binding scope can be walked after
    # the reading scope.
    #
    # PROVABLY INERT ON THIS TREE, and deliberately kept: run the scan with
    # and without this pre-pass and the real module yields [] both ways. Every
    # module global leerie.py mutates under `global` is ALSO bound at module
    # scope (`_last_parse_error` at :9093, `_STRICT_PROXY` at :13070, both
    # annotated assignments), which is the ordinary shape. The pattern this
    # guards is real Python that this repo simply does not contain today —
    # `test_no_false_positives[global declared then assigned]` is the only
    # thing exercising it, and that case fails without the pre-pass.
    def collect_globals(table: symtable.SymbolTable) -> None:
        for sym in table.get_symbols():
            if sym.is_global() and sym.is_assigned():
                bound.add(sym.get_name())
        for child in table.get_children():
            collect_globals(child)

    collect_globals(top)

    found: list[tuple[str, str]] = []

    def walk(table: symtable.SymbolTable, path: str) -> None:
        for sym in table.get_symbols():
            # is_global() is True for GLOBAL_IMPLICIT and GLOBAL_EXPLICIT —
            # i.e. names CPython will look up in module globals at runtime.
            # A free variable (closure) reports is_free(), not is_global(),
            # so enclosing-scope reads are correctly ignored.
            if (sym.is_referenced() and sym.is_global()
                    and not sym.is_assigned()
                    and sym.get_name() not in bound):
                found.append((path, sym.get_name()))
        for child in table.get_children():
            walk(child, f"{path}.{child.get_name()}")

    for child in top.get_children():
        walk(child, child.get_name())
    return sorted(set(found))


def _python_files() -> list[Path]:
    files = [REPO_ROOT / "orchestrator" / "leerie.py"]
    files += sorted((REPO_ROOT / "orchestrator").rglob("*.py"))
    files += sorted((REPO_ROOT / "chain").rglob("*.py"))
    files += sorted((REPO_ROOT / "tests").rglob("*.py"))
    files += sorted((REPO_ROOT / "scripts").rglob("*.py"))
    # dict.fromkeys: dedupe while keeping order stable for the failure text.
    return list(dict.fromkeys(f for f in files if f.exists()))


def test_no_undefined_names_anywhere():
    offenders: list[str] = []
    for path in _python_files():
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for scope, name in undefined_names(source, str(path)):
            rel = path.relative_to(REPO_ROOT)
            offenders.append(f"{rel}: {scope}(): {name}")

    assert not offenders, (
        "undefined name(s) — these raise NameError when the line executes, "
        "and ast.parse cannot see them:\n  " + "\n  ".join(offenders))


def test_the_scan_finds_files_at_all():
    """Anti-vacuity: a scan that walks nothing certifies 'clean' forever."""
    files = _python_files()
    assert len(files) > 100, f"only {len(files)} files scanned"
    assert (REPO_ROOT / "orchestrator" / "leerie.py") in files


def test_the_scan_fires_on_an_injected_defect():
    """Anti-vacuity partner: prove the rule can fail, on the real module.

    Reproduces the v0.20.0 shape — a name read inside a function that is
    bound nowhere — rather than a synthetic snippet, so a future refactor of
    `undefined_names` that quietly stops analysing this file fails here.
    """
    source = (REPO_ROOT / "orchestrator" / "leerie.py").read_text()
    injected = source.replace(
        "def _read_version(",
        "def _canary_scope():\n"
        "    return _leerie_undefined_canary\n\n\n"
        "def _read_version(",
        1)
    assert injected != source, "anchor for the injection has moved"

    found = undefined_names(injected, "leerie.py")
    assert ("_canary_scope", "_leerie_undefined_canary") in found


@pytest.mark.parametrize("label,source", [
    ("closure reads enclosing scope",
     "def outer():\n    x = 1\n    def inner():\n        return x\n"),
    ("module constant read in a function",
     "CONST = 1\n\n\ndef f():\n    return CONST\n"),
    ("annotated module constant",
     "CONST: int = 1\n\n\ndef f():\n    return CONST\n"),
    ("name bound only inside an if at module level",
     "import sys\nif sys.platform:\n    X = 1\n\n\ndef f():\n    return X\n"),
    ("global declared then assigned",
     "def f():\n    global Y\n    Y = 1\n\n\ndef g():\n    return Y\n"),
    ("comprehension variable",
     "def f(items):\n    return [i for i in items if i]\n"),
    ("except-as binding",
     "def f():\n    try:\n        pass\n    except OSError as e:\n"
     "        return e\n"),
    ("builtin",
     "def f(x):\n    return len(x)\n"),
])
def test_no_false_positives(label, source):
    """The shapes that a naive scan flags. Each of these was a false positive
    in the ad hoc scan used to triage the incident; symtable resolves them."""
    assert undefined_names(source, "x.py") == [], label


def test_a_genuinely_undefined_name_is_reported():
    """The positive control for the parametrized negatives above — without
    it, a scan that returns `[]` unconditionally passes all of them."""
    assert undefined_names(
        "def f():\n    return nope\n", "x.py") == [("f", "nope")]


def test_an_unparseable_file_is_reported_not_raised():
    """Guards the failure *message*, not coverage: `ast.parse` in the same
    workflow already fails on such a file, but an escaping SyntaxError here
    would take down the whole-repo scan without naming the file."""
    found = undefined_names("def f(:\n", "broken.py")
    assert len(found) == 1
    assert found[0][0] == "<syntaxerror>"
