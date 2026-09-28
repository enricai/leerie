"""Tests for the sentinel's operator-overlap signature (DESIGN §12 L4).

Motivation (measured, 2026-09-26): a run died mid-phase-5 over exactly one
delta — a newly-added untracked report `.md` the OPERATOR had just dropped
into the checkout for the next run. The message blamed a worker escape and
framed resume behind a restore step, and the operator re-ran from scratch
(a full replan) instead of resuming. The stop itself is correct (the
planning baseline moved); the framing wasted a run.

`_tripwire_operator_overlap` classifies mechanically on
`_diff_repo_state`'s fixed delta prefixes + git porcelain codes (never on
prose): all-untracked-additions → operator framing that leads with
`resume`; anything else → the unchanged worker-escape framing.

Truth-table tests pair with behavioral ones driving the REAL
`_assert_repo_unchanged` against a real git repo (harness shape from
tests/test_work_sentinel.py), asserting the MESSAGE each signature
produces — the discriminating substance, not just that a die happened.
"""
from __future__ import annotations

import asyncio

import pytest

from tests.conftest import run_git_repo_first


def _init(repo):
    repo.mkdir(parents=True, exist_ok=True)
    run_git_repo_first(repo, "init", "-q", ".")
    run_git_repo_first(repo, "config", "user.email", "t@t")
    run_git_repo_first(repo, "config", "user.name", "t")
    (repo / "src.txt").write_text("original\n")
    run_git_repo_first(repo, "add", "-A")
    run_git_repo_first(repo, "commit", "-qm", "init")
    return repo


class _St:
    def __init__(self, repo):
        self.repo_root = repo
        self.run_id = "r1"
        self.data = {}

    def save(self):
        pass


def _arm(leerie, monkeypatch, repo):
    st = _St(repo)
    st.data["repo_state_before_planning"] = asyncio.run(
        leerie._snapshot_repo_state(str(repo)))
    monkeypatch.setattr(leerie, "die",
                        lambda m, code=1: (_ for _ in ()).throw(
                            SystemExit(m)))
    return st


def _check(leerie, st):
    return asyncio.run(leerie._assert_repo_unchanged(
        st, "phase 5 (wave 1)", porcelain_only=True))


# --- predicate truth table -------------------------------------------------

class TestPredicate:
    def test_all_untracked_additions_is_operator(self, leerie):
        assert leerie._tripwire_operator_overlap(
            ["working tree changed: ?? report-a.md"]) is True
        assert leerie._tripwire_operator_overlap(
            ["working tree changed: ?? report-a.md",
             "working tree changed: ?? prompt-notes.txt"]) is True

    def test_a_tracked_modification_is_not(self, leerie):
        assert leerie._tripwire_operator_overlap(
            ["working tree changed:  M src.txt"]) is False

    def test_mixed_deltas_are_not(self, leerie):
        """One tracked edit among untracked additions keeps the escape
        framing — the predicate is all-or-nothing by design."""
        assert leerie._tripwire_operator_overlap(
            ["working tree changed: ?? report-a.md",
             "working tree changed:  M src.txt"]) is False

    def test_head_and_ref_deltas_are_not(self, leerie):
        assert leerie._tripwire_operator_overlap(
            ["HEAD moved: abc -> def"]) is False
        assert leerie._tripwire_operator_overlap(
            ["ref created/moved: refs/heads/feature abc"]) is False

    def test_empty_is_not(self, leerie):
        """An empty delta list must never classify as anything — the
        caller returns before classification, and a True here would be a
        latent die-on-clean-tree if that ordering ever changed."""
        assert leerie._tripwire_operator_overlap([]) is False


# --- behavioral: the message each signature produces -----------------------

class TestMessage:
    def test_untracked_only_gets_the_operator_framing(
            self, leerie, tmp_path, monkeypatch):
        """The incident shape: the operator drops a report file for the
        next run. Still a die (the baseline moved) — but the message
        names operator activity and leads with a bare resume."""
        repo = _init(tmp_path / "repo")
        st = _arm(leerie, monkeypatch, repo)
        (repo / "next-task-report.md").write_text("findings\n")
        with pytest.raises(SystemExit) as ei:
            _check(leerie, st)
        msg = str(ei.value)
        assert "next-task-report.md" in msg
        assert "operator activity" in msg
        assert "./leerie resume r1" in msg
        # The worker-escape lead must NOT be the frame here — that exact
        # wording is what sent the measured incident to a from-scratch
        # re-run.
        assert "a worker modified your real checkout" not in msg

    def test_tracked_edit_keeps_the_escape_framing(
            self, leerie, tmp_path, monkeypatch):
        repo = _init(tmp_path / "repo")
        st = _arm(leerie, monkeypatch, repo)
        (repo / "src.txt").write_text("TAMPERED\n")
        with pytest.raises(SystemExit) as ei:
            _check(leerie, st)
        msg = str(ei.value)
        assert "a worker modified your real checkout" in msg
        assert "operator activity" not in msg

    def test_mixed_deltas_keep_the_escape_framing(
            self, leerie, tmp_path, monkeypatch):
        """A worker that edits a tracked file AND drops a new one must not
        be softened by the new file's presence."""
        repo = _init(tmp_path / "repo")
        st = _arm(leerie, monkeypatch, repo)
        (repo / "src.txt").write_text("TAMPERED\n")
        (repo / "dropped.md").write_text("x\n")
        with pytest.raises(SystemExit) as ei:
            _check(leerie, st)
        msg = str(ei.value)
        assert "a worker modified your real checkout" in msg
        assert "operator activity" not in msg
