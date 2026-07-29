"""Unit tests for the conformer's gating solution-completeness axis
(DESIGN §9 *The one gating axis: solution completeness*):

- `actionable_solution_defects` — the anti-gaming filter (only defects with a
  concrete_case AND where gate).
- `_format_solution_defects` — the mandatory-criteria retry feedback block.
- `validate_conformance_result`'s solution_defects cross-field invariant.
"""
from __future__ import annotations

import tempfile


def _defect(kind="unhandled_input", case="empty list", where="foo.py:10",
            why="crashes"):
    return {"kind": kind, "concrete_case": case, "where": where,
            "why_ships_a_defect": why}


class TestSolutionDefectsSchemaMinLength:
    """F4: concrete_case/where/why_ships_a_defect carry minLength:1 so an empty
    string is rejected at the JSON layer (worker retries via claude_p) instead
    of tripping validate_conformance_result's cross-field break, which would
    break the whole conformance loop early. Mirrors dep_capture's discipline."""

    def test_defect_fields_have_min_length(self, leerie):
        item = (leerie.SCHEMAS["conformer"]["properties"]["solution_defects"]
                ["items"]["properties"])
        assert item["concrete_case"].get("minLength") == 1
        assert item["where"].get("minLength") == 1
        assert item["why_ships_a_defect"].get("minLength") == 1


class TestActionableSolutionDefects:
    def test_none_result_is_empty(self, leerie):
        assert leerie.actionable_solution_defects(None) == []

    def test_missing_field_is_empty(self, leerie):
        assert leerie.actionable_solution_defects({"subtask_id": "x"}) == []

    def test_actionable_defect_survives(self, leerie):
        res = {"solution_defects": [_defect()]}
        out = leerie.actionable_solution_defects(res)
        assert len(out) == 1

    def test_defect_missing_concrete_case_is_dropped(self, leerie):
        """The anti-gaming guard: a defect without a concrete case is
        non-actionable and must NOT gate."""
        res = {"solution_defects": [_defect(case="")]}
        assert leerie.actionable_solution_defects(res) == []

    def test_defect_missing_where_is_dropped(self, leerie):
        res = {"solution_defects": [_defect(where="  ")]}
        assert leerie.actionable_solution_defects(res) == []

    def test_mixed_keeps_only_actionable(self, leerie):
        res = {"solution_defects": [_defect(), _defect(case=""), _defect()]}
        assert len(leerie.actionable_solution_defects(res)) == 2

    def test_non_dict_defect_is_skipped(self, leerie):
        res = {"solution_defects": ["not a dict", _defect()]}
        assert len(leerie.actionable_solution_defects(res)) == 1


class TestFormatSolutionDefects:
    def test_renders_each_defect_as_mandatory_criteria(self, leerie):
        out = leerie._format_solution_defects([_defect(), _defect(
            kind="missing_guard", case="null token", where="auth.py:5")])
        assert "MANDATORY" in out
        assert "foo.py:10" in out
        assert "auth.py:5" in out
        assert "empty list" in out
        assert "null token" in out


class TestValidateConformanceSolutionDefects:
    def _base(self):
        """A minimal well-formed conformer result (everything but
        solution_defects)."""
        return {
            "subtask_id": "feat-001",
            "rules_files_read": [],
            "rule_violations_fixed": [],
            "rule_violations_residual": [],
            "docs_updates": [],
            "tests_updates": [],
            "build": {"ran": False, "passed": False, "command": "(none)",
                      "summary": "n/a"},
            "lint": {"ran": False, "passed": False, "command": "(none)",
                     "summary": "n/a"},
            "tests": {"ran": False, "passed": False, "command": "(none)",
                      "summary": "n/a"},
            "summary": "ok",
            "confidence": {"conformance": 9, "basis": "x",
                           "falsifiers_tested": [], "contradictions_reconciled": [],
                           "gap_to_close": {}},
            "solution_defects": [],
        }

    def test_valid_defect_passes(self, leerie):
        with tempfile.TemporaryDirectory() as wt:
            res = self._base()
            res["solution_defects"] = [_defect()]
            assert leerie.validate_conformance_result(res, wt) is None

    def test_empty_concrete_case_is_rejected(self, leerie):
        with tempfile.TemporaryDirectory() as wt:
            res = self._base()
            res["solution_defects"] = [_defect(case="")]
            err = leerie.validate_conformance_result(res, wt)
            assert err is not None and "concrete_case" in err

    def test_empty_where_is_rejected(self, leerie):
        with tempfile.TemporaryDirectory() as wt:
            res = self._base()
            res["solution_defects"] = [_defect(where="")]
            err = leerie.validate_conformance_result(res, wt)
            assert err is not None and "where" in err

    def test_empty_array_passes(self, leerie):
        with tempfile.TemporaryDirectory() as wt:
            assert leerie.validate_conformance_result(self._base(), wt) is None
