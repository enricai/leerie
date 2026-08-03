"""Tests for the per-worker `subprocess.TimeoutExpired` catch in
`_run_implementer` and `_run_conformer`.

Background. `_invoke` raises `subprocess.TimeoutExpired` when a worker
hits the per-process wall-clock cap (`worker_timeout_sec`, default
5400s / 90 min). Earlier versions of `_run_implementer` only caught
`WorkerError`, so the timeout escaped the implementer → _settle_subtask
→ _gather_or_cancel → phase_execute → _orchestrate → main()'s catch-all,
dumping a 50-KB traceback (with the entire `claude -p` command line)
to the user's terminal. This file pins the catch so a refactor can't
silently regress that fix. The traces that prompted the fix appear
verbatim in CHANGELOG.md / DESIGN.md context.

These are source-text pins. A behavioral test would have to stand up
a real git worktree (`new-worktree.sh`) since `_run_implementer`'s
first step is worktree creation; that's outside the scope of the
fast unit suite (cf. CLAUDE.md: "The worker invocation path
(`claude_p`) is not unit-tested; meaningful testing requires a stub
or live `claude` binary"). Pins on the source text are how the rest
of the suite covers `_run_implementer`-adjacent invariants.
"""
from __future__ import annotations

import inspect
import re
from pathlib import Path

LEERIE_PY = Path(__file__).resolve().parent.parent / "orchestrator" / "leerie.py"


def _function_source_on_disk(name: str) -> str:
    """Return a function's source text by reading leerie.py directly,
    not via `inspect.getsource(leerie.<name>)`.

    Why: the session-scoped `leerie` fixture is shared across tests, and
    some tests rebind module attributes (e.g.
    `tests/test_run_conformance_phase.py` does
    `leerie_mod._run_conformer = _stub` without monkeypatch cleanup).
    A later `inspect.getsource(leerie._run_conformer)` then returns the
    stub's source, not the real one — and a pin test against the
    real source's invariants fails for a reason unrelated to the
    invariant. Reading from disk sidesteps the leak.

    Matches `async def <name>` or `def <name>` and returns text up to
    (but not including) the next top-level `def` / `async def` / `class`.
    """
    src = LEERIE_PY.read_text()
    m = re.search(
        rf"^(?:async )?def {re.escape(name)}\b.*?"
        r"(?=^(?:async )?(?:def |class ))",
        src, re.DOTALL | re.MULTILINE,
    )
    if m is None:
        raise AssertionError(f"could not locate {name}() in {LEERIE_PY}")
    return m.group(0)


# --- _run_implementer: catches TimeoutExpired and returns handoff ---------

def test_run_implementer_catches_timeout_expired(leerie):
    """The `except subprocess.TimeoutExpired:` arm must exist in
    `_run_implementer` so a per-worker timeout becomes an
    `incomplete-handoff` envelope rather than a process-killing
    unhandled traceback."""
    src = inspect.getsource(leerie._run_implementer)
    assert "except subprocess.TimeoutExpired" in src, (
        "_run_implementer must catch subprocess.TimeoutExpired so a worker "
        "that hits worker_timeout_sec doesn't escape as an unhandled "
        "exception. See leerie.py:4625-area."
    )


def test_run_implementer_timeout_returns_handoff_envelope(leerie):
    """The timeout catch must return an envelope shaped like the
    WorkerError handoff: status='incomplete-handoff' with a
    checkpoint_path. Same shape so _settle_subtask's existing
    handoff machinery handles the timeout case uniformly."""
    src = inspect.getsource(leerie._run_implementer)
    # Find the TimeoutExpired branch and grab everything until the
    # next except / def boundary.
    m = re.search(
        r"except subprocess\.TimeoutExpired[^\n]*:(.*?)(?=^\s*except |^\s*def |\Z)",
        src, re.DOTALL | re.MULTILINE,
    )
    assert m, "could not locate TimeoutExpired arm in _run_implementer source"
    arm = m.group(1)
    assert '"incomplete-handoff"' in arm, (
        "TimeoutExpired arm must return status='incomplete-handoff' "
        "to route through _settle_subtask's existing handoff path."
    )
    assert "checkpoint_path" in arm, (
        "TimeoutExpired arm must set checkpoint_path so a fresh "
        "implementer can pick up from any partial state."
    )
    assert "timed out" in arm.lower(), (
        "TimeoutExpired arm's summary should mention the timeout so "
        "the user-facing log and any telemetry can distinguish it "
        "from a no-result WorkerError handoff. (Neither _validate_result "
        "nor _retryable_failure reads the summary text for routing — "
        "this is a human-readability pin.)"
    )


# --- _run_conformer: same catch, but returns None (advisory phase) -------

def test_run_conformer_catches_timeout_expired(leerie):
    """Conformer phase is advisory (DESIGN §9). A timed-out conformer
    must become a logged warning + return None, not a run-killing
    traceback. Same `subprocess.TimeoutExpired` shield as
    `_run_implementer`."""
    src = _function_source_on_disk("_run_conformer")
    assert "except subprocess.TimeoutExpired" in src, (
        "_run_conformer must catch subprocess.TimeoutExpired so a "
        "conformer timeout doesn't escape the advisory phase. See "
        "leerie.py near _run_conformer's WorkerError catch."
    )


def test_run_conformer_timeout_returns_none(leerie):
    """The conformer timeout catch must return None (matching the
    existing WorkerError catch) so _settle_subtask's caller treats
    the conformance pass as silently-empty rather than as a result
    requiring further handling."""
    src = _function_source_on_disk("_run_conformer")
    m = re.search(
        r"except subprocess\.TimeoutExpired[^\n]*:(.*?)(?=^\s*except |^\s*def |\Z)",
        src, re.DOTALL | re.MULTILINE,
    )
    assert m, "could not locate TimeoutExpired arm in _run_conformer source"
    arm = m.group(1)
    assert re.search(r"\breturn None\b", arm), (
        "conformer's TimeoutExpired arm must return None to match the "
        "WorkerError arm's advisory-phase semantics."
    )


# --- regression: the WorkerError arm must still exist alongside ---------

def test_run_implementer_retains_worker_error_catch(leerie):
    """The TimeoutExpired catch is additive — the WorkerError catch
    (which handles the more common max-turns / schema-invalid case)
    must remain in place."""
    src = inspect.getsource(leerie._run_implementer)
    assert "except WorkerError" in src


def test_run_conformer_retains_worker_error_catch(leerie):
    src = _function_source_on_disk("_run_conformer")
    assert "except WorkerError" in src
