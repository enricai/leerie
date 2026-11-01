"""The falsifier's confirms queue on the execution lock itself (DESIGN §6
*Evaluations queue on the lock itself*).

Review round 21 (execution M1): a sample's confirm waited on a lock of its
own outside the execution lock, and its first run, B* reruns and facts read
were three separate exclusives. Each release of an exclusive admitted the
workers waiting to spawn before the next queued confirm could ask, so that
confirm then waited for a whole worker, and workers slipped in between one
confirm's sections — 2.9x the unlocked wall time at 4 lanes, ~5x at 8.

The real `_falsify_items` and `_falsifier_gates` (and the real
`_confirm_still_failing`, `_ExecLock` and pass-through gates) drive the
schedule here; only the worker, the probe's runs and the facts read are
stand-ins, each holding the lock the way the real one does (a worker shared
with `worker=True`, a run or facts read inside `_held_out_section`).
"""
from __future__ import annotations

import asyncio
import contextvars
import random
import time
import types
from pathlib import Path

import pytest

CASE = "probe.test.ts :: probe case"
_ITEM: contextvars.ContextVar[str] = contextvars.ContextVar("_ITEM")
U = 0.01  # one schedule unit, in seconds


def _events_env(leerie, tmp_path, monkeypatch, *, lanes: int,
                worker_units, runs: int = 2):
    """Stub everything around the schedule; return (st, caps, events)."""
    events: list[tuple] = []

    def ev(kind, who):
        events.append((time.monotonic(), kind, who))

    def checkout(st, label, sha):
        d = tmp_path / label
        (d / "falsify_probe").mkdir(parents=True, exist_ok=True)
        return d
    monkeypatch.setattr(leerie, "_falsifier_checkout", checkout)

    async def deps(*a, **k):
        return True

    async def restore(*a, **k):
        return None
    monkeypatch.setattr(leerie, "_ensure_worktree_deps", deps)
    monkeypatch.setattr(leerie, "_restore_tree", restore)
    monkeypatch.setattr(leerie, "_ts_resolvable", lambda *a, **k: True)
    monkeypatch.setattr(leerie, "_measurement_grants",
                        lambda *a, **k: leerie.WorkerGrants((), ()))
    monkeypatch.setattr(leerie, "resolve_test_files_scoped",
                        lambda root: "vitest run {files}")
    monkeypatch.setattr(leerie, "_acceptance_own_start", lambda st: None)
    monkeypatch.setattr(leerie, "_falsifier_place_leads",
                        lambda *a, **k: ("", []))
    monkeypatch.setattr(leerie, "_falsifier_user_prompt",
                        lambda *a, **k: "u")
    monkeypatch.setattr(leerie, "_falsifier_probe_activity",
                        lambda *a, **k: {})
    monkeypatch.setattr(leerie, "_falsifier_needs_reask",
                        lambda *a, **k: False)
    monkeypatch.setattr(leerie, "_falsifier_save_probe",
                        lambda *a, **k: None)
    monkeypatch.setattr(leerie, "_acceptance_cmd",
                        lambda *a, **k: "vitest run probe.test.ts")
    monkeypatch.setattr(leerie, "_run_repo", lambda st: tmp_path)
    monkeypatch.setattr(leerie, "_falsifier_coupling",
                        lambda *a, **k: {"coupled": False})
    monkeypatch.setattr(leerie, "_host_cpu_psi", lambda *a, **k: 0.0)

    async def claude_p(**kw):
        who = kw["sid"]
        i = int(who.split("-i")[1].split("-")[0])
        _ITEM.set(who)
        ev("worker-ask", who)
        async with leerie._exec_lock().shared(worker=True):
            ev("worker-start", who)
            await asyncio.sleep(worker_units[i] * U)
            ev("worker-end", who)
        lane = Path(kw["cwd"])
        (lane / "falsify_probe" / "probe.test.ts").write_text("x")
        return {"verdict": "unmet_demonstrated",
                "probe_path": "falsify_probe/probe.test.ts",
                "case_name": "probe case",
                "assertion_quote": "expect(add(2, 3)).toBe(5)",
                "item_span_quote": "add of two positives returns the sum",
                "entry_point": "add", "reason": "r"}
    monkeypatch.setattr(leerie, "claude_p", claude_p)

    async def capture(tree, cmd, st, caps, *, label, **kw):
        async with leerie._held_out_section(st):
            ev("run", label)
            await asyncio.sleep(1 * U)
        return {"ids": [CASE]}
    monkeypatch.setattr(leerie, "_capture_failing_test_ids", capture)

    async def facts(st, tree, src, rel, case):
        async with leerie._held_out_section(st):
            ev("facts", _ITEM.get())
            await asyncio.sleep(1 * U)
        return {"reach_src": ["expect(add(2, 3)).toBe(5)"]}
    monkeypatch.setattr(leerie, "_probe_case_facts_placed", facts)

    st = types.SimpleNamespace(
        run_dir=tmp_path / "run", repo_root=tmp_path, run_id="r",
        data={"task": "t", "acceptance": {"sets": []}},
        save=lambda: None, bump_workers=lambda caps: None)
    (st.run_dir / "logs").mkdir(parents=True)
    caps = dict(leerie.DEFAULT_CAPS)
    caps.update(max_parallel=lanes, falsifier_samples=1,
                confirm_pairs=runs - 1, confirm_pairs_escalated=runs - 1,
                confirm_budget_sec=60)
    return st, caps, events


def _drive(leerie, st, caps, items: int) -> float:
    normalized = [{"item": "add of two positives returns the sum"}
                  for _ in range(items)]

    async def go():
        t0 = time.monotonic()
        await leerie._falsify_items(
            st, caps, {}, {}, sha="0" * 40, normalized=normalized,
            indices=list(range(items)), parts_by_idx={}, progress={})
        return time.monotonic() - t0
    return asyncio.run(go())


def _per_item(events) -> dict[str, dict[str, float]]:
    """Per item index: when its worker asked, started and ended, and when
    its confirm's first run and facts read began."""
    out: dict[str, dict[str, float]] = {}
    for t, kind, who in events:
        item = who.split("-i", 1)[1].split("-")[0] if "-i" in who else who
        rec = out.setdefault(item, {})
        key = {"run": "first_run"}.get(kind, kind)
        if key == "first_run" and key in rec:
            continue
        rec[key] = t
    return out


def _assert_schedule(events) -> None:
    items = _per_item(events)
    starts = [r["worker-start"] for r in items.values()]
    for i, r in items.items():
        # One hold: no worker starts inside a confirm, B* rerun included.
        inside = [t for t in starts if r["first_run"] < t < r["facts"]]
        assert not inside, (i, r)
        # The queue: a confirm whose worker had ended before another
        # worker asked to spawn runs before that worker starts.
        for j, q in items.items():
            if r["worker-end"] < q["worker-ask"]:
                assert r["first_run"] < q["worker-start"], (i, j, r, q)


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_a_confirm_is_one_hold_and_queues_on_the_lock(leerie, tmp_path,
                                                      monkeypatch, seed):
    """Random worker lengths, 4 lanes, 10 items: every item confirms
    (first run, B* rerun, facts read), no worker starts inside a confirm,
    and no worker that asked after a confirm was queued starts before
    it."""
    rnd = random.Random(seed)
    units = [rnd.uniform(3, 12) for _ in range(10)]
    st, caps, events = _events_env(leerie, tmp_path, monkeypatch, lanes=4,
                                   worker_units=units)
    _drive(leerie, st, caps, 10)
    assert sum(1 for _, k, _ in events if k in ("run", "facts")) == 10 * 3
    assert len(_per_item(events)) == 10
    _assert_schedule(events)


def test_queued_confirms_precede_a_worker_that_asks_after_them(
        leerie, tmp_path, monkeypatch):
    """The convoy's shape, set up: four workers end together, so three
    confirms queue behind the first; the fifth item's worker asks to spawn
    on the lane the first confirm frees. Every queued confirm runs before
    it — on a lock outside the execution lock, the one queued third waited
    for that whole worker."""
    units = [2, 2.2, 2.4, 2.6, 30]
    st, caps, events = _events_env(leerie, tmp_path, monkeypatch, lanes=4,
                                   worker_units=units)
    _drive(leerie, st, caps, 5)
    items = _per_item(events)
    assert all(items[i]["first_run"] < items["4"]["worker-start"]
               for i in "0123"), items
    _assert_schedule(events)


def _unlocked(leerie, monkeypatch) -> None:
    """The reference schedule: confirms one at a time, as before the
    execution lock (the review's "none"), and no worker ever held back —
    every section shared."""
    monkeypatch.setattr(leerie, "_held_out_section",
                        lambda st: leerie._exec_lock().shared())
    real, one = leerie._falsifier_gates, {}

    async def serial(*a, **k):
        lock = one.setdefault("lock", asyncio.Lock())
        async with lock:
            return await real(*a, **k)
    monkeypatch.setattr(leerie, "_falsifier_gates", serial)


def test_wall_time_stays_near_the_inherent_batch_cost(leerie, tmp_path,
                                                      monkeypatch):
    """The review's measure on the real code: 4 lanes, 16 items, workers
    of 5–40 units, three held-out runs of 1 unit per confirm, against the
    same schedule with confirms serial and workers never held back.
    Inherent (no worker while a confirm runs) is ~1.5x; the convoy measured
    2.9x. Bounded loosely, at 2.2x, against timing noise."""
    rnd = random.Random(7)
    units = [rnd.uniform(5, 40) for _ in range(16)]
    st, caps, _ = _events_env(leerie, tmp_path, monkeypatch, lanes=4,
                              worker_units=units)
    locked = _drive(leerie, st, caps, 16)
    _unlocked(leerie, monkeypatch)
    st2, caps2, _ = _events_env(leerie, tmp_path / "u", monkeypatch,
                                lanes=4, worker_units=units)
    unlocked = _drive(leerie, st2, caps2, 16)
    assert locked / unlocked < 2.2, (locked, unlocked)
