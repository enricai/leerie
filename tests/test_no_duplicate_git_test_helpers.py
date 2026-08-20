"""The git-subprocess-runner and git-repo-with-initial-commit patterns live
in exactly one place: `tests/conftest.py`'s `run_git_repo_first` /
`run_git_cwd_kw` / `run_git_cwd_first_stdout` / `init_git_repo`.

Both migrations are complete: no `tests/test_*.py` defines a local `_git(...)`
(refactor-002/003/004, three distinct signature families) or `_init_repo(...)`
helper any more. So this guard is fully strict on both halves — every hit is a
new regression, and both known-offender snapshots are empty.

An allowlist that is empty on both halves means the enforcement tests pass by
finding nothing, which a broken glob or a broken regex also achieves. The
positive controls below (`test_the_scan_enumerates_the_tests_directory`,
`test_the_patterns_detect_a_planted_definition`) exist so that a scan which has
silently stopped scanning fails as a broken scan rather than certifying
everything.
"""
from __future__ import annotations

import re
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent

_GIT_DEF_RE = re.compile(r"^\s*def _git\(", re.MULTILINE)
_INIT_REPO_DEF_RE = re.compile(r"^\s*def _init_repo\(", re.MULTILINE)

# Files that pre-dated the shared conftest helpers. Both snapshots are now
# empty: the last batch of local `_git(...)` definitions (refactor-002/003/004)
# and the last batch of local `_init_repo(...)` definitions have both been
# migrated, so every remaining occurrence of either is a genuinely new
# regression rather than a pre-existing offender. The sets are kept (rather
# than deleted along with the `hits - KNOWN` subtraction) so that a future
# partial migration has somewhere to record its own snapshot.
_KNOWN_GIT_OFFENDERS: set[str] = set()
_KNOWN_INIT_REPO_OFFENDERS: set[str] = set()


def _files_defining(pattern: re.Pattern, root: Path = TESTS_DIR) -> set[str]:
    hits: set[str] = set()
    for path in sorted(root.glob("test_*.py")):
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
    """Prove the snapshot lists still reflect current reality.

    Both migrations are complete, so both halves assert equality against the
    empty set rather than a non-empty check (which would now be vacuously
    false). Asserting the allowlists themselves are empty is what catches a
    silent re-expansion: adding a name back would otherwise let a new local
    helper through `test_no_new_local_*` unnoticed.
    """
    assert _KNOWN_GIT_OFFENDERS == set(), (
        f"_KNOWN_GIT_OFFENDERS grew back to {sorted(_KNOWN_GIT_OFFENDERS)}; an "
        "allowlisted file is exempt from the enforcement guard above"
    )
    assert _KNOWN_INIT_REPO_OFFENDERS == set(), (
        f"_KNOWN_INIT_REPO_OFFENDERS grew back to "
        f"{sorted(_KNOWN_INIT_REPO_OFFENDERS)}; an allowlisted file is exempt "
        "from the enforcement guard above"
    )

    git_hits = _files_defining(_GIT_DEF_RE)
    init_hits = _files_defining(_INIT_REPO_DEF_RE)
    assert git_hits == set(), (
        f"expected zero remaining _git(...) definitions now that migration "
        f"is complete; found {sorted(git_hits)} — either migrate these files "
        "or record them in _KNOWN_GIT_OFFENDERS"
    )
    assert init_hits == set(), (
        f"expected zero remaining _init_repo(...) definitions now that "
        f"migration is complete; found {sorted(init_hits)} — either migrate "
        "these files or record them in _KNOWN_INIT_REPO_OFFENDERS"
    )


def test_the_scan_enumerates_the_tests_directory() -> None:
    """Anti-vacuity: a glob that matches nothing makes every guard above pass.

    The floor is deliberately far below the real count (514 at the time of
    writing) — this catches a broken root/pattern, not a shrinking suite.
    """
    found = list(TESTS_DIR.glob("test_*.py"))
    assert len(found) > 100, (
        f"the scan enumerated only {len(found)} test files under {TESTS_DIR}; "
        "the guards above are passing because they are scanning nothing"
    )


def test_the_patterns_detect_a_planted_definition(tmp_path: Path) -> None:
    """Anti-vacuity: drive the real `_files_defining` over a planted tree.

    Covers the module-level and the nested/indented form (hence `re.MULTILINE`
    plus the leading `\\s*`), proves a non-matching file is not swept up, and
    pins the negative control: `_init_repo_with_origin(...)` is a real name in
    tests/test_host_finalize_rebase.py, so widening the regex to drop the `\\(`
    would start false-positiving that file.
    """
    (tmp_path / "test_planted_git.py").write_text(
        "import subprocess\n\n\ndef _git(*args):\n    return args\n"
    )
    (tmp_path / "test_planted_init_repo.py").write_text(
        "class T:\n    def _init_repo(self, tmp_path):\n        return tmp_path\n"
    )
    (tmp_path / "test_planted_clean.py").write_text(
        "def _init_repo_with_origin(tmp_path):\n    return tmp_path\n"
    )

    assert _files_defining(_GIT_DEF_RE, root=tmp_path) == {"test_planted_git.py"}
    assert _files_defining(_INIT_REPO_DEF_RE, root=tmp_path) == {
        "test_planted_init_repo.py"
    }
