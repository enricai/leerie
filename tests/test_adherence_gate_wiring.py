"""Source-coupling pins for the instruction-adherence gate's wiring seams.

Pins the orchestrator seams that only source inspection can verify —
behavioral driving of the full classify->plan->adherence-gate->execute
pipeline is not something this suite does (mirrors
test_dep_capture_wiring.py's inspect.getsource approach):

  1. phase_adherence_gate invokes BOTH the deterministic
     check_prescribed_command_coverage floor AND the adherence_judge
     worker (the two-stage, corpus-validated design — DESIGN: the floor
     alone is PRIMARY, the judge is SECONDARY).
  2. A violation (floor issue or low adherence score) routes through the
     EXISTING _run_checked_loop planner-retry path — re-invoking phase_plan
     — not a new pause/resume mechanism. Negatively: the gate's source must
     NOT introduce EXIT_NEEDS_ANSWERS / surface_clarification machinery.
  3. adherence_judge's WorkerError degrades (keeps the floor-only verdict,
     never discards the assembled plan) rather than propagating and
     abandoning the plan — mirroring fit_judge's WorkerError->degrade shape
     (leerie.py ~5256-5280).
  4. SCHEMAS["planner"]'s subtask schema carries a structured runs_commands
     array field (language->JSON: the planner declares invoked commands as
     data, not prose the floor would have to re-interpret).
"""
from __future__ import annotations

import inspect

import pytest


# ---------------------------------------------------------------------------
# 1. phase_adherence_gate invokes both the deterministic floor and the judge
# ---------------------------------------------------------------------------

class TestGateInvokesFloorAndJudge:
    """phase_adherence_gate must run both the PRIMARY deterministic floor
    (check_prescribed_command_coverage) and the SECONDARY adherence_judge
    worker — the two-stage composition validated (0/21 false positives) in
    the design's corpus check. Gating on the judge alone was measured to
    false-positive ~12% of ordinary runs."""

    def test_gate_calls_deterministic_floor(self, leerie):
        src = inspect.getsource(leerie.phase_adherence_gate)
        assert "check_prescribed_command_coverage(" in src, (
            "phase_adherence_gate must call the deterministic "
            "check_prescribed_command_coverage floor (PRIMARY layer)"
        )

    def test_gate_invokes_adherence_judge_worker(self, leerie):
        src = inspect.getsource(leerie.phase_adherence_gate)
        assert 'schema_key="adherence_judge"' in src, (
            "phase_adherence_gate must invoke the adherence_judge "
            "worker via claude_p(schema_key=\"adherence_judge\", ...) "
            "(SECONDARY semantic layer)"
        )

    def test_floor_checked_more_than_once(self, leerie):
        """The floor must be consulted on both the fast (judge-succeeded)
        path and the degrade (judge-crashed) path — not only inside the
        judge-success branch, else a judge crash would silently drop the
        model-independent floor verdict too."""
        src = inspect.getsource(leerie.phase_adherence_gate)
        floor_call_count = src.count("check_prescribed_command_coverage(")
        assert floor_call_count >= 2, (
            "check_prescribed_command_coverage must be called on both the "
            "judge-success path and the judge-crashed degrade path "
            f"(found {floor_call_count} call site(s))"
        )

    def test_gate_short_circuits_when_not_prescribed(self, leerie):
        """Free no-op for the ~90% common case: no prescribed procedure
        means neither worker call is worth its spend."""
        src = inspect.getsource(leerie.phase_adherence_gate)
        assert "is_prescribed" in src, (
            "phase_adherence_gate must short-circuit on "
            "prescribed_procedure.is_prescribed being falsy"
        )

    def test_gate_respects_skip_flag(self, leerie):
        src = inspect.getsource(leerie.phase_adherence_gate)
        assert "skip_adherence_check" in src, (
            "phase_adherence_gate must honor st.data['skip_adherence_check'] "
            "(--skip-adherence-check / LEERIE_SKIP_ADHERENCE_CHECK / "
            "skip_adherence_check=true)"
        )


# ---------------------------------------------------------------------------
# 2. Violation routes through the EXISTING _run_checked_loop retry path,
#    NOT a new pause/resume mechanism.
# ---------------------------------------------------------------------------

class TestViolationRoutesThroughExistingRetryLoop:
    """A violation (floor issue or low adherence score) must feed the
    existing `_run_checked_loop` planner-retry path (re-invoking phase_plan)
    -- the same mechanism the reconciler and overlap-judge already use --
    rather than any new pause/resume machinery."""

    def test_gate_calls_run_checked_loop(self, leerie):
        src = inspect.getsource(leerie.phase_adherence_gate)
        assert "_run_checked_loop(" in src, (
            "phase_adherence_gate must drive its retry logic through the "
            "existing _run_checked_loop helper (CRITIC-pattern reuse), not "
            "a bespoke retry loop"
        )

    def test_feedback_callback_reinvokes_phase_plan(self, leerie):
        """On a violation, make_feedback_prompt's callback must re-invoke
        phase_plan — the re-plan IS the feedback, exactly like the
        reconciler/overlap-judge retry shape."""
        src = inspect.getsource(leerie.phase_adherence_gate)
        # Anchor on the make_feedback_prompt callback and confirm phase_plan(
        # is invoked inside it (not just referenced elsewhere in the file).
        cb_idx = src.find("async def _on_feedback")
        assert cb_idx != -1, (
            "phase_adherence_gate must define a make_feedback_prompt "
            "callback (e.g. _on_feedback) for _run_checked_loop"
        )
        next_def_idx = src.find("\n    async def ", cb_idx + 1)
        if next_def_idx == -1:
            next_def_idx = src.find("\n    judge_result, gate_warnings", cb_idx)
        cb_src = src[cb_idx:next_def_idx if next_def_idx != -1 else None]
        assert "phase_plan(" in cb_src, (
            "the feedback callback must re-invoke phase_plan(...) — the "
            "re-plan is the feedback, reusing _run_checked_loop's existing "
            "retry semantics"
        )

    def test_run_checked_loop_bounded_by_judgment_check_rounds(self, leerie):
        src = inspect.getsource(leerie.phase_adherence_gate)
        loop_idx = src.find("_run_checked_loop(")
        assert loop_idx != -1
        # judgment_check_rounds must appear as the max_rounds argument,
        # bounding the retry loop exactly like every other planner-adjacent
        # gate (reconciler, overlap-judge).
        close_idx = src.find(")", loop_idx)
        # Scan forward through the whole call (it's a multi-line call).
        call_end = src.find("\n\n", loop_idx)
        call_src = src[loop_idx:call_end if call_end != -1 else len(src)]
        assert "judgment_check_rounds" in call_src, (
            "phase_adherence_gate's _run_checked_loop call must bound "
            "max_rounds via caps['judgment_check_rounds'], matching every "
            "other exhausted planner-adjacent gate's cap"
        )

    def test_gate_dies_on_exhaustion(self, leerie):
        """On loop exhaustion with remaining issues, the gate must die() —
        exactly like every other exhausted planner gate (reconciler,
        overlap-judge) — never silently proceed with a violating plan."""
        src = inspect.getsource(leerie.phase_adherence_gate)
        assert "die(" in src, (
            "phase_adherence_gate must die() when the retry loop is "
            "exhausted without a clean plan"
        )

    def test_gate_does_not_introduce_new_pause_resume_machinery(self, leerie):
        """NEGATIVE pin (per investigation_notes): the design explicitly
        reuses _run_checked_loop and does NOT add a new pause/resume path.
        The gate's source must not reference EXIT_NEEDS_ANSWERS or
        surface_clarification — introducing either would mean a second,
        undocumented pause mechanism competing with the classifier's
        existing clarification-question flow."""
        src = inspect.getsource(leerie.phase_adherence_gate)
        assert "EXIT_NEEDS_ANSWERS" not in src, (
            "phase_adherence_gate must NOT reference EXIT_NEEDS_ANSWERS — "
            "violations are handled via the existing _run_checked_loop "
            "re-plan path, not a new pause/resume exit code"
        )
        assert "surface_clarification" not in src, (
            "phase_adherence_gate must NOT call surface_clarification — "
            "that is the classifier/implementer clarification-question "
            "seam, not the plan-check retry seam this gate reuses"
        )


# ---------------------------------------------------------------------------
# 3. adherence_judge's WorkerError degrades rather than discarding the plan.
# ---------------------------------------------------------------------------

class TestAdherenceJudgeWorkerErrorDegrades:
    """A WorkerError from the adherence_judge call (infrastructure crash —
    auth failure, killed session, PID exhaustion) must degrade to a
    floor-only verdict and preserve the already-assembled plan, mirroring
    fit_judge's WorkerError->degrade guard (leerie.py ~5256-5280:
    `except WorkerError: log(...); return [subtask]` — crash never discards
    sibling work already paid for)."""

    def test_judge_crash_path_does_not_discard_plan(self, leerie):
        src = inspect.getsource(leerie.phase_adherence_gate)
        # _run_checked_loop absorbs WorkerError internally (retries then
        # returns None on total exhaustion) — the gate's degrade guard is
        # the `judge_result is None` branch that follows.
        none_idx = src.find("if judge_result is None:")
        assert none_idx != -1, (
            "phase_adherence_gate must handle the case where "
            "_run_checked_loop exhausts every round on adherence_judge "
            "crashes (judge_result is None) — the WorkerError->degrade path"
        )

    def test_degrade_path_returns_the_plan_not_none_or_raise(self, leerie):
        """The degrade branch must return the (unmodified-by-judge) plan —
        never raise past it and never return None/empty, which would
        discard every planner call already paid for in this round."""
        src = inspect.getsource(leerie.phase_adherence_gate)
        none_idx = src.find("if judge_result is None:")
        assert none_idx != -1
        # Slice to the next top-level statement after the branch (the
        # non-crash remaining_issues handling) to scope the assertion to
        # just this branch's body.
        next_idx = src.find("remaining_issues = _check_adherence(", none_idx)
        assert next_idx != -1, (
            "expected the judge_result is None branch to precede the "
            "remaining_issues handling"
        )
        branch_src = src[none_idx:next_idx]
        assert "return cur_plans[0]" in branch_src, (
            "the adherence_judge crash-degrade branch must return the "
            "assembled plan (cur_plans[0]), not discard it"
        )

    def test_degrade_path_still_honors_floor_violations(self, leerie):
        """Degrading the JUDGE does not mean degrading the FLOOR — the
        floor is model-independent and must still die() on a genuine
        violation even when the judge itself crashed every round."""
        src = inspect.getsource(leerie.phase_adherence_gate)
        none_idx = src.find("if judge_result is None:")
        assert none_idx != -1
        next_idx = src.find("remaining_issues = _check_adherence(", none_idx)
        branch_src = src[none_idx:next_idx if next_idx != -1 else None]
        assert "die(" in branch_src, (
            "the judge-crash degrade branch must still die() if the "
            "deterministic floor independently found violations — a "
            "crashed judge must not silently waive a model-independent "
            "floor violation"
        )

    def test_run_checked_loop_itself_retries_worker_error(self, leerie):
        """Corroborating pin at the shared helper: _run_checked_loop's own
        WorkerError handling retries (continue) rather than aborting
        (break) — the mechanism phase_adherence_gate relies on to not
        discard the loop on a single transient crash."""
        src = inspect.getsource(leerie._run_checked_loop)
        except_idx = src.find("except WorkerError as exc:")
        assert except_idx != -1, (
            "_run_checked_loop must catch WorkerError separately from "
            "other exceptions"
        )
        next_except_idx = src.find("except Exception as exc:", except_idx)
        assert next_except_idx != -1
        we_branch = src[except_idx:next_except_idx]
        assert "continue" in we_branch, (
            "_run_checked_loop's WorkerError branch must continue (retry "
            "with a fresh worker), not break/abandon the loop"
        )


# ---------------------------------------------------------------------------
# 4. SCHEMAS['planner'] subtask schema carries runs_commands[]
# ---------------------------------------------------------------------------

class TestPlannerSchemaCarriesRunsCommands:
    """The planner subtask schema must gain a structured runs_commands
    array field (language->JSON: the planner declares the commands a
    subtask invokes as data, so the deterministic floor never has to
    re-interpret subtask prose)."""

    def _subtask_item_schema(self, leerie) -> dict:
        planner_schema = leerie.SCHEMAS["planner"]
        subtasks_prop = planner_schema["properties"]["subtasks"]
        return subtasks_prop["items"]

    def test_runs_commands_field_present(self, leerie):
        item = self._subtask_item_schema(leerie)
        assert "runs_commands" in item["properties"], (
            "SCHEMAS['planner'] subtask items must declare a "
            "'runs_commands' property"
        )

    def test_runs_commands_is_array_of_strings(self, leerie):
        item = self._subtask_item_schema(leerie)
        rc = item["properties"]["runs_commands"]
        assert rc.get("type") == "array", (
            "runs_commands must be a JSON array (structured data), not a "
            "free-text field the floor would have to parse"
        )
        assert rc.get("items", {}).get("type") == "string", (
            "runs_commands items must be strings (one entry per command "
            "the subtask invokes)"
        )

    def test_runs_commands_not_required(self, leerie):
        """Most subtasks run no prescribed command — the field must stay
        optional so ordinary plans are unaffected."""
        item = self._subtask_item_schema(leerie)
        assert "runs_commands" not in item.get("required", []), (
            "runs_commands must remain optional on the planner subtask "
            "schema — most subtasks invoke no prescribed command"
        )


# ---------------------------------------------------------------------------
# Sanity: the phase itself is wired into the orchestrate pipeline, after
# phase_overlap_judge and before schedule() — corroborates the _task
# insertion-point claim without duplicating test_phase_adherence_gate.py's
# fuller wiring-order coverage.
# ---------------------------------------------------------------------------

class TestPhaseInsertionPoint:
    def test_phase_adherence_gate_called_after_overlap_judge(self, leerie):
        src = inspect.getsource(leerie)
        overlap_idx = src.find("phase_overlap_judge(")
        gate_idx = src.find("phase_adherence_gate(", overlap_idx)
        schedule_idx = src.find("schedule(", gate_idx) if gate_idx != -1 else -1
        assert overlap_idx != -1 and gate_idx != -1 and schedule_idx != -1, (
            "expected phase_overlap_judge(...) then phase_adherence_gate(...) "
            "then schedule(...) to appear in that order in the orchestrator "
            "source"
        )
