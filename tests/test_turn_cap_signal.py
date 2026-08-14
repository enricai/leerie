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

import inspect
import io
import re
import tokenize

import pytest


def _claude_p_src(leerie) -> str:
    """`claude_p`'s source with comments stripped.

    The region deliberately explains the deleted comparison, naming
    `num_turns`, `max_turns` and the 0.8 factor in prose. A raw scan matches
    that explanation and fails on correct code — the same trap the
    zombie-reaper and clobber guards document in CLAUDE.md.
    """
    src = inspect.getsource(leerie.claude_p)
    # `tokenize`, not a `#`-prefix line heuristic: a `#` inside a string
    # literal would corrupt the result, and CLAUDE.md names that exact trap.
    # It works out the same on today's tree; the point is that it stays right.
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



def _logical_lines(src: str) -> list[str]:
    """Physical lines joined while a bracket is open.

    A comparison wrapped across lines is invisible to a per-line scan, and this
    file's own style wraps at 79 columns — so `if (num_turns\n >= 0.8 *
    max_turns):` would evade a guard that reads one line at a time.
    """
    out, buf, depth = [], "", 0
    for line in src.splitlines():
        buf = line if not buf else buf + " " + line.strip()
        depth += line.count("(") - line.count(")")
        if depth <= 0:
            out.append(buf)
            buf, depth = "", 0
    if buf:
        out.append(buf)
    return out


def _ratio_offenders(src: str) -> list[str]:
    """Lines comparing `num_turns` against `max_turns` in any spelling.

    Deliberately broad. The first version matched two literal shapes, so
    `0.8 * float(max_turns) <= turns` and `turns / max_turns >= 0.8` — both
    reinstating the defect exactly — sailed past it.
    """
    out = []
    for line in _logical_lines(src):
        if "max_turns" not in line:
            continue
        if not re.search(
                r"(0\.\d+\s*\*|/\s*(float\()?max_turns|>=|<=|[^<>=!]>[^=]|"
                r"[^<>=!]<[^=])", line):
            continue
        # The COUNTER, in either spelling. `\bturns\b` alone cannot match
        # `num_turns` — `_` is a word character, so there is no boundary
        # between them — which left this scan blind to the very identifier the
        # finding is about (POSTMORTEM F7 names `num_turns` throughout). It
        # passed only because the local in `claude_p` happens to be `turns`.
        if not re.search(r"(?:\bnum_turns\b|\bturns\b)",
                         line.replace("max_turns", "")):
            continue
        out.append(line.strip())
    return out


def test_no_ratio_comparison_between_num_turns_and_max_turns(leerie):
    """The two numbers are not commensurable, so no code may compare them."""
    offenders = _ratio_offenders(_claude_p_src(leerie))
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


@pytest.mark.parametrize("canary", [
    "if turns >= 0.8 * max_turns:",
    "if 0.8 * float(max_turns) <= turns:",
    "if turns / max_turns >= 0.8:",
    "if turns > max_turns - 2:",
    # The `num_turns` spelling — what the CLI actually reports, what F7 names,
    # and what the previous regex could not see.
    "if num_turns >= 0.8 * max_turns:",
    'if envelope.get("num_turns", -1) >= 0.8 * max_turns:',
    "if 0.8 * float(max_turns) <= num_turns:",
    "ratio = num_turns / max_turns",
    # Wrapped across lines, which a per-line scan misses.
    "if (num_turns\n        >= 0.8 * max_turns):",
])
def test_the_scan_fires_on_a_reinstated_comparison(canary):
    """Anti-vacuity, absent from the original.

    An offender-list scan that can no longer match anything certifies the tree
    clean forever. Two of these four evaded the first version's two literal
    regexes while reinstating the defect exactly.
    """
    assert _ratio_offenders(canary), f"the scan must flag {canary!r}"


def test_the_scan_leaves_legitimate_max_turns_uses_alone():
    """The converse: `max_turns` is a real parameter, passed and forwarded all
    over `claude_p`. Flagging its ordinary uses would make the guard unusable.
    """
    for benign in ("max_turns=max_turns,", "        max_turns: int,",
                   'log(f"max_turns={max_turns}")'):
        assert not _ratio_offenders(benign), benign
