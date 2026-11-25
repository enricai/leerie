"""A worker's share of the CPUs and of the memory is one share (DESIGN §6).

Barnacle 0.100.0 on a 160-CPU host: a worker capped at a fixed 10.1 GiB saw
every CPU, so its vitest selection started 80 forks (`maxWorkers: 50%`) and
was OOM-killed with 9.7 GB in 113 node and 20 esbuild processes — five such
kills across #748, #795 and #796, #796's taking every fix its delivery
re-drive had left. What a worker's tools spawn follows the CPUs they see, so
every capped process gets 1/max_parallel of the CPUs as an affinity slot,
and its ceiling is 1/max_parallel of the slice (tests/test_slice_aware_memory.py).

Real processes, real affinity, real `flock`s; only the cgroup broker is
stubbed (as in tests/test_spawn_held_enrollment.py).
"""
from __future__ import annotations

import fcntl
import os
import subprocess
import sys

import pytest

from tests.conftest import _run

CPUS = sorted(os.sched_getaffinity(0))
needs_two_cpus = pytest.mark.skipif(
    len(CPUS) < 2, reason="a CPU share needs at least two CPUs to differ")


def _affinity_writer(path) -> list[str]:
    """A command whose only effect is writing its own CPU set to `path`."""
    return [sys.executable, "-c",
            "import os, pathlib, sys; pathlib.Path(sys.argv[1]).write_text("
            "','.join(map(str, sorted(os.sched_getaffinity(0)))))",
            str(path)]


def _stub_broker(leerie, monkeypatch):
    monkeypatch.setattr(leerie, "_cgroup_create", lambda sid, m, p: sid)
    monkeypatch.setattr(leerie, "_cgroup_enroll", lambda sid, pid: None)
    monkeypatch.setattr(leerie, "_cgroup_stat", lambda sid: (0, 0, 0, 0))
    monkeypatch.setattr(leerie, "_cgroup_destroy", lambda sid: None)


@pytest.fixture
def share(leerie, monkeypatch, tmp_path):
    """Every CPU its own slot (max_parallel = the CPU count), slots held in
    a fresh host directory; the in-process tally starts empty."""
    monkeypatch.setenv(leerie._HOST_DIR_ENV, str(tmp_path / "host"))
    monkeypatch.setattr(leerie, "_CPU_SLOTS_IN_USE", {})
    leerie._configure_cpu_share(len(CPUS))
    yield
    leerie._configure_cpu_share(1)


def _contained(leerie, tmp_path, out, memory_max_bytes):
    return _run(leerie._run_contained(
        _affinity_writer(out), grants=leerie.WorkerGrants(("/",), ()),
        cwd=str(tmp_path), name="exec-t1-1", run_id="abcdef123456",
        memory_max_bytes=memory_max_bytes, pids_max=64, timeout=30,
        log_path=tmp_path / "log", label="x"))


@needs_two_cpus
def test_a_capped_command_runs_on_its_share(leerie, tmp_path, monkeypatch,
                                            share):
    """The command's own process reads a CPU set one slot wide: what any
    tool it starts would size itself from."""
    _stub_broker(leerie, monkeypatch)
    out = tmp_path / "cpus"
    assert _contained(leerie, tmp_path, out, 1 << 30)["rc"] == 0
    seen = [int(c) for c in out.read_text().split(",")]
    assert len(seen) == 1 and seen[0] in CPUS


@needs_two_cpus
def test_an_uncapped_measurement_sees_every_cpu(leerie, tmp_path,
                                                monkeypatch, share):
    """A measurement (no memory ceiling) is not a share: it sees the host."""
    _stub_broker(leerie, monkeypatch)
    out = tmp_path / "cpus"
    assert _contained(leerie, tmp_path, out, 0)["rc"] == 0
    assert [int(c) for c in out.read_text().split(",")] == CPUS


@needs_two_cpus
def test_a_run_of_one_worker_sees_every_cpu(leerie, tmp_path, monkeypatch):
    """max_parallel 1: the share is the host, so nothing is pinned."""
    _stub_broker(leerie, monkeypatch)
    leerie._configure_cpu_share(1)
    out = tmp_path / "cpus"
    assert _contained(leerie, tmp_path, out, 1 << 30)["rc"] == 0
    assert [int(c) for c in out.read_text().split(",")] == CPUS


def test_concurrent_takes_get_distinct_slots_host_wide(leerie, monkeypatch,
                                                       tmp_path):
    """16 CPUs, max_parallel 4: four takes get four disjoint slots of four
    CPUs; a fifth shares the slot this process uses least; a slot held by
    ANOTHER process (another run's orchestrator) is never taken."""
    monkeypatch.setattr(leerie, "_host_cpu_list", lambda: list(range(16)))
    monkeypatch.setattr(leerie, "_CPU_SLOTS_IN_USE", {})
    leerie._configure_cpu_share(4)
    host = tmp_path / "host"
    try:
        # Another process holds slot 2 for its whole life.
        (host / "cpu-slots").mkdir(parents=True)
        other = subprocess.Popen(
            [sys.executable, "-c",
             "import fcntl, os, sys, time; f = os.open(sys.argv[1], "
             "os.O_RDONLY | os.O_CREAT, 0o644); fcntl.flock(f, "
             "fcntl.LOCK_EX); print('held', flush=True); time.sleep(60)",
             str(host / "cpu-slots" / "w4-2")], stdout=subprocess.PIPE)
        assert other.stdout.readline().strip() == b"held"
        takes = [leerie._take_cpu_share(f"w{i}", host_dir=host)
                 for i in range(3)]
        slots = sorted(t[1] for t in takes)
        assert slots == [0, 1, 3]
        assert all(t[0] is not None for t in takes)
        assert sorted(c for t in takes for c in t[2]) == \
            [*range(0, 8), *range(12, 16)]
        # Every slot held: the fourth shares, holding no lock.
        fd, slot, cpus = leerie._take_cpu_share("w3", host_dir=host)
        assert fd is None and slot in (0, 1, 2, 3) and len(cpus) == 4
        leerie._release_cpu_share(fd, slot)
        # A released slot is free again, host-wide.
        leerie._release_cpu_share(takes[0][0], takes[0][1])
        again = leerie._take_cpu_share("w9", host_dir=host)
        assert again[1] == takes[0][1] and again[0] is not None
        leerie._release_cpu_share(again[0], again[1])
        for t in takes[1:]:
            leerie._release_cpu_share(t[0], t[1])
        assert all(v == 0 for v in leerie._CPU_SLOTS_IN_USE.values())
    finally:
        other.kill()
        other.wait()
        leerie._configure_cpu_share(1)


def test_without_a_host_directory_this_process_spreads_its_own(
        leerie, monkeypatch):
    monkeypatch.delenv(leerie._HOST_DIR_ENV, raising=False)
    monkeypatch.setattr(leerie, "_host_cpu_list", lambda: list(range(8)))
    monkeypatch.setattr(leerie, "_CPU_SLOTS_IN_USE", {})
    leerie._configure_cpu_share(4)
    try:
        takes = [leerie._take_cpu_share(f"w{i}") for i in range(4)]
        assert sorted(t[1] for t in takes) == [0, 1, 2, 3]
        assert all(t[0] is None for t in takes)
    finally:
        leerie._configure_cpu_share(1)


def test_the_share_is_resolved_with_the_memory_caps(leerie, monkeypatch,
                                                    tmp_path):
    """`_resolve_memory_caps` — run at start and once the repository's
    commands are read — sets both halves from the same `max_parallel`."""
    monkeypatch.setattr(leerie, "_host_cpu_list", lambda: list(range(160)))
    monkeypatch.setattr(leerie, "_cgroup_slice_info",
                        lambda: (295785652224, 0, 0))
    monkeypatch.delenv(leerie.WORKER_MEMORY_MAX_ENV, raising=False)
    caps = {"max_parallel": 5, "worker_memory_max_cli": None}
    try:
        leerie._resolve_memory_caps(caps, tmp_path)
        assert leerie._WORKER_CPU_WIDTH == 32
        assert caps["worker_memory_max_bytes"] == 295785652224 // 5
    finally:
        leerie._configure_cpu_share(1)


def test_invoke_sets_the_share_while_the_worker_is_held(leerie, tmp_path,
                                                        monkeypatch):
    """The worker's affinity is set after its enroll and before its gate
    opens, so no process of the worker starts outside it; the slot is
    released when the worker's cgroup is destroyed."""
    from tests.test_spawn_held_enrollment import _Proc
    order: list = []
    monkeypatch.setattr(leerie, "_cgroup_create", lambda sid, m, p: sid)
    monkeypatch.setattr(leerie, "_cgroup_enroll",
                        lambda sid, pid: order.append("enroll") or None)
    monkeypatch.setattr(leerie, "_cgroup_destroy",
                        lambda sid: order.append("destroy"))
    monkeypatch.setattr(leerie, "_cgroup_stat", lambda sid: None)
    monkeypatch.setattr(leerie, "_take_cpu_share",
                        lambda name: order.append(("take", name))
                        or (None, 3, [6, 7]))
    monkeypatch.setattr(leerie, "_apply_cpu_share",
                        lambda pid, cpus, label: order.append(
                            ("apply", pid, cpus)))
    monkeypatch.setattr(leerie, "_release_cpu_share",
                        lambda fd, slot: order.append(("free", slot)))
    real_release = leerie._release_spawn_gate

    def _release(w, *, run=True):
        order.append(("open", run))
        real_release(w, run=run)
    monkeypatch.setattr(leerie, "_release_spawn_gate", _release)

    async def _spawn(*cmd, **kw):
        return _Proc(4321)
    monkeypatch.setattr("asyncio.create_subprocess_exec", _spawn)
    leerie_dir = tmp_path / ".leerie"
    (leerie_dir / "logs").mkdir(parents=True)
    with __import__("contextlib").suppress(Exception):
        _run(leerie._invoke(
            ["claude", "-p", "x"], cwd=str(tmp_path), timeout=60,
            sid="planner-s0", leerie_dir=leerie_dir, verbosity="quiet",
            worker_memory_max_bytes=1 << 30, worker_pids_max=64,
            run_id="deadbeef1234", grants=leerie.WorkerGrants(("/",), ())))
    assert order[:4] == ["enroll", ("take", "deadbeef1234-planner-s0"),
                         ("apply", 4321, [6, 7]), ("open", True)]
    assert order.index("destroy") < order.index(("free", 3))


@needs_two_cpus
def test_a_refused_affinity_leaves_every_cpu(leerie, tmp_path, monkeypatch,
                                             share):
    """A share the kernel refuses is logged, and the command still runs."""
    _stub_broker(leerie, monkeypatch)

    def _refuse(pid, cpus):
        raise PermissionError(1, "Operation not permitted")
    monkeypatch.setattr(os, "sched_setaffinity", _refuse)
    logged: list = []
    monkeypatch.setattr(leerie, "log", lambda m: logged.append(m))
    out = tmp_path / "cpus"
    assert _contained(leerie, tmp_path, out, 1 << 30)["rc"] == 0
    assert [int(c) for c in out.read_text().split(",")] == CPUS
    assert any("could not set its CPU share" in m for m in logged)
