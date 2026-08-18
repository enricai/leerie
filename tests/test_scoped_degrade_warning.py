"""`scoped` silently behaving as `full` must be said out loud (DESIGN §9).

`scoped` is the default and an axis with no resolvable proxy falls back to the
canonical command — correct, and deliberately not a silent skip. But nothing
told the operator, so a pytest repo paid the full oracle once per subtask
while believing it had asked for a cheap falsifier. Measured across the run
corpus: every other repo resolved a proxy on ~99% of subtasks; this one
resolved none on 16 of 16, spending 6.9 h in orchestrator-run full suites.
"""
from __future__ import annotations

import ast
import inspect

import pytest


@pytest.fixture(autouse=True)
def _reset(leerie):
    """The latch is module-level (once per process, not once per subtask), so
    it leaks across tests unless cleared on both sides."""
    leerie._scoped_degrade_warned = False
    yield
    leerie._scoped_degrade_warned = False


def _warns(leerie, capture, blt, scoped, mode="scoped"):
    leerie._warn_scoped_degraded_once(blt, scoped, mode)
    return [m for m in capture if "no delta proxy resolved" in m]


@pytest.fixture
def capture(leerie, monkeypatch):
    msgs = []
    monkeypatch.setattr(leerie, "log", lambda m, *a, **k: msgs.append(str(m)))
    return msgs


def test_fires_when_a_canonical_axis_has_no_proxy(leerie, capture):
    got = _warns(leerie, capture, {"test": "pytest"}, {})
    assert len(got) == 1
    assert "tests" in got[0]


def test_silent_when_a_proxy_resolves(leerie, capture):
    """THE ANTI-VACUITY PARTNER. Without it a warning that fired
    unconditionally would pass the test above, and the signal would be noise
    on the ~99% of repos where scoping actually works."""
    assert _warns(leerie, capture,
                  {"test": "pnpm test"},
                  {"test": "npx vitest related --run {files}"}) == []


@pytest.mark.parametrize("mode", ["full", "off"])
def test_silent_when_the_operator_chose_the_mode(leerie, capture, mode):
    """`full` is not a degrade — it is what was asked for."""
    assert _warns(leerie, capture, {"test": "pytest"}, {}, mode) == []


def test_silent_when_the_repo_defines_no_command_for_the_axis(leerie, capture):
    """An axis the repo does not define is absent, not degraded."""
    assert _warns(leerie, capture, {"test": "", "build": "  "}, {}) == []


def test_fires_only_once_per_process(leerie, capture):
    for _ in range(5):
        leerie._warn_scoped_degraded_once({"test": "pytest"}, {}, "scoped")
    assert len([m for m in capture if "no delta proxy resolved" in m]) == 1


def test_names_every_degraded_axis_and_both_remedies(leerie, capture):
    got = _warns(leerie, capture,
                 {"build": "make", "test": "pytest"},
                 {"build": "npx tsc --noEmit"})
    assert len(got) == 1
    msg = got[0]
    assert "tests" in msg and "build" not in msg.split("for:")[1].split(".")[0]
    assert "test_scoped" in msg and "--subtask-tests off" in msg
    assert "{test_files}" in msg, "must point pytest repos at the right placeholder"


def test_partial_degrade_reports_only_the_unresolved_axis(leerie, capture):
    got = _warns(leerie, capture,
                 {"build": "next build", "test": "pytest"},
                 {"build": "npx tsc --noEmit"})
    assert "tests" in got[0].split("for:")[1]


# --------------------------------------------------------------------------
# wiring: the helper is inert unless phase_execute calls it, unconditionally
# --------------------------------------------------------------------------

def test_the_call_cannot_abort_phase_execute(leerie):
    """REGRESSION (CI, PR #217). The call reads `.leerie/config.toml` via
    `resolve_blt`/`resolve_blt_scoped` at phase entry, where nothing touched
    the filesystem before — so `tests/test_disk_preflight.py`, which drives
    `phase_execute` with a Mock `st`, died on `Mock / str` in `_load_blt_config`
    and took the disk-preflight coverage with it. An advisory warning must
    never be able to abort a run; the baseline call three lines below carries
    the same defence for the same reason."""
    src = inspect.getsource(leerie.phase_execute)
    i = src.index("_warn_scoped_degraded_once(")
    before = src[:i]
    assert before.rstrip().endswith("try:"), (
        "the advisory call must sit inside a try/except — it reads the "
        "filesystem at phase entry and cannot be allowed to raise")
    after = src[i:i + 700]
    assert "except Exception" in after


def test_phase_execute_calls_it_before_the_baseline(leerie):
    src = inspect.getsource(leerie.phase_execute)
    assert "_warn_scoped_degraded_once(" in src
    assert src.index("_warn_scoped_degraded_once(") < src.index(
        'st.data.get("skip_base_baseline")'), \
        "must precede the baseline block so it fires before any wave spends"


def test_the_call_is_not_nested_under_skip_base_baseline(leerie):
    """The baseline is sentinel-skipped on resume and flag-skipped by
    --skip-base-baseline. A call nested inside that guard would go silent on
    exactly the runs where the operator most needs telling."""
    tree = ast.parse(inspect.getsource(leerie.phase_execute).lstrip())
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        if "skip_base_baseline" not in ast.unparse(node.test):
            continue
        assert "_warn_scoped_degraded_once" not in ast.unparse(node), \
            "the warning is nested under the baseline guard"
