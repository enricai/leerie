"""Guard: judgment phases log their check-loop warnings BEFORE die().

`_run_checked_loop` catches a worker exception and stores the underlying
cause as a warning ("<name> round N: worker crashed: <exc>"). A crash on
every round leaves result=None (a `WorkerError` is retried first — it is
infrastructure, not a verdict — so None means the retries were exhausted
too; any other exception abandons the loop immediately). Every judgment
phase then die()s on the None — but four of them logged the warnings
*after* the die. `die()` calls sys.exit(), so those loops were
unreachable and the cause was destroyed: the operator saw only the
generic "<worker> crashed and produced no result" with no hint of why.

That is not a cosmetic bug. It is what made a real classifier crash
undiagnosable — the exception text (which distinguishes an OOM from a
nonzero exit from a missing result event) never reached the terminal, and
it is unrecoverable after the fact because nothing else persists it.

These are source-coupling guards (mirroring test_dep_capture_wiring.py):
the ordering is only observable by inspection, since die() exits.
"""
from __future__ import annotations

import inspect

import pytest


# (function name, the log-prefix string it uses for its warnings)
_PHASES = [
    ("phase_classify", '  classifier: '),
    ("phase_provision", '  provision: '),
    ("phase_reconcile", '  reconciler: '),
    ("phase_overlap_judge", '  overlap-judge: '),
]


def _find_phase(leerie, name):
    fn = getattr(leerie, name, None)
    if fn is None:
        pytest.skip(f"{name} not present under that name")
    return inspect.getsource(fn)


@pytest.mark.parametrize("fn_name,prefix", _PHASES)
def test_warnings_are_logged_before_die(leerie, fn_name, prefix):
    src = _find_phase(leerie, fn_name)
    assert prefix in src, f"{fn_name}: warning log line not found"
    log_at = src.index(prefix)
    # The die() guarded by the None check — the first die mentioning
    # "crashed and produced no result".
    die_at = src.index("crashed and produced no result")
    assert log_at < die_at, (
        f"{fn_name}: warnings are logged AFTER die() — die() calls "
        f"sys.exit(), so the failure cause is destroyed unprinted")


def test_run_checked_loop_still_records_the_exception_text(leerie):
    """The warnings are only worth printing because they carry the cause.
    Pin that `_run_checked_loop` still interpolates the exception — on
    **both** of its crash arms.

    A bare `"worker crashed: {exc}" in src` is not enough any more. The loop
    now has two crash arms — `except WorkerError` (retries; infrastructure)
    and `except Exception` (breaks; a leerie bug) — so a substring check
    matches whichever one it finds first and would keep passing if the other
    lost its cause. Count both."""
    src = inspect.getsource(leerie._run_checked_loop)
    assert src.count("worker crashed: {exc}") == 2, (
        "both crash arms of _run_checked_loop must record the exception "
        "text: the WorkerError arm (which retries) and the catch-all arm "
        "(which breaks). Found "
        f"{src.count('worker crashed: {exc}')}")


def test_integrate_wave_logs_warnings_before_die(leerie):
    """`integrate_wave`'s crash path is the same convention as the four
    phases above, but its die string differs ("crashed; merge aborted"), so
    the parametrized guard cannot cover it.

    Pinned separately because this site is where the convention was *missed*
    once already: the crash path used to die() without recording
    `blocked[sid]` or logging its warnings at all, which is precisely how a
    PID-exhausted integrator's cause went unprinted."""
    src = inspect.getsource(leerie.integrate_wave)
    log_at = src.index("for w in int_warnings")
    die_at = src.index("crashed; merge aborted")
    assert log_at < die_at, (
        "integrate_wave: the crash path must log its int_warnings BEFORE "
        "die() — die() calls sys.exit(), so anything after it is dead code "
        "and the crash cause is destroyed unprinted")


def test_integrate_wave_records_blocked_before_die(leerie):
    """The other half of the local convention its own neighbor documents:
    record the block to state before dying, so `--resume` and the operator
    can see why the run stopped."""
    src = inspect.getsource(leerie.integrate_wave)
    blocked_at = src.index('setdefault("blocked", {})[sid]')
    die_at = src.index("crashed; merge aborted")
    assert blocked_at < die_at, (
        "integrate_wave: blocked[sid] must be recorded (and saved) before "
        "die()")


def test_die_exits_immediately(leerie):
    """The premise of the whole guard: anything after die() is dead code."""
    src = inspect.getsource(leerie.die)
    assert "sys.exit(" in src
