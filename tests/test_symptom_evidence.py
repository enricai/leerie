"""DESIGN §9 *A stale finding is not a bug* — `check_symptom_evidence`.

Run `fa979580`'s N18 subtask "fixed" a cgroup leak that #190 had already
fixed **before the run started**, and shipped an event-loop stall doing it
(`_cgroup_destroy` is synchronous and was called from async `_invoke`, so
raising its timeout tripled a real stall). Nothing in the run asked whether
the reported symptom still happened.

Three design decisions are pinned here, each of which looks like an omission
until you know the measurement behind it.

**(1) Advisory, not gating.** These strings are logged and attached to the
result; they never reach `check_implementer_output`. A retry cannot make a
stale finding un-stale — it asks the same worker the same question — and a
second *gating* evidence field would stack retry pressure on top of the
production-evidence gate. `test_not_wired_into_the_gating_check` is the pin.

**(2) `bugfix-` only, by id prefix.** Scoped structurally rather than by
reading the task text, per CLAUDE.md's *Language-to-JSON* rule. A feature
subtask has no prior symptom to reproduce, so demanding one would be noise
on the majority of subtasks.

**(3) "Tests fail on base" is NOT this.** Measured on that run, all four
findings' new tests already failed on base (9 of 13 for N14-16) — a new test
against code that does not yet exist trivially fails. The claim wanted here
is behavioural, which is why the field asks for `how` and `observed` rather
than a bare boolean the worker can assert for free.
"""
from __future__ import annotations

import inspect

import pytest


# ---- scoping --------------------------------------------------------------

@pytest.mark.parametrize("sid", ["feat-001", "test-003", "docs-002",
                                 "refactor-001", "infra-004"])
def test_non_bugfix_subtasks_are_silent(leerie, sid):
    """A subtask that is not fixing a reported symptom has none to
    reproduce. Demanding evidence there would fire on most of a plan."""
    assert leerie.check_symptom_evidence({"subtask_id": sid}, {}) == []


def test_id_is_read_from_the_subtask_when_the_result_omits_it(leerie):
    """`subtask_id` is schema-required but the check must not depend on the
    worker having echoed it back correctly to be scoped."""
    out = leerie.check_symptom_evidence(
        {"symptom_evidence": {"reproduced": True, "how": "x"}},
        {"id": "bugfix-002"})
    assert out == []
    # ...and the scoping still applies from that source.
    assert leerie.check_symptom_evidence({}, {"id": "feat-002"}) == []


# ---- the four outcomes ----------------------------------------------------

def test_absent_evidence_is_flagged(leerie):
    out = leerie.check_symptom_evidence({"subtask_id": "bugfix-001"}, {})
    assert len(out) == 1 and out[0].startswith("NO_SYMPTOM_EVIDENCE:")


def test_reproduced_symptom_is_silent(leerie):
    assert leerie.check_symptom_evidence(
        {"subtask_id": "bugfix-001",
         "symptom_evidence": {"reproduced": True, "how": "git stash && ...",
                              "observed": "leaked 3 dirs"}}, {}) == []


def test_unreproduced_symptom_is_surfaced(leerie):
    """The N18 shape verbatim — and the case the whole check exists for.

    Not an error: it is the most useful thing the subtask can report, so the
    message says the work may already be done rather than accusing the
    worker of failing.
    """
    out = leerie.check_symptom_evidence(
        {"subtask_id": "bugfix-005",
         "symptom_evidence": {"reproduced": False,
                              "not_reproduced_reason": "fixed by #190"}}, {})
    assert len(out) == 1
    assert out[0].startswith("SYMPTOM_DID_NOT_REPRODUCE:")
    assert "fixed by #190" in out[0], "the worker's reason must survive"
    assert "already" in out[0]


def test_unreproduced_without_a_reason_still_surfaces(leerie):
    out = leerie.check_symptom_evidence(
        {"subtask_id": "bugfix-005", "symptom_evidence": {"reproduced": False}}, {})
    assert len(out) == 1 and out[0].startswith("SYMPTOM_DID_NOT_REPRODUCE:")
    assert not out[0].rstrip().endswith(":"), "dangling colon with no detail"


@pytest.mark.parametrize("bad", ["yes", 1, None, [], {}])
def test_non_boolean_reproduced_is_flagged(leerie, bad):
    """Keys on `is True` / `is False`, so a truthy string must not read as a
    reproduction."""
    out = leerie.check_symptom_evidence(
        {"subtask_id": "bugfix-001", "symptom_evidence": {"reproduced": bad}}, {})
    assert len(out) == 1 and out[0].startswith("MALFORMED_SYMPTOM_EVIDENCE:")


def test_non_dict_evidence_is_flagged(leerie):
    for bad in ("reproduced", ["reproduced"], 7):
        out = leerie.check_symptom_evidence(
            {"subtask_id": "bugfix-001", "symptom_evidence": bad}, {})
        assert out and out[0].startswith("NO_SYMPTOM_EVIDENCE:")


# ---- schema ---------------------------------------------------------------

def test_schema_shape(leerie):
    """Flat, one required inner bool — the decoder-safety shape
    `_production_evidence_schema` documents (anthropics/claude-code#49747)."""
    sub = leerie.SCHEMAS["implementer"]["properties"]["symptom_evidence"]
    assert sub["required"] == ["reproduced"]
    assert sub["properties"]["reproduced"] == {"type": "boolean"}
    assert set(sub["properties"]) == {
        "reproduced", "how", "observed", "not_reproduced_reason"}
    assert not any(p.get("type") == "object"
                   for p in sub["properties"].values())


def test_schema_field_is_optional(leerie):
    assert "symptom_evidence" not in leerie.SCHEMAS["implementer"]["required"]


def test_not_on_the_conformer_schema(leerie):
    """The conformer did not write the fix and has no base tree to
    reproduce against — asking it would be asking for a guess."""
    assert "symptom_evidence" not in leerie.SCHEMAS["conformer"]["properties"]


# ---- wiring ---------------------------------------------------------------

def test_not_wired_into_the_gating_check(leerie):
    """The load-bearing pin: advisory means advisory.

    `check_implementer_output`'s issues drive `implementer_confidence_retries`.
    Routing this there would retry a worker over a stale finding — which a
    retry cannot fix — and stack pressure on the production-evidence gate.
    """
    src = inspect.getsource(leerie.check_implementer_output)
    assert "check_symptom_evidence" not in src


def test_wired_as_advisory_on_the_success_path(leerie):
    src = inspect.getsource(leerie)
    i = src.index("check_symptom_evidence(res, subtask)")
    window = src[i - 400:i + 300]
    assert 'status == "complete"' in window, \
        "must run only on the success path — a blocked subtask has no claim"
    assert "symptom_warnings" in window, "must be recorded on the result"
    assert "log(" in window, "must be visible to the operator"


def test_prompt_asks_for_the_field(leerie):
    import pathlib
    p = (pathlib.Path(__file__).resolve().parent.parent
         / "prompts" / "implementer.md").read_text()
    assert "symptom_evidence" in p
    assert "not_reproduced_reason" in p
