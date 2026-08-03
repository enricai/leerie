"""Finding severity: only gating findings re-invoke (DESIGN §CRITIC).

A check function's findings are not all the same kind of thing. Some name a
defect that makes the output unusable (a dangling dependency, a cycle, a
subtask with no success criteria). Others are advice about a judgement call
that is frequently correct as it stands — `INTRA_DOMAIN_OVERLAP`'s own text is
*"consider merging or splitting"*.

Treating both as retry triggers cost two measured things on the 2026-08-03
runs:

- Advice cannot converge, so it burns the round cap. `INTRA_DOMAIN_OVERLAP`
  went 43 → 12 → 6 across every planner in both runs and never reached zero.
- Per-subtask advice made issue count scale ~1:1 with subtask count, turning
  multi-sample selection's primary key into a plan-size penalty.

The severity default is **gating**, so an unclassified finding keeps today's
behaviour and an incomplete classification cannot silently disarm a real gate.
`test_unknown_labels_default_to_gating` is the guard on that, and it is the
single most important test in this file.
"""
from __future__ import annotations

import asyncio

import pytest


def _run(coro):
    """Run an async coroutine synchronously (mirrors
    `tests/test_checked_loop.py` — this repo does not use pytest-asyncio)."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ----- classification -------------------------------------------------------

@pytest.mark.parametrize("label", sorted([
    "INTRA_DOMAIN_OVERLAP", "PHANTOM_PATH", "OVERSIZED",
    "MANY_CATEGORIES", "SAME_WORK_RISK", "TEST_OWNERSHIP_RISK",
]))
def test_advisory_labels_are_advisory(leerie, label):
    assert leerie._issue_is_advisory(f"{label}: something — detail") is True


@pytest.mark.parametrize("label", sorted([
    # Break scheduling or make a subtask unusable.
    "DANGLING_DEP", "INTRA_DOMAIN_CYCLE", "EMPTY_CRITERIA",
    # Safety.
    "PROTECTED_PATH",
    # Self-report contradictions and coverage floors.
    "MIGRATION_TARGETS_MISSING", "UNCOVERED_MIGRATION_SURFACE",
    "PRESCRIBED_CMD_UNRUN", "REQUIRED_ITEM_UNCOVERED",
    # Other workers' correctness findings.
    "BAD_PREFIX", "RENAME_TO_NOWHERE", "SELF_DEP",
    "DROP_BREAKS_GRAPH", "PHANTOM_ARTIFACT", "DUPLICATE_PROVIDER",
    "EMPTY_RECIPE", "MISSING_WORKDIR", "WRONG_PM",
]))
def test_correctness_labels_remain_gating(leerie, label):
    assert leerie._issue_is_advisory(f"{label}: something — detail") is False


def test_unknown_labels_default_to_gating(leerie):
    """THE load-bearing test. The advisory set is an allowlist precisely so a
    finding nobody classified keeps today's behaviour. If this ever inverts,
    every future check silently stops gating until someone remembers to
    classify it — the opposite of the intended failure mode."""
    for issue in ("BRAND_NEW_CHECK: x — y",
                  "SOMETHING_UNCLASSIFIED: x",
                  "lowercase noise",
                  "no label at all"):
        assert leerie._issue_is_advisory(issue) is False


def test_subtype_parenthetical_is_stripped(leerie):
    """Issue strings may carry `LABEL (subtype):`, per `_issue_signature`'s
    documented shape. The severity lookup must see the bare label."""
    assert leerie._issue_is_advisory(
        "SAME_WORK_RISK (variant): a and b — detail") is True
    assert leerie._issue_is_advisory(
        "DANGLING_DEP (variant): a — detail") is False


def test_non_strings_are_gating_not_crashes(leerie):
    for bad in (None, 42, [], {}):
        assert leerie._issue_is_advisory(bad) is False


# ----- partition ------------------------------------------------------------

def test_partition_splits_and_preserves_order(leerie):
    gating, advisory = leerie._partition_issues_by_severity([
        "PHANTOM_PATH: a — x",
        "DANGLING_DEP: b — y",
        "INTRA_DOMAIN_OVERLAP: c — z",
        "EMPTY_CRITERIA: d — w",
    ])
    assert gating == ["DANGLING_DEP: b — y", "EMPTY_CRITERIA: d — w"]
    assert advisory == ["PHANTOM_PATH: a — x", "INTRA_DOMAIN_OVERLAP: c — z"]


def test_partition_of_empty_is_two_empties(leerie):
    assert leerie._partition_issues_by_severity([]) == ([], [])


def test_partition_loses_nothing(leerie):
    issues = ["PHANTOM_PATH: a", "DANGLING_DEP: b", "OVERSIZED: c"]
    gating, advisory = leerie._partition_issues_by_severity(issues)
    assert sorted(gating + advisory) == sorted(issues)


# ----- _run_checked_loop integration ----------------------------------------

def test_advisory_only_result_is_accepted_without_a_retry(leerie):
    """The convergence fix. Advice that cannot be satisfied must not consume
    the round budget."""
    calls = []

    async def invoke(*a, **k):
        calls.append(1)
        return {"ok": True}

    async def feedback(_fb):
        raise AssertionError("advisory findings must not drive a re-invoke")

    res, warnings = _run(leerie._run_checked_loop(
        invoke=invoke,
        check=lambda _r: ["INTRA_DOMAIN_OVERLAP: 'a.ts' touched by [x, y] "
                          "— consider merging or splitting"],
        name="planner", max_rounds=3, make_feedback_prompt=feedback,
    ))
    assert res == {"ok": True}
    assert len(calls) == 1, "advisory findings cost a retry round"
    assert any("INTRA_DOMAIN_OVERLAP" in w for w in warnings), (
        "advisory findings must still be surfaced, not silently dropped")


def test_falsifier_advisory_would_retry_without_the_split(leerie):
    """Anti-vacuity control: the same loop with a GATING label does re-invoke,
    proving the test above measures the severity split rather than some
    unrelated short-circuit."""
    calls, fbs = [], []

    async def invoke(*a, **k):
        calls.append(1)
        return {"ok": True}

    async def feedback(fb):
        fbs.append(fb)

    # A DIFFERENT gating issue each round: an identical one every round is a
    # true repeat and the oscillation guard correctly stops early, which would
    # measure that guard rather than the severity split.
    seq = iter([["DANGLING_DEP: x1 — nope"],
                ["DANGLING_DEP: x2 — nope"],
                ["DANGLING_DEP: x3 — nope"]])
    _run(leerie._run_checked_loop(
        invoke=invoke, check=lambda _r: next(seq),
        name="planner", max_rounds=3, make_feedback_prompt=feedback,
    ))
    assert len(calls) == 3, "a gating finding must exhaust the rounds"
    assert fbs, "a gating finding must produce feedback"


def test_gating_finding_still_retries_when_mixed_with_advisory(leerie):
    """A gating finding is not masked by advisory siblings."""
    calls, fbs = [], []

    async def invoke(*a, **k):
        calls.append(1)
        return {"ok": True}

    async def feedback(fb):
        fbs.append(fb)

    _run(leerie._run_checked_loop(
        invoke=invoke,
        check=lambda _r: ["PHANTOM_PATH: a — x",
                          "DANGLING_DEP: b — y"],
        name="planner", max_rounds=2, make_feedback_prompt=feedback,
    ))
    assert len(calls) == 2


def test_feedback_never_contains_advisory_findings(leerie):
    """A worker asked to fix advice it cannot satisfy is the non-convergence
    mechanism itself. Feedback must carry only gating findings."""
    fbs = []

    async def invoke(*a, **k):
        return {"ok": True}

    async def feedback(fb):
        fbs.append(fb)

    _run(leerie._run_checked_loop(
        invoke=invoke,
        check=lambda _r: ["INTRA_DOMAIN_OVERLAP: a — x",
                          "DANGLING_DEP: b — y"],
        name="planner", max_rounds=2, make_feedback_prompt=feedback,
    ))
    assert fbs
    joined = "\n".join(fbs)
    assert "DANGLING_DEP" in joined
    assert "INTRA_DOMAIN_OVERLAP" not in joined


def test_advisory_findings_do_not_trip_the_oscillation_guard(leerie):
    """The guard aborts when a round's issue set exactly repeats an earlier
    one. Advisory findings are stable across rounds by nature, so counting
    them would abort a genuinely-converging retry early."""
    calls = []

    async def invoke(*a, **k):
        calls.append(1)
        return {"ok": True}

    async def feedback(_fb):
        pass

    # Same advisory finding every round; the gating one differs per round.
    seq = iter([
        ["INTRA_DOMAIN_OVERLAP: a — x", "DANGLING_DEP: b1 — y"],
        ["INTRA_DOMAIN_OVERLAP: a — x", "DANGLING_DEP: b2 — y"],
        ["INTRA_DOMAIN_OVERLAP: a — x", "DANGLING_DEP: b3 — y"],
    ])
    _run(leerie._run_checked_loop(
        invoke=invoke, check=lambda _r: next(seq),
        name="planner", max_rounds=3, make_feedback_prompt=feedback,
    ))
    assert len(calls) == 3, "the loop aborted early on stable advisory noise"


def test_clean_result_still_breaks_immediately(leerie):
    """Regression: no findings at all is unchanged."""
    calls = []

    async def invoke(*a, **k):
        calls.append(1)
        return {"ok": True}

    res, warnings = _run(leerie._run_checked_loop(
        invoke=invoke, check=lambda _r: [], name="x", max_rounds=3,
        make_feedback_prompt=lambda _fb: None,
    ))
    assert len(calls) == 1 and res == {"ok": True} and warnings == []


# ----- allowlist discipline -------------------------------------------------

def test_every_advisory_label_is_actually_emitted_somewhere(leerie):
    """A label in the allowlist that no check emits is dead configuration
    that reads as an active decision — and worse, would silently do nothing
    if a check later started emitting a DIFFERENT spelling of it."""
    import inspect
    src = inspect.getsource(leerie)
    for label in leerie._ADVISORY_ISSUE_LABELS:
        assert f'"{label}:' in src or f"{label}: " in src, (
            f"{label} is allowlisted as advisory but no check emits it")


def test_advisory_set_is_a_frozenset(leerie):
    """Mutable module-level config could be edited at runtime by a caller and
    change gating behaviour globally."""
    assert isinstance(leerie._ADVISORY_ISSUE_LABELS, frozenset)
