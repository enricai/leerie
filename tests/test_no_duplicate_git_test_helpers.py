"""The git-subprocess-runner and git-repo-with-initial-commit patterns live
in exactly one place: `tests/conftest.py`'s `run_git_repo_first` /
`run_git_cwd_kw` / `run_git_cwd_first_stdout` / `init_git_repo`.

The `_git(...)` migration (refactor-002/003/004, three distinct signature
families) is complete; `_init_repo(...)` migration is still in progress —
migrating those remaining call sites is separate, later work. This guard is
written so that a test file cannot quietly reintroduce a local
reimplementation instead of importing the shared helper.

Until `_init_repo` migration completes, this test is intentionally scoped to
catch NEW occurrences without failing on the pre-existing ones: it snapshots
the currently-known offenders and fails only if a file outside that snapshot
(or `tests/conftest.py` itself) defines `def _git(` or `def _init_repo(`.
"""
from __future__ import annotations

import re
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent

_GIT_DEF_RE = re.compile(r"^\s*def _git\(", re.MULTILINE)
_INIT_REPO_DEF_RE = re.compile(r"^\s*def _init_repo\(", re.MULTILINE)

# Files that pre-date the shared conftest helpers (measured via Grep at the
# time this guard was written). Migrating each off its local `_git`/
# `_init_repo` is separate, later work — see this subtask's investigation_notes.
#
# The last batch of local `_git(...)` definitions (refactor-002/003/004)
# has been migrated, so this set is now empty — every remaining `_git(...)`
# occurrence is a genuinely new regression, not a pre-existing offender.
_KNOWN_GIT_OFFENDERS: set[str] = set()

_KNOWN_INIT_REPO_OFFENDERS = {
    "test_check_rebaser_worktree_state.py",
    "test_external_leerie_branch.py",
    "test_finalize_sh_behavior.py",
    "test_host_finalize_hook_probe.py",
    "test_host_finalize_rebase.py",
    "test_main_cli_wiring.py",
    "test_run_rebaser.py",
    "test_scan_conflict_markers.py",
}


def _files_defining(pattern: re.Pattern) -> set[str]:
    hits: set[str] = set()
    for path in sorted(TESTS_DIR.glob("test_*.py")):
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        if pattern.search(text):
            hits.add(path.name)
    return hits


def test_conftest_exposes_the_shared_git_helpers() -> None:
    import tests.conftest as conftest_mod

    assert callable(conftest_mod.run_git_repo_first)
    assert callable(conftest_mod.run_git_cwd_kw)
    assert callable(conftest_mod.run_git_cwd_first_stdout)
    assert callable(conftest_mod.init_git_repo)


def test_no_new_local_git_runner_definitions() -> None:
    hits = _files_defining(_GIT_DEF_RE)
    new = hits - _KNOWN_GIT_OFFENDERS
    assert not new, (
        f"{sorted(new)} define a local `_git(...)` helper not in the known "
        "pre-migration offender list. Import run_git_repo_first / "
        "run_git_cwd_kw / run_git_cwd_first_stdout from tests/conftest.py "
        "instead of reimplementing it."
    )


def test_no_new_local_init_repo_definitions() -> None:
    hits = _files_defining(_INIT_REPO_DEF_RE)
    new = hits - _KNOWN_INIT_REPO_OFFENDERS
    assert not new, (
        f"{sorted(new)} define a local `_init_repo(...)` helper not in the "
        "known pre-migration offender list. Import init_git_repo from "
        "tests/conftest.py instead of reimplementing it."
    )


def test_offender_lists_can_shrink_when_migration_lands() -> None:
    """Anti-vacuity: prove the snapshot lists actually reflect current
    reality, so a file quietly migrating off its local helper is noticed
    (a stale entry doesn't break enforcement, but it does mean the guard
    has drifted from what it claims to describe).

    The `_git(...)` migration (refactor-002/003/004) is complete, so
    `_KNOWN_GIT_OFFENDERS` is deliberately empty and `git_hits` is expected
    to be empty too — asserted via the equality check below rather than a
    non-empty check, which would now be vacuously false. `_init_repo(...)`
    migration is not yet complete, so that half still asserts non-empty."""
    git_hits = _files_defining(_GIT_DEF_RE)
    init_hits = _files_defining(_INIT_REPO_DEF_RE)
    assert git_hits == set(), (
        f"expected zero remaining _git(...) definitions now that migration "
        f"is complete; found {sorted(git_hits)} — either update "
        "_KNOWN_GIT_OFFENDERS or migrate these files"
    )
    assert init_hits, "expected at least one pre-migration _init_repo(...) definition"
