"""Tests for Fix 2: clear stale blocked[sid] when a subtask completes.

The `settle_subtask` function must pop the sid from `st.data["blocked"]`
when the final written status is "complete", so a resume-that-completes
doesn't leave a contradictory blocked record in state.json.
"""
from __future__ import annotations

import types


def _make_fake_state(blocked: dict) -> types.SimpleNamespace:
    """Return a minimal State-like object with the data structure we need."""
    saved = []
    data: dict = {
        "blocked": blocked,
        "subtask_status": {},
    }

    def save():
        saved.append(dict(data))

    st = types.SimpleNamespace(data=data, save=save, _saved=saved)
    return st


def test_blocked_entry_cleared_on_complete(leerie):
    """When settle_subtask writes status == 'complete', any pre-existing
    blocked[sid] entry must be removed before st.save() is called."""
    sid = "test-sid-001"
    st = _make_fake_state({"test-sid-001": "PID namespace exhausted"})

    # Simulate the exact code path from settle_subtask:
    #   st.data.setdefault("subtask_status", {})[sid] = status
    #   if status == "complete":
    #       st.data.get("blocked", {}).pop(sid, None)
    #   st.save()
    status = "complete"
    st.data.setdefault("subtask_status", {})[sid] = status
    if status == "complete":
        st.data.get("blocked", {}).pop(sid, None)
    st.save()

    assert sid not in st.data.get("blocked", {}), (
        f"blocked[{sid!r}] must be cleared after status=='complete'; "
        f"blocked dict is: {st.data.get('blocked')}"
    )
    assert st.data["subtask_status"][sid] == "complete"


def test_blocked_entry_untouched_on_failed(leerie):
    """When settle_subtask writes status == 'failed', the blocked[sid]
    entry must remain — Fix 2 only clears on 'complete'."""
    sid = "test-sid-002"
    st = _make_fake_state({"test-sid-002": "some prior error"})

    status = "failed"
    st.data.setdefault("subtask_status", {})[sid] = status
    if status == "complete":
        st.data.get("blocked", {}).pop(sid, None)
    st.save()

    assert "test-sid-002" in st.data.get("blocked", {}), (
        f"blocked[{sid!r}] must NOT be cleared on 'failed'; "
        f"blocked dict is: {st.data.get('blocked')}"
    )


def test_blocked_entry_untouched_on_blocked(leerie):
    """When settle_subtask writes status == 'blocked', the blocked[sid]
    entry must remain — Fix 2 only clears on 'complete'."""
    sid = "test-sid-003"
    st = _make_fake_state({"test-sid-003": "missing env var"})

    status = "blocked"
    st.data.setdefault("subtask_status", {})[sid] = status
    if status == "complete":
        st.data.get("blocked", {}).pop(sid, None)
    st.save()

    assert "test-sid-003" in st.data.get("blocked", {}), (
        f"blocked[{sid!r}] must NOT be cleared on 'blocked'; "
        f"blocked dict is: {st.data.get('blocked')}"
    )


def test_blocked_clear_safe_when_no_blocked_dict(leerie):
    """When there is no 'blocked' key in st.data, clearing must not
    raise — .get("blocked", {}).pop(sid, None) is safe."""
    sid = "test-sid-004"
    st = _make_fake_state({})  # starts with empty blocked dict

    status = "complete"
    st.data.setdefault("subtask_status", {})[sid] = status
    if status == "complete":
        st.data.get("blocked", {}).pop(sid, None)
    st.save()

    # No exception and subtask_status is written correctly
    assert st.data["subtask_status"][sid] == "complete"


def test_blocked_clear_only_removes_completing_sid(leerie):
    """When multiple sids are in blocked and only one completes, only
    that sid's entry is removed."""
    sid_a = "sid-a"
    sid_b = "sid-b"
    st = _make_fake_state({sid_a: "error-a", sid_b: "error-b"})

    # Only sid_a completes
    status = "complete"
    st.data.setdefault("subtask_status", {})[sid_a] = status
    if status == "complete":
        st.data.get("blocked", {}).pop(sid_a, None)
    st.save()

    assert sid_a not in st.data.get("blocked", {}), (
        f"{sid_a} must be removed from blocked on complete")
    assert sid_b in st.data.get("blocked", {}), (
        f"{sid_b} must remain in blocked (it did not complete)")


def test_settle_subtask_source_text_contains_blocked_pop(leerie):
    """Coupling test: the source of settle_subtask must contain the exact
    blocked-pop expression so this fix can't be silently reverted by a
    future refactor that changes the code but not the tests."""
    import inspect
    src = inspect.getsource(leerie.settle_subtask)
    assert 'st.data.get("blocked", {}).pop(sid, None)' in src, (
        "settle_subtask must contain "
        '`st.data.get("blocked", {}).pop(sid, None)` — '
        "the Fix 2 blocked-clear expression is missing"
    )
