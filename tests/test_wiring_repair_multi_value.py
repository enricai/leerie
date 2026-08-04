"""`_expand_multi_value_wiring_defects` — a comma-joined `tag_or_dep`.

`prompts/wiring_judge.md` asks for a single value ("the capability tag or
subtask id the edge concerns") but `SCHEMAS["wiring_judge"]` types the field
as a bare `string`, so a comma-joined list is schema-valid. Nothing enforced
the prompt's request — the CLAUDE.md §12 failure mode exactly.

Measured on run `488c42e5` (2026-08-04): the judge emitted ONE defect naming
fourteen subtask ids at once. `bugfix-018` — "Run lint, build, and targeted
test suites across all Phase 2a changes" — genuinely needed to run after every
test subtask, and already declared `depends_on` on 17 others including two
tests, so the finding was **correct**. `_repair_missing_requires` resolves
`tag_or_dep` by exact lookup, matched nothing, returned the defect unrepaired,
and `phase_wiring_gate` die()d — discarding a valid 34-subtask plan over
formatting.

`tests/fixtures/wiring_multi_value/shape.json` is a **synthetic**
shape-matched reproduction — 32 subtasks, a 14-value comma-joined
`tag_or_dep`, an acyclic graph, and two well-formed defects that must pass
through untouched. Following the `incident_2026_07_19` precedent the real
run's plan is deliberately NOT committed: it carried subtask titles,
capability tags and source file names from a private repo, and this repo is
public. The test's value is the SHAPE, not the content.
"""
from __future__ import annotations

import json
import pathlib

import pytest

FIXTURE = (pathlib.Path(__file__).parent / "fixtures" /
           "wiring_multi_value" / "shape.json")
MULTI = json.loads(FIXTURE.read_text())["multi_value"]


@pytest.fixture
def incident() -> dict:
    return json.loads(FIXTURE.read_text())


def _index(subtasks: list[dict]):
    by_id = {s["id"]: s for s in subtasks}
    providers: dict[str, list[str]] = {}
    for sid, s in by_id.items():
        for tag in s.get("provides") or []:
            providers.setdefault(tag, []).append(sid)
    return by_id, providers


def _defect(**kw) -> dict:
    d = {"kind": "missing_requires", "sid": "a-1", "tag_or_dep": "x",
         "concrete_reason": "r", "severity": "live_defect"}
    d.update(kw)
    return d


# --- the recorded incident ------------------------------------------------ #

class TestTheRecordedIncident:
    def test_the_14_id_defect_expands_to_14(self, leerie, incident):
        by_id, prov = _index(incident["subtasks"])
        out = leerie._expand_multi_value_wiring_defects(
            incident["defects"], by_id, prov)
        # 2 well-formed pass through + 14 expanded
        assert len(out) == 16
        exp = [d for d in out if d.get("_expanded_from")]
        assert len(exp) == 14
        assert {d["tag_or_dep"] for d in exp} == {
            p.strip() for p in MULTI.split(",")}

    def test_every_expanded_value_is_a_real_subtask(self, leerie, incident):
        by_id, prov = _index(incident["subtasks"])
        out = leerie._expand_multi_value_wiring_defects(
            incident["defects"], by_id, prov)
        for d in out:
            if d.get("_expanded_from"):
                assert d["tag_or_dep"] in by_id

    def test_well_formed_defects_pass_through_untouched(
            self, leerie, incident):
        by_id, prov = _index(incident["subtasks"])
        out = leerie._expand_multi_value_wiring_defects(
            incident["defects"], by_id, prov)
        plain = [d for d in out if not d.get("_expanded_from")]
        assert len(plain) == 2
        assert {d["tag_or_dep"] for d in plain} == {
            "component-006-ready", "component-014-ready"}

    def test_expanded_defects_inherit_the_reason(self, leerie, incident):
        by_id, prov = _index(incident["subtasks"])
        out = leerie._expand_multi_value_wiring_defects(
            incident["defects"], by_id, prov)
        for d in out:
            assert d["concrete_reason"] == "synthetic"
            assert d["kind"] == "missing_requires"
            assert d["sid"] in {"check-006", "check-014", "impl-018"}

    def test_end_to_end_repair_leaves_no_residual(self, leerie, incident):
        """THE POINT: the gate die()s only on unrepaired defects. This is the
        assertion that the run would have survived."""
        plans = [{"domain": "d", "subtasks":
                  json.loads(json.dumps(incident["subtasks"]))}]
        repairs, unrepaired = leerie._repair_missing_requires(
            plans, json.loads(json.dumps(incident["defects"])))
        assert unrepaired == [], (
            f"gate would still die on: "
            f"{[d.get('tag_or_dep') for d in unrepaired]}")
        assert len(repairs) >= 14

    def test_repair_adds_the_14_depends_on_edges(self, leerie, incident):
        plans = [{"domain": "d", "subtasks":
                  json.loads(json.dumps(incident["subtasks"]))}]
        leerie._repair_missing_requires(
            plans, json.loads(json.dumps(incident["defects"])))
        b = {s["id"]: s for s in plans[0]["subtasks"]}["impl-018"]
        for part in (p.strip() for p in MULTI.split(",")):
            assert part in (b.get("depends_on") or []), part


# --- conservatism: well-formed input must never be perturbed -------------- #

class TestDoesNotFireOnWellFormedInput:
    def test_single_value_is_untouched(self, leerie, incident):
        by_id, prov = _index(incident["subtasks"])
        d = [_defect(sid="check-006",
                     tag_or_dep="component-006-ready")]
        assert leerie._expand_multi_value_wiring_defects(d, by_id, prov) == d

    def test_a_tag_containing_a_comma_that_resolves_is_untouched(self, leerie):
        """ANTI-REGRESSION: splitting must be a FALLBACK. A real tag with a
        comma in it resolves whole and must not be mangled."""
        by_id = {"a-1": {"id": "a-1", "provides": ["weird,tag"]},
                 "a-2": {"id": "a-2"}}
        prov = {"weird,tag": ["a-1"]}
        d = [_defect(sid="a-2", tag_or_dep="weird,tag")]
        out = leerie._expand_multi_value_wiring_defects(d, by_id, prov)
        assert out == d and not out[0].get("_expanded_from")

    def test_whole_string_wins_when_parts_also_resolve(self, leerie):
        """ANTI-VACUITY: the resolves-whole guard must be load-bearing.

        A tag that resolves WHOLE and whose comma-parts ALSO resolve
        individually is the only shape that distinguishes "try whole first"
        from "always split". Without the guard this expands into two wrong
        edges instead of matching the tag the judge actually named.
        (The earlier comma-tag test does not kill that mutant: its parts
        do not resolve, so the all-parts-resolve guard catches it anyway.)"""
        by_id = {"a-1": {"id": "a-1", "provides": ["alpha,beta"]},
                 "alpha": {"id": "alpha", "provides": []},
                 "beta": {"id": "beta", "provides": []},
                 "a-2": {"id": "a-2", "provides": []}}
        prov = {"alpha,beta": ["a-1"]}
        d = [_defect(sid="a-2", tag_or_dep="alpha,beta")]
        out = leerie._expand_multi_value_wiring_defects(d, by_id, prov)
        assert len(out) == 1, "must match the whole tag, not split it"
        assert out[0]["tag_or_dep"] == "alpha,beta"
        assert not out[0].get("_expanded_from")

    def test_partial_resolution_does_not_expand(self, leerie, incident):
        """If only SOME parts resolve the judge meant something else; applying
        a subset would encode a different claim than the one made."""
        by_id, prov = _index(incident["subtasks"])
        d = [_defect(sid="impl-018",
                     tag_or_dep="check-001, nonexistent-999")]
        out = leerie._expand_multi_value_wiring_defects(d, by_id, prov)
        assert out == d

    def test_other_kinds_are_not_expanded(self, leerie, incident):
        by_id, prov = _index(incident["subtasks"])
        d = [_defect(kind="broken_by_drop", sid="impl-018",
                     tag_or_dep="check-001, check-002")]
        assert leerie._expand_multi_value_wiring_defects(d, by_id, prov) == d

    def test_empty_and_degenerate_values(self, leerie, incident):
        by_id, prov = _index(incident["subtasks"])
        for v in ("", ",", " , ", "   "):
            d = [_defect(sid="impl-018", tag_or_dep=v)]
            assert leerie._expand_multi_value_wiring_defects(
                d, by_id, prov) == d, v

    def test_order_is_preserved(self, leerie, incident):
        by_id, prov = _index(incident["subtasks"])
        out = leerie._expand_multi_value_wiring_defects(
            incident["defects"], by_id, prov)
        assert out[0]["sid"] == "check-006"
        assert out[1]["sid"] == "check-014"
        assert all(d["sid"] == "impl-018" for d in out[2:])


# --- the cycle guard must still hold -------------------------------------- #

class TestCycleGuardStillApplies:
    def test_an_expanded_edge_that_would_cycle_is_refused(self, leerie):
        """Expansion must not bypass `_would_cycle_after`. b-1 already
        depends on a-1, so a-1 -> b-1 closes a cycle and must be refused."""
        subs = [{"id": "a-1", "depends_on": [], "requires": [], "provides": []},
                {"id": "b-1", "depends_on": ["a-1"], "requires": [],
                 "provides": []},
                {"id": "c-1", "depends_on": [], "requires": [], "provides": []}]
        plans = [{"domain": "d", "subtasks": subs}]
        d = [_defect(sid="a-1", tag_or_dep="b-1, c-1")]
        repairs, unrepaired = leerie._repair_missing_requires(plans, d)
        got = {r["tag"] for r in repairs}
        assert "c-1" in got, "the safe edge should still be applied"
        assert "b-1" not in got, "the cycling edge must be refused"
        assert unrepaired, "the refused edge must reach the die path"


# --- wiring --------------------------------------------------------------- #

class TestWiring:
    def test_repair_calls_the_expander(self, leerie):
        import inspect
        src = inspect.getsource(leerie._repair_missing_requires)
        assert "_expand_multi_value_wiring_defects(" in src

    def test_expansion_precedes_the_repair_loop(self, leerie):
        import inspect
        src = inspect.getsource(leerie._repair_missing_requires)
        assert (src.index("_expand_multi_value_wiring_defects(")
                < src.index("for d in defects:"))
