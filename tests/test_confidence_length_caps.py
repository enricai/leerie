"""The confidence block's shape — flattened, and with NO length caps.

Filename is historical: this file owns the "should the confidence block carry
`maxLength` caps?" question, and as of 2026-08-03 the measured answer is **no**.

The caps were introduced to mitigate `anthropics/claude-code#49747` (the CLI
corrupting a `StructuredOutput` call), resized once from 2000/500 to 8000/2000,
and are now deleted outright. Measured over the full run corpus — 6,526 `basis`
values and 30,719 list items — the 8000/2000 pair bound on **0.00%** of real
output (observed maxima 4,342 and 1,362). A constraint that never binds cannot
mitigate anything; it was dead schema surface. The earlier 2000/500 pair bound
on 2.38% and 6.32%, i.e. it was pure rejection pressure.

**Do not "restore" either pair.** Sizing was never the lever.

What replaced it is a *shape* change. The old block was five required fields —
axes + `basis` + two arrays + a nested `gap_to_close` object — which is, field
for field, #49747's reported trigger profile (many required parameters, arrays
mixed with paragraph-length strings). A controlled A/B against the live CLI
(real `fit_judge` schema, same prompt and model, n=8 per arm) measured **8/8
first attempts corrupted with the block present and 0/8 without it**. So the
block is flattened: axes + `basis` required, the two arrays kept as optional
properties, `gap_to_close` removed entirely (it was the only nested object, and
its only consumer was a diagnostic log line).

Note what did NOT change: `confidence` stays **required** at the top level.
That is the DESIGN §8/§12 structural self-gating contract — every gate still
reads a real number. Only the sub-fields relaxed.
"""
from __future__ import annotations

import json

import pytest


# Observed maxima across the corpus. Update ONLY with a fresh measurement.
_MEASURED_MAX_BASIS = 4342
_MEASURED_MAX_LIST_ITEM = 1362


def _confidence_workers(leerie) -> list[str]:
    """Every worker whose schema carries a confidence block.

    Derived, not hardcoded. The hardcoded tuple this replaces listed seven
    names and therefore silently skipped `conformer`, `implementer` and
    `rebaser` — a guard that reads as complete while covering 7 of 10. Mirrors
    `_confidence_prompt_workers` below, and picks up a future confidence
    worker automatically.
    """
    return sorted(
        name for name, schema in leerie.SCHEMAS.items()
        if isinstance((schema.get("properties") or {}).get("confidence"), dict))


def test_the_derived_worker_set_is_complete(leerie):
    """Anti-vacuity for every guard below that iterates it.

    The hardcoded tuple this replaced covered 7 of 10, silently skipping
    `conformer`, `implementer` and `rebaser` — so the guards passed while
    those three were unchecked. Naming them explicitly makes a future
    regression to a partial set fail loudly."""
    workers = set(_confidence_workers(leerie))
    assert {"conformer", "implementer", "rebaser"} <= workers, (
        f"previously-unguarded workers missing from the derived set: {workers}")
    assert len(workers) == 10, f"expected 10 confidence workers, got {sorted(workers)}"


# ----- the caps are gone, and must stay gone --------------------------------

def test_cap_constants_no_longer_exist(leerie):
    """Deleting the constants is what makes re-adding a cap a deliberate act
    rather than a one-character edit."""
    for name in ("_CONFIDENCE_BASIS_MAX_LENGTH",
                 "_CONFIDENCE_LIST_ITEM_MAX_LENGTH"):
        assert not hasattr(leerie, name), (
            f"{name} is back. The caps were measured non-binding at 0.00% "
            "across 37k real values — re-adding one reintroduces rejection "
            "pressure and mitigates nothing.")


def test_no_maxlength_anywhere_in_the_confidence_block(leerie):
    offenders = [w for w in _confidence_workers(leerie)
                 if "maxLength" in json.dumps(
                     leerie.SCHEMAS[w]["properties"]["confidence"])]
    assert not offenders, f"confidence block carries a maxLength: {offenders}"


def test_a_submission_at_the_observed_maximum_validates(leerie):
    """The falsifier for the whole change: output at the largest size ever
    actually measured must validate. Under the old 2000/500 caps this payload
    was rejected outright."""
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
        },
    }
    jsonschema.validate(payload, leerie.SCHEMAS["planner"])
    json.dumps(payload)


# ----- the flattened shape ---------------------------------------------------

def test_only_axes_and_basis_are_required(leerie):
    conf = leerie._confidence_schema(["fit"])
    assert set(conf["required"]) == {"fit", "basis"}


def test_the_two_arrays_survive_as_optional_properties(leerie):
    """Optional, not deleted: the prompts still ask for them, so the §8
    discipline survives — they simply stop rejecting a correct answer."""
    conf = leerie._confidence_schema(["fit"])
    for field in ("falsifiers_tested", "contradictions_reconciled"):
        assert field in conf["properties"], f"{field} was deleted, not relaxed"
        assert field not in conf["required"], f"{field} is required again"


def test_gap_to_close_is_gone_everywhere(leerie):
    """It was the block's only nested object — the sharpest edge of #49747's
    trigger profile — and nothing decided anything on it (its sole consumer
    was a diagnostic log line, now reading `confidence.basis`)."""
    assert "gap_to_close" not in leerie._confidence_schema(["fit"])["properties"]
    for worker in _confidence_workers(leerie):
        blob = json.dumps(leerie.SCHEMAS[worker])
        assert "gap_to_close" not in blob, f"{worker} still carries gap_to_close"


def test_confidence_block_has_no_nested_object(leerie):
    """The general form of the rule, so a future nested field is caught even
    if it is not named `gap_to_close`."""
    props = leerie._confidence_schema(["fit", "solution"])["properties"]
    nested = [k for k, v in props.items() if v.get("type") == "object"]
    assert not nested, f"nested object(s) reintroduced: {nested}"


def test_a_partial_confidence_block_validates(leerie):
    """The shape that used to be rejected: axes + basis, nothing else."""
    jsonschema = pytest.importorskip("jsonschema")
    jsonschema.validate(
        {"fit": 8.5, "basis": "read all three files"},
        leerie._confidence_schema(["fit"]))


def test_a_block_missing_basis_is_still_rejected(leerie):
    """Anti-vacuity: the relaxation must not have emptied the contract."""
    jsonschema = pytest.importorskip("jsonschema")
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"fit": 8.5}, leerie._confidence_schema(["fit"]))


def test_a_block_missing_its_axis_is_still_rejected(leerie):
    """The axis is the number every §8 gate reads — losing it would silently
    disable the gate rather than fail it."""
    jsonschema = pytest.importorskip("jsonschema")
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"basis": "x"}, leerie._confidence_schema(["fit"]))


def test_confidence_remains_top_level_required(leerie):
    """Unchanged by the flattening, and deliberately so: making the whole
    block optional is a DESIGN §8 contract change, not a schema tweak."""
    for worker in _confidence_workers(leerie):
        assert "confidence" in leerie.SCHEMAS[worker]["required"], worker


# ----- the prompt fragment must not describe limits that no longer exist -----

def test_prompt_fragment_states_no_enforced_limit(leerie):
    """A prompt claiming a bound that the schema does not enforce is worse
    than silence: it makes workers truncate real evidence for nothing."""
    frag = (leerie.PROMPTS / "_confidence.md").read_text()
    assert "characters**" not in frag, (
        "_confidence.md still states a hard character limit")
    for claim in ("rejected outright", "enforced"):
        assert claim not in frag, f"_confidence.md still claims limits are {claim}"


# ----- the fragment must actually reach the workers -------------------------
#
# The deleted `test_every_confidence_worker_is_told_the_limits` was,
# incidentally, the only thing asserting that `_confidence.md` reaches all ten
# confidence-emitting prompts. Its replacement above checks the fragment's
# CONTENT but not its INCLUSION, so dropping an `{{include: _confidence.md}}`
# would silently remove the guidance from that worker with every test green.
#
# The worker set is derived from SCHEMAS rather than hardcoded, so a NEW
# confidence-emitting worker that forgets the include is caught too.


def _confidence_prompt_workers(leerie) -> list[str]:
    out = []
    for name, schema in leerie.SCHEMAS.items():
        if "confidence" not in (schema.get("properties") or {}):
            continue
        if not (leerie.PROMPTS / f"{name}.md").exists():
            continue
        out.append(name)
    return sorted(out)


def test_the_worker_set_is_not_empty(leerie):
    """Anti-vacuity: a derivation that found nothing would make every
    parametrized case below vacuously pass."""
    workers = _confidence_prompt_workers(leerie)
    assert len(workers) >= 9, f"only found {workers}"


def test_every_confidence_prompt_includes_the_fragment(leerie):
    missing = [w for w in _confidence_prompt_workers(leerie)
               if "{{include: _confidence.md}}"
               not in (leerie.PROMPTS / f"{w}.md").read_text()]
    assert not missing, (
        f"confidence-emitting prompts missing the fragment include: {missing}")


def test_the_include_actually_resolves(leerie):
    """Stronger than the literal-string check: `_load_prompt` must expand the
    placeholder, so a renamed or deleted fragment is caught rather than
    shipping the raw `{{include: …}}` text to the model."""
    frag = (leerie.PROMPTS / "_confidence.md").read_text().strip()
    probe = frag.splitlines()[0].lstrip("# ").strip()
    assert probe, "fragment has no usable first line to probe for"
    for worker in _confidence_prompt_workers(leerie):
        text = leerie._load_prompt(worker)
        assert probe in text, f"{worker}: fragment did not expand"
        assert "{{include:" not in text, (
            f"{worker}: an unresolved include placeholder would ship verbatim")
