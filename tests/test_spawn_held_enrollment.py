"""Spawn-held enrollment (DESIGN §6 *Enrollment holds the spawn*, plan row F1).

A child is born in its parent's cgroup at fork time, so enrolling a process
after it is already running races its own first forks — measured, 71 of 72
build descendants escaped on rootful. Every enrolled process now starts as a
gate blocked on a pipe; it is enrolled, then released to `exec`.

The substance tests spawn REAL processes through `_run_streaming` and
`_invoke`; only the broker round-trips are stubbed, and the stub checks — at
the moment the broker would enroll — that the command has not run yet.
"""
from __future__ import annotations

import json
import os
import sys
import time

from tests.conftest import _run


def _writer(path) -> list[str]:
    """A command whose only effect is creating `path`."""
    return [sys.executable, "-c",
            "import pathlib, sys; pathlib.Path(sys.argv[1]).write_text('ran')",
            str(path)]


def test_command_cannot_run_before_it_is_enrolled(leerie, tmp_path,
                                                  monkeypatch):
    """At enroll time the command has not run, even after a pause long
    enough for an un-held child to have finished; after the release it
    runs. Enroll-after-spawn fails this: the marker exists at enroll."""
    marker = tmp_path / "marker"
    seen: list = []

    def _enroll(sid, pid):
        time.sleep(0.6)
        seen.append((sid, pid, marker.exists()))
        return None
    monkeypatch.setattr(leerie, "_cgroup_enroll", _enroll)

    rc, _tail = _run(leerie._run_streaming(
        _writer(marker), cwd=str(tmp_path), verbosity="quiet",
        cgroup_sid="run-cmd"))

    assert rc == 0
    assert len(seen) == 1
    sid, pid, existed = seen[0]
    assert sid == "run-cmd" and pid > 0
    assert existed is False, "the command ran before it was enrolled"
    assert marker.read_text() == "ran"


def test_exec_keeps_the_enrolled_pid(leerie, tmp_path, monkeypatch):
    """The pid the broker enrolled is the command's own pid — `exec`, not a
    fork — so the cgroup it was enrolled into is the command's."""
    out = tmp_path / "pid"
    pids: list = []
    monkeypatch.setattr(leerie, "_cgroup_enroll",
                        lambda sid, pid: pids.append(pid) or None)
    rc, _ = _run(leerie._run_streaming(
        [sys.executable, "-c",
         "import os, pathlib, sys; "
         "pathlib.Path(sys.argv[1]).write_text(str(os.getpid()))", str(out)],
        cwd=str(tmp_path), verbosity="quiet", cgroup_sid="run-cmd"))
    assert rc == 0
    assert pids == [int(out.read_text())]


def test_a_cancelled_release_never_runs_the_command(leerie, tmp_path,
                                                    monkeypatch):
    """If enrollment is interrupted, the gate is closed without a release
    and exits without running anything."""
    marker = tmp_path / "marker"

    def _enroll(sid, pid):
        raise KeyboardInterrupt("interrupted mid-enroll")
    monkeypatch.setattr(leerie, "_cgroup_enroll", _enroll)

    raised = False
    try:
        _run(leerie._run_streaming(_writer(marker), cwd=str(tmp_path),
                                   verbosity="quiet", cgroup_sid="run-cmd"))
    except KeyboardInterrupt:
        raised = True
    assert raised
    time.sleep(0.5)
    assert not marker.exists()


def test_gate_refuses_anything_but_the_release_byte(leerie, tmp_path):
    marker = tmp_path / "marker"
    argv, r, w = leerie._spawn_held_argv(_writer(marker))
    import subprocess
    proc = subprocess.Popen(argv, pass_fds=(r,))
    os.close(r)
    leerie._release_spawn_gate(w, run=False)
    assert proc.wait(timeout=10) == leerie._SPAWN_GATE_REFUSED_RC
    assert not marker.exists()


def test_gate_reports_a_missing_executable(leerie, tmp_path):
    import subprocess
    argv, r, w = leerie._spawn_held_argv(["leerie-no-such-binary-xyz"])
    proc = subprocess.Popen(argv, pass_fds=(r,), stderr=subprocess.PIPE)
    os.close(r)
    leerie._release_spawn_gate(w)
    _, err = proc.communicate(timeout=10)
    assert proc.returncode == 127
    assert b"leerie-no-such-binary-xyz" in err


def test_without_a_cgroup_nothing_is_gated(leerie, tmp_path, monkeypatch):
    calls: list = []
    monkeypatch.setattr(leerie, "_cgroup_enroll",
                        lambda *a: calls.append(a) or None)
    marker = tmp_path / "marker"
    rc, _ = _run(leerie._run_streaming(_writer(marker), cwd=str(tmp_path),
                                       verbosity="quiet"))
    assert rc == 0 and marker.exists() and calls == []


# ---- _invoke: create, spawn held, enroll, release — in that order ----------

class _Proc:
    def __init__(self, pid):
        self.pid = pid
        self.returncode = 0
        self.stdout = _Lines([json.dumps({
            "type": "result", "subtype": "success", "is_error": False,
            "result": "ok", "structured_output": {"ok": True}})])
        self.stderr = _Lines([])

    async def wait(self):
        return 0

    def kill(self):
        pass

    def terminate(self):
        pass


class _Lines:
    def __init__(self, lines):
        self._lines = [(l + "\n").encode() for l in lines]

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._lines:
            raise StopAsyncIteration
        return self._lines.pop(0)

    async def read(self, n=-1):
        return b""


def test_invoke_creates_the_cgroup_before_spawning_held(leerie, tmp_path,
                                                        monkeypatch):
    order: list = []
    spawned: dict = {}

    def _create(sid, mem, pids):
        order.append("create")
        return sid
    monkeypatch.setattr(leerie, "_cgroup_create", _create)

    def _enroll(sid, pid):
        order.append(("enroll", pid))
        return None
    monkeypatch.setattr(leerie, "_cgroup_enroll", _enroll)
    monkeypatch.setattr(leerie, "_cgroup_destroy", lambda sid: None)
    monkeypatch.setattr(leerie, "_cgroup_stat", lambda sid: None)

    released: list = []
    real_release = leerie._release_spawn_gate

    def _release(w, *, run=True):
        order.append(("release", run))
        released.append(w)
        real_release(w, run=run)
    monkeypatch.setattr(leerie, "_release_spawn_gate", _release)

    async def _spawn(*cmd, **kw):
        order.append("spawn")
        spawned["cmd"] = cmd
        spawned["pass_fds"] = kw.get("pass_fds")
        return _Proc(4321)
    monkeypatch.setattr("asyncio.create_subprocess_exec", _spawn)

    leerie_dir = tmp_path / ".leerie"
    (leerie_dir / "logs").mkdir(parents=True)
    with __import__("contextlib").suppress(Exception):
        _run(leerie._invoke(
            ["claude", "-p", "x"], cwd=str(tmp_path), timeout=60,
            sid="planner-s0", leerie_dir=leerie_dir, verbosity="quiet",
            worker_memory_max_bytes=1 << 30, worker_pids_max=64,
            run_id="deadbeef1234"))

    assert order[:4] == ["create", "spawn", ("enroll", 4321),
                         ("release", True)]
    cmd = list(spawned["cmd"])
    # The worker argv rides behind the gate, unchanged.
    assert cmd[-3:] == ["claude", "-p", "x"]
    assert cmd[cmd.index("-c") + 1] == leerie._SPAWN_GATE_SRC
    gate_fd = int(cmd[cmd.index("-c") + 2])
    assert spawned["pass_fds"] == (gate_fd,)


def test_invoke_without_caps_spawns_the_worker_directly(leerie, tmp_path,
                                                        monkeypatch):
    spawned: dict = {}

    async def _spawn(*cmd, **kw):
        spawned["cmd"] = cmd
        return _Proc(99)
    monkeypatch.setattr("asyncio.create_subprocess_exec", _spawn)
    leerie_dir = tmp_path / ".leerie"
    (leerie_dir / "logs").mkdir(parents=True)
    with __import__("contextlib").suppress(Exception):
        _run(leerie._invoke(
            ["claude", "-p", "x"], cwd=str(tmp_path), timeout=60,
            sid="planner-s0", leerie_dir=leerie_dir, verbosity="quiet"))
    assert list(spawned["cmd"]) == ["claude", "-p", "x"]


def test_invoke_spawn_failure_destroys_the_precreated_cgroup(
        leerie, tmp_path, monkeypatch):
    destroyed: list = []
    monkeypatch.setattr(leerie, "_cgroup_create", lambda sid, m, p: sid)
    monkeypatch.setattr(leerie, "_cgroup_destroy",
                        lambda sid: destroyed.append(sid))

    async def _spawn(*cmd, **kw):
        raise OSError(11, "Resource temporarily unavailable")
    monkeypatch.setattr("asyncio.create_subprocess_exec", _spawn)
    leerie_dir = tmp_path / ".leerie"
    (leerie_dir / "logs").mkdir(parents=True)
    raised = False
    try:
        _run(leerie._invoke(
            ["claude", "-p", "x"], cwd=str(tmp_path), timeout=60,
            sid="planner-s0", leerie_dir=leerie_dir, verbosity="quiet",
            worker_memory_max_bytes=1 << 30, worker_pids_max=64,
            run_id="deadbeef1234"))
    except OSError:
        raised = True
    assert raised
    assert destroyed == ["deadbeef1234-planner-s0"]


def test_every_enroll_call_site_is_held(leerie):
    """Wherever leerie enrolls a process, the spawn is held: each function
    that calls `_cgroup_enroll` also builds the gate and releases it."""
    import inspect
    for fn in (leerie._invoke, leerie._run_streaming):
        src = inspect.getsource(fn)
        assert "_cgroup_enroll" in src
        spawn = src.index("_spawn_held_argv(")
        create_exec = src.index("asyncio.create_subprocess_exec(")
        enroll = src.index("_cgroup_enroll,")
        release = src.index("_release_spawn_gate(gate_w)")
        assert spawn < create_exec < enroll < release, fn.__name__
    # And no other function enrolls a process.
    callers = [name for name, obj in vars(leerie).items()
               if callable(obj) and getattr(obj, "__module__", None)
               == leerie.__name__ and name != "_cgroup_enroll"
               and "_cgroup_enroll," in (inspect.getsource(obj)
                                         if inspect.isfunction(obj) else "")]
    assert sorted(callers) == ["_invoke", "_run_streaming"]


def test_run_contained_measures_and_destroys(leerie, tmp_path, monkeypatch):
    """`_run_contained`: create (uncapped for a measurement), run held,
    read the peak, destroy — and a v1 `-1` pids peak falls back to the
    polled pids.current."""
    calls: list = []
    monkeypatch.setattr(leerie, "_cgroup_create",
                        lambda sid, m, p: calls.append(("create", sid, m, p))
                        or sid)
    monkeypatch.setattr(leerie, "_cgroup_enroll",
                        lambda sid, pid: calls.append(("enroll", sid)) or None)
    monkeypatch.setattr(leerie, "_cgroup_stat",
                        lambda sid: (7, -1, 0, 0))
    monkeypatch.setattr(leerie, "_cgroup_peak", lambda sid: (123456, -1))
    monkeypatch.setattr(leerie, "_cgroup_destroy",
                        lambda sid: calls.append(("destroy", sid)))
    out = _run(leerie._run_contained(
        [sys.executable, "-c", "import time; time.sleep(1.3)"],
        cwd=str(tmp_path), name="measure-build", run_id="abcdef123456zz",
        memory_max_bytes=0, pids_max=0, timeout=30,
        log_path=tmp_path / "log", label="m", measure=True))
    assert out["rc"] == 0 and out["contained"]
    assert out["peak"] == {"memory_bytes": 123456, "pids": 7}
    assert calls[0] == ("create", "abcdef123456-measure-build", 0, 0)
    assert calls[1] == ("enroll", "abcdef123456-measure-build")
    assert calls[-1] == ("destroy", "abcdef123456-measure-build")


def test_run_contained_reports_cgroup_kills(leerie, tmp_path, monkeypatch):
    monkeypatch.setattr(leerie, "_cgroup_create", lambda sid, m, p: sid)
    monkeypatch.setattr(leerie, "_cgroup_enroll", lambda sid, pid: None)
    monkeypatch.setattr(leerie, "_cgroup_stat", lambda sid: (0, 64, 5, 1))
    monkeypatch.setattr(leerie, "_cgroup_destroy", lambda sid: None)
    out = _run(leerie._run_contained(
        [sys.executable, "-c", "import sys; sys.exit(137)"],
        cwd=str(tmp_path), name="exec-t1-1", run_id=None,
        memory_max_bytes=1 << 30, pids_max=64, timeout=30,
        log_path=tmp_path / "log", label="x"))
    assert out["rc"] == 137
    assert out["oom_kill"] == 1 and out["pids_denied"] == 5
    assert out["peak"] is None


def test_run_contained_runs_uncontained_when_containment_is_off(
        leerie, tmp_path, monkeypatch):
    monkeypatch.setattr(leerie, "_cgroup_create", lambda *a: None)
    enrolled: list = []
    monkeypatch.setattr(leerie, "_cgroup_enroll",
                        lambda *a: enrolled.append(a) or None)
    out = _run(leerie._run_contained(
        [sys.executable, "-c", "print('hi')"], cwd=str(tmp_path),
        name="exec-t1-1", run_id=None, memory_max_bytes=1, pids_max=1,
        timeout=30, log_path=tmp_path / "log", label="x"))
    assert out["rc"] == 0 and not out["contained"] and enrolled == []
    assert "hi" in out["tail"]

