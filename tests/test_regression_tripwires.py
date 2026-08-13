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

DESIGN = pathlib.Path(__file__).resolve().parent.parent / "docs" / "DESIGN.md"
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


@pytest.mark.parametrize("signal,why", [
    ("fit_judge crashed; accepting as leaf",
     "the stdin-race (#198) downstream symptom — the crash-to-leaf degrade is "
     "silent, so this log line is its only visible trace"),
    ("exceeds maximum allowed tokens",
     "the context-budget fix (#194); it became impossible once the payload "
     "moved off argv, so its return means the transport changed"),
    ("push_error",
     "never judge a finalize outcome from the remote — read run.json"),
])
def test_tripwire_names_its_signal(section, signal, why):
    assert signal in section, (
        f"the tripwire for {why!r} no longer names the signal {signal!r} an "
        "operator would actually grep for")


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
