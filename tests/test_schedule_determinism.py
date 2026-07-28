"""Pin schedule()'s determinism under a plans checkpoint round-trip.

The planning-resume design (see the resumable-planning-checkpoints task)
rests on a safety-by-construction property: `schedule()` re-sorts every
wave by subtask id (`orchestrator/leerie.py:17374`,
`wave = sorted(sid for sid in remaining if preds[sid] <= done)`), so the
wave partition is a pure function of the dependency graph plus
lexicographic ids — independent of dict/set iteration order and of the
input plan/subtask order. That means a `plans` list persisted to
state.json and reloaded on `--resume` (a JSON round-trip) rehydrates to
a byte-identical schedule as the original in-memory run.

This test proves the property directly against schedule() rather than
assuming it: a fresh call, a JSON-round-tripped call, and calls with
plan order and per-plan subtask order reversed must all agree on
`waves` and on the merged subtask id set. Removing the `sorted(...)` at
the wave-construction line must fail this file.

Pure-function property test: no state, no stubs, no async — the only
subtask whose failure would indict schedule() itself rather than the
resume machinery built on top of it.
"""
from __future__ import annotations

import copy
import json


def _subtask(sid: str, *, depends_on=None, requires=None,
             provides=None) -> dict:
    """A well-formed subtask, overridable per-call."""
    return {
        "id": sid,
        "title": f"do {sid}",
        "depends_on": list(depends_on or []),
        "requires": list(requires or []),
        "provides": list(provides or []),
        "success_criteria_seed": "the thing is done",
        "size": "small",
    }


def _plan(domain: str, *subtasks: dict) -> dict:
    return {
        "domain": domain,
        "status": "ready",
        "subtasks": list(subtasks),
    }


def _requires(tag: str) -> list[dict]:
    return [{"tag": tag, "extent": "in_plan"}]


def _multi_domain_plans() -> list[dict]:
    """A fixture spanning several domains with BOTH intra-domain
    `depends_on` edges and cross-domain `requires`/`provides` edges
    (resolved through `_build_predecessor_graph`) — a fixture with only
    `depends_on` would not exercise the tag-channel ordering where
    iteration-order sensitivity would most plausibly creep in.

    Shape (acyclic, ≥1 subtask — schedule() die()s on cycles/empty):
      feat-001                              (no deps)          -> wave 0
      feat-002 depends_on feat-001          (intra-domain)      -> wave 1
      bug-001  requires "feat-ready"        (cross-domain tag,  -> wave 1
               provided by feat-001)         same wave as feat-002
      bug-002  depends_on bug-001           (intra-domain)      -> wave 2
      test-001 requires "bug-fixed"         (cross-domain tag,  -> wave 2
               provided by bug-002)          same wave as bug-002
      test-002 (no deps)                                        -> wave 0
      refactor-001 requires "feat-ready" AND "bug-fixed"        -> wave 2
               (two cross-domain providers, different waves)
    """
    feat_001 = _subtask("feat-001", provides=["feat-ready"])
    feat_002 = _subtask("feat-002", depends_on=["feat-001"])
    bug_001 = _subtask("bug-001", requires=_requires("feat-ready"))
    bug_002 = _subtask(
        "bug-002", depends_on=["bug-001"], provides=["bug-fixed"])
    test_001 = _subtask("test-001", requires=_requires("bug-fixed"))
    test_002 = _subtask("test-002")
    refactor_001 = _subtask(
        "refactor-001",
        requires=_requires("feat-ready") + _requires("bug-fixed"),
    )
    return [
        _plan("feature-implementation", feat_001, feat_002),
        _plan("bug-fixing", bug_001, bug_002),
        _plan("testing", test_001, test_002),
        _plan("refactoring", refactor_001),
    ]


def _reverse_subtask_order(plans: list[dict]) -> list[dict]:
    """Deep-copy `plans` with each plan's `subtasks` list reversed —
    exercises input-order independence within a plan, distinct from
    reversing the order of the plans list itself."""
    out = copy.deepcopy(plans)
    for plan in out:
        plan["subtasks"] = list(reversed(plan["subtasks"]))
    return out


def test_schedule_deterministic_across_json_roundtrip_and_reordering(leerie):
    plans = _multi_domain_plans()

    subtasks_fresh, waves_fresh = leerie.schedule(copy.deepcopy(plans))

    # A JSON round-trip (exactly what a state.json checkpoint reload
    # does) must reproduce the identical schedule.
    roundtripped = json.loads(json.dumps(plans))
    subtasks_rt, waves_rt = leerie.schedule(roundtripped)

    # Reversing the order of the plans list itself.
    reversed_plans = list(reversed(copy.deepcopy(plans)))
    subtasks_rev, waves_rev = leerie.schedule(reversed_plans)

    # Reversing each plan's subtasks list.
    reversed_subtasks = _reverse_subtask_order(plans)
    subtasks_rev_st, waves_rev_st = leerie.schedule(reversed_subtasks)

    # Reversing both plan order AND each plan's subtask order.
    reversed_both = _reverse_subtask_order(list(reversed(
        copy.deepcopy(plans))))
    subtasks_rev_both, waves_rev_both = leerie.schedule(reversed_both)

    assert waves_fresh == waves_rt, (
        "schedule() output changed across a JSON round-trip — a "
        "checkpointed-then-reloaded plans list would NOT rehydrate to "
        "the same wave partition as the fresh run")
    assert waves_fresh == waves_rev, (
        "schedule() output is sensitive to plan input order")
    assert waves_fresh == waves_rev_st, (
        "schedule() output is sensitive to a plan's subtasks list order")
    assert waves_fresh == waves_rev_both, (
        "schedule() output is sensitive to combined plan/subtask reordering")

    # Same merged subtask id set across every variant.
    ids_fresh = set(subtasks_fresh)
    assert ids_fresh == set(subtasks_rt)
    assert ids_fresh == set(subtasks_rev)
    assert ids_fresh == set(subtasks_rev_st)
    assert ids_fresh == set(subtasks_rev_both)

    # Sanity: the fixture actually exercises multiple waves and both
    # edge channels, so this test would catch a real ordering
    # regression rather than vacuously passing on a single-wave graph.
    assert len(waves_fresh) >= 3
    assert waves_fresh[0] == sorted(waves_fresh[0])
    all_ids = {sid for wave in waves_fresh for sid in wave}
    assert all_ids == {
        "feat-001", "feat-002", "bug-001", "bug-002",
        "test-001", "test-002", "refactor-001",
    }
    # Cross-domain requires edges actually constrained the schedule:
    # bug-001 (requires feat-ready) must not precede feat-001's wave.
    wave_of = {sid: i for i, wave in enumerate(waves_fresh) for sid in wave}
    assert wave_of["bug-001"] > wave_of["feat-001"]
    assert wave_of["test-001"] > wave_of["bug-002"]
    assert wave_of["refactor-001"] > wave_of["feat-001"]
    assert wave_of["refactor-001"] > wave_of["bug-002"]


def test_each_wave_is_sorted(leerie):
    """Every wave is lexicographically sorted by id — the exact
    invariant the `sorted(...)` call at the wave-construction line
    provides and that makes the round-trip determinism above possible.
    Removing that `sorted(...)` still produces a valid topological
    order (Kahn's algorithm doesn't require sorted waves to be
    correct), so this must be checked directly rather than inferred
    from the round-trip test alone."""
    plans = _multi_domain_plans()
    _subtasks, waves = leerie.schedule(plans)
    for wave in waves:
        assert wave == sorted(wave), (
            f"wave {wave!r} is not lexicographically sorted — "
            "schedule() must sort each wave by subtask id for the "
            "output to be independent of dict/set iteration order")
