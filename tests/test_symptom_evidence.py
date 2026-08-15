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
from tests.source_strip import code_only as _code_only   # single owner; see that module


# ---- scoping --------------------------------------------------------------
#
# Scoping is by the planner's `fixes_reported_symptom` declaration, NOT by the
# subtask id. The id-prefix version produced 10 of 10 false positives across the
# run corpus, because two mechanisms mint a `bugfix-` id onto work that fixes no
# symptom: `_repair_prescribed_commands` synthesises `{prefix}{900+n:03d}` from
# the HOST subtask's domain, and a duplicate-provider/overlap merge re-homes a
# `feat-` subtask under a surviving `bugfix-` id.
# See docs/POSTMORTEM-2026-08-14.md, F18.

@pytest.mark.parametrize("sid", ["feat-001", "test-003", "docs-002",
                                 "refactor-001", "infra-004", "bugfix-007"])
def test_undeclared_subtasks_are_silent_whatever_their_id(leerie, sid):
    """No declaration means no symptom to reproduce — including for a
    `bugfix-` id, which is the whole point of the change."""
    assert leerie.check_symptom_evidence({}, sid, False) == []


def test_the_synthesised_verification_subtask_is_silent(leerie):
    """`bugfix-901`, the canonical false positive.

    `_repair_prescribed_commands` names its verification-only subtask from the
    host subtask's domain, so on a bug-fixing host it is `bugfix-901` — and the
    old prefix scope demanded a symptom repro from it. It sets
    `fixes_reported_symptom: False` explicitly.
    """
    assert leerie.check_symptom_evidence(
        {"symptom_evidence": {"reproduced": False}}, "bugfix-901", False) == []


def test_a_declared_symptom_fixer_is_checked_whatever_its_id(leerie):
    """The converse, and the reason the id was never the right signal.

    A merge can re-home a genuine symptom fix under a `feat-` id. The
    declaration follows the work; the id does not.
    """
    out = leerie.check_symptom_evidence({}, "feat-011", True)
    assert len(out) == 1 and out[0].startswith("NO_SYMPTOM_EVIDENCE:")


def test_scoping_ignores_the_workers_echoed_subtask_id(leerie):
    """Nothing cross-checks `result["subtask_id"]` against the real id, so it
    must not influence scoping. Both assertions flip if the echo is trusted."""
    assert leerie.check_symptom_evidence(
        {"subtask_id": "feat-001"}, "bugfix-005", True) != []
    assert leerie.check_symptom_evidence(
        {"subtask_id": "bugfix-005"}, "feat-001", False) == []


@pytest.mark.parametrize("bad", [None, 3, "true", ["x"], {"a": 1}])
def test_non_bool_declaration_raises_rather_than_silently_passing(leerie, bad):
    """The sibling rule, applied to the parameter that now decides scope.

    `check_implementer_output` refuses `subtask or {}` because an empty dict
    makes `NO_PLANNED_FILES_TOUCHED` unable to fire. A coerced declaration is
    the identical shape: anything falsy silently disables this check. Loud
    beats quiet for a contract violation.
    """
    with pytest.raises(TypeError):
        leerie.check_symptom_evidence(
            {"symptom_evidence": {"reproduced": False}}, "bugfix-001", bad)


# ---- the four outcomes ----------------------------------------------------

def test_absent_evidence_is_flagged(leerie):
    out = leerie.check_symptom_evidence({}, "bugfix-001", True)
    assert len(out) == 1 and out[0].startswith("NO_SYMPTOM_EVIDENCE:")


def test_reproduced_symptom_is_silent(leerie):
    assert leerie.check_symptom_evidence(
        {"symptom_evidence": {"reproduced": True, "how": "git stash && ...",
                              "observed": "leaked 3 dirs"}}, "bugfix-001", True) == []


def test_unreproduced_symptom_is_surfaced(leerie):
    """The N18 shape verbatim — and the case the whole check exists for.

    Not an error: it is the most useful thing the subtask can report, so the
    message says the work may already be done rather than accusing the
    worker of failing.
    """
    out = leerie.check_symptom_evidence(
        {"symptom_evidence": {"reproduced": False,
                              "not_reproduced_reason": "fixed by #190"}}, "bugfix-001", True)
    assert len(out) == 1
    assert out[0].startswith("SYMPTOM_DID_NOT_REPRODUCE:")
    assert "fixed by #190" in out[0], "the worker's reason must survive"
    assert "already" in out[0]


def test_unreproduced_without_a_reason_still_surfaces(leerie):
    out = leerie.check_symptom_evidence(
        {"symptom_evidence": {"reproduced": False}}, "bugfix-005", True)
    assert len(out) == 1 and out[0].startswith("SYMPTOM_DID_NOT_REPRODUCE:")
    assert not out[0].rstrip().endswith(":"), "dangling colon with no detail"


@pytest.mark.parametrize("bad", ["yes", 1, None, [], {}])
def test_non_boolean_reproduced_is_flagged(leerie, bad):
    """Keys on `is True` / `is False`, so a truthy string must not read as a
    reproduction."""
    out = leerie.check_symptom_evidence(
        {"symptom_evidence": {"reproduced": bad}}, "bugfix-001", True)
    assert len(out) == 1 and out[0].startswith("MALFORMED_SYMPTOM_EVIDENCE:")


def test_non_dict_evidence_is_flagged(leerie):
    for bad in ("reproduced", ["reproduced"], 7):
        out = leerie.check_symptom_evidence(
            {"symptom_evidence": bad}, "bugfix-001", True)
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
    i = src.index("_sym_findings = check_symptom_evidence(")
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


# ---- persistence + surfacing ---------------------------------------------

def test_findings_are_persisted_not_just_logged(leerie):
    """`phase_execute` keeps results in memory and writes only `blocked`
    reasons out of them, so a result-only record dies with the process —
    while "this bugfix may be re-fixing something already fixed" is exactly
    what belongs in the run record. Same argument, and same shape, as
    `unreviewed_subtasks`."""
    src = inspect.getsource(leerie)
    i = src.index("_sym_findings = check_symptom_evidence(")
    window = src[i:i + 1200]
    assert 'st.data.setdefault("symptom_findings", {})[sid]' in window


def test_state_key_is_declared_and_adjacent(leerie):
    """`STATE_FIELDS` is checked against the IMPLEMENTATION.md §8 table in
    both directions by `test_state_fields.py`; adjacency to
    `unreviewed_subtasks` keeps the two operator-facing signals together."""
    fields = list(leerie.STATE_FIELDS)
    assert "symptom_findings" in fields
    assert abs(fields.index("symptom_findings")
               - fields.index("unreviewed_subtasks")) == 1


def test_a_clean_later_attempt_clears_a_stale_entry(leerie):
    """`_settle_subtask` is a `while True:` loop and the completeness gate
    re-drives, so an append-only record would keep reporting a finding a
    later attempt no longer makes — the same staleness bug already fixed
    once for `unreviewed_subtasks`."""
    src = inspect.getsource(leerie)
    i = src.index("_sym_findings = check_symptom_evidence(")
    window = src[i:i + 1200]
    assert 'st.data.get("symptom_findings", {}).pop(sid, None)' in window


def test_summary_surfaces_only_the_actionable_finding(leerie):
    """`NO_SYMPTOM_EVIDENCE` is worker hygiene and would fire on most runs
    until the field is adopted. A summary line that fires every run is how a
    warning stops being read — the failure mode the satisfied-rescue
    sentinel is deliberately excluded from `unreviewed_subtasks` to avoid."""
    src = _code_only(inspect.getsource(leerie.phase_finalize))
    assert "symptom_findings" in src
    assert "SYMPTOM_DID_NOT_REPRODUCE" in src
    assert "NO_SYMPTOM_EVIDENCE" not in src


def test_state_round_trips(leerie, tmp_path):
    root = tmp_path / ".leerie"
    (root / "runs" / "r1").mkdir(parents=True)
    st = leerie.State(root, "r1")
    st.data = {"task": "t", "worker_count": 0,
               "symptom_findings": {"bugfix-005": ["SYMPTOM_DID_NOT_REPRODUCE: x"]}}
    st.save()
    import json
    on_disk = json.loads((root / "runs" / "r1" / "state.json").read_text())
    assert on_disk["symptom_findings"]["bugfix-005"] == [
        "SYMPTOM_DID_NOT_REPRODUCE: x"]
