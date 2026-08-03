"""Confidence-block length caps, sized against measured output.

The caps exist to mitigate `anthropics/claude-code#49747` (the CLI corrupting
a `StructuredOutput` call, correlated with the length of a single tool-call
string argument — observed at ~16KB in run `d8302c0d46d8…`). That mitigation
is real and is NOT removed here.

What was wrong was the sizing. Measured across real planner submissions
(2026-08-03): `basis` ran 463–1321 chars and list items 130–502. The old caps
(2000 / 500) sat directly on that distribution's shoulder, so overflow was
routine rather than exceptional — **29 rejections across two runs** (12
`basis`, 9 `falsifiers_tested`, 8 `contradictions_reconciled`), including a
live reproduction that missed by **two characters** (502 against the 500 cap)
and resubmitted at 260.

A cap set at the top of the natural distribution does not shorten output; it
converts the tail into rejected work.

Note what this file does NOT change: `confidence` stays **required** at the
top level. Removing that was considered and rejected — it is a deliberate
DESIGN §8/§12 structural self-gating contract with its own tests
(`test_schemas_confidence.py`), and it accounted for only 4 of the 65 measured
failures against the caps' 29. Overturning a documented discipline mechanism
needs a DESIGN change and better evidence than that.
"""
from __future__ import annotations

import json
import re

import pytest


# The measured maxima the caps are sized against. Update these ONLY with a
# fresh measurement, never to make a failing test pass.
_MEASURED_MAX_BASIS = 1321
_MEASURED_MAX_LIST_ITEM = 502
# The single-argument length at which #49747 corruption was actually observed.
_OBSERVED_CORRUPTION_LENGTH = 16000


def test_caps_clear_the_measured_distribution(leerie):
    """A cap at or near the observed maximum guarantees periodic rejection.
    Require real headroom, not a hairline pass."""
    assert leerie._CONFIDENCE_BASIS_MAX_LENGTH >= 2 * _MEASURED_MAX_BASIS
    assert (leerie._CONFIDENCE_LIST_ITEM_MAX_LENGTH
            >= 2 * _MEASURED_MAX_LIST_ITEM)


def test_caps_stay_below_the_observed_corruption_length(leerie):
    """The mitigation must survive the resize. If a cap ever reaches the
    length at which corruption was actually seen, it has stopped mitigating
    anything."""
    assert leerie._CONFIDENCE_BASIS_MAX_LENGTH < _OBSERVED_CORRUPTION_LENGTH
    assert (leerie._CONFIDENCE_LIST_ITEM_MAX_LENGTH
            < _OBSERVED_CORRUPTION_LENGTH)


def test_the_two_character_overflow_now_passes(leerie):
    """The exact live reproduction: `falsifiers_tested[3]` at 502 chars was
    rejected against the 500 cap, costing a full re-submission."""
    assert 502 <= leerie._CONFIDENCE_LIST_ITEM_MAX_LENGTH


def test_caps_are_still_enforced_in_the_schema(leerie):
    """Resized, not removed — the schema must still carry maxLength."""
    conf = leerie.SCHEMAS["planner"]["properties"]["confidence"]["properties"]
    assert conf["basis"]["maxLength"] == leerie._CONFIDENCE_BASIS_MAX_LENGTH
    for field in ("falsifiers_tested", "contradictions_reconciled"):
        assert (conf[field]["items"]["maxLength"]
                == leerie._CONFIDENCE_LIST_ITEM_MAX_LENGTH)


def test_confidence_remains_top_level_required(leerie):
    """Guard against a future change quietly making `confidence` optional as
    a shortcut for reducing schema failures. That is a DESIGN §8 contract —
    change DESIGN first, not the schema."""
    for worker in ("planner", "classifier", "provision", "reconciler",
                   "plan_overlap_judge", "integrator", "fit_judge"):
        assert "confidence" in leerie.SCHEMAS[worker]["required"], worker


# ----- prompts must state the limits ---------------------------------------

_CONFIDENCE_PROMPTS = [
    "classifier", "planner", "reconciler", "provision", "plan_overlap_judge",
    "integrator", "implementer", "conformer", "fit_judge", "rebaser",
]


@pytest.mark.parametrize("name", _CONFIDENCE_PROMPTS)
def test_every_confidence_worker_is_told_the_limits(leerie, name):
    """The caps were previously stated in NO prompt, so a worker had no way to
    comply with a bound it was never told about while being asked for detailed
    evidence. A rejection the model could not have avoided is not a gate."""
    text = leerie._load_prompt(name)
    assert str(leerie._CONFIDENCE_BASIS_MAX_LENGTH) in text, (
        f"{name} does not state the basis limit")
    assert str(leerie._CONFIDENCE_LIST_ITEM_MAX_LENGTH) in text, (
        f"{name} does not state the list-item limit")


def test_prompt_numbers_track_the_constants(leerie):
    """The fragment must not drift from the schema it describes — a prompt
    stating a stale number is worse than none, since a worker that obeys it is
    still rejected."""
    frag = (leerie.PROMPTS / "_confidence.md").read_text()
    lines = frag.splitlines()

    def _numbers_on_line_mentioning(word: str) -> set[int]:
        out: set[int] = set()
        for ln in lines:
            if word in ln:
                out |= {int(n) for n in re.findall(r"\*\*(\d+) characters\*\*", ln)}
        return out

    # Checked per-line, against the specific field each line describes. An
    # earlier version asserted `"2000 characters" not in frag or <cap> == 2000`,
    # which the list-item cap of 2000 made ALWAYS TRUE — it could never fail,
    # so it guarded nothing.
    assert _numbers_on_line_mentioning("`basis`") == {
        leerie._CONFIDENCE_BASIS_MAX_LENGTH}
    for field in ("falsifiers_tested", "contradictions_reconciled"):
        assert _numbers_on_line_mentioning(field) == {
            leerie._CONFIDENCE_LIST_ITEM_MAX_LENGTH}, field


def test_a_realistic_submission_fits(leerie):
    """End-to-end sanity against the measured shape: a submission at the
    observed maxima validates."""
    jsonschema = pytest.importorskip("jsonschema")
    payload = {
        "domain": "feature-implementation",
        "subtasks": [],
        "status": "ready",
        "confidence": {
            "task_understanding": 9.0,
            "decomposition_quality": 9.0,
            "basis": "x" * _MEASURED_MAX_BASIS,
            "falsifiers_tested": ["y" * _MEASURED_MAX_LIST_ITEM],
            "contradictions_reconciled": ["z" * _MEASURED_MAX_LIST_ITEM],
            "gap_to_close": {},
        },
    }
    jsonschema.validate(payload, leerie.SCHEMAS["planner"])
    json.dumps(payload)
