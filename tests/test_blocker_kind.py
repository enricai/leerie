"""Typed implementer `blocker_kind` (DESIGN §13 *A blocked implementer says
why, as a type*): code routes a `blocked` / `failed` result on the typed
kind alone — `external_precondition` (with a named resource) is the only
kind accepted, `environment_limit` retries in place once, `cannot_reproduce`
is settled by execution, every other kind is re-driven from the shared
budget. Each test drives the real `_settle_subtask` (or the router) and
asserts the resulting status and state, not the presence of code."""
from __future__ import annotations

import asyncio
import json

import pytest

from tests.test_oom_naming import env  # noqa: F401  (pytest fixture)


def _stub_results(leerie, monkeypatch, results):
    """`_run_implementer` returns `results` in order (the last repeats);
    records (continuation, note) per call."""
    calls = []

    async def _stub(sid, leerie_dir, caps, st, models, efforts,
                    continuation=False, note=""):
        calls.append((continuation, note))
        return dict(results[min(len(calls) - 1, len(results) - 1)])
    monkeypatch.setattr(leerie, "_run_implementer", _stub)
    return calls


def _blocked(kind=None, *, status="blocked", resource=None,
             blocker="stuck on it"):
    r = {"subtask_id": "t1", "status": status, "blocker": blocker,
         "summary": blocker}
    if kind is not None:
        r["blocker_kind"] = kind
    if resource is not None:
        r["external_resource"] = resource
    return r


def _settle(env):  # noqa: F811
    c = env["leerie"]
    return asyncio.run(c._settle_subtask(
        env["sid"], env["run_dir"], env["caps"], env["st"], env["models"],
        env["efforts"]))


# --- the normaliser ----------------------------------------------------------

@pytest.mark.parametrize("res, expected", [
    ({"blocker_kind": "upstream_missing"}, ("upstream_missing", "")),
    ({}, ("other", "")),
    ({"blocker_kind": "made_up"}, ("other", "")),
    ({"blocker_kind": "external_precondition"}, ("other", "")),
    ({"blocker_kind": "external_precondition", "external_resource": "  "},
     ("other", "")),
    ({"blocker_kind": "external_precondition",
      "external_resource": " a payment-provider sandbox key "},
     ("external_precondition", "a payment-provider sandbox key")),
])
def test_blocker_kind_normalises(leerie, res, expected):
    assert leerie._blocker_kind(res) == expected


def test_schema_carries_the_kinds_through_defs(leerie):
    """The enum lives in the schema's `$defs` and is exactly BLOCKER_KINDS;
    the property is a `$ref` to it (the substance: a ref that resolves)."""
    sch = leerie.SCHEMAS["implementer"]
    ref = sch["properties"]["blocker_kind"]["$ref"]
    assert ref == "#/$defs/blocker_kind"
    target = sch["$defs"][ref.rsplit("/", 1)[1]]
    assert target["enum"] == list(leerie.BLOCKER_KINDS)
    ext = sch["properties"]["external_resource"]["$ref"]
    assert sch["$defs"][ext.rsplit("/", 1)[1]] == {"type": "string"}
    assert "blocker_kind" not in sch["required"]


# --- routing, end to end through _settle_subtask ----------------------------

def test_external_precondition_is_accepted_and_listed(env, monkeypatch):  # noqa: F811
    c = env["leerie"]
    env["st"].data["needs_human_verification"] = [
        {"source": "required_item", "item_index": 0, "item": "x",
         "span": "S1", "source_ref": ""}]
    env["st"].data["blocked"] = {"t1": "older"}
    _stub_results(c, monkeypatch, [_blocked(
        "external_precondition", resource="live SMTP credentials")])
    res = _settle(env)
    assert res["status"] == "accepted_external"
    st = env["st"]
    assert st.data["subtask_status"]["t1"] == "accepted_external"
    assert "t1" not in (st.data.get("blocked") or {})
    entries = st.data["needs_human_verification"]
    assert entries[0]["source"] == "required_item"     # kept, in order
    assert entries[1] == {"source": "blocker", "subtask": "t1",
                          "resource": "live SMTP credentials",
                          "blocker": "stuck on it"}
    assert st.data["blocker_kinds"]["t1"] == {
        "kind": "external_precondition", "status": "blocked",
        "resource": "live SMTP credentials"}
    assert "accepted_external" in c._SETTLED_STATUSES


def test_external_without_a_resource_is_redriven_not_accepted(env, monkeypatch):  # noqa: F811
    c = env["leerie"]
    _stub_results(c, monkeypatch, [_blocked("external_precondition")])
    res = _settle(env)
    assert res["status"] == "blocked"
    assert res["blocker"].startswith("[other]")
    assert not env["st"].data.get("needs_human_verification")


@pytest.mark.parametrize("kind", ["nothing_to_change", "upstream_missing",
                                  "regression_unresolved", "other", None])
def test_redrive_kinds_end_blocked(env, monkeypatch, kind):  # noqa: F811
    c = env["leerie"]
    _stub_results(c, monkeypatch, [_blocked(kind)])
    res = _settle(env)
    assert res["status"] == "blocked"
    assert env["st"].data["subtask_status"]["t1"] == "blocked"
    assert env["st"].data["blocker_kinds"]["t1"]["kind"] == (kind or "other")


def test_self_reported_failed_is_redriven_not_terminal(env, monkeypatch):  # noqa: F811
    """A worker-reported `failed` used to be terminal (`broken`); typed, it
    is routed like any blocker — here re-driven."""
    c = env["leerie"]
    calls = _stub_results(c, monkeypatch, [_blocked(
        "regression_unresolved", status="failed")])
    res = _settle(env)
    assert len(calls) == 1
    assert res["status"] == "blocked"
    assert env["st"].data["subtask_status"]["t1"] == "blocked"


def test_environment_limit_retries_once_then_unconverged(env, monkeypatch):  # noqa: F811
    c = env["leerie"]
    calls = _stub_results(c, monkeypatch, [_blocked("environment_limit")])
    res = _settle(env)
    assert len(calls) == 2, "one in-place retry, then terminal"
    assert calls[1][0] is True and "environment limit" in calls[1][1]
    assert res["status"] == "unconverged"
    rec = env["st"].data["unconverged_subtasks"]["t1"]
    assert rec["kind"] == "environment_limit"


def test_environment_limit_then_success_completes_path(env, monkeypatch):  # noqa: F811
    """The retry is real: a second attempt's result is what counts."""
    c = env["leerie"]
    calls = _stub_results(c, monkeypatch, [
        _blocked("environment_limit"), _blocked("upstream_missing")])
    res = _settle(env)
    assert len(calls) == 2
    assert res["status"] == "blocked"


# --- cannot_reproduce: settled by execution ----------------------------------

def _exec_stub(leerie, monkeypatch, verdict):
    seen = []

    async def _ex(subtask, worktree, st, caps, *, sid):
        seen.append(sid)
        return {"verdict": verdict, "why": f"exec {verdict}",
                "commands": [{"argv": ["true"], "tail": "boom",
                              "rc": 0 if verdict == "pass" else 1}],
                "dirty": []}
    monkeypatch.setattr(leerie, "_settle_by_execution", _ex)
    return seen


def test_cannot_reproduce_settles_complete_when_execution_passes(
        env, monkeypatch):  # noqa: F811
    c = env["leerie"]
    seen = _exec_stub(c, monkeypatch, "pass")
    _stub_results(c, monkeypatch, [_blocked("cannot_reproduce")])
    res = _settle(env)
    assert seen == ["t1"]
    assert res["status"] == "complete"
    assert env["st"].data["subtask_status"]["t1"] == "complete"
    assert env["st"].data["dropped_subtasks"]["t1"]["reason"] == \
        "settled_by_execution"


def test_cannot_reproduce_is_redriven_when_execution_fails(env, monkeypatch):  # noqa: F811
    c = env["leerie"]
    _exec_stub(c, monkeypatch, "fail")
    _stub_results(c, monkeypatch, [_blocked("cannot_reproduce")])
    res = _settle(env)
    assert res["status"] == "blocked"
    assert "settle by execution" in res["blocker"]
    assert "untrusted data" in res["blocker"]


def test_cannot_reproduce_with_commits_is_not_executed(env, monkeypatch):  # noqa: F811
    """Commits on a 'could not reproduce' branch are unreviewed work: never
    settled by execution, always re-driven."""
    c = env["leerie"]
    seen = _exec_stub(c, monkeypatch, "pass")
    wt = env["worktree"]
    (wt / "x.py").write_text("x = 1\n")
    import subprocess
    subprocess.run(["git", "add", "-A"], cwd=wt, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "w"], cwd=wt, check=True)
    _stub_results(c, monkeypatch, [_blocked("cannot_reproduce")])
    res = _settle(env)
    assert seen == []
    assert res["status"] == "blocked"


# --- the wave loop and the run outcome ---------------------------------------

def test_accepted_external_is_not_integrated_nor_unconverged(leerie,
                                                            monkeypatch,
                                                            tmp_path):
    from tests.test_redrive_budget import _mock_st, patched  # noqa: F401
    from unittest import mock
    monkeypatch.setattr(leerie, "_run_script", mock.AsyncMock(
        return_value=mock.Mock(returncode=0, stderr="")))
    monkeypatch.setattr(leerie, "_prune_leerie_worktrees", mock.Mock())
    monkeypatch.setattr(leerie, "_disk_free_ratio", lambda p: 0.9)
    monkeypatch.setattr(leerie, "_degrade_max_parallel_for_wave",
                        lambda m, d: m)
    monkeypatch.setattr(leerie, "_scan_conflict_markers",
                        mock.AsyncMock(return_value=None))
    monkeypatch.setattr(leerie, "_prune_subtask_worktree", mock.AsyncMock())
    merged = []

    async def integrate(wave, results, *a, **k):
        done = [s for s in wave
                if results.get(s, {}).get("status") == "complete"]
        merged.extend(done)
        return done
    monkeypatch.setattr(leerie, "integrate_wave", integrate)
    calls = []

    async def fake(sid, leerie_dir, caps, st, models, efforts,
                   redrive_note=None):
        calls.append(sid)
        status = "accepted_external" if sid == "a" else "complete"
        st.data["subtask_status"][sid] = status
        return {"subtask_id": sid, "status": status}
    monkeypatch.setattr(leerie, "_settle_subtask", fake)
    st = _mock_st([["a", "b"]])
    asyncio.run(leerie.phase_execute(tmp_path, st, dict(leerie.DEFAULT_CAPS),
                                     {}, {}))
    assert calls == ["a", "b"], "accepted_external is never re-driven"
    assert merged == ["b"]
    assert "unconverged_subtasks" not in st.data
    assert st.data["completed_waves"] == 1


def test_phase_classify_keeps_blocker_entries(leerie, tmp_path, monkeypatch):
    """`phase_classify` rewrites only its own `required_item` entries: an
    accepted external blocker survives a re-classify, a stale required item
    does not. Drives the real phase with a stubbed classifier."""
    from tests import test_requirement_coverage as rc
    st = rc._state(leerie, tmp_path)
    st.data["needs_human_verification"] = [
        {"source": "blocker", "subtask": "a", "resource": "r",
         "blocker": "b"},
        {"source": "required_item", "item_index": 9, "item": "stale",
         "span": "S9", "source_ref": ""}]
    clean = rc._clean(leerie, rc._units(leerie))
    clean["required_items"][1]["verifiability"] = "external"

    async def fake_claude_p(**kw):
        return clean
    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    monkeypatch.chdir(tmp_path)
    asyncio.run(leerie.phase_classify(rc.TASK, st, dict(leerie.DEFAULT_CAPS),
                                      False, rc.MODELS, rc.EFFORTS))
    assert st.data["needs_human_verification"] == [
        {"source": "blocker", "subtask": "a", "resource": "r",
         "blocker": "b"},
        {"source": "required_item", "item_index": 1, "item": "do S4",
         "span": "S4", "source_ref": ""}]


def test_prompt_names_every_kind(leerie):
    text = (leerie.Path(leerie.__file__).resolve().parent.parent
            / "prompts" / "implementer.md").read_text()
    for k in leerie.BLOCKER_KINDS:
        assert f"`{k}`" in text, k
    assert "`external_resource`" in text
    json.dumps(leerie.SCHEMAS["implementer"])


def test_planner_and_implementer_share_the_external_kind(leerie):
    """Reconciled with stage 4b's planner blocker (D4): exactly one value is
    shared — the external precondition — and both name its resource the same
    way."""
    shared = set(leerie.PLANNER_BLOCKER_KINDS) & set(leerie.BLOCKER_KINDS)
    assert shared == {"external_precondition"}
    planner_props = leerie._schema_property_names(leerie.SCHEMAS["planner"])
    assert "external_resource" in planner_props
    assert "external_resource" in leerie.SCHEMAS["implementer"]["properties"]
