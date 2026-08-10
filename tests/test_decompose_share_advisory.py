"""`_warn_decomposition_share` — the advisory that records decomposition's
share of a run's realized spend (N3+N4).

Advisory by construction: `_bump_decompose_workers`' hard gate is a runaway
backstop sized against `max_total_workers`, and at the shipped defaults it
trips at 800 decomposition calls while the worst run in a 143-run corpus
spent 348 — so it fires on nothing. The share of *realized* spend is the
figure the 40% was originally derived from, but it cannot be a live gate:
during planning the denominator is still tiny, so the ratio starts near 1.0
and would refuse healthy runs at ~7 calls.

This file exists because the advisory shipped with no tests at all — new
behaviour, a new `STATE_FIELDS` entry and a new log line, in a repo that
otherwise source-pins individual call sites.
"""
from __future__ import annotations

import inspect

import pytest


class _St:
    """Minimal State stand-in: the advisory only reads/writes `data` and
    calls `save()`."""

    def __init__(self, data):
        self.data = dict(data)
        self.saves = 0

    def save(self):
        self.saves += 1


def _caps(leerie, **over):
    c = {"decompose_budget_share": 0.40, "max_total_workers": 2000}
    c.update(over)
    return c


def test_records_the_share(leerie):
    st = _St({"worker_count": 100, "decompose_worker_count": 13})
    got = leerie._warn_decomposition_share(st, _caps(leerie))
    assert got == pytest.approx(0.13)
    assert st.data["decompose_share"] == pytest.approx(0.13)


def test_persists_through_save(leerie):
    """An in-memory-only write is lost on pause or crash, and the whole
    point of the field is that a later calibration sweep can read it."""
    st = _St({"worker_count": 50, "decompose_worker_count": 25})
    leerie._warn_decomposition_share(st, _caps(leerie))
    assert st.saves >= 1, "the advisory must st.save() the value it records"


def test_warns_at_or_above_the_threshold(leerie, capsys):
    st = _St({"worker_count": 100, "decompose_worker_count": 40})
    leerie._warn_decomposition_share(st, _caps(leerie))
    out = capsys.readouterr()
    assert "decomposition used 40/100" in (out.out + out.err)


def test_silent_below_the_threshold(leerie, capsys):
    """A healthy run (corpus p50 15%) must not emit a warning, or the
    signal is noise."""
    st = _St({"worker_count": 100, "decompose_worker_count": 15})
    leerie._warn_decomposition_share(st, _caps(leerie))
    out = capsys.readouterr()
    assert "decomposition used" not in (out.out + out.err)


def test_no_spend_yet_is_a_no_op(leerie):
    """Guards the ZeroDivisionError, and a missing key entirely."""
    st = _St({})
    assert leerie._warn_decomposition_share(st, _caps(leerie)) is None
    assert "decompose_share" not in st.data
    st2 = _St({"worker_count": 0, "decompose_worker_count": 0})
    assert leerie._warn_decomposition_share(st2, _caps(leerie)) is None


def test_is_advisory_never_raises(leerie):
    """It must not be able to abort planning. A run that spent every call
    on decomposition is exactly the pathology being recorded — recording it
    must not also kill the run."""
    st = _St({"worker_count": 200, "decompose_worker_count": 200})
    assert leerie._warn_decomposition_share(st, _caps(leerie)) == 1.0


def test_threshold_comes_from_caps_not_a_literal(leerie, capsys):
    """An operator-lowered share must move the advisory with it."""
    st = _St({"worker_count": 100, "decompose_worker_count": 20})
    leerie._warn_decomposition_share(st, _caps(leerie,
                                               decompose_budget_share=0.10))
    assert "decomposition used 20/100" in "".join(capsys.readouterr())


def test_tolerates_a_caps_dict_missing_max_total_workers(leerie, capsys):
    """The whole point of the `.get()` — and the one case no other test in
    this file constructs.

    Every other test here passes a caps dict carrying `max_total_workers`,
    so the bracket lookup this replaced would have passed all of them. It
    KeyErrors only on a partial-caps caller, and only on the branch that
    runs when the warning fires — the two conditions have to coincide, so
    the fix was shipped untested. `run_recapture_deps`, `run_rebaser` and
    `_replay_capture` each build their own minimal caps.
    """
    st = _St({"worker_count": 100, "decompose_worker_count": 60})  # 60%
    share = leerie._warn_decomposition_share(st, {})
    assert share == 0.6
    out = "".join(capsys.readouterr())
    assert "decomposition used 60/100" in out
    # The fallback figure came from DEFAULT_CAPS, not from a KeyError path.
    left = leerie.DEFAULT_CAPS["max_total_workers"] - 100
    assert f"{left} of {leerie.DEFAULT_CAPS['max_total_workers']}" in out


def test_declared_in_state_fields(leerie):
    """Guard-the-guard for the generic parity sweep: this key must be
    declared, or `resume` carries an undeclared field."""
    assert "decompose_share" in leerie.STATE_FIELDS


def test_wired_into_phase_plan(leerie):
    """Source-coupled: the advisory is inert unless `phase_plan` calls it,
    and it must run AFTER the expansion loop — before it, the denominator
    excludes the decomposition spend it is measuring."""
    src = inspect.getsource(leerie.phase_plan)
    assert "_warn_decomposition_share(st, caps)" in src
    i_expand = src.index("_recursive_decompose(")
    i_warn = src.index("_warn_decomposition_share")
    assert i_expand < i_warn, (
        "the advisory must run after expansion, or it measures a "
        "denominator that excludes what it is reporting on")
