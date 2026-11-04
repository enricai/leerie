"""`conftest.source_segments` is `ast.get_source_segment` with the source split
once, and it is the only way a test takes a node's source.

`ast.get_source_segment` re-splits its whole source on every call; on Python
3.10 and 3.11 the split is a pure-Python loop over every character. A
collection-time loop of calls over leerie.py's ~1,700 functions
(tests/test_needs_human_sources_render.py) held the 3.11 CI leg about 26
minutes and the 3.10 leg about 46 before their first test, on run
41777003910. The rule below is derived from the call's shape, not from the
files that made it slow, so a new loop over a large module is caught however
it is spelled.
"""
from __future__ import annotations

import ast
from pathlib import Path

from tests.conftest import source_segments

TESTS = Path(__file__).resolve().parent
LEERIE_PY = TESTS.parent / "orchestrator" / "leerie.py"
# The oracle: this file compares the helper against the real thing.
ALLOWED = {Path(__file__).name}

# Every shape the stdlib's own splitter decides: \r\n, a bare \r, a form feed
# and U+2028 inside a line (neither is a line break to `ast`, both are to
# `str.splitlines`), a multi-byte character before a node (the offsets are
# UTF-8 bytes), a one-line node, a multi-line one, and no final newline.
SYNTHETIC = (
    "def f(x):\r\n"
    "    s = 'é\u2028ü'; t = {'a': [1,\r\n"
    "        2]}\r"
    "    \x0c\n"
    "    return (x +\n"
    "            len(s)) if t else None\n"
    "class C:\n"
    "    y = f(\n"
    "        'ñ')"
)


def _positioned(tree: ast.AST) -> list[ast.AST]:
    return [n for n in ast.walk(tree)
            if getattr(n, "end_lineno", None) is not None]


def test_it_is_the_stdlib_segment_on_every_node():
    seg = source_segments(SYNTHETIC)
    nodes = _positioned(ast.parse(SYNTHETIC))
    assert len(nodes) > 20
    for n in nodes:
        assert seg(n) == ast.get_source_segment(SYNTHETIC, n), ast.dump(n)
    # Multi-line nodes are where a wrong split would show.
    assert any(n.end_lineno > n.lineno for n in nodes)


def test_it_is_the_stdlib_segment_on_leerie_py():
    """A few functions across the real module (each oracle call re-splits it,
    so a few): the first, one in the middle, the last."""
    src = LEERIE_PY.read_text()
    fns = [n for n in ast.walk(ast.parse(src))
           if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    seg = source_segments(src)
    for n in (fns[0], fns[len(fns) // 2], fns[-1]):
        assert seg(n) == ast.get_source_segment(src, n)


def test_a_node_without_a_position_has_no_segment():
    seg = source_segments(SYNTHETIC)
    assert seg(ast.Name(id="x")) is None
    assert ast.get_source_segment(SYNTHETIC, ast.Name(id="x")) is None


def _calls_of_get_source_segment(path: Path) -> list[int]:
    tree = ast.parse(path.read_text(), filename=str(path))
    out = []
    for n in ast.walk(tree):
        if not isinstance(n, ast.Call):
            continue
        f = n.func
        name = (f.attr if isinstance(f, ast.Attribute)
                else f.id if isinstance(f, ast.Name) else None)
        if name == "get_source_segment":
            out.append(n.lineno)
    return out


def test_no_test_calls_get_source_segment():
    found = {p.name: _calls_of_get_source_segment(p)
             for p in sorted(TESTS.rglob("*.py"))}
    offenders = {k: v for k, v in found.items() if v and k not in ALLOWED}
    assert not offenders, (
        f"{offenders}: `ast.get_source_segment` re-splits its whole source "
        "per call (a character loop on Python 3.10/3.11); take segments "
        "through tests.conftest.source_segments, which splits once")
    # Anti-vacuity: the scan sees this file's own oracle calls.
    assert found[Path(__file__).name]
