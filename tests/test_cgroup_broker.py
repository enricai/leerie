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
    # `destroy` waits for the killed subtree to drain before rmdir'ing
    # (see _drain_then_rmdir). The shipped 10s budget is right for a real
    # conformer's process tree but would make every failure-path test here
    # spin for 10s, so shrink it. The shipped value is pinned separately by
    # test_destroy_drain_budget_is_ten_seconds.
    monkeypatch.setattr(mod, "_DESTROY_DRAIN_TIMEOUT_SEC", 0.05)
    monkeypatch.setattr(mod, "_DESTROY_DRAIN_POLL_SEC", 0.005)
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


def test_destroy_removes_dir(broker, monkeypatch):
    # On a real kernel cgroupfs, cgroup.kill is a pre-existing pseudo-file
    # that writing to it does not turn into a dentry blocking rmdir; on
    # this fake (plain-directory) cgroupfs it would, so stub the
    # cgroup.kill write out to isolate the success path.
    broker._handle("create wsid 0 0")
    d = Path(broker.V2_ROOT) / broker.SLICE / "leerie-w-wsid"
    assert d.is_dir()
    orig_write = broker._write
    monkeypatch.setattr(
        broker, "_write",
        lambda path, *a, **kw: None if path.endswith("cgroup.kill") else orig_write(path, *a, **kw))
    assert broker._handle("destroy wsid") == "OK"
    assert not d.exists()


def test_destroy_reports_err_on_rmdir_failure(broker):
    """N17: a failed rmdir (e.g. ENOTEMPTY because a controller left a
    stray file behind) must surface as `ERR ...`, not a silently-discarded
    `OK` — see the 70-stale-vs-12-live cgroup leak this closes."""
    broker._handle("create wsid 0 64")
    d = Path(broker.V2_ROOT) / broker.SLICE / "leerie-w-wsid"
    assert d.is_dir()
    assert (d / "pids.max").exists()  # non-empty dir -> rmdir fails
    resp = broker._handle("destroy wsid")
    assert resp.startswith("ERR ")
    assert d.exists()


def test_v2_destroy_returns_none_on_success(broker, monkeypatch):
    d = Path(broker.V2_ROOT) / broker.SLICE / "leerie-w-wsid2"
    d.mkdir(parents=True)
    monkeypatch.setattr(broker, "_write", lambda *a, **kw: None)
    assert broker._v2_destroy("wsid2") is None
    assert not d.exists()


def test_v2_destroy_returns_error_message_on_failure(broker, monkeypatch):
    d = Path(broker.V2_ROOT) / broker.SLICE / "leerie-w-wsid3"
    d.mkdir(parents=True)

    def _raise(path):
        raise OSError(39, "Directory not empty")

    monkeypatch.setattr(broker.os, "rmdir", _raise)
    err = broker._v2_destroy("wsid3")
    assert err == "Directory not empty"


# ---- destroy: drain before rmdir ------------------------------------------
# `cgroup.kill` is asynchronous — it delivers SIGKILL and returns while the
# kernel tears the tree down. An immediate rmdir races that and loses with
# EBUSY, leaking the dir: measured, 116 stale leerie-w-* dirs of which 88
# were -conformer (test-suite trees reaching hundreds of PIDs). The
# orchestrator had been logging the cause verbatim: "cgroup destroy failed
# (ERR Device or resource busy); dir may be leaked".

def test_destroy_waits_for_procs_to_drain(broker, monkeypatch):
    """A subtree that is still dying must be waited for, not abandoned.
    Zero-retry code fails this; so does any fixed sleep shorter than the
    drain."""
    d = Path(broker.V2_ROOT) / broker.SLICE / "leerie-w-drain"
    d.mkdir(parents=True)
    # Deliberately no real cgroup.procs file: on a real cgroupfs the
    # pseudo-files do not block rmdir, but on this plain-directory fake
    # they would, masking what this test is about. The stubbed _read below
    # supplies the contents instead.
    monkeypatch.setattr(broker, "_write", lambda *a, **kw: None)

    reads = {"n": 0}
    orig_read = broker._read

    def draining(path):
        if path.endswith("cgroup.procs"):
            reads["n"] += 1
            # Still dying for the first few polls, then drained.
            return "" if reads["n"] > 3 else "111\n222\n"
        return orig_read(path)

    monkeypatch.setattr(broker, "_read", draining)
    assert broker._v2_destroy("drain") is None
    assert not d.exists()
    assert reads["n"] > 3, "must have polled while the tree was draining"


def test_destroy_does_not_rmdir_while_procs_remain(broker, monkeypatch):
    """Anti-vacuity for the above: while cgroup.procs is non-empty the
    rmdir must not even be attempted, so a 'drain' that just retries a
    doomed rmdir cannot pass."""
    d = Path(broker.V2_ROOT) / broker.SLICE / "leerie-w-busy"
    d.mkdir(parents=True)
    monkeypatch.setattr(broker, "_write", lambda *a, **kw: None)
    monkeypatch.setattr(broker, "_read",
                        lambda p: "999\n" if p.endswith("cgroup.procs") else "")
    attempted = []
    monkeypatch.setattr(broker.os, "rmdir",
                        lambda p: attempted.append(p))
    err = broker._v2_destroy("busy")
    assert attempted == []
    assert err is not None


def test_destroy_still_reports_error_when_budget_exhausted(broker, monkeypatch):
    """The retry must not swallow a genuine leak — the orchestrator's
    'dir may be leaked' warning depends on this error surfacing."""
    d = Path(broker.V2_ROOT) / broker.SLICE / "leerie-w-stuck"
    d.mkdir(parents=True)
    monkeypatch.setattr(broker, "_write", lambda *a, **kw: None)
    monkeypatch.setattr(broker, "_read",
                        lambda p: "" if p.endswith("cgroup.procs") else "")

    def _raise(path):
        raise OSError(16, "Device or resource busy")

    monkeypatch.setattr(broker.os, "rmdir", _raise)
    assert broker._v2_destroy("stuck") == "Device or resource busy"


def test_destroy_drain_budget_is_ten_seconds(broker):
    """Pins the shipped constants (the fixture shrinks them for speed).
    10s because the leaking workers were conformers whose trees reached
    the hundreds; a sub-second budget does not cover their teardown."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("cgroup_broker_pristine",
                                                  _BROKER_PATH)
    pristine = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pristine)
    assert pristine._DESTROY_DRAIN_TIMEOUT_SEC == 10.0
    assert 0 < pristine._DESTROY_DRAIN_POLL_SEC <= 0.5


# ---- slice verb: budget, live count, unreclaimable -------------------------

def test_slice_verb_reports_max_live_and_unreclaimable(broker):
    slice_dir = Path(broker.V2_ROOT) / broker.SLICE
    (slice_dir / "memory.max").write_text("58986594304\n")
    (slice_dir / "memory.stat").write_text(
        "anon 8589934592\nfile 11072506368\ninactive_file 11072506368\n"
        "slab_unreclaimable 33554432\nunevictable 0\n")
    live = slice_dir / "leerie-w-alive"
    live.mkdir()
    (live / "cgroup.procs").write_text("4242\n")
    dead = slice_dir / "leerie-w-dead"
    dead.mkdir()
    (dead / "cgroup.procs").write_text("")

    resp = broker._handle("slice")
    parts = resp.split()
    assert parts[0] == "OK"
    assert int(parts[1]) == 58986594304
    assert int(parts[2]) == 1, "empty cgroup.procs is not a live sibling"
    assert int(parts[3]) == 8589934592 + 33554432


def test_slice_unreclaimable_excludes_page_cache(broker):
    """THE calibration pin. Measured live, 10.4 GiB of 20.5 GiB in use was
    reclaimable `inactive_file`. Counting it (i.e. using memory.current)
    under-reports headroom by half and stalls a fleet with ample room."""
    slice_dir = Path(broker.V2_ROOT) / broker.SLICE
    (slice_dir / "memory.max").write_text("58986594304\n")
    (slice_dir / "memory.current").write_text("22011707392")  # 20.5 GiB
    (slice_dir / "memory.stat").write_text(
        "anon 8181121024\nfile 12320309248\ninactive_file 11136925696\n"
        "active_file 1181116416\nslab_unreclaimable 33554432\n"
        "unevictable 0\n")
    got = broker._slice_unreclaimable()
    assert got == 8181121024 + 33554432
    assert got < 22011707392, "must not equal/exceed memory.current"


def test_slice_unreclaimable_minus_one_when_unreadable(broker):
    """No memory.stat at all -> -1 ("unknown"), which the orchestrator
    treats as fail-open rather than as a full slice."""
    assert broker._slice_unreclaimable() == -1


def test_slice_unreclaimable_tolerates_missing_optional_keys(broker):
    """Some kernels omit unevictable/slab_unreclaimable. Only the primary
    anon/rss key is required — treating an optional absence as a read
    failure would fail open far more often than necessary."""
    slice_dir = Path(broker.V2_ROOT) / broker.SLICE
    (slice_dir / "memory.stat").write_text("anon 1234\nfile 99\n")
    assert broker._slice_unreclaimable() == 1234


def test_slice_unreclaimable_minus_one_when_primary_key_absent(broker):
    slice_dir = Path(broker.V2_ROOT) / broker.SLICE
    (slice_dir / "memory.stat").write_text("file 99\ninactive_file 99\n")
    assert broker._slice_unreclaimable() == -1


def test_no_hierarchy_errors(broker, monkeypatch):
    """When no usable hierarchy is detected, ops report ERR rather than
    silently pretending to enforce."""
    monkeypatch.setattr(broker, "_HIER", "none")
    assert broker._handle("create wsid 0 64") == "ERR no usable cgroup hierarchy"


# ---- stat verb (PID-exhaustion detection, DESIGN §6) ----------------------

def _seed_pids_files(broker, sid, current, maxval, events_max, oom_kill=0):
    """Write fake v2 pids.* + memory.events controller files for `sid`."""
    d = Path(broker.V2_ROOT) / broker.SLICE / f"leerie-w-{sid}"
    d.mkdir(parents=True, exist_ok=True)
    (d / "pids.current").write_text(str(current))
    (d / "pids.max").write_text(str(maxval))
    (d / "pids.events").write_text(f"max {events_max}\n")
    (d / "memory.events").write_text(f"oom_kill {oom_kill}\n")
    return d


def test_stat_reads_v2_counters(broker):
    _seed_pids_files(broker, "wsid", current=256, maxval=256, events_max=42)
    assert broker._handle("stat wsid") == "OK 256 256 42 0"


def test_stat_unlimited_max_reports_minus_one(broker):
    """pids.max == 'max' (unlimited) → -1 so the client never false-detects
    current >= max on an uncapped cgroup."""
    d = Path(broker.V2_ROOT) / broker.SLICE / "leerie-w-wsid"
    d.mkdir(parents=True)
    (d / "pids.current").write_text("5")
    (d / "pids.max").write_text("max")
    (d / "pids.events").write_text("max 0\n")
    (d / "memory.events").write_text("oom_kill 0\n")
    assert broker._handle("stat wsid") == "OK 5 -1 0 0"


def test_stat_missing_cgroup_degrades_to_sentinels(broker):
    """No cgroup dir (containment off / raced with destroy) → safe
    sentinels (current 0, max -1, events 0, oom 0), never a raise."""
    assert broker._handle("stat ghost") == "OK 0 -1 0 0"


def test_stat_rejects_bad_sid(broker):
    assert broker._handle("stat ../evil") == "ERR bad sid"


def test_stat_missing_sid_arg_errors(broker):
    # _handle wraps _do in try/except (OSError, ValueError, IndexError).
    assert broker._handle("stat").startswith("ERR")


def test_stat_v1_has_no_events(broker, monkeypatch, tmp_path):
    """v1's pids controller exposes current/max but no pids.events → the
    events_max field is always 0 (detection falls back to current>=max).
    v1's memory controller has no memory.events seeded here → oom
    degrades to 0 (missing-file sentinel)."""
    monkeypatch.setattr(broker, "_HIER", "v1")
    pdir = (Path(broker.V2_ROOT) / "pids" / broker.SLICE / "leerie-w-wsid")
    pdir.mkdir(parents=True)
    (pdir / "pids.current").write_text("100")
    (pdir / "pids.max").write_text("100")
    assert broker._handle("stat wsid") == "OK 100 100 0 0"


def test_stat_v1_reads_memory_events_oom(broker, monkeypatch, tmp_path):
    """v1's memory controller exposes memory.events with the same
    oom_kill key on modern kernels → read it from the memory (not pids)
    controller dir."""
    monkeypatch.setattr(broker, "_HIER", "v1")
    pdir = (Path(broker.V2_ROOT) / "pids" / broker.SLICE / "leerie-w-wsid")
    mdir = (Path(broker.V2_ROOT) / "memory" / broker.SLICE / "leerie-w-wsid")
    pdir.mkdir(parents=True)
    mdir.mkdir(parents=True)
    (pdir / "pids.current").write_text("100")
    (pdir / "pids.max").write_text("100")
    (mdir / "memory.events").write_text("oom_kill 2\n")
    assert broker._handle("stat wsid") == "OK 100 100 0 2"


def test_stat_events_parser_ignores_unknown_keys(broker):
    """pids.events may carry keys other than 'max' (e.g. 'max.imposed' on
    some kernels); only the exact 'max' line counts."""
    d = Path(broker.V2_ROOT) / broker.SLICE / "leerie-w-wsid"
    d.mkdir(parents=True)
    (d / "pids.current").write_text("10")
    (d / "pids.max").write_text("64")
    (d / "pids.events").write_text("max.imposed 7\nmax 3\n")
    (d / "memory.events").write_text("oom_kill 0\n")
    assert broker._handle("stat wsid") == "OK 10 64 3 0"


def test_stat_reads_v2_oom_kill(broker):
    """memory.events' oom_kill counter surfaces as the 4th stat token —
    the definitive OOM-kill signal (mirrors the pids.events 'max' key)."""
    _seed_pids_files(broker, "wsid", current=10, maxval=64, events_max=0,
                      oom_kill=5)
    assert broker._handle("stat wsid") == "OK 10 64 0 5"


def test_stat_memory_events_missing_file_degrades_to_zero(broker):
    """No memory.events file (containment off / race with destroy) → oom
    degrades to 0 rather than raising (mirrors the pids.* sentinel
    convention)."""
    d = Path(broker.V2_ROOT) / broker.SLICE / "leerie-w-wsid"
    d.mkdir(parents=True)
    (d / "pids.current").write_text("10")
    (d / "pids.max").write_text("64")
    (d / "pids.events").write_text("max 0\n")
    # memory.events intentionally not written.
    assert broker._handle("stat wsid") == "OK 10 64 0 0"


def test_memory_events_oom_parser_ignores_unknown_keys(broker):
    """memory.events carries other keys (low, high, max, oom, oom_kill);
    only the exact 'oom_kill' line counts."""
    d = Path(broker.V2_ROOT) / broker.SLICE / "leerie-w-wsid"
    d.mkdir(parents=True)
    (d / "pids.current").write_text("1")
    (d / "pids.max").write_text("64")
    (d / "pids.events").write_text("max 0\n")
    (d / "memory.events").write_text(
        "low 0\nhigh 0\nmax 1\noom 2\noom_kill 7\n")
    assert broker._handle("stat wsid") == "OK 1 64 0 7"


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


def test_probe_sid_is_run_scoped_and_distinct_per_call(broker, monkeypatch):
    """The probe cgroup name must carry a per-probe random suffix, so two
    concurrent containers' probes never share `leerie-w-PROBE` under the
    same VM slice (the --cgroupns=host case) — the same cross-run collision
    class the run-scoped worker cgroup name fixes. Assert (a) the sid matches
    `PROBE-<8hex>` and passes `_valid_sid`, and (b) two probe calls use two
    DIFFERENT sids (a bare `PROBE` would fail (b))."""
    import re

    seen: list[str] = []
    real_do = broker._do

    def recording_do(verb, args):
        if verb == "create":
            seen.append(args[0])
        return real_do(verb, args)

    monkeypatch.setattr(broker, "_do", recording_do)
    # Avoid a real fork/kill in this control-flow test.
    monkeypatch.setattr(broker.os, "fork", lambda: 999999)
    monkeypatch.setattr(broker.os, "kill", lambda pid, sig: None)
    monkeypatch.setattr(broker.os, "waitpid", lambda pid, opts: (pid, 0))

    assert broker._handle("probe") == "OK v2"
    assert broker._handle("probe") == "OK v2"

    assert len(seen) == 2
    for sid in seen:
        assert re.fullmatch(r"PROBE-[0-9a-f]{8}", sid), sid
        assert broker._valid_sid(sid)
    assert seen[0] != seen[1], "probe sids must differ across calls"


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
    # Same reason as the `broker` fixture: shrink the destroy drain budget
    # so the deliberate ENOTEMPTY failure path below returns promptly
    # instead of spending the shipped 10s.
    monkeypatch.setattr(mod, "_DESTROY_DRAIN_TIMEOUT_SEC", 0.05)
    monkeypatch.setattr(mod, "_DESTROY_DRAIN_POLL_SEC", 0.005)
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

    # On this fake (plain-directory) cgroupfs the limit files created
    # above are real dentries a real kernel cgroupfs would not have, so
    # rmdir genuinely fails here (ENOTEMPTY) — this is the N17 contract:
    # a failed teardown surfaces as ERR rather than a silently-swallowed
    # OK. Real cgroupfs directories contain no such stray files.
    resp = rootless_broker._handle("destroy wsid")
    assert resp.startswith("ERR ")
