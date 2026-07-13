"""Tests for the cgroup broker (`scripts/cgroup-broker.py`).

The broker is the single most-privileged surface in the worker
containment path (DESIGN §6 *Memory containment*), so its input
validation and protocol dispatch are security-relevant and pinned here.
We import the broker module and point its `V2_ROOT` at a tmp directory
acting as a fake unified cgroupfs — the file writes are ordinary file
writes there, which is enough to test the protocol, sid validation, and
create/enroll/destroy dispatch. Real cgroupfs behavior (v1 vs v2, the
kernel's cgroup.kill / migration semantics) is covered by an
in-container reproduction, not this unit test.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_BROKER_PATH = (Path(__file__).resolve().parent.parent
                / "scripts" / "cgroup-broker.py")


@pytest.fixture
def broker(tmp_path, monkeypatch):
    """Load cgroup-broker.py as a module with V2_ROOT pointed at a tmp
    dir that looks like a unified (v2) cgroupfs, and force v2 hierarchy."""
    spec = importlib.util.spec_from_file_location("cgroup_broker",
                                                  _BROKER_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    root = tmp_path / "cgroup"
    slice_dir = root / mod.SLICE
    slice_dir.mkdir(parents=True)
    # Make _detect pick v2: expose the unified marker + controllers.
    (root / "cgroup.controllers").write_text("cpu memory pids")
    (slice_dir / "cgroup.subtree_control").write_text("")
    (slice_dir / "cgroup.controllers").write_text("memory pids")

    monkeypatch.setattr(mod, "V2_ROOT", str(root))
    # v1/hybrid paths are independent of V2_ROOT (see module docstring —
    # V1_ROOT is never rootless-overridden), but test_stat_v1_has_no_events
    # below still exercises the split-hierarchy helpers against this same
    # fake cgroupfs, so point both at the tmp root.
    monkeypatch.setattr(mod, "V1_ROOT", str(root))
    mod._HIER = mod._detect()
    return mod


# ---- hierarchy detection --------------------------------------------------

def test_detect_v2(broker):
    assert broker._HIER == "v2"


# ---- sid validation (security-critical) -----------------------------------

@pytest.mark.parametrize("sid,ok", [
    ("feat-007-conformer", True),
    ("abc123", True),
    ("a.b_c-d", True),
    ("", False),
    ("../escape", False),
    ("a/b", False),
    ("a b", False),
    ("foo;rm", False),
])
def test_valid_sid(broker, sid, ok):
    assert broker._valid_sid(sid) is ok


def test_create_rejects_bad_sid(broker):
    assert broker._handle("create ../evil 0 64") == "ERR bad sid"


def test_create_rejects_negative_limits(broker):
    assert broker._handle("create good 0 -5") == "ERR bad limit"


def test_enroll_rejects_bad_pid(broker):
    assert broker._handle("enroll good 0").startswith("ERR")
    assert broker._handle("enroll good notanint").startswith("ERR")


# ---- protocol dispatch ----------------------------------------------------

def test_ping(broker):
    assert broker._handle("ping") == "OK"


def test_empty_request(broker):
    assert broker._handle("") == "ERR empty"


def test_unknown_verb(broker):
    assert broker._handle("frobnicate x").startswith("ERR unknown verb")


def test_create_writes_v2_limit_files(broker, tmp_path):
    assert broker._handle("create wsid 268435456 64") == "OK"
    d = Path(broker.V2_ROOT) / broker.SLICE / "leerie-w-wsid"
    assert (d / "pids.max").read_text() == "64"
    assert (d / "memory.max").read_text() == "268435456"


def test_enroll_writes_cgroup_procs(broker):
    broker._handle("create wsid 0 64")
    assert broker._handle("enroll wsid 4321") == "OK"
    d = Path(broker.V2_ROOT) / broker.SLICE / "leerie-w-wsid"
    assert "4321" in (d / "cgroup.procs").read_text()


def test_destroy_removes_dir(broker):
    broker._handle("create wsid 0 64")
    d = Path(broker.V2_ROOT) / broker.SLICE / "leerie-w-wsid"
    assert d.is_dir()
    assert broker._handle("destroy wsid") == "OK"
    # cgroup.kill is a stray file on a regular fs, so rmdir may be blocked;
    # the contract is that destroy returns OK and does not raise.


def test_no_hierarchy_errors(broker, monkeypatch):
    """When no usable hierarchy is detected, ops report ERR rather than
    silently pretending to enforce."""
    monkeypatch.setattr(broker, "_HIER", "none")
    assert broker._handle("create wsid 0 64") == "ERR no usable cgroup hierarchy"


# ---- stat verb (PID-exhaustion detection, DESIGN §6) ----------------------

def _seed_pids_files(broker, sid, current, maxval, events_max):
    """Write fake v2 pids.* controller files for `sid`."""
    d = Path(broker.V2_ROOT) / broker.SLICE / f"leerie-w-{sid}"
    d.mkdir(parents=True, exist_ok=True)
    (d / "pids.current").write_text(str(current))
    (d / "pids.max").write_text(str(maxval))
    (d / "pids.events").write_text(f"max {events_max}\n")
    return d


def test_stat_reads_v2_counters(broker):
    _seed_pids_files(broker, "wsid", current=256, maxval=256, events_max=42)
    assert broker._handle("stat wsid") == "OK 256 256 42"


def test_stat_unlimited_max_reports_minus_one(broker):
    """pids.max == 'max' (unlimited) → -1 so the client never false-detects
    current >= max on an uncapped cgroup."""
    d = Path(broker.V2_ROOT) / broker.SLICE / "leerie-w-wsid"
    d.mkdir(parents=True)
    (d / "pids.current").write_text("5")
    (d / "pids.max").write_text("max")
    (d / "pids.events").write_text("max 0\n")
    assert broker._handle("stat wsid") == "OK 5 -1 0"


def test_stat_missing_cgroup_degrades_to_sentinels(broker):
    """No cgroup dir (containment off / raced with destroy) → safe
    sentinels (current 0, max -1, events 0), never a raise."""
    assert broker._handle("stat ghost") == "OK 0 -1 0"


def test_stat_rejects_bad_sid(broker):
    assert broker._handle("stat ../evil") == "ERR bad sid"


def test_stat_missing_sid_arg_errors(broker):
    # _handle wraps _do in try/except (OSError, ValueError, IndexError).
    assert broker._handle("stat").startswith("ERR")


def test_stat_v1_has_no_events(broker, monkeypatch, tmp_path):
    """v1's pids controller exposes current/max but no pids.events → the
    events_max field is always 0 (detection falls back to current>=max)."""
    monkeypatch.setattr(broker, "_HIER", "v1")
    pdir = (Path(broker.V2_ROOT) / "pids" / broker.SLICE / "leerie-w-wsid")
    pdir.mkdir(parents=True)
    (pdir / "pids.current").write_text("100")
    (pdir / "pids.max").write_text("100")
    assert broker._handle("stat wsid") == "OK 100 100 0"


def test_stat_events_parser_ignores_unknown_keys(broker):
    """pids.events may carry keys other than 'max' (e.g. 'max.imposed' on
    some kernels); only the exact 'max' line counts."""
    d = Path(broker.V2_ROOT) / broker.SLICE / "leerie-w-wsid"
    d.mkdir(parents=True)
    (d / "pids.current").write_text("10")
    (d / "pids.max").write_text("64")
    (d / "pids.events").write_text("max.imposed 7\nmax 3\n")
    assert broker._handle("stat wsid") == "OK 10 64 3"


# ---- probe round-trip -----------------------------------------------------

def test_probe_round_trips_ok(broker):
    """`probe` forks a real child, creates+enrolls+destroys a throwaway
    cgroup, and returns OK with the hierarchy. On the fake cgroupfs the
    writes are regular-file writes, so this exercises the full control
    flow (including the fork/kill/reap) without a real kernel cgroup."""
    resp = broker._handle("probe")
    assert resp == "OK v2"


def test_probe_robust_when_child_already_reaped(broker, monkeypatch):
    """The v2 hazard: `destroy` writes cgroup.kill which the kernel uses to
    SIGKILL the enrolled probe child; if the zombie is reaped before the
    broker's own os.kill/waitpid, those raise ProcessLookupError/
    ChildProcessError. The probe must tolerate an already-gone child and
    still return OK, not falsely fail (which would trip the fail-closed
    gate and abort a healthy run).

    Simulate without a real fork: os.fork returns a bogus pid in the
    parent, and os.kill/os.waitpid raise the already-gone errors. The
    suppress() wrappers must swallow both and the probe must return OK."""
    monkeypatch.setattr(broker.os, "fork", lambda: 999999)  # parent branch

    def gone_kill(pid, sig):
        raise ProcessLookupError

    def gone_wait(pid, opts):
        raise ChildProcessError

    monkeypatch.setattr(broker.os, "kill", gone_kill)
    monkeypatch.setattr(broker.os, "waitpid", gone_wait)
    resp = broker._handle("probe")
    assert resp == "OK v2"


# ---- rootless: LEERIE_CGROUP_V2_ROOT (systemd-delegated user slice) -------
#
# DESIGN §6 *Rootless exception*: rootlesskit maps container "root" to the
# real host UID, which has no privilege over the top-level /sys/fs/cgroup.
# container-entry.sh instead points the broker at the systemd-delegated
# user slice (/sys/fs/cgroup/user.slice/user-<uid>.slice/user@<uid>.service)
# via LEERIE_CGROUP_V2_ROOT, read once at module import. Unlike the `broker`
# fixture above (which patches V2_ROOT post-import), this exercises the
# actual env-var-driven initialization path.

@pytest.fixture
def rootless_broker(tmp_path, monkeypatch):
    """Load cgroup-broker.py with LEERIE_CGROUP_V2_ROOT pointed at a fake
    nested user-slice path, set BEFORE import so the module-level
    `os.environ.get(...)` default resolution is actually exercised."""
    v2_root = tmp_path / "cgroup" / "user.slice" / "user-1000.slice" / "user@1000.service"
    v2_slice_dir = v2_root / "leerie.slice"
    v2_slice_dir.mkdir(parents=True)
    (v2_root / "cgroup.controllers").write_text("cpu memory pids")
    (v2_slice_dir / "cgroup.subtree_control").write_text("")
    (v2_slice_dir / "cgroup.controllers").write_text("memory pids")

    monkeypatch.setenv("LEERIE_CGROUP_V2_ROOT", str(v2_root))
    spec = importlib.util.spec_from_file_location("cgroup_broker_rootless",
                                                  _BROKER_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod._HIER = mod._detect()
    return mod


def test_rootless_v2_root_read_from_env(rootless_broker, tmp_path):
    """V2_ROOT must resolve to the env-provided delegated-slice path, not
    the hardcoded top-level default."""
    expected = str(tmp_path / "cgroup" / "user.slice" / "user-1000.slice"
                    / "user@1000.service")
    assert rootless_broker.V2_ROOT == expected


def test_rootless_v1_root_unaffected_by_env(rootless_broker):
    """V1_ROOT (Fly-only split hierarchy) must stay the literal top-level
    path regardless of LEERIE_CGROUP_V2_ROOT — v1/hybrid is never rootless."""
    assert rootless_broker.V1_ROOT == "/sys/fs/cgroup"


def test_rootless_detect_picks_v2_at_delegated_root(rootless_broker):
    assert rootless_broker._HIER == "v2"


def test_rootless_create_enroll_destroy_round_trip(rootless_broker):
    """The full worker-containment sequence — create, enroll (migrate a
    pid in), destroy — against the delegated-slice root, mirroring what
    container-entry.sh + the broker do for a real rootless worker."""
    assert rootless_broker._handle("create wsid 268435456 64") == "OK"
    d = Path(rootless_broker.V2_ROOT) / rootless_broker.SLICE / "leerie-w-wsid"
    assert (d / "pids.max").read_text() == "64"
    assert (d / "memory.max").read_text() == "268435456"

    assert rootless_broker._handle("enroll wsid 4321") == "OK"
    assert "4321" in (d / "cgroup.procs").read_text()

    assert rootless_broker._handle("destroy wsid") == "OK"
