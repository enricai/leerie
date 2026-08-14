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
import re


def _claude_p_src(leerie) -> str:
    """`claude_p`'s source with comments stripped.

    The region deliberately explains the deleted comparison, naming
    `num_turns`, `max_turns` and the 0.8 factor in prose. A raw scan matches
    that explanation and fails on correct code — the same trap the
    zombie-reaper and clobber guards document in CLAUDE.md.
    """
    src = inspect.getsource(leerie.claude_p)
    return "\n".join(
        line for line in src.splitlines()
        if not line.lstrip().startswith("#")
    )


def test_no_ratio_comparison_between_num_turns_and_max_turns(leerie):
    """The two numbers are not commensurable, so no code may compare them."""
    src = _claude_p_src(leerie)
    offenders = [
        line.strip() for line in src.splitlines()
        if re.search(r"0\.8\s*\*\s*max_turns", line)
        or re.search(r"turns\s*>=.*max_turns", line)
    ]
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
