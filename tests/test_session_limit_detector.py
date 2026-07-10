"""Tests for `detect_session_limit()` — the Claude Code subscription
session-limit / rate-limit message detector.

The detector is the single load-bearing surface for the rate-limit
auto-resume contract (DESIGN §6 *Cleanup on abnormal exit*): if it
returns a `RateLimitedExit` with a parseable `reset_at`, main() will
sleep until that moment and `os.execv` the orchestrator into
`--resume` (sys.executable __file__ --resume --run-id <id>). A wrong
parse here would produce a wrong-time sleep — strictly worse than no
auto-resume — so the detector must be conservative: only return a
non-None `reset_at` when every step of the parse (regex match,
integer conversion, AM/PM normalization, ZoneInfo lookup, range
checks) succeeds.

Empirical anchor: the verbatim message text observed identical across
three independent runs on 2026-05-27 is:

    "You've hit your session limit · resets 3:10am (America/Bogota)"
"""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


# The verbatim observed message — the load-bearing test anchor.
VERBATIM = "You've hit your session limit · resets 3:10am (America/Bogota)"


# --- positive cases -------------------------------------------------------

def test_verbatim_message_returns_exit_with_parsed_reset_at(leerie):
    exc = leerie.detect_session_limit(VERBATIM)
    assert exc is not None
    assert exc.raw_message == VERBATIM
    assert exc.reset_at is not None
    # 3:10am in America/Bogota — assertable independent of "now"
    assert exc.reset_at.hour == 3
    assert exc.reset_at.minute == 10
    assert exc.reset_at.tzinfo.key == "America/Bogota"


def test_pm_time_normalizes_correctly(leerie):
    text = "You've hit your session limit · resets 7:30pm (UTC)"
    exc = leerie.detect_session_limit(text)
    assert exc is not None
    assert exc.reset_at.hour == 19
    assert exc.reset_at.minute == 30


def test_midnight_12am_normalizes_to_hour_zero(leerie):
    text = "You've hit your session limit · resets 12:00am (UTC)"
    exc = leerie.detect_session_limit(text)
    assert exc is not None
    assert exc.reset_at.hour == 0


def test_noon_12pm_stays_hour_twelve(leerie):
    text = "You've hit your session limit · resets 12:00pm (UTC)"
    exc = leerie.detect_session_limit(text)
    assert exc is not None
    assert exc.reset_at.hour == 12


def test_case_insensitive_prefix(leerie):
    text = "YOU'VE HIT YOUR SESSION LIMIT · resets 3:10am (UTC)"
    exc = leerie.detect_session_limit(text)
    assert exc is not None


def test_case_insensitive_ampm(leerie):
    text = "You've hit your session limit · resets 3:10AM (UTC)"
    exc = leerie.detect_session_limit(text)
    assert exc is not None
    assert exc.reset_at.hour == 3


def test_reset_time_in_past_rolls_to_tomorrow(leerie, monkeypatch):
    """If the parsed reset time is earlier than now (or equal), it's
    tomorrow. Without this the auto-resume would sleep for a negative
    duration and skip entirely."""
    tz = ZoneInfo("UTC")
    fixed_now = datetime(2026, 5, 27, 23, 0, 0, tzinfo=tz)

    class _FrozenDateTime:
        @classmethod
        def now(cls, tz=None):
            return fixed_now if tz is None else fixed_now.astimezone(tz)
    monkeypatch.setattr(leerie, "datetime", _FrozenDateTime)

    text = "You've hit your session limit · resets 1:00am (UTC)"
    exc = leerie.detect_session_limit(text)
    assert exc is not None
    assert exc.reset_at is not None
    # Tomorrow's 1am, not today's
    assert exc.reset_at.date() == (fixed_now + timedelta(days=1)).date()
    assert exc.reset_at.hour == 1


# --- negative cases — must NOT match --------------------------------------

def test_empty_text_returns_none(leerie):
    assert leerie.detect_session_limit("") is None


def test_unrelated_text_returns_none(leerie):
    assert leerie.detect_session_limit("The user asked a question.") is None


def test_workers_discussing_rate_limit_code_returns_none(leerie):
    """The barnacle false-positive case I found in worker logs — a
    legitimate assistant text discussing rate-limit handling in code.
    Must NOT match. The detector's load-bearing requirement is that
    broader 'rate-limit' / 'rate-limited' patterns are NOT used; only
    the literal Claude Code marketing-copy prefix counts."""
    text = ('Now let me also downgrade the duplicate pino log lines to '
            'debug. `logger.warn` at line ~211: "hot path rate-limited '
            'for ${siteId}... not falling back" — this duplicates the '
            'event content.')
    assert leerie.detect_session_limit(text) is None


def test_general_rate_limit_mention_returns_none(leerie):
    text = "The API was rate-limited, but we caught the 429 and retried."
    assert leerie.detect_session_limit(text) is None


# --- parse-failure cases — must match but with reset_at=None --------------

def test_unknown_timezone_returns_exit_with_none_reset(leerie):
    """An unparseable timezone name must produce a clean fallback to
    manual --resume, not a wrong-time sleep."""
    text = "You've hit your session limit · resets 3:10am (Mars/Olympus)"
    exc = leerie.detect_session_limit(text)
    assert exc is not None
    assert exc.reset_at is None


def test_no_reset_clause_returns_exit_with_none_reset(leerie):
    """The prefix matches but there's no `resets ...` clause."""
    text = "You've hit your session limit — please try again later."
    exc = leerie.detect_session_limit(text)
    assert exc is not None
    assert exc.reset_at is None


def test_malformed_time_returns_exit_with_none_reset(leerie):
    """Hour out of range (25:xx) must fall back to None, not crash."""
    text = "You've hit your session limit · resets 25:00am (UTC)"
    exc = leerie.detect_session_limit(text)
    assert exc is not None
    assert exc.reset_at is None


def test_malformed_minute_returns_exit_with_none_reset(leerie):
    text = "You've hit your session limit · resets 3:99am (UTC)"
    exc = leerie.detect_session_limit(text)
    assert exc is not None
    assert exc.reset_at is None


# --- exception shape ------------------------------------------------------

def test_rate_limited_exit_is_baseexception(leerie):
    """Must subclass BaseException (not Exception) so the broad
    `except Exception` handlers inside orchestrate() don't swallow it
    — same pattern as InterruptedBySignal."""
    assert issubclass(leerie.RateLimitedExit, BaseException)
    assert not issubclass(leerie.RateLimitedExit, Exception)


def test_rate_limited_exit_carries_fields(leerie):
    """The exit's `reset_at`, `raw_message`, and `out_of_credits` are how
    main()'s handler decides between pause-and-surface, sleep-to-reset, and
    fixed-backoff auto-resume — they must be set as attributes, not just
    constructor args. out_of_credits defaults False (the rate-limit case)."""
    exc = leerie.RateLimitedExit(reset_at=None, raw_message="hi")
    assert exc.reset_at is None
    assert exc.raw_message == "hi"
    assert str(exc) == "hi"
    assert exc.out_of_credits is False
    exc2 = leerie.RateLimitedExit(
        reset_at=None, raw_message="broke", out_of_credits=True)
    assert exc2.out_of_credits is True


# --- main() arm integration ------------------------------------------------

def test_main_has_rate_limited_exit_arm():
    """Pin that main() catches RateLimitedExit. Without this arm the
    exception would fall through to the catch-all BaseException
    handler and the auto-resume path wouldn't run."""
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent
           / "orchestrator" / "leerie.py").read_text()
    assert "except RateLimitedExit" in src


def test_main_rate_limit_arm_appears_before_keyboard_interrupt():
    """except RateLimitedExit must be matched BEFORE main()'s except
    KeyboardInterrupt — both inherit BaseException, but the more
    specific one needs to come first or it never fires. Note there's
    ALSO a local `except KeyboardInterrupt` inside `_sleep_then_reexec`
    (the auto-resume sleep guard) that appears earlier in the file, so
    we compare the RateLimitedExit arm against the FIRST KeyboardInterrupt
    that follows it (main()'s arm), not the first in the file."""
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent
           / "orchestrator" / "leerie.py").read_text()
    rl_pos = src.find("except RateLimitedExit")
    assert rl_pos != -1
    ki_after = src.find("except KeyboardInterrupt", rl_pos)
    assert ki_after != -1, "main()'s except KeyboardInterrupt must follow the arm"


# --- _sleep_then_reexec (auto-resume tail) --------------------------------
# Shared by the clock-based rate-limit arms: cleanup → sleep → os.execv
# --resume. A reset_at=None rate-limit (unparseable session-limit message)
# auto-resumes on a fixed backoff instead of exiting 75 — these pin that
# behavior. Out-of-credits does NOT route through this helper (it
# pauses-and-surfaces); see test_main_out_of_credits_pauses_and_surfaces.

from types import SimpleNamespace  # noqa: E402


def _fake_st(run_id="run-abc"):
    return SimpleNamespace(run_id=run_id, run_dir=None, data={})


def test_sleep_then_reexec_cleans_sleeps_and_reexecs(leerie, monkeypatch):
    """Happy path: cleanup runs, time.sleep gets the wait, os.execv is
    invoked with `--resume --run-id <id>` (and never returns)."""
    calls = {}
    monkeypatch.setattr(leerie, "_cleanup_on_abnormal_exit",
                        lambda st, **k: calls.setdefault("cleanup", True))
    monkeypatch.setattr(leerie.time, "sleep",
                        lambda s: calls.setdefault("slept", s))

    def fake_execv(exe, argv):
        calls["execv"] = argv
        raise SystemExit(0)  # stand in for "execv replaces the process"
    monkeypatch.setattr(leerie.os, "execv", fake_execv)

    st = _fake_st("run-xyz")
    import pytest
    with pytest.raises(SystemExit):
        leerie._sleep_then_reexec(st, 300, "rate limited / no reset time")

    assert calls.get("cleanup") is True
    assert calls.get("slept") == 300
    argv = calls.get("execv")
    assert argv is not None
    assert "--resume" in argv and "--run-id" in argv
    assert argv[argv.index("--run-id") + 1] == "run-xyz"


def test_sleep_then_reexec_ctrl_c_during_sleep_returns_130(leerie, monkeypatch):
    """Ctrl-C during the sleep → returns 130 (caller sets exit_code) and does
    NOT os.execv — state is preserved for a manual --resume."""
    execv_called = {"v": False}
    monkeypatch.setattr(leerie, "_cleanup_on_abnormal_exit", lambda st, **k: None)

    def boom(_s):
        raise KeyboardInterrupt
    monkeypatch.setattr(leerie.time, "sleep", boom)
    monkeypatch.setattr(leerie.os, "execv",
                        lambda *a: execv_called.__setitem__("v", True))

    rc = leerie._sleep_then_reexec(_fake_st(), 300, "reason")
    assert rc == 130
    assert execv_called["v"] is False


def test_sleep_then_reexec_sigterm_during_sleep_returns_128_plus_signum(
        leerie, monkeypatch):
    """SIGTERM/SIGHUP during the sleep → InterruptedBySignal caught, mapped to
    128+signum (SIGTERM=15→143), NOT allowed to escape as a bare traceback with
    exit 1. State preserved (cleanup already ran)."""
    execv_called = {"v": False}
    monkeypatch.setattr(leerie, "_cleanup_on_abnormal_exit", lambda st, **k: None)

    def boom(_s):
        raise leerie.InterruptedBySignal("SIGTERM")
    monkeypatch.setattr(leerie.time, "sleep", boom)
    monkeypatch.setattr(leerie.os, "execv",
                        lambda *a: execv_called.__setitem__("v", True))

    rc = leerie._sleep_then_reexec(_fake_st(), 300, "reason")
    assert rc == 143, f"SIGTERM should map to 128+15=143, got {rc}"
    assert execv_called["v"] is False


def test_sleep_then_reexec_execv_failure_returns_75(leerie, monkeypatch):
    """If os.execv itself fails (should-never-happen), the helper catches the
    OSError and returns 75 (EX_TEMPFAIL) rather than letting a bare traceback
    escape past the sibling except arms. State preserved for a manual --resume."""
    monkeypatch.setattr(leerie, "_cleanup_on_abnormal_exit", lambda st, **k: None)
    monkeypatch.setattr(leerie.time, "sleep", lambda _s: None)

    def bad_execv(*_a):
        raise OSError("no such interpreter")
    monkeypatch.setattr(leerie.os, "execv", bad_execv)

    rc = leerie._sleep_then_reexec(_fake_st(), 300, "reason")
    assert rc == leerie.EXIT_LOCKED == 75


def test_rate_limit_no_reset_uses_fixed_backoff_not_exit_75(leerie):
    """Source-pin the new contract: the reset_at-None arm calls
    `_sleep_then_reexec(... RATE_LIMIT_RETRY_BACKOFF_SEC ...)` instead of
    printing 'resume manually' + exit 75."""
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent
           / "orchestrator" / "leerie.py").read_text()
    assert "RATE_LIMIT_RETRY_BACKOFF_SEC = 300" in src
    # the handler routes both arms through the shared helper
    assert "_sleep_then_reexec(st, wait_seconds, reason)" in src
    # the old "could not parse reset time … exit 75" manual path is gone
    assert "could not parse reset time" not in src


def test_main_out_of_credits_pauses_and_surfaces(leerie):
    """Source-pin the pause-and-surface contract: the RateLimitedExit arm
    checks `e.out_of_credits` and, for that case, exits EXIT_LOCKED with a
    --resume hint instead of calling _sleep_then_reexec (out-of-credits has
    no reset clock, so auto-resuming would spin against the wall). The check
    must sit inside the RateLimitedExit arm and before the reset_at branch."""
    import inspect
    src = inspect.getsource(leerie.main)
    arm = src[src.find("except RateLimitedExit"):]
    # the arm must branch on the flag...
    assert "if e.out_of_credits:" in arm
    ooc = arm.find("if e.out_of_credits:")
    reset_branch = arm.find("if e.reset_at is not None")
    assert ooc != -1 and reset_branch != -1
    # ...before the clock-based reset_at branch...
    assert ooc < reset_branch, (
        "out_of_credits must be checked before the reset_at auto-resume arm")
    # ...and the out-of-credits body (up to the `else:` that opens the
    # rate-limit arm) routes to EXIT_LOCKED and never *calls*
    # _sleep_then_reexec.
    else_pos = arm.find("\n        else:", ooc)
    assert else_pos != -1, "out-of-credits `if` must be paired with an `else:`"
    ooc_block = arm[ooc:else_pos]
    assert "EXIT_LOCKED" in ooc_block
    assert "_sleep_then_reexec(" not in ooc_block
