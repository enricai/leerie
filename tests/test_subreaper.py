"""Tests for the orchestrator zombie-reaper (DESIGN §6 *Zombie reaping*).

The leerie container's PID 1 is `runuser`/the entrypoint (or an idle
`sleep infinity` on Fly), NOT a real init — it never `wait()`s arbitrary
orphans. Worker tool subtrees routinely orphan short-lived subprocesses
(git, ssh-agent, their children); without a subreaper those reparent to
PID 1 and rot as `<defunct>` zombies, each counting against the worker
cgroup's `pids.max` until it fills and every `fork()` EAGAINs. This was
captured live in production (453 defunct `git`, all ppid==1).

The fix: the orchestrator calls `_become_subreaper()` early in `main()`
(so orphaned descendants reparent to it) and runs `_zombie_reaper()` to
`waitpid` them. These tests verify both halves.
"""
from __future__ import annotations

import ast
import asyncio
import contextlib
import inspect
import os
import subprocess
import textwrap
import sys

import pytest

linux_only = pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="prctl(PR_SET_CHILD_SUBREAPER) and PID-reparenting are Linux-only.",
)


def test_become_subreaper_noop_off_linux_does_not_raise(leerie):
    """On any platform, `_become_subreaper()` must return a bool and never
    raise — on non-Linux it is a documented no-op (returns False)."""
    result = leerie._become_subreaper()
    assert isinstance(result, bool)
    if not sys.platform.startswith("linux"):
        assert result is False


@linux_only
def test_become_subreaper_sets_the_flag(leerie):
    """On Linux, `_become_subreaper()` installs the child-subreaper flag,
    verifiable by reading it back via prctl(PR_GET_CHILD_SUBREAPER)."""
    import ctypes

    assert leerie._become_subreaper() is True
    libc = ctypes.CDLL(None, use_errno=True)
    out = ctypes.c_int(0)
    rc = libc.prctl(leerie._PR_GET_CHILD_SUBREAPER, ctypes.byref(out), 0, 0, 0)
    assert rc == 0
    assert out.value == 1, "PR_GET_CHILD_SUBREAPER should read back 1 (set)"


@linux_only
def test_zombie_reaper_reaps_an_orphaned_child(leerie):
    """A child that exits without being wait()ed becomes a <defunct> zombie.
    One tick of `_zombie_reaper` must reap it so it no longer occupies a task
    slot. This is the exact mechanism that was filling the cgroup pids.max.

    We fork a direct child (so this test process is its parent and thus the
    one obligated to wait) that exits immediately, register it the way
    `_DescendantTracker._poll_loop` registers an observed descendant, then run
    the reaper and confirm it is gone.

    The registration is mandatory, not incidental: the reaper reaps only its
    `_REAPABLE_PIDS` allowlist and never discovers pids by scanning /proc
    (DESIGN §6 *Zombie reaping*). An unregistered zombie is invisible to it by
    design — that is what keeps it off asyncio's children.
    """
    pid = os.fork()
    if pid == 0:  # child
        os._exit(0)
    leerie._mark_reapable({pid})  # what _DescendantTracker does in production

    # Give the child a moment to exit and become a zombie.
    def _is_zombie(p: int) -> bool:
        try:
            with open(f"/proc/{p}/stat") as f:
                # field 3 (0-indexed 2) is state; 'Z' = zombie
                return f.read().split(")")[-1].split()[0] == "Z"
        except OSError:
            return False

    for _ in range(50):
        if _is_zombie(pid):
            break
        import time
        time.sleep(0.02)
    assert _is_zombie(pid), "child should be a zombie before reaping"

    async def _one_tick():
        task = asyncio.create_task(leerie._zombie_reaper(interval_sec=0.05))
        await asyncio.sleep(0.2)  # enough for at least one waitpid drain
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    try:
        asyncio.run(_one_tick())

        # After reaping, the pid must no longer exist at all (zombie cleared).
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
    finally:
        leerie._REAPABLE_PIDS.discard(pid)


def test_reaper_is_wired_into_orchestrate(leerie):
    """The reaper only helps if `orchestrate()` actually spawns AND cancels it
    (mirroring `_memory_sampler`'s lifecycle). Source-pin the wiring so a
    refactor can't silently drop it — the fix is inert without this."""
    import inspect

    src = inspect.getsource(leerie.orchestrate)
    assert "_zombie_reaper()" in src, (
        "orchestrate() must spawn _zombie_reaper() as a background task")
    assert "reaper_task = asyncio.create_task" in src, (
        "reaper task must be created like sampler_task")
    assert "reaper_task.cancel()" in src, (
        "reaper task must be cancelled in orchestrate()'s finally")


def test_main_installs_subreaper(leerie):
    """`main()` must call `_become_subreaper()` before spawning workers, or
    orphans never reparent to us and the reaper has nothing to reap."""
    import inspect

    src = inspect.getsource(leerie.main)
    assert "_become_subreaper()" in src, (
        "main() must call _become_subreaper() early, before orchestrate()")


@linux_only
def test_zombie_reaper_does_not_steal_unregistered_subprocess_status(leerie):
    """THE regression test. The reaper must not steal the exit status of an
    asyncio child that was NEVER registered anywhere.

    This is the production failure (DESIGN §6 *Zombie reaping*): `run_proc`
    does not register its pids, and even `_invoke` cannot register during the
    window between `fork()` and asyncio's `os.pidfd_open()`. A scanning reaper
    took `preflight`'s own `git config user.email` pid on 40/40 real runs,
    making CPython fabricate returncode 255, which `preflight` misreported as
    "git user.email is not configured" on a correctly-seeded machine.

    No registration here, deliberately — that is the whole point. Safety must
    come from the reaper only reaping its `_REAPABLE_PIDS` allowlist. Run the
    reaper hot (1ms) across many short-lived children: a scanning reaper fails
    this in a handful of iterations (measured 246/300 on Fly); an allowlist
    reaper cannot fail it at all."""
    async def _run() -> list[int]:
        reaper = asyncio.create_task(leerie._zombie_reaper(interval_sec=0.001))
        codes = []
        try:
            for _ in range(40):
                proc = await asyncio.create_subprocess_exec(
                    "sh", "-c", "exit 7",
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                codes.append(await proc.wait())
        finally:
            reaper.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await reaper
        return codes

    codes = asyncio.run(_run())
    stolen = [c for c in codes if c != 7]
    assert not stolen, (
        f"{len(stolen)}/{len(codes)} exit codes were stolen by the reaper "
        f"(got {sorted(set(stolen))}, expected 7) — the reaper must reap only "
        "pids on its _REAPABLE_PIDS allowlist, never scan for zombies")


@linux_only
def test_zombie_reaper_still_reaps_a_recorded_orphan(leerie):
    """The reaper must still do its job. Guards against a "fix" that is clean
    only because it reaps nothing (DESIGN §6: orphans pile up against
    pids.max). A pid recorded via `_mark_reapable` must actually be reaped."""
    pid = os.fork()
    if pid == 0:
        os._exit(0)

    async def _run() -> None:
        leerie._mark_reapable({pid})
        reaper = asyncio.create_task(leerie._zombie_reaper(interval_sec=0.01))
        try:
            for _ in range(100):
                await asyncio.sleep(0.02)
                if pid not in leerie._REAPABLE_PIDS:
                    return
        finally:
            reaper.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await reaper

    try:
        asyncio.run(_run())
        assert pid not in leerie._REAPABLE_PIDS, (
            "reaper never reaped a recorded orphan — an allowlist reaper that "
            "reaps nothing is not a fix, it is a disabled reaper")
    finally:
        leerie._REAPABLE_PIDS.discard(pid)
        try:
            os.waitpid(pid, 0)
        except (ChildProcessError, OSError):
            pass


def test_zombie_reaper_never_scans_proc_for_zombies(leerie):
    """Source-coupling guard for the design invariant (DESIGN §6 *Zombie
    reaping*). Behavior tests are timing-dependent; this one is not.

    A reaper that discovers pids by scanning is wrong no matter how it filters:
    a pid between `fork()` and asyncio's `pidfd_open()` is in no registry, so
    every exclusion has a hole (measured: excluding asyncio-known pids still
    corrupted 212/300). The reaper must only ever consult `_REAPABLE_PIDS`."""
    # Strip the docstring: it *describes* the /proc scan this function must
    # not do, so a naive substring check would match the prose, not the code.
    tree = ast.parse(textwrap.dedent(inspect.getsource(leerie._zombie_reaper)))
    fn = tree.body[0]
    if (fn.body and isinstance(fn.body[0], ast.Expr)
            and isinstance(fn.body[0].value, ast.Constant)
            and isinstance(fn.body[0].value.value, str)):
        fn.body = fn.body[1:]
    code = ast.unparse(fn)

    assert "_REAPABLE_PIDS" in code, (
        "the reaper must reap from the _REAPABLE_PIDS allowlist")
    for forbidden in ("/proc", "listdir", "_orphan_zombie_children"):
        assert forbidden not in code, (
            f"the reaper must not discover pids by scanning ({forbidden!r} "
            "found in its source) — an allowlist is the only correct shape")


def test_descendant_tracker_publishes_to_reaper_allowlist(leerie):
    """The fix is inert without this wiring: if the tracker stops publishing,
    `_REAPABLE_PIDS` stays empty and the reaper silently reaps nothing, so
    orphans pile up against pids.max with no other signal."""
    src = inspect.getsource(leerie._DescendantTracker._poll_loop)
    assert "_mark_reapable" in src, (
        "_DescendantTracker._poll_loop must publish observed descendants to "
        "the reaper's allowlist via _mark_reapable")


def test_mark_reapable_never_admits_an_asyncio_pid(leerie):
    """`_mark_reapable` must drop anything asyncio owns, even if a caller
    passes it in. Asyncio's watcher owns those exit statuses."""
    leerie._REAPABLE_PIDS.clear()
    leerie._ASYNCIO_MANAGED_PIDS.add(424242)
    try:
        leerie._mark_reapable({424242, 424243})
        assert 424242 not in leerie._REAPABLE_PIDS, (
            "an asyncio-managed pid must never enter the reap allowlist")
        assert 424243 in leerie._REAPABLE_PIDS
    finally:
        leerie._ASYNCIO_MANAGED_PIDS.discard(424242)
        leerie._REAPABLE_PIDS.clear()


def test_reparented_orphans_accepts_orchestrator_ppid(leerie, monkeypatch):
    """After `_become_subreaper`, orphans reparent to the orchestrator, not
    PID 1. `_reparented_orphans` must accept `ppid == os.getpid()` in addition
    to `ppid == 1`, or the mid-run reaper misses every reparented orphan."""
    import os as _os
    min_age = leerie._PID_REAP_MIN_AGE_SEC
    me = _os.getpid()
    fake_ps = (
        "  PID  PPID ELAPSED\n"
        f"  300 {me} {min_age + 10}\n"   # reparented to orchestrator — accept
        f"  301     1 {min_age + 10}\n"  # reparented to init — accept
        f"  302     2 {min_age + 10}\n"  # attached elsewhere — reject
    )

    def fake_run(cmd, **kwargs):
        class R:
            stdout = fake_ps
        return R()

    monkeypatch.setattr(leerie.subprocess, "run", fake_run)
    result = leerie._reparented_orphans({300, 301, 302})
    assert set(result) == {300, 301}, f"got {result}"


@linux_only
def test_run_proc_returns_true_rc_under_inherited_sigchld_ignore():
    """An inherited `SIGCHLD=SIG_IGN` makes the *kernel* auto-reap our children,
    so asyncio's PidfdChildWatcher `waitpid`s a pid that is already gone, gets
    ChildProcessError, and invents **returncode 255 with empty stdout**.

    That fake 255 is what made `preflight()`'s first check —
    `git config user.email` — die with "git user.email is not configured" on a
    machine whose identity was perfectly configured. Git was never involved: the
    *first* subprocess of the process is the one that loses its exit status, and
    the git check merely happened to be first.

    `main()` must therefore reset SIGCHLD to SIG_DFL before spawning anything.
    This test asserts the true exit code (7) survives, not 255.

    Runs in a subprocess: SIG_IGN is process-wide and would poison the rest of
    the pytest session (every later `subprocess.run` would lose its status).
    """
    script = """
import asyncio, importlib.util, signal, sys
signal.signal(signal.SIGCHLD, signal.SIG_IGN)   # what a parent (ssh/hallpass) can leave us
spec = importlib.util.spec_from_file_location("lp", sys.argv[1])
lp = importlib.util.module_from_spec(spec); spec.loader.exec_module(lp)
lp._restore_sigchld_default()                          # the fix under test
async def m():
    # Without the reset this does not merely return the wrong code: asyncio
    # raises (ProcessLookupError / CancelledError) because the child is gone
    # before it can be waited on. Report either failure shape as non-"7".
    try:
        r = await lp.run_proc(["sh", "-c", "exit 7"])
        print(r.returncode)
    except BaseException as e:
        print(f"RAISED:{type(e).__name__}")
asyncio.run(m())
"""
    target = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "orchestrator", "leerie.py")
    out = subprocess.run([sys.executable, "-c", script, target],
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, f"probe failed: {out.stderr[-2000:]}"
    rc = out.stdout.strip().splitlines()[-1]
    assert rc == "7", (
        f"run_proc reported {rc!r}, expected '7' — the child's exit status was "
        "lost to kernel auto-reaping (verified failure modes without the fix: "
        "rc=255, or RAISED:ProcessLookupError). _restore_sigchld_default() must "
        "restore SIGCHLD to SIG_DFL before any subprocess is spawned.")


@linux_only
def test_zombie_reaper_survives_no_children(leerie):
    """With no children to wait on, the reaper must not crash — it swallows
    ChildProcessError and keeps looping."""
    async def _run():
        task = asyncio.create_task(leerie._zombie_reaper(interval_sec=0.05))
        await asyncio.sleep(0.15)
        assert not task.done(), "reaper must keep running when there are no children"
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(_run())
