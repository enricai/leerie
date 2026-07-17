"""Source-text pins for the per-run shell scripts.

After commit 3 of the parallel-safe refactor:
- `scripts/setup-run.sh` (renamed from setup-staging.sh) takes a RUN_ID
  argument and scopes all paths under `.leerie/runs/$RUN_ID/`.
- `scripts/new-worktree.sh` takes a second RUN_ID argument; subtask
  branches are `leerie/subtasks/$RUN_ID/$ID`, branched off
  `leerie/runs/$RUN_ID`.
- `scripts/integrate.sh` takes a second RUN_ID argument; merge target is
  `leerie/runs/$RUN_ID` and merge source is
  `leerie/subtasks/$RUN_ID/$ID`.
- `scripts/finalize.sh` takes a RUN_ID argument; merge source is
  `leerie/runs/$RUN_ID`.

The run-branch (`leerie/runs/…`) and subtask-branch
(`leerie/subtasks/…`) prefixes are deliberately disjoint so neither is
an ancestor ref of the other in git's loose ref store — without this,
`git worktree add` for the first subtask fails with `cannot lock ref …`.
See DESIGN.md §3 and `test_branch_namespaces_dont_collide.py`.

None of these scripts should reference the `leerie/staging` branch
name — it does not exist in the per-run layout.
"""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "scripts"


def _script(name: str) -> str:
    return (SCRIPTS / name).read_text()


# --- setup-run.sh ---------------------------------------------------------

def test_setup_run_script_exists():
    """The renamed-from-setup-staging.sh script exists at the new path."""
    assert (SCRIPTS / "setup-run.sh").exists()


def test_setup_staging_script_is_gone():
    """The pre-refactor script name no longer exists — it was renamed."""
    assert not (SCRIPTS / "setup-staging.sh").exists()


def test_setup_run_takes_run_id_arg():
    src = _script("setup-run.sh")
    assert 'RUN_ID="${1:?usage: setup-run.sh <run-id>}"' in src


def test_setup_run_uses_per_run_paths():
    """The script derives RUN_DIR from LEERIE_STATE_DIR (or .leerie fallback)."""
    src = _script("setup-run.sh")
    assert 'LEERIE_ROOT="${LEERIE_STATE_DIR:-.leerie}"' in src
    assert 'RUN_DIR="${LEERIE_ROOT}/runs/${RUN_ID}"' in src
    # And it doesn't write to top-level paths.
    assert '.leerie/worktrees/staging' not in src
    assert '.leerie/state.json' not in src


def test_setup_run_branch_is_per_run():
    src = _script("setup-run.sh")
    assert 'BRANCH="leerie/runs/${RUN_ID}"' in src
    # The 'leerie/staging' branch name must not appear.
    assert 'leerie/staging' not in src


def test_setup_run_no_git_exclude():
    """setup-run.sh must not write .leerie/ into .git/info/exclude.
    State now lives outside the repo; no per-repo git-exclude is needed."""
    src = _script("setup-run.sh")
    assert 'info/exclude' not in src
    assert "'.leerie/'" not in src
    assert '".leerie/"' not in src


def test_setup_run_idempotency_check_uses_fixed_string():
    """The already-present check must use -xF (fixed-string, whole-line).

    Mirrors test_new_worktree_idempotency_check_uses_fixed_string: the old
    pattern `grep -q "worktree .*/${STAGING_WT}$"` expands to a double slash
    when $STAGING_WT is absolute — `worktree .*/` + `/leerie-state/...` — which
    `git worktree list` never emits. The guard never matched, so `git worktree
    add` ran unconditionally and every --resume over a stale staging dir died
    with "fatal: '...' already exists". Measured: the old pattern scores 0
    matches against real porcelain output where -xF scores 1.

    The old pattern is quoted in an explanatory comment in the script, so the
    absence check runs against executable lines only — same technique as
    test_finalize_references_per_run_branch below.
    """
    src = _script("setup-run.sh")
    assert 'grep -qxF "worktree $STAGING_WT"' in src
    non_comment = "\n".join(
        line for line in src.splitlines() if not line.lstrip().startswith("#")
    )
    assert 'grep -q "worktree .*/' not in non_comment, (
        "setup-run.sh still has the double-slash-prone regex guard in an "
        "executable line — it must use `grep -qxF` instead."
    )


def test_setup_run_canonicalizes_staging_wt_to_absolute():
    """$STAGING_WT must be resolved to an absolute path before the reuse grep.

    git worktree list --porcelain outputs absolute, symlink-resolved paths.
    When LEERIE_STATE_DIR is unset $STAGING_WT is relative, and git resolves
    symlinks that a relative path does not. Mirrors
    test_new_worktree_canonicalizes_wt_to_absolute.
    """
    src = _script("setup-run.sh")
    assert 'case "$STAGING_WT" in' in src
    assert 'STAGING_WT="$(pwd -P)/$STAGING_WT"' in src


def test_setup_run_reclaims_orphaned_staging_dir():
    """An unregistered-but-present staging dir must be removed before `add`.

    Neither `git worktree prune` (only drops entries whose dir is *gone*) nor
    `--force` (overrides branch-checked-out and path-*missing*, not
    path-present) recovers this — both measured against a real repo. Removing
    the directory is the only remedy. The `&&` guard is load-bearing: it must
    never fire on a still-registered worktree.

    Asserted against executable lines only: the script *explains* prune's
    limits in a comment, so a whole-file `in src` check passes even when the
    executable `git worktree prune` is deleted (verified by mutation).
    """
    src = _script("setup-run.sh")
    non_comment = "\n".join(
        line for line in src.splitlines() if not line.lstrip().startswith("#")
    )
    assert 'rm -rf "$STAGING_WT"' in non_comment
    assert '[ -d "$STAGING_WT" ]' in non_comment
    assert 'git worktree prune' in non_comment, (
        "setup-run.sh must run `git worktree prune` in an executable line — "
        "the comment explaining prune's limits does not satisfy this."
    )


def test_setup_run_checks_external_leerie_branch():
    """setup-run.sh must check for a pre-existing 'leerie' branch.
    This is defense-in-depth for the --resume path, which skips
    preflight(). Without it, a user branch named 'leerie' crashes
    `git branch leerie/runs/<id>` with 'cannot lock ref'."""
    src = _script("setup-run.sh")
    assert "refs/heads/leerie" in src, (
        "setup-run.sh must check for a pre-existing 'leerie' branch "
        "before creating leerie/runs/<id>."
    )


# --- new-worktree.sh ------------------------------------------------------

def test_new_worktree_takes_run_id_arg():
    src = _script("new-worktree.sh")
    assert 'RUN_ID="${2:?usage: new-worktree.sh <subtask-id> <run-id>}"' in src


def test_new_worktree_uses_per_run_paths():
    src = _script("new-worktree.sh")
    assert 'LEERIE_ROOT="${LEERIE_STATE_DIR:-.leerie}"' in src
    assert 'WT="${LEERIE_ROOT}/runs/${RUN_ID}/worktrees/${ID}"' in src


def test_new_worktree_branch_uses_subtasks_namespace():
    """Subtask branches are leerie/subtasks/<run-id>/<sid>, branched off
    leerie/runs/<run-id>. The two namespaces must be disjoint so neither
    is an ancestor ref of the other in git's loose ref store."""
    src = _script("new-worktree.sh")
    assert 'BRANCH="leerie/subtasks/${RUN_ID}/${ID}"' in src
    assert 'PARENT_BRANCH="leerie/runs/${RUN_ID}"' in src


def test_new_worktree_branches_off_run_branch():
    """The fresh-subtask path branches off the per-run branch, not staging."""
    src = _script("new-worktree.sh")
    assert '"$PARENT_BRANCH"' in src
    assert 'leerie/staging' not in src


def test_new_worktree_idempotency_check_uses_fixed_string():
    """The already-present check must use -xF (fixed-string, whole-line) so it
    works for absolute LEERIE_STATE_DIR values like /leerie-state.

    The old pattern `grep -q "worktree .*/${WT}$"` expands to a double-slash
    when $WT is absolute, e.g. `worktree .*/` + `/leerie-state/...` =
    `worktree .*/` + `/leerie-state/...`.  grep never finds a match, the
    script falls through to `git worktree add` on an existing directory, and
    every continuation retry dies with "fatal: '...' already exists"."""
    src = _script("new-worktree.sh")
    assert 'grep -qxF "worktree $WT"' in src
    assert 'grep -q "worktree .*/' not in src


def test_new_worktree_canonicalizes_wt_to_absolute():
    """$WT must be resolved to an absolute path before the reuse grep.

    git worktree list --porcelain outputs absolute, symlink-resolved paths.
    When LEERIE_STATE_DIR is unset (Fly runtime), $WT is relative and the
    grep never matches, crashing continuation retries with
    "fatal: '...' already exists"."""
    src = _script("new-worktree.sh")
    assert 'case "$WT" in' in src
    assert 'WT="$(pwd -P)/$WT"' in src


# --- integrate.sh ---------------------------------------------------------

def test_integrate_takes_run_id_arg():
    src = _script("integrate.sh")
    assert 'RUN_ID="${2:?usage: integrate.sh <subtask-id> <run-id>}"' in src


def test_integrate_merges_into_per_run_staging():
    src = _script("integrate.sh")
    assert 'LEERIE_ROOT="${LEERIE_STATE_DIR:-.leerie}"' in src
    assert 'STAGING="${LEERIE_ROOT}/runs/${RUN_ID}/worktrees/staging"' in src


def test_integrate_branch_uses_subtasks_namespace():
    src = _script("integrate.sh")
    assert 'BRANCH="leerie/subtasks/${RUN_ID}/${ID}"' in src
    assert 'leerie/staging' not in src


# --- finalize.sh ----------------------------------------------------------

def test_finalize_takes_run_id_arg():
    src = _script("finalize.sh")
    assert 'RUN_ID="${1:?usage: finalize.sh <run-id>}"' in src


def test_finalize_references_per_run_branch():
    """finalize.sh resolves the per-run branch from ${RUN_ID}."""
    src = _script("finalize.sh")
    assert 'BRANCH="leerie/runs/${RUN_ID}"' in src
    # The 'leerie/staging' branch must not appear in executable lines.
    non_comment = "\n".join(
        line for line in src.splitlines() if not line.lstrip().startswith("#")
    )
    assert 'leerie/staging' not in non_comment


def test_finalize_uses_per_run_working_branch_file():
    """Working branch is recorded per-run under runs/<id>/working-branch."""
    src = _script("finalize.sh")
    # The top-level .leerie/working-branch must not appear.
    assert "\".leerie/working-branch\"" not in src
    assert "'.leerie/working-branch'" not in src


def test_finalize_honors_state_dir_env():
    """finalize.sh derives RUN_DIR from LEERIE_STATE_DIR (or .leerie fallback)
    — mirrors setup-run.sh:25. Without this, in-container invocations
    (LEERIE_STATE_DIR=/leerie-state) look at /work/.leerie/runs/<id>/
    instead of /leerie-state/runs/<id>/ and finalize.sh aborts with
    'working-branch missing' during phase 6 of every Fly run that
    reaches finalize. Regression cover for the bug observed
    2026-06-06 on a resumed stackpulse run after wave 4 completed."""
    src = _script("finalize.sh")
    assert 'LEERIE_ROOT="${LEERIE_STATE_DIR:-.leerie}"' in src
    assert 'RUN_DIR="${LEERIE_ROOT}/runs/${RUN_ID}"' in src
    # The naked hardcoded path must not appear in executable lines.
    non_comment = "\n".join(
        line for line in src.splitlines() if not line.lstrip().startswith("#")
    )
    assert 'RUN_DIR=".leerie/runs/' not in non_comment


def test_cleanup_honors_state_dir_env():
    """cleanup.sh derives LEERIE_ROOT from LEERIE_STATE_DIR (or .leerie fallback)
    — same pattern as finalize.sh and setup-run.sh. Without this, the
    in-container post-finalize cleanup invocation (run_script call at
    orchestrator/leerie.py:13085) silently no-ops because /work/.leerie/runs/
    doesn't exist, leaving subtask branches behind."""
    src = _script("cleanup.sh")
    assert 'LEERIE_ROOT="${LEERIE_STATE_DIR:-.leerie}"' in src
    # Every executable path reference uses ${LEERIE_ROOT}, not a hardcoded
    # .leerie/runs/ literal.
    non_comment = "\n".join(
        line for line in src.splitlines() if not line.lstrip().startswith("#")
    )
    assert '.leerie/runs' not in non_comment, (
        "cleanup.sh has a remaining hardcoded `.leerie/runs` literal "
        "in an executable line — should use ${LEERIE_ROOT}/runs/ instead.\n"
        "Lines containing it:\n" + "\n".join(
            line for line in non_comment.splitlines()
            if '.leerie/runs' in line
        )
    )
