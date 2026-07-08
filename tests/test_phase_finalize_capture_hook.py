"""phase_finalize must invoke capture_repo_deps inside a non-fatal try/except.

Regression pin for DESIGN §6½ capture-and-bake finalize hook: a refactor
could drop the call, drop the try/except wrapper, or change the arguments.
This test asserts the wiring is intact so any such regression fails loudly.

The behavioral tests for capture_repo_deps itself live in test_capture_deps.py.
This file pins only the call-site wiring inside phase_finalize.
"""
from __future__ import annotations

import inspect


def test_phase_finalize_calls_capture_repo_deps(leerie):
    """phase_finalize must call capture_repo_deps with Path(os.getcwd()) and st.

    If this assertion fails, the DESIGN §6½ finalize hook has been dropped
    or renamed — auto-capture of repo deps will silently stop firing."""
    src = inspect.getsource(leerie.phase_finalize)
    assert "capture_repo_deps(" in src, (
        "phase_finalize must invoke capture_repo_deps(). "
        "The call was removed or renamed — DESIGN §6½ capture-and-bake "
        "at finalize will silently stop firing."
    )
    assert "Path(os.getcwd())" in src, (
        "phase_finalize must pass Path(os.getcwd()) as the repo_root "
        "argument to capture_repo_deps(). "
        "The argument was changed or removed."
    )


def test_phase_finalize_capture_is_nonfatal(leerie):
    """capture_repo_deps must be wrapped in try/except Exception so a capture
    failure never blocks a run from completing (DESIGN §6½ non-fatal contract).

    If this assertion fails, a capture error will propagate and may abort
    finalize — a run would fail for a reason unrelated to the task."""
    src = inspect.getsource(leerie.phase_finalize)
    assert "try:" in src, (
        "phase_finalize must contain a try: block wrapping "
        "capture_repo_deps(). "
        "The try/except was removed — capture errors are no longer swallowed."
    )
    assert "except Exception" in src, (
        "phase_finalize must catch Exception around capture_repo_deps() "
        "so any capture failure is non-fatal. "
        "The except-clause was removed or narrowed."
    )
    # The try: must precede capture_repo_deps( so the call is inside the guard.
    try_pos = src.index("try:")
    call_pos = src.index("capture_repo_deps(")
    assert try_pos < call_pos, (
        "the try: block must appear before capture_repo_deps() in "
        "phase_finalize so the call is actually inside the guard."
    )
    # The except Exception must follow capture_repo_deps( (closes the block).
    except_pos = src.index("except Exception")
    assert call_pos < except_pos, (
        "except Exception must appear after capture_repo_deps() in "
        "phase_finalize — the call must be inside the try/except guard."
    )
