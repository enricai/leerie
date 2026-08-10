"""Wave-entry concurrency degrade (`_degrade_max_parallel_for_wave`).

Merged design: main's per-worker **ceiling** and cache-aware headroom signal,
with the self-run branch's **degrade-instead-of-block** response. The branch's
insight was that blocking a spawn for up to 10 minutes is the wrong answer to
"the slice is busy" — shrinking the wave's own concurrency is. Main's
contribution is the signal: `slice_max - unreclaimable`, which excludes
reclaimable page cache.

The original branch version of this file pinned a divisor-based
`_slice_worker_memory_max`, which the merge dropped in favour of main's
ceiling. Rewritten against the merged function.

Note the degrade and `_await_worker_memory_admission` must read the SAME
signal, or they can disagree about whether memory is available — one
admitting while the other blocks.
"""
from __future__ import annotations

import ast
import asyncio
import inspect
import textwrap

import pytest


_SLICE_54_9_GIB = 58986594304          # the measured host
_PEAK = None                            # filled from the module in fixtures


@pytest.fixture(autouse=True)
def _reset_admissions(leerie):
    """`_active_admissions` is module-level and conftest's `leerie` fixture is
    session-scoped — see tests/test_slice_aware_memory.py."""
    leerie._active_admissions.clear()
    yield
    leerie._active_admissions.clear()


def _slice(leerie, monkeypatch, unreclaimable, slice_max=_SLICE_54_9_GIB):
    monkeypatch.setattr(leerie, "_cgroup_slice_info",
                        lambda: (slice_max, 3, unreclaimable))


def test_roomy_slice_does_not_degrade(leerie, monkeypatch):
    """The common case must be untouched — a degrade that fires when memory
    is plentiful would be the stall this whole mechanism removes, wearing a
    different hat."""
    _slice(leerie, monkeypatch, 2 * 1024**3)          # 52.9 GiB free
    assert leerie._degrade_max_parallel_for_wave(5) == 5


def test_busy_slice_degrades_to_what_fits(leerie, monkeypatch):
    """The branch's core idea: shrink concurrency rather than block a spawn.
    With 14.9 GiB free and a 6.3 GiB floor, two workers fit, not five."""
    _slice(leerie, monkeypatch, 40 * 1024**3)         # 14.9 GiB free
    got = leerie._degrade_max_parallel_for_wave(5)
    assert got == 2, f"14.9 GiB / 6.3 GiB should fit 2 workers, got {got}"


def test_never_degrades_below_one(leerie, monkeypatch):
    """A wave of zero workers makes no progress at all — strictly worse than
    an over-subscribed one, which the per-spawn gate still backstops."""
    _slice(leerie, monkeypatch, _SLICE_54_9_GIB - 1024**3)   # 1 GiB free
    assert leerie._degrade_max_parallel_for_wave(5) == 1


def test_fail_open_when_no_slice_budget(leerie, monkeypatch):
    """Containment off / no broker: nothing to size against."""
    monkeypatch.setattr(leerie, "_cgroup_slice_info", lambda: None)
    assert leerie._degrade_max_parallel_for_wave(5) == 5


def test_fail_open_when_unreclaimable_unknown(leerie, monkeypatch):
    """-1 means the broker could not read memory.stat. Unknown must not be
    read as 'full' — that would degrade every wave to 1 on a read error."""
    _slice(leerie, monkeypatch, -1)
    assert leerie._degrade_max_parallel_for_wave(5) == 5


def test_uses_the_same_signal_as_the_blocking_gate(leerie, monkeypatch):
    """The load-bearing consistency property. If the degrade sized against
    `memory.current` while the gate reads unreclaimable, a slice holding
    30 GiB of reclaimable page cache would degrade the wave to 1 while the
    gate cheerfully admitted — the two disagreeing about the same slice.

    Pinned behaviourally: page cache is invisible to both. `memory.current`
    is not even readable through `_cgroup_slice_info`, so a regression here
    means someone added a second signal."""
    # 8 GiB unreclaimable, but a slice whose memory.current would be ~40 GiB
    # once page cache is counted. Only the unreclaimable part may matter.
    _slice(leerie, monkeypatch, 8 * 1024**3)          # 46.9 GiB real headroom
    assert leerie._degrade_max_parallel_for_wave(5) == 5

    src = inspect.getsource(leerie._degrade_max_parallel_for_wave)
    assert "memory.current" not in src or "not" in src.lower()
    assert "unreclaimable" in src


def test_is_synchronous_not_a_per_spawn_gate(leerie):
    """The whole point of the branch's design: one cheap check at wave entry,
    not an await per spawn. A coroutine here would reintroduce the shape it
    replaced."""
    assert not inspect.iscoroutinefunction(
        leerie._degrade_max_parallel_for_wave)
    # Strip the docstring before scanning the body — it names
    # `_await_worker_memory_admission` on purpose, and a naive substring
    # check matches the prose describing the thing it forbids. Same trap
    # CLAUDE.md records for the zombie-reaper guard.
    tree = ast.parse(textwrap.dedent(
        inspect.getsource(leerie._degrade_max_parallel_for_wave)))
    fn = tree.body[0]
    if (fn.body and isinstance(fn.body[0], ast.Expr)
            and isinstance(fn.body[0].value, ast.Constant)
            and isinstance(fn.body[0].value.value, str)):
        fn.body = fn.body[1:]
    body = ast.unparse(fn)
    assert "await" not in body
    assert "sleep" not in body


def test_result_does_not_oscillate_when_reapplied(leerie, monkeypatch):
    """The degraded value goes to `asyncio.Semaphore` and must never be fed
    back into a later headroom computation. Applying the function to its own
    output must be a fixed point, or successive waves could ratchet down."""
    _slice(leerie, monkeypatch, 40 * 1024**3)
    once = leerie._degrade_max_parallel_for_wave(5)
    assert leerie._degrade_max_parallel_for_wave(once) == once


def test_wired_at_wave_entry(leerie):
    """Source-coupled: the function is inert unless `phase_execute` actually
    calls it and hands the result to the wave's Semaphore. An unwired fix is
    the failure mode CLAUDE.md records for the coverage gate."""
    src = inspect.getsource(leerie.phase_execute)
    assert "_degrade_max_parallel_for_wave(" in src
    assert "caps[\"max_parallel\"]" in src
    # The demand estimate must reach it, or the N14-16 correction is inert
    # at the wave-sizing stage even though the resolver computed it.
    assert "caps.get(\"worker_demand_estimate_bytes\")" in src, (
        "phase_execute must hand the per-worker demand estimate to the "
        "degrade, else a heap-declaring repo's waves are sized on the "
        "build-peak constant N14-16 showed is too small")
    i_call = src.index("_degrade_max_parallel_for_wave")
    i_sem = src.index("asyncio.Semaphore(wave_max_parallel)")
    assert i_call < i_sem, "the degrade must precede the Semaphore it sizes"


# ---- the two stages, composed --------------------------------------------
# Each stage is pinned alone above and in tests/test_slice_aware_memory.py.
# Their RELATIONSHIP was not, despite being the whole argument for having
# both — DESIGN §6 claims "stage 1 exists so stage 2 rarely acts". These
# drive the real functions against each other rather than either in
# isolation.

class TestComposesWithTheBlockingGate:

    @staticmethod
    def _run_wave(leerie, monkeypatch, unreclaimable, max_parallel=5):
        """Size a wave with the degrade, then push every worker of that wave
        through the real gate. Returns (N, blocked)."""
        monkeypatch.setattr(
            leerie, "_cgroup_slice_info",
            lambda: (_SLICE_54_9_GIB, 1, unreclaimable))
        n = leerie._degrade_max_parallel_for_wave(max_parallel)
        blocked = 0
        for _ in range(n):
            slept = []

            async def rec(s):
                slept.append(s)

            monkeypatch.setattr(asyncio, "sleep", rec)
            asyncio.run(leerie._await_worker_memory_admission(
                poll_interval_sec=1.0, max_wait_sec=3.0))
            if slept:
                blocked += 1
        return n, blocked

    @pytest.mark.parametrize("unreclaimable_gib", [2, 20, 35, 40, 46])
    def test_a_sized_wave_does_not_block_at_the_gate(
            self, leerie, monkeypatch, unreclaimable_gib):
        """THE property DESIGN claims. A wave sized to real headroom must
        pass the gate without a single worker waiting — otherwise the
        degrade is not doing its job and every worker pays the blocking
        path, which is the ~600s-per-spawn shape this design removed.

        Note the gate ALSO reserves a build peak per in-flight worker, so
        the last worker of a wave of N needs `peak * N` — exactly what the
        degrade guaranteed at entry. The two are tight by construction, and
        this is where a threshold change in either would show up."""
        n, blocked = self._run_wave(
            leerie, monkeypatch, unreclaimable_gib * 1024**3)
        assert n >= 1
        assert blocked == 0, (
            f"degrade sized the wave to {n} but {blocked} of those workers "
            f"blocked at the gate — the two stages disagree about the same "
            f"slice")

    def test_gate_still_backstops_headroom_lost_mid_wave(
            self, leerie, monkeypatch):
        """Anti-vacuity control for the test above, and the reason the gate
        is kept at all: the degrade reads headroom ONCE at wave entry and
        cannot know a sibling run will take the slice afterwards.

        Without this, the zero-blocked assertion above passes just as well
        against a gate that never blocks under any conditions."""
        roomy = {"unrec": 2 * 1024**3}
        monkeypatch.setattr(
            leerie, "_cgroup_slice_info",
            lambda: (_SLICE_54_9_GIB, 1, roomy["unrec"]))
        n = leerie._degrade_max_parallel_for_wave(5)
        assert n == 5, "a roomy slice must not degrade"

        roomy["unrec"] = 48 * 1024**3          # a sibling run arrives
        blocked = 0
        for _ in range(n):
            slept = []

            async def rec(s):
                slept.append(s)

            monkeypatch.setattr(asyncio, "sleep", rec)
            asyncio.run(leerie._await_worker_memory_admission(
                poll_interval_sec=1.0, max_wait_sec=3.0))
            if slept:
                blocked += 1
        assert blocked > 0, (
            "the gate must catch headroom the wave-entry degrade could not "
            "have known about — otherwise it is dead weight")

    def test_the_two_read_the_same_threshold(self, leerie):
        """Both stages must size on the SAME per-worker demand figure. If
        one drifted they would disagree about whether the same slice has
        room — the degrade sizing a wave the gate then blocks, or worse,
        admitting one it should not have.

        The demand figure is now a *parameter* threaded from
        `caps["worker_demand_estimate_bytes"]` rather than a hard-coded
        constant (N14-16: the build-peak constant understates a repo that
        declares its own Node heap). The invariant is unchanged — what
        moved is where the number comes from — so this pins two things:
        both fall back to the same constant when nothing is passed, and
        both take the override by parameter."""
        for fn in (leerie._degrade_max_parallel_for_wave,
                   leerie._await_worker_memory_admission):
            src = inspect.getsource(fn)
            assert "_WORKER_BUILD_PEAK_BYTES" in src, (
                f"{fn.__name__} no longer falls back to the shared "
                f"build-peak constant")
            sig = inspect.signature(fn).parameters["build_peak_bytes"]
            assert sig.default is None, (
                f"{fn.__name__}'s demand figure must be an injectable "
                f"parameter, not a def-time constant")
        # The fallback really is the same object, not two equal literals:
        # drive each with nothing supplied against a slice sized to fit
        # exactly one build peak and require identical answers.
        est = leerie._WORKER_BUILD_PEAK_BYTES
        assert leerie.resolve_worker_demand_estimate(None) == est
        # A declared heap raises BOTH, by construction — one resolver feeds
        # both call sites (phase_execute and _invoke_admitted).
        raised = leerie.resolve_worker_demand_estimate(9 * 1024**3)
        assert raised == 9 * 1024**3 + leerie._NODE_HEAP_HEADROOM_BYTES
        assert raised > est
