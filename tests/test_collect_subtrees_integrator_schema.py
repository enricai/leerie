"""`collect-subtrees.sh`'s hardcoded integrator schema must equal the live one.

`scripts/remote/collect-subtrees.sh` embeds a copy of `SCHEMAS["integrator"]`
as a single-quoted shell string, because it invokes `claude -p --json-schema`
directly from bash on the remote machine and cannot import the orchestrator.
A duplicated definition with no equality check is a drift bug waiting to
happen — and it already happened.

**Measured 2026-08-03:** the embedded copy still carried `maxLength` 2000/500
on the confidence fields, values the live schema had moved off *twice* since
(to 8000/2000, then deleted entirely). So remote integrator runs were validating
their worker output against a materially different contract than local ones —
silently, because nothing compared the two. This file is that comparison.

The guard is deliberately whole-object equality rather than a spot-check of the
fields that happened to drift: the next drift will be in a different field, and
a test that only knows about the last one is a test for a bug that is already
fixed.
"""
from __future__ import annotations

import json
import re
from pathlib import Path


REPO = Path(__file__).resolve().parent.parent
COLLECT_SH = REPO / "scripts" / "remote" / "collect-subtrees.sh"

# The assignment is `integrator_schema='{...}'` on one line — single-quoted, so
# the JSON's own double quotes need no escaping and the string is inert to the
# shell. Anchored on the variable name so an unrelated single-quoted JSON blob
# elsewhere in the script cannot be picked up by accident.
_ASSIGN_RE = re.compile(r"^integrator_schema='(\{.*\})'\s*$", re.M)


def _embedded_schema() -> dict:
    text = COLLECT_SH.read_text()
    match = _ASSIGN_RE.search(text)
    assert match, (
        "could not find the `integrator_schema='{...}'` assignment in "
        f"{COLLECT_SH.relative_to(REPO)} — if the assignment moved or changed "
        "shape, update this guard rather than deleting it; an unparsed "
        "assignment means the drift check silently stops running")
    return json.loads(match.group(1))


def test_embedded_schema_is_valid_json():
    """Guard-the-guard: a malformed blob would make every equality assertion
    below fail for the wrong reason, or (worse) never run."""
    assert isinstance(_embedded_schema(), dict)


def test_embedded_schema_matches_the_live_schema(leerie):
    """The whole point. Whole-object equality, not a spot-check."""
    embedded = _embedded_schema()
    live = leerie.SCHEMAS["integrator"]
    assert embedded == live, (
        "scripts/remote/collect-subtrees.sh's embedded integrator schema has "
        "drifted from SCHEMAS['integrator']. Remote integrator runs would "
        "validate against a different contract than local ones. Re-sync the "
        "shell string from the live schema."
    )


def test_the_drift_that_actually_happened_is_gone():
    """Regression pin on the measured incident: the embedded copy carried
    `maxLength` caps that the live schema had already moved off twice, and
    later deleted outright."""
    blob = json.dumps(_embedded_schema())
    assert "maxLength" not in blob, (
        "the embedded schema carries maxLength constraints; the live "
        "confidence block has none (measured non-binding at 0.00% and "
        "deleted 2026-08-03)")
    assert "gap_to_close" not in blob, (
        "the embedded schema still requires gap_to_close, removed from the "
        "live confidence block 2026-08-03")


def test_guard_would_catch_a_single_character_change(leerie):
    """Anti-vacuity: prove the comparison is real by mutating one character of
    the embedded copy in memory and asserting it no longer matches. Without
    this, a `_embedded_schema()` that silently returned the live object would
    make every test above pass while checking nothing."""
    embedded = _embedded_schema()
    assert embedded == leerie.SCHEMAS["integrator"], "precondition"

    mutated = json.loads(json.dumps(embedded))
    mutated["required"] = list(mutated["required"]) + ["a_field_that_drifted"]
    assert mutated != leerie.SCHEMAS["integrator"]

    # …and that the two objects are genuinely distinct instances, so equality
    # above reflects content rather than identity.
    assert embedded is not leerie.SCHEMAS["integrator"]


def test_confidence_block_in_the_embedded_copy_is_the_flattened_shape(leerie):
    """The flattening (DESIGN §8) has to reach the remote path too, or remote
    integrators keep hitting the #49747 trigger profile that local ones no
    longer present."""
    conf = _embedded_schema()["properties"]["confidence"]
    assert set(conf["required"]) == {"resolution", "basis"}
    for relaxed in ("falsifiers_tested", "contradictions_reconciled"):
        assert relaxed in conf["properties"]
        assert relaxed not in conf["required"]
