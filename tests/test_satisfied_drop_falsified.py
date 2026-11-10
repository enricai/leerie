"""A subtask is dropped as already satisfied only when the falsifier cannot
show its criterion unmet on the base (DESIGN §8).

Live (barnacle e7512406): the phase-3 satisfied-probe dropped
`bugfix-005-2` ("skips loopback and link-local addresses") by reading,
while saying it had run no test; its claim about `::ffff`-mapped addresses
was false, and its own verify tests pass without the behaviour. Replays
dropped it 6 of 6 in every tool arm.

Real `_filter_satisfied_subtasks` / `_falsify_satisfied_drops` /
`_falsify_items` / `_falsifier_gates` over a real pytest repository and the
run's own git repository; only `claude_p` and the TS AST facts are stubbed
(the probes are Python), as in tests/test_delivery_detection.py. Synthetic
text."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from tests.conftest import run_git_out
from tests.test_delivery_detection import (ITEM, PASSING_PROBE_SRC, PROBE_REL,
                                           PROBE_SRC, _claim, _detection_env)

MODELS = {"satisfied_probe": "sonnet", "falsifier": "sonnet"}
EFFORTS = {"satisfied_probe": None, "falsifier": "medium"}
SEED = "the calc tests pass"
SATISFIED = {"satisfied": True, "evidence": "add is implemented in calc.py",
             "checked": ["calc.py"], "unsatisfied_reason": "cannot_verify",
             "equivalent_coverage_exists": False,
             "sibling_invalidation_risk": True}


def _plans() -> list[dict]:
    return [{"domain": "bug-fixing", "status": "ready", "subtasks": [
        {"id": "t-1", "title": "add sums", "intent": ITEM,
         "success_criteria_seed": SEED, "files_likely_touched": ["calc.py"],
         "provides": ["sum"]},
        {"id": "t-2", "title": "other", "intent": "something else",
         "success_criteria_seed": "the other tests pass",
         "files_likely_touched": ["other.py"]}]}]


def _phase3_state(leerie, tmp_path, monkeypatch, *, k=3, ts=True):
    repo, head, st, caps = _detection_env(leerie, tmp_path, monkeypatch,
                                          judged=False, ts=ts, k=k)
    # Phase 3 runs before any held-out set exists in this repository.
    st.data.pop("acceptance", None)
    st.save()
    return repo, head, st, caps


def _stub(leerie, monkeypatch, verdicts: dict, behave):
    """claude_p: a satisfied_probe answers `verdicts[sid]`; a falsifier runs
    `behave(kw, n)` in its own checkout (n = falsifier calls so far)."""
    calls: list[dict] = []

    async def fake_claude_p(**kw):
        calls.append(kw)
        if kw["schema_key"] == "satisfied_probe":
            return verdicts[kw["sid"][len("satisfied_probe-"):]]
        assert kw["schema_key"] == "falsifier", kw["schema_key"]
        n = sum(1 for c in calls if c["schema_key"] == "falsifier") - 1
        return behave(kw, n)
    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    return calls


def _write(kw, src):
    p = Path(kw["cwd"]) / PROBE_REL
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(src)


def _sweep(leerie, st, repo, caps, plans):
    return asyncio.run(leerie._filter_satisfied_subtasks(
        plans, repo, st, caps, MODELS, EFFORTS))


def _ids(plans):
    return [s["id"] for p in plans for s in p["subtasks"]]


def _falsifier_sids(calls):
    return [c["sid"] for c in calls if c["schema_key"] == "falsifier"]


def test_a_criterion_the_base_fails_keeps_the_subtask(
        leerie, tmp_path, monkeypatch):
    repo, head, st, caps = _phase3_state(leerie, tmp_path, monkeypatch)

    def behave(kw, n):
        _write(kw, PROBE_SRC)          # fails on the base: add subtracts
        return _claim()
    calls = _stub(leerie, monkeypatch,
                  {"t-1": SATISFIED, "t-2": {"satisfied": False,
                                             "evidence": "missing"}}, behave)
    plans = _plans()
    assert _sweep(leerie, st, repo, caps, plans) is None
    assert _ids(plans) == ["t-1", "t-2"]
    assert "t-1" not in (st.data.get("dropped_subtasks") or {})
    # One sample, confirmed by every gate, ends it (any-of-k).
    assert _falsifier_sids(calls) == ["falsifier-base-t-1-s0"]
    rec = st.data["satisfied_probe_cache"]["t-1"]
    assert rec["base_sha"] == head
    s0 = rec["falsifier"]["samples"][0]
    assert rec["falsifier"]["confirmed"] is True and s0["confirmed"] is True
    assert s0["gates"] == {
        "new_file": True, "not_a_lead": True, "runnable": True,
        "item_quote": True, "case_fails": True, "fails_under_bstar": True,
        "facts_read": True, "assertion_in_case": True, "not_coupled": True,
        "at_assertion": True}
    # Its probe is kept under the sweep's own directory, never detection's.
    saved = s0["probe_saved"]
    assert Path(saved["dir"]) == (st.run_dir / "satisfied-probe" / "probes"
                                  / "base-t-1")
    assert Path(saved["dir"], saved["file"]).read_text() == PROBE_SRC
    assert not (st.run_dir / "acceptance" / "probes").exists()
    assert "delivery_gate" not in st.data
    assert not list((st.run_dir / "worktrees").glob("*falsifier-*"))


def test_the_payload_is_the_subtasks_words_on_the_runs_start(
        leerie, tmp_path, monkeypatch):
    repo, head, st, caps = _phase3_state(leerie, tmp_path, monkeypatch, k=1)
    heads: list[str] = []

    def behave(kw, n):
        heads.append(run_git_out(Path(kw["cwd"]), "rev-parse", "HEAD"))
        return _claim(verdict="could_not_demonstrate", probe_path="")
    calls = _stub(leerie, monkeypatch, {"t-1": SATISFIED,
                                        "t-2": {"satisfied": False,
                                                "evidence": "x"}}, behave)
    _sweep(leerie, st, repo, caps, _plans())
    (p0,) = [c for c in calls if c["schema_key"] == "falsifier"]
    up = p0["user_prompt"]
    item = json.loads(up.split("THE ITEM TO CHECK:\n", 1)[1]
                      .split("\n\n", 1)[0])
    assert item == {"item": ITEM, "source_ref": "subtask t-1",
                    "parts": [SEED]}
    assert leerie._FALSIFIER_BASE_START in up
    assert up.rstrip().endswith(leerie._FALSIFIER_BASE_CLOSE)
    assert "FINISHED tree" not in up and head not in up
    assert p0["system_prompt"] == leerie._load_prompt("falsifier")
    assert p0["allowed_tools"] == leerie.ACT_TOOLS and p0["autonomous"]
    assert heads == [head]          # the base, where the run starts
    # Detection's own payload is unchanged by the new argument.
    it = {"item": ITEM, "span": "S1"}
    assert leerie._falsifier_user_prompt("T", it, "abc", "", [], [], []) == (
        leerie._falsifier_user_prompt("T", it, "abc", "", [], [], [],
                                      base_claim=False))
    assert "FINISHED tree" in leerie._falsifier_user_prompt(
        "T", it, "abc", "", [], [], [])


def test_no_demonstration_lets_the_drop_stand(leerie, tmp_path, monkeypatch):
    repo, head, st, caps = _phase3_state(leerie, tmp_path, monkeypatch)
    calls = _stub(leerie, monkeypatch,
                  {"t-1": SATISFIED, "t-2": {"satisfied": False,
                                             "evidence": "x"}},
                  lambda kw, n: _claim(verdict="could_not_demonstrate",
                                       probe_path=""))
    plans = _plans()
    _sweep(leerie, st, repo, caps, plans)
    assert _ids(plans) == ["t-2"]
    assert st.data["dropped_subtasks"]["t-1"]["reason"] == "already_satisfied"
    assert _falsifier_sids(calls) == [f"falsifier-base-t-1-s{n}"
                                      for n in range(3)]
    f = st.data["satisfied_probe_cache"]["t-1"]["falsifier"]
    assert f["confirmed"] is False and len(f["samples"]) == 3


def test_a_probe_that_passes_on_the_base_keeps_nothing(
        leerie, tmp_path, monkeypatch):
    """A claimed demonstration whose probe passes on the base is checked by
    code, not taken: the drop stands."""
    repo, head, st, caps = _phase3_state(leerie, tmp_path, monkeypatch, k=1)

    def behave(kw, n):
        _write(kw, PASSING_PROBE_SRC)
        return _claim()
    _stub(leerie, monkeypatch, {"t-1": SATISFIED,
                                "t-2": {"satisfied": False,
                                        "evidence": "x"}}, behave)
    plans = _plans()
    _sweep(leerie, st, repo, caps, plans)
    assert _ids(plans) == ["t-2"]
    s0 = st.data["satisfied_probe_cache"]["t-1"]["falsifier"]["samples"][0]
    assert s0["gates"]["case_fails"] is False and s0["confirmed"] is False


def test_a_cached_outcome_is_reused_on_resume(leerie, tmp_path, monkeypatch):
    repo, head, st, caps = _phase3_state(leerie, tmp_path, monkeypatch)
    st.data["satisfied_probe_cache"] = {
        "t-1": {**SATISFIED, "base_sha": head,
                "falsifier": {"confirmed": True, "samples": []}},
        "t-2": {**SATISFIED, "base_sha": head,
                "falsifier": {"confirmed": False, "samples": []}}}
    st.save()
    calls = _stub(leerie, monkeypatch, {}, None)
    plans = _plans()
    _sweep(leerie, st, repo, caps, plans)
    assert calls == []
    assert _ids(plans) == ["t-1"]
    assert list(st.data["dropped_subtasks"]) == ["t-2"]


def test_a_falsifier_that_cannot_run_leaves_the_drop_uncached(
        leerie, tmp_path, monkeypatch):
    repo, head, st, caps = _phase3_state(leerie, tmp_path, monkeypatch, ts=False)
    calls = _stub(leerie, monkeypatch,
                  {"t-1": SATISFIED, "t-2": {"satisfied": False,
                                             "evidence": "x"}}, None)
    plans = _plans()
    _sweep(leerie, st, repo, caps, plans)
    assert _falsifier_sids(calls) == []
    assert _ids(plans) == ["t-2"]
    assert "falsifier" not in st.data["satisfied_probe_cache"]["t-1"]


def test_only_an_already_satisfied_drop_is_falsified(
        leerie, tmp_path, monkeypatch):
    repo, head, st, caps = _phase3_state(leerie, tmp_path, monkeypatch)
    calls = _stub(leerie, monkeypatch, {
        "t-1": {"satisfied": False, "evidence": "covered elsewhere",
                "unsatisfied_reason": "artifact_missing",
                "equivalent_coverage_exists": True,
                "sibling_invalidation_risk": False},
        "t-2": {"satisfied": False, "evidence": "x",
                "unsatisfied_reason": "behavior_gap"}}, None)
    plans = _plans()
    _sweep(leerie, st, repo, caps, plans)
    assert _falsifier_sids(calls) == []
    assert _ids(plans) == ["t-2"]
    assert (st.data["dropped_subtasks"]["t-1"]["reason"]
            == "equivalent_coverage")
