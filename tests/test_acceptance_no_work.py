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
    # The marker is carried forward (round-1 M4): otherwise the run after an
    # accepted one would dispute again, every other re-run.
    assert st.data["acceptance_dispute"]["accepted"] is True
    assert "WARNING" in capsys.readouterr().out
    assert len(finished) == 1


def test_a_third_run_still_does_not_dispute(leerie, tmp_path, monkeypatch,
                                           finished):
    """Round-1 M4, end to end over three runs' state: dispute, accept,
    accept — never dispute, accept, dispute."""
    st = _st(leerie, tmp_path)
    _sibling(st, "run-1", dispute={"failing_sets": 2, "cases": ["x"]})
    import os, time
    d2 = _sibling(st, "run-2", dispute={"failing_sets": 2, "cases": ["x"],
                                        "accepted": True})
    t = time.time() + 10
    os.utime(d2, (t, t))
    assert leerie._prior_acceptance_dispute(st) is True
    assert _settle(leerie, monkeypatch, st, _results([False, False, False])) is True


_FIVE = [{"index": k, "cases": {f"t/test_{k}.py": [f"case {k}"]}}
         for k in range(1, 6)]


@pytest.mark.parametrize("failing,named,evidence", [
    # Five sets: 1-3 shown, 4-5 held back. Shown 1, 2 and held-back 4, 5
    # fail: only the shown names leave the gate.
    ({1, 2, 4, 5}, ["case 1", "case 2"], "case 1; case 2"),
    # Shown 3 and held-back 4, 5 fail (3 of 5, a majority): one name.
    ({3, 4, 5}, ["case 3"], "case 3"),
])
def test_dispute_evidence_names_only_shown_sets(
        leerie, tmp_path, monkeypatch, finished, failing, named, evidence):
    st = _st(leerie, tmp_path)

    async def fake(st_, caps):
        return [{"index": k, "passed": k not in failing,
                 "failing_files": [f"t/test_{k}.py"] if k in failing else []}
                for k in range(1, 6)]
    monkeypatch.setattr(leerie, "_acceptance_results_on_head", fake)
    st.data.update(acceptance={"sets": _FIVE}, no_work_pending=True,
                   no_work_confirmation={"judge_evidence": "j"})
    assert asyncio.run(leerie._settle_pending_no_work(
        st, dict(leerie.DEFAULT_CAPS))) is False
    assert st.data["acceptance_dispute"]["cases"] == named
    assert st.data["no_work_dispute"]["judge_evidence"].endswith(evidence)


def test_dispute_with_only_hidden_failures_gives_counts_alone(
        leerie, tmp_path, monkeypatch, finished):
    """Four sets, 3 and 4 held back. Those two fail and 1, 2 pass — a tie,
    which counts as failing — so every failing case is held back and the
    evidence carries the counts alone."""
    st = _st(leerie, tmp_path)
    four = _FIVE[:4]

    async def fake(st_, caps):
        return [{"index": k, "passed": k <= 2,
                 "failing_files": [] if k <= 2 else [f"t/test_{k}.py"]}
                for k in range(1, 5)]
    monkeypatch.setattr(leerie, "_acceptance_results_on_head", fake)
    st.data.update(acceptance={"sets": four}, no_work_pending=True,
                   no_work_confirmation={"judge_evidence": "j"})
    assert asyncio.run(leerie._settle_pending_no_work(
        st, dict(leerie.DEFAULT_CAPS))) is False
    ev = st.data["no_work_dispute"]["judge_evidence"]
    assert "case 3" not in ev and "case 4" not in ev
    assert "2 of 4 sets" in ev and "held back" in ev
    assert st.data["acceptance_dispute"]["cases"] == []


def test_unmeasurable_sets_keep_the_judges_confirmation(leerie, tmp_path,
                                                       monkeypatch, finished):
    st = _st(leerie, tmp_path)
    res = [dict(r, unmeasured=True) for r in _results([False, False, False])]
    assert _settle(leerie, monkeypatch, st, res) is True
    assert st.data["no_work_acceptance"]["verdict"] == "not measurable"


def test_no_valid_sets_keeps_the_judges_confirmation(leerie, tmp_path,
                                                    monkeypatch, finished):
    st = _st(leerie, tmp_path)
    assert _settle(leerie, monkeypatch, st, None) is True
    assert st.data["no_work_acceptance"]["verdict"] == "no valid sets"


def test_dispute_counts_only_measured_sets(leerie, tmp_path, monkeypatch,
                                          finished):
    """An unrunnable set is no evidence: it is neither a failing set in the
    recorded count nor a source of failing case names."""
    st = _st(leerie, tmp_path)
    res = _results([False, False, True])
    res[2] = {"index": 3, "passed": False, "unmeasured": True,
              "failing_files": []}
    res.append({"index": 4, "passed": False, "unmeasured": True,
                "failing_files": []})
    assert _settle(leerie, monkeypatch, st, res) is False
    assert st.data["acceptance_dispute"]["failing_sets"] == 2
    assert st.data["acceptance_dispute"]["total_sets"] == 2


def test_settle_errors_fail_open_to_the_judges_confirmation(
        leerie, tmp_path, monkeypatch, finished):
    st = _st(leerie, tmp_path, no_work_pending=True,
             no_work_confirmation={"judge_evidence": "verified on HEAD"})

    async def boom(st_, caps):
        raise OSError("planning worktree vanished")
    monkeypatch.setattr(leerie, "_acceptance_results_on_head", boom)
    assert asyncio.run(leerie._settle_pending_no_work_failing_open(
        st, dict(leerie.DEFAULT_CAPS))) is True
    assert st.data["no_work_acceptance"] == {"verdict": "error: OSError"}
    assert finished == [{"<confirmed already-satisfied>": "verified on HEAD"}]


def test_prior_dispute_reads_only_the_newest_completed_same_task_run(
        leerie, tmp_path):
    st = _st(leerie, tmp_path)
    assert leerie._prior_acceptance_dispute(st) is False
    _sibling(st, "run-other", task="another task", dispute={"cases": []})
    _sibling(st, "run-crashed", exit_code="1", dispute={"cases": []})
    assert leerie._prior_acceptance_dispute(st) is False
    _sibling(st, "run-ok", dispute={"cases": ["x"]})
    assert leerie._prior_acceptance_dispute(st) is True


def test_an_unacted_dispute_does_not_count(leerie, tmp_path):
    """DESIGN §8 *A dispute counts only once it is acted on*: a disputing
    run that still ended as no work leaves the next run free to dispute."""
    st = _st(leerie, tmp_path)
    _sibling(st, "run-prev", dispute={"failing_sets": 2, "cases": ["x"],
                                      "unacted": True})
    assert leerie._prior_acceptance_dispute(st) is False


def test_a_wrong_dispute_that_goes_unacted_is_bounded(leerie, tmp_path,
                                                      monkeypatch, finished):
    """Round-1 review of #283: wrong held-out sets, planners correctly find
    nothing. Run 1's dispute goes unacted; run 2 re-disputes (marked
    `redispute`); when that goes unacted too, run 3 accepts — two extra
    runs, never one per re-run forever."""
    import os, time
    st = _st(leerie, tmp_path)
    _sibling(st, "run-1", dispute={"failing_sets": 2, "cases": ["x"],
                                   "unacted": True})
    assert leerie._prior_acceptance_dispute(st) is False
    # Run 2 (this state) disputes again, recorded as the re-dispute.
    assert _settle(leerie, monkeypatch, st, _results([False, False, True])) is False
    assert st.data["acceptance_dispute"].get("redispute") is True
    leerie._mark_dispute_unacted(st)
    d2 = _sibling(st, "run-2", dispute=st.data["acceptance_dispute"])
    t = time.time() + 10
    os.utime(d2, (t, t))
    # Run 3 accepts.
    assert leerie._prior_acceptance_dispute(st) is True


def test_a_first_dispute_is_not_a_redispute(leerie, tmp_path, monkeypatch,
                                            finished):
    st = _st(leerie, tmp_path)
    assert _settle(leerie, monkeypatch, st, _results([False, False, True])) is False
    assert "redispute" not in st.data["acceptance_dispute"]


@pytest.mark.parametrize("dispute,marked", [
    ({"failing_sets": 2, "cases": ["x"]}, True),
    # A carried-forward acceptance is not this run's dispute to mark.
    ({"failing_sets": 2, "cases": ["x"], "accepted": True}, False),
    (None, False),
])
def test_mark_dispute_unacted(leerie, tmp_path, dispute, marked):
    st = _st(leerie, tmp_path,
             **({"acceptance_dispute": dispute} if dispute else {}))
    leerie._mark_dispute_unacted(st)
    got = (st.data.get("acceptance_dispute") or {}).get("unacted", False)
    assert got is marked
    # Persisted, since the next run reads it from state.json.
    on_disk = json.loads(st.path.read_text()).get("acceptance_dispute") or {}
    assert on_disk.get("unacted", False) is marked


@pytest.mark.parametrize("res,fix_ids,want", [
    (_results([False, False, True]), {"s0"}, {"s0"}),   # majority fails
    (_results([True, True, False]), {"s0"}, set()),     # majority passes
    (None, {"s0"}, set()),                              # no valid sets
    ([dict(r, unmeasured=True) for r in _results([False] * 3)],
     {"s0"}, set()),                                    # nothing measured
    (_results([False, False, False]), set(), set()),    # no fix subtasks
    (_results([True, False]), {"s0"}, {"s0"}),          # a tie protects
])
def test_pre_sweep_protect(leerie, tmp_path, monkeypatch, res, fix_ids, want):
    st = _st(leerie, tmp_path)
    ran = []

    async def fake(st_, caps):
        ran.append(1)
        return res
    monkeypatch.setattr(leerie, "_acceptance_results_on_head", fake)
    assert asyncio.run(leerie._fix_ids_held_out_sets_protect(
        st, dict(leerie.DEFAULT_CAPS), fix_ids)) == want
    # No fix subtasks → the sets are not even run.
    assert bool(ran) is bool(fix_ids)


def test_pre_sweep_protect_skipped_with_the_sweep(leerie, tmp_path,
                                                  monkeypatch):
    """No sweep runs under `skip_satisfied_check`, so the sets are not run
    on HEAD just to protect from it."""
    st = _st(leerie, tmp_path, skip_satisfied_check=True)
    ran = []

    async def fake(st_, caps):
        ran.append(1)
        return _results([False, False, False])
    monkeypatch.setattr(leerie, "_acceptance_results_on_head", fake)
    assert asyncio.run(leerie._fix_ids_held_out_sets_protect(
        st, dict(leerie.DEFAULT_CAPS), {"s0"})) == set()
    assert ran == []


@pytest.mark.parametrize("head,runs", [("base-sha", False),
                                       ("later-sha", True)])
def test_pre_sweep_protect_on_the_validity_base_skips_the_run(
        leerie, tmp_path, monkeypatch, head, runs):
    """On the commit the sets were validated on, every set already failed:
    protect without running them again. Elsewhere, measure."""
    st = _st(leerie, tmp_path, acceptance={"sets": _SETS,
                                           "validity_base": "base-sha"},
             planning_worktree=str(tmp_path))
    ran = []

    async def fake(st_, caps):
        ran.append(1)
        return _results([True, True, True])

    async def no_wt(st_):
        return None

    async def sha(path):
        return head
    monkeypatch.setattr(leerie, "_acceptance_results_on_head", fake)
    monkeypatch.setattr(leerie, "_ensure_planning_worktree", no_wt)
    monkeypatch.setattr(leerie, "_branch_head_sha", sha)
    got = asyncio.run(leerie._fix_ids_held_out_sets_protect(
        st, dict(leerie.DEFAULT_CAPS), {"s0"}))
    assert bool(ran) is runs
    assert got == (set() if runs else {"s0"})


def test_pre_sweep_protect_errors_protect_nothing(leerie, tmp_path,
                                                  monkeypatch):
    st = _st(leerie, tmp_path)

    async def boom(st_, caps):
        raise OSError("planning worktree vanished")
    monkeypatch.setattr(leerie, "_acceptance_results_on_head", boom)
    assert asyncio.run(leerie._fix_ids_held_out_sets_protect(
        st, dict(leerie.DEFAULT_CAPS), {"s0"})) == set()


def test_dispute_then_probe_drops_everything_keeps_the_fix(
        leerie, tmp_path, monkeypatch, finished):
    """The post-merge #282 scenario end to end over the real helpers: run 1
    disputes; its sweep would call every subtask satisfied. The fix
    subtask is protected, so the plan keeps it and no no-work exit fires."""
    st = _st(leerie, tmp_path)
    assert _settle(leerie, monkeypatch, st, _results([False, False, True])) is False
    plans = [{"domain": "bug-fixing", "status": "ready", "subtasks": [
        {"id": "fix-1", "fixes_reported_symptom": True,
         "success_criteria_seed": "the symptom is gone"}]}]
    fix_ids = {"fix-1"}
    protect = asyncio.run(leerie._fix_ids_held_out_sets_protect(
        st, dict(leerie.DEFAULT_CAPS), fix_ids))

    async def satisfied(**kw):
        return {"satisfied": True, "evidence": "looks done"}
    monkeypatch.setattr(leerie, "claude_p", satisfied)
    monkeypatch.setattr(leerie, "_branch_head_sha",
                        lambda *_a, **_k: asyncio.sleep(0, "sha"))
    st.data["planning_worktree"] = str(tmp_path)
    got = asyncio.run(leerie._filter_satisfied_subtasks(
        plans, tmp_path, st, dict(leerie.DEFAULT_CAPS), {}, {},
        protect=protect))
    assert got is None
    assert [s["id"] for s in plans[0]["subtasks"]] == ["fix-1"]
    assert asyncio.run(leerie._finish_if_every_fix_already_on_head(
        st, dict(leerie.DEFAULT_CAPS), fix_ids, plans)) is False
    assert finished == []


# --- A4 and wiring -------------------------------------------------------------

def test_passes_on_head_helper(leerie, tmp_path, monkeypatch):
    st = _st(leerie, tmp_path)
    unmeasured = [dict(r, unmeasured=True) for r in _results([False] * 3)]
    for res, want in ((None, False), (_results([True, True, False]), True),
                      (_results([False, False, True]), False),
                      # Nothing measured is no evidence, never "already
                      # fixed" (round-2 review).
                      (unmeasured, False)):
        async def fake(st_, caps, r=res):
            return r
        monkeypatch.setattr(leerie, "_acceptance_results_on_head", fake)
        assert asyncio.run(leerie._acceptance_passes_on_head(
            st, dict(leerie.DEFAULT_CAPS))) is want


def _plans(*flags):
    return [{"domain": "bug-fixing", "subtasks": [
        {"id": f"s{i}", "fixes_reported_symptom": f}
        for i, f in enumerate(flags)]}]


@pytest.mark.parametrize("before,after,passes,ends", [
    ({"s0"}, _plans(False), True, True),     # every fix dropped, sets pass
    ({"s0"}, _plans(False), False, False),   # sets fail → keep the plan
    ({"s0"}, _plans(True), True, False),     # a fix subtask survived
    (set(), _plans(False), True, False),     # the plan had no fix subtasks
])
def test_every_fix_already_on_head_routing(leerie, tmp_path, monkeypatch,
                                           finished, before, after, passes,
                                           ends):
    """Behavioural A4 (round-1 review: structure alone is not substance)."""
    st = _st(leerie, tmp_path)

    async def fake(st_, caps):
        return passes
    monkeypatch.setattr(leerie, "_acceptance_passes_on_head", fake)
    got = asyncio.run(leerie._finish_if_every_fix_already_on_head(
        st, dict(leerie.DEFAULT_CAPS), before, after))
    assert got is ends and len(finished) == (1 if ends else 0)


def test_already_fixed_check_errors_proceed_with_the_plan(
        leerie, tmp_path, monkeypatch, finished):
    st = _st(leerie, tmp_path)

    async def boom(st_, caps):
        raise OSError("set dir pruned")
    monkeypatch.setattr(leerie, "_acceptance_passes_on_head", boom)
    assert asyncio.run(leerie._finish_if_every_fix_already_on_head(
        st, dict(leerie.DEFAULT_CAPS), {"s0"}, _plans(False))) is False
    assert finished == []


def test_run_phases_wiring(leerie):
    src = inspect.getsource(leerie._run_phases)
    # Settled after the acceptance sets exist, before planning.
    i_write = src.index("await _acceptance_write_or_skip(")
    i_settle = src.index("await _settle_pending_no_work_failing_open(st, caps)")
    i_plan = src.index("plans = await phase_plan(")
    assert i_write < i_settle < i_plan
    # A4: the fix-subtask set is taken BEFORE the sweep and re-checked after,
    # gated on the held-out sets passing on HEAD.
    i_before = src.index("fix_ids_before = ")
    i_sweep = src.index("await _filter_satisfied_subtasks(")
    i_a4 = src.index("await _finish_if_every_fix_already_on_head(")
    assert i_before < i_sweep < i_a4
    # The protect set is computed after the fix ids and fed to the sweep.
    i_protect = src.index("protect = await _fix_ids_held_out_sets_protect(")
    assert i_before < i_protect < i_sweep
    assert "protect=protect)" in src[i_sweep:i_a4]


def test_both_other_no_work_exits_mark_the_dispute_unacted(leerie):
    """Each `_finish_no_work_run` reached from the empty-plans and the
    sweep exits is immediately preceded by `_mark_dispute_unacted`."""
    lines = [l.strip() for l in
             inspect.getsource(leerie._run_phases).splitlines()]
    for exit_ in ("_finish_no_work_run(st, no_work_map)",
                  "_finish_no_work_run(st, satisfied_no_work)"):
        i = lines.index(exit_)
        assert lines[i - 1] == "_mark_dispute_unacted(st)", exit_
