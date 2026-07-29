"""Tests for `warn_test_subtask_missing_producer_edge` (DESIGN §5) — the
advisory that flags a `test-`-domain subtask declaring no cross-subtask edge
while the plan has producing subtasks.

This is the dominant real cause of the 2026-07-29 wiring-gate deaths: a test
that needs a not-yet-created source file but declares no requires/depends_on.
The warn is deliberately advisory (the missing edge is semantic — see the
function docstring and the plan's Fix 3 rationale); the wiring gate is the
enforcer. These tests pin the flag/silent boundary, asserted by capturing the
log output.
"""
from __future__ import annotations


def _sub(sid, **kw):
    s = {"id": sid, "title": sid}
    s.update(kw)
    return s


def _warn_out(leerie, capsys, plans) -> str:
    """Run the warn and return everything it printed. `log()` prints to stdout
    (not stdlib logging), so capsys — not caplog — is the capture channel."""
    capsys.readouterr()  # clear
    leerie.warn_test_subtask_missing_producer_edge(plans)
    return capsys.readouterr().out


def _fired(out: str) -> bool:
    return "under-wired test subtask" in out


def test_fires_on_test_with_no_edges_and_a_producer(leerie, capsys):
    plans = [{"domain": "d", "status": "ready", "subtasks": [
        _sub("test-001", files_likely_touched=["src/a.test.ts"]),
        _sub("feat-001", files_likely_touched=["src/a.ts"],
             provides=["a-impl"]),
    ]}]
    out = _warn_out(leerie, capsys, plans)
    assert _fired(out)
    # names the offending sid
    assert "test-001" in out


def test_silent_when_test_declares_requires(leerie, capsys):
    plans = [{"domain": "d", "status": "ready", "subtasks": [
        _sub("test-001", files_likely_touched=["src/a.test.ts"],
             requires=[{"tag": "a-impl", "extent": "in_plan"}]),
        _sub("feat-001", files_likely_touched=["src/a.ts"],
             provides=["a-impl"]),
    ]}]
    assert not _fired(_warn_out(leerie, capsys, plans))


def test_silent_when_test_declares_depends_on(leerie, capsys):
    plans = [{"domain": "d", "status": "ready", "subtasks": [
        _sub("test-001", files_likely_touched=["src/a.test.ts"],
             depends_on=["feat-001"]),
        _sub("feat-001", files_likely_touched=["src/a.ts"]),
    ]}]
    assert not _fired(_warn_out(leerie, capsys, plans))


def test_silent_when_no_other_producer(leerie, capsys):
    # The under-wired test declares no edges, but NO other subtask is a
    # producer (none has provides or files) → nothing to depend on → silent.
    plans = [{"domain": "d", "status": "ready", "subtasks": [
        _sub("test-001"),  # under-wired but nothing produces anything
        _sub("test-002"),  # also bare — not a producer
    ]}]
    assert not _fired(_warn_out(leerie, capsys, plans))


def test_silent_on_single_subtask_plan(leerie, capsys):
    plans = [{"domain": "d", "status": "ready", "subtasks": [
        _sub("test-001", files_likely_touched=["src/a.test.ts"]),
    ]}]
    assert not _fired(_warn_out(leerie, capsys, plans))


def test_non_test_subtask_never_flagged(leerie, capsys):
    # A feat subtask with no edges is NOT the target of this advisory.
    plans = [{"domain": "d", "status": "ready", "subtasks": [
        _sub("feat-002", files_likely_touched=["src/b.ts"]),
        _sub("feat-001", files_likely_touched=["src/a.ts"],
             provides=["a-impl"]),
    ]}]
    assert not _fired(_warn_out(leerie, capsys, plans))


def test_navegando_shape_fires(leerie, capsys):
    """The real navegando shape: test-007 (coverage-floors) declares no edge
    to the feat subtasks whose files it must register. Disjoint paths — the
    exact case a mechanical file-overlap rule misses but this declaration-
    absence check catches."""
    plans = [{"domain": "d", "status": "ready", "subtasks": [
        _sub("test-007", files_likely_touched=["vitest.config.ts",
             "src/tests/coverage-floors-guard.test.ts"]),
        _sub("feat-002", files_likely_touched=["src/hooks/use-io.ts"],
             provides=["io-hook"]),
        _sub("feat-006", files_likely_touched=["src/components/cc.tsx"],
             provides=["cc-component"]),
    ]}]
    out = _warn_out(leerie, capsys, plans)
    assert _fired(out)
    assert "test-007" in out


def test_empty_plan_no_crash(leerie, capsys):
    assert not _fired(_warn_out(leerie, capsys, []))
    assert not _fired(_warn_out(leerie, capsys,
                                  [{"domain": "d", "subtasks": []}]))
