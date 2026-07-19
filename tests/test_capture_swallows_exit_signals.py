"""The best-effort `capture_repo_deps` calls must swallow the
`BaseException`-derived exit signals (`TerminalAuthFailure` /
`RateLimitedExit`), not just `Exception`.

Incident (2026-07-19, wave 1 of 4): a run's container OAuth session expired
mid-wave. `claude_p` correctly raised `TerminalAuthFailure` and `main()`'s
`except TerminalAuthFailure` handler began its resumable-pause dance
(worktree-only cleanup, `--resume` hint, `exit_code = EXIT_LOCKED`). But that
handler then makes a *best-effort* `capture_repo_deps(...)` call — which
invokes `claude -p` again, re-hits the still-dead auth, and raises a *second*
`TerminalAuthFailure`. That exception subclasses `BaseException` (so it
propagates through `asyncio.gather` and worker `except Exception` blocks), so
the handler's `except Exception as _cap_exc` could not catch it. The re-raise
escaped `main()`, skipped the `exit_code = EXIT_LOCKED` assignment (which sits
*after* the capture block), skipped the exit-code file write and `sys.exit`,
and crashed the run with exit 1 — the launcher then printed
`finalize: skipped — container exited with code 1` instead of the intended
resumable pause.

The fix widens every best-effort capture guard to
`except (Exception, TerminalAuthFailure, RateLimitedExit)`. These two tests
pin it:

  1. Source-coupling (primary, enforceable): every best-effort capture guard
     names both signal types. A future revert to a bare `except Exception`
     re-opens the escape and fails this guard — the same discipline
     `test_terminal_auth_routing.py` uses for the handler's own shape.
  2. Behavioral: the *exact* try/except-inside-except control-flow shape from
     `main()`'s `TerminalAuthFailure` arm, reconstructed here, reaches
     `exit_code = EXIT_LOCKED` when the inner capture raises a `BaseException`
     — and provably would NOT under the pre-fix `except Exception`.
"""
from __future__ import annotations

import ast
import inspect
import textwrap


# ---------------------------------------------------------------------------
# 1. Source-coupling: every best-effort capture guard catches the
#    BaseException-derived exit signals, not just Exception.
# ---------------------------------------------------------------------------

def _capture_guard_handlers(fn) -> list[ast.ExceptHandler]:
    """Return every ExceptHandler that *directly* guards a `capture_repo_deps`
    / `_backstop_capture_prior_runs` call — i.e. the best-effort capture guards
    this fix hardens.

    "Directly" is load-bearing: `main()`'s top-level `try` wraps
    `asyncio.run(orchestrate(...))`, and its handler *bodies* (`except
    WorkerError`, `except TerminalAuthFailure`, ...) contain the nested capture
    try-blocks. Walking the outer try would wrongly collect `except WorkerError`
    as a "capture guard". So we match only a `Try` whose own `body` (not its
    handlers, not deeper nesting) contains the capture call."""
    src = textwrap.dedent(inspect.getsource(fn))
    tree = ast.parse(src)
    targets = {"capture_repo_deps", "_backstop_capture_prior_runs"}

    def calls_capture_before_nested_try(stmt: ast.AST) -> bool:
        """True if `stmt`'s subtree contains a target call reachable without
        crossing a nested Try boundary (so an inner try's capture call is
        attributed to the inner try, not this one)."""
        found = False

        def visit(n: ast.AST) -> None:
            nonlocal found
            if found:
                return
            if (isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                    and n.func.id in targets):
                found = True
                return
            for child in ast.iter_child_nodes(n):
                if isinstance(child, ast.Try):
                    continue  # inner try owns its own capture call
                visit(child)

        visit(stmt)
        return found

    handlers: list[ast.ExceptHandler] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        if any(calls_capture_before_nested_try(s) for s in node.body):
            handlers.extend(node.handlers)
    return handlers


def _handler_type_names(handler: ast.ExceptHandler) -> set[str]:
    t = handler.type
    if t is None:
        return set()
    if isinstance(t, ast.Tuple):
        return {e.id for e in t.elts if isinstance(e, ast.Name)}
    if isinstance(t, ast.Name):
        return {t.id}
    return set()


# The functions carrying a best-effort capture guard (each verified to hold at
# least one). `main` carries four (auth-locked, out-of-credits, cancel,
# signal arms); the rest carry one each.
_GUARD_FUNCS = [
    "main",
    "_backstop_capture_prior_runs",
    "run_recapture_deps",
]


def test_every_capture_guard_catches_the_exit_signals(leerie):
    """Every best-effort capture guard must name both TerminalAuthFailure and
    RateLimitedExit alongside Exception. A bare `except Exception` cannot catch
    these BaseException subclasses and re-opens the incident's escape."""
    total = 0
    for fname in _GUARD_FUNCS:
        fn = getattr(leerie, fname)
        handlers = _capture_guard_handlers(fn)
        assert handlers, (
            f"{fname}: expected at least one best-effort capture guard; found "
            f"none (did the call site move out of this function?)")
        for h in handlers:
            names = _handler_type_names(h)
            total += 1
            assert "TerminalAuthFailure" in names, (
                f"{fname}: a capture guard does not catch TerminalAuthFailure "
                f"(names={sorted(names)}) — the 2026-07-19 escape re-opens")
            assert "RateLimitedExit" in names, (
                f"{fname}: a capture guard does not catch RateLimitedExit "
                f"(names={sorted(names)}) — same BaseException escape class")
            assert "Exception" in names, (
                f"{fname}: a capture guard dropped Exception (names="
                f"{sorted(names)}) — ordinary capture errors must still be "
                f"swallowed")
    # main() has 4 arms; the two module-level helpers have 1 each → 6 total.
    assert total >= 6, f"expected >= 6 hardened capture guards, saw {total}"


def test_finalize_capture_guard_is_hardened(leerie):
    """phase_finalize's clean-path capture guard is the one that would derail a
    *successful* run into a crash; pin it explicitly (it lives in whichever
    function phase_finalize's body inlines the capture into)."""
    # phase_finalize is async and may be nested; find it by name.
    fn = getattr(leerie, "phase_finalize", None)
    assert fn is not None, "phase_finalize not found"
    handlers = _capture_guard_handlers(fn)
    assert handlers, "phase_finalize: no best-effort capture guard found"
    for h in handlers:
        names = _handler_type_names(h)
        assert {"TerminalAuthFailure", "RateLimitedExit"} <= names, (
            f"phase_finalize capture guard not hardened (names={sorted(names)})")


# ---------------------------------------------------------------------------
# 2. Behavioral: the try/except-inside-except control-flow shape from main()'s
#    TerminalAuthFailure arm reaches EXIT_LOCKED when the inner capture raises a
#    BaseException — and would NOT under a bare `except Exception`.
# ---------------------------------------------------------------------------

def _simulate_taf_arm(leerie, capture_raises, *, widened: bool):
    """Reconstruct the essential control flow of main()'s
    `except TerminalAuthFailure` arm: a best-effort capture call whose failure
    must not prevent reaching `exit_code = EXIT_LOCKED`.

    `widened=True` uses the shipped guard tuple; `widened=False` uses the
    pre-fix bare `except Exception` — proving the test actually discriminates.
    Returns (exit_code, escaped_exc)."""
    EXIT_LOCKED = leerie.EXIT_LOCKED
    exit_code = 0  # main()'s initial value
    escaped = None
    try:
        # <-- best-effort dep_capture (the block that crashed in prod)
        try:
            raise capture_raises
        except (
            (Exception, leerie.TerminalAuthFailure, leerie.RateLimitedExit)
            if widened else Exception
        ):
            pass
        exit_code = EXIT_LOCKED  # set AFTER the capture block, as in main()
    except BaseException as e:  # main() has no sibling that catches this
        escaped = e
    return exit_code, escaped


def test_widened_guard_reaches_exit_locked_on_terminal_auth(leerie):
    """The observed incident: capture re-raises TerminalAuthFailure. The
    widened guard swallows it and execution reaches EXIT_LOCKED."""
    exc = leerie.TerminalAuthFailure("OAuth session expired and could not "
                                     "be refreshed")
    exit_code, escaped = _simulate_taf_arm(leerie, exc, widened=True)
    assert escaped is None, f"exception escaped the arm: {escaped!r}"
    assert exit_code == leerie.EXIT_LOCKED


def test_widened_guard_reaches_exit_locked_on_rate_limit(leerie):
    """Symmetry: capture re-raises RateLimitedExit (also BaseException-derived,
    also raisable by claude_p). Still swallowed, still reaches EXIT_LOCKED."""
    exc = leerie.RateLimitedExit(None, "rate limited during capture")
    exit_code, escaped = _simulate_taf_arm(leerie, exc, widened=True)
    assert escaped is None, f"exception escaped the arm: {escaped!r}"
    assert exit_code == leerie.EXIT_LOCKED


def test_prefix_bare_except_would_have_escaped(leerie):
    """Discrimination guard: the pre-fix bare `except Exception` does NOT catch
    the BaseException re-raise — it escapes and EXIT_LOCKED is never reached.
    This proves the tests above assert something real (they would pass
    vacuously if the simulated arm couldn't reproduce the escape)."""
    exc = leerie.TerminalAuthFailure("OAuth session expired")
    exit_code, escaped = _simulate_taf_arm(leerie, exc, widened=False)
    assert isinstance(escaped, leerie.TerminalAuthFailure)
    assert exit_code == 0  # EXIT_LOCKED assignment was skipped
