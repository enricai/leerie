"""The execution lock (DESIGN §6 *A held-out evaluation runs alone, in a
world that dies with it (I3)*, the time half): every worker holds it shared
from its spawn until its process has exited, as every confined command does;
a held-out evaluation holds it exclusive, in its run only (`_ExecLock`; no
level spans runs). A worker spawned inside an evaluation stops the run, and by rule nothing reachable inside an evaluation spawns a worker or
waits on a semaphore a shared holder may hold, so the lock cannot deadlock.

Real code throughout: the real `_invoke` spawning a real process (unconfined:
`grants=None`, the host-side seam's form, so no CLI is needed), the real
`_run_streaming` and shim."""
from __future__ import annotations

import ast
import asyncio
import json
import textwrap
import time
from pathlib import Path

import pytest

from tests.conftest import REPO_ROOT
from tests.test_worker_confinement import (  # noqa: F401  (fixture)
    _St, pytestmark, run)

LEERIE_PY = REPO_ROOT / "orchestrator" / "leerie.py"


def _top_level(module: ast.Module) -> dict[str, ast.AST]:
    """The module's functions and its classes' methods, by name: a nested
    helper (a local `_invoke`, a closure) never stands in for one."""
    out: dict[str, ast.AST] = {}
    for n in module.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out[n.name] = n
        elif isinstance(n, ast.ClassDef):
            for m in n.body:
                if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    out.setdefault(m.name, m)
    return out
RESULT = json.dumps({"type": "result", "subtype": "success",
                     "is_error": False, "result": "{}",
                     "structured_output": {}})


def _view(run) -> Path:
    return run["wts"][f"{run['run_dir'].name}-view-staging"]


def _st(run) -> _St:
    return _St(run["user"], run["run_dir"])


def _worker(leerie, tmp_path: Path, secs: float, events: list,
            name: str = "worker"):
    """A real `_invoke` of a process that lives `secs` and then emits a
    result event."""
    async def one():
        events.append((f"{name}-asked", time.monotonic()))
        await leerie._invoke(
            ["bash", "-c", f"sleep {secs}; echo '{RESULT}'"],
            str(tmp_path), 60, name, tmp_path, "quiet", grants=None)
        events.append((f"{name}-done", time.monotonic()))
    return one


def _command(leerie, st, tree, name, secs, events):
    async def one():
        await leerie._run_streaming(
            ["bash", "-c", f"sleep {secs}"],
            grants=leerie._measurement_grants(st, tree), cwd=str(tree),
            timeout=60, verbosity="quiet")
        events.append((name, time.monotonic()))
    return one


# --- the run's level -----------------------------------------------------------

def test_no_measurement_runs_during_a_held_out_evaluation(leerie, run):
    st, tree = _st(run), _view(run)
    events: list = []

    async def held():
        async with leerie._held_out_section(st):
            events.append(("held-start", time.monotonic()))
            await _command(leerie, st, tree, "held-cmd", 1.0, events)()
            events.append(("held-end", time.monotonic()))

    async def later_measurement():
        await asyncio.sleep(0.3)
        t = time.monotonic()
        await _command(leerie, st, tree, "measure", 0.0, events)()
        events.append(("asked", t))

    async def go():
        await asyncio.gather(held(), later_measurement())
    asyncio.run(go())
    at = dict(events)
    assert at["asked"] < at["held-end"] < at["measure"], events


def test_a_held_out_evaluation_waits_for_running_measurements(leerie, run):
    """And a measurement asked for while it waits runs after it, so a
    stream of measurements cannot starve it."""
    st, tree = _st(run), _view(run)
    events: list = []

    async def held():
        await asyncio.sleep(0.3)
        async with leerie._held_out_section(st):
            events.append(("held-start", time.monotonic()))
            await _command(leerie, st, tree, "held-cmd", 0.5, events)()
            events.append(("held-end", time.monotonic()))

    async def asked_later():
        await asyncio.sleep(0.6)
        await _command(leerie, st, tree, "late", 0.0, events)()

    async def go():
        await asyncio.gather(_command(leerie, st, tree, "first", 1.2,
                                      events)(), held(), asked_later())
    asyncio.run(go())
    at = dict(events)
    assert at["first"] <= at["held-start"] < at["held-end"] <= at["late"], \
        events


def test_an_evaluation_waits_for_a_running_worker_and_no_worker_starts(
        leerie, tmp_path):
    """A worker is a reader for its process's whole life: an evaluation
    asked for while one runs starts only after its process has exited, and
    a worker asked for meanwhile starts only after the evaluation."""
    events: list = []

    async def held():
        await asyncio.sleep(0.3)
        events.append(("held-asked", time.monotonic()))
        async with leerie._held_out_section(object()):
            events.append(("held-start", time.monotonic()))
            await asyncio.sleep(0.5)
            events.append(("held-end", time.monotonic()))

    async def late_worker():
        await asyncio.sleep(0.6)
        await _worker(leerie, tmp_path, 0.0, events, "late")()

    async def go():
        await asyncio.gather(_worker(leerie, tmp_path, 1.2, events)(),
                             held(), late_worker())
    asyncio.run(asyncio.wait_for(go(), 30))
    at = dict(events)
    assert (at["held-asked"] < at["worker-done"] <= at["held-start"]
            < at["held-end"] <= at["late-done"]), events
    assert at["late-asked"] < at["held-end"], events


def test_a_worker_spawned_inside_an_evaluation_stops_the_run(leerie,
                                                             tmp_path):
    """No pass-through for a worker: inside a section, `_invoke` dies with
    the invariant before anything is spawned, and leaves the lock free."""
    marker = tmp_path / "spawned"

    async def go():
        async with leerie._held_out_section(object()):
            await leerie._invoke(
                ["bash", "-c", f"touch {marker}; echo '{RESULT}'"],
                str(tmp_path), 60, "inside", tmp_path, "quiet", grants=None)
    with pytest.raises(SystemExit):
        asyncio.run(asyncio.wait_for(go(), 30))
    assert not marker.exists()
    assert not (tmp_path / "stdin").exists() or not list(
        (tmp_path / "stdin").iterdir())

    async def after():
        lock = leerie._exec_lock()
        await leerie._invoke(["bash", "-c", f"echo '{RESULT}'"],
                             str(tmp_path), 60, "after", tmp_path, "quiet",
                             grants=None)
        return lock.readers, lock.writer
    assert asyncio.run(asyncio.wait_for(after(), 30)) == (0, False)


def test_a_cancelled_worker_releases_the_lock(leerie, tmp_path):
    async def go():
        lock = leerie._exec_lock()
        t = asyncio.ensure_future(_worker(leerie, tmp_path, 30, [])())
        await asyncio.sleep(0.5)
        assert lock.readers == 1
        t.cancel()
        with pytest.raises(asyncio.CancelledError):
            await t
        async with lock.exclusive() as outer:
            return outer, lock.readers
    assert asyncio.run(asyncio.wait_for(go(), 30)) == (True, 0)


def test_nested_sections_and_their_commands_pass_through(leerie):
    async def go():
        lock = leerie._exec_lock()
        async with lock.exclusive() as outer:
            async with lock.exclusive() as inner:
                async with lock.shared():
                    return outer, inner, lock.readers, lock.writer
    assert asyncio.run(asyncio.wait_for(go(), 5)) == (True, False, 0, True)


def test_a_shared_holders_own_commands_pass_through_a_waiting_evaluation(
        leerie):
    """A command run under a shared hold (an AST script's caller, `mise
    ls`) is the same holder: it never waits behind an evaluation that is
    itself waiting for that hold to end."""
    async def go():
        lock = leerie._exec_lock()
        held, release = asyncio.Event(), asyncio.Event()
        seen = {}

        async def holder():
            async with lock.shared():
                held.set()
                await release.wait()
                async with lock.shared():
                    seen["inner"] = lock.readers
        h = asyncio.ensure_future(holder())
        await held.wait()
        w = asyncio.ensure_future(lock.exclusive().__aenter__())
        await asyncio.sleep(0.05)
        seen["waiting"] = lock.writers_waiting
        release.set()
        await h
        assert await w is True
        return seen["inner"], seen["waiting"], lock.writer
    assert asyncio.run(asyncio.wait_for(go(), 5)) == (1, 1, True)


def test_an_evaluation_inside_a_shared_hold_stops_the_run(leerie):
    """It would wait for its own caller: a loud failure, never a wait."""
    async def go():
        async with leerie._exec_lock().shared():
            async with leerie._held_out_section(object()):
                pass
    with pytest.raises(SystemExit):
        asyncio.run(asyncio.wait_for(go(), 5))


def test_a_cancelled_waiter_does_not_wedge_the_lock(leerie):
    async def go():
        lock = leerie._exec_lock()
        held, release = asyncio.Event(), asyncio.Event()

        async def holder():
            async with lock.shared():
                held.set()
                await release.wait()
        h = asyncio.ensure_future(holder())
        await held.wait()
        w = asyncio.ensure_future(lock.exclusive().__aenter__())
        await asyncio.sleep(0.05)
        w.cancel()
        with pytest.raises(asyncio.CancelledError):
            await w
        release.set()
        await h
        async with lock.shared():
            pass
        return lock.writers_waiting, lock.readers
    assert asyncio.run(asyncio.wait_for(go(), 5)) == (0, 0)


# --- the lock is the run's alone (DESIGN §6 *There is no host level*) -------

def test_two_runs_locks_never_block_each_other(leerie, tmp_path, monkeypatch):
    """Two runs (two event loops, as two orchestrators are) sharing the
    host-wide directory: while one holds an evaluation, the other's workers
    and its own evaluation start at once. The host level this replaces made
    the second wait for the first."""
    import threading
    monkeypatch.setenv(leerie._HOST_DIR_ENV, str(tmp_path))
    holding, release = threading.Event(), threading.Event()
    took: dict = {}

    def run_a():
        async def go():
            async with leerie._exec_lock().exclusive():
                holding.set()
                await asyncio.to_thread(release.wait, 30)
        asyncio.run(go())

    def run_b():
        async def go():
            t0 = time.monotonic()
            async with leerie._exec_lock().shared(worker=True):
                took["shared"] = time.monotonic() - t0
            t0 = time.monotonic()
            async with leerie._exec_lock().exclusive():
                took["exclusive"] = time.monotonic() - t0
        asyncio.run(asyncio.wait_for(go(), 10))

    a = threading.Thread(target=run_a)
    a.start()
    try:
        assert holding.wait(10)
        b = threading.Thread(target=run_b)
        b.start()
        b.join(15)
        assert not b.is_alive()
    finally:
        release.set()
        a.join(15)
    assert took["shared"] < 1.0 and took["exclusive"] < 1.0, took


def test_an_evaluation_logs_both_ends_of_its_wait(leerie, monkeypatch):
    """A stall is never silent: the wait names what it waits for, and its
    end how long it took; an evaluation that need not wait logs nothing."""
    lines: list[str] = []
    monkeypatch.setattr(leerie, "log", lambda m, *a, **k: lines.append(m))

    async def go():
        lock = leerie._exec_lock()
        async with lock.exclusive():
            pass
        quiet = list(lines)
        held, release = asyncio.Event(), asyncio.Event()

        async def holder(worker):
            async with lock.shared(worker=worker):
                held.set()
                await release.wait()
        hs = [asyncio.ensure_future(holder(w)) for w in (True, False)]
        await held.wait()
        await asyncio.sleep(0)
        w = asyncio.ensure_future(lock.exclusive().__aenter__())
        await asyncio.sleep(0.05)
        asked = list(lines)
        await asyncio.sleep(1.1)
        release.set()
        await asyncio.gather(*hs)
        assert await w is True
        return quiet, asked
    quiet, asked = asyncio.run(asyncio.wait_for(go(), 10))
    assert quiet == []
    assert asked == ["  execution lock: held-out evaluation waits for 2 "
                     "shared holder(s) of this run (1 worker(s))"], asked
    assert len(lines) == 2 and lines[1].startswith(
        "  execution lock: held-out evaluation waited "), lines
    assert int(lines[1].split("waited ")[1].rstrip("s")) >= 1, lines


# --- deadlock-freedom, by rule -----------------------------------------------

# What may never be reached from inside an evaluation: a worker (which would
# die by the guard), every wait a shared holder may be holding while it
# waits for the lock — the BLT semaphore, memory admission, the confirm
# lock, the parallelism semaphores (a shared holder takes the lock after
# them, so waiting for one inside an evaluation could wait on a holder that
# waits on the evaluation) — and a second host build slot (the section
# holds one; with the host's one slot it would wait on itself).
_FORBIDDEN_IN_SECTION = frozenset({
    "claude_p", "_invoke", "_invoke_admitted",
    "_acquire_build_slot", "_blt_semaphore",
    "_await_worker_memory_admission", "_confirm_lock", "Semaphore"})
# The three gates a measurement takes them through, which pass through
# inside an exclusive hold instead of waiting (DESIGN §6 *Inside an
# evaluation every concurrency gate is already the holder's*): the rule
# stops at them inside a section, and
# `test_every_gate_passes_through_inside_an_evaluation` executes that
# pass-through against a gate another holder has exhausted. A falsifier's
# confirm reaches all three: its section spans its B* reruns (DESIGN §6
# *Evaluations queue on the lock itself*). `_confirm_gate` is not a wait on
# a holder: a bounded poll of host CPU pressure, holding nothing.
# A section nested in a section is the same one: it takes no second slot
# (`_host_build_slot(take=False)`), and its own body is a root of the rule.
_PASS_THROUGH_GATES = frozenset({"_blt_slot", "_build_class_slot",
                                 "_confirm_hold", "_held_out_section"})


def _reach(funcs: dict[str, ast.AST], roots: set[str],
           stop: frozenset[str] = frozenset()) -> dict[str, list]:
    """Every name reachable by call from `roots` through functions defined in
    the module, with one path to it; never through a name in `stop`."""
    paths: dict[str, list] = {r: [r] for r in roots}
    todo = list(roots)
    while todo:
        name = todo.pop()
        fn = funcs.get(name)
        if fn is None or name in stop:
            continue
        for c in ast.walk(fn):
            if not isinstance(c, ast.Call):
                continue
            for n in ([c.func] + list(c.args)):
                callee = (n.id if isinstance(n, ast.Name) else
                          n.attr if isinstance(n, ast.Attribute) else None)
                if callee and callee not in paths:
                    paths[callee] = paths[name] + [callee]
                    todo.append(callee)
    return paths


def _section_violations(module: ast.Module) -> list[tuple[str, list]]:
    funcs = _top_level(module)
    out = []
    for fn in funcs.values():
        for w in ast.walk(fn):
            if not (isinstance(w, ast.AsyncWith) and any(
                    "_held_out_section" in ast.unparse(i.context_expr)
                    or "exclusive()" in ast.unparse(i.context_expr)
                    for i in w.items)):
                continue
            roots = set()
            for stmt in w.body:
                for c in ast.walk(stmt):
                    if isinstance(c, ast.Call):
                        for n in [c.func] + list(c.args):
                            if isinstance(n, ast.Name):
                                roots.add(n.id)
                            elif isinstance(n, ast.Attribute):
                                roots.add(n.attr)
                    elif isinstance(c, ast.AsyncWith):
                        roots |= {ast.unparse(i.context_expr).split("(")[0]
                                  .split(".")[-1] for i in c.items}
            for name, path in _reach(funcs, roots,
                                     _PASS_THROUGH_GATES).items():
                if name in _FORBIDDEN_IN_SECTION:
                    out.append((fn.name, path))
    return out


def test_nothing_inside_an_evaluation_spawns_a_worker_or_waits_on_a_holder():
    """Every `async with _held_out_section(...)` in the module, and the
    lock's own exclusive holders, reach no worker spawn and no wait a shared
    holder may hold — transitively, through every function the module
    defines."""
    module = ast.parse(LEERIE_PY.read_text())
    sections = [n for n in ast.walk(module) if isinstance(n, ast.AsyncWith)
                and any("_held_out_section" in ast.unparse(i.context_expr)
                        for i in n.items)]
    assert len(sections) >= 2, "the rule found no evaluation"
    assert _section_violations(module) == []


def test_the_section_rule_catches_a_worker_or_a_slot_inside():
    planted = ast.parse(textwrap.dedent("""\
        async def a(st):
            async with _held_out_section(st):
                await helper(st)
        async def helper(st):
            return await claude_p("u", "s")
        async def b(st):
            async with _held_out_section(st):
                fd = await _acquire_build_slot(st, label="x")
        async def c(st, caps):
            async with _held_out_section(st):
                await measure(st, caps)
        async def measure(st, caps):
            async with _blt_semaphore(caps), _confirm_lock():
                pass
        async def d(st, caps):
            async with _held_out_section(st):
                async with _blt_slot(caps), _confirm_hold(), \\
                        _build_class_slot(st, label="x"):
                    pass
        async def _blt_slot(caps):
            async with _blt_semaphore(caps):
                pass
        async def _confirm_hold():
            async with _confirm_lock():
                pass
        async def _build_class_slot(st, *, label):
            await _acquire_build_slot(st, label=label)
        """))
    got = {(f, tuple(p)) for f, p in _section_violations(planted)}
    assert ("a", ("helper", "claude_p")) in got
    assert ("b", ("_acquire_build_slot",)) in got
    assert ("c", ("measure", "_blt_semaphore")) in got
    assert ("c", ("measure", "_confirm_lock")) in got
    # The pass-through gates are the sanctioned way in: not violations.
    assert not [f for f, _ in got if f == "d"]


def _exhausted(leerie):
    """Hold each gate's raw resource from another task: the BLT semaphore
    to its limit, the confirm lock, and every host build slot."""
    caps = {"blt_parallel": 1}

    async def holder(started: asyncio.Event, release: asyncio.Event, st):
        sem = leerie._blt_semaphore(caps)
        # Another run's slot holder: the slot alone, not this run's lock.
        async with sem, leerie._confirm_lock(), \
                leerie._host_build_slot(st, label="other"):
            started.set()
            await release.wait()
    return caps, holder


@pytest.mark.parametrize("inside", [True, False])
def test_every_gate_passes_through_inside_an_evaluation(leerie, run,
                                                         inside,
                                                         monkeypatch):
    """Inside an exclusive hold all three gates are entered at once while
    another task holds each raw resource; outside one, the same entry
    waits — the two arms disagree, so neither passes by accident."""
    monkeypatch.delenv(leerie._HOST_DIR_ENV, raising=False)
    st = _st(run)
    st.data = {"blt_peak": {"slots": 1}}
    caps, holder = _exhausted(leerie)

    async def main() -> bool:
        started, release = asyncio.Event(), asyncio.Event()
        h = asyncio.ensure_future(holder(started, release, st))
        await started.wait()

        async def enter():
            async with leerie._blt_slot(caps), leerie._confirm_hold(), \
                    leerie._build_class_slot(st, label="mine"):
                return True
        try:
            if inside:
                async with leerie._exec_lock().exclusive():
                    return await asyncio.wait_for(enter(), 2)
            return await asyncio.wait_for(enter(), 0.5)
        except asyncio.TimeoutError:
            return False
        finally:
            release.set()
            await h
    assert asyncio.run(main()) is inside


def test_every_shared_holder_takes_the_lock_after_its_semaphores():
    """The other side of the rule: inside the shared hold of
    `_run_streaming`, `_invoke` and the `mise ls` spawn nothing waits on a
    semaphore, so an evaluation that waits for them never waits on one."""
    module = ast.parse(LEERIE_PY.read_text())
    funcs = _top_level(module)
    holds = [w for fn in ("_run_streaming", "_run_mise_install")
             if fn in funcs for w in ast.walk(funcs[fn])
             if isinstance(w, ast.AsyncWith) and any(
                 "_exec_lock" in ast.unparse(i.context_expr)
                 for i in w.items)]
    assert holds
    for w in holds:
        roots = {getattr(c.func, "id", None) or getattr(c.func, "attr", "")
                 for c in ast.walk(w) if isinstance(c, ast.Call)}
        bad = set(_reach(funcs, roots)) & (_FORBIDDEN_IN_SECTION
                                           - {"Semaphore"})
        assert not bad, bad
    invoke = set(_reach(funcs, {"_invoke"}))
    assert not invoke & (_FORBIDDEN_IN_SECTION - {"_invoke", "Semaphore"})


# --- an evaluation is one of the host's builds, after its run's lock --------
# (DESIGN §6 *The run's execution lock, then a host build slot, everywhere*;
# review round 31, isolation E1 and execution 5: with the slot taken first,
# an evaluation held a host slot while it waited for its run's workers.)

@pytest.mark.parametrize("slot_taken", [True, False])
def test_an_evaluation_takes_a_host_slot_once_it_holds_the_lock(
        leerie, run, slot_taken, monkeypatch):
    """Evaluations of different runs overlap now that the lock is the
    run's; each takes a host build slot, as every command does, so the
    host's build semaphore bounds them (review round 30 execution). With
    the host's only slot held elsewhere the section holds the lock and
    waits for the slot; with it free it enters at once — the arms
    disagree. Its own commands pass through the slot it holds."""
    monkeypatch.delenv(leerie._HOST_DIR_ENV, raising=False)
    st = _st(run)
    st.data = {"blt_peak": {"slots": 1}}

    async def main() -> tuple[bool, bool]:
        started, release = asyncio.Event(), asyncio.Event()

        async def other_run():
            # Another run's holder: the slot alone, not this run's lock.
            async with leerie._host_build_slot(st, label="other run"):
                started.set()
                await release.wait()
        h = asyncio.ensure_future(other_run()) if slot_taken else None
        if h is not None:
            await asyncio.wait_for(started.wait(), 5)
        locked = []

        async def evaluate():
            async with leerie._held_out_section(st):
                async with leerie._build_class_slot(st, label="own"):
                    return True

        async def watch():
            await asyncio.sleep(0.3)
            locked.append(leerie._exec_lock().writer)
        try:
            w = asyncio.ensure_future(watch())
            entered = await asyncio.wait_for(evaluate(), 1.5)
        except asyncio.TimeoutError:
            entered = False
        finally:
            release.set()
            if h is not None:
                await h
        await w
        return entered, locked[0]
    entered, locked = asyncio.run(main())
    assert entered is not slot_taken
    # Waiting for the slot, it already held the lock exclusive.
    assert locked is slot_taken


def test_a_command_takes_its_slot_under_the_shared_lock(leerie, run,
                                                        monkeypatch):
    """The order is the lock, then the slot: a command's slot is taken
    inside its shared hold, which an evaluation of its run waits for, and
    a holder already sharing the lock takes a slot without a second hold.
    Round 30's order stopped the run here."""
    monkeypatch.delenv(leerie._HOST_DIR_ENV, raising=False)
    st = _st(run)
    st.data = {"blt_peak": {"slots": 1}}

    async def main() -> tuple:
        lock = leerie._exec_lock()
        async with leerie._build_class_slot(st, label="cmd") as slot:
            inside = (lock.readers, slot)
        async with lock.shared():
            async with leerie._build_class_slot(st, label="x") as slot:
                nested = (lock.readers, slot)
        return inside, nested, lock.readers
    assert asyncio.run(asyncio.wait_for(main(), 5)) == ((1, 0), (1, 0), 0)


def test_an_evaluation_waiting_for_its_runs_workers_holds_no_slot(
        leerie, run, tmp_path, monkeypatch):
    """Run A's worker holds A's lock shared; A's evaluation waits for it.
    Run B (another event loop, as another orchestrator is) asks for the
    host's only slot meanwhile and gets it at once: the waiting evaluation
    holds none. With the slot taken before the lock, B waited until A's
    worker exited (review round 31, isolation E1)."""
    import threading
    monkeypatch.setenv(leerie._HOST_DIR_ENV, str(tmp_path / "host"))
    (tmp_path / "host").mkdir()
    st = _st(run)
    st.data = {"blt_peak": {"slots": 1}}
    waiting, release = threading.Event(), threading.Event()
    took: dict = {}

    def run_a():
        async def go():
            lock = leerie._exec_lock()

            async def worker():
                async with lock.shared(worker=True):
                    await asyncio.to_thread(release.wait, 30)

            async def evaluate():
                while not lock.readers:
                    await asyncio.sleep(0.01)
                async with leerie._held_out_section(st):
                    took["a-evaluated"] = True

            async def mark():
                while not lock.writers_waiting:
                    await asyncio.sleep(0.01)
                waiting.set()
            await asyncio.gather(worker(), evaluate(), mark())
        asyncio.run(go())

    def run_b():
        async def go():
            t0 = time.monotonic()
            async with leerie._build_class_slot(st, label="run b") as slot:
                took["b"] = (time.monotonic() - t0, slot)
        try:
            asyncio.run(asyncio.wait_for(go(), 2))
        except asyncio.TimeoutError:
            took["b"] = None

    a = threading.Thread(target=run_a)
    a.start()
    try:
        assert waiting.wait(10)
        time.sleep(0.2)
        b = threading.Thread(target=run_b)
        b.start()
        b.join(10)
    finally:
        release.set()
        a.join(15)
    assert took["b"] is not None and took["b"][0] < 1.0, took
    assert took["b"][1] == 0
    assert took.get("a-evaluated"), took


def test_nothing_inside_a_build_slot_takes_another_gate():
    """One build, one slot, by rule rather than by a flag: nothing reachable
    inside a `_build_class_slot` hold takes a second slot, opens a section,
    waits on a semaphore or spawns a worker — so a slot holder waits on
    nothing but its own command, which is what makes the lock-then-slot
    order deadlock-free."""
    module = ast.parse(LEERIE_PY.read_text())
    funcs = _top_level(module)
    forbidden = (_FORBIDDEN_IN_SECTION
                 | {"_build_class_slot", "_host_build_slot",
                    "_held_out_section", "_blt_slot", "_confirm_hold"})
    holds = [(fn.name, w) for fn in funcs.values() for w in ast.walk(fn)
             if isinstance(w, ast.AsyncWith) and any(
                 "_build_class_slot" in ast.unparse(i.context_expr)
                 for i in w.items)]
    assert len(holds) >= 5, "the rule found no build slot"
    bad = []
    for name, w in holds:
        roots = {n.id if isinstance(n, ast.Name) else n.attr
                 for stmt in w.body for c in ast.walk(stmt)
                 if isinstance(c, ast.Call)
                 for n in [c.func] + list(c.args)
                 if isinstance(n, (ast.Name, ast.Attribute))}
        hit = set(_reach(funcs, roots)) & forbidden
        if hit:
            bad.append((name, sorted(hit)))
    assert bad == []
