"""Tests for the mechanical-check functions used by the CRITIC-pattern
feedback loop (DESIGN §8 + §12).  Each check function is pure Python
(no LLM, no I/O except the repo_root path) and returns a list[str] of
issue descriptions — empty when clean.
"""
from __future__ import annotations

from pathlib import Path

import pytest


def _conf(**axes: float) -> dict:
    """Build a valid confidence dict that clears the 9.0 gate."""
    return {**axes, "basis": "test", "falsifiers_tested": [],
            "contradictions_reconciled": [], "gap_to_close": {}}


# --- check_classifier_output -------------------------------------------- #

class TestCheckClassifierOutput:
    def test_clean_output(self, leerie, tmp_path):
        (tmp_path / "infra").mkdir()
        result = {"categories": ["infrastructure"], "questions": [],
                  "confidence": _conf(classification=9.5)}
        assert leerie.check_classifier_output(result, tmp_path) == []

    def test_infra_no_dir(self, leerie, tmp_path):
        result = {"categories": ["infrastructure"], "questions": []}
        issues = leerie.check_classifier_output(result, tmp_path)
        assert any("CATEGORY_NO_DIR" in i for i in issues)

    def test_docs_no_dir(self, leerie, tmp_path):
        result = {"categories": ["documentation"], "questions": []}
        issues = leerie.check_classifier_output(result, tmp_path)
        assert any("CATEGORY_NO_DIR" in i for i in issues)

    def test_docs_with_dir(self, leerie, tmp_path):
        (tmp_path / "docs").mkdir()
        result = {"categories": ["documentation"], "questions": [],
                  "confidence": _conf(classification=9.0)}
        assert leerie.check_classifier_output(result, tmp_path) == []

    def test_empty_why_underivable(self, leerie, tmp_path):
        result = {"categories": ["testing"],
                  "questions": [{"id": "q1", "question": "?",
                                 "why_underivable": ""}]}
        issues = leerie.check_classifier_output(result, tmp_path)
        assert any("EMPTY_WHY" in i for i in issues)

    def test_prescribed_procedure_empty_evidence(self, leerie, tmp_path):
        result = {"categories": ["testing"], "questions": [],
                  "prescribed_procedure": {
                      "is_prescribed": True,
                      "commands": ["recon browser", "recon generate"],
                      "forbid_manual": True,
                      "evidence": ""}}
        issues = leerie.check_classifier_output(result, tmp_path)
        assert any("EMPTY_EVIDENCE" in i for i in issues)

    def test_prescribed_procedure_whitespace_only_evidence(self, leerie, tmp_path):
        result = {"categories": ["testing"], "questions": [],
                  "prescribed_procedure": {
                      "is_prescribed": True,
                      "commands": ["recon generate"],
                      "evidence": "   "}}
        issues = leerie.check_classifier_output(result, tmp_path)
        assert any("EMPTY_EVIDENCE" in i for i in issues)

    def test_prescribed_procedure_with_evidence_ok(self, leerie, tmp_path):
        result = {"categories": ["testing"], "questions": [],
                  "confidence": _conf(classification=9.5),
                  "prescribed_procedure": {
                      "is_prescribed": True,
                      "commands": ["recon browser", "recon generate"],
                      "forbid_manual": True,
                      "evidence": "your ONLY job is to run recon browser "
                                  "then recon generate"}}
        issues = leerie.check_classifier_output(result, tmp_path)
        assert not any("EMPTY_EVIDENCE" in i for i in issues)

    def test_prescribed_procedure_not_prescribed_no_evidence_needed(
            self, leerie, tmp_path):
        result = {"categories": ["testing"], "questions": [],
                  "confidence": _conf(classification=9.5),
                  "prescribed_procedure": {
                      "is_prescribed": False, "commands": [],
                      "evidence": ""}}
        issues = leerie.check_classifier_output(result, tmp_path)
        assert not any("EMPTY_EVIDENCE" in i for i in issues)

    def test_prescribed_procedure_absent_no_evidence_needed(self, leerie, tmp_path):
        result = {"categories": ["testing"], "questions": []}
        issues = leerie.check_classifier_output(result, tmp_path)
        assert not any("EMPTY_EVIDENCE" in i for i in issues)

    def test_many_categories(self, leerie, tmp_path):
        result = {"categories": ["a", "b", "c", "d", "e"],
                  "questions": []}
        issues = leerie.check_classifier_output(result, tmp_path)
        assert any("MANY_CATEGORIES" in i for i in issues)

    def test_four_categories_ok(self, leerie, tmp_path):
        result = {"categories": ["a", "b", "c", "d"],
                  "questions": []}
        issues = leerie.check_classifier_output(result, tmp_path)
        assert not any("MANY_CATEGORIES" in i for i in issues)

    def test_same_work_risk_bug_and_feature(self, leerie, tmp_path):
        result = {"categories": ["bug-fixing", "feature-implementation"],
                  "questions": []}
        issues = leerie.check_classifier_output(result, tmp_path)
        assert any("SAME_WORK_RISK" in i for i in issues)

    def test_same_work_risk_bug_and_refactoring(self, leerie, tmp_path):
        result = {"categories": ["bug-fixing", "refactoring"],
                  "questions": []}
        issues = leerie.check_classifier_output(result, tmp_path)
        assert any("SAME_WORK_RISK" in i for i in issues)

    def test_same_work_risk_feature_and_refactoring(self, leerie, tmp_path):
        result = {"categories": ["feature-implementation", "refactoring"],
                  "questions": []}
        issues = leerie.check_classifier_output(result, tmp_path)
        assert any("SAME_WORK_RISK" in i for i in issues)

    def test_no_same_work_risk_bug_and_testing(self, leerie, tmp_path):
        result = {"categories": ["bug-fixing", "testing"],
                  "questions": []}
        issues = leerie.check_classifier_output(result, tmp_path)
        assert not any("SAME_WORK_RISK" in i for i in issues)

    def test_no_same_work_risk_single_category(self, leerie, tmp_path):
        result = {"categories": ["bug-fixing"],
                  "questions": []}
        issues = leerie.check_classifier_output(result, tmp_path)
        assert not any("SAME_WORK_RISK" in i for i in issues)

    # TEST_OWNERSHIP_RISK: a code category + testing can collide on test-file
    # ownership (the barnacle fake-timer incident). Distinct label from
    # SAME_WORK_RISK (those are same-intent overlaps); bug+testing correctly
    # produces NO SAME_WORK_RISK (test above) but DOES produce this advisory.
    def test_test_ownership_risk_bug_and_testing(self, leerie, tmp_path):
        result = {"categories": ["bug-fixing", "testing"], "questions": []}
        issues = leerie.check_classifier_output(result, tmp_path)
        assert any("TEST_OWNERSHIP_RISK" in i for i in issues)
        # And it is NOT mislabeled as a same-intent overlap.
        assert not any("SAME_WORK_RISK" in i for i in issues)

    def test_test_ownership_risk_feature_and_testing(self, leerie, tmp_path):
        result = {"categories": ["feature-implementation", "testing"],
                  "questions": []}
        issues = leerie.check_classifier_output(result, tmp_path)
        assert any("TEST_OWNERSHIP_RISK" in i for i in issues)

    def test_test_ownership_risk_refactoring_and_testing(self, leerie, tmp_path):
        result = {"categories": ["refactoring", "testing"], "questions": []}
        issues = leerie.check_classifier_output(result, tmp_path)
        assert any("TEST_OWNERSHIP_RISK" in i for i in issues)

    def test_no_test_ownership_risk_without_testing(self, leerie, tmp_path):
        result = {"categories": ["bug-fixing", "feature-implementation"],
                  "questions": []}
        issues = leerie.check_classifier_output(result, tmp_path)
        assert not any("TEST_OWNERSHIP_RISK" in i for i in issues)

    def test_no_test_ownership_risk_testing_alone(self, leerie, tmp_path):
        result = {"categories": ["testing"], "questions": []}
        issues = leerie.check_classifier_output(result, tmp_path)
        assert not any("TEST_OWNERSHIP_RISK" in i for i in issues)

    def test_no_test_ownership_risk_testing_and_docs(self, leerie, tmp_path):
        # documentation is not a code category — no test-file ownership clash.
        result = {"categories": ["documentation", "testing"], "questions": []}
        issues = leerie.check_classifier_output(result, tmp_path)
        assert not any("TEST_OWNERSHIP_RISK" in i for i in issues)

    # --- judge_confirmed suppression (regression: barnacle classification-
    # gate exhaustion, 2026-07-31 — the independent classification_judge
    # would confirm a category the classifier's own SAME_WORK_RISK/
    # TEST_OWNERSHIP_RISK self-check kept stripping on the next re-classify
    # round, oscillating forever) ---------------------------------------- #

    def test_judge_confirmed_suppresses_same_work_risk(self, leerie, tmp_path):
        result = {"categories": ["bug-fixing", "feature-implementation"],
                  "questions": []}
        issues = leerie.check_classifier_output(
            result, tmp_path,
            judge_confirmed=frozenset({"bug-fixing", "feature-implementation"}))
        assert not any("SAME_WORK_RISK" in i for i in issues)

    def test_judge_confirmed_suppresses_test_ownership_risk(self, leerie, tmp_path):
        result = {"categories": ["bug-fixing", "testing"], "questions": []}
        issues = leerie.check_classifier_output(
            result, tmp_path,
            judge_confirmed=frozenset({"bug-fixing", "testing"}))
        assert not any("TEST_OWNERSHIP_RISK" in i for i in issues)

    def test_judge_confirmed_partial_pair_does_not_suppress(self, leerie, tmp_path):
        # Only ONE member of the pair is judge-confirmed — the advisory
        # must still fire, since the pair as a whole was never vetted.
        result = {"categories": ["bug-fixing", "feature-implementation"],
                  "questions": []}
        issues = leerie.check_classifier_output(
            result, tmp_path, judge_confirmed=frozenset({"bug-fixing"}))
        assert any("SAME_WORK_RISK" in i for i in issues)

    def test_judge_confirmed_does_not_suppress_unconfirmed_pair(self, leerie, tmp_path):
        # judge_confirmed only covers bug-fixing/feature-implementation;
        # an unrelated SAME_WORK_RISK pair (bug-fixing/refactoring) present
        # in the same categories list must still fire.
        result = {"categories": ["bug-fixing", "feature-implementation",
                                  "refactoring"], "questions": []}
        issues = leerie.check_classifier_output(
            result, tmp_path,
            judge_confirmed=frozenset({"bug-fixing", "feature-implementation"}))
        assert not any(
            "SAME_WORK_RISK: 'bug-fixing' and 'feature-implementation'" in i
            for i in issues)
        assert any(
            "SAME_WORK_RISK: 'bug-fixing' and 'refactoring'" in i
            for i in issues)

    def test_judge_confirmed_default_is_empty_no_behavior_change(self, leerie, tmp_path):
        # Regression pin: omitting judge_confirmed entirely (every caller
        # except phase_classification_gate's re-classify loop) must behave
        # exactly as before this parameter was added.
        result = {"categories": ["bug-fixing", "feature-implementation"],
                  "questions": []}
        issues = leerie.check_classifier_output(result, tmp_path)
        assert any("SAME_WORK_RISK" in i for i in issues)


# --- check_planner_output ---------------------------------------------- #

class TestCheckPlannerOutput:
    def _plan(self, subtasks, conf=True):
        d = {"subtasks": subtasks, "status": "ready",
             "domain": "testing"}
        if conf:
            d["confidence"] = _conf(task_understanding=9.5,
                                    decomposition_quality=9.5)
        return d

    def test_clean_plan(self, leerie, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "foo.ts").touch()
        plan = self._plan([{
            "id": "test-001", "title": "t",
            "success_criteria_seed": "check",
            "files_likely_touched": ["src/foo.ts"],
            "depends_on": [], "size": "small",
        }])
        assert leerie.check_planner_output(plan, tmp_path, "testing") == []

    def test_phantom_path(self, leerie, tmp_path):
        plan = self._plan([{
            "id": "test-001", "title": "t",
            "success_criteria_seed": "check",
            "files_likely_touched": ["nonexistent/deep/file.ts"],
            "depends_on": [], "size": "small",
        }])
        issues = leerie.check_planner_output(plan, tmp_path, "testing")
        assert any("PHANTOM_PATH" in i for i in issues)

    def test_parent_exists_ok(self, leerie, tmp_path):
        (tmp_path / "src").mkdir()
        plan = self._plan([{
            "id": "test-001", "title": "t",
            "success_criteria_seed": "check",
            "files_likely_touched": ["src/new-file.ts"],
            "depends_on": [], "size": "small",
        }])
        assert leerie.check_planner_output(plan, tmp_path, "testing") == []

    def test_dangling_dep(self, leerie, tmp_path):
        plan = self._plan([{
            "id": "test-001", "title": "t",
            "success_criteria_seed": "check",
            "depends_on": ["test-999"], "size": "small",
        }])
        issues = leerie.check_planner_output(plan, tmp_path, "testing")
        assert any("DANGLING_DEP" in i for i in issues)

    def test_cross_domain_dep_not_flagged(self, leerie, tmp_path):
        plan = self._plan([{
            "id": "test-001", "title": "t",
            "success_criteria_seed": "check",
            "depends_on": ["feat-001"], "size": "small",
        }])
        issues = leerie.check_planner_output(plan, tmp_path, "testing")
        assert not any("DANGLING_DEP" in i for i in issues)

    def test_empty_criteria(self, leerie, tmp_path):
        plan = self._plan([{
            "id": "test-001", "title": "t",
            "success_criteria_seed": "",
            "depends_on": [], "size": "small",
        }])
        issues = leerie.check_planner_output(plan, tmp_path, "testing")
        assert any("EMPTY_CRITERIA" in i for i in issues)

    def test_oversized(self, leerie, tmp_path):
        plan = self._plan([{
            "id": "test-001", "title": "t",
            "success_criteria_seed": "check",
            "depends_on": [], "size": "large",
        }])
        issues = leerie.check_planner_output(plan, tmp_path, "testing")
        assert any("OVERSIZED" in i for i in issues)

    def test_intra_domain_overlap(self, leerie, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "foo.ts").touch()
        plan = self._plan([
            {"id": "test-001", "title": "a",
             "success_criteria_seed": "x",
             "files_likely_touched": ["src/foo.ts"],
             "depends_on": [], "size": "small"},
            {"id": "test-002", "title": "b",
             "success_criteria_seed": "y",
             "files_likely_touched": ["src/foo.ts"],
             "depends_on": [], "size": "small"},
        ])
        issues = leerie.check_planner_output(plan, tmp_path, "testing")
        assert any("INTRA_DOMAIN_OVERLAP" in i for i in issues)

    def test_intra_domain_overlap_suppressed_for_cofile_cluster(self, leerie, tmp_path):
        # Deliberate sub-file split (DESIGN §5½ (P1) *Sub-file*): region children
        # of one file share a _cofile_cluster marker — their same-file overlap is
        # intentional and must NOT trip the advisory.
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "big.ts").touch()
        plan = self._plan([
            {"id": "test-001-r1", "title": "region 1",
             "success_criteria_seed": "x",
             "files_likely_touched": ["src/big.ts"],
             "_cofile_cluster": "test-001",
             "depends_on": [], "size": "small"},
            {"id": "test-001-r2", "title": "region 2",
             "success_criteria_seed": "y",
             "files_likely_touched": ["src/big.ts"],
             "_cofile_cluster": "test-001",
             "depends_on": [], "size": "small"},
        ])
        issues = leerie.check_planner_output(plan, tmp_path, "testing")
        assert not any("INTRA_DOMAIN_OVERLAP" in i for i in issues)

    def test_intra_domain_overlap_still_warns_on_mixed_cluster(self, leerie, tmp_path):
        # A cluster child plus an UNRELATED subtask on the same file is an
        # accidental overlap, not a coherent cluster — the advisory still fires.
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "big.ts").touch()
        plan = self._plan([
            {"id": "test-001-r1", "title": "region 1",
             "success_criteria_seed": "x",
             "files_likely_touched": ["src/big.ts"],
             "_cofile_cluster": "test-001",
             "depends_on": [], "size": "small"},
            {"id": "test-009", "title": "unrelated",
             "success_criteria_seed": "z",
             "files_likely_touched": ["src/big.ts"],
             "depends_on": [], "size": "small"},
        ])
        issues = leerie.check_planner_output(plan, tmp_path, "testing")
        assert any("INTRA_DOMAIN_OVERLAP" in i for i in issues)

    def test_protected_path(self, leerie, tmp_path):
        plan = self._plan([{
            "id": "test-001", "title": "t",
            "success_criteria_seed": "check",
            "files_likely_touched": [".leerie/state.json"],
            "depends_on": [], "size": "small",
        }])
        issues = leerie.check_planner_output(plan, tmp_path, "testing")
        assert any("PROTECTED_PATH" in i for i in issues)

    def test_intra_domain_cycle(self, leerie, tmp_path):
        plan = self._plan([
            {"id": "test-001", "title": "a",
             "success_criteria_seed": "x",
             "depends_on": ["test-002"], "size": "small"},
            {"id": "test-002", "title": "b",
             "success_criteria_seed": "y",
             "depends_on": ["test-001"], "size": "small"},
        ])
        issues = leerie.check_planner_output(plan, tmp_path, "testing")
        assert any("INTRA_DOMAIN_CYCLE" in i for i in issues)

    def test_no_cycle_when_linear(self, leerie, tmp_path):
        plan = self._plan([
            {"id": "test-001", "title": "a",
             "success_criteria_seed": "x",
             "depends_on": [], "size": "small"},
            {"id": "test-002", "title": "b",
             "success_criteria_seed": "y",
             "depends_on": ["test-001"], "size": "small"},
        ])
        issues = leerie.check_planner_output(plan, tmp_path, "testing")
        assert not any("INTRA_DOMAIN_CYCLE" in i for i in issues)

    # G3 — decomposition_quality is demoted to an advisory self-report: it is
    # retained in the schema but NO LONGER gates. The independent fit_judge is
    # the authoritative decomposition gate (DESIGN §5½).
    def test_low_decomposition_quality_does_not_gate(self, leerie, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "foo.ts").touch()
        plan = {"subtasks": [{
            "id": "test-001", "title": "t",
            "success_criteria_seed": "check",
            "files_likely_touched": ["src/foo.ts"],
            "depends_on": [], "size": "small"}],
            "status": "ready", "domain": "testing",
            # decomposition_quality below the 9.0 gate, task_understanding above
            "confidence": _conf(task_understanding=9.5,
                                decomposition_quality=2.0)}
        issues = leerie.check_planner_output(plan, tmp_path, "testing")
        assert not any("decomposition_quality" in i for i in issues)

    # feat-003 — task_understanding is likewise demoted to an advisory
    # self-report: the independent task_coverage_judge
    # (phase_planning_coverage_gate) is now the authoritative coverage gate
    # (DESIGN §8), removing the planner's self-grading bias.
    def test_low_task_understanding_does_not_gate(self, leerie, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "foo.ts").touch()
        plan = {"subtasks": [{
            "id": "test-001", "title": "t",
            "success_criteria_seed": "check",
            "files_likely_touched": ["src/foo.ts"],
            "depends_on": [], "size": "small"}],
            "status": "ready", "domain": "testing",
            "confidence": _conf(task_understanding=2.0,
                                decomposition_quality=9.5)}
        issues = leerie.check_planner_output(plan, tmp_path, "testing")
        assert not any("task_understanding" in i for i in issues)


# --- instruction-adherence gate: advisory-vs-gating split --------------- #
#
# Mirrors the G3 decomposition_quality-does-not-gate / task_understanding-
# still-gates pair above, but for the instruction-adherence gate (DESIGN:
# instruction-adherence is code-enforced, sibling to §12). The deterministic
# floor (check_prescribed_command_coverage) is a SEPARATE function from
# check_planner_output — it is wired into phase_adherence_gate, not into the
# planner check loop — so these tests assert the OUTCOME (a prescribed,
# uncovered command gates; a goal-only task never gates) rather than
# asserting against a specific confidence axis name, per the investigation
# note that the exact wiring choice must not be baked into the test.
class TestAdherenceGateAdvisoryVsGating:
    def test_prescribed_cmd_unrun_gates(self, leerie):
        """A prescribed command with no covering subtask is a gating
        issue — the deterministic floor is the PRIMARY, always-enforced
        layer of the instruction-adherence gate."""
        prescribed = {
            "is_prescribed": True,
            "commands": ["recon browser", "recon generate"],
            "forbid_manual": True,
            "evidence": "your ONLY job is to run recon browser then "
                        "recon generate",
        }
        subtasks = [{"id": "feat-001", "runs_commands": ["write contract.ts"]}]
        issues = leerie.check_prescribed_command_coverage(prescribed, subtasks)
        assert any("PRESCRIBED_CMD_UNRUN" in i for i in issues)

    def test_goal_only_task_does_not_gate(self, leerie):
        """A goal-only task (no prescribed procedure) never gates — the
        floor is silent by construction, guarding the ~90% ordinary-task
        case against false positives."""
        prescribed = {"is_prescribed": False, "commands": []}
        subtasks = [{"id": "feat-001", "runs_commands": []}]
        issues = leerie.check_prescribed_command_coverage(prescribed, subtasks)
        assert not any("PRESCRIBED_CMD_UNRUN" in i for i in issues)

    def test_covered_prescribed_command_does_not_gate(self, leerie):
        """A prescribed command that some subtask's runs_commands does
        cover must not gate — the floor only fires on a genuine gap."""
        prescribed = {
            "is_prescribed": True,
            "commands": ["recon generate"],
            "forbid_manual": True,
            "evidence": "explicit procedure",
        }
        subtasks = [{"id": "feat-001", "runs_commands": ["run recon generate"]}]
        issues = leerie.check_prescribed_command_coverage(prescribed, subtasks)
        assert not any("PRESCRIBED_CMD_UNRUN" in i for i in issues)

    def test_check_planner_output_carries_no_separate_adherence_axis(
            self, leerie, tmp_path):
        """check_planner_output itself has no self-reported adherence axis
        to demote to advisory (unlike decomposition_quality) — the gate
        lives entirely in the independent, always-enforced floor above.
        A clean plan with no prescribed_procedure at all must not surface
        any PRESCRIBED_CMD_UNRUN-shaped issue from check_planner_output."""
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "foo.ts").touch()
        plan = {"subtasks": [{
            "id": "test-001", "title": "t",
            "success_criteria_seed": "check",
            "files_likely_touched": ["src/foo.ts"],
            "depends_on": [], "size": "small"}],
            "status": "ready", "domain": "testing",
            "confidence": _conf(task_understanding=9.5,
                                decomposition_quality=9.5)}
        issues = leerie.check_planner_output(plan, tmp_path, "testing")
        assert not any("PRESCRIBED_CMD_UNRUN" in i for i in issues)


# --- check_reconciler_output -------------------------------------------- #

class TestCheckReconcilerOutput:
    def _plans_with_provides(self, tags):
        return [{"subtasks": [{"id": "feat-001", "provides": tags}]}]

    def test_clean(self, leerie):
        output = {"renames": [], "added_subtasks": [],
                  "confidence": _conf(reconciliation=9.0)}
        plans = self._plans_with_provides(["tag-a"])
        assert leerie.check_reconciler_output(output, plans) == []

    def test_rename_to_nowhere(self, leerie):
        output = {"renames": [{"sid": "x", "from": "a", "to": "ghost"}],
                  "added_subtasks": []}
        plans = self._plans_with_provides(["tag-a"])
        issues = leerie.check_reconciler_output(output, plans)
        assert any("RENAME_TO_NOWHERE" in i for i in issues)

    def test_rename_to_existing_clean(self, leerie):
        output = {"renames": [{"sid": "x", "from": "a", "to": "tag-a"}],
                  "added_subtasks": [],
                  "confidence": _conf(reconciliation=9.0)}
        plans = self._plans_with_provides(["tag-a"])
        assert leerie.check_reconciler_output(output, plans) == []

    def test_bad_prefix(self, leerie):
        output = {"renames": [],
                  "added_subtasks": [{"id": "zz-001", "depends_on": []}]}
        issues = leerie.check_reconciler_output(output, [{"subtasks": []}])
        assert any("BAD_PREFIX" in i for i in issues)

    def test_self_dep(self, leerie):
        output = {"renames": [],
                  "added_subtasks": [{"id": "feat-001",
                                      "depends_on": ["feat-001"]}]}
        issues = leerie.check_reconciler_output(output, [{"subtasks": []}])
        assert any("SELF_DEP" in i for i in issues)


# --- check_provision_output --------------------------------------------- #

class TestCheckProvisionOutput:
    def test_clean(self, leerie, tmp_path):
        (tmp_path / "pnpm-lock.yaml").touch()
        result = {"recipe": [{"kind": "install",
                               "command": ["pnpm", "install"],
                               "working_dir": "."}],
                  "confidence": _conf(recipe_correctness=9.0)}
        assert leerie.check_provision_output(result, tmp_path) == []

    def test_wrong_pm(self, leerie, tmp_path):
        (tmp_path / "pnpm-lock.yaml").touch()
        result = {"recipe": [{"kind": "install",
                               "command": ["npm", "install"],
                               "working_dir": "."}]}
        issues = leerie.check_provision_output(result, tmp_path)
        assert any("WRONG_PM" in i for i in issues)

    def test_missing_workdir(self, leerie, tmp_path):
        result = {"recipe": [{"kind": "install",
                               "command": ["pip", "install"],
                               "working_dir": "nonexistent"}]}
        issues = leerie.check_provision_output(result, tmp_path)
        assert any("MISSING_WORKDIR" in i for i in issues)

    def test_empty_recipe_with_lockfile(self, leerie, tmp_path):
        (tmp_path / "package-lock.json").touch()
        result = {"recipe": []}
        issues = leerie.check_provision_output(result, tmp_path)
        assert any("EMPTY_RECIPE" in i for i in issues)

    def test_empty_recipe_no_lockfile(self, leerie, tmp_path):
        result = {"recipe": [],
                  "confidence": _conf(recipe_correctness=9.5)}
        assert leerie.check_provision_output(result, tmp_path) == []


# --- check_overlap_judge_output ----------------------------------------- #

class TestCheckOverlapJudgeOutput:
    def _plans(self):
        return [{"subtasks": [
            {"id": "feat-001", "provides": ["tag-a"],
             "files_likely_touched": ["src/a.ts"]},
            {"id": "refactor-001", "provides": [],
             "files_likely_touched": ["src/b.ts"]},
        ]}]

    def test_clean(self, leerie, tmp_path):
        output = {"collisions": [],
                  "confidence": _conf(judgment=9.0)}
        assert leerie.check_overlap_judge_output(
            output, self._plans(), tmp_path) == []

    def test_no_file_overlap(self, leerie, tmp_path):
        output = {"collisions": [{
            "a_sid": "feat-001", "b_sid": "refactor-001",
            "artifact": "some thing", "resolution": "merge",
            "reason": "overlap"}]}
        issues = leerie.check_overlap_judge_output(
            output, self._plans(), tmp_path)
        assert any("NO_FILE_OVERLAP" in i for i in issues)

    def test_drop_breaks_graph(self, leerie, tmp_path):
        plans = [{"subtasks": [
            {"id": "feat-001", "provides": ["needed-tag"],
             "files_likely_touched": ["src/a.ts"],
             "requires": []},
            {"id": "feat-002", "provides": [],
             "files_likely_touched": ["src/a.ts"],
             "requires": [{"tag": "needed-tag", "extent": "in_plan"}]},
        ]}]
        output = {"collisions": [{
            "a_sid": "feat-001", "b_sid": "feat-002",
            "artifact": "src/a.ts", "resolution": "drop_a",
            "reason": "superseded"}]}
        issues = leerie.check_overlap_judge_output(
            output, plans, tmp_path)
        assert any("DROP_BREAKS_GRAPH" in i for i in issues)

    def test_spurious_cofile_collision_flagged(self, leerie, tmp_path):
        """A P1 sub-file split's region siblings share a _cofile_cluster —
        an `unresolvable` verdict between them is always wrong (DESIGN
        §5½ *Sub-file*) and must be flagged regardless of the judge's
        free-text reasoning."""
        plans = [{"subtasks": [
            {"id": "feat-002-r1", "files_likely_touched": ["src/big.ts"],
             "_cofile_cluster": "feat-002"},
            {"id": "feat-002-r2", "files_likely_touched": ["src/big.ts"],
             "_cofile_cluster": "feat-002"},
        ]}]
        output = {"collisions": [{
            "a_sid": "feat-002-r1", "b_sid": "feat-002-r2",
            "artifact": "src/big.ts", "resolution": "unresolvable",
            "reason": "identical intents"}]}
        issues = leerie.check_overlap_judge_output(
            output, plans, tmp_path)
        assert any("SPURIOUS_COFILE_COLLISION" in i for i in issues)

    def test_spurious_cofile_collision_not_flagged_for_different_clusters(
            self, leerie, tmp_path):
        """A genuine collision between subtasks in DIFFERENT (or absent)
        _cofile_cluster groups must still be reported — the check must
        not over-suppress an accidental same-file overlap."""
        plans = [{"subtasks": [
            {"id": "feat-002-r1", "files_likely_touched": ["src/big.ts"],
             "_cofile_cluster": "feat-002"},
            {"id": "refactor-009", "files_likely_touched": ["src/big.ts"]},
        ]}]
        output = {"collisions": [{
            "a_sid": "feat-002-r1", "b_sid": "refactor-009",
            "artifact": "src/big.ts", "resolution": "unresolvable",
            "reason": "genuine conflict"}]}
        issues = leerie.check_overlap_judge_output(
            output, plans, tmp_path)
        assert not any("SPURIOUS_COFILE_COLLISION" in i for i in issues)

    def test_spurious_cofile_collision_ignores_non_unresolvable(
            self, leerie, tmp_path):
        """The check only fires on `unresolvable` — a `merge`/`drop_a`/
        `drop_b` resolution between cluster siblings is not the failure
        mode this guards against (an unresolvable die() is)."""
        plans = [{"subtasks": [
            {"id": "feat-002-r1", "files_likely_touched": ["src/big.ts"],
             "_cofile_cluster": "feat-002"},
            {"id": "feat-002-r2", "files_likely_touched": ["src/big.ts"],
             "_cofile_cluster": "feat-002"},
        ]}]
        output = {"collisions": [{
            "a_sid": "feat-002-r1", "b_sid": "feat-002-r2",
            "artifact": "src/big.ts", "resolution": "merge",
            "reason": "unrelated"}]}
        issues = leerie.check_overlap_judge_output(
            output, plans, tmp_path)
        assert not any("SPURIOUS_COFILE_COLLISION" in i for i in issues)


# --- check_implementer_output ------------------------------------------ #

class TestCheckImplementerOutput:
    def test_clean(self, leerie):
        result = {"status": "complete", "criteria_results": [
            {"criterion": "test passes", "met": True}]}
        subtask = {"files_likely_touched": ["src/foo.ts"]}
        assert leerie.check_implementer_output(
            result, subtask, {"src/foo.ts"}) == []

    def test_no_planned_files_touched(self, leerie):
        result = {"status": "complete"}
        subtask = {"files_likely_touched": ["src/foo.ts"]}
        issues = leerie.check_implementer_output(
            result, subtask, {"src/bar.ts"})
        assert any("NO_PLANNED_FILES_TOUCHED" in i for i in issues)

    def test_unmet_criterion(self, leerie):
        result = {"status": "complete", "criteria_results": [
            {"criterion": "test passes", "met": False}]}
        subtask = {}
        issues = leerie.check_implementer_output(
            result, subtask, set())
        assert any("UNMET_CRITERION" in i for i in issues)

    def test_no_criteria_is_ok(self, leerie):
        result = {"status": "complete"}
        assert leerie.check_implementer_output(
            result, {}, {"src/foo.ts"}) == []


# --- _confidence_issues ------------------------------------------------- #

class TestConfidenceIssues:
    def test_all_clear(self, leerie):
        conf = {"root_cause": 9.5, "solution": 9.0}
        assert leerie._confidence_issues(conf, ["root_cause", "solution"]) == []

    def test_one_below(self, leerie):
        conf = {"root_cause": 8.9, "solution": 9.0}
        issues = leerie._confidence_issues(conf, ["root_cause", "solution"])
        assert len(issues) == 1
        assert "root_cause" in issues[0]
        assert "LOW_CONFIDENCE" in issues[0]

    def test_all_axes_missing(self, leerie):
        assert leerie._confidence_issues({}, ["classification"]) == []

    def test_one_axis_present_one_missing(self, leerie):
        conf = {"root_cause": 9.5}
        issues = leerie._confidence_issues(
            conf, ["root_cause", "solution"])
        assert len(issues) == 1
        assert "solution" in issues[0]

    def test_exactly_threshold(self, leerie):
        conf = {"classification": 9.0}
        assert leerie._confidence_issues(conf, ["classification"]) == []

    def test_custom_threshold(self, leerie):
        conf = {"x": 7.0}
        assert leerie._confidence_issues(conf, ["x"], threshold=7.0) == []
        issues = leerie._confidence_issues(conf, ["x"], threshold=7.1)
        assert len(issues) == 1


# --- LOW_CONFIDENCE in check functions ---------------------------------- #

class TestLowConfidenceGating:
    def test_classifier_self_score_does_not_gate(self, leerie, tmp_path):
        """DESIGN §8: the classifier's `classification` self-score is NO
        LONGER a gating axis — the independent classification_judge is the
        authoritative gate. A low self-score must NOT produce LOW_CONFIDENCE."""
        result = {"categories": ["testing"], "questions": [],
                  "confidence": _conf(classification=2.0)}
        issues = leerie.check_classifier_output(result, tmp_path)
        assert not any("LOW_CONFIDENCE" in i for i in issues)

    def test_planner_self_score_does_not_gate(self, leerie, tmp_path):
        """DESIGN §8: the planner's `task_understanding` self-score is NO
        LONGER a gating axis — the independent task_coverage_judge
        (phase_planning_coverage_gate) is the authoritative gate. A low
        self-score must NOT produce LOW_CONFIDENCE."""
        plan = {"subtasks": [], "status": "ready", "domain": "testing",
                "confidence": _conf(task_understanding=2.0,
                                    decomposition_quality=9.5)}
        issues = leerie.check_planner_output(plan, tmp_path, "testing")
        assert not any("LOW_CONFIDENCE" in i for i in issues)

    def test_reconciler_self_score_does_not_gate(self, leerie):
        """DESIGN §8: the reconciler's `reconciliation` self-score is NO
        LONGER a gating axis — the deterministic check_plan_wiring + the
        independent wiring_judge are authoritative. Low self-score must NOT
        produce LOW_CONFIDENCE."""
        output = {"renames": [], "added_subtasks": [],
                  "confidence": _conf(reconciliation=2.0)}
        issues = leerie.check_reconciler_output(output, [{"subtasks": []}])
        assert not any("LOW_CONFIDENCE" in i for i in issues)

    def test_overlap_judge_self_score_does_not_gate(self, leerie, tmp_path):
        """DESIGN §8: the overlap judge's `judgment` self-score is NO LONGER
        a gating axis — this worker is already the independent adversarial
        check, so its own deterministic validators (PHANTOM_ARTIFACT,
        NO_FILE_OVERLAP, DROP_BREAKS_GRAPH) are authoritative. A low
        self-score with otherwise-clean output must NOT produce
        LOW_CONFIDENCE."""
        output = {"collisions": [],
                  "confidence": _conf(judgment=1.0)}
        issues = leerie.check_overlap_judge_output(
            output, [{"subtasks": []}], tmp_path)
        assert not any("LOW_CONFIDENCE" in i for i in issues)

    def test_provision_self_score_does_not_gate(self, leerie, tmp_path):
        """DESIGN §8: the provision worker's `recipe_correctness` self-score
        is NO LONGER a gating axis — the independent provision_judge is
        authoritative. A recipe with content but a low self-score must NOT
        produce LOW_CONFIDENCE (empty recipe with lockfiles is a separate
        mechanical EMPTY_RECIPE check; tmp_path has no lockfiles)."""
        result = {"recipe": [{"kind": "install", "command": ["true"],
                              "working_dir": "."}],
                  "confidence": _conf(recipe_correctness=2.0)}
        issues = leerie.check_provision_output(result, tmp_path)
        assert not any("LOW_CONFIDENCE" in i for i in issues)

    # feat-006 — integrator's resolution self-score is demoted to an advisory
    # self-report: the independent integration_judge (in integrate_wave) is now
    # the authoritative behavioral-integration gate (DESIGN §8), removing the
    # integrator's self-grading bias.
    def test_integrator_low_resolution_does_not_gate(self, leerie):
        result = {"confidence": _conf(resolution=7.5)}
        issues = leerie.check_integrator_output(result)
        assert not any("resolution" in i.lower() for i in issues)
        assert not any("LOW_CONFIDENCE" in i for i in issues)

    def test_integrator_clean(self, leerie):
        result = {"confidence": _conf(resolution=9.0)}
        assert leerie.check_integrator_output(result) == []
