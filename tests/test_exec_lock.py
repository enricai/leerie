"""The execution lock (DESIGN §6 *A held-out evaluation runs alone, in a
world that dies with it (I3)*, the time half): every worker holds it shared
from its spawn until its process has exited, as every confined command does;
a held-out evaluation holds it exclusive — in the run (`_ExecLock`) and on
the host (`_host_exec_lock`). A worker spawned inside an evaluation stops the
run, and by rule nothing reachable inside an evaluation spawns a worker or
waits on a semaphore a shared holder may hold, so the lock cannot deadlock.

Real code throughout: the real `_invoke` spawning a real process (unconfined:
`grants=None`, the host-side seam's form, so no CLI is needed), the real
`_run_streaming` and shim."""
from __future__ import annotations

import ast
import asyncio
import fcntl
import json
import os
import subprocess
import sys
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


# --- the host's level --------------------------------------------------------

_HOLDER = textwrap.dedent("""\
    import fcntl, os, sys, time
    fd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_SH if sys.argv[2] == "sh" else fcntl.LOCK_EX)
    print("held", flush=True)
    time.sleep(float(sys.argv[3]))
    """)


def _hold(path: Path, mode: str, secs: float) -> subprocess.Popen:
    p = subprocess.Popen([sys.executable, "-c", _HOLDER, str(path), mode,
                          str(secs)], stdout=subprocess.PIPE, text=True)
    assert p.stdout.readline().strip() == "held"
    return p


def test_an_evaluation_waits_for_another_runs_shared_holder(
        leerie, tmp_path, monkeypatch):
    """Another run's worker (a process holding `exec.lock` shared) delays
    this run's evaluation until it lets go; this run's shared takers then
    wait behind the evaluation (writer preference through `exec.intent`)."""
    monkeypatch.setenv(leerie._HOST_DIR_ENV, str(tmp_path))
    other = _hold(tmp_path / leerie.HOST_EXEC_LOCK, "sh", 1.5)
    events: list = []

    async def held():
        events.append(("held-asked", time.monotonic()))
        async with leerie._host_exec_lock(True):
            events.append(("held-start", time.monotonic()))
            await asyncio.sleep(0.3)
            events.append(("held-end", time.monotonic()))

    async def shared_later():
        await asyncio.sleep(0.5)
        async with leerie._host_exec_lock(False):
            events.append(("shared-start", time.monotonic()))

    async def go():
        await asyncio.gather(held(), shared_later())
    try:
        asyncio.run(asyncio.wait_for(go(), 30))
    finally:
        other.wait(timeout=30)
    at = dict(events)
    assert at["held-start"] - at["held-asked"] >= 1.0, events
    assert at["held-end"] <= at["shared-start"], events


def test_the_host_level_is_released_and_absent_without_a_host_dir(
        leerie, tmp_path, monkeypatch):
    monkeypatch.delenv(leerie._HOST_DIR_ENV, raising=False)

    async def none():
        async with leerie._host_exec_lock(True):
            return "ok"
    assert asyncio.run(none()) == "ok"
    monkeypatch.setenv(leerie._HOST_DIR_ENV, str(tmp_path))

    async def twice():
        async with leerie._exec_lock().exclusive():
            pass
        async with leerie._exec_lock().shared():
            pass
    asyncio.run(asyncio.wait_for(twice(), 10))
    # Nothing still holds either file: an exclusive flock succeeds at once.
    for name in (leerie.HOST_EXEC_LOCK, leerie.HOST_EXEC_INTENT):
        fd = os.open(tmp_path / name, os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(fd)


# --- deadlock-freedom, by rule -----------------------------------------------

# What may never be reached from inside an evaluation: a worker (which would
# die by the guard), and every wait a shared holder may be holding while it
# waits for the lock — the build slot, the BLT semaphore, memory admission,
# the confirm lock, the parallelism semaphores (a shared holder takes the
# lock after them, so waiting for one inside an evaluation could wait on a
# holder that waits on the evaluation).
_FORBIDDEN_IN_SECTION = frozenset({
    "claude_p", "_invoke", "_invoke_admitted", "_build_class_slot",
    "_acquire_build_slot", "_blt_semaphore",
    "_await_worker_memory_admission", "_confirm_lock", "_confirm_gate",
    "Semaphore"})


def _reach(funcs: dict[str, ast.AST], roots: set[str]) -> dict[str, list]:
    """Every name reachable by call from `roots` through functions defined in
    the module, with one path to it."""
    paths: dict[str, list] = {r: [r] for r in roots}
    todo = list(roots)
    while todo:
        name = todo.pop()
        fn = funcs.get(name)
        if fn is None:
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
            for name, path in _reach(funcs, roots).items():
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
                async with _build_class_slot(st, label="x"):
                    pass
        """))
    got = {(f, tuple(p)) for f, p in _section_violations(planted)}
    assert ("a", ("helper", "claude_p")) in got
    assert ("b", ("_build_class_slot",)) in got


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
