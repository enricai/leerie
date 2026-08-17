"""Unit tests for the burst-reservation token pair that backs the memory
admission gate (DESIGN §6 Memory containment): `_reserve_worker_memory_admission`
issues a monotonically increasing release token and records its reservation
timestamp in the module-level `_active_admissions` dict; `_release_worker_memory_admission`
removes that entry and is idempotent on `None` or an already-released token.

The larger gate consumers (tests/test_slice_aware_memory.py,
tests/test_memory_admission_degrade.py) exercise this pair indirectly but
never reference it by name.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _reset_admissions(leerie):
    """`_active_admissions` is module-level mutable state and conftest's
    `leerie` fixture is **session-scoped** — the module is loaded once for
    the whole suite, so entries survive across tests AND across files.
    Clear on both sides: before, so these tests are order-independent;
    after, so they cannot perturb any other file that exercises the gate."""
    leerie._active_admissions.clear()
    yield
    leerie._active_admissions.clear()


def test_reserve_returns_a_new_unique_token_each_call(leerie):
    t1 = leerie._reserve_worker_memory_admission()
    t2 = leerie._reserve_worker_memory_admission()
    t3 = leerie._reserve_worker_memory_admission()
    assert len({t1, t2, t3}) == 3


def test_reserve_tokens_are_monotonically_increasing(leerie):
    t1 = leerie._reserve_worker_memory_admission()
    t2 = leerie._reserve_worker_memory_admission()
    t3 = leerie._reserve_worker_memory_admission()
    assert t1 < t2 < t3


def test_reserve_records_token_in_active_admissions(leerie):
    token = leerie._reserve_worker_memory_admission()
    assert token in leerie._active_admissions


def test_reserve_records_a_monotonic_timestamp(leerie, monkeypatch):
    fake_clock = iter([100.0])
    monkeypatch.setattr(leerie.time, "monotonic", lambda: next(fake_clock))
    token = leerie._reserve_worker_memory_admission()
    assert leerie._active_admissions[token] == 100.0


def test_release_removes_the_entry(leerie):
    token = leerie._reserve_worker_memory_admission()
    assert token in leerie._active_admissions
    leerie._release_worker_memory_admission(token)
    assert token not in leerie._active_admissions


def test_release_only_removes_the_named_token(leerie):
    t1 = leerie._reserve_worker_memory_admission()
    t2 = leerie._reserve_worker_memory_admission()
    leerie._release_worker_memory_admission(t1)
    assert t1 not in leerie._active_admissions
    assert t2 in leerie._active_admissions


def test_release_is_a_noop_on_none(leerie):
    token = leerie._reserve_worker_memory_admission()
    leerie._release_worker_memory_admission(None)
    assert token in leerie._active_admissions
    assert leerie._active_admissions == {token: leerie._active_admissions[token]}


def test_release_is_idempotent_on_an_already_released_token(leerie):
    token = leerie._reserve_worker_memory_admission()
    leerie._release_worker_memory_admission(token)
    assert token not in leerie._active_admissions
    leerie._release_worker_memory_admission(token)
    assert token not in leerie._active_admissions


def test_release_is_a_noop_on_an_unknown_token(leerie):
    leerie._reserve_worker_memory_admission()
    leerie._release_worker_memory_admission(999999)
    assert len(leerie._active_admissions) == 1
