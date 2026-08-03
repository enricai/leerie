"""Regression guard: `check_diff_scope` and the finalize divergence
check must compute diffs against the run branch
(`leerie/runs/<run-id>`), never the bare `leerie/staging` name.

Background: an earlier refactor moved branch names from a global
`leerie/staging` to per-run branches. Two git-diff call sites had
kept the old name hardcoded — git returned non-zero and the functions
silently returned `None`, disabling DESIGN §12's protected-path
enforcement (check_diff_scope) and the finalize divergence-warning
check.

This test pins the source so a future regression would fail loudly
instead of silently disabling enforcement again. Source-text pin
(no live git).
"""
from __future__ import annotations

import inspect


def test_check_diff_scope_uses_run_branch(leerie):
    """check_diff_scope must compute its diff against the run branch.
    Failure here means workers can write to `.leerie/`, `.git/`, or
    `.claude/` without being caught."""
    src = inspect.getsource(leerie.check_diff_scope)
    assert "leerie/staging..HEAD" not in src, (
        "check_diff_scope is referencing the bare 'leerie/staging' "
        "branch, which does not exist under per-run. The "
        "protected-path enforcement is silently disabled."
    )
    assert "_compute_run_branch(st.run_id)" in src, (
        "check_diff_scope must derive its diff base from "
        "_compute_run_branch(st.run_id) so the check fires against the "
        "actual per-run branch."
    )


def test_phase_finalize_divergence_uses_run_branch(leerie):
    """phase_finalize's post-merge divergence warning must compute the
    diff against the run branch. Otherwise the warning never fires
    (git returns non-zero) and a silent merge that drops changes goes
    unreported."""
    src = inspect.getsource(leerie.phase_finalize)
    assert "leerie/staging..HEAD" not in src, (
        "phase_finalize is referencing the bare 'leerie/staging' "
        "branch for the divergence check. The warning would never fire."
    )
    assert "_compute_run_branch(st.run_id)" in src, (
        "phase_finalize must compute the divergence diff against "
        "_compute_run_branch(st.run_id)."
    )


def test_no_staging_branch_in_executable_code(leerie):
    """Sweep guard: no remaining executable references to
    `leerie/staging` in leerie.py. The string may appear in
    comments or docstrings but never as a live argument to a git
    command."""
    from pathlib import Path
    src = Path(leerie.__file__).read_text()
    for lineno, line in enumerate(src.splitlines(), start=1):
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        if "leerie/staging" not in line:
            continue
        if 'subprocess' in line or '"git"' in line or "'git'" in line:
            raise AssertionError(
                f"leerie.py:{lineno} still passes 'leerie/staging' "
                f"to a git command: {line.strip()!r}"
            )
