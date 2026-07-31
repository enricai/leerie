"""Tests for the mechanical-feedback retry loop infrastructure:
``_format_check_feedback`` and ``_run_checked_loop`` (CRITIC pattern —
DESIGN §8 + §12).

(``_confidence_axes_clear`` was removed when the implementer's self-score
gate was retired — DESIGN §8 *Independent adversarial verification*: the
conformer's independent ``solution_defects`` axis is now the authoritative
completeness gate, so no worker gates on its own confidence number.)
"""
from __future__ import annotations

import asyncio

import pytest


# --- _format_check_feedback ---------------------------------------------- #

def test_format_feedback_structure(leerie):
    fb = leerie._format_check_feedback(
        ["PHANTOM_PATH: foo.py not found", "DANGLING_DEP: bar"], 0, 3)
    assert "round 1 of 3" in fb
    assert "2 issue(s)" in fb
    assert "PHANTOM_PATH" in fb
    assert "DANGLING_DEP" in fb
    assert "mechanically-derived" in fb


def test_format_feedback_single_issue(leerie):
    fb = leerie._format_check_feedback(["OVERSIZED: x"], 1, 2)
    assert "round 2 of 2" in fb
    assert "1 issue(s)" in fb


# --- _run_checked_loop --------------------------------------------------- #

@pytest.fixture()
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


def _run(coro, loop=None):
    """Run an async coroutine synchronously."""
    if loop is None:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()
    return loop.run_until_complete(coro)


async def _noop_feedback(fb):
    """Stub `make_feedback_prompt` for tests modeling a feedback-driven
    caller (classifier, reconciler, provision, overlap judge, integrator)
    that mechanically re-drives on a found issue — as opposed to a
    detect-and-die, single-pass caller (`make_feedback_prompt=None`),
    which stops on the first round with issues. Its presence, not its
    body, is what the loop inspects; real callers close over mutable
    prompt state instead of no-op'ing."""


def test_loop_clean_on_first_round(leerie):
    calls = []

    async def invoke():
        calls.append(1)
        return {"status": "ready"}

    result, warnings = _run(leerie._run_checked_loop(
        invoke=invoke, check=lambda r: [], name="test", max_rounds=3))
    assert result == {"status": "ready"}
    assert warnings == []
    assert len(calls) == 1


def test_loop_retries_then_clears(leerie):
    """A round-to-round DIFFERENT issue each retry (genuine narrowing, not
    a repeat) converges normally without tripping the oscillation guard."""
    attempt = [0]

    async def invoke():
        attempt[0] += 1
        if attempt[0] < 3:
            return {"bad": True, "n": attempt[0]}
        return {"good": True}

    def check(r):
        if r.get("bad"):
            return [f"ISSUE: still bad, attempt {r['n']}"]
        return []

    result, warnings = _run(leerie._run_checked_loop(
        invoke=invoke, check=check, name="test", max_rounds=5,
        make_feedback_prompt=_noop_feedback))
    assert result == {"good": True}
    assert len(warnings) == 2
    assert attempt[0] == 3


def test_loop_exhausts_rounds(leerie):
    """A round-to-round DIFFERENT issue each time (no repeat) exhausts
    max_rounds normally — the oscillation guard only fires on a repeat."""
    calls = [0]

    async def invoke():
        calls[0] += 1
        return {"n": calls[0]}

    def check(r):
        # A distinct issue every round — never repeats, so the oscillation
        # guard must not intervene; the loop runs the full budget.
        return [f"ISSUE: bad round {r['n']}"]

    result, warnings = _run(leerie._run_checked_loop(
        invoke=invoke,
        check=check,
        name="test",
        max_rounds=2,
        make_feedback_prompt=_noop_feedback,
    ))
    assert result == {"n": 2}
    assert len(warnings) == 2
    assert calls[0] == 2


def test_loop_crash_breaks(leerie):
    """A non-WorkerError crash is a bug in leerie itself, not a flaky worker:
    retrying re-runs the same defect, so the loop still abandons immediately."""
    async def invoke():
        raise RuntimeError("boom")

    result, warnings = _run(leerie._run_checked_loop(
        invoke=invoke, check=lambda r: [], name="test", max_rounds=3))
    assert result is None
    assert len(warnings) == 1
    assert "crashed" in warnings[0]


# --- WorkerError crash retry (DESIGN §12 *salvage if there is something to
# salvage*) -------------------------------------------------------------------
# A PID-exhausted / OOM-killed worker is an infrastructure failure, not a
# verdict about the work. `_read_stream`'s own PID-cap message promises "a
# fresh worker retries with a clean PID table" — true for implementers, and
# false for every `_run_checked_loop` caller until this retry existed. Run
# 879defae's wave-2 integrator died exactly here.

def test_loop_worker_error_retries_then_succeeds(leerie):
    """A WorkerError consumes a round and re-invokes; a later clean round wins."""
    calls = []

    async def invoke():
        calls.append(1)
        if len(calls) == 1:
            raise leerie.WorkerError("worker x exhausted its PID cgroup")
        return {"ok": True}

    result, warnings = _run(leerie._run_checked_loop(
        invoke=invoke, check=lambda r: [], name="test", max_rounds=3))
    assert result == {"ok": True}, (
        "a WorkerError must not abandon the loop — the next round is a fresh "
        "claude -p session with a clean PID table")
    assert len(calls) == 2, f"expected a retry after the crash; got {calls}"
    assert any("crashed" in w for w in warnings), (
        "the crash must still be surfaced as a warning, not swallowed")


def test_loop_worker_error_every_round_returns_none(leerie):
    """When every round crashes, the loop still returns None (callers'
    `is None` escalation path is unchanged) and bounds itself by max_rounds."""
    calls = []

    async def invoke():
        calls.append(1)
        raise leerie.WorkerError("worker x exhausted its PID cgroup")

    result, warnings = _run(leerie._run_checked_loop(
        invoke=invoke, check=lambda r: [], name="test", max_rounds=3))
    assert result is None
    assert len(calls) == 3, (
        f"must retry exactly max_rounds times, no more: {len(calls)}")
    assert len(warnings) == 3


def test_loop_worker_error_does_not_leak_stale_result(leerie):
    """A crash after a successful-but-dirty round must not return that stale
    result as if it were the crashed round's output. Needs
    make_feedback_prompt (a feedback-driven caller) to reach round 1 at
    all — a detect-and-die (no-feedback) caller now stops at round 0's
    dirty finding and never gets a chance to crash on a later round; see
    test_loop_no_feedback_worker_error_then_issue_found_stops for that
    path's own crash-then-issue-found coverage."""
    calls = []

    async def invoke():
        calls.append(1)
        if len(calls) == 1:
            return {"stale": True}          # dirty: check() flags it
        raise leerie.WorkerError("boom")    # then crash for every later round

    result, warnings = _run(leerie._run_checked_loop(
        invoke=invoke,
        check=lambda r: ["ISSUE: dirty"],
        name="test",
        max_rounds=3,
        make_feedback_prompt=_noop_feedback,
    ))
    assert result is None, (
        "the crashed round must clear last_res; returning {'stale': True} "
        "would let a caller act on output no round actually produced")


def test_loop_none_result_breaks(leerie):
    async def invoke():
        return None

    result, warnings = _run(leerie._run_checked_loop(
        invoke=invoke, check=lambda r: [], name="test", max_rounds=3))
    assert result is None
    assert len(warnings) == 1
    assert "None" in warnings[0]


# --- Detect-and-die, single pass (make_feedback_prompt=None) -------------- #
# `phase_wiring_gate`, `phase_provision_gate`, and the integration judge all
# call this loop with no `make_feedback_prompt`: nothing re-drives the input
# between rounds, so a further round can only ever LOSE a found defect (on a
# non-deterministic re-roll that happens not to reproduce it), never gain
# real information. A round with issues must be final.

def test_loop_no_feedback_stops_on_first_issue_round(leerie):
    """With make_feedback_prompt=None (the default — every real detect-and-
    die caller), a round that finds issues must stop the loop immediately,
    not retry up to max_rounds."""
    calls = [0]

    async def invoke():
        calls[0] += 1
        return {"n": calls[0]}

    result, warnings = _run(leerie._run_checked_loop(
        invoke=invoke,
        check=lambda r: ["WIRING_DEFECT (missing_requires) sid/tag: reason"],
        name="test",
        max_rounds=3,
    ))
    assert calls[0] == 1, (
        f"detect-and-die must stop after the first issue-bearing round, "
        f"not retry an unchanged payload: {calls[0]} invocations")
    assert any("missing_requires" in w for w in warnings)


def test_loop_no_feedback_does_not_swallow_a_defect_on_re_roll(leerie):
    """Regression for the silent-un-catch bug: a defect found on round 0
    must not be discarded because a later round's non-deterministic judge
    session happens not to reproduce it. Simulates re-roll variance — the
    payload never changes, but the judge's output does — via odd/even
    invocation counts."""
    calls = [0]

    async def invoke():
        calls[0] += 1
        return {"n": calls[0]}

    def check(r):
        # Odd rounds find the defect, even rounds don't — modeling a
        # non-deterministic judge session re-attacking unchanged input.
        # The bug this test pins: the OLD loop would continue past round 0
        # (finding the defect), hit round 1 (clean by luck), and report
        # clean — silently dropping a real defect.
        if r["n"] % 2 == 1:
            return ["WIRING_DEFECT (missing_requires) sid/tag: reason"]
        return []

    result, warnings = _run(leerie._run_checked_loop(
        invoke=invoke, check=check, name="test", max_rounds=3))
    assert calls[0] == 1, (
        "must stop at round 0's finding, never reach round 1's lucky-clean "
        f"re-roll: {calls[0]} invocations")
    assert any("missing_requires" in w for w in warnings)


def test_loop_no_feedback_worker_error_then_issue_found_stops(leerie):
    """The WorkerError infra-crash retry (round 0 crashes) is unaffected by
    the detect-and-die fix: round 1 runs fresh, finds an issue, and THAT
    round is final — it must not retry further looking for a clean pass."""
    calls = [0]

    async def invoke():
        calls[0] += 1
        if calls[0] == 1:
            raise leerie.WorkerError("worker x exhausted its PID cgroup")
        return {"n": calls[0]}

    result, warnings = _run(leerie._run_checked_loop(
        invoke=invoke,
        check=lambda r: ["RECIPE_FAILURE (bad_recipe) cmd: reason"],
        name="test",
        max_rounds=3,
    ))
    assert calls[0] == 2, (
        f"round 0 crash must retry once (infra recovery), then round 1's "
        f"finding must be final: {calls[0]} invocations")
    assert any("crashed" in w for w in warnings)
    assert any("bad_recipe" in w for w in warnings)


def test_loop_feedback_callback_called(leerie):
    """A distinct issue every round (never repeats) must not trip the
    oscillation guard, so feedback fires on every non-final round."""
    feedback_received = []
    calls = [0]

    async def invoke():
        calls[0] += 1
        return {"x": calls[0]}

    async def on_feedback(fb):
        feedback_received.append(fb)

    result, warnings = _run(leerie._run_checked_loop(
        invoke=invoke,
        check=lambda r: [f"ISSUE: x{r['x']}"],
        name="test",
        max_rounds=3,
        make_feedback_prompt=on_feedback,
    ))
    assert len(feedback_received) == 2
    assert "ISSUE: x1" in feedback_received[0]


def test_loop_feedback_not_called_on_last_round(leerie):
    feedback_received = []
    calls = [0]

    async def invoke():
        calls[0] += 1
        return {"x": calls[0]}

    async def on_feedback(fb):
        feedback_received.append(fb)

    _run(leerie._run_checked_loop(
        invoke=invoke,
        check=lambda r: [f"ISSUE: x{r['x']}"],
        name="test",
        max_rounds=2,
        make_feedback_prompt=on_feedback,
    ))
    assert len(feedback_received) == 1


# --- Oscillation guard ---------------------------------------------------- #
# Root-cause fix for the classification-gate thrash incident: neither this
# loop nor any known caller's make_feedback_prompt accumulates feedback
# across rounds, so a fix for one round's complaint can silently reintroduce
# an earlier round's complaint, cycling rather than converging until the
# caller's own exhaustion die() fires. The guard tracks each round's issue
# SIGNATURE (the `LABEL: subject` prefix before an em dash, not the full
# string — real callers' issue text carries free-form LLM-regenerated
# evidence prose after the dash that differs between rounds even for the
# identical underlying defect).

def test_issue_signature_strips_evidence_after_dash(leerie):
    assert leerie._issue_signature(
        "MISCATEGORIZATION (missing_category): testing — some evidence A"
    ) == "MISCATEGORIZATION (missing_category): testing"
    assert leerie._issue_signature(
        "MISCATEGORIZATION (missing_category): testing — totally different "
        "evidence B, much longer"
    ) == "MISCATEGORIZATION (missing_category): testing"


def test_issue_signature_no_dash_is_whole_string(leerie):
    assert leerie._issue_signature("PLAIN_ISSUE") == "PLAIN_ISSUE"


def test_loop_breaks_early_on_exact_repeat(leerie):
    """Round 1 reproduces round 0's exact issue text — the simplest
    oscillation case. The loop must not burn round 2."""
    calls = [0]

    async def invoke():
        calls[0] += 1
        return {"n": calls[0]}

    result, warnings = _run(leerie._run_checked_loop(
        invoke=invoke,
        check=lambda r: ["ISSUE: same problem every time"],
        name="test",
        max_rounds=5,
        make_feedback_prompt=_noop_feedback,
    ))
    assert calls[0] == 2, (
        f"must stop at round 1 (the repeat), never reach round 2+: "
        f"{calls[0]} invocations")
    assert any("repeats an earlier round" in w for w in warnings)


def test_loop_breaks_on_two_round_cycle(leerie):
    """Reproduces the exact funeralworks incident shape: round 0 flags A,
    round 1's fix drops A but introduces B, round 2's fix re-introduces A
    (with different LLM-regenerated evidence prose than round 0's A, as a
    real re-classify call would produce) — a 2-cycle that never converges.
    Must stop at round 2 rather than burning the full budget."""
    calls = [0]

    async def invoke():
        calls[0] += 1
        return {"n": calls[0]}

    def check(r):
        n = r["n"]
        if n == 1:
            return ["MISCATEGORIZATION (missing_category): testing — "
                     "5+ new test files required per the Instructions "
                     "section"]
        if n == 2:
            return ["MISCATEGORIZATION (missing_category): documentation "
                     "— docs/SEED.md is now factually wrong"]
        # round 3: testing flagged again, different evidence text than
        # round 1's — this is the real-world shape (LLM-regenerated prose)
        return ["MISCATEGORIZATION (missing_category): testing — "
                 "per-endpoint regression pins across 6 named endpoints"]

    result, warnings = _run(leerie._run_checked_loop(
        invoke=invoke, check=check, name="test", max_rounds=5,
        make_feedback_prompt=_noop_feedback))
    assert calls[0] == 3, (
        f"must stop at round 2 (the repeat of round 0's signature), "
        f"never reach round 3+: {calls[0]} invocations")
    assert any("repeats an earlier round" in w for w in warnings)


def test_loop_does_not_falsely_trigger_on_monotonic_growth(leerie):
    """Validated against the real matching successful transcript: round 0
    flags {A, B} missing; the fix narrows too far to {}; round 1 flags a
    DIFFERENT superset resolution that never repeats an earlier round's
    signature set. Legitimate convergence must never trip the guard."""
    calls = [0]

    async def invoke():
        calls[0] += 1
        return {"n": calls[0]}

    def check(r):
        if r["n"] == 1:
            return [
                "MISCATEGORIZATION (missing_category): testing — evidence",
                "MISCATEGORIZATION (missing_category): "
                "feature-implementation — evidence",
            ]
        return []  # round 2: resolved via file-ownership split, clean

    result, warnings = _run(leerie._run_checked_loop(
        invoke=invoke, check=check, name="test", max_rounds=3,
        make_feedback_prompt=_noop_feedback))
    assert calls[0] == 2, "must run both rounds — no oscillation to detect"
    assert not any("repeats an earlier round" in w for w in warnings)


def test_loop_does_not_falsely_trigger_on_shrinking_issue_set(leerie):
    """A round whose issue set is a genuine subset of nothing seen before
    (fewer, different-signature issues than any prior round) must not
    trigger — the guard only fires when a round's issues exactly repeat
    (not merely overlap with) some earlier round's issues."""
    calls = [0]

    async def invoke():
        calls[0] += 1
        return {"n": calls[0]}

    def check(r):
        if r["n"] == 1:
            return ["ISSUE_A: one", "ISSUE_B: two", "ISSUE_C: three"]
        if r["n"] == 2:
            return ["ISSUE_D: four"]  # different issue, not a subset of round 1
        return []

    result, warnings = _run(leerie._run_checked_loop(
        invoke=invoke, check=check, name="test", max_rounds=3,
        make_feedback_prompt=_noop_feedback))
    assert calls[0] == 3
    assert not any("repeats an earlier round" in w for w in warnings)


def test_loop_does_not_falsely_trigger_on_genuine_partial_progress(leerie):
    """Regression pin for a real production incident (barnacle
    classification-gate exhaustion, 2026-07-31): round 0 flags {A, B};
    round 1's fix resolves A but B is still open, so round 1's issue set
    is {B} — a non-empty PROPER SUBSET of round 0's {A, B}, not a repeat.

    Before the fix, the guard's `issue_set <= seen` check treated any
    subset relationship as a repeat and aborted here, even though this is
    ordinary incremental convergence (fewer open issues, not the same
    issues coming back). The guard must only abort on an EXACT repeat of
    a previously seen issue-signature set, never on a proper subset — a
    proper subset is strictly less information than the round it's a
    subset of, which is forward progress, not oscillation.

    This is the case `test_loop_does_not_falsely_trigger_on_shrinking_issue_set`
    above is docstring-named for but does not actually test: that test's
    round-2 issue (`ISSUE_D`) is DISJOINT from round 1's issues, not a
    proper subset of them, so it never exercised the `<=` comparison this
    bug lived in. Verified: reverting the `issue_set in seen_issue_sets`
    fix back to `any(issue_set <= seen for seen in seen_issue_sets)` fails
    this test (the loop wrongly stops at round 1, calls[0] == 2)."""
    calls = [0]

    async def invoke():
        calls[0] += 1
        return {"n": calls[0]}

    def check(r):
        if r["n"] == 1:
            return ["ISSUE_A: one — evidence", "ISSUE_B: two — evidence"]
        if r["n"] == 2:
            # A genuinely fixed; B still open. {B} is a proper subset of
            # {A, B} — must NOT be treated as a repeat.
            return ["ISSUE_B: two — different evidence text this round"]
        return []  # round 3: B also fixed, clean

    result, warnings = _run(leerie._run_checked_loop(
        invoke=invoke, check=check, name="test", max_rounds=5,
        make_feedback_prompt=_noop_feedback))
    assert calls[0] == 3, (
        "must continue past round 1's partial fix and reach the clean "
        f"round 2: {calls[0]} invocations")
    assert not any("repeats an earlier round" in w for w in warnings)


def test_loop_still_catches_exact_repeat_of_a_shrunk_set(leerie):
    """Companion to the partial-progress test above: once a round's issue
    set has genuinely shrunk to {B}, a LATER round reproducing that exact
    {B} again (not a further subset, the identical set) is a true
    stall/repeat and must still abort — the fix narrows the guard to
    exact-match, it does not disable it."""
    calls = [0]

    async def invoke():
        calls[0] += 1
        return {"n": calls[0]}

    def check(r):
        if r["n"] == 1:
            return ["ISSUE_A: one — ev1", "ISSUE_B: two — ev2"]
        if r["n"] == 2:
            return ["ISSUE_B: two — ev3"]  # partial progress, allowed through
        # round 3: identical signature set to round 2 — a true repeat now.
        return ["ISSUE_B: two — ev4-different-prose"]

    result, warnings = _run(leerie._run_checked_loop(
        invoke=invoke, check=check, name="test", max_rounds=6,
        make_feedback_prompt=_noop_feedback))
    assert calls[0] == 3, (
        f"must stop at round 2 (exact repeat of round 1's shrunk {{B}} "
        f"set): {calls[0]} invocations")
    assert any("repeats an earlier round" in w for w in warnings)


def test_loop_oscillation_guard_respects_worker_error_rounds(leerie):
    """A WorkerError round records no issue signature (no `check()` call
    happened) — it must not corrupt the seen-set bookkeeping for
    subsequent real rounds."""
    calls = [0]

    async def invoke():
        calls[0] += 1
        if calls[0] == 2:
            raise leerie.WorkerError("transient crash")
        return {"n": calls[0]}

    def check(r):
        return [f"ISSUE: round {r['n']}"]  # always distinct

    result, warnings = _run(leerie._run_checked_loop(
        invoke=invoke, check=check, name="test", max_rounds=4,
        make_feedback_prompt=_noop_feedback))
    assert calls[0] == 4
    assert not any("repeats an earlier round" in w for w in warnings)
