"""N3 wiring: the residual `_repair_missing_requires` leaves is ordered, not
died on (DESIGN §5 *What is left after that is ordered, not refused*).

Frozen as q4 W: fan-in `requires` behind every provider, else a per-provider
`depends_on` fallback skipping descendants, else a typed warning. The gate
dies only on a defect naming a sid that is not in the plan.
"""
from __future__ import annotations

import asyncio
import copy

import pytest


def _plans(*subtasks: dict) -> list[dict]:
    return [{"domain": "x", "subtasks": [copy.deepcopy(s) for s in subtasks]}]


def _st(id_, provides=(), requires=(), depends_on=(), cluster=None) -> dict:
    s = {"id": id_, "title": id_, "intent": id_,
         "files_likely_touched": [f"{id_}.py"],
         "provides": list(provides),
         "requires": [{"tag": t, "extent": "in_plan", "reason": "r"}
                      for t in requires],
         "depends_on": list(depends_on)}
    if cluster is not None:
        s["_cofile_cluster"] = cluster
    return s


def _defect(sid, value, kind="missing_requires") -> dict:
    return {"kind": kind, "sid": sid, "tag_or_dep": value,
            "concrete_reason": "needs it", "severity": "live_defect"}


def _waves(leerie, plans) -> dict[str, int]:
    _subs, waves = leerie._schedule(plans)
    return {sid: i for i, w in enumerate(waves) for sid in w}


def _acyclic(leerie, plans) -> bool:
    subs = {s["id"]: s for p in plans for s in p["subtasks"]}
    preds, _p, _e = leerie._build_predecessor_graph(subs, quiet=True)
    succ = {k: set() for k in subs}
    for t, src in preds.items():
        for s in src:
            succ[s].add(t)
    return not leerie._tarjan_sccs(set(subs), succ)


class TestFanIn:
    def test_two_clusters_fan_in_orders_behind_every_provider(self, leerie):
        # Two providers in DIFFERENT clusters: the unambiguous channels
        # refuse this (it used to die); fan-in requires the tag.
        plans = _plans(_st("feat-001", provides=["cap"], cluster="a"),
                       _st("feat-002", provides=["cap"], cluster="b"),
                       _st("test-001"))
        warns: list[dict] = []
        repairs, unrepaired = leerie._repair_missing_requires(
            plans, [_defect("test-001", "cap")], warns)
        assert unrepaired == [] and warns == []
        assert [r["channel"] for r in repairs] == ["fanin"]
        assert repairs[0]["providers"] == ["feat-001", "feat-002"]
        wave = _waves(leerie, plans)
        assert wave["test-001"] > wave["feat-001"]
        assert wave["test-001"] > wave["feat-002"]

    def test_broken_by_drop_gets_the_same_edge(self, leerie):
        plans = _plans(_st("feat-001", provides=["cap"], cluster="a"),
                       _st("feat-002", provides=["cap"], cluster="b"),
                       _st("test-001"))
        repairs, unrepaired = leerie._repair_missing_requires(
            plans, [_defect("test-001", "cap", kind="broken_by_drop")], [])
        assert unrepaired == []
        assert repairs[-1]["channel"] == "fanin"
        wave = _waves(leerie, plans)
        assert wave["test-001"] > max(wave["feat-001"], wave["feat-002"])


class TestDependsOnFallback:
    def test_descendant_provider_is_skipped_the_other_ordered(self, leerie):
        # feat-002 depends on test-001, so requiring `cap` (which feat-002
        # provides) would cycle; the fallback orders test-001 behind
        # feat-001 only and records feat-002 as skipped.
        plans = _plans(_st("feat-001", provides=["cap"], cluster="a"),
                       _st("feat-002", provides=["cap"], cluster="b",
                           depends_on=["test-001"]),
                       _st("test-001"))
        warns: list[dict] = []
        repairs, unrepaired = leerie._repair_missing_requires(
            plans, [_defect("test-001", "cap")], warns)
        assert unrepaired == [] and warns == []
        fb = [r for r in repairs if r["channel"] == "depends_on_fallback"]
        assert fb and fb[0]["deps_added"] == ["feat-001"]
        assert fb[0]["skipped_cyclic"] == ["feat-002"]
        test = next(s for s in plans[0]["subtasks"] if s["id"] == "test-001")
        assert test["depends_on"] == ["feat-001"]
        assert _acyclic(leerie, plans)
        wave = _waves(leerie, plans)
        assert wave["test-001"] > wave["feat-001"]

    def test_every_provider_a_descendant_warns_never_dies(self, leerie):
        plans = _plans(_st("feat-001", provides=["cap"], cluster="a",
                           depends_on=["test-001"]),
                       _st("feat-002", provides=["cap"], cluster="b",
                           depends_on=["test-001"]),
                       _st("test-001"))
        before = copy.deepcopy(plans)
        warns: list[dict] = []
        repairs, unrepaired = leerie._repair_missing_requires(
            plans, [_defect("test-001", "cap")], warns)
        assert unrepaired == [] and repairs == []
        assert [w["why"] for w in warns] == ["every_provider_is_a_descendant"]
        assert warns[0]["skipped"] == ["feat-001", "feat-002"]
        assert plans == before


class TestWarnings:
    def test_missing_provides_is_a_warning(self, leerie):
        plans = _plans(_st("feat-001"), _st("test-001"))
        warns: list[dict] = []
        _r, unrepaired = leerie._repair_missing_requires(
            plans, [_defect("feat-001", "cap", kind="missing_provides")],
            warns)
        assert unrepaired == []
        assert warns == [{"sid": "feat-001", "value": "cap",
                          "kind": "missing_provides",
                          "why": "missing_provides_not_an_edge"}]

    def test_nothing_provides_it_is_a_warning(self, leerie):
        plans = _plans(_st("feat-001"), _st("test-001"))
        warns: list[dict] = []
        _r, unrepaired = leerie._repair_missing_requires(
            plans, [_defect("test-001", "ghost-cap")], warns)
        assert unrepaired == []
        assert warns[0]["why"] == "no_in_plan_provider"

    def test_unknown_sid_is_the_only_unrepaired_shape(self, leerie):
        plans = _plans(_st("feat-001", provides=["cap"]), _st("test-001"))
        d = _defect("test-999", "cap")
        warns: list[dict] = []
        _r, unrepaired = leerie._repair_missing_requires(plans, [d], warns)
        assert unrepaired == [d] and warns == []


class TestGate:
    def _run_gate(self, leerie, tmp_path, monkeypatch, plans, defects):
        st = leerie.State(tmp_path / ".leerie", "wiring-run")
        st.data = {"task": "t", "worker_count": 0}

        async def fake_claude_p(**kw):
            assert kw["schema_key"] == "wiring_judge"
            return {"plan_reviewed": True, "wiring_defects": defects,
                    "rationale": "r"}
        monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
        monkeypatch.setattr(leerie, "_judgment_cwd", lambda st: str(tmp_path))
        caps = dict(leerie.DEFAULT_CAPS)
        out = asyncio.run(leerie.phase_wiring_gate(
            plans, "task", st, caps, {}, {}))
        return st, out

    def test_ambiguous_clusters_now_pass_with_an_edge(
            self, leerie, tmp_path, monkeypatch):
        plans = _plans(_st("feat-001", provides=["cap"], cluster="a"),
                       _st("feat-002", provides=["cap"], cluster="b"),
                       _st("test-001"))
        st, out = self._run_gate(leerie, tmp_path, monkeypatch, plans,
                                 [_defect("test-001", "cap")])
        gate = st.data["wiring_gate"]
        assert [r["channel"] for r in gate["repairs"]] == ["fanin"]
        assert gate["warnings"] == []
        test = next(s for s in out[0]["subtasks"] if s["id"] == "test-001")
        assert [e["tag"] for e in test["requires"]] == ["cap"]

    def test_warning_persisted_and_run_continues(
            self, leerie, tmp_path, monkeypatch):
        plans = _plans(_st("feat-001"), _st("test-001"))
        st, _out = self._run_gate(leerie, tmp_path, monkeypatch, plans,
                                  [_defect("test-001", "ghost-cap")])
        assert st.data["wiring_gate"]["warnings"][0]["why"] == \
            "no_in_plan_provider"

    def test_dies_only_on_unknown_sid(self, leerie, tmp_path, monkeypatch):
        plans = _plans(_st("feat-001", provides=["cap"]), _st("test-001"))
        with pytest.raises(SystemExit):
            self._run_gate(leerie, tmp_path, monkeypatch, plans,
                           [_defect("test-999", "cap")])
