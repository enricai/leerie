"""The git-subprocess-runner and git-repo-with-initial-commit patterns live
in exactly one place: `tests/conftest.py`'s `run_git_repo_first` /
`run_git_cwd_kw` / `run_git_cwd_first_stdout` / `init_git_repo`.

As of this subtask, ~31 test files still define their own local `_git(...)`
(three distinct signature families) and ~12 define their own `_init_repo(...)`
— migrating those call sites is separate, later work (see the subtask's
`scope_note`). This guard is written now so that once migration subtasks
land, a test file cannot quietly reintroduce a local reimplementation instead
of importing the shared helper.

Until migration completes, this test is intentionally scoped to catch NEW
occurrences without failing on the pre-existing ones: it snapshots the
currently-known offenders and fails only if a file outside that snapshot
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
_KNOWN_GIT_OFFENDERS = {
    "test_blt_memo.py",
    "test_check_merge_committed.py",
    "test_check_rebaser_worktree_state.py",
    "test_clobbered_owned_files.py",
    "test_collect_subtrees_sh.py",
    "test_ec2_seed_repo_shallow.py",
    "test_empty_handoff_keeps_committed_work.py",
    "test_ensure_planning_worktree.py",
    "test_host_finalize_rebase.py",
    "test_mid_run_satisfied_no_commits.py",
    "test_new_worktree_concurrency.py",
    "test_new_worktree_idempotency.py",
    "test_planning_worktree_script.py",
    "test_pre_spawn_settle_is_integrable.py",
    "test_prepush_preflight.py",
    "test_prune_subtask_worktree.py",
    "test_prune_verb.py",
    "test_prune_worktree_attribution.py",
    "test_rescue_integrator_work.py",
    "test_reset_subtask_worktree.py",
    "test_resume_seed_self_heal.py",
    "test_run_rebaser.py",
    "test_satisfied_probe_cache_invalidation.py",
    "test_scan_conflict_markers.py",
    "test_scoped_axes.py",
    "test_seed_repo_shallow_roundtrip.py",
    "test_settle_subtask_branch_coverage.py",
    "test_setup_run_idempotency.py",
    "test_stale_install_warning.py",
    "test_work_sentinel.py",
    "test_worktree_prune_scoping.py",
}

_KNOWN_INIT_REPO_OFFENDERS = {
    "test_check_merge_committed.py",
    "test_check_rebaser_worktree_state.py",
    "test_ec2_seed_repo.py",
    "test_external_leerie_branch.py",
    "test_finalize_sh_behavior.py",
    "test_host_finalize_hook_probe.py",
    "test_host_finalize_rebase.py",
    "test_main_cli_wiring.py",
    "test_prune_subtask_worktree.py",
    "test_reset_subtask_worktree.py",
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
    has drifted from what it claims to describe)."""
    git_hits = _files_defining(_GIT_DEF_RE)
    init_hits = _files_defining(_INIT_REPO_DEF_RE)
    assert git_hits, "expected at least one pre-migration _git(...) definition"
    assert init_hits, "expected at least one pre-migration _init_repo(...) definition"
