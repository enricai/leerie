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
    assert leerie.check_symptom_evidence({}, sid) == []


def test_scoping_uses_the_orchestrators_sid_not_the_workers_echo(leerie):
    """The sid comes from the orchestrator, never from `result["subtask_id"]`.

    Nothing in the module cross-checks the worker's echoed `subtask_id`
    against the real id, so scoping on it would let a worker that echoed
    `feat-001` while working on `bugfix-005` slip past the prefix entirely.
    Both assertions below would flip if the echo were trusted.
    """
    assert leerie.check_symptom_evidence(
        {"subtask_id": "feat-001"}, "bugfix-005") != []
    assert leerie.check_symptom_evidence(
        {"subtask_id": "bugfix-005"}, "feat-001") == []


@pytest.mark.parametrize("bad", [None, 3, ["bugfix-001"], {"id": "bugfix-001"}])
def test_non_string_sid_raises_rather_than_silently_passing(leerie, bad):
    """The sibling rule, applied to this function's own parameter.

    `check_implementer_output` refuses `subtask or {}` because an empty dict
    makes `NO_PLANNED_FILES_TOUCHED` unable to fire. `str(sid or "")` here
    was the identical shape: a non-string `sid` produced `""`, which fails
    the `bugfix-` prefix, so the check returned `[]` and silently disabled
    itself. Loud beats quiet for a contract violation, and the two sibling
    checks should be pinned by the same rule rather than two different ones.
    """
    with pytest.raises((AttributeError, TypeError)):
        leerie.check_symptom_evidence(
            {"symptom_evidence": {"reproduced": False}}, bad)


def test_empty_sid_is_accepted_and_silent(leerie):
    """Anti-vacuity partner: `""` is a *string*, so it is not a contract
    violation — it is simply not a bugfix id, and must return `[]` rather
    than raise. The guard must reject non-strings without rejecting this."""
    assert leerie.check_symptom_evidence(
        {"symptom_evidence": {"reproduced": False}}, "") == []


# ---- the four outcomes ----------------------------------------------------

def test_absent_evidence_is_flagged(leerie):
    out = leerie.check_symptom_evidence({}, "bugfix-001")
    assert len(out) == 1 and out[0].startswith("NO_SYMPTOM_EVIDENCE:")


def test_reproduced_symptom_is_silent(leerie):
    assert leerie.check_symptom_evidence(
        {"symptom_evidence": {"reproduced": True, "how": "git stash && ...",
                              "observed": "leaked 3 dirs"}}, "bugfix-001") == []


def test_unreproduced_symptom_is_surfaced(leerie):
    """The N18 shape verbatim — and the case the whole check exists for.

    Not an error: it is the most useful thing the subtask can report, so the
    message says the work may already be done rather than accusing the
    worker of failing.
    """
    out = leerie.check_symptom_evidence(
        {"symptom_evidence": {"reproduced": False,
                              "not_reproduced_reason": "fixed by #190"}}, "bugfix-001")
    assert len(out) == 1
    assert out[0].startswith("SYMPTOM_DID_NOT_REPRODUCE:")
    assert "fixed by #190" in out[0], "the worker's reason must survive"
    assert "already" in out[0]


def test_unreproduced_without_a_reason_still_surfaces(leerie):
    out = leerie.check_symptom_evidence(
        {"symptom_evidence": {"reproduced": False}}, "bugfix-005")
    assert len(out) == 1 and out[0].startswith("SYMPTOM_DID_NOT_REPRODUCE:")
    assert not out[0].rstrip().endswith(":"), "dangling colon with no detail"


@pytest.mark.parametrize("bad", ["yes", 1, None, [], {}])
def test_non_boolean_reproduced_is_flagged(leerie, bad):
    """Keys on `is True` / `is False`, so a truthy string must not read as a
    reproduction."""
    out = leerie.check_symptom_evidence(
        {"symptom_evidence": {"reproduced": bad}}, "bugfix-001")
    assert len(out) == 1 and out[0].startswith("MALFORMED_SYMPTOM_EVIDENCE:")


def test_non_dict_evidence_is_flagged(leerie):
    for bad in ("reproduced", ["reproduced"], 7):
        out = leerie.check_symptom_evidence(
            {"symptom_evidence": bad}, "bugfix-001")
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
    i = src.index("check_symptom_evidence(res, sid)")
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
    i = src.index("check_symptom_evidence(res, sid)")
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
    i = src.index("check_symptom_evidence(res, sid)")
    window = src[i:i + 1200]
    assert 'st.data.get("symptom_findings", {}).pop(sid, None)' in window


def _code_only(src: str) -> str:
    """`src` with comments removed.

    Required for the negative assertion below, and for the reason this
    repo documents repeatedly: the code comment there *names*
    `NO_SYMPTOM_EVIDENCE` while explaining why it is excluded, so a raw
    substring scan matches the prose describing the thing it forbids and
    fails on correct code. `tokenize` rather than a `#` heuristic, so a
    `#` inside a string literal cannot corrupt the result.
    """
    import io
    import tokenize
    out, last_line, last_col = [], 1, 0
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.start[0] > last_line:
            out.append("\n" * (tok.start[0] - last_line))
            last_col = 0
        if tok.type == tokenize.COMMENT:
            last_line, last_col = tok.end
            continue
        if tok.start[1] > last_col:
            out.append(" " * (tok.start[1] - last_col))
        out.append(tok.string)
        last_line, last_col = tok.end
    return "".join(out)


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
