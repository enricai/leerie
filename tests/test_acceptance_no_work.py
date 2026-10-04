"""No work is declared on executed evidence, and disputed at most once
(DESIGN §8).

A confirmed no-work claim used to end the run on the judge's reading alone;
one such confirmation (generate-1.12.74) declared done a defect that
report-shaped tests still reproduce on barnacle's HEAD. On a defect-fix task
with held-out acceptance available the confirmation is now held pending, and
settled after the acceptance sets run on HEAD: pass → no work; fail → one
dispute (plan the work); fail again on the next run → accept with a warning,
because held-out tests can be wrong and a repeated dispute would loop forever.
"""
from __future__ import annotations

import asyncio
import inspect
import json
from pathlib import Path

import pytest


def _st(leerie, tmp_path, *, scoped=True, **data):
    repo = tmp_path / "repo"
    (repo / ".leerie").mkdir(parents=True)
    if scoped:
        (repo / ".leerie" / "config.toml").write_text(
            'test_scoped = "python3 -m pytest -q {test_files}"\n')
    root = tmp_path / "state"
    (root / "runs" / "run-now").mkdir(parents=True)
    st = leerie.State(root, "run-now", repo_root=repo)
    st.data = {"task": "fix the report", "categories": ["bug-fixing"],
               "likely_already_satisfied": True,
               "likely_already_satisfied_evidence": "commit abc fixed it",
               "worker_count": 0}
    st.data.update(data)
    st.save()
    return st


def _sibling(st, name, *, task=None, exit_code="0", dispute=None):
    d = st.run_dir.parent / name
    d.mkdir()
    (d / "state.json").write_text(json.dumps(
        {"task": task or st.data["task"],
         **({"acceptance_dispute": dispute} if dispute else {})}))
    if exit_code is not None:
        (d / "orchestrator.exit_code").write_text(exit_code)
    return d


@pytest.fixture
def finished(leerie, monkeypatch):
    calls = []
    monkeypatch.setattr(leerie, "_finish_no_work_run",
                        lambda st, m, **k: calls.append(m))
    return calls


def _judge_confirms(leerie, monkeypatch):
    async def fake_claude_p(**kw):
        assert kw["schema_key"] == "no_work_judge"
        return {"confirmed": True, "evidence": "verified on HEAD"}
    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)


def _confirm(leerie, st):
    return asyncio.run(leerie._confirm_no_work_on_converged_gate(
        st.data["task"], st, dict(leerie.DEFAULT_CAPS),
        {"no_work_judge": "sonnet"}, {"no_work_judge": "medium"}))


# --- the converged gate holds a confirmation pending ------------------------

def test_confirmation_is_held_pending_when_acceptance_can_check(
        leerie, tmp_path, monkeypatch, finished):
    st = _st(leerie, tmp_path)
    _judge_confirms(leerie, monkeypatch)
    assert _confirm(leerie, st) is False
    assert st.data["no_work_pending"] is True
    assert finished == []
    assert st.data["no_work_confirmation"]["judge_evidence"] == "verified on HEAD"


@pytest.mark.parametrize("override", [
    {"skip_acceptance_check": True},
    {"categories": ["feature-implementation"]},
    {"_no_scoped": True},
])
def test_confirmation_finishes_at_once_when_acceptance_cannot_check(
        leerie, tmp_path, monkeypatch, finished, override):
    scoped = not override.pop("_no_scoped", False)
    st = _st(leerie, tmp_path, scoped=scoped, **override)
    _judge_confirms(leerie, monkeypatch)
    assert _confirm(leerie, st) is True
    assert len(finished) == 1 and "no_work_pending" not in st.data


# --- settling on executed evidence -------------------------------------------

_SETS = [{"index": 1, "cases": {"t/test_a.py": ["case one"]}},
         {"index": 2, "cases": {"t/test_b.py": ["case two"]}},
         {"index": 3, "cases": {"t/test_c.py": ["case three"]}}]


def _results(passed):
    return [{"index": s["index"], "passed": p,
             "failing_files": [] if p else list(s["cases"])}
            for s, p in zip(_SETS, passed)]


def _settle(leerie, monkeypatch, st, results):
    async def fake(st_, caps):
        return results
    monkeypatch.setattr(leerie, "_acceptance_results_on_head", fake)
    st.data["acceptance"] = {"sets": _SETS}
    st.data["no_work_pending"] = True
    st.data["no_work_confirmation"] = {"classifier_evidence": "c",
                                       "judge_evidence": "j"}
    return asyncio.run(leerie._settle_pending_no_work(
        st, dict(leerie.DEFAULT_CAPS)))


def test_passing_sets_end_the_run_as_no_work(leerie, tmp_path, monkeypatch,
                                            finished):
    st = _st(leerie, tmp_path)
    assert _settle(leerie, monkeypatch, st, _results([True, True, False])) is True
    assert st.data["no_work_acceptance"]["verdict"] == "pass"
    assert len(finished) == 1


def test_failing_sets_dispute_once_and_plan(leerie, tmp_path, monkeypatch,
                                           finished):
    st = _st(leerie, tmp_path)
    assert _settle(leerie, monkeypatch, st, _results([False, False, True])) is False
    assert finished == []
    assert st.data["acceptance_dispute"]["failing_sets"] == 2
    ev = st.data["no_work_dispute"]["judge_evidence"]
    assert "case one" in ev and "case two" in ev and "case three" not in ev
    assert "no_work_pending" not in st.data


def test_a_second_failing_run_accepts_no_work(leerie, tmp_path, monkeypatch,
                                             finished, capsys):
    st = _st(leerie, tmp_path)
    _sibling(st, "run-prev", dispute={"failing_sets": 2, "cases": ["x"]})
    assert _settle(leerie, monkeypatch, st, _results([False, False, False])) is True
    assert st.data["no_work_acceptance"]["verdict"] == "accepted after dispute"
    assert "acceptance_dispute" not in st.data      # no second dispute record
    assert "WARNING" in capsys.readouterr().out
    assert len(finished) == 1


def test_no_valid_sets_keeps_the_judges_confirmation(leerie, tmp_path,
                                                    monkeypatch, finished):
    st = _st(leerie, tmp_path)
    assert _settle(leerie, monkeypatch, st, None) is True
    assert st.data["no_work_acceptance"]["verdict"] == "no valid sets"


def test_prior_dispute_reads_only_the_newest_completed_same_task_run(
        leerie, tmp_path):
    st = _st(leerie, tmp_path)
    assert leerie._prior_acceptance_dispute(st) is False
    _sibling(st, "run-other", task="another task", dispute={"cases": []})
    _sibling(st, "run-crashed", exit_code="1", dispute={"cases": []})
    assert leerie._prior_acceptance_dispute(st) is False
    _sibling(st, "run-ok", dispute={"cases": ["x"]})
    assert leerie._prior_acceptance_dispute(st) is True


# --- A4 and wiring -------------------------------------------------------------

def test_passes_on_head_helper(leerie, tmp_path, monkeypatch):
    st = _st(leerie, tmp_path)
    for res, want in ((None, False), (_results([True, True, False]), True),
                      (_results([False, False, True]), False)):
        async def fake(st_, caps, r=res):
            return r
        monkeypatch.setattr(leerie, "_acceptance_results_on_head", fake)
        assert asyncio.run(leerie._acceptance_passes_on_head(
            st, dict(leerie.DEFAULT_CAPS))) is want


def test_run_phases_wiring(leerie):
    src = inspect.getsource(leerie._run_phases)
    # Settled after the acceptance sets exist, before planning.
    i_write = src.index("await phase_acceptance_write(")
    i_settle = src.index("await _settle_pending_no_work(st, caps)")
    i_plan = src.index("plans = await phase_plan(")
    assert i_write < i_settle < i_plan
    # A4: the fix-subtask set is taken BEFORE the sweep and re-checked after,
    # gated on the held-out sets passing on HEAD.
    i_before = src.index("fix_ids_before = ")
    i_sweep = src.index("await _filter_satisfied_subtasks(")
    i_after = src.index("fix_ids_after = ")
    i_pass = src.index("await _acceptance_passes_on_head(st, caps)")
    assert i_before < i_sweep < i_after < i_pass
