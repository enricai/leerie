"""Tests for the cgroup containment helpers in leerie.py.

Since the containment fix, these helpers (`_cgroup_probe`,
`_cgroup_create`, `_cgroup_enroll`, `_cgroup_destroy` in
`orchestrator/leerie.py`) are thin clients of the cgroup broker
(`scripts/cgroup-broker.py`): they send a text request over a Unix
socket and act on the response. The direct cgroupfs writes moved to the
broker, which runs at an identity that owns (or was delegated) the cgroup
subtree — real root rootful, the rootlesskit-mapped host UID rootless —
the only identities where cgroup enrollment / limit-setting works (see
DESIGN §6 *Memory containment* and the reproduced delegation constraint).

So these tests mock `_cgroup_request` (the socket round-trip) and pin the
client contracts:

  1. Probe passes only when the broker answers `OK <hierarchy>`; a broker
     `ERR ...` or an unreachable socket makes it False (and records the
     hierarchy on success).
  2. Probe failure makes `_cgroup_create` a no-op returning None.
  3. create/enroll/destroy send the right payloads and treat any non-`OK`
     response (or socket OSError) as failure without raising.

The broker's own cgroupfs behavior (v1 vs v2 paths, sid validation) is
covered separately by an in-container reproduction, not unit tests — it
requires a real cgroup hierarchy the test host lacks.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def _load_broker():
    """Load scripts/cgroup-broker.py as a module (same technique as
    tests/test_cgroup_broker.py) so tests can call its real `_valid_sid`
    rather than re-declaring the sid regex — a drift-proof coupling."""
    path = Path(__file__).resolve().parent.parent / "scripts" / "cgroup-broker.py"
    spec = importlib.util.spec_from_file_location("cgroup_broker", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(autouse=True)
def reset_probe_memo(leerie):
    """Reset the module-level probe memo + hierarchy before every test so
    one test's `_CGROUP_PROBE_RESULT` doesn't leak into the next."""
    leerie._CGROUP_PROBE_RESULT = None
    leerie._CGROUP_HIERARCHY = None
    yield
    leerie._CGROUP_PROBE_RESULT = None
    leerie._CGROUP_HIERARCHY = None


def _stub_broker(leerie, monkeypatch, responses):
    """Replace `_cgroup_request` with a stub that records every payload it
    was sent and returns queued responses. `responses` is either a single
    string (returned for every call) or a callable(payload) -> str. A
    string starting with 'RAISE' makes the stub raise OSError."""
    sent = []

    def fake(payload, timeout=5.0):
        sent.append(payload)
        resp = responses(payload) if callable(responses) else responses
        if resp.startswith("RAISE"):
            raise OSError(resp[len("RAISE"):].strip() or "connection refused")
        return resp

    monkeypatch.setattr(leerie, "_cgroup_request", fake)
    return sent


# ---- probe ----------------------------------------------------------------

def test_probe_success_records_hierarchy(leerie, monkeypatch):
    sent = _stub_broker(leerie, monkeypatch, "OK v2")
    assert leerie._cgroup_probe() is True
    assert leerie._CGROUP_HIERARCHY == "v2"
    assert sent == ["probe"]


def test_probe_success_v1(leerie, monkeypatch):
    _stub_broker(leerie, monkeypatch, "OK v1")
    assert leerie._cgroup_probe() is True
    assert leerie._CGROUP_HIERARCHY == "v1"


def test_probe_failure_on_broker_err(leerie, monkeypatch):
    """Broker answers ERR (e.g. no usable hierarchy on a v1/hybrid host):
    probe is False."""
    _stub_broker(leerie, monkeypatch, "ERR no usable cgroup hierarchy")
    assert leerie._cgroup_probe() is False


def test_probe_failure_when_broker_unreachable(leerie, monkeypatch):
    """Socket connect raises (broker never launched): probe is False,
    degrades gracefully."""
    _stub_broker(leerie, monkeypatch, "RAISE no such file")
    assert leerie._cgroup_probe() is False


def test_probe_memoizes(leerie, monkeypatch):
    """Once probe runs, the result is cached; a second call does not
    re-hit the broker."""
    sent = _stub_broker(leerie, monkeypatch, "OK v2")
    first = leerie._cgroup_probe()
    second = leerie._cgroup_probe()
    assert first is second is True
    assert sent == ["probe"]  # only one round-trip


# ---- create ---------------------------------------------------------------

def test_create_sends_payload_and_returns_sid(leerie, monkeypatch):
    sent = _stub_broker(
        leerie, monkeypatch,
        lambda p: "OK v2" if p == "probe" else "OK")
    sid = leerie._cgroup_create("test-sid", 256 * 1024**2, 64)
    assert sid == "test-sid"
    assert f"create test-sid {256 * 1024**2} 64" in sent


def test_create_returns_none_when_probe_failed(leerie, monkeypatch):
    _stub_broker(leerie, monkeypatch, "RAISE unreachable")
    assert leerie._cgroup_create("sid", 1 << 30, 64) is None


def test_create_returns_none_on_broker_reject(leerie, monkeypatch):
    """Probe passes but the create is rejected (e.g. bad sid): None, no
    raise, worker runs uncapped."""
    _stub_broker(
        leerie, monkeypatch,
        lambda p: "OK v2" if p == "probe" else "ERR bad sid")
    assert leerie._cgroup_create("sid", 1 << 30, 64) is None


# ---- enroll ---------------------------------------------------------------

def test_enroll_sends_payload(leerie, monkeypatch):
    sent = _stub_broker(leerie, monkeypatch, "OK")
    assert leerie._cgroup_enroll("sid-b", 12345) is True
    assert sent == ["enroll sid-b 12345"]


def test_enroll_returns_false_on_broker_err(leerie, monkeypatch):
    _stub_broker(leerie, monkeypatch, "ERR bad sid/pid")
    assert leerie._cgroup_enroll("sid", 1) is False


def test_enroll_returns_false_on_unreachable(leerie, monkeypatch):
    _stub_broker(leerie, monkeypatch, "RAISE refused")
    assert leerie._cgroup_enroll("sid", 1) is False


# ---- destroy --------------------------------------------------------------

def test_destroy_sends_payload(leerie, monkeypatch):
    sent = _stub_broker(leerie, monkeypatch, "OK")
    leerie._cgroup_destroy("sid-c")
    assert sent == ["destroy sid-c"]


def test_destroy_handles_none(leerie, monkeypatch):
    """None sid (containment was off for this worker) is a no-op — the
    broker must not even be contacted."""
    sent = _stub_broker(leerie, monkeypatch, "OK")
    leerie._cgroup_destroy(None)
    assert sent == []


def test_destroy_swallows_unreachable(leerie, monkeypatch):
    """A socket error during teardown is swallowed (best-effort)."""
    _stub_broker(leerie, monkeypatch, "RAISE gone")
    leerie._cgroup_destroy("sid-d")  # must not raise


# ---- fail-closed gate + recording (unified) -------------------------------

class _FakeState:
    def __init__(self, data=None):
        self.data = dict(data or {})
        self.saved = False

    def save(self):
        self.saved = True


def test_gate_records_and_passes_when_enforced(leerie, monkeypatch):
    """When containment is enforced, the outcome is recorded and the run
    proceeds (no raise)."""
    monkeypatch.setattr(leerie, "_cgroup_probe", lambda: True)
    monkeypatch.setattr(leerie, "_CGROUP_HIERARCHY", "v2")
    st = _FakeState({"task": "t"})
    leerie.enforce_and_record_cgroup_containment(st, allow_uncapped=False)
    assert st.data["cgroup_containment"] == {"enforced": True,
                                             "hierarchy": "v2"}
    assert st.saved


def test_gate_dies_when_unenforced_and_not_waived(leerie, monkeypatch):
    """The fix's core safety property: an unenforced run die()s by
    default rather than running uncapped (what caused the crash). The
    outcome is still recorded before the die()."""
    monkeypatch.setattr(leerie, "_cgroup_probe", lambda: False)
    st = _FakeState({"task": "t"})
    with pytest.raises(SystemExit):
        leerie.enforce_and_record_cgroup_containment(st, allow_uncapped=False)
    assert st.data["cgroup_containment"]["enforced"] is False


def test_gate_warns_and_continues_when_waived(leerie, monkeypatch):
    """--dangerously-allow-uncapped downgrades the fatal gate to a
    warning and lets the run proceed."""
    monkeypatch.setattr(leerie, "_cgroup_probe", lambda: False)
    st = _FakeState({"task": "t"})
    leerie.enforce_and_record_cgroup_containment(st, allow_uncapped=True)
    assert st.data["cgroup_containment"]["enforced"] is False


def test_gate_merges_into_existing_state(leerie, monkeypatch):
    """Regression for the resume-corruption bug: the gate must MERGE the
    outcome into an already-populated st.data, never replace it. The
    earlier design blind-saved an empty dict + one key in main() before
    st was loaded, discarding task/waves/etc. and bricking --resume — so
    the gate now runs in _run_phases after st.data is populated and merges."""
    monkeypatch.setattr(leerie, "_cgroup_probe", lambda: True)
    monkeypatch.setattr(leerie, "_CGROUP_HIERARCHY", "v2")
    st = _FakeState({"task": "do a thing", "waves": [["a"]],
                     "worker_count": 3})
    leerie.enforce_and_record_cgroup_containment(st, allow_uncapped=False)
    # Existing keys survive.
    assert st.data["task"] == "do a thing"
    assert st.data["waves"] == [["a"]]
    assert st.data["worker_count"] == 3
    # New key added.
    assert st.data["cgroup_containment"] == {"enforced": True,
                                             "hierarchy": "v2"}


def test_gate_takes_state_and_flag(leerie):
    """Pins the unified signature (st, allow_uncapped) — the gate now
    lives with the state recording, not as a state-free main() call."""
    import inspect
    sig = inspect.signature(leerie.enforce_and_record_cgroup_containment)
    assert list(sig.parameters) == ["st", "allow_uncapped"]


# ---- stat (PID-exhaustion + memory-OOM probe, DESIGN §6) ------------------

def test_stat_parses_ok_quadruple(leerie, monkeypatch):
    sent = _stub_broker(leerie, monkeypatch, "OK 256 256 42 3")
    assert leerie._cgroup_stat("feat-001") == (256, 256, 42, 3)
    assert sent == ["stat feat-001"]


def test_stat_none_sid_returns_none_without_calling(leerie, monkeypatch):
    sent = _stub_broker(leerie, monkeypatch, "OK 1 2 3 4")
    assert leerie._cgroup_stat(None) is None
    assert sent == []  # short-circuits before the socket round-trip


def test_stat_broker_unreachable_returns_none(leerie, monkeypatch):
    _stub_broker(leerie, monkeypatch, "RAISE no broker")
    assert leerie._cgroup_stat("feat-001") is None


def test_stat_broker_error_returns_none(leerie, monkeypatch):
    _stub_broker(leerie, monkeypatch, "ERR bad sid")
    assert leerie._cgroup_stat("feat-001") is None


def test_stat_malformed_ok_returns_none(leerie, monkeypatch):
    # Wrong arity (including the pre-widening 4-token response) or
    # non-integer fields must not crash the caller.
    _stub_broker(leerie, monkeypatch, "OK 256 256 42")
    assert leerie._cgroup_stat("feat-001") is None
    _stub_broker(leerie, monkeypatch, "OK a b c d")
    assert leerie._cgroup_stat("feat-001") is None


def test_stat_unlimited_max_passthrough(leerie, monkeypatch):
    # Broker reports -1 for an uncapped cgroup; the client passes it through
    # so the detector's `mx > 0` guard suppresses false exhaustion.
    _stub_broker(leerie, monkeypatch, "OK 5 -1 0 0")
    assert leerie._cgroup_stat("feat-001") == (5, -1, 0, 0)


def test_stat_reports_oom_kill_count(leerie, monkeypatch):
    # The 4th token is memory.events' oom_kill counter — the memory-OOM
    # signal `_invoke`'s no-envelope path checks (DESIGN §6 *Detecting
    # memory OOM*), mirroring pids.events.max's role for fork denial.
    _stub_broker(leerie, monkeypatch, "OK 10 1024 0 2")
    assert leerie._cgroup_stat("feat-001") == (10, 1024, 0, 2)


# ---- run-scoped cgroup sid (DESIGN §6 *Memory containment*, run-scoped
#      cgroup name) — concurrent runs sharing one VM's leerie.slice/ must
#      not collide on a bare worker sid, else one run's destroy
#      (v2 cgroup.kill) SIGKILLs another run's worker leader. ---------------

def test_worker_sid_prefixes_with_run_id(leerie):
    """A run id yields `<run-id[:12]>-<sid>` so two runs' same-named workers
    map to distinct cgroups."""
    run = "279400a6d7d79916565abba313a4df0ec9fdd8b91b7fbf73872ad475ec898c8c"
    assert leerie._cgroup_worker_sid(run, "classifier") == \
        "279400a6d7d7-classifier"


def test_worker_sid_none_run_id_is_bare(leerie):
    """No run context (e.g. tests / callers without a run) → bare sid,
    preserving the pre-run-scope name."""
    assert leerie._cgroup_worker_sid(None, "classifier") == "classifier"
    assert leerie._cgroup_worker_sid("", "classifier") == "classifier"


def test_worker_sid_distinct_across_concurrent_runs(leerie):
    """The load-bearing property: the same worker sid in two different runs
    produces two DIFFERENT cgroup names — so one run's cgroup.kill cannot
    reach the other run's worker. This is the regression that reproduced as
    the stackpulse/summarizer classifier collision."""
    a = leerie._cgroup_worker_sid("aaaaaaaaaaaa1111", "classifier")
    b = leerie._cgroup_worker_sid("bbbbbbbbbbbb2222", "classifier")
    assert a != b
    assert a == "aaaaaaaaaaaa-classifier"
    assert b == "bbbbbbbbbbbb-classifier"


def test_worker_sid_passes_broker_validation(leerie):
    """The composed sid must satisfy the broker's OWN `_valid_sid` — else
    create is rejected and the worker runs uncapped. Coupled to the live
    broker (not a copied regex) so it can't drift if `_SID_RE` changes."""
    broker = _load_broker()
    scoped = leerie._cgroup_worker_sid(
        "279400a6d7d79916565abba313a4df0ec9fdd8b91b7fbf73872ad475ec898c8c",
        "fit-judge-docs-001-d0")
    assert broker._valid_sid(scoped)
