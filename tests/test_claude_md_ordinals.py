"""Numbered lists in CLAUDE.md must actually be numbered in order.

CLAUDE.md is loaded into every session, so a passage that says "Three lessons"
and then runs First → Third → Second is read that way by every worker and
every contributor. That exact defect shipped: PR #204 changed "Two lessons" to
"Three lessons" and inserted the new paragraph *above* the existing second
one, in the same change that added a checklist item about verifying claims.

Cheap to guard and impossible to catch by reading — the ordinals are hundreds
of words apart, and the count and the sequence are separated by a page of
prose. Prototyped before landing: over CLAUDE.md's ordinal-declaring phrases
it flags exactly the one defect and nothing else.

**Coverage is narrow, and saying so is the honest framing.** Of the nine
phrases `_DECL` currently matches, eight are followed by no ordinals at all,
so the `>= 5` floor below is satisfied by declarations that check nothing —
only the `any(seq ...)` clause does real work, and today it rests on a single
passage. The noun list is a sample, not a taxonomy: "Three consequences"
elsewhere in this file is not matched. This guards the shape that already
broke, cheaply; it is not a general prose checker, and widening the nouns
would trade false negatives for false positives in a 247 KB document.

Deliberately narrow. It checks two mechanical properties — the ordinals appear
in ascending order, and there are no more of them than the declared count — and
says nothing about whether the prose is correct, which no test can.
"""
from __future__ import annotations

import pathlib
import re

import pytest

CLAUDE_MD = pathlib.Path(__file__).resolve().parent.parent / "CLAUDE.md"

_ORDINALS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5}
_COUNTS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6}

# "Three lessons", "two reasons", "four traps" …
_DECL = re.compile(
    r"\b(one|two|three|four|five|six)\s+"
    r"(lessons?|reasons?|points?|things?|properties|traps?)\b", re.I)
_ORD = re.compile(r"\b(First|Second|Third|Fourth|Fifth)\b")

# How far after the declaration to look for its ordinals. Wide enough to span
# the paragraphs a single list occupies here, narrow enough not to swallow the
# next unrelated section.
_WINDOW = 2500


def _declarations() -> list[tuple[str, int, list[int]]]:
    text = CLAUDE_MD.read_text()
    out = []
    for m in _DECL.finditer(text):
        seq = [_ORDINALS[w.lower()]
               for w in _ORD.findall(text[m.end():m.end() + _WINDOW])]
        out.append((m.group(0), _COUNTS[m.group(1).lower()], seq))
    return out


def test_scan_finds_declarations():
    """Anti-vacuity: a scan that matches nothing passes forever."""
    decls = _declarations()
    assert len(decls) >= 5, (
        f"only {len(decls)} ordinal-declaring phrase(s) found in CLAUDE.md — "
        "the pattern probably broke, so the checks below are hollow")
    assert any(seq for _, _, seq in decls), (
        "no declaration has any ordinals after it; the window or the ordinal "
        "pattern is wrong")


def test_ordinals_appear_in_ascending_order():
    """Ascending AND distinct.

    `sorted()` alone misses the commonest form of this drift: two paragraphs
    both opening "Second,". That gives `[1, 2, 2, 3]` — sorted, and with a
    distinct count equal to the declared total, so every other check here
    passes it too.
    """
    failures = []
    for phrase, _declared, seq in _declarations():
        if len(seq) >= 2 and (seq != sorted(seq) or len(seq) != len(set(seq))):
            names = [n for n, v in sorted(_ORDINALS.items(), key=lambda kv: kv[1])]
            failures.append(
                f"{phrase!r}: ordinals run "
                f"{[names[v - 1] for v in seq]} — out of order")
    assert not failures, (
        "numbered lists in CLAUDE.md are out of sequence:\n  "
        + "\n  ".join(failures))


def test_no_more_ordinals_than_declared():
    """A list that says "Two lessons" and then gives three is the same class
    of drift, from the other direction — it is what produced the misordering
    (the count was raised and the new item inserted in the wrong place)."""
    failures = []
    for phrase, declared, seq in _declarations():
        distinct = len(set(seq))
        if distinct > declared:
            failures.append(
                f"{phrase!r} declares {declared} but {distinct} ordinals follow")
    assert not failures, (
        "declared counts disagree with the ordinals that follow:\n  "
        + "\n  ".join(failures))


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__])
