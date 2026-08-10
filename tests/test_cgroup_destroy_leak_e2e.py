"""End-to-end reproduction + fix pin for N18 (cgroup worker directory leak).

Unlike tests/test_cgroup_helpers.py (which stubs `_cgroup_request` and
never touches a real socket) and tests/test_cgroup_broker.py (which drives
the broker module in-process, never over a socket), this file runs a REAL
Unix-domain-socket server thread wrapping the actual `scripts/cgroup-broker.py`
module's `_handle()` dispatch against a fake cgroupfs, and drives
`leerie._cgroup_destroy` against it over the real socket path
(`leerie._CGROUP_BROKER_SOCK`). It asserts the resource is actually reaped
(the fake `leerie-w-<sid>` directory is gone from disk), not merely that
`_cgroup_destroy` returned without raising -- mirroring
tests/test_provision_volume.py's discipline for Fly volume reaping.

Root cause (N18): the broker's `destroy` verb runs a drain-then-rmdir loop
bounded by its own `_DESTROY_DRAIN_TIMEOUT_SEC` and keeps working toward
that rmdir for the full budget regardless of whether the client is still
listening -- it does not touch the socket during the drain. But the
client's `_cgroup_request` socket timeout for `destroy` was the bare 5.0s
default, UNDER the broker's 10.0s drain budget. A destroy that legitimately
needs more than 5s (a large worker subtree, e.g. a conformer's test-suite
process tree) made the client give up with a bare socket timeout --
swallowed silently by `contextlib.suppress(OSError)` in `_cgroup_destroy`,
with no log line (unlike a broker-reported `ERR ...`, which IS logged).
Nothing in the orchestrator was left waiting for the broker's still-running
drain, so run/container teardown could proceed and kill the broker (PID 1)
mid-drain. Because workers run with `--cgroupns=host`, the cgroup directory
lives on the HOST cgroupfs and outlives the container -- so a broker killed
mid-drain permanently orphans the `leerie-w-<sid>` directory rather than
merely delaying its removal. The fix: `_cgroup_destroy` now requests
`_CGROUP_DESTROY_TIMEOUT_SEC` (>= the broker's own drain budget), so the
client cannot move on (and let a container-teardown race kill the broker)
before the broker either finishes or the client itself has waited out the
broker's own worst case.
"""
from __future__ import annotations

import importlib.util
import os
import socket
import threading
import time
from pathlib import Path

import pytest

_BROKER_PATH = (Path(__file__).resolve().parent.parent
                / "scripts" / "cgroup-broker.py")


def _load_broker():
    spec = importlib.util.spec_from_file_location("cgroup_broker_e2e",
                                                  _BROKER_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _FakeBrokerServer:
    """A real Unix-socket server thread that dispatches through the real
    broker module's `_handle()` -- single connection at a time, mirroring
    the shipped broker's single-threaded accept loop (the property this
    incident depends on: a slow `destroy` blocks the broker from doing
    anything else, including finishing that same `destroy` any faster)."""

    def __init__(self, broker_mod, sock_path: str):
        self._mod = broker_mod
        self._sock_path = sock_path
        self._srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        if os.path.exists(sock_path):
            os.unlink(sock_path)
        self._srv.bind(sock_path)
        self._srv.listen(16)
        self._srv.settimeout(0.2)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()

    def _run(self):
        while not self._stop.is_set():
            try:
                conn, _ = self._srv.accept()
            except OSError:
                continue
            try:
                conn.settimeout(5.0)
                data = conn.recv(4096).decode(errors="replace")
                resp = self._mod._handle(data)
                conn.sendall((resp + "\n").encode())
            except OSError:
                pass
            finally:
                conn.close()

    def stop(self):
        self._stop.set()
        self._thread.join(timeout=2.0)
        self._srv.close()
        if os.path.exists(self._sock_path):
            os.unlink(self._sock_path)


@pytest.fixture
def fake_cgroupfs(tmp_path, monkeypatch):
    """Real broker module pointed at a tmp dir standing in for a v2
    unified cgroupfs, with drain timing shrunk to test speed but keeping
    the SAME ratio as production (broker drain budget > naive client
    default) so the race this test reproduces is the real one, just fast.

    On a real kernel cgroupfs, `cgroup.procs`/`cgroup.kill` are pre-existing
    pseudo-files that writing to (or reading from) never turns into a dentry
    blocking `rmdir` -- on this fake (plain-directory) cgroupfs they would,
    masking what these tests are about (same rationale as
    tests/test_cgroup_broker.py's `test_destroy_removes_dir` /
    `test_destroy_waits_for_procs_to_drain`). So `cgroup.kill` writes are
    stubbed to no-ops, and `cgroup.procs` reads are served from an in-memory
    per-sid "drains at" schedule instead of a real file."""
    mod = _load_broker()
    root = tmp_path / "cgroup"
    slice_dir = root / mod.SLICE
    slice_dir.mkdir(parents=True)
    (root / "cgroup.controllers").write_text("cpu memory pids")
    (slice_dir / "cgroup.subtree_control").write_text("")
    (slice_dir / "cgroup.controllers").write_text("memory pids")
    monkeypatch.setattr(mod, "V2_ROOT", str(root))
    monkeypatch.setattr(mod, "V1_ROOT", str(root))
    # Broker's own drain budget: 0.6s (test-speed stand-in for the real
    # 10.0s). A "slow subtree" in this test clears cgroup.procs at 0.35s --
    # comfortably inside this budget, so the broker itself always succeeds
    # once it gets to run its poll loop to completion.
    monkeypatch.setattr(mod, "_DESTROY_DRAIN_TIMEOUT_SEC", 0.6)
    monkeypatch.setattr(mod, "_DESTROY_DRAIN_POLL_SEC", 0.02)

    drain_at: dict[str, float] = {}
    orig_read = mod._read
    orig_write = mod._write

    def fake_read(path):
        if path.endswith("/cgroup.procs"):
            for sid, t in drain_at.items():
                if f"leerie-w-{sid}/cgroup.procs" in path:
                    return "" if time.monotonic() >= t else "4242\n"
            return ""
        return orig_read(path)

    def fake_write(path, *a, **kw):
        if path.endswith("cgroup.kill"):
            return None
        return orig_write(path, *a, **kw)

    monkeypatch.setattr(mod, "_read", fake_read)
    monkeypatch.setattr(mod, "_write", fake_write)
    mod._HIER = mod._detect()
    return mod, root, slice_dir, drain_at


def _seed_slow_draining_worker(slice_dir: Path, drain_at: dict, sid: str,
                                clear_after_sec: float) -> Path:
    """Create a fake leerie-w-<sid> dir and schedule its (fixture-stubbed)
    `cgroup.procs` to report empty after `clear_after_sec` -- standing in
    for a real worker subtree that takes measurable time to die after
    `cgroup.kill` (DESIGN §6, cgroup-broker.py's own `_drain_then_rmdir`
    docstring: SIGKILL -> fully-reaped is asynchronous and scales with tree
    size)."""
    d = slice_dir / f"leerie-w-{sid}"
    d.mkdir()
    drain_at[sid] = time.monotonic() + clear_after_sec
    return d


def test_destroy_with_fixed_timeout_actually_reaps_a_slow_draining_worker(
        leerie, fake_cgroupfs, monkeypatch):
    """The fix: with the client timeout scaled to the SAME margin as the
    shipped `_CGROUP_DESTROY_TIMEOUT_SEC` (comfortably >= the broker's own
    drain budget), `_cgroup_destroy` blocks until the broker actually
    finishes, and the directory is gone by the time the call returns --
    the resource is verifiably reaped, not merely that `destroy` was sent."""
    mod, root, slice_dir, drain_at = fake_cgroupfs
    sock_path = str(root / "broker.sock")
    srv = _FakeBrokerServer(mod, sock_path)
    srv.start()
    try:
        monkeypatch.setattr(leerie, "_CGROUP_BROKER_SOCK", sock_path)
        # Scaled client timeout: same >= relationship as production
        # (_CGROUP_DESTROY_TIMEOUT_SEC 15.0s vs broker's 10.0s), applied to
        # this test's 0.6s broker budget.
        monkeypatch.setattr(leerie, "_CGROUP_DESTROY_TIMEOUT_SEC", 0.9)

        d = _seed_slow_draining_worker(slice_dir, drain_at, "e2e-fixed", 0.35)
        assert d.is_dir()

        leerie._cgroup_destroy("e2e-fixed")

        assert not d.exists(), (
            "leerie-w-e2e-fixed still present after _cgroup_destroy "
            "returned -- the client must wait at least as long as the "
            "broker's own drain budget")
    finally:
        srv.stop()


def test_destroy_across_repeated_create_destroy_cycles_leaves_no_directories(
        leerie, fake_cgroupfs, monkeypatch):
    """Success criterion, directly: repeated create/destroy cycles against a
    real (faked) cgroupfs leave zero leerie-w-* directories behind, even
    when several of them have a realistically slow drain."""
    mod, root, slice_dir, drain_at = fake_cgroupfs
    sock_path = str(root / "broker.sock")
    srv = _FakeBrokerServer(mod, sock_path)
    srv.start()
    try:
        monkeypatch.setattr(leerie, "_CGROUP_BROKER_SOCK", sock_path)
        monkeypatch.setattr(leerie, "_CGROUP_DESTROY_TIMEOUT_SEC", 0.9)

        for i in range(5):
            sid = f"e2e-cycle-{i}"
            drain = 0.0 if i % 2 == 0 else 0.35
            d = _seed_slow_draining_worker(slice_dir, drain_at, sid, drain)
            leerie._cgroup_destroy(sid)
            assert not d.exists(), f"leerie-w-{sid} leaked on cycle {i}"

        leftover = [p for p in slice_dir.iterdir()
                    if p.name.startswith("leerie-w-")]
        assert leftover == [], f"stale directories remain: {leftover}"
    finally:
        srv.stop()


class _SimulatedKill(BaseException):
    """Stands in for the broker process (PID 1) receiving SIGKILL mid-drain
    -- deliberately not an `OSError` subclass, so it is never mistaken for
    an ordinary rmdir/socket failure by anything under test."""


@pytest.mark.filterwarnings(
    "ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_pre_fix_short_timeout_plus_broker_teardown_leaks_the_directory(
        leerie, fake_cgroupfs, monkeypatch):
    """Regression control, reproducing the incident mechanism directly: a
    client timeout SHORTER than the broker's drain budget (the pre-fix
    5.0s-vs-10.0s shape, scaled to this test's budget) gives up on a
    still-legitimately-draining destroy, is swallowed silently by
    `_cgroup_destroy`'s `contextlib.suppress(OSError)`, and if the broker
    is then torn down (simulating container teardown racing ahead, exactly
    as `--cgroupns=host` makes possible since the cgroup dir outlives the
    container) before its own drain completes, the directory is
    PERMANENTLY orphaned -- proving this is a genuine leak mechanism, not
    a merely-delayed removal, and that the fixed timeout is what closes it.

    A real SIGKILL cannot be delivered to one thread inside this test
    process without killing the whole test run, so the kill is simulated
    cooperatively: `_drain_then_rmdir`'s poll loop is wrapped to check a
    `threading.Event` before every sleep and raise `_SimulatedKill` (never
    caught anywhere in the broker or this harness) the moment it is set --
    which, exactly like a real SIGKILL, aborts the in-progress drain loop
    before it reaches its own `os.rmdir`."""
    mod, root, slice_dir, drain_at = fake_cgroupfs
    killed = threading.Event()
    orig_drain = mod._drain_then_rmdir

    def killable_drain(d, budget_sec):
        deadline = time.monotonic() + budget_sec
        while True:
            if killed.is_set():
                raise _SimulatedKill()
            if not mod._read(f"{d}/cgroup.procs").strip():
                try:
                    os.rmdir(d)
                    return None
                except FileNotFoundError:
                    return None
                except OSError:
                    pass
            if time.monotonic() >= deadline:
                return "Device or resource busy"
            time.sleep(mod._DESTROY_DRAIN_POLL_SEC)

    monkeypatch.setattr(mod, "_drain_then_rmdir", killable_drain)

    sock_path = str(root / "broker.sock")
    srv = _FakeBrokerServer(mod, sock_path)
    srv.start()
    try:
        monkeypatch.setattr(leerie, "_CGROUP_BROKER_SOCK", sock_path)
        # Pre-fix shape: client timeout (0.15s) UNDER the broker's own
        # drain budget (0.6s) -- same ratio as the real bug's 5.0s vs 10.0s.
        monkeypatch.setattr(leerie, "_CGROUP_DESTROY_TIMEOUT_SEC", 0.15)

        d = _seed_slow_draining_worker(slice_dir, drain_at, "e2e-orphan", 0.35)
        assert d.is_dir()

        leerie._cgroup_destroy("e2e-orphan")  # must not raise (swallowed)

        # The client gave up before the broker's drain (0.35s) let alone
        # its full budget (0.6s) elapsed -- the directory is still there
        # right now, proving the client-side abandonment is real.
        assert d.exists(), (
            "expected the directory to still be present immediately after "
            "the short-timeout _cgroup_destroy call returns (client gave "
            "up before the broker finished)")

        # Now simulate the broker (PID 1) being torn down along with the
        # container before its own drain-then-rmdir loop reaches its rmdir.
        killed.set()
        time.sleep(0.6)  # past what the broker's own budget would have been

        assert d.exists(), (
            "the directory should be permanently orphaned once the broker "
            "is torn down mid-drain -- if it disappeared anyway, this test "
            "is not reproducing the N18 leak mechanism")
    finally:
        killed.set()
        srv.stop()
