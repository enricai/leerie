"""Review round 11 suspicion 3, proven real: `_run_final_conformance` read
`conformance._baseline` once, before its loop. A baseline whose install
failed is measured again inside the loop (`_retry_failed_baseline`, through
`_confirm_final_test_failures`), which replaces `_baseline` in state — but
the loop kept judging `_conformance_clean` against the old copy. When the
failed-install baseline counted `build` red because its dependencies were
missing, a red final build was forgiven for the rest of the loop: the first
round read clean, the loop stopped, and `--strict-conformer` did not block.
Real git and a real pytest suite; the build command really fails."""
from __future__ import annotations

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))
from test_redrive_test_id_diff_refresh import CMD  # noqa: E402
from test_shipping_tree_is_committed import _quiet_conformer, _repo  # noqa


@pytest.mark.parametrize("remeasured", [True, False])
def test_the_loop_judges_against_the_remeasured_baseline(
        leerie, tmp_path, monkeypatch, remeasured):
    run = _repo(leerie, tmp_path, monkeypatch)
    st = run["st"]
    build = f"{sys.executable} -c 'import sys; sys.exit(1)'"
    monkeypatch.setattr(leerie, "resolve_blt",
                        lambda root: {"test": CMD, "build": build})
    base = st.data["conformance"]["_baseline"]
    # The phase-4 baseline whose install failed: build counted red for a
    # missing dependency, tests unmeasured.
    base.update({"axes": {"build": {"ran": True, "measured": True,
                                    "passed": False, "command": build},
                          "tests": {"ran": True, "measured": True,
                                    "passed": False, "deps_failed": True,
                                    "command": CMD}},
                 "red_axes": ["build"]})
    st.save()
    retried: list[int] = []

    async def retry(st_, caps, *, log_path):
        if not remeasured:
            return False
        retried.append(1)
        b = st_.data["conformance"]["_baseline"]
        st_.data["conformance"]["_baseline"] = {
            **b, "axes": {"build": {"ran": True, "measured": True,
                                    "passed": True, "command": build}},
            "red_axes": []}
        st_.data["baseline_failing_ids"] = []
        return True
    monkeypatch.setattr(leerie, "_retry_failed_baseline", retry)
    _quiet_conformer(leerie, monkeypatch, clean=False)
    calls: list[str] = []

    async def conformer(*a, **k):
        calls.append(k.get("sid"))
        return {}
    monkeypatch.setattr(leerie, "claude_p", conformer)
    caps = {**run["caps"], "strict_conformer": True}

    def final():
        asyncio.run(leerie._run_final_conformance(
            run["run_dir"], st, caps, {"conformer": "sonnet"},
            {"conformer": "low"}))
    if remeasured:
        # The base builds once measured with its dependencies: the final
        # build's failure is this run's, every round is spent on it, and
        # the strict conformer blocks the run.
        with pytest.raises(SystemExit):
            final()
        fin = st.data["conformance"]["_final"]
        assert retried and len(calls) == caps["conformance_rounds"]
        assert fin["blocked"] is True
    else:
        # Control: the base still counts build red, so a red final build
        # is the base's and the first round is clean.
        final()
        fin = st.data["conformance"]["_final"]
        assert len(calls) == 1 and fin["blocked"] is False


def test_a_round_that_breaks_before_its_post_reads_the_remeasure_too(
        leerie, tmp_path, monkeypatch):
    """The same staleness after the loop: round 1's re-measure of the base
    fails again (build still counted red), round 2's conformer dies before
    its post-measurement, and the pre-measurement's confirm re-measures the
    base successfully. The strict verdict on round 1's red build must read
    the base that builds."""
    run = _repo(leerie, tmp_path, monkeypatch)
    st = run["st"]
    build = f"{sys.executable} -c 'import sys; sys.exit(1)'"
    monkeypatch.setattr(leerie, "resolve_blt",
                        lambda root: {"test": CMD, "build": build})
    st.data["conformance"]["_baseline"].update(
        {"axes": {"build": {"ran": True, "measured": True, "passed": False,
                            "command": build},
                  "tests": {"ran": True, "measured": True, "passed": False,
                            "deps_failed": True, "command": CMD}},
         "red_axes": ["build"]})
    st.save()
    tries: list[int] = []

    async def retry(st_, caps, *, log_path):
        tries.append(1)
        if len(tries) < 2:
            return False
        b = st_.data["conformance"]["_baseline"]
        st_.data["conformance"]["_baseline"] = {
            **b, "axes": {"build": {"ran": True, "measured": True,
                                    "passed": True, "command": build}},
            "red_axes": []}
        st_.data["baseline_failing_ids"] = []
        return True
    monkeypatch.setattr(leerie, "_retry_failed_baseline", retry)
    _quiet_conformer(leerie, monkeypatch, clean=False)
    calls: list[str] = []

    async def conformer(*a, **k):
        calls.append(k.get("sid"))
        if len(calls) == 1:
            # An open solution defect keeps the loop going whatever the
            # baseline says; the strict verdict reads only the baseline.
            return {"solution_defects": [{"kind": "k", "where": "mod.py:1",
                                          "concrete_case": "X = 0"}]}
        raise leerie.WorkerError("died before its post-measurement")
    monkeypatch.setattr(leerie, "claude_p", conformer)
    monkeypatch.setattr(leerie, "_conformance_clean",
                        lambda r, b: "build" in leerie._baseline_red_axes(b))
    caps = {**run["caps"], "strict_conformer": True}
    with pytest.raises(SystemExit):
        asyncio.run(leerie._run_final_conformance(
            run["run_dir"], st, caps, {"conformer": "sonnet"},
            {"conformer": "low"}))
    assert len(calls) == 2 and len(tries) == 2
    assert st.data["conformance"]["_final"]["blocked"] is True
