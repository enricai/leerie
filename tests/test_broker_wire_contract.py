"""The cgroup broker's wire responses must parse in the orchestrator.

`scripts/cgroup-broker.py` composes multi-field responses and
`orchestrator/leerie.py` parses them, with the field count hand-written on
BOTH sides and — until this file — nothing comparing them:

    slice -> "OK <memory.max> <live> <unreclaimable>"   (broker)
             len(parts) != 4                            (leerie)
    stat  -> "OK <cur> <max> <events> <oom_kill>"       (broker)
             len(parts) == 5                            (leerie)

Drift is **silent** in the worst way. Both parsers return `None` on a
mismatch, and `None` is a legitimate value meaning "containment is off":
`_cgroup_slice_info` returning `None` makes worker sizing fall back to the
legacy `/proc/meminfo` basis AND turns the admission gate into a no-op,
while `_cgroup_stat` returning `None` silently disables PID-exhaustion
detection and memory-OOM naming. Nothing logs, nothing fails.

This is the same class CLAUDE.md records for
`scripts/remote/collect-subtrees.sh`, where a duplicated schema **had
already drifted in production** — *"silently, because nothing compared the
two"* — and `tests/test_collect_subtrees_integrator_schema.py` is the prior
art for pinning it.

The existing tests cannot catch this by construction:
`tests/test_cgroup_helpers.py` stubs `_cgroup_request` with hand-written
response strings (so it tests leerie's parser against a fixture, not
against the broker), and `tests/test_cgroup_broker.py` never touches
leerie's parsers at all. This file is the only place the two meet: it takes
the string the **real** broker emits and feeds it to the **real** leerie
parser.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_BROKER_PATH = (Path(__file__).resolve().parent.parent
                / "scripts" / "cgroup-broker.py")

# Seeded values, chosen distinct and non-round so a parser that returned a
# positionally-shifted field would not coincidentally match.
_SLICE_MAX = 58986594304
_ANON = 8181121024
_SLAB_UNRECLAIMABLE = 33554432
_UNEVICTABLE = 4096
_PIDS_CURRENT = 137
_PIDS_MAX = 2048
_PIDS_EVENTS_MAX = 3
_OOM_KILL = 2


@pytest.fixture
def broker(tmp_path, monkeypatch):
    """The real broker module over a fake unified (v2) cgroupfs, seeded
    with a live worker cgroup — same construction as
    tests/test_cgroup_broker.py's fixture."""
    spec = importlib.util.spec_from_file_location("cgroup_broker_contract",
                                                  _BROKER_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    root = tmp_path / "cgroup"
    slice_dir = root / mod.SLICE
    slice_dir.mkdir(parents=True)
    (root / "cgroup.controllers").write_text("cpu memory pids")
    (slice_dir / "cgroup.subtree_control").write_text("")
    (slice_dir / "cgroup.controllers").write_text("memory pids")

    # Slice-level facts the `slice` verb reads.
    (slice_dir / "memory.max").write_text(f"{_SLICE_MAX}\n")
    (slice_dir / "memory.stat").write_text(
        f"anon {_ANON}\n"
        "file 12320309248\n"
        "inactive_file 11136925696\n"
        f"slab_unreclaimable {_SLAB_UNRECLAIMABLE}\n"
        f"unevictable {_UNEVICTABLE}\n")

    # One live worker cgroup, with the counters `stat` reads.
    w = slice_dir / "leerie-w-wsid"
    w.mkdir()
    (w / "cgroup.procs").write_text("4242\n")
    (w / "pids.current").write_text(f"{_PIDS_CURRENT}\n")
    (w / "pids.max").write_text(f"{_PIDS_MAX}\n")
    (w / "pids.events").write_text(f"max {_PIDS_EVENTS_MAX}\n")
    (w / "memory.events").write_text(f"oom_kill {_OOM_KILL}\n")

    monkeypatch.setattr(mod, "V2_ROOT", str(root))
    monkeypatch.setattr(mod, "V1_ROOT", str(root))
    mod._HIER = mod._detect()
    assert mod._HIER == "v2", "fixture must present a usable v2 hierarchy"
    return mod


def _feed(leerie, monkeypatch, response: str) -> list[str]:
    """Point leerie's broker client at a canned response — the ONLY thing
    stubbed here is the socket. Both the producer of `response` and the
    parser under test are the real implementations."""
    sent: list[str] = []

    def fake(payload, timeout=5.0):
        sent.append(payload)
        return response

    monkeypatch.setattr(leerie, "_cgroup_request", fake)
    return sent


# ---- slice ----------------------------------------------------------------

def test_slice_response_parses_in_leerie(broker, leerie, monkeypatch):
    """The broker's real `slice` output must survive `_cgroup_slice_info`."""
    resp = broker._handle("slice")
    assert resp.startswith("OK "), resp

    sent = _feed(leerie, monkeypatch, resp)
    info = leerie._cgroup_slice_info()

    assert info is not None, (
        f"leerie could not parse the broker's own slice response: {resp!r}. "
        "The two field counts have drifted — and this failure mode is "
        "silent in production (None means 'containment off', so sizing "
        "falls back to /proc/meminfo and admission becomes a no-op).")
    assert sent == ["slice"]
    slice_max, live, unreclaimable = info
    assert slice_max == _SLICE_MAX
    assert live == 1
    assert unreclaimable == _ANON + _SLAB_UNRECLAIMABLE + _UNEVICTABLE


def test_slice_values_feed_the_real_consumers(broker, leerie, monkeypatch):
    """Beyond arity: the parsed values must be the ones the ceiling and the
    admission gate actually key on, so a silent field TRANSPOSITION (right
    count, wrong order) is caught too."""
    _feed(leerie, monkeypatch, broker._handle("slice"))
    slice_max, _live, unreclaimable = leerie._cgroup_slice_info()

    assert leerie._worker_memory_ceiling(slice_max) >= \
        leerie._WORKER_BUILD_PEAK_BYTES
    # ~54.9 GiB slice with ~7.6 GiB unreclaimable: ample headroom.
    assert slice_max - unreclaimable > leerie._WORKER_BUILD_PEAK_BYTES


def test_slice_arity_drift_is_detected(broker, leerie, monkeypatch):
    """Anti-vacuity: prove the guard above actually fires on drift, so a
    future field addition on either side cannot pass silently."""
    real = broker._handle("slice")
    _feed(leerie, monkeypatch, real + " 999")
    assert leerie._cgroup_slice_info() is None
    _feed(leerie, monkeypatch, " ".join(real.split()[:-1]))
    assert leerie._cgroup_slice_info() is None


# ---- stat -----------------------------------------------------------------

def test_stat_response_parses_in_leerie(broker, leerie, monkeypatch):
    """Same contract for `stat`. Not changed by the memory work, but it
    carries the identical hand-maintained-on-both-sides risk, and its
    silent-None failure disables PID-exhaustion detection and OOM naming."""
    resp = broker._handle("stat wsid")
    assert resp.startswith("OK "), resp

    _feed(leerie, monkeypatch, resp)
    got = leerie._cgroup_stat("wsid")

    assert got is not None, (
        f"leerie could not parse the broker's own stat response: {resp!r}")
    assert got == (_PIDS_CURRENT, _PIDS_MAX, _PIDS_EVENTS_MAX, _OOM_KILL)


def test_stat_arity_drift_is_detected(broker, leerie, monkeypatch):
    real = broker._handle("stat wsid")
    _feed(leerie, monkeypatch, real + " 999")
    assert leerie._cgroup_stat("wsid") is None


# ---- probe ------------------------------------------------------------

def test_probe_response_parses_in_leerie(broker, leerie, monkeypatch):
    """`_cgroup_probe` parses `"OK <hierarchy>"` -- the third broker
    response leerie's client parses field-by-field, alongside `slice` and
    `stat` above. Untested against the real broker output before this:
    `tests/test_cgroup_helpers.py` only ever feeds it hand-written
    fixture strings ("OK v2"/"OK v1"), never the real probe's own
    fork+create+enroll+destroy round trip."""
    leerie._CGROUP_PROBE_RESULT = None
    leerie._CGROUP_HIERARCHY = None
    try:
        resp = broker._handle("probe")
        assert resp.startswith("OK "), resp

        _feed(leerie, monkeypatch, resp)
        assert leerie._cgroup_probe() is True
        assert leerie._CGROUP_HIERARCHY == "v2"
    finally:
        # `leerie` is session-scoped (tests/conftest.py); reset the
        # module-level memo so this probe result doesn't leak into a
        # later test in a different file (mirrors
        # tests/test_cgroup_helpers.py's reset_probe_memo autouse fixture).
        leerie._CGROUP_PROBE_RESULT = None
        leerie._CGROUP_HIERARCHY = None


# ---- guard-the-guard ------------------------------------------------------

def test_broker_emits_the_verbs_this_file_pins(broker):
    """If a verb is renamed or dropped, this file must fail as a missing
    contract rather than silently pinning nothing."""
    assert not broker._handle("slice").startswith("ERR")
    assert not broker._handle("stat wsid").startswith("ERR")
    assert not broker._handle("probe").startswith("ERR")
