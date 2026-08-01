"""Corpus regression lock for the wiring-gate auto-repair (DESIGN §5).

`tests/test_wiring_gate_repair.py` pins the repair's *rules* against synthetic
plans — one shape per rule. This file pins its *measured effect* against every
run in the corpus that ever died at the wiring gate, so the numbers quoted in
DESIGN/IMPLEMENTATION and in the PR that shipped the feature stay reproducible
after this session's scratchpad is gone.

Why it matters: the justification for repairing at all is "3 of the 6 historical
deaths proceed, and the other 3 are refused for principled reasons." That claim
drove a DESIGN contract change (detect-and-die → detect-repair-then-die). If a
future edit to `_repair_missing_requires` quietly widens or narrows what it
accepts, the per-rule tests may all still pass while this ratio moves — which is
the signal that the trade-off underneath the feature has changed.

The fixture is the real recorded `wiring_judge` output and the real post-filter
plans from six runs, reduced to the fields the repair actually reads
(`id`/`provides`/`requires`/`depends_on`/`files_likely_touched`, and each
defect's `kind`/`sid`/`tag_or_dep`/`severity`). Every `concrete_reason` is
redacted to a constant — the repair never reads it, only its non-emptiness
gates, so the fixture asserts presence without carrying prose from real runs.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

_FIXTURE = (Path(__file__).resolve().parent
            / "fixtures" / "wiring_repair_corpus" / "corpus.json")

# run -> (live defects, repairable defects). Measured 2026-08-01 against the
# shipped `_repair_missing_requires`. The three that repair fully are the
# feature's whole justification; the three that do not are refused for reasons
# the rule is deliberately built to respect (see the per-run comments).
EXPECTED = {
    "29a6bf8e": (1, 1),    # fully repairable -> run proceeds
    "3a4abba3": (2, 2),    # fully repairable -> run proceeds
    "6146bd2f": (4, 4),    # fully repairable -> run proceeds
    "eed1153d": (5, 4),    # 1 tag has NO in-plan provider
    "ad69057f": (11, 8),   # 3 tags have several providers (ambiguous)
    "62a19deb": (22, 0),   # every tag has NO provider — correctly refused
}


def _corpus() -> dict:
    return json.loads(_FIXTURE.read_text())


def test_fixture_covers_every_expected_run():
    """Guard the guard: a fixture that silently lost runs would make every
    per-run assertion below vacuous."""
    assert set(_corpus()) == set(EXPECTED)


@pytest.mark.parametrize("run", sorted(EXPECTED))
def test_repair_counts_match_the_measured_corpus(leerie, run):
    data = _corpus()[run]
    plans = copy.deepcopy(data["plans"])
    live = leerie._live_wiring_defects(
        {"wiring_defects": data["wiring_defects"]})
    repairs, unrepaired = leerie._repair_missing_requires(plans, live)

    exp_live, exp_rep = EXPECTED[run]
    assert len(live) == exp_live, (
        f"{run}: live-defect count drifted — the gating filter changed")
    assert len(repairs) == exp_rep, (
        f"{run}: repaired {len(repairs)} of {len(live)}, expected {exp_rep}. "
        "The repair rule's acceptance criteria have moved; confirm that is "
        "intended before updating this number")
    assert len(repairs) + len(unrepaired) <= len(live)


@pytest.mark.parametrize("run", ["29a6bf8e", "3a4abba3", "6146bd2f"])
def test_fully_repaired_runs_schedule_with_producers_first(leerie, run):
    """The three runs the feature exists for: after repair the plan schedules,
    and every added edge actually orders the consumer behind its producer."""
    data = _corpus()[run]
    plans = copy.deepcopy(data["plans"])
    live = leerie._live_wiring_defects(
        {"wiring_defects": data["wiring_defects"]})
    repairs, unrepaired = leerie._repair_missing_requires(plans, live)
    assert not unrepaired, f"{run} is expected to repair fully"

    subtasks, waves = leerie.schedule(copy.deepcopy(plans))
    pos = {sid: i for i, w in enumerate(waves) for sid in w}
    for r in repairs:
        assert pos[r["provider"]] < pos[r["sid"]], (
            f"{run}: {r['sid']} must be scheduled after {r['provider']}, "
            f"the sole provider of {r['tag']!r}")
    assert not leerie.check_plan_wiring(subtasks)
    leerie.validate_plan(subtasks)


def test_unrepairable_runs_are_refused_for_principled_reasons(leerie):
    """The three that still die must fail the rule for the documented
    reasons — no provider, or several — not because the repair silently
    stopped working."""
    reasons = {}
    for run in ("eed1153d", "ad69057f", "62a19deb"):
        data = _corpus()[run]
        plans = copy.deepcopy(data["plans"])
        by_id = {s["id"]: s for p in plans for s in p.get("subtasks", [])}
        providers: dict[str, list[str]] = {}
        for sid, s in by_id.items():
            for tag in s.get("provides") or []:
                providers.setdefault(tag, []).append(sid)
        live = leerie._live_wiring_defects(
            {"wiring_defects": data["wiring_defects"]})
        _repairs, unrepaired = leerie._repair_missing_requires(plans, live)
        for d in unrepaired:
            n = len(providers.get((d.get("tag_or_dep") or "").strip(), []))
            reasons.setdefault(run, set()).add(
                "no_provider" if n == 0 else
                "several_providers" if n > 1 else "other")
    assert reasons["62a19deb"] == {"no_provider"}
    assert reasons["eed1153d"] == {"no_provider"}
    assert reasons["ad69057f"] == {"several_providers"}
    assert "other" not in set().union(*reasons.values()), (
        "an unrepaired defect fell through for a reason the rule does not "
        "document — investigate before accepting")


def test_headline_ratio_is_three_of_six(leerie):
    """The number DESIGN, IMPLEMENTATION and PR #145 all quote."""
    full = 0
    for run, data in _corpus().items():
        plans = copy.deepcopy(data["plans"])
        live = leerie._live_wiring_defects(
            {"wiring_defects": data["wiring_defects"]})
        _r, unrepaired = leerie._repair_missing_requires(plans, live)
        if live and not unrepaired:
            full += 1
    assert full == 3, (
        f"{full}/6 runs now repair fully, not 3/6. The documented trade-off "
        "behind detect-repair-then-die has moved; update DESIGN §5 and "
        "IMPLEMENTATION if that is intended")
