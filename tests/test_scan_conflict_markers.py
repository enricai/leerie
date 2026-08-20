"""Tests for _scan_conflict_markers() (leerie.py:12969) — the deterministic
post-integration safety net phase 5's wave-integration loop calls after
every wave (leerie.py:30310) to detect unresolved `<<<<<<<` conflict
markers left in the staging tree.

Builds real temp git repos rather than mocking git (mirroring
tests/test_check_rebaser_worktree_state.py's discipline), since this
function's entire job is inspecting real git grep output.
"""
from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest

from tests.conftest import init_git_repo, run_git_repo_first


def test_nonexistent_staging_returns_none_without_shelling_out(
        leerie, tmp_path, monkeypatch):
    missing = tmp_path / "does-not-exist"

    async def _boom(*a, **k):
        raise AssertionError("run_proc should not be called")

    monkeypatch.setattr(leerie, "run_proc", _boom)
    result = asyncio.run(leerie._scan_conflict_markers(missing))
    assert result is None


def test_clean_tree_returns_none(leerie, tmp_path):
    repo = init_git_repo(tmp_path / "staging")
    result = asyncio.run(leerie._scan_conflict_markers(repo))
    assert result is None


def test_tree_with_conflict_markers_returns_message_with_file_count_and_sample(
        leerie, tmp_path):
    repo = init_git_repo(tmp_path / "staging")
    (repo / "b.txt").write_text("<<<<<<< HEAD\nours\n=======\ntheirs\n>>>>>>> x\n")
    run_git_repo_first(repo, "add", ".")
    run_git_repo_first(repo, "commit", "-q", "-m", "leaves markers")

    result = asyncio.run(leerie._scan_conflict_markers(repo))
    assert result is not None
    assert "1 file(s)" in result
    assert "b.txt" in result
    assert "…" not in result


def test_more_than_five_files_gets_ellipsis_marker(leerie, tmp_path):
    repo = init_git_repo(tmp_path / "staging")
    for i in range(7):
        (repo / f"c{i}.txt").write_text(
            "<<<<<<< HEAD\nours\n=======\ntheirs\n>>>>>>> x\n")
    run_git_repo_first(repo, "add", ".")
    run_git_repo_first(repo, "commit", "-q", "-m", "leaves markers in 7 files")

    result = asyncio.run(leerie._scan_conflict_markers(repo))
    assert result is not None
    assert "7 file(s)" in result
    assert "…" in result


def test_oserror_from_run_proc_is_caught_and_returns_none(
        leerie, tmp_path, monkeypatch):
    repo = init_git_repo(tmp_path / "staging")

    async def _raise_oserror(*a, **k):
        raise OSError("git binary not found")

    monkeypatch.setattr(leerie, "run_proc", _raise_oserror)
    result = asyncio.run(leerie._scan_conflict_markers(repo))
    assert result is None
