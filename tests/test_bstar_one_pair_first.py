"""Review round 8 L3: a sized B* confirm gives every unit one base/final
pair before any unit gets a second (DESIGN §9 *Which failures are new*,
step 2, *One pair for every file before a second for any*).

Before: `_confirm_sized`'s first pass ran all `k` pairs of the units it
chose before a unit it left out got its first. Sizing is by recorded
durations; a first pass that ran faster than recorded spent the difference
on its own second and third pairs. Every driver runs the real
`_confirm_new_failures` on a fake clock."""
from __future__ import annotations

import asyncio

FLAKE, REG = "tests/test_flake.py", "tests/test_reg.py"
CANDS = {f"{FLAKE}::t": FLAKE, f"{REG}::t": REG}


def _drive(leerie, recorded: dict[str, float], actual: dict[str, float]):
    """`recorded`: the durations the sizing reads; `actual`: what one rerun
    of each file costs. The regression file fails on every final rerun and
    passes on every base rerun; the flake file passes everywhere."""
    clock = {"t": 0.0}
    calls: list[dict] = []
    pairs_log: list[tuple[str, ...]] = []

    async def confirm(units, cands, pcaps, gate, **pairs):
        calls.append({"units": list(units), **{k: (v if k != "resume"
                                                   else bool(v))
                                               for k, v in pairs.items()}})
        cost = sum(actual[u] for u in units) + 10.0

        def runner(side: str):
            async def run(left: float):
                if cost > left:
                    clock["t"] += left
                    return None
                clock["t"] += cost
                pairs_log.append((side, *units))
                return ({c for c in cands if c.startswith(REG)}
                        if side == "final" else set())
            return run
        return await leerie._confirm_new_failures(
            cands, runner("base"), runner("final"), pcaps, label="x",
            seen_failing=True, prior_gate=gate, read_psi=lambda: 0.0,
            clock=lambda: clock["t"], **pairs)

    v = asyncio.run(leerie._confirm_sized(
        CANDS, recorded, rank={}, caps={**leerie.DEFAULT_CAPS}, overhead=10.0,
        label="x", confirm=confirm))
    return v, calls, pairs_log, clock["t"]


def test_a_first_pass_faster_than_recorded_no_longer_starves_the_rest(
        leerie):
    """The reviewer's files, 300 s and 600 s recorded, with the flake file
    rerunning in 100 s. Before: three flake pairs (660 s) left 1140 s, short
    of one regression pair (1220 s) — the regression was never rerun and
    the run would ship it `done`. Now the flake file's one pair (220 s)
    leaves 1580 s: the regression is rerun and new."""
    assert leerie.DEFAULT_CAPS["confirm_budget_sec"] == 1800
    v, calls, pairs, t = _drive(
        leerie, {FLAKE: 300.0, REG: 600.0}, {FLAKE: 100.0, REG: 600.0})
    assert [c["units"] for c in calls] == [[FLAKE], [REG], [FLAKE]]
    assert calls[0].get("max_pairs") == 1 and calls[1].get("max_pairs") == 1
    assert calls[2].get("resume") is True and "max_pairs" not in calls[2]
    # One pair of the flake file, one of the regression file, then the
    # flake file's further pairs with what was left.
    assert pairs[:4] == [("base", FLAKE), ("final", FLAKE),
                         ("base", REG), ("final", REG)]
    assert f"{REG}::t" in v["new"] and f"{REG}::t" in v["budget_short"]
    assert not v.get("sized_out")
    assert f"{FLAKE}::t" not in v["new"]
    # The continued pass's verdict is over every pair it finished.
    assert v["per_id"][f"{FLAKE}::t"]["final_runs"] == 2
    assert t <= 1800.0


def test_the_reviewers_exact_durations_are_bounded_by_arithmetic(leerie):
    """With durations that are right, one pair of both files never fits:
    (300 + 600 + 10) × 2 = 1820 and 620 + 1220 = 1840 both exceed 1800.
    The flake file still takes only one pair before the regression file is
    sized — and then its remaining pairs from what is left."""
    v, calls, pairs, t = _drive(
        leerie, {FLAKE: 300.0, REG: 600.0}, {FLAKE: 300.0, REG: 600.0})
    assert [c["units"] for c in calls] == [[FLAKE], [FLAKE]]
    assert calls[0].get("max_pairs") == 1 and calls[1].get("resume") is True
    assert v["sized_out_units"] == [REG]
    assert f"{REG}::t" in v["budget_short_unsettled"]
    assert t <= 1800.0


def test_a_first_pass_holding_every_unit_runs_its_pairs_as_before(leerie):
    """Nothing waits: no `max_pairs`, one pass, all `k` pairs."""
    v, calls, pairs, _t = _drive(
        leerie, {FLAKE: 30.0, REG: 200.0}, {FLAKE: 30.0, REG: 200.0})
    assert calls == [{"units": [FLAKE, REG]}]
    assert v["pairs_run"] == 3 and v["new"] == [f"{REG}::t"]
    assert not v.get("budget_short")


def test_a_paused_confirm_resumes_where_it_stopped(leerie):
    """`_confirm_new_failures` itself: paused after one pair, it reports a
    shortfall; resumed with that pair, it finishes `k` and decides over all
    of them — a final rerun that passes refutes `new`."""
    seq = {"final": [{"a"}, {"a"}, set()], "base": [set(), set(), set()]}

    def runner(side: str):
        async def run(left: float):
            return seq[side].pop(0)
        return run
    caps = {**leerie.DEFAULT_CAPS}
    v1 = asyncio.run(leerie._confirm_new_failures(
        ["a"], runner("base"), runner("final"), caps, label="x",
        seen_failing=True, read_psi=lambda: 0.0, max_pairs=1))
    assert v1["paused"] and v1["budget_exhausted"] and v1["pairs_run"] == 1
    assert v1["new"] == ["a"] and v1["budget_short"] == ["a"]
    v2 = asyncio.run(leerie._confirm_new_failures(
        ["a"], runner("base"), runner("final"), caps, label="x",
        seen_failing=True, prior_gate=v1["gate"], resume=v1["_resume"]))
    assert v2["pairs_run"] == 3 and not v2["budget_exhausted"]
    assert "paused" not in v2 and "_resume" not in v2
    assert v2["new"] == [] and v2["resource_affected"] == ["a"]
    assert v2["per_id"]["a"] == {"final_failed": 2, "base_failed": 0,
                                 "final_runs": 3, "base_runs": 3}
