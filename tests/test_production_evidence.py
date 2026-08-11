"""DESIGN §9 *Evidence must be production-grounded* — `check_production_evidence`,
its schema, and its wiring.

Every other gate in the run asks whether the code matches its specification.
None asks whether the specification matches reality. Run `fa979580` shipped
four inert fixes past six conformers and a final whole-tree pass at confidence
8.5; the clearest one resolved a repo's declared Node heap against a
`.leerie/config.toml` fixture declaring it inline — a shape **0 of the 5 repos
leerie manages use**, while 2 of 5 declare it in `package.json`. Executed
against the real repos, the shipped resolver returned `None` where the
corrected one returns 8192 and 4096 MiB.

Two traps are pinned here that are not obvious from the test names.

**(1) The gate must fire on ABSENCE.** The field is deliberately optional in
the schema — `_confidence_schema`'s docstring records what requiring a field
costs (`plan_overlap_judge` valid on 40.9% of invocations, 84 of its 85
failures being one required field), and the payload is lost whole, not
field-wise. Optional-in-schema plus gates-on-absence is the combination; drop
either half and the field becomes decorative. `test_absent_evidence_gates`
replays the exact shape every historical result has.

**(2) `exercised: true` with nothing behind it is the bare assertion this
check exists to replace.** A worker can always claim it ran something. The
check cannot verify the claim, but it can refuse the claim that carries
neither a command nor an observation, which is the shape a worker produces
when it is pattern-matching the field rather than running anything.
"""
from __future__ import annotations

import inspect

import pytest


# ---- the check itself -----------------------------------------------------

def test_absent_evidence_gates(leerie):
    """The historical shape: no `production_evidence` key at all.

    This is what every conformer result in run fa979580 looks like, and it
    must not pass silently — that silence IS the defect.
    """
    issues = leerie.check_production_evidence({"subtask_id": "bugfix-001"})
    assert len(issues) == 1
    assert issues[0].startswith("NO_PRODUCTION_EVIDENCE:")


def test_exercised_with_observation_passes(leerie):
    issues = leerie.check_production_evidence({
        "production_evidence": {
            "exercised": True,
            "how": "python3 -c 'print(resolve_blt(Path(\".\")))'",
            "observed": "{'test': 'pnpm run test'} -> heap None",
        }})
    assert issues == []


def test_exercised_with_only_how_passes(leerie):
    """`how` alone is enough — the command is replayable, which is the
    property that makes the claim checkable by a human later."""
    assert leerie.check_production_evidence({
        "production_evidence": {"exercised": True, "how": "pytest -k heap"}}) == []


def test_exercised_with_only_observed_passes(leerie):
    assert leerie.check_production_evidence({
        "production_evidence": {"exercised": True, "observed": "returned None"}}) == []


def test_bare_exercised_claim_gates(leerie):
    """`exercised: true` and nothing else is the assertion this replaces."""
    issues = leerie.check_production_evidence({
        "production_evidence": {"exercised": True}})
    assert len(issues) == 1
    assert issues[0].startswith("UNSUPPORTED_PRODUCTION_EVIDENCE:")


@pytest.mark.parametrize("blank", ["", "   ", "\n"])
def test_whitespace_only_fields_do_not_satisfy_the_claim(leerie, blank):
    """A space is not a command. Guards the `.strip()` in the check."""
    issues = leerie.check_production_evidence({
        "production_evidence": {
            "exercised": True, "how": blank, "observed": blank}})
    assert issues and issues[0].startswith("UNSUPPORTED_PRODUCTION_EVIDENCE:")


def test_unexercisable_with_reason_passes(leerie):
    """"I could not make this fire here" is a legitimate, recorded answer.

    The gate exists to forbid silence, not to forbid the honest negative —
    a Fly-only branch genuinely cannot be exercised on a local run, and a
    gate that refused that would be answered with a fabricated `observed`.
    """
    assert leerie.check_production_evidence({
        "production_evidence": {
            "exercised": False,
            "unexercisable_reason": "path needs a Node repo; this one is Python",
        }}) == []


def test_unexercised_without_reason_gates(leerie):
    issues = leerie.check_production_evidence({
        "production_evidence": {"exercised": False}})
    assert len(issues) == 1
    assert issues[0].startswith("UNEXERCISED_PRODUCTION_PATH:")


@pytest.mark.parametrize("bad", ["yes", 1, None, [], {}])
def test_non_boolean_exercised_gates(leerie, bad):
    """`exercised` is the one field the schema requires, and the check keys
    on `is True` / `is False`. A truthy string must not read as True."""
    issues = leerie.check_production_evidence({
        "production_evidence": {"exercised": bad}})
    assert len(issues) == 1
    assert issues[0].startswith("MALFORMED_PRODUCTION_EVIDENCE:")


def test_non_dict_evidence_gates(leerie):
    for bad in ("exercised", ["exercised"], 3):
        issues = leerie.check_production_evidence({"production_evidence": bad})
        assert issues and issues[0].startswith("NO_PRODUCTION_EVIDENCE:")


# ---- schema ---------------------------------------------------------------

def test_schema_present_on_both_workers(leerie):
    """The implementer is where this is cheapest to ask (it has the repo
    mounted and just wrote the path); the conformer is the independent
    check. Both carry it."""
    for worker in ("implementer", "conformer"):
        props = leerie.SCHEMAS[worker]["properties"]
        assert "production_evidence" in props, worker


def test_schema_field_is_optional_at_the_top_level(leerie):
    """Load-bearing, and the opposite of the obvious choice.

    Requiring it would cost the whole payload on a miss, not just the field
    — the measured outcome recorded in `_confidence_schema`'s docstring.
    Gating happens in `check_production_evidence` instead, which is why
    `test_absent_evidence_gates` is the anti-vacuity partner to this test:
    optional here MUST mean gating there.
    """
    for worker in ("implementer", "conformer"):
        assert "production_evidence" not in leerie.SCHEMAS[worker]["required"], \
            f"{worker}: requiring this field costs the entire submission"


def test_schema_requires_only_exercised(leerie):
    """Flat, one required inner field, and that field a bare bool.

    anthropics/claude-code#49747: the decoder flips to legacy XML mid-
    argument on tool calls with many required parameters mixed with verbose
    strings. `how`/`observed` are the verbose ones and are optional for
    exactly that reason.
    """
    sub = leerie.SCHEMAS["implementer"]["properties"]["production_evidence"]
    assert sub["required"] == ["exercised"]
    assert sub["properties"]["exercised"] == {"type": "boolean"}
    assert set(sub["properties"]) == {
        "exercised", "how", "observed", "unexercisable_reason"}
    # Flat: no nested objects (the sharpest edge of that trigger profile).
    assert not any(p.get("type") == "object"
                   for p in sub["properties"].values())


def test_schema_instances_validate(leerie):
    jsonschema = pytest.importorskip("jsonschema")
    sub = leerie.SCHEMAS["conformer"]["properties"]["production_evidence"]
    jsonschema.validate({"exercised": True, "how": "x", "observed": "y"}, sub)
    jsonschema.validate({"exercised": False,
                         "unexercisable_reason": "no node repo"}, sub)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"how": "x"}, sub)          # exercised missing
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"exercised": "true"}, sub)  # wrong type


# ---- wiring ---------------------------------------------------------------

def test_wired_into_check_implementer_output(leerie):
    """The check is inert unless something calls it.

    Source-coupled because the call sits on the `status == "complete"`
    branch of a function whose other inputs (a real subtask dict, a git
    diff) are expensive to construct here — the behavioural coverage is the
    per-branch tests above.
    """
    src = inspect.getsource(leerie.check_implementer_output)
    assert "check_production_evidence(result)" in src


def test_gate_runs_only_on_a_complete_result(leerie):
    """A blocked or incomplete-handoff implementer has no finished path to
    exercise; demanding evidence there would turn a legitimate blocker into
    a retry loop."""
    issues = leerie.check_implementer_output(
        {"subtask_id": "x", "status": "blocked"}, {}, set())
    assert not any("PRODUCTION_EVIDENCE" in i for i in issues)

    issues = leerie.check_implementer_output(
        {"subtask_id": "x", "status": "complete"}, {}, set())
    assert any(i.startswith("NO_PRODUCTION_EVIDENCE:") for i in issues)


def test_wired_into_the_conformance_phase(leerie):
    """The conformer's copy must be READ, not merely declared.

    Shipped once as a dead field: `_production_evidence_schema()` was on both
    schemas while `check_production_evidence` had exactly one call site, and
    `conformer.md` never mentioned it — so the conformer carried a field
    nothing asked it to fill and nothing consumed, while DESIGN and
    IMPLEMENTATION both described it as consumed.
    """
    src = inspect.getsource(leerie)
    assert "check_production_evidence(conf_res)" in src, \
        "the conformer's production_evidence is declared but never read"


def test_conformer_side_is_advisory_not_gating(leerie):
    """It must extend the conformance WARNINGS, not the gating axis.

    `solution_defects` is deliberately the one gating conformer axis
    (DESIGN §9); routing this into a second blocking gate would let an
    advisory phase stop a run over a field a worker merely omitted.
    """
    src = inspect.getsource(leerie)
    i = src.index("check_production_evidence(conf_res)")
    stmt = src[src.rindex("\n", 0, i) + 1:i + 40]
    assert "conf_warnings.extend" in stmt, (
        f"expected the conformer's evidence to become a warning, got: "
        f"{stmt.strip()!r}")


def test_conformer_evidence_only_read_when_a_result_exists(leerie):
    """A crashed conformer has no result to inspect; calling the check on
    `None` would emit a spurious NO_PRODUCTION_EVIDENCE on top of the crash
    warning that already explains the situation."""
    src = inspect.getsource(leerie)
    i = src.index("check_production_evidence(conf_res)")
    assert "if conf_res is not None:" in src[max(0, i - 900):i]


def test_conformer_prompt_asks_for_the_field(leerie):
    import pathlib
    p = (pathlib.Path(__file__).resolve().parent.parent
         / "prompts" / "conformer.md").read_text()
    assert "production_evidence" in p
    assert "unexercisable_reason" in p
    assert "advisory" in p.lower()


def test_prompt_asks_for_the_field(leerie):
    """§12: code enforces, the prompt documents. A gate the prompt never
    mentions is answered by retries, not by evidence."""
    import pathlib
    p = (pathlib.Path(__file__).resolve().parent.parent
         / "prompts" / "implementer.md").read_text()
    assert "production_evidence" in p
    assert "unexercisable_reason" in p


# ---- the whole-tree final pass (DESIGN §9) --------------------------------

def test_final_conformance_checks_production_evidence(leerie):
    """The last gate before a run is declared done must ask the question too.

    On run `fa979580` the whole-tree pass certified four inert fixes with
    `solution_defects: []` at confidence 8.5 — it has the broadest view of
    any conformer and was the one place the question was never asked. #197
    wired the per-subtask call site and missed this one.
    """
    src = inspect.getsource(leerie._run_final_conformance)
    assert "check_production_evidence(res)" in src


def test_final_conformance_evidence_is_advisory(leerie):
    """Extends `warnings`, never a blocking path — same reasoning as the
    per-subtask site: `solution_defects` is the only gating conformer axis,
    and the final pass must not gain a second way to stop a run."""
    src = inspect.getsource(leerie._run_final_conformance)
    i = src.index("check_production_evidence(res)")
    stmt = src[src.rindex("warnings", 0, i):i]
    assert "warnings.extend" in stmt, (
        f"expected the final pass's evidence to become a warning, got "
        f"{stmt.strip()!r}")


def test_final_conformance_checks_after_shape_validation(leerie):
    """A malformed result `break`s before this; checking a payload that
    failed `_validate_conformance_result` would report a missing field on a
    result that was already rejected for a different reason."""
    src = inspect.getsource(leerie._run_final_conformance)
    assert src.index("_validate_conformance_result(res") < \
        src.index("check_production_evidence(res)")
