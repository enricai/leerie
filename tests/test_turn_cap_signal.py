"""The turn cap has exactly one trustworthy signal, and it is the CLI's own.

`claude -p` computes the `num_turns` it reports from two different counters.
On the cap-enforcement path it reports the `max_turns_reached` attachment's
count alongside `is_error: true` and subtype `error_max_turns`; on the success
path it reports a separately maintained count. Only the first is commensurable
with the `--max-turns` leerie passes.

leerie used to carry a second warning — a "context-decay proxy" firing when
`num_turns >= 0.8 * max_turns` — which fired *only* on the success path (it was
the `elif` to the terminal_reason branch) and compared the success-path number
against the flag bounding the other one. Measured across a real corpus, 61 of
those warnings fired and **11 were arithmetically impossible**: 21/20, 26/20,
28/20, 31/30, and 62 through 72 out of 60, across three different caps.

See docs/POSTMORTEM-2026-08-14.md, F7.
"""
from __future__ import annotations

import ast
import inspect
import io
import re
import textwrap
import tokenize
from pathlib import Path

import pytest


def _module_src() -> str:
    """The whole orchestrator module, comments stripped.

    Scoped to `claude_p` this scan read one function, so moving the comparison
    into a helper — `_warn_on_turn_pressure(envelope, max_turns)`, say — hid it
    completely. The sweep is clean module-wide, so there is no cost to the
    wider surface and a real hiding place closes.

    Raw source, deliberately. `_ratio_offenders` is pure `ast`, and comments
    do not appear in an AST, so there is nothing to strip — while the
    token-level stripper this file uses for its TEXTUAL assertions drops
    backslash line-continuations and produces source that no longer parses
    (`leerie.py:4312` is one). Stripping here would have been a lossy no-op.
    """
    return ((Path(__file__).resolve().parent.parent
             / "orchestrator" / "leerie.py").read_text())


def _strip_comments(src: str) -> str:
    """`tokenize`, not a `#`-prefix line heuristic: a `#` inside a string
    literal would corrupt the result, and CLAUDE.md names that exact trap.

    For TEXTUAL assertions only — the result is not guaranteed to parse, since
    a backslash line-continuation is not a token and is dropped.
    """
    out, last = [], (1, 0)
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type == tokenize.COMMENT:
            continue
        if tok.start[0] > last[0]:
            out.append("\n" * (tok.start[0] - last[0]))
            last = (tok.start[0], 0)
        out.append(" " * max(0, tok.start[1] - last[1]) + tok.string)
        last = tok.end
    return "".join(out)


def _claude_p_src(leerie) -> str:
    """`claude_p`'s source with comments stripped.

    The region deliberately explains the deleted comparison, naming
    `num_turns`, `max_turns` and the 0.8 factor in prose. A raw scan matches
    that explanation and fails on correct code — the same trap the
    zombie-reaper and clobber guards document in CLAUDE.md.
    """
    return _strip_comments(inspect.getsource(leerie.claude_p))




def _aliases(tree: ast.AST, seeds: set[str], keys: tuple[str, ...]) -> set[str]:
    """Names whose value derives from `seeds`, or from a lookup of `keys`.

    Transitive, so `a = envelope.get("num_turns", -1); b = a` taints both.

    Four binding forms, because the `ast.Assign`-with-a-bare-`Name`-target
    version this replaced saw only one of them: annotated assignment, walrus
    and tuple-unpack all bind a name and all slipped past it.
    """
    t = set(seeds)
    for _ in range(4):                       # cheap fixpoint
        before = len(t)
        for n in ast.walk(tree):
            if isinstance(n, ast.Assign):
                tgts, val = n.targets, n.value
            elif isinstance(n, ast.AnnAssign) and n.value is not None:
                tgts, val = [n.target], n.value
            elif isinstance(n, ast.NamedExpr):
                tgts, val = [n.target], n.value
            else:
                continue
            src = ast.unparse(val)
            hit = any(f"'{k}'" in src or f'"{k}"' in src for k in keys) or bool(
                {x.id for x in ast.walk(val) if isinstance(x, ast.Name)} & t)
            if not hit:
                continue
            for tg in tgts:
                for nm in ast.walk(tg):
                    if isinstance(nm, ast.Name):
                        t.add(nm.id)
        if len(t) == before:
            break
    return t


def _ratio_offenders(src: str) -> list[str]:
    """Comparisons between the CLI's reported turn count and `--max-turns`.

    Keyed on PROVENANCE on BOTH operands, not on either one's name. Two regex
    generations were keyed on the name and both were blind to the thing they
    were named for: `\bturns\b` cannot match `num_turns` (`_` is a word
    character), and adding the second literal spelling still let
    `_used = envelope.get("num_turns", -1)` through — the same defect written
    by anyone who renames a variable.

    The ast version that replaced them still had one line of dead code and one
    name-keyed operand:

    - it tested `'"num_turns"' in ast.unparse(p)` with DOUBLE quotes, and
      `ast.unparse` always emits single ones. That arm could never match, so
      the most direct spelling of F7 —
      `if envelope.get("num_turns", -1) >= 0.8 * max_turns:` — was missed,
      along with the subscript and walrus forms. It survived because
      `_tainted` checked both spellings, so every canary that assigned to a
      local passed and no canary used the inline form.
    - the cap side was matched by the literal name `max_turns`, so
      `cap = max_turns` defeated it.

    Both operands now go through `_aliases`, and the quote spelling is no
    longer written by hand anywhere.
    """
    tree = ast.parse(textwrap.dedent(src))
    counts = _aliases(tree, {"num_turns"}, ("num_turns",))
    caps = _aliases(tree, {"max_turns"}, ())
    out = []
    for n in ast.walk(tree):
        if not isinstance(n, ast.Compare):
            continue
        parts = [n.left, *n.comparators]
        names = [{x.id for x in ast.walk(p) if isinstance(x, ast.Name)}
                 for p in parts]
        srcs = [ast.unparse(p) for p in parts]
        has_cap = any(s & caps for s in names)
        has_count = any(s & counts for s in names) or any(
            "'num_turns'" in s or '"num_turns"' in s for s in srcs)
        if has_cap and has_count:
            out.append(ast.unparse(n))
    return out


def test_no_ratio_comparison_between_num_turns_and_max_turns(leerie):
    """The two numbers are not commensurable, so no code may compare them."""
    offenders = _ratio_offenders(_module_src())
    assert not offenders, (
        "num_turns (success path) and --max-turns (bounding the enforcement "
        "path's counter) measure different things; comparing them produced 11 "
        "impossible readings out of 61. Use the CLI's terminal_reason instead:"
        "\n  " + "\n  ".join(offenders)
    )


def test_the_terminal_reason_branch_survives(leerie):
    """Anti-vacuity: the real cap signal must still be reported.

    Deleting the proxy is only correct because this branch already names the
    condition, from the CLI's own signal. If it goes, the cap becomes silent
    and the test above passes for the wrong reason.
    """
    src = _claude_p_src(leerie)
    assert 'envelope.get("terminal_reason"' in src, src[:400]
    assert "terminal_reason=" in src, (
        "the non-clean-exit warning must still surface terminal_reason")
    assert "num_turns=" in src, (
        "the warning should still report num_turns — reporting the number is "
        "fine, it is only the comparison against the cap that was meaningless")


def test_the_comment_records_why_the_proxy_was_deleted(leerie):
    """A removed gate leaves an invitation to re-add it unless the reason stays.

    CLAUDE.md's dead-code note makes this point about helpers; it applies at
    least as strongly to a deleted warning, which looks like an oversight.
    """
    src = inspect.getsource(leerie.claude_p)
    assert "F7" in src or "two DIFFERENT counters" in src, (
        "the deletion must carry its reason inline so a future reader does not "
        "restore the comparison")


# The assignment `claude_p` really has. A bare `turns` with no provenance is
# deliberately NOT flagged — it could be any variable, and guessing from the
# name is the mistake this predicate exists to stop making.
_T = 'turns = envelope.get("num_turns", -1)\n'


@pytest.mark.parametrize("canary", [
    # The historical shapes, written the way `claude_p` actually writes them —
    # the count comes from the envelope, which is what makes it the CLI's
    # reported turn count rather than an unrelated local named `turns`.
    _T + "if turns >= 0.8 * max_turns:\n    pass",
    _T + "if 0.8 * float(max_turns) <= turns:\n    pass",
    _T + "if turns / max_turns >= 0.8:\n    pass",
    _T + "if turns > max_turns - 2:\n    pass",
    # The `num_turns` spelling — what the CLI reports and what F7 names, and
    # what `\\bturns\\b` structurally cannot match.
    "if num_turns >= 0.8 * max_turns:\n    pass",
    "if 0.8 * float(max_turns) <= num_turns:\n    pass",
    # A RENAMED local. Both regex generations were keyed on the name and both
    # let this through — it is the same defect written by anyone who renames a
    # variable, which is why the check is keyed on provenance instead.
    'u = envelope.get("num_turns", -1)\nif u >= 0.8 * max_turns:\n    pass',
    '_used = envelope.get("num_turns", -1)\nif 0.8 * max_turns <= _used:\n    pass',
    # ...and transitively.
    'a = envelope.get("num_turns", -1)\nb = a\nif b >= 0.8 * max_turns:\n    pass',
    # Wrapped across lines: ast needs no line-joining at all.
    'if (num_turns\n        >= 0.8 * max_turns):\n    pass',
    # THE most direct spelling of F7, and the one the double-quote arm could
    # never match — `ast.unparse` emits single quotes. Verified missed.
    'if envelope.get("num_turns", -1) >= 0.8 * max_turns:\n    pass',
    'if envelope["num_turns"] >= 0.8 * max_turns:\n    pass',
    # Walrus: binds a name, and the Assign-only taint walk did not see it.
    'if (t := envelope.get("num_turns", -1)) >= 0.8 * max_turns:\n    pass',
    # Annotated assignment and tuple-unpack, same reason.
    't: int = envelope.get("num_turns", -1)\nif t >= 0.8 * max_turns:\n    pass',
    '_, t = 1, envelope.get("num_turns", -1)\nif t >= 0.8 * max_turns:\n    pass',
    # A RENAMED CAP. The cap side was matched by its literal name, so this
    # defeated the check even with the count side fully tainted.
    'u = envelope.get("num_turns", -1)\ncap = max_turns\nif u >= 0.8 * cap:\n    pass',
])
def test_the_scan_fires_on_a_reinstated_comparison(canary):
    """Anti-vacuity. An offender-list scan that can no longer match anything
    certifies the tree clean forever."""
    assert _ratio_offenders(canary), f"the scan must flag {canary!r}"


@pytest.mark.parametrize("benign", [
    "claude_p(max_turns=max_turns)",
    "cmd += ['--max-turns', str(max_turns)]",
    'log(f"max_turns={max_turns}")',
    # A return annotation is not a comparison. `[^<>=!]>[^=]` matched the `->`
    # of a joined signature and failed a correct tree for it.
    "def _watch(turns: int, max_turns: int) -> None:\n    pass",
    "def _note(num_turns: int, max_turns: int) -> str:\n    return ''",
    # Reporting both numbers is not comparing them.
    't = envelope.get("num_turns", -1)\nlog(f"turns={t} cap={max_turns}")',
    # A cap alias compared against something with no turn-count provenance is
    # an ordinary bound check, not the F7 comparison.
    "cap = max_turns\nif cap > 0:\n    pass",
])
def test_the_scan_leaves_legitimate_max_turns_uses_alone(benign):
    """The converse: `max_turns` is a real parameter, passed and forwarded all
    over `claude_p`. Flagging its ordinary uses makes the guard unusable."""
    assert not _ratio_offenders(benign), benign
