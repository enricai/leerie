"""Tests for _schedule()'s handling of blocked planner outputs.

DESIGN §8 planner gate: a planner can exit `status: "blocked"` with an
empty subtasks list. _schedule() must:
  - die with an informative message when ALL planners block (and thus
    no subtasks exist)
  - emit a WARNING and proceed when SOME planners block but at least
    one produced subtasks

The warning path is the P2-1 audit finding: silent loss of a domain is
a footgun, so the partial-block case surfaces a second log line at
scheduling time even though phase_plan already logged each blocked
domain.
"""
from __future__ import annotations

import pytest


def _good_subtask(sid="feat-001", **overrides):
    """A baseline well-formed subtask, overridable per-test."""
    base = {
        "id": sid,
        "title": "a good subtask",
        "depends_on": [],
        "requires": [],
        "provides": [],
        "success_criteria_seed": "the thing is done",
        "size": "small",
    }
    base.update(overrides)
    return base


def _ready_plan(domain: str, *subtasks: dict,
                basis: str | None = None) -> dict:
    """A planner output with status ready and the given subtasks.

    `basis` populates `confidence.basis` (the cleared-but-empty path
    reads this to surface why the planner concluded no work was
    needed); None omits the confidence block, matching the legacy
    callers that don't care about it."""
    plan: dict = {
        "domain": domain,
        "status": "ready",
        "subtasks": list(subtasks),
    }
    if basis is not None:
        plan["confidence"] = {
            "task_understanding": 9.5,
            "decomposition_quality": 9.2,
            "basis": basis,
            "falsifiers_tested": [],
            "contradictions_reconciled": [],
            "gap_to_close": {},
        }
    return plan


def _blocked_plan(domain: str, gap: dict | None = None) -> dict:
    """A planner output with status blocked, empty subtasks, gap analysis."""
    return {
        "domain": domain,
        "status": "blocked",
        "subtasks": [],
        "confidence": {
            "task_understanding": 7.5,
            "decomposition_quality": 6.0,
            "basis": "could not pin the scope",
            "falsifiers_tested": [],
            "contradictions_reconciled": [],
            "gap_to_close": gap or {
                "task_understanding": "need clarification on X",
            },
        },
    }


def test_all_blocked_dies_with_informative_message(leerie, capsys):
    """When every planner blocked, _schedule() dies citing each blocked
    domain and pointing the user at the configurable knob and the gap
    field — not the generic 'no subtasks' message."""
    plans = [
        _blocked_plan("feature-implementation"),
        _blocked_plan("bug-fixing"),
    ]
    with pytest.raises(SystemExit) as exc:
        leerie._schedule(plans)
    assert exc.value.code != 0
    err = capsys.readouterr().err
    # Specific blocked domains are named so the user knows which planners
    # could not clear the gate.
    assert "feature-implementation" in err
    assert "bug-fixing" in err
    # The hint points at the knob and the gap field, not just "failed."
    assert "confidence_rounds" in err.lower() or "confidence-rounds" in err
    # The gap moved from `confidence.gap_to_close` to `confidence.basis` when
    # the confidence block was flattened (DESIGN §8) — the message must name
    # a field that still exists, or it sends the operator looking for nothing.
    assert "confidence.basis" in err
    assert "gap_to_close" not in err


def test_partial_block_emits_warning_and_proceeds(leerie, capsys):
    """When some planners block but at least one produced subtasks,
    _schedule() must succeed AND log a WARNING naming the blocked
    domain(s). Silent loss of a domain is the footgun this test guards
    against."""
    plans = [
        _ready_plan("feature-implementation", _good_subtask("feat-001")),
        _blocked_plan("bug-fixing"),
    ]
    subtasks, waves = leerie._schedule(plans)
    # Scheduling proceeded with the ready domain's subtasks.
    assert "feat-001" in subtasks
    assert any("feat-001" in wave for wave in waves)
    # The blocked domain is named in a WARNING that _schedule() emits
    # to stdout via log() (capsys captures both streams).
    out = capsys.readouterr().out
    assert "WARNING" in out
    assert "bug-fixing" in out


def test_all_ready_no_warning(leerie, capsys):
    """No blocked planners → no WARNING line. Sanity check that the
    warning fires only on the partial-block path, not unconditionally."""
    plans = [
        _ready_plan("feature-implementation", _good_subtask("feat-001")),
        _ready_plan("testing", _good_subtask("test-001")),
    ]
    subtasks, _waves = leerie._schedule(plans)
    assert set(subtasks.keys()) == {"feat-001", "test-001"}
    out = capsys.readouterr().out
    assert "WARNING" not in out


def test_blocked_domain_without_subtasks_does_not_contribute_provides(leerie):
    """Sanity: a blocked planner has subtasks=[], so it provides nothing.
    A ready sibling that requires a capability the blocked planner would
    have provided will fail _validate_plan (tested separately); _schedule()
    itself just won't see that capability in the providers map.

    This test pins the upstream fact: _schedule() merges only the
    subtasks of ready (or empty-but-ready) plans, never fabricates
    'provides' on behalf of a blocked planner."""
    plans = [
        _ready_plan("feature-implementation", _good_subtask("feat-001")),
        _blocked_plan("refactoring"),
    ]
    subtasks, _waves = leerie._schedule(plans)
    # Only the ready domain's subtask is in the merged map.
    assert list(subtasks.keys()) == ["feat-001"]


# ----- _detect_no_work (DESIGN §8 cleared-but-empty terminal state) ---------

def test_detect_no_work_all_ready_empty(leerie):
    """All planners returned status=ready with no subtasks → returns
    a {domain: basis} map quoting each planner's confidence.basis.

    This is the cleared-but-empty terminal state: each planner cleared
    its gate and confirmed the task is already satisfied on HEAD.
    `_orchestrate` short-circuits on this and exits 0."""
    plans = [
        _ready_plan("bug-fixing", basis="HEAD already ships the fix"),
        _ready_plan("testing", basis="regression test already exists"),
    ]
    out = leerie._detect_no_work(plans)
    assert out == {
        "bug-fixing": "HEAD already ships the fix",
        "testing": "regression test already exists",
    }


def test_detect_no_work_returns_none_when_any_subtasks(leerie):
    """If any planner produced subtasks, that's a normal run — return
    None so _schedule() proceeds. An empty-but-ready sibling simply
    contributes zero subtasks to the merged plan, which is fine."""
    plans = [
        _ready_plan("bug-fixing", basis="nothing to fix"),
        _ready_plan("feature-implementation",
                    _good_subtask("feat-001"), basis="one subtask"),
    ]
    assert leerie._detect_no_work(plans) is None


def test_detect_no_work_returns_none_when_any_blocked(leerie):
    """An empty-ready + blocked mix is NOT a no-work outcome — a
    blocker is a gate failure the user must see. Return None so
    _schedule()'s existing all-blocked die() path can fire."""
    plans = [
        _ready_plan("bug-fixing", basis="nothing to fix"),
        _blocked_plan("testing"),
    ]
    assert leerie._detect_no_work(plans) is None
    # And the existing all-blocked die() still fires for the
    # blocked-only case (regression guard that _detect_no_work didn't
    # accidentally swallow the blocked path).
    blocked_only = [_blocked_plan("bug-fixing"), _blocked_plan("testing")]
    assert leerie._detect_no_work(blocked_only) is None
    with pytest.raises(SystemExit):
        leerie._schedule(blocked_only)


def test_detect_no_work_basis_missing_falls_back(leerie):
    """A ready-empty plan missing confidence.basis (or with a
    malformed confidence block) must still produce an entry in the
    output map — falling back to a placeholder string rather than
    raising. The planner schema requires `basis`, but the function's
    job is to surface reasoning, not re-validate the schema."""
    # Missing confidence entirely.
    plans_a = [{"domain": "bug-fixing", "status": "ready", "subtasks": []}]
    out_a = leerie._detect_no_work(plans_a)
    assert out_a is not None
    assert "bug-fixing" in out_a
    assert out_a["bug-fixing"]  # non-empty placeholder
    # Confidence present but basis empty/non-string.
    plans_b = [{
        "domain": "testing",
        "status": "ready",
        "subtasks": [],
        "confidence": {"basis": "   "},  # whitespace only
    }]
    out_b = leerie._detect_no_work(plans_b)
    assert out_b is not None
    assert out_b["testing"]  # non-empty placeholder
    plans_c = [{
        "domain": "configuration-build",
        "status": "ready",
        "subtasks": [],
        "confidence": None,  # malformed
    }]
    out_c = leerie._detect_no_work(plans_c)
    assert out_c is not None
    assert out_c["configuration-build"]


def test_detect_no_work_empty_plans_returns_none(leerie):
    """No plans at all → return None. (Defensive; the orchestrator
    won't reach phase 3 with an empty plans list, but the helper
    shouldn't crash if it ever does.)"""
    assert leerie._detect_no_work([]) is None


# ----- source-text coupling: pin the call site -----------------------------

def test_orchestrate_calls_detect_no_work_between_reconcile_and_schedule():
    """The cleared-but-empty short-circuit (DESIGN §8) must run after
    phase_reconcile (so the reconciler has had its chance to add
    subtasks via `added_subtasks`) and before _schedule (which would
    otherwise hit the all-blocked die() path or proceed with an empty
    plan). Pin the order so a refactor that moves the check elsewhere
    fails this test instead of silently breaking the no-work flow.

    Mirrors test_orchestrate_call_sites.py's source-text coupling
    pattern."""
    from pathlib import Path
    body = (Path(__file__).resolve().parent.parent
            / "orchestrator" / "leerie.py").read_text()
    # Restrict to the _run_phases body the way the other coupling
    # tests do — find the function and bound by the next top-level
    # def.
    start = body.find("async def _run_phases(")
    assert start >= 0, "_run_phases function not found in leerie.py"
    rest = body[start:]
    end = rest.find("\nasync def ", 1)
    if end < 0:
        end = rest.find("\ndef ", 1)
    fn = rest[:end if end > 0 else len(rest)]
    reconcile_idx = fn.find("await phase_reconcile(")
    detect_idx = fn.find("_detect_no_work(")
    schedule_idx = fn.find("_schedule(plans)")
    assert reconcile_idx >= 0
    assert detect_idx >= 0, (
        "_run_phases must call _detect_no_work() — without it, a run "
        "where every planner returns status=ready with empty subtasks "
        "dies on 'planners produced no subtasks' instead of exiting "
        "cleanly. See DESIGN §8 *The cleared-but-empty terminal "
        "state*.")
    assert schedule_idx >= 0
    assert reconcile_idx < detect_idx < schedule_idx, (
        "ordering must be: phase_reconcile → _detect_no_work → "
        "_schedule. _detect_no_work must come after the reconciler "
        "(so any added_subtasks land in the plan first) and before "
        "_schedule (which would otherwise die on empty subtasks).")
    # And the branch must call _finish_no_work_run + return on the
    # no-work path. A `_detect_no_work` call whose result is ignored
    # would not short-circuit phase_execute.
    finish_idx = fn.find("_finish_no_work_run(")
    return_idx = fn.find("return", detect_idx)
    assert finish_idx > detect_idx, (
        "_run_phases must call _finish_no_work_run() to record the "
        "no-work outcome and write finished_at — otherwise leerie list "
        "won't show the run as done.")
    assert return_idx > detect_idx, (
        "_run_phases must return after _finish_no_work_run() so "
        "phase_execute / phase_finalize are skipped — they would "
        "otherwise try to set up and push a non-existent run branch.")


def test_orchestrate_resume_guard_for_no_work_required():
    """A `resume` of a finished no-work run must short-circuit
    before phase_execute. Without this guard, phase_execute would
    call setup-run.sh (creating a fresh empty run branch that didn't
    exist before), iterate zero waves, then phase_finalize's
    finalize.sh would fail its non-empty-branch check and die with
    `finalize failed: nothing to finalize`. Pin the guard so a future
    refactor that removes it fails THIS test instead of breaking
    every `resume` of a no-work run.

    Mirrors the existing source-text coupling pattern from
    test_orchestrate_call_sites.py."""
    from pathlib import Path
    body = (Path(__file__).resolve().parent.parent
            / "orchestrator" / "leerie.py").read_text()
    start = body.find("async def _run_phases(")
    assert start >= 0
    rest = body[start:]
    end = rest.find("\nasync def ", 1)
    if end < 0:
        end = rest.find("\ndef ", 1)
    fn = rest[:end if end > 0 else len(rest)]
    # The guard must sit inside the resume branch.
    resume_idx = fn.find("if args.resume:")
    else_idx = fn.find("\n    else:", resume_idx)
    guard_idx = fn.find('if st.data.get("no_work_required"):')
    assert guard_idx >= 0, (
        "_run_phases must guard against re-resuming a finished "
        "no-work run. Without it, phase_execute creates a fresh "
        "empty run branch and finalize.sh fails. Add: "
        '`if st.data.get("no_work_required"): log(...); return` '
        "in the resume branch.")
    assert resume_idx < guard_idx < else_idx, (
        "the no_work_required guard must live inside the "
        "`if args.resume:` block — the initial-run path handles the "
        "no-work case via _detect_no_work() between phase_reconcile "
        "and _schedule.")
    # And the guard must return.
    return_after_guard = fn.find("return", guard_idx)
    next_section = fn.find("\n        if ", guard_idx + 1)
    assert return_after_guard > guard_idx, (
        "the no_work_required guard must `return` from _run_phases "
        "so phase_execute / phase_finalize are skipped.")
    if next_section > 0:
        assert return_after_guard < next_section, (
            "the `return` must be inside the no_work_required guard "
            "block, not after it.")


# ----- the blocked-planner gap diagnostic -----------------------------------
#
# `phase_plan`'s per-category summary renders a blocked planner's gap via
# `_format_blocked_gap`. The gap moved from `confidence.gap_to_close` (a
# compact dict, frequently `{}`) to `confidence.basis` when the confidence
# block was flattened (DESIGN §8) — and `basis` is prose, so the line needs
# collapsing and truncating that the dict never did. Lives here rather than in
# its own file because this module already owns what the operator sees when
# planners block.

# The largest `confidence.basis` observed across the run corpus. Imported
# rather than redefined: `test_confidence_length_caps` owns that measurement
# (it is the file about basis lengths), and duplicating it would give a single
# measured fact two places to be updated. Cross-test-file import is the
# established pattern here — see `tests/test_ec2_launcher_credentials.py` and
# `tests/test_fetch_branch_leerie_streamback.py`.
from tests.test_confidence_length_caps import _MEASURED_MAX_BASIS


def test_short_gap_passes_through_verbatim(leerie):
    """The common case must not be mangled by the truncation machinery."""
    text = "need a deterministic repro for the timeout"
    assert leerie._format_blocked_gap({"basis": text}) == text


def test_a_maximal_basis_is_cut_with_a_visible_marker(leerie):
    """A silent cut would read as a planner that stopped mid-sentence."""
    out = leerie._format_blocked_gap({"basis": "x" * _MEASURED_MAX_BASIS})
    assert out.endswith("… [truncated; see log]")
    assert len(out) < _MEASURED_MAX_BASIS
    assert out.startswith("x" * 50)


def test_embedded_newlines_collapse_to_one_line(leerie):
    """`basis` is prose and routinely contains newlines. The summary is one
    line per category, so a raw newline would split one domain's status across
    several rows and desynchronise the block."""
    out = leerie._format_blocked_gap({"basis": "line one\n\nline two\ttabbed"})
    assert "\n" not in out and "\t" not in out
    assert out == "line one line two tabbed"


@pytest.mark.parametrize("conf", [None, {}, {"basis": ""}, {"basis": None},
                                  {"fit": 9.0}, "not-a-dict", []])
def test_absent_or_malformed_confidence_yields_empty_string(leerie, conf):
    """`""`, never None and never a raise: the caller interpolates this into an
    f-string, so returning None would print the literal "None" and a raise
    would kill a run over a diagnostic."""
    assert leerie._format_blocked_gap(conf) == ""


def test_the_cap_still_binds_on_real_output(leerie):
    """Anti-vacuity, and the whole reason this file exists.

    A cap set above the real distribution is inert — exactly the failure the
    deleted `maxLength` caps demonstrated (measured non-binding at 0.00%, see
    `tests/test_confidence_length_caps.py`). Raising this constant past the
    measured maximum would silently disable the truncation while every other
    test here kept passing."""
    assert leerie._BLOCKED_GAP_LOG_MAX < _MEASURED_MAX_BASIS


def test_phase_plan_uses_the_helper(leerie):
    """Source-coupling guard: the helper is inert if the call site inlines its
    own copy again, which is how this logic shipped untested in the first
    place."""
    import inspect
    src = inspect.getsource(leerie.phase_plan)
    assert "_format_blocked_gap(" in src
    assert "BLOCKED (planner gate)" in src
