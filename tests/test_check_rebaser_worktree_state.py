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


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True, text=True)


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "test@x")
    _git(repo, "config", "user.name", "test")
    (repo / "a.txt").write_text("a\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "a")
    return repo


def _rev_parse(repo: Path, ref: str) -> str:
    return _git(repo, "rev-parse", ref).stdout.strip()


# --- "rebased" claim ---------------------------------------------------------

def test_rebased_claim_with_clean_tree_passes(leerie, tmp_path):
    """A clean, non-mid-rebase worktree with a 'rebased' claim: no error."""
    repo = _init_repo(tmp_path)
    pre_sha = _rev_parse(repo, "HEAD")
    result = asyncio.run(
        leerie.check_rebaser_worktree_state(repo, "rebased", pre_sha))
    assert result is None


def test_rebased_claim_with_conflict_markers_fails(leerie, tmp_path):
    """A 'rebased' claim but conflict markers remain in tracked content —
    the worker's self-report does not match reality."""
    repo = _init_repo(tmp_path)
    pre_sha = _rev_parse(repo, "HEAD")
    (repo / "a.txt").write_text(
        "<<<<<<< HEAD\nours\n=======\ntheirs\n>>>>>>> branch\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "leaves markers")
    result = asyncio.run(
        leerie.check_rebaser_worktree_state(repo, "rebased", pre_sha))
    assert result is not None
    assert "conflict markers remain" in result
    assert "a.txt" in result


def test_rebased_claim_still_mid_rebase_merge_fails(leerie, tmp_path):
    """A 'rebased' claim but .git/rebase-merge is still present — the
    rebase never actually completed."""
    repo = _init_repo(tmp_path)
    pre_sha = _rev_parse(repo, "HEAD")
    (repo / ".git" / "rebase-merge").mkdir()
    result = asyncio.run(
        leerie.check_rebaser_worktree_state(repo, "rebased", pre_sha))
    assert result is not None
    assert "mid-rebase" in result
    assert "rebase-merge" in result


def test_rebased_claim_still_mid_rebase_apply_fails(leerie, tmp_path):
    repo = _init_repo(tmp_path)
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
    repo = _init_repo(tmp_path)
    pre_sha = _rev_parse(repo, "HEAD")
    (repo / "b.txt").write_text("b\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "b")
    assert _rev_parse(repo, "HEAD") != pre_sha
    result = asyncio.run(
        leerie.check_rebaser_worktree_state(repo, "rebased", pre_sha))
    assert result is None


# --- "irreconcilable" / "failed" claim ---------------------------------------

def test_irreconcilable_claim_with_unchanged_head_passes(leerie, tmp_path):
    """The abort genuinely restored the branch — HEAD matches pre-rebase
    sha, no mid-rebase state: no error."""
    repo = _init_repo(tmp_path)
    pre_sha = _rev_parse(repo, "HEAD")
    result = asyncio.run(
        leerie.check_rebaser_worktree_state(
            repo, "irreconcilable", pre_sha))
    assert result is None


def test_failed_claim_with_unchanged_head_passes(leerie, tmp_path):
    repo = _init_repo(tmp_path)
    pre_sha = _rev_parse(repo, "HEAD")
    result = asyncio.run(
        leerie.check_rebaser_worktree_state(repo, "failed", pre_sha))
    assert result is None


def test_irreconcilable_claim_with_changed_head_fails(leerie, tmp_path):
    """Worker claims 'irreconcilable' (abort) but HEAD has actually moved —
    the abort did not restore the original state."""
    repo = _init_repo(tmp_path)
    pre_sha = _rev_parse(repo, "HEAD")
    (repo / "b.txt").write_text("b\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "b")
    result = asyncio.run(
        leerie.check_rebaser_worktree_state(
            repo, "irreconcilable", pre_sha))
    assert result is not None
    assert "differs from its pre-rebase sha" in result


def test_irreconcilable_claim_still_mid_rebase_fails(leerie, tmp_path):
    """Worker claims 'irreconcilable' but never actually finished the
    abort — .git/rebase-merge is still present."""
    repo = _init_repo(tmp_path)
    pre_sha = _rev_parse(repo, "HEAD")
    (repo / ".git" / "rebase-merge").mkdir()
    result = asyncio.run(
        leerie.check_rebaser_worktree_state(
            repo, "irreconcilable", pre_sha))
    assert result is not None
    assert "mid-rebase" in result
    assert "abort did not complete" in result


def test_failed_claim_still_mid_rebase_fails(leerie, tmp_path):
    repo = _init_repo(tmp_path)
    pre_sha = _rev_parse(repo, "HEAD")
    (repo / ".git" / "rebase-apply").mkdir()
    result = asyncio.run(
        leerie.check_rebaser_worktree_state(repo, "failed", pre_sha))
    assert result is not None
    assert "mid-rebase" in result
