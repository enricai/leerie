"""The advisory-vs-gating split (DESIGN §8 *Independent adversarial
verification*, §9 anti-gaming): the self-graded confidence axes are demoted to
advisory, and the independent verifiers are the load-bearing gates that key on
INDEPENDENTLY-CONSTRUCTED CONCRETE DEFECTS — never on a self-assertable or
lowerable signal.

This mirrors test_check_functions.py::TestAdherenceGateAdvisoryVsGating (the
planner's decomposition_quality demotion) for the five newly-gated self-graders.
"""
from __future__ import annotations

import inspect


class TestConformerConfidenceIsAdvisory:
    """The conformer's `confidence.conformance` self-score does NOT gate; the
    independent `solution_defects` axis does."""

    def test_conformance_axis_is_advisory_in_settle_subtask(self, leerie):
        """settle_subtask gates on actionable_solution_defects, not on the
        conformer's confidence.conformance self-score."""
        src = inspect.getsource(leerie.settle_subtask)
        # The gating branch keys on solution_defects, not on a conformance
        # confidence number.
        assert "actionable_solution_defects(conf_res)" in src
        assert 'conf_res.get("confidence")' not in src

    def test_actionable_filter_requires_concrete_case(self, leerie):
        """The gate cannot fire on a self-assertable/vague signal — a defect
        must carry a concrete_case AND where, or it is dropped."""
        src = inspect.getsource(leerie.actionable_solution_defects)
        assert "concrete_case" in src
        assert "where" in src

    def test_solution_defects_is_a_required_schema_field(self, leerie):
        assert "solution_defects" in leerie.SCHEMAS["conformer"]["required"]

    def test_conformance_confidence_still_present_but_advisory(self, leerie):
        """The self-score stays in the schema (the §8/§12 discipline record)
        but is no longer the gate."""
        conf = leerie.SCHEMAS["conformer"]["properties"]["confidence"]
        assert conf["type"] == "object"


class TestDemotedSelfScoresDoNotGate:
    """DESIGN §8: for every worker that now has an independent verifier, the
    worker's own confidence self-score is NO LONGER a gate. A low self-score
    must produce no LOW_CONFIDENCE issue. (The `confidence` object stays in the
    schema as the §8 discipline record — only the gate is removed.)"""

    def test_classifier_low_self_score_does_not_gate(self, leerie, tmp_path):
        result = {"categories": ["testing"], "questions": [],
                  "confidence": {"classification": 1.0, "basis": "",
                                 "falsifiers_tested": [],
                                 "contradictions_reconciled": [],
                                 "gap_to_close": {}}}
        issues = leerie.check_classifier_output(result, tmp_path)
        assert not any("LOW_CONFIDENCE" in i for i in issues)

    def test_reconciler_low_self_score_does_not_gate(self, leerie):
        output = {"renames": [], "added_subtasks": [],
                  "confidence": {"reconciliation": 1.0, "basis": "",
                                 "falsifiers_tested": [],
                                 "contradictions_reconciled": [],
                                 "gap_to_close": {}}}
        issues = leerie.check_reconciler_output(output, [{"subtasks": []}])
        assert not any("LOW_CONFIDENCE" in i for i in issues)

    def test_overlap_judge_low_self_score_does_not_gate(self, leerie, tmp_path):
        """The overlap judge is itself an adversarial check on the plan; a
        second self-score gate on ITS OWN judgment would be self-scoring one
        level removed. Its deterministic validators (PHANTOM_ARTIFACT,
        NO_FILE_OVERLAP, DROP_BREAKS_GRAPH) remain the sole gate."""
        output = {"collisions": [],
                  "confidence": {"judgment": 1.0, "basis": "",
                                 "falsifiers_tested": [],
                                 "contradictions_reconciled": [],
                                 "gap_to_close": {}}}
        issues = leerie.check_overlap_judge_output(
            output, [{"subtasks": []}], tmp_path)
        assert not any("LOW_CONFIDENCE" in i for i in issues)

    def test_overlap_judge_deterministic_violation_still_gates(
            self, leerie, tmp_path):
        """Even with a demoted self-score, a genuine deterministic violation
        (here: a collision naming two subtasks with no shared
        files_likely_touched) still gates."""
        output = {"collisions": [{
            "a_sid": "feat-001", "b_sid": "feat-002",
            "artifact": "some thing", "resolution": "merge",
            "reason": "overlap"}],
            "confidence": {"judgment": 9.9, "basis": "",
                           "falsifiers_tested": [],
                           "contradictions_reconciled": [],
                           "gap_to_close": {}}}
        plans = [{"subtasks": [
            {"id": "feat-001", "files_likely_touched": ["src/a.ts"]},
            {"id": "feat-002", "files_likely_touched": ["src/b.ts"]},
        ]}]
        issues = leerie.check_overlap_judge_output(output, plans, tmp_path)
        assert any("NO_FILE_OVERLAP" in i for i in issues)

    def test_planner_low_self_score_does_not_gate(self, leerie, tmp_path):
        """The planner's `task_understanding` self-score is NO LONGER a
        gating axis — the independent task_coverage_judge is authoritative. A
        low self-score must NOT produce LOW_CONFIDENCE."""
        plan = {"subtasks": [], "status": "ready", "domain": "testing",
                "confidence": {"task_understanding": 2.0,
                               "decomposition_quality": 9.5,
                               "basis": "",
                               "falsifiers_tested": [],
                               "contradictions_reconciled": [],
                               "gap_to_close": {}}}
        issues = leerie.check_planner_output(plan, tmp_path, "testing")
        assert not any("LOW_CONFIDENCE" in i for i in issues)

    def test_integrator_low_self_score_does_not_gate(self, leerie):
        """The integrator's `resolution` self-score is NO LONGER a gating
        axis — the independent integration_judge is authoritative. A low
        self-score must NOT produce LOW_CONFIDENCE."""
        result = {"confidence": {"resolution": 7.5,
                                 "basis": "",
                                 "falsifiers_tested": [],
                                 "contradictions_reconciled": [],
                                 "gap_to_close": {}}}
        issues = leerie.check_integrator_output(result)
        assert not any("resolution" in i.lower() for i in issues)
        assert not any("LOW_CONFIDENCE" in i for i in issues)

    def test_provision_low_self_score_does_not_gate(self, leerie, tmp_path):
        result = {"recipe": [{"kind": "install", "command": ["true"],
                              "working_dir": "."}],
                  "confidence": {"recipe_correctness": 1.0, "basis": "",
                                 "falsifiers_tested": [],
                                 "contradictions_reconciled": [],
                                 "gap_to_close": {}}}
        issues = leerie.check_provision_output(result, tmp_path)
        assert not any("LOW_CONFIDENCE" in i for i in issues)

    def test_implementer_self_score_gate_is_gone(self, leerie):
        """settle_subtask no longer runs the root_cause/solution self-score
        gate — the conformer solution_defects axis is authoritative. The old
        '_confidence_axes_clear' call and 'confidence gate not cleared' log
        must be absent from settle_subtask."""
        src = inspect.getsource(leerie.settle_subtask)
        assert "_confidence_axes_clear" not in src
        assert "confidence gate not cleared" not in src

    def test_confidence_axes_clear_helper_removed(self, leerie):
        """The self-score helper itself was dead code after the gate removal
        and was deleted."""
        assert not hasattr(leerie, "_confidence_axes_clear")


class TestVerifierGatesKeyOnConcreteDefects:
    """Each new gate keys on a NON-EMPTY array of concretely-named found
    defects — the §9 anti-gaming property: no lowerable numeric bar."""

    def test_classification_gate_keys_on_miscategorizations(self, leerie):
        src = inspect.getsource(leerie.phase_classification_gate)
        assert "miscategorizations" in src
        # No numeric-threshold gate.
        assert "instruction_adherence" not in src

    def test_classification_gate_requires_concrete_evidence(self, leerie):
        """A miscategorization gates only with concrete_work_evidence."""
        src = inspect.getsource(leerie.phase_classification_gate)
        assert "concrete_work_evidence" in src

    def test_wiring_gate_keys_on_wiring_defects(self, leerie):
        src = inspect.getsource(leerie.phase_wiring_gate)
        assert "wiring_defects" in src
        assert "concrete_reason" in src

    def test_wiring_gate_only_gates_on_live_defect_severity(self, leerie):
        """A non-empty wiring_defects array alone is not sufficient to
        gate — only a live_defect-severity entry is (run
        d8302c0d46d8..., barnacle, 2026-07-31: the judge's own rationale
        called a flagged item a latent fragility, not a live defect, and
        the gate died anyway before this distinction existed)."""
        src = inspect.getsource(leerie.phase_wiring_gate)
        assert "severity" in src
        assert "live_defect" in src
        assert "latent_risk" in src

    def test_provision_gate_keys_on_recipe_failures(self, leerie):
        src = inspect.getsource(leerie.phase_provision_gate)
        assert "recipe_failures" in src
        assert "concrete_reason" in src


class TestVerifiersCarryNoSelfConfidence:
    """The verifiers are themselves the independent check — none carries a
    _confidence_schema, so they cannot re-introduce self-grading."""

    def test_no_verifier_has_a_confidence_axis(self, leerie):
        for w in ("classification_judge", "wiring_judge", "provision_judge"):
            schema = leerie.SCHEMAS[w]
            assert "confidence" not in schema.get("properties", {}), w
            assert "confidence" not in schema["required"], w

    def test_verifiers_default_via_model_default_fallback(self, leerie):
        for w in ("classification_judge", "wiring_judge", "provision_judge"):
            assert w not in leerie.MODEL_DEFAULT_PER_WORKER, w


class TestCompletenessRetryHasOwnBudget:
    """The completeness gate's retry budget is a distinct DEFAULT_CAPS
    counter, not a prompt instruction (CLAUDE.md caps discipline) and not a
    borrowed budget."""

    def test_completeness_retry_rounds_cap_exists(self, leerie):
        assert "completeness_retry_rounds" in leerie.DEFAULT_CAPS
        assert isinstance(
            leerie.DEFAULT_CAPS["completeness_retry_rounds"], int)

    def test_default_is_one(self, leerie):
        assert leerie.DEFAULT_CAPS["completeness_retry_rounds"] == 1
