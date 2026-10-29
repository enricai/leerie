"""Review round 8 L2: one fact, one "Needs human verification" line, and
every regression-check line leads with what the reviewer must do (DESIGN
§8 *The final diff describes the tree that ships*).

Before: a task forbidding the test command, with no test coupled to the
change, listed the scoped-replacement gap and the final-diff gap for the
same fact; and the final-diff line, which embedded a measurement summary of
up to 200 characters before its instruction, was cut by `_PR_ENTRY_MAX` at
"…Run the tests on the PR branch and compa"."""
from __future__ import annotations

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))
from test_redrive_test_id_diff_refresh import CMD, redriven  # noqa: E402,F401

GROUP = "Regression checks the run could not settle"


class _St:
    def __init__(self) -> None:
        self.data: dict = {}

    def save(self) -> None:
        pass


def _lines(leerie, data: dict) -> list[str]:
    return leerie._pr_needs_human(data, lambda _s: True).get(GROUP, [])


def _forbid(leerie, st, monkeypatch) -> None:
    st.data["forbidden_commands"] = [{"command": "python3 -m pytest",
                                      "scope": "whole_suite",
                                      "evidence_quote": "do not run pytest"}]
    monkeypatch.setattr(leerie, "_forbidden_specs", lambda d: [
        {"command": "python3 -m pytest", "scope": "whole_suite"}])
    monkeypatch.setattr(
        leerie, "_forbidden_command_hit",
        lambda c, f: "python3 -m pytest" if "pytest" in c else None)
    monkeypatch.setattr(leerie, "_coupled_test_files", lambda *a, **k: {})


def test_a_forbidden_suite_is_listed_once(leerie, redriven, monkeypatch):
    """The reviewer's driver: the regression-check entry stays, the
    final-diff entry for the same fact is not added beside it."""
    st = redriven["st"]
    st.data.pop("test_id_diff", None)
    _forbid(leerie, st, monkeypatch)
    asyncio.run(leerie._refresh_shipping_test_id_diff(
        st.run_dir, st, redriven["caps"], label="test-id-diff-final"))
    srcs = [e.get("source") for e in st.data["needs_human_verification"]]
    assert leerie.REGRESSION_GAP_SOURCE in srcs
    assert leerie.FINAL_DIFF_GAP_SOURCE not in srcs
    lines = _lines(leerie, st.data)
    assert sum("python3 -m pytest" in ln for ln in lines) == 1, lines
    assert lines[0].startswith("- [ ] Check for regressions by hand:")


def test_an_unmeasured_suite_the_task_does_not_forbid_is_still_listed(
        leerie, redriven, monkeypatch):
    """The other side of the de-duplication: with no regression-check entry
    the final-diff gap is the only line, and it is listed."""
    st = redriven["st"]
    summary = "E" * 400

    async def unmeasured(tree, axes, st_, caps_, **kw):
        return {"tests": {"ran": True, "measured": False, "passed": None,
                          "command": CMD, "summary": summary}}
    monkeypatch.setattr(leerie, "_measure_axes", unmeasured)
    asyncio.run(leerie._refresh_shipping_test_id_diff(
        st.run_dir, st, redriven["caps"], label="test-id-diff-final"))
    (line,) = [ln for ln in _lines(leerie, st.data)
               if "failing-test-ID diff" in ln]
    # The 200-character summary no longer cuts the instruction.
    assert "run the tests on the PR branch and compare with the base" in line
    assert "EEEE" in line


@pytest.mark.parametrize("n_files", [1, 10, 25])
def test_a_long_budget_list_never_cuts_its_instruction(leerie, n_files):
    st = _St()
    files = [f"tests/very/deeply/nested/package/test_module_{i:03d}.py"
             for i in range(n_files)]
    leerie._set_confirm_budget_gap(st, "failing-test-ID diff", files,
                                   sized_out=files)
    (line,) = _lines(leerie, st.data)
    assert "rerun these against the base by hand" in line
    assert "whether the run made them fail is unknown" in line
    assert files[0] in line


def test_a_kept_groups_unchecked_items_come_before_its_files(leerie):
    st = _St()
    files = [f"tests/very/deeply/nested/package/test_module_{i:03d}.py"
             for i in range(10)]
    leerie._set_redrive_unsettled_gap(
        st, [{"group": "r0g0", "ids": files, "sized_out": [],
              "files": {f: f for f in files}, "unchecked_items": [0]}],
        [{"item": "no existing test breaks"}])
    (line,) = _lines(leerie, st.data)
    assert "check by hand the required item(s) its ratchet could not " \
           "check: no existing test breaks" in line


def test_a_long_test_outcome_reason_never_cuts_the_instruction(leerie):
    st = _St()
    leerie._set_test_outcome_gap(st, ["no existing test breaks"],
                                 "R" * 600)
    (line,) = _lines(leerie, st.data)
    assert line.startswith("- [ ] Check by hand that no existing test newly "
                           "fails: RRR")


def test_a_long_regression_gap_why_never_cuts_its_instruction(
        leerie, redriven, monkeypatch):
    st = redriven["st"]
    _forbid(leerie, st, monkeypatch)
    rec = asyncio.run(leerie._scoped_test_replacement(
        str(redriven["staging"]), st, CMD, "python3 -m pytest",
        [{"command": "python3 -m pytest", "scope": "whole_suite"}],
        log_prefix="t"))
    assert rec["skip_kind"].startswith("forbidden_")
    (line,) = _lines(leerie, st.data)
    assert line.startswith("- [ ] Check for regressions by hand: the task "
                           "forbids")
