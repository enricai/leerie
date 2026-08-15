"""The single owner of "Python source with comments and docstrings removed".

Ten test files carried a copy of this. It is the shape CLAUDE.md records for
`tests/launcher_blocks.py`: two copies of a *rule* drift exactly the way two
copies of a *list* do — and here every copy shared three defects, which
compounded into a silent fail-open.

**1. It dropped backslash line continuations.** A `\\` is not a token, so a
statement split across two physical lines came back as two statements.

**2. It could not round-trip an f-string.** Since 3.12 an f-string tokenizes
into `FSTRING_START` / `FSTRING_MIDDLE` / expression tokens, and re-emitting
those by column arithmetic does not reproduce the original.

**3. It removed docstrings by substring** — `text.replace(doc, "", 1)` deletes
the first occurrence of that text *anywhere in the file*, so a docstring whose
wording also appears in real code silently corrupts it.

Any of those makes the result unparseable, and the copies answered that with
`except SyntaxError: return text` — handing the caller **unstripped source**
while it believed the opposite. Five of the ten were reading
`orchestrator/leerie.py`, where defect 1 fires at line 4312, so every textual
assertion in them had been running against the comments and docstrings they
were written to exclude. Found when a new scan flagged the very docstring
explaining the rule it enforces.

The fix is to stop rewriting the file. Comment and docstring spans are blanked
**in place**, by position; every other byte, line number and column is
untouched by construction, so there is nothing left for an f-string or a
continuation to break. A docstring becomes `""` rather than nothing, because a
function whose body is only a docstring needs a statement to remain.

It raises rather than degrading. Returning the input on failure is
indistinguishable from a successful strip at every call site, which is the
whole defect above.
"""
from __future__ import annotations

import ast
import io
import textwrap
import tokenize


def _offsets(src: str) -> list[int]:
    """Absolute character offset of the start of each 1-indexed line."""
    offs, pos = [0], 0
    for line in src.splitlines(keepends=True):
        pos += len(line)
        offs.append(pos)
    return offs


def _blank_span(chars: list[str], offs: list[int], start: tuple[int, int],
                end: tuple[int, int], keep: str = "") -> None:
    """Overwrite `start`..`end` with `keep` then spaces, in place.

    1-indexed lines, 0-indexed columns — `tokenize`'s and `ast`'s convention.

    Newlines are never overwritten. Blanking them collapses a multi-line
    docstring into one physical line, which changes every later line number
    and breaks the next span's coordinates — measured, it corrupted 30 files
    and left `leerie.py` unparseable.
    """
    i, j = offs[start[0] - 1] + start[1], offs[end[0] - 1] + end[1]
    for k, ch in enumerate(keep):
        if i + k < j:
            chars[i + k] = ch
    for k in range(i + len(keep), j):
        if chars[k] != "\n":
            chars[k] = " "


def _byte_col_to_char(line: str, bcol: int) -> int:
    """`ast` reports columns in UTF-8 BYTES; `tokenize` reports characters.

    Mixing them is silent and off-by-N-per-non-ASCII-character. Measured:
    `chain/__init__.py`'s module docstring contains an em dash, so its
    `end_col_offset` of 53 overshot the 51-character line by two and blanked
    the first two characters of `__version__` on the line below.
    """
    return len(line.encode("utf-8")[:bcol].decode("utf-8", errors="ignore"))


def _docstring_spans(src: str) -> list[tuple[tuple[int, int], tuple[int, int]]]:
    """The character span of every docstring literal."""
    lines = src.splitlines()
    spans = []
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                 ast.ClassDef, ast.Module)):
            continue
        body = getattr(node, "body", None)
        if not body:
            continue
        first = body[0]
        if (isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
                and first.value.end_lineno is not None):
            spans.append((
                (first.value.lineno,
                 _byte_col_to_char(lines[first.value.lineno - 1],
                                   first.value.col_offset)),
                (first.value.end_lineno,
                 _byte_col_to_char(lines[first.value.end_lineno - 1],
                                   first.value.end_col_offset))))
    return spans


def _comment_spans(src: str) -> list[tuple[tuple[int, int], tuple[int, int]]]:
    """The character span of every comment.

    `tokenize`, not a `#`-prefix heuristic: a `#` inside a string literal
    would corrupt the result, and CLAUDE.md names that exact trap.
    """
    return [(t.start, t.end)
            for t in tokenize.generate_tokens(io.StringIO(src).readline)
            if t.type == tokenize.COMMENT]


def strip_comments(src: str) -> str:
    """`src` with comments blanked. Every other byte is unchanged.

    Dedented first, so `inspect.getsource` of a NESTED function works — its
    source carries the enclosing indentation and would not parse otherwise.
    A no-op for a module or a top-level function.
    """
    src = textwrap.dedent(src)
    chars, offs = list(src), _offsets(src)
    for start, end in _comment_spans(src):
        _blank_span(chars, offs, start, end)
    return "".join(chars)


def code_only(src: str) -> str:
    """`src` with comments AND docstrings blanked. Every other byte unchanged.

    Dedented first, for the same reason as `strip_comments`.

    Raises `SyntaxError` if `src` does not parse — see the module docstring
    for why degrading is not an option here.
    """
    src = textwrap.dedent(src)
    spans = _docstring_spans(src)          # raises on unparseable input
    chars, offs = list(src), _offsets(src)
    for start, end in spans:
        # `""` and not nothing: a function whose body is only a docstring
        # needs a statement left behind, or the result does not parse.
        _blank_span(chars, offs, start, end, keep='""')
    for start, end in _comment_spans(src):
        _blank_span(chars, offs, start, end)
    return "".join(chars)


def shell_code_only(src: str) -> str:
    """Shell source with comments removed, including TRAILING ones.

    The `#`-prefix line heuristic this replaces made the guard depend on where
    a comment sat: `prune_leerie_worktrees "$X"  # never a bare git worktree
    prune here` failed, while the same words on their own line passed. These
    three scripts carry ~10 explanatory comment lines each precisely because
    the construct is forbidden, so that is a guard which fails on correct code.

    Quote-aware rather than a bare `#` split: `${VAR#prefix}` and a `#` inside
    a string are not comments.
    """
    out = []
    for line in src.splitlines():
        q = None
        skip = False
        for i, ch in enumerate(line):
            if skip:
                skip = False
                continue
            if q:
                # An escaped quote inside a double-quoted span does not close
                # it; treating it as a close inverted the state and truncated
                # the line at the next `#`, hiding anything after it.
                if q == '"' and ch == "\\":
                    skip = True
                    continue
                if ch == q:
                    q = None
                continue
            if ch in "'\"":
                q = ch
                continue
            if ch == "#" and (i == 0 or line[i - 1] in " \t"):
                line = line[:i]
                break
        out.append(line)
    return "\n".join(out)
