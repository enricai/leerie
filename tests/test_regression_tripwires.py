"""The regression tripwires in DESIGN §14½ must stay present and specific.

Four signals whose *reappearance* means a specific, already-fixed defect has
come back. They were asked for explicitly by the work order — "fold this into
the monitoring notes" — and were dropped silently across five PRs, because the
destination they named ("the monitoring notes") did not exist in this repo.
Creating the section without a guard would leave them exactly as droppable as
they were before.

Deliberately weak by design: prose cannot be mechanically verified for
*truth*, only for presence. What this pins is that each tripwire still names
the concrete signal an operator would grep for, rather than decaying into a
general statement about being careful. A tripwire that no longer names its
signal is not a tripwire.
"""
from __future__ import annotations

import pathlib

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
DESIGN = REPO_ROOT / "docs" / "DESIGN.md"
SECTION_HEADING = "## 14½. Regression tripwires"


@pytest.fixture(scope="module")
def section() -> str:
    text = DESIGN.read_text()
    assert SECTION_HEADING in text, (
        "DESIGN lost its regression-tripwires section. It exists because four "
        "explicit work-order asks were dropped across five PRs for want of a "
        "destination; deleting it re-opens that.")
    body = text.split(SECTION_HEADING, 1)[1]
    # Ends at the next top-level heading.
    return body.split("\n## ", 1)[0]


def test_section_is_not_empty(section):
    """Anti-vacuity: every substring check below passes against a heading
    with nothing under it."""
    assert len(section.strip()) > 400, (
        "the tripwires section is present but nearly empty; the substring "
        "assertions below would pass while carrying no content")


# (signal, emitted_by_leerie, why it is here)
#
# The split is load-bearing, and getting it wrong is how this guard would
# have been deleted rather than fixed. A signal leerie EMITS can be checked
# against the source — that is the half that catches a tripwire quoting a
# string the code does not print, which is exactly the defect that shipped.
# A signal leerie only OBSERVES (an upstream API message) has zero hits here
# by definition, so requiring it in the source would fail on correct code.
_TRIPWIRES = [
    ("fit_judge crashed for", True,
     "the stdin-race (#198) downstream symptom — the crash-to-leaf degrade is "
     "silent, so this log line is its only visible trace"),
    # DESIGN quotes this signal as TWO fragments (the subtask id sits between
    # them), so both need checking. Guarding only the first left the exact
    # defect this file exists to catch — DESIGN quoting a string the code does
    # not print — live for the other half.
    ("; accepting as leaf", True,
     "the second half of the same fit_judge signal"),
    ("exceeds maximum allowed tokens", False,
     "the context-budget fix (#194); an UPSTREAM API rejection, never a "
     "leerie string"),
    ("push_error", True,
     "never judge a finalize outcome from the remote — read this run.json "
     "field instead"),
]

SOURCES = [
    REPO_ROOT / "orchestrator" / "leerie.py",
    REPO_ROOT / "scripts" / "host-finalize.sh",
]


@pytest.mark.parametrize("signal,emitted,why", _TRIPWIRES,
                         ids=[t[0][:24] for t in _TRIPWIRES])
def test_tripwire_names_its_signal(section, signal, emitted, why):
    assert signal in section, (
        f"the tripwire for {why!r} no longer names the signal {signal!r} an "
        "operator would actually grep for")


@pytest.mark.parametrize("signal,emitted,why", _TRIPWIRES,
                         ids=[t[0][:24] for t in _TRIPWIRES])
def test_emitted_signals_actually_exist_in_the_source(signal, emitted, why):
    """The half that would have caught the defect this file shipped with.

    DESIGN quoted `fit_judge crashed; accepting as leaf`, but the code emits
    `fit_judge crashed for {sid}; accepting as leaf` — the id sits between the
    halves, so the quoted string is not a substring and an operator grepping
    it verbatim gets nothing. The original guard asserted the wrong literal
    was present *in DESIGN* and never compared it to the source, so it pinned
    the error instead of catching it.
    """
    if not emitted:
        pytest.skip(f"{signal!r} is an upstream string leerie never emits")
    found = any(signal in p.read_text() for p in SOURCES)
    assert found, (
        f"DESIGN's tripwire quotes {signal!r} as a signal leerie emits, but "
        f"it appears in none of {[p.name for p in SOURCES]}. An operator "
        "grepping for it would get zero hits.")


def test_at_least_two_signals_are_source_checked():
    """Anti-vacuity: if every tripwire were marked 'observed', the check above
    would skip its way to green."""
    assert sum(1 for _, emitted, _ in _TRIPWIRES if emitted) >= 2


def test_work_order_launch_tripwire_survives(section):
    """The fourth ask has no log line — its signal is a procedure, so pin the
    distinction it draws rather than a string."""
    lowered = section.lower()
    assert "appendix" in lowered and "work-order section" in lowered, (
        "the launch-from-the-work-order-section tripwire lost the distinction "
        "it exists to draw (appendix vs work-order section)")


def test_tripwires_are_not_presented_as_gates(section):
    """They are things to recognise in a log, and saying so is load-bearing:
    the repo's whole discipline is that a checkable guarantee belongs in code,
    so a reader must not mistake these for checks that already exist."""
    lowered = section.lower()
    assert "not a gate" in lowered or "none of them is a gate" in lowered, (
        "the section no longer says these are not gates; without that a "
        "reader may assume leerie already checks them")
