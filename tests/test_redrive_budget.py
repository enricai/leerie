"""The shared re-drive budget (DESIGN §13 *One re-drive budget per
subtask*): one persisted counter per subtask that every re-drive kind draws
from, a run-level wall-time hard stop, `unconverged` as the terminal state
of an exhausted subtask, dependents settled without running, and a resume
filter that treats `unconverged` / `accepted_external` as settled."""
from __future__ import annotations

import asyncio
import inspect
import json
from unittest import mock

import pytest


def _mock_st(waves, **extra):
    st = mock.Mock()
    st.data = {"waves": waves, "completed_waves": 0, "subtask_status": {},
               "integration_gate": {}, "skip_base_baseline": True, **extra}
    st.save = mock.Mock()
    st.repo_root = "/tmp/repo"
    st.path = "/tmp/state.json"
    st.run_id = "run-1"
    return st


@pytest.fixture
def patched(leerie, monkeypatch):
    monkeypatch.setattr(leerie, "_run_script", mock.AsyncMock(
        return_value=mock.Mock(returncode=0, stderr="")))
    monkeypatch.setattr(leerie, "_prune_leerie_worktrees", mock.Mock())
    monkeypatch.setattr(leerie, "_disk_free_ratio", lambda p: 0.9)
    monkeypatch.setattr(leerie, "_degrade_max_parallel_for_wave",
                        lambda m, d: m)
    monkeypatch.setattr(leerie, "_scan_conflict_markers",
                        mock.AsyncMock(return_value=None))
    monkeypatch.setattr(leerie, "_prune_subtask_worktree", mock.AsyncMock())

    async def integrate(wave, results, *a, **k):
        return [s for s in wave if results.get(s, {}).get("status")
                == "complete"]
    monkeypatch.setattr(leerie, "integrate_wave", integrate)
    return leerie


def _settle_script(leerie, monkeypatch, script):
    """`script[sid]` is a list of statuses returned by successive settles;
    the last repeats. Records (sid, redrive_note) per call."""
    calls = []

    async def fake(sid, leerie_dir, caps, st, models, efforts,
                   redrive_note=None):
        calls.append((sid, redrive_note))
        seq = script[sid]
        n = sum(1 for c in calls if c[0] == sid) - 1
        status = seq[min(n, len(seq) - 1)]
        st.data["subtask_status"][sid] = status
        return {"subtask_id": sid, "status": status,
                "blocker": f"{sid} blocker" if status != "complete" else None}
    monkeypatch.setattr(leerie, "_settle_subtask", fake)
    return calls


# --- the counter -----------------------------------------------------------

def test_counter_and_budget_predicate(leerie):
    st = _mock_st([])
    caps = dict(leerie.DEFAULT_CAPS)
    assert caps["subtask_redrive_rounds"] == 2
    assert caps["redrive_budget_sec"] == 14400
    assert leerie._redrive_allowed(st, caps, "a")
    assert leerie._consume_redrive(st, "a") == 1
    assert leerie._redrive_allowed(st, caps, "a")
    assert leerie._consume_redrive(st, "a") == 2
    assert not leerie._redrive_allowed(st, caps, "a")
    assert st.data["redrive_rounds"] == {"a": 2}
    assert leerie._redrive_allowed(st, caps, "b")
    st.data["redrive_spent_sec"] = 14400
    assert not leerie._redrive_allowed(st, caps, "b")


def test_completeness_redrive_draws_from_the_shared_counter(leerie):
    """Both budgets gate the completeness re-drive, and taking it consumes
    the shared round (the two must not multiply)."""
    src = inspect.getsource(leerie._settle_subtask)
    block = src[src.index("_actionable_solution_defects(conf_res)"):]
    block = block[:block.index("continue")]
    assert '"completeness_retry_rounds"' in block
    assert "_redrive_allowed(st, caps, sid)" in block
    assert "_consume_redrive(st, sid)" in block


# --- the wave loop ---------------------------------------------------------

def test_blocked_then_redriven_to_complete_integrates(patched, monkeypatch,
                                                      tmp_path):
    leerie = patched
    st = _mock_st([["a"]])
    calls = _settle_script(leerie, monkeypatch, {"a": ["blocked", "complete"]})
    asyncio.run(leerie.phase_execute(tmp_path, st, dict(leerie.DEFAULT_CAPS),
                                     {}, {}))
    assert [c[0] for c in calls] == ["a", "a"]
    assert calls[0][1] is None
    assert "a blocker" in calls[1][1] and "RE-DRIVE 1" in calls[1][1]
    assert st.data["subtask_status"]["a"] == "complete"
    assert st.data["redrive_rounds"] == {"a": 1}
    assert "unconverged_subtasks" not in st.data
    assert st.data["completed_waves"] == 1


def test_exhausted_subtask_is_unconverged_after_exactly_the_cap(
        patched, monkeypatch, tmp_path):
    leerie = patched
    st = _mock_st([["a", "b"]])
    calls = _settle_script(leerie, monkeypatch,
                           {"a": ["blocked"], "b": ["complete"]})
    asyncio.run(leerie.phase_execute(tmp_path, st, dict(leerie.DEFAULT_CAPS),
                                     {}, {}))
    assert [c[0] for c in calls].count("a") == 3      # 1 + 2 re-drives
    assert st.data["subtask_status"] == {"a": "unconverged",
                                         "b": "complete"}
    assert st.data["redrive_rounds"] == {"a": 2}
    assert st.data["completed_waves"] == 1


def test_failed_subtask_is_not_redriven(patched, monkeypatch, tmp_path):
    leerie = patched
    st = _mock_st([["a"]])
    calls = _settle_script(leerie, monkeypatch, {"a": ["failed"]})
    asyncio.run(leerie.phase_execute(tmp_path, st, dict(leerie.DEFAULT_CAPS),
                                     {}, {}))
    assert calls == [("a", None)]
    assert st.data["subtask_status"]["a"] == "unconverged"
    assert st.data["unconverged_subtasks"]["a"]["previous_status"] == "failed"


def test_spent_run_budget_stops_every_redrive(patched, monkeypatch, tmp_path):
    leerie = patched
    st = _mock_st([["a"]], redrive_spent_sec=14400.0)
    calls = _settle_script(leerie, monkeypatch, {"a": ["blocked", "complete"]})
    asyncio.run(leerie.phase_execute(tmp_path, st, dict(leerie.DEFAULT_CAPS),
                                     {}, {}))
    assert calls == [("a", None)]
    assert st.data["subtask_status"]["a"] == "unconverged"


def test_counter_consumed_earlier_is_not_handed_back(patched, monkeypatch,
                                                     tmp_path):
    """A counter persisted by an earlier settle (the completeness re-drive,
    or a prior invocation) leaves no wave-end re-drive."""
    leerie = patched
    st = _mock_st([["a"]], redrive_rounds={"a": 2})
    calls = _settle_script(leerie, monkeypatch, {"a": ["blocked", "complete"]})
    asyncio.run(leerie.phase_execute(tmp_path, st, dict(leerie.DEFAULT_CAPS),
                                     {}, {}))
    assert calls == [("a", None)]
    assert st.data["subtask_status"]["a"] == "unconverged"


def test_redrive_round_wall_time_is_charged(leerie, tmp_path):
    st = _mock_st([["a"]], redrive_spent_sec=10.0)

    async def settle(sid, note):
        await asyncio.sleep(0.3)
        return {"status": "complete"}
    asyncio.run(leerie._redrive_blocked_subtasks(
        {"a": {"status": "blocked", "blocker": "x"}}, settle, st,
        dict(leerie.DEFAULT_CAPS), tmp_path))
    assert 10.3 <= st.data["redrive_spent_sec"] < 15.0


def test_in_settle_redrive_clock_is_charged_once_per_wave(leerie):
    st = _mock_st([])
    assert leerie._close_redrive_clock(st, "a") == 0.0
    leerie._open_redrive_clock(st, "a")
    leerie._open_redrive_clock(st, "a")        # the first open wins
    assert leerie._close_redrive_clock(st, "a") >= 0.0
    assert leerie._close_redrive_clock(st, "a") == 0.0


def test_dependents_of_unconverged_settle_without_running(
        patched, monkeypatch, tmp_path):
    leerie = patched
    (tmp_path / "plan.json").write_text(json.dumps({"subtasks": {
        "a": {"depends_on": []},
        "b": {"depends_on": ["a"]},
        "c": {"depends_on": ["b"]},
        "d": {"depends_on": []}}}))
    st = _mock_st([["a", "d"], ["b"], ["c"]])
    calls = _settle_script(leerie, monkeypatch,
                           {"a": ["blocked"], "d": ["complete"],
                            "b": ["complete"], "c": ["complete"]})
    asyncio.run(leerie.phase_execute(tmp_path, st, dict(leerie.DEFAULT_CAPS),
                                     {}, {}))
    ran = {c[0] for c in calls}
    assert ran == {"a", "d"}
    assert st.data["subtask_status"] == {"a": "unconverged", "d": "complete",
                                         "b": "unconverged",
                                         "c": "unconverged"}
    assert "a" in st.data["unconverged_subtasks"]["b"]["reason"]
    assert st.data["completed_waves"] == 3


def test_dependents_of_accepted_external_settle_external_without_running(
        patched, monkeypatch, tmp_path):
    """DESIGN §13 *A dependent of accepted-external work is
    accepted-external too*: by the graph alone — `depends_on`, a
    `requires` tag, transitively — and citing the predecessor's resource."""
    leerie = patched
    (tmp_path / "plan.json").write_text(json.dumps({"subtasks": {
        "a": {"depends_on": [], "provides": ["creds"]},
        "g": {"depends_on": []},
        "d": {"depends_on": []},
        "b": {"depends_on": ["a"]},
        "c": {"requires": [{"tag": "creds", "extent": "in_plan"}]},
        "e": {"depends_on": ["b"]},
        "f": {"depends_on": ["a", "g"]},
        "h": {"depends_on": ["d"]}}}))
    st = _mock_st([["a", "g", "d"], ["b", "c", "f", "h"], ["e"]])
    ran: list[str] = []

    async def fake(sid, leerie_dir, caps, st_, models, efforts,
                   redrive_note=None):
        ran.append(sid)
        if sid == "a":
            return leerie._settle_accepted_external(
                st_, "a", "a production API key", "needs the key")
        status = "blocked" if sid == "g" else "complete"
        st_.data["subtask_status"][sid] = status
        return {"subtask_id": sid, "status": status, "blocker": "x"}
    monkeypatch.setattr(leerie, "_settle_subtask", fake)
    asyncio.run(leerie.phase_execute(tmp_path, st, dict(leerie.DEFAULT_CAPS),
                                     {}, {}))
    # Only the independent work ran (g re-driven to its cap).
    assert set(ran) == {"a", "g", "d", "h"}
    status = st.data["subtask_status"]
    assert {k: status[k] for k in "abcefh"} == {
        "a": "accepted_external", "b": "accepted_external",
        "c": "accepted_external", "e": "accepted_external",
        "f": "unconverged", "h": "complete"}
    # Unfinished work outranks human-gated work.
    assert "g" in st.data["unconverged_subtasks"]["f"]["reason"]
    nhv = {e["subtask"]: e for e in st.data["needs_human_verification"]}
    assert set(nhv) == {"a", "b", "c", "e"}
    for sid in "bce":
        assert nhv[sid]["resource"] == "a production API key"
        assert nhv[sid]["source"] == "blocker"
        assert "a" in nhv[sid]["via"]
    assert nhv["e"]["via"] == ["a", "b"]
    # Never a re-drive round, never `unconverged`.
    assert not set("bce") & set(st.data.get("redrive_rounds") or {})
    assert not set("bce") & set(st.data.get("unconverged_subtasks") or {})
    assert st.data["blocker_kinds"]["b"]["kind"] == "external_precondition"
    assert st.data["completed_waves"] == 3


def test_external_dependent_cites_every_predecessor_resource(leerie):
    st = mock.Mock()
    st.data = {"subtask_status": {}, "needs_human_verification": [
        {"source": "blocker", "subtask": "a", "resource": "R1",
         "blocker": "x"},
        {"source": "blocker", "subtask": "b", "resource": "R2",
         "blocker": "y"},
        {"source": "blocker", "subtask": "z", "resource": "R3",
         "blocker": "z"}]}
    leerie._settle_external_dependent(st, "c", ["a", "b"])
    got = [(e["subtask"], e["resource"], e.get("via"))
           for e in st.data["needs_human_verification"]]
    assert got[3:] == [("c", "R1", ["a", "b"]), ("c", "R2", ["a", "b"])]
    assert st.data["subtask_status"]["c"] == "accepted_external"


@pytest.mark.parametrize("settled", ["unconverged", "accepted_external",
                                     "complete"])
def test_resume_treats_settled_statuses_as_done(patched, monkeypatch,
                                                tmp_path, settled):
    leerie = patched
    st = _mock_st([["a", "b"]], subtask_status={"a": settled})
    calls = _settle_script(leerie, monkeypatch, {"b": ["complete"]})
    asyncio.run(leerie.phase_execute(tmp_path, st, dict(leerie.DEFAULT_CAPS),
                                     {}, {}))
    assert calls == [("b", None)]
    assert st.data["subtask_status"]["a"] == settled


def test_resume_still_retries_blocked(patched, monkeypatch, tmp_path):
    leerie = patched
    st = _mock_st([["a"]], subtask_status={"a": "blocked"})
    calls = _settle_script(leerie, monkeypatch, {"a": ["complete"]})
    asyncio.run(leerie.phase_execute(tmp_path, st, dict(leerie.DEFAULT_CAPS),
                                     {}, {}))
    assert calls == [("a", None)]


def test_stop_on_blocked_halts_after_the_redrives(patched, monkeypatch,
                                                  tmp_path):
    leerie = patched
    st = _mock_st([["a"]], stop_on_blocked=True)
    calls = _settle_script(leerie, monkeypatch, {"a": ["blocked"]})
    with pytest.raises(SystemExit):
        asyncio.run(leerie.phase_execute(
            tmp_path, st, dict(leerie.DEFAULT_CAPS), {}, {}))
    assert len(calls) == 3
    assert st.data["blocked"] == {"a": "a blocker"}
    assert st.data["completed_waves"] == 0


def test_settle_redrive_note_starts_a_continuation(leerie):
    """`redrive_note` reaches the first implementer attempt as a
    continuation carrying the note — the shape of the completeness
    re-drive."""
    src = inspect.getsource(leerie._settle_subtask)
    assert 'note = redrive_note or ""' in src
    assert "continuation = redrive_note is not None" in src
