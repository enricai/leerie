"""Tests for check_rebaser_worktree_state() — the mechanical verification
of the `rebaser` worker's claimed outcome (DESIGN §6 *Finalization*
"Rebase-onto-base before push", §12 "the orchestrator does not trust an
integrator's 'resolved' claim; it confirms the merge was actually
completed" — applied here to rebaser).

Builds real temp git repos/worktrees (mirroring
tests/test_clobbered_owned_files.py's discipline) rather than mocking git,
since this function's entire job is inspecting real git state.
"""
from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest

from tests.conftest import init_git_repo, run_git_repo_first


def _rev_parse(repo: Path, ref: str) -> str:
    return run_git_repo_first(repo, "rev-parse", ref).stdout.strip()


# --- "rebased" claim ---------------------------------------------------------

def test_rebased_claim_with_clean_tree_passes(leerie, tmp_path):
    """A clean, non-mid-rebase worktree with a 'rebased' claim: no error."""
    repo = init_git_repo(tmp_path / "repo")
    pre_sha = _rev_parse(repo, "HEAD")
    result = asyncio.run(
        leerie.check_rebaser_worktree_state(repo, "rebased", pre_sha))
    assert result is None


def test_rebased_claim_with_conflict_markers_fails(leerie, tmp_path):
    """A 'rebased' claim but conflict markers remain in tracked content —
    the worker's self-report does not match reality."""
    repo = init_git_repo(tmp_path / "repo")
    pre_sha = _rev_parse(repo, "HEAD")
    (repo / "a.txt").write_text(
        "<<<<<<< HEAD\nours\n=======\ntheirs\n>>>>>>> branch\n")
    run_git_repo_first(repo, "add", ".")
    run_git_repo_first(repo, "commit", "-q", "-m", "leaves markers")
    result = asyncio.run(
        leerie.check_rebaser_worktree_state(repo, "rebased", pre_sha))
    assert result is not None
    assert "conflict markers remain" in result
    assert "a.txt" in result


def test_rebased_claim_still_mid_rebase_merge_fails(leerie, tmp_path):
    """A 'rebased' claim but .git/rebase-merge is still present — the
    rebase never actually completed."""
    repo = init_git_repo(tmp_path / "repo")
    pre_sha = _rev_parse(repo, "HEAD")
    (repo / ".git" / "rebase-merge").mkdir()
    result = asyncio.run(
        leerie.check_rebaser_worktree_state(repo, "rebased", pre_sha))
    assert result is not None
    assert "mid-rebase" in result
    assert "rebase-merge" in result


def test_rebased_claim_still_mid_rebase_apply_fails(leerie, tmp_path):
    repo = init_git_repo(tmp_path / "repo")
    pre_sha = _rev_parse(repo, "HEAD")
    (repo / ".git" / "rebase-apply").mkdir()
    result = asyncio.run(
        leerie.check_rebaser_worktree_state(repo, "rebased", pre_sha))
    assert result is not None
    assert "mid-rebase" in result
    assert "rebase-apply" in result


def test_rebased_claim_with_new_clean_commit_passes(leerie, tmp_path):
    """A genuinely different HEAD from pre_rebase_sha is fine for a
    'rebased' claim — rebasing is expected to move HEAD. Only conflict
    markers / mid-rebase state matter here, not sha equality."""
    repo = init_git_repo(tmp_path / "repo")
    pre_sha = _rev_parse(repo, "HEAD")
    (repo / "b.txt").write_text("b\n")
    run_git_repo_first(repo, "add", ".")
    run_git_repo_first(repo, "commit", "-q", "-m", "b")
    assert _rev_parse(repo, "HEAD") != pre_sha
    result = asyncio.run(
        leerie.check_rebaser_worktree_state(repo, "rebased", pre_sha))
    assert result is None


DIVIDER = "=" * 90


def _commit(repo: Path, name: str, text: str, msg: str) -> None:
    (repo / name).write_text(text)
    run_git_repo_first(repo, "add", name)
    run_git_repo_first(repo, "commit", "-q", "-m", msg)


def _diverged_branches(tmp_path: Path) -> tuple[Path, Path, str]:
    """A repo whose base (`main`) and run branch (`run`) both moved after
    forking, with a run-branch worktree added the way `host_finalize`
    adds it. Returns (repo, worktree, pre-rebase sha of `run`)."""
    repo = init_git_repo(tmp_path / "repo")
    _commit(repo, "receipt.text.erb",
            f"Receipt\n{DIVIDER}\nTotal: 1\n{DIVIDER}\n", "receipt")
    run_git_repo_first(repo, "branch", "run")
    _commit(repo, "receipt.text.erb",
            f"Receipt\n{DIVIDER}\nTotal: 2\n{DIVIDER}\n", "upstream edit")
    worktree = tmp_path / "rebase-wt"
    run_git_repo_first(repo, "worktree", "add", "-q", str(worktree), "run")
    _commit(worktree, "b.txt", "b\n", "run-branch work")
    return repo, worktree, _rev_parse(worktree, "HEAD")


def test_rebased_claim_with_divider_rows_passes(leerie, tmp_path):
    """A real rebase in a `git worktree add` copy, where both the base and
    the rebased-in upstream commit carry `=====…` divider rows (plain-text
    mailer templates). Those rows are content, not conflict markers: a
    whole-tree `^={7}` grep rejected every successful rebase in such a
    repo, so every PR reported "Rebase onto <base> was not applied"."""
    _, worktree, pre_sha = _diverged_branches(tmp_path)
    run_git_repo_first(worktree, "rebase", "-q", "main")
    assert _rev_parse(worktree, "HEAD") != pre_sha
    result = asyncio.run(
        leerie.check_rebaser_worktree_state(worktree, "rebased", pre_sha))
    assert result is None


def test_rebased_claim_ignores_markers_already_on_base(leerie, tmp_path):
    """Marker-shaped lines the rebase did not introduce are not the
    rebaser's to answer for."""
    repo = init_git_repo(tmp_path / "repo")
    _commit(repo, "fixture.txt",
            "<<<<<<< ours\nx\n=======\ny\n>>>>>>> theirs\n", "fixture")
    pre_sha = _rev_parse(repo, "HEAD")
    _commit(repo, "b.txt", "b\n", "b")
    result = asyncio.run(
        leerie.check_rebaser_worktree_state(repo, "rebased", pre_sha))
    assert result is None


def test_rebased_claim_with_uncommitted_markers_fails(leerie, tmp_path):
    """Markers left in the working tree, never committed, still count."""
    repo = init_git_repo(tmp_path / "repo")
    pre_sha = _rev_parse(repo, "HEAD")
    (repo / "a.txt").write_text(
        "<<<<<<< HEAD\nours\n=======\ntheirs\n>>>>>>> branch\n")
    result = asyncio.run(
        leerie.check_rebaser_worktree_state(repo, "rebased", pre_sha))
    assert result is not None
    assert "a.txt" in result


@pytest.mark.parametrize("claim", ["rebased", "irreconcilable"])
def test_real_worktree_mid_rebase_is_detected(leerie, tmp_path, claim):
    """In a `git worktree add` copy `.git` is a file, so rebase state lives
    under the common dir; a `<worktree>/.git/rebase-merge` probe can never
    see it."""
    _, worktree, pre_sha = _diverged_branches(tmp_path)
    _commit(worktree, "receipt.text.erb", "Receipt\nTotal: 3\n", "clash")
    pre_sha = _rev_parse(worktree, "HEAD")
    r = subprocess.run(["git", "-C", str(worktree), "rebase", "main"],
                       capture_output=True, text=True)
    assert r.returncode != 0, "fixture must stop mid-rebase on a conflict"
    assert not (worktree / ".git" / "rebase-merge").exists()
    # resolved but never `--continue`d, so only the rebase state is wrong
    (worktree / "receipt.text.erb").write_text("Receipt\nTotal: 3\n")
    result = asyncio.run(
        leerie.check_rebaser_worktree_state(worktree, claim, pre_sha))
    assert result is not None
    assert "mid-rebase" in result


# --- "irreconcilable" / "failed" claim ---------------------------------------

def test_irreconcilable_claim_with_unchanged_head_passes(leerie, tmp_path):
    """The abort genuinely restored the branch — HEAD matches pre-rebase
    sha, no mid-rebase state: no error."""
    repo = init_git_repo(tmp_path / "repo")
    pre_sha = _rev_parse(repo, "HEAD")
    result = asyncio.run(
        leerie.check_rebaser_worktree_state(
            repo, "irreconcilable", pre_sha))
    assert result is None


def test_failed_claim_with_unchanged_head_passes(leerie, tmp_path):
    repo = init_git_repo(tmp_path / "repo")
    pre_sha = _rev_parse(repo, "HEAD")
    result = asyncio.run(
        leerie.check_rebaser_worktree_state(repo, "failed", pre_sha))
    assert result is None


def test_irreconcilable_claim_with_changed_head_fails(leerie, tmp_path):
    """Worker claims 'irreconcilable' (abort) but HEAD has actually moved —
    the abort did not restore the original state."""
    repo = init_git_repo(tmp_path / "repo")
    pre_sha = _rev_parse(repo, "HEAD")
    (repo / "b.txt").write_text("b\n")
    run_git_repo_first(repo, "add", ".")
    run_git_repo_first(repo, "commit", "-q", "-m", "b")
    result = asyncio.run(
        leerie.check_rebaser_worktree_state(
            repo, "irreconcilable", pre_sha))
    assert result is not None
    assert "differs from its pre-rebase sha" in result


def test_irreconcilable_claim_still_mid_rebase_fails(leerie, tmp_path):
    """Worker claims 'irreconcilable' but never actually finished the
    abort — .git/rebase-merge is still present."""
    repo = init_git_repo(tmp_path / "repo")
    pre_sha = _rev_parse(repo, "HEAD")
    (repo / ".git" / "rebase-merge").mkdir()
    result = asyncio.run(
        leerie.check_rebaser_worktree_state(
            repo, "irreconcilable", pre_sha))
    assert result is not None
    assert "mid-rebase" in result
    assert "abort did not complete" in result


def test_failed_claim_still_mid_rebase_fails(leerie, tmp_path):
    repo = init_git_repo(tmp_path / "repo")
    pre_sha = _rev_parse(repo, "HEAD")
    (repo / ".git" / "rebase-apply").mkdir()
    result = asyncio.run(
        leerie.check_rebaser_worktree_state(repo, "failed", pre_sha))
    assert result is not None
    assert "mid-rebase" in result
