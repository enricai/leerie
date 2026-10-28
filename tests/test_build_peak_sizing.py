"""F1 sizing (DESIGN §6 *The repo's own build peak*, *A host-wide build
semaphore*, *At the measured ceiling, a resource kill is an environment
limit*).

The baseline measures each BLT command's `memory.peak` / `pids.peak` in an
uncapped broker cgroup; the run raises its worker caps to `max(ceiling, 1.25
x peak + 0.3 GiB)` and `max(pids cap, ceil(1.5 x pids peak))`, and sizes a
host-wide flock semaphore `floor((MemAvailable - 4 GiB) / peak)`, never
fewer than 1, that only build/test commands take.
"""
from __future__ import annotations

import asyncio
import fcntl
import math
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.conftest import _run

GIB = 1024 ** 3


class _St:
    def __init__(self, tmp_path: Path, data: dict | None = None):
        self.run_dir = tmp_path / "state" / "runs" / "r1"
        self.run_dir.mkdir(parents=True)
        self.run_id = "r1"
        self.data = data or {}
        self.saves = 0

    def save(self):
        self.saves += 1


# ---- caps raised to the measured peak --------------------------------------

def test_benched_funeralworks_peak_gives_the_benched_caps(leerie, tmp_path):
    """Bench F4/P4: peak 17.81 GiB / 2852 pids -> 22.56 GiB / 4278 pids."""
    st = _St(tmp_path, {"blt_peak": {"memory_bytes": int(17.81 * GIB),
                                     "pids": 2852}})
    caps = {"worker_memory_max_bytes": 10624 * 1024 ** 2,
            "worker_pids_max": 2048}
    leerie._apply_blt_peak(st, caps)
    assert round(caps["worker_memory_max_bytes"] / GIB, 2) == 22.56
    assert caps["worker_pids_max"] == 4278 == math.ceil(1.5 * 2852)
    assert st.data["blt_peak"]["worker_memory_max_bytes"] == \
        caps["worker_memory_max_bytes"]


def test_caps_are_only_ever_raised(leerie, tmp_path):
    st = _St(tmp_path, {"blt_peak": {"memory_bytes": 2 * GIB, "pids": 100}})
    caps = {"worker_memory_max_bytes": 12 * GIB, "worker_pids_max": 2048}
    leerie._apply_blt_peak(st, caps)
    assert caps == {"worker_memory_max_bytes": 12 * GIB,
                    "worker_pids_max": 2048}


def test_uncapped_runs_stay_uncapped(leerie, tmp_path):
    """No memory cap resolved (no containment): the peak does not invent
    one."""
    st = _St(tmp_path, {"blt_peak": {"memory_bytes": 9 * GIB, "pids": 10}})
    caps = {"worker_memory_max_bytes": None, "worker_pids_max": 2048}
    leerie._apply_blt_peak(st, caps)
    assert caps["worker_memory_max_bytes"] is None


def test_record_takes_the_largest_axis_peak(leerie, tmp_path, monkeypatch):
    monkeypatch.setattr(leerie, "_mem_available_bytes", lambda: 64 * GIB)
    st = _St(tmp_path)
    caps = {"worker_memory_max_bytes": 8 * GIB, "worker_pids_max": 2048}
    axes = {
        "build": {"ran": True, "peak": {"memory_bytes": 6 * GIB,
                                        "pids": 3000}},
        "lint": {"ran": True, "peak": {"memory_bytes": 1 * GIB, "pids": 50}},
        "tests": {"ran": True, "peak": {"memory_bytes": 10 * GIB,
                                        "pids": 900}},
        "none": {"ran": False},
    }
    leerie._record_blt_peak(st, axes, caps)
    peak = st.data["blt_peak"]
    assert peak["memory_bytes"] == 10 * GIB and peak["axis"] == "tests"
    # pids peak is the largest over every axis, not the memory winner's
    assert peak["pids"] == 3000
    assert peak["slots"] == (64 - 4) // 10
    assert caps["worker_memory_max_bytes"] == int(12.5 * GIB) + int(0.3 * GIB)
    assert caps["worker_pids_max"] == 4500


def test_record_without_any_peak_writes_nothing(leerie, tmp_path):
    st = _St(tmp_path)
    caps = {"worker_memory_max_bytes": 8 * GIB, "worker_pids_max": 2048}
    leerie._record_blt_peak(st, {"build": {"ran": True}}, caps)
    assert "blt_peak" not in st.data
    assert caps["worker_memory_max_bytes"] == 8 * GIB


@pytest.mark.parametrize("avail,peak,slots", [
    (64 * GIB, 17 * GIB, 3),
    (20 * GIB, 17 * GIB, 1),   # one build that fits is admitted
    (8 * GIB, 17 * GIB, 1),    # one that does not still runs, alone
    (None, 17 * GIB, 1),       # unreadable meminfo
    (64 * GIB, 0, 1),
])
def test_slot_count(leerie, monkeypatch, avail, peak, slots):
    # None: the live read, made unreadable.
    monkeypatch.setattr(leerie, "_mem_available_bytes", lambda: None)
    assert leerie._build_sem_slots(peak, mem_available=avail) == slots


def test_mem_available_parses_meminfo(leerie, tmp_path):
    p = tmp_path / "meminfo"
    p.write_text("MemTotal: 100 kB\nMemAvailable:    2048 kB\n")
    assert leerie._mem_available_bytes(str(p)) == 2048 * 1024
    assert leerie._mem_available_bytes(str(tmp_path / "absent")) is None


# ---- the host-wide semaphore ------------------------------------------------

def test_slots_are_shared_through_the_host_dir(leerie, tmp_path, monkeypatch):
    host = tmp_path / "host"
    host.mkdir()
    monkeypatch.setenv(leerie._HOST_DIR_ENV, str(host))
    st = _St(tmp_path, {"blt_peak": {"slots": 2}})
    assert leerie._build_sem_dir(st) == host / "build-slots"

    async def go():
        async with leerie._build_class_slot(st, label="a") as a, \
                leerie._build_class_slot(st, label="b") as b:
            return a, b
    assert sorted(_run(go())) == [0, 1]


def test_without_the_mount_slots_live_under_the_state_root(leerie, tmp_path,
                                                           monkeypatch):
    monkeypatch.delenv(leerie._HOST_DIR_ENV, raising=False)
    st = _St(tmp_path)
    assert leerie._build_sem_dir(st) == \
        tmp_path / "state" / ".host" / "build-slots"


def test_a_full_semaphore_waits_and_a_dead_holder_frees_its_slot(
        leerie, tmp_path, monkeypatch):
    """Another process (another run's container) holds the only slot; the
    acquirer waits, and gets it once that process is gone — the kernel
    drops a flock with its holder, even on SIGKILL."""
    host = tmp_path / "host"
    (host / "build-slots").mkdir(parents=True)
    monkeypatch.setenv(leerie._HOST_DIR_ENV, str(host))
    st = _St(tmp_path, {"blt_peak": {"slots": 1}})
    holder = subprocess.Popen(
        [sys.executable, "-c",
         "import fcntl, os, sys, time; "
         "fd = os.open(sys.argv[1], os.O_RDONLY | os.O_CREAT, 0o644); "
         "fcntl.flock(fd, fcntl.LOCK_EX); print('held', flush=True); "
         "time.sleep(60)", str(host / "build-slots" / "slot-0")],
        stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "held"
        sleeps: list = []

        async def _sleep(sec):
            sleeps.append(sec)
            if len(sleeps) == 2:
                holder.kill()
                holder.wait()
            await asyncio.sleep(0)
        fd, slot, n = _run(leerie._acquire_build_slot(st, label="x",
                                                      sleep=_sleep))
        try:
            assert (slot, n) == (0, 1)
            assert len(sleeps) == 2  # waited while the holder lived
        finally:
            os.close(fd)
    finally:
        if holder.poll() is None:
            holder.kill()
            holder.wait()


def test_a_slot_file_another_identity_made_still_locks(leerie, tmp_path,
                                                       monkeypatch):
    """Slot files are opened read-only, so one that is not writable to
    this identity (made by another run's identity) still serves."""
    host = tmp_path / "host"
    (host / "build-slots").mkdir(parents=True)
    slot = host / "build-slots" / "slot-0"
    slot.write_text("")
    slot.chmod(0o444)
    monkeypatch.setenv(leerie._HOST_DIR_ENV, str(host))
    st = _St(tmp_path, {"blt_peak": {"slots": 1}})
    fd, idx, _ = _run(leerie._acquire_build_slot(st, label="x"))
    try:
        assert idx == 0
        with pytest.raises(BlockingIOError):
            other = os.open(str(slot), os.O_RDONLY)
            try:
                fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
            finally:
                os.close(other)
    finally:
        os.close(fd)


def test_an_unusable_dir_runs_ungated(leerie, tmp_path, monkeypatch):
    monkeypatch.delenv(leerie._HOST_DIR_ENV, raising=False)
    st = _St(tmp_path)
    blocker = tmp_path / "state" / ".host"
    blocker.write_text("not a dir")  # mkdir of build-slots fails
    fd, slot, _ = _run(leerie._acquire_build_slot(st, label="x"))
    assert (fd, slot) == (None, None)


def test_only_build_and_test_axes_take_a_slot(leerie, tmp_path, monkeypatch):
    import contextlib
    taken: list = []

    @contextlib.asynccontextmanager
    async def _slot(st, *, label):
        taken.append(label)
        yield 0
    monkeypatch.setattr(leerie, "_build_class_slot", _slot)

    async def _measure(axis, cmd, tree, **kw):
        return {"ran": True, "measured": True, "passed": True,
                "command": cmd, "summary": ""}
    monkeypatch.setattr(leerie, "_measure_blt", _measure)

    async def _deps(*a, **k):
        return True
    monkeypatch.setattr(leerie, "_ensure_worktree_deps", _deps)
    st = _St(tmp_path)
    _run(leerie._measure_axes(
        str(tmp_path), {"build": "b", "lint": "l", "tests": "t"}, st,
        dict(leerie.DEFAULT_CAPS), log_path=tmp_path / "log",
        verbosity="quiet", memo=False))
    assert taken == ["baseline-build", "baseline-tests"]


def test_measure_peak_reaches_the_contained_run(leerie, tmp_path, monkeypatch):
    seen: dict = {}

    async def _contained(argv, **kw):
        seen.update(kw, argv=argv)
        return {"rc": 0, "tail": "ok", "timed_out": False, "contained": True,
                "oom_kill": 0, "pids_denied": 0,
                "peak": {"memory_bytes": 5 * GIB, "pids": 321}}
    monkeypatch.setattr(leerie, "_run_contained", _contained)
    res = _run(leerie._measure_blt(
        "build", "pnpm run build", str(tmp_path), timeout=10,
        log_path=tmp_path / "log", verbosity="quiet", measure_peak=True,
        run_id="abc", forbidden=[]))
    assert res["passed"] is True
    assert res["peak"] == {"memory_bytes": 5 * GIB, "pids": 321}
    assert seen["argv"] == ["bash", "-c", "pnpm run build"]
    assert (seen["memory_max_bytes"], seen["pids_max"]) == (0, 0)
    assert seen["measure"] is True and seen["run_id"] == "abc"


# ---- environment limit at the measured ceiling -----------------------------

def test_worker_kill_at_the_measured_ceiling_retries_once_then_unconverges(
        leerie, tmp_path):
    st = _St(tmp_path, {"blt_peak": {"memory_bytes": GIB, "pids": 1}})
    st.data["subtask_status"] = {}
    assert leerie._environment_limit_hit(st, "s1", "oom", 1) is None
    assert st.data["subtask_status"]["s1"] == "in_progress"
    res = leerie._environment_limit_hit(st, "s1", "oom", 2)
    assert res["status"] == "unconverged"
    assert st.data["unconverged_subtasks"]["s1"]["kind"] == \
        "environment_limit"


def test_unsized_runs_keep_the_ordinary_oom_retry(leerie, tmp_path):
    assert leerie._at_measured_ceiling(_St(tmp_path)) is False
    assert leerie._at_measured_ceiling(
        _St(tmp_path / "b", {"blt_peak": {"memory_bytes": 1}})) is True


def test_unshared_host_user_can_use_its_own_dir(leerie, tmp_path,
                                                monkeypatch):
    """The rootless shape, live: a process in a nested user namespace whose
    single map entry makes the outer owner its own uid (`unshare --user
    --map-user`, as container-entry.sh drops to leerie) can create and lock
    slot files in a directory the outer identity owns."""
    if shutil.which("unshare") is None:
        pytest.skip("unshare not available")
    host = tmp_path / "host"
    host.mkdir()
    probe = subprocess.run(
        ["unshare", "--user", "--map-user=4242", "--map-group=4242",
         sys.executable, "-c",
         "import fcntl, os, sys; d = os.path.join(sys.argv[1], "
         "'build-slots'); os.makedirs(d, exist_ok=True); "
         "fd = os.open(os.path.join(d, 'slot-0'), os.O_RDONLY | os.O_CREAT, "
         "0o644); fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB); "
         "print(os.getuid())", str(host)],
        capture_output=True, text=True)
    if probe.returncode != 0 and "Operation not permitted" in probe.stderr:
        pytest.skip("unprivileged user namespaces are disabled here")
    assert probe.returncode == 0, probe.stderr
    assert probe.stdout.strip() == "4242"
    assert (host / "build-slots" / "slot-0").exists()


# ---- the baseline measures, records and applies; resume re-applies ---------

def _baseline_harness(leerie, tmp_path, monkeypatch, data):
    import types
    leerie_dir = tmp_path / "leerie_dir"
    (leerie_dir / "worktrees" / "staging").mkdir(parents=True)
    run_dir = tmp_path / "run"
    (run_dir / "logs").mkdir(parents=True)
    st = types.SimpleNamespace(run_dir=run_dir, run_id="r1",
                               repo_root=tmp_path / "repo", data=data,
                               save=lambda: None)
    monkeypatch.setattr(leerie, "resolve_blt", lambda root: {
        "build": "make build", "lint": "make lint", "test": "pytest"})
    monkeypatch.setattr(leerie, "_write_run_json", lambda *a, **k: None)
    monkeypatch.setattr(leerie, "_mem_available_bytes", lambda: 40 * GIB)
    return leerie_dir, st


def test_baseline_measures_every_axis_and_raises_the_caps(
        leerie, tmp_path, monkeypatch):
    leerie_dir, st = _baseline_harness(
        leerie, tmp_path, monkeypatch,
        {"provision": {"recipe": []}, "verbosity": "quiet"})
    measured: list = []

    async def _contained(argv, **kw):
        measured.append((argv[2], kw["measure"], kw["memory_max_bytes"]))
        mem = {"make build": 9, "make lint": 1, "pytest": 3}[argv[2]] * GIB
        return {"rc": 0, "tail": "ok", "timed_out": False, "contained": True,
                "oom_kill": 0, "pids_denied": 0,
                "peak": {"memory_bytes": mem, "pids": 2500}}
    monkeypatch.setattr(leerie, "_run_contained", _contained)
    caps = {"worker_memory_max_bytes": 8 * GIB, "worker_pids_max": 2048}

    _run(leerie._capture_conformance_baseline(leerie_dir, st, caps))

    assert sorted(m[0] for m in measured) == ["make build", "make lint",
                                              "pytest"]
    assert all(m[1] is True and m[2] == 0 for m in measured)
    assert st.data["blt_peak"]["axis"] == "build"
    assert st.data["blt_peak"]["slots"] == (40 - 4) // 9
    assert caps["worker_memory_max_bytes"] == int(1.25 * 9 * GIB) + \
        int(0.3 * GIB)
    assert caps["worker_pids_max"] == 3750


def test_resume_reapplies_the_recorded_peak(leerie, tmp_path, monkeypatch):
    leerie_dir, st = _baseline_harness(leerie, tmp_path, monkeypatch, {
        "conformance": {"_baseline": {"axes": {}, "red_axes": []}},
        "blt_peak": {"memory_bytes": 9 * GIB, "pids": 2500}})

    async def _never(*a, **k):
        raise AssertionError("a resumed baseline must not re-measure")
    monkeypatch.setattr(leerie, "_run_contained", _never)
    caps = {"worker_memory_max_bytes": 8 * GIB, "worker_pids_max": 2048}

    _run(leerie._capture_conformance_baseline(leerie_dir, st, caps))

    assert caps["worker_pids_max"] == 3750
    assert caps["worker_memory_max_bytes"] > 11 * GIB


def test_peak_client_degrades_to_none(leerie, monkeypatch):
    assert leerie._cgroup_peak(None) is None

    def _down(payload, timeout=5.0):
        raise OSError("no broker")
    monkeypatch.setattr(leerie, "_cgroup_request", _down)
    assert leerie._cgroup_peak("x") is None
