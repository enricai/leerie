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

    def test_low_task_understanding_still_gates(self, leerie, tmp_path):
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
        assert any("task_understanding" in i and "LOW_CONFIDENCE" in i
                   for i in issues)


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
    def test_classifier_low_confidence(self, leerie, tmp_path):
        result = {"categories": ["testing"], "questions": [],
                  "confidence": _conf(classification=8.0)}
        issues = leerie.check_classifier_output(result, tmp_path)
        assert any("LOW_CONFIDENCE" in i for i in issues)

    def test_planner_low_confidence(self, leerie, tmp_path):
        plan = {"subtasks": [], "status": "ready", "domain": "testing",
                "confidence": _conf(task_understanding=8.0,
                                    decomposition_quality=9.5)}
        issues = leerie.check_planner_output(plan, tmp_path, "testing")
        assert any("LOW_CONFIDENCE" in i and "task_understanding" in i
                    for i in issues)

    def test_reconciler_low_confidence(self, leerie):
        output = {"renames": [], "added_subtasks": [],
                  "confidence": _conf(reconciliation=5.0)}
        issues = leerie.check_reconciler_output(output, [{"subtasks": []}])
        assert any("LOW_CONFIDENCE" in i for i in issues)

    def test_overlap_judge_low_confidence(self, leerie, tmp_path):
        output = {"collisions": [],
                  "confidence": _conf(judgment=8.9)}
        issues = leerie.check_overlap_judge_output(
            output, [{"subtasks": []}], tmp_path)
        assert any("LOW_CONFIDENCE" in i for i in issues)

    def test_provision_low_confidence(self, leerie, tmp_path):
        result = {"recipe": [],
                  "confidence": _conf(recipe_correctness=0.0)}
        issues = leerie.check_provision_output(result, tmp_path)
        assert any("LOW_CONFIDENCE" in i for i in issues)

    def test_integrator_low_confidence(self, leerie):
        result = {"confidence": _conf(resolution=7.5)}
        issues = leerie.check_integrator_output(result)
        assert any("LOW_CONFIDENCE" in i for i in issues)

    def test_integrator_clean(self, leerie):
        result = {"confidence": _conf(resolution=9.0)}
        assert leerie.check_integrator_output(result) == []
