"""Corpus regression lock for the wiring-gate auto-repair (DESIGN §5).

`tests/test_wiring_gate_repair.py` pins the repair's *rules* against synthetic
plans — one shape per rule. This file pins its *measured effect* against every
run in the corpus that ever died at the wiring gate, so the numbers quoted in
DESIGN/IMPLEMENTATION and in the PR that shipped the feature stay reproducible
after this session's scratchpad is gone.

Why it matters: the justification for repairing at all is a ratio — "N of the 6
historical deaths proceed, and the rest are refused for principled reasons" —
and that claim drove a DESIGN contract change (detect-and-die →
detect-repair-then-die). It was **3 of 6** when the repair read only the tag
channel (PR #145); reading the id and single-cofile-cluster channels as well
took it to **5 of 6**. If a future edit to `_repair_missing_requires` quietly
widens or narrows what it accepts, the per-rule tests may all still pass while
this ratio moves — which is the signal that the trade-off underneath the
feature has changed.

`EXPECTED_CHANNELS` pins the same runs a second way: which channel each
repair flows through. A defect that still repairs but for the *wrong* reason
(an id-channel defect accidentally matching a same-named tag, say) leaves the
counts identical and is invisible without it.

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

# run -> (live defects, repairable defects). Re-measured 2026-08-03 after the
# repair learned to read BOTH dependency channels (DESIGN §5): `tag_or_dep`
# carries a capability tag OR a subtask id, and a multi-provider tag whose
# providers all share one `_cofile_cluster` is a sub-file split, not a real
# ambiguity. Five of six now repair fully; the sixth is refused for the one
# reason the rule is deliberately built to respect — the plan genuinely lacks
# the work (see the per-run comments and `EXPECTED_CHANNELS`).
EXPECTED = {
    "29a6bf8e": (1, 1),    # fully repairable -> run proceeds
    "3a4abba3": (2, 2),    # fully repairable -> run proceeds
    "6146bd2f": (4, 4),    # fully repairable -> run proceeds
    "eed1153d": (5, 4),    # 1 tag: no provider, and not a subtask id either
    "ad69057f": (11, 11),  # 8 tag + 3 single-cluster fan-out (was: refused)
    "62a19deb": (22, 22),  # every defect names a subtask id (was: refused)
}

# The channel each run's repairs actually flow through. This is the assertion
# that would catch a regression where a defect still repairs but via the wrong
# reasoning — e.g. an id-channel defect accidentally matching a same-named tag.
EXPECTED_CHANNELS = {
    "29a6bf8e": {"tag": 1},
    "3a4abba3": {"tag": 2},
    "6146bd2f": {"tag": 4},
    "eed1153d": {"tag": 4},
    "ad69057f": {"tag": 8, "cofile_cluster": 3},
    "62a19deb": {"id": 22},
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


@pytest.mark.parametrize("run", sorted(EXPECTED_CHANNELS))
def test_repairs_flow_through_the_expected_channel(leerie, run):
    """A defect repairing for the *wrong* reason is a silent regression that
    the count assertions above cannot see."""
    data = _corpus()[run]
    plans = copy.deepcopy(data["plans"])
    live = leerie._live_wiring_defects(
        {"wiring_defects": data["wiring_defects"]})
    repairs, _unrepaired = leerie._repair_missing_requires(plans, live)

    seen: dict[str, int] = {}
    for r in repairs:
        assert r.get("channel"), (
            f"{run}: repair {r} carries no channel — the audit trail in "
            "state.data['wiring_gate'].repairs would be unattributable")
        seen[r["channel"]] = seen.get(r["channel"], 0) + 1
    assert seen == EXPECTED_CHANNELS[run]


@pytest.mark.parametrize("run", ["29a6bf8e", "3a4abba3", "6146bd2f",
                                 "62a19deb", "ad69057f"])
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


def test_the_one_unrepairable_run_is_refused_for_a_principled_reason(leerie):
    """`eed1153d` must fail for the documented reason — the plan genuinely
    lacks the work — not because the repair silently stopped working.

    "No provider" alone is no longer a sufficient explanation: since the
    repair reads the id channel too, a value with no provider that names a
    surviving subtask id IS repairable. The refusal is only principled when
    the value is neither.
    """
    data = _corpus()["eed1153d"]
    plans = copy.deepcopy(data["plans"])
    by_id = {s["id"]: s for p in plans for s in p.get("subtasks", [])}
    providers: dict[str, list[str]] = {}
    for sid, s in by_id.items():
        for tag in s.get("provides") or []:
            providers.setdefault(tag, []).append(sid)
    live = leerie._live_wiring_defects(
        {"wiring_defects": data["wiring_defects"]})
    _repairs, unrepaired = leerie._repair_missing_requires(plans, live)

    assert len(unrepaired) == 1
    for d in unrepaired:
        val = (d.get("tag_or_dep") or "").strip()
        assert not providers.get(val), f"{val!r} has an in-plan provider"
        assert val not in by_id, (
            f"{val!r} names a surviving subtask id — the id channel should "
            "have repaired this rather than refusing it")


def test_runs_refused_only_for_counting_split_siblings_now_repair(leerie):
    """Regression pin for the two runs the channel work unblocked.

    `62a19deb` died with 22 defects that every one named a subtask id, and
    `ad69057f` with 3 that named a tag whose 11 providers were all one
    sub-file cluster. Both were refused as "no provider" / "ambiguous" by a
    rule that only read the tag channel and counted split siblings as rival
    providers. Neither refusal was ever principled.
    """
    for run in ("62a19deb", "ad69057f"):
        data = _corpus()[run]
        plans = copy.deepcopy(data["plans"])
        live = leerie._live_wiring_defects(
            {"wiring_defects": data["wiring_defects"]})
        _repairs, unrepaired = leerie._repair_missing_requires(plans, live)
        assert not unrepaired, (
            f"{run}: {len(unrepaired)} defect(s) still refused — this run is "
            "the whole justification for reading both channels")


def test_headline_ratio_is_five_of_six(leerie):
    """The number DESIGN and IMPLEMENTATION quote after the channel work.

    Was 3/6 when the repair read the tag channel alone (PR #145).
    """
    full = 0
    for run, data in _corpus().items():
        plans = copy.deepcopy(data["plans"])
        live = leerie._live_wiring_defects(
            {"wiring_defects": data["wiring_defects"]})
        _r, unrepaired = leerie._repair_missing_requires(plans, live)
        if live and not unrepaired:
            full += 1
    assert full == 5, (
        f"{full}/6 runs now repair fully, not 5/6. The documented trade-off "
        "behind detect-repair-then-die has moved; update DESIGN §5 and "
        "IMPLEMENTATION if that is intended")
