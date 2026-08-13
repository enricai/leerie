"""Every worker prompt must name the fields its schema requires (N29).

`SCHEMAS[w]` and `prompts/<w>.md` are two halves of one contract: the schema
is what the CLI validates against, the prompt is what the worker is told to
produce. When they drift, the prompt actively instructs the worker to emit a
shape the schema does not accept.

This shipped once. #199 flattened `SCHEMAS["conformer"]` --
`rule_violations_fixed`/`rule_violations_residual` collapsed into one
`rule_violations` array keyed by `status`, and `docs_updates`/`tests_updates`
into `file_updates` keyed by `kind` -- but `prompts/conformer.md` was not
migrated in the same change. For three commits the prompt documented four
field names the schema had removed and never mentioned the two it required,
on the worker that runs once per subtask.

Measured at the time: the drift did NOT corrupt output, because the injected
StructuredOutput tool dominates the prose (three live `claude -p` runs against
the new schema plus the stale prompt all returned the new fields correctly,
including one whose user prompt deliberately used the old vocabulary). What
was actually lost is the per-field guidance -- the non-empty `rule` rule and
the worktree-relative `path` containment rule were attached to names that no
longer existed, and nothing told the worker what `status`/`kind` mean.

`tests/test_production_evidence.py::test_conformer_prompt_asks_for_the_field`
is the hand-written precedent for one field on one worker. This generalises it
to every field on every worker, because the next drift will be somewhere else.

Deliberately NOT asserted here: that the prompt describes the field
*correctly*. Only a live run can show that (CLAUDE.md's own central principle
-- prompts are advisory, code enforces). This pins presence, which is the part
a mechanical check can own.
"""
from __future__ import annotations

import json
import pathlib
import re

import pytest

PROMPTS = pathlib.Path(__file__).resolve().parent.parent / "prompts"

# Worker -> set of required property names deliberately absent from its prompt.
# Empty today, and that is the point: adding an entry is a deliberate, reviewed
# act, not the silent default. Prefer fixing the prompt over adding a row here.
ALLOWLIST: dict[str, set[str]] = {}


def _mentions(text: str, name: str) -> bool:
    """Word-boundary match, NOT a substring test.

    Load-bearing: `rule_violations` is a substring of `rule_violations_fixed`,
    so a plain `in` check reports the pre-fix conformer prompt as mentioning a
    field it had actually removed -- the guard would have passed on the exact
    drift it exists to catch. `\\b` fails against a trailing `_` (a word
    character), which is what makes the two distinguishable.
    """
    return re.search(r"\b" + re.escape(name) + r"\b", text) is not None


def _mentions_enum_value(text: str, value: str) -> bool:
    """Enum values are quoted or backticked in prose, never bare -- a bare
    match would accept the English word `fixed` appearing in an unrelated
    sentence, which is common in these prompts."""
    return f'"{value}"' in text or f"`{value}`" in text


def _required_enum_discriminators(schema: dict) -> list[tuple[str, str, str]]:
    """(property, subfield, value) for every REQUIRED enum on an array's items.

    Scoped to required-on-the-item enums because those are the discriminators
    the orchestrator dispatches on -- `_expand_conformer_output` drops an entry
    whose `status`/`kind` it does not recognise, so an unexplained
    discriminator is how a finding disappears.
    """
    out: list[tuple[str, str, str]] = []
    for prop, spec in (schema.get("properties") or {}).items():
        if spec.get("type") != "array":
            continue
        items = spec.get("items") or {}
        item_required = items.get("required") or []
        for sub, sub_spec in (items.get("properties") or {}).items():
            if "enum" in sub_spec and sub in item_required:
                for value in sub_spec["enum"]:
                    out.append((prop, sub, value))
    return out


def _workers_with_prompts(leerie) -> list[str]:
    return sorted(w for w in leerie.SCHEMAS if (PROMPTS / f"{w}.md").exists())


def test_scan_covers_a_meaningful_number_of_workers(leerie):
    """Anti-vacuity: a scan that matches nothing passes forever."""
    workers = _workers_with_prompts(leerie)
    assert len(workers) >= 10, (
        f"only {len(workers)} worker(s) paired a schema with a prompt -- the "
        "pairing rule probably broke, so the parity checks below are hollow"
    )
    assert "conformer" in workers


def test_every_required_property_is_named_in_its_prompt(leerie):
    failures: list[str] = []
    for worker in _workers_with_prompts(leerie):
        text = (PROMPTS / f"{worker}.md").read_text()
        allowed = ALLOWLIST.get(worker, set())
        for prop in leerie.SCHEMAS[worker].get("required") or []:
            if prop in allowed:
                continue
            if not _mentions(text, prop):
                failures.append(f"{worker}.md never names required field {prop!r}")
    assert not failures, "prompt/schema drift:\n  " + "\n  ".join(failures)


def test_every_required_enum_discriminator_is_named_in_its_prompt(leerie):
    failures: list[str] = []
    for worker in _workers_with_prompts(leerie):
        text = (PROMPTS / f"{worker}.md").read_text()
        for prop, sub, value in _required_enum_discriminators(
                leerie.SCHEMAS[worker]):
            if not _mentions_enum_value(text, value):
                failures.append(
                    f"{worker}.md never names {prop}[].{sub} value {value!r} -- "
                    "an entry carrying an unrecognised discriminator is dropped"
                )
    assert not failures, "undocumented discriminators:\n  " + "\n  ".join(failures)


def test_conformer_names_the_flattened_fields_specifically(leerie):
    """Named pin for the drift that actually shipped.

    The sweep above fails with a diff; this fails naming N29, so a future
    reflatten of these two arrays is legible rather than generic.
    """
    text = (PROMPTS / "conformer.md").read_text()
    for field in ("rule_violations", "file_updates"):
        assert _mentions(text, field), f"conformer.md lost {field}"
    for gone in ("rule_violations_fixed", "rule_violations_residual",
                 "docs_updates", "tests_updates"):
        assert not _mentions(text, gone), (
            f"conformer.md still documents {gone}, removed from the schema by "
            "#199's flatten"
        )


def test_guard_fires_on_a_prompt_missing_a_required_field(leerie, tmp_path):
    """Falsification: the check must fail on a prompt that omits a field.

    Verified live against the real pre-fix prompt (`git show HEAD~:...` at the
    time this landed), which the guard flags for `file_updates` plus three
    discriminator values.
    """
    schema = leerie.SCHEMAS["conformer"]
    required = schema.get("required") or []
    assert required, "conformer schema has no required fields to omit"
    victim = required[0]

    text = "\n".join(f"`{p}` is documented." for p in required if p != victim)
    assert not _mentions(text, victim)

    substring_decoy = "rule_violations_fixed and docs_updates are documented."
    assert not _mentions(substring_decoy, "rule_violations"), (
        "word-boundary matching regressed to a substring test -- the pre-fix "
        "conformer prompt would pass this guard again"
    )


def test_no_prompt_documents_a_field_the_schemas_removed(leerie):
    """The cross-referencing case, which the per-worker sweep cannot see.

    `prompts/judge.md` describes the *conformer's* field list so the judge
    can score its structure — a prompt naming another worker's schema. The
    per-worker checks above pair `conformer.md` with `SCHEMAS["conformer"]`
    and never look at `judge.md`, so when #199 flattened the conformer wire
    shape, judge.md kept instructing the judge to expect four fields that no
    longer exist. It would have scored correct output as structurally
    invalid.

    This scans EVERY prompt for the removed names, wherever they appear.
    The orchestrator legitimately still uses them —
    `_expand_conformer_output` fans the flattened arrays back out for
    consumers written against the old shape — so this is scoped to
    `prompts/`, which is product surface sent verbatim to workers.
    """
    removed = ("rule_violations_fixed", "rule_violations_residual",
               "docs_updates", "tests_updates")
    offenders: list[str] = []
    for prompt in sorted(PROMPTS.glob("*.md")):
        text = prompt.read_text()
        for name in removed:
            if _mentions(text, name):
                offenders.append(f"{prompt.name} still documents {name}")
    assert not offenders, (
        "prompt(s) documenting fields removed from SCHEMAS:\n  "
        + "\n  ".join(offenders))


def test_allowlist_is_empty_and_stays_deliberate(leerie):
    """The allowlist is empty, and that is the state worth pinning.

    Every required field on every worker is currently documented, so no
    exemption is needed. Asserting the emptiness makes adding a row a
    deliberate, reviewed act that fails this test first — rather than a
    silent widening nobody sees.

    Written as an explicit assertion because the loop below is vacuous while
    the dict is empty: `for ... in {}` never executes, so a body-only test
    passes unconditionally and cannot be falsified by any product change.
    """
    assert ALLOWLIST == {}, (
        "an exemption was added to the prompt/schema parity allowlist. That "
        "is allowed, but it must be justified here: prefer documenting the "
        "field in its prompt over exempting it, since the exemption applies "
        "forever and silently.")


def test_allowlist_entries_are_real_fields(leerie):
    """If the allowlist is ever populated, a row naming a field that no
    longer exists silently widens the exemption to nothing, hiding a real
    omission behind stale config.

    Vacuous by construction while ALLOWLIST is empty — which is why the test
    above pins the emptiness separately rather than relying on this one.
    """
    for worker, fields in ALLOWLIST.items():
        assert worker in leerie.SCHEMAS, f"allowlist names unknown worker {worker!r}"
        required = set(leerie.SCHEMAS[worker].get("required") or [])
        stale = fields - required
        assert not stale, f"{worker}: allowlist names non-required field(s) {stale}"


def test_schemas_remain_json_serializable(leerie):
    """The parity scan reads schema structure; a schema that cannot round-trip
    would break the CLI's `--json-schema` long before it broke this file."""
    for worker in _workers_with_prompts(leerie):
        json.loads(json.dumps(leerie.SCHEMAS[worker]))


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__])
