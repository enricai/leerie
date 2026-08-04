"""`_filter_provably_false_wiring_defects` — drop findings the plan contradicts.

The overlap judge has `NO_FILE_OVERLAP` / `PHANTOM_ARTIFACT` / `DROP_BREAKS_GRAPH`
catching it assert something provably false. The wiring judge had no equivalent,
so an unsubstantiable finding still killed the run.

Measured across the run corpus (2026-08-04): of the 12 residual defects in the
six runs that survive today's repair and still die at the gate, **10 are
provably false by set membership alone** — 4 name a capability the plan still
provides, 3 name one whose provider was dropped `already_satisfied` (the probe
verified it exists on the base tree), and 3 name a tag no plan snapshot ever
contained. Only 2 (`missing_provides`) are real.

Replaying the filter + expander + repair over all 15 analyzable wiring deaths:
**13 of 15 would now pass**; the 2 that still die carry genuine
`missing_provides` findings, which is the correct outcome.

Fixture is SYNTHETIC and shape-matched (`incident_2026_07_19` precedent) — this
repo is public and real plans carry private repo content.
"""
from __future__ import annotations

import json
import pathlib

import pytest

FIXTURE = (pathlib.Path(__file__).parent / "fixtures" /
           "wiring_false_findings" / "shape.json")


@pytest.fixture
def fx() -> dict:
    return json.loads(FIXTURE.read_text())


def _plans(fx): return [{"domain": "d", "subtasks":
                         json.loads(json.dumps(fx["subtasks"]))}]


def _run(leerie, fx, defects=None):
    plans = _plans(fx)
    return leerie._filter_provably_false_wiring_defects(
        plans, defects if defects is not None else fx["defects"],
        fx["dropped_subtasks"])


class TestDropsProvablyFalseFindings:
    def test_broken_by_merge_naming_a_still_provided_capability(self, leerie, fx):
        """The finding's own premise — 'a merge severed this' — is false when
        the capability is still in the provides union. 4 of 12 residuals."""
        kept, notes = _run(leerie, fx)
        assert not any(d["kind"] == "broken_by_merge" for d in kept)
        assert any("premise is false" in n for n in notes)

    def test_broken_by_drop_on_an_already_satisfied_provider(self, leerie, fx):
        """`satisfied_probe` verified the work exists on the base tree, so the
        capability needs no in-plan provider. 3 of 12 residuals."""
        kept, notes = _run(leerie, fx)
        assert not any(d["tag_or_dep"] == "cap-satisfied" for d in kept)
        assert any("already-satisfied" in n for n in notes)

    def test_a_capability_that_exists_nowhere_is_KEPT(self, leerie, fx):
        """DELIBERATELY NOT FILTERED. A tag naming nothing is mechanically
        indistinguishable from the plan genuinely lacking that work, which
        `_repair_missing_requires`' contract already treats as real and
        die-worthy ("the plan genuinely lacks the capability, not the edge").
        A predicate for this was written, then removed: it broke
        `test_unlabelled_defect_still_gates`, which pins exactly this."""
        kept, _ = _run(leerie, fx)
        assert any(d["tag_or_dep"] == "cap-invented" for d in kept)


class TestKeepsRealFindings:
    """ANTI-VACUITY: a filter that drops everything is not a filter."""

    def test_a_genuine_missing_requires_survives(self, leerie, fx):
        """check-001 needs cap-a, impl-001 provides it, no edge declared.
        This is the canonical TRUE finding — the channel measuring 99% true
        (69/70). It must never be filtered."""
        kept, _ = _run(leerie, fx)
        assert any(d["kind"] == "missing_requires"
                   and d["tag_or_dep"] == "cap-a" for d in kept)

    def test_missing_provides_survives(self, leerie, fx):
        """The 2 genuinely-real residuals in the corpus are this kind."""
        kept, _ = _run(leerie, fx)
        assert any(d["kind"] == "missing_provides" for d in kept)

    def test_not_everything_is_filtered(self, leerie, fx):
        kept, notes = _run(leerie, fx)
        assert kept, "filter removed every finding"
        assert notes, "filter removed nothing"


class TestPredicateOneIsScopedToBrokenBy:
    """The still-provided predicate must NOT apply to `missing_requires`.

    A missing_requires naming a still-provided capability is exactly the true
    shape — provider exists, consumer failed to declare the edge. Widening
    predicate 1 to all kinds silently disables the gate's main channel."""

    def test_missing_requires_on_a_provided_tag_is_kept(self, leerie, fx):
        d = [{"kind": "missing_requires", "sid": "check-001",
              "tag_or_dep": "cap-a", "concrete_reason": "s",
              "severity": "live_defect"}]
        kept, _ = _run(leerie, fx, d)
        assert kept == d

    def test_broken_by_drop_on_a_provided_tag_is_dropped(self, leerie, fx):
        d = [{"kind": "broken_by_drop", "sid": "check-001",
              "tag_or_dep": "cap-a", "concrete_reason": "s",
              "severity": "live_defect"}]
        kept, _ = _run(leerie, fx, d)
        assert kept == []


class TestMultiValueIsExpandedNotDiscarded:
    """Regression: run eed1153d's judge emitted
    'size-field-enum-enforced / integrator-schema-drift-fixed' — a TRUE
    two-tag finding. Filtering before expanding discards it as
    unsubstantiable, because the joined string resolves to nothing."""

    def test_slash_joined_true_finding_expands(self, leerie, fx):
        by = {s["id"]: s for s in fx["subtasks"]}
        prov: dict[str, list[str]] = {}
        for sid, s in by.items():
            for t in s.get("provides") or []:
                prov.setdefault(t, []).append(sid)
        d = [x for x in fx["defects"] if " / " in (x.get("tag_or_dep") or "")]
        assert d, "fixture must carry a slash-joined defect"
        out = leerie._expand_multi_value_wiring_defects(d, by, prov)
        assert len(out) == 2
        assert {x["tag_or_dep"] for x in out} == {"cap-b", "cap-c"}

    def test_expansion_precedes_filtering_at_the_call_site(self, leerie):
        import inspect
        src = inspect.getsource(leerie.phase_wiring_gate)
        assert (src.index("_expand_multi_value_wiring_defects(")
                < src.index("_filter_provably_false_wiring_defects(")), (
            "expanding after filtering discards true multi-value findings")

    def test_a_path_shaped_tag_is_not_split(self, leerie):
        """' / ' is matched WITH spaces so `src/lib/x` stays intact."""
        by = {"a-1": {"id": "a-1", "provides": ["src/lib/x"]},
              "a-2": {"id": "a-2", "provides": []}}
        prov = {"src/lib/x": ["a-1"]}
        d = [{"kind": "missing_requires", "sid": "a-2",
              "tag_or_dep": "src/lib/x", "concrete_reason": "s"}]
        assert leerie._expand_multi_value_wiring_defects(d, by, prov) == d


class TestWiring:
    def test_gate_calls_the_filter(self, leerie):
        import inspect
        assert ("_filter_provably_false_wiring_defects("
                in inspect.getsource(leerie.phase_wiring_gate))

    def test_filter_precedes_repair(self, leerie):
        import inspect
        src = inspect.getsource(leerie.phase_wiring_gate)
        assert (src.index("_filter_provably_false_wiring_defects(")
                < src.index("_repair_missing_requires("))

    def test_empty_tag_is_kept_for_the_existing_guard(self, leerie, fx):
        """An empty tag_or_dep is handled by _repair_missing_requires' own
        guard; the filter must not swallow it."""
        d = [{"kind": "missing_requires", "sid": "check-001",
              "tag_or_dep": "", "concrete_reason": "s"}]
        kept, _ = _run(leerie, fx, d)
        assert kept == d
