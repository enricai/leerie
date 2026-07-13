"""Tests for the empty-handoff-keeps-committed-work fix (settle_subtask).

The defect: when a worker is reaped mid-turn (e.g. it backgrounded an
expensive final step like a build that OOM-died) it writes no checkpoint,
`validate_result` tags the synthesized result `empty_handoff`, and the settle
loop `fail()`s it — which `_reset_subtask_worktree`s away a green, committed
diff and burns the retry cap until the whole run dies.

The fix (in `settle_subtask`): before failing an `empty_handoff`, if the
worktree has commits ahead of the run branch, the worker DID produce a
deliverable — settle it as `complete` and let the advisory conformance phase
record whatever verification step didn't finish, instead of discarding it.

The load-bearing predicate is `branch_has_commits_ahead` — a POSITIVE-polarity
bool (True only when the worktree exists, git succeeds, AND there are commits).
This matters: the earlier `check_branch_has_commits(...) is None` gate was buggy
because that function returns None for THREE cases (worktree gone, git failed,
has-commits), so it would have rescued an `empty_handoff` as a fake `complete`
when the worktree was gone. `branch_has_commits_ahead` collapses the
indeterminate cases to False (not rescued) so only a proven commit triggers the
rescue.

These tests pin that predicate's contract against real temp git repos
(mirroring test_clobbered_owned_files.py) and source-couple the rescue wiring in
`settle_subtask` so the fix can't be silently reverted.
"""
from __future__ import annotations

import asyncio
import inspect
import subprocess


def _git(path, *args):
    subprocess.run(["git", *args], cwd=str(path), check=True,
                   capture_output=True, text=True)


def _repo_with_run_branch(tmp_path):
    """A repo with a base commit on branch `run` (the run branch). Returns
    the path. HEAD == run, no commits ahead yet."""
    d = tmp_path
    _git(d, "init", "-q", "-b", "main")
    _git(d, "config", "user.email", "t@leerie.local")
    _git(d, "config", "user.name", "leerie test")
    (d / "base.py").write_text("base\n")
    _git(d, "add", "-A")
    _git(d, "commit", "-qm", "base")
    _git(d, "branch", "run")
    return d


class TestBranchHasCommitsAhead:
    """`branch_has_commits_ahead(...) is True` is exactly the rescue gate the
    settle loop uses to decide whether an `empty_handoff` holds real work.
    True ONLY on a proven commit; every indeterminate case is False."""

    def test_committed_work_is_true(self, leerie, tmp_path):
        # Worker committed a green diff, then was reaped mid-build (no
        # checkpoint). The branch has a commit ahead of `run` → rescuable.
        d = _repo_with_run_branch(tmp_path)
        (d / "feature.py").write_text("the implemented feature\n")
        _git(d, "add", "-A")
        _git(d, "commit", "-qm", "feat: implement the thing")
        r = asyncio.run(leerie.branch_has_commits_ahead(str(d), "run"))
        assert r is True

    def test_no_commits_is_false(self, leerie, tmp_path):
        # Worker was reaped having committed nothing — a genuine no-op. Not
        # rescuable.
        d = _repo_with_run_branch(tmp_path)
        r = asyncio.run(leerie.branch_has_commits_ahead(str(d), "run"))
        assert r is False

    def test_uncommitted_only_is_false(self, leerie, tmp_path):
        # Debris left uncommitted by a reaped worker is not a deliverable.
        # No commit ahead of `run` → not rescuable.
        d = _repo_with_run_branch(tmp_path)
        (d / "half-done.py").write_text("work in progress, never committed\n")
        r = asyncio.run(leerie.branch_has_commits_ahead(str(d), "run"))
        assert r is False

    def test_missing_worktree_is_false(self, leerie, tmp_path):
        # Worktree gone (cleanup ran) → indeterminate → False, so the settle
        # loop does NOT rescue it (the earlier `is None` gate was buggy here:
        # a gone worktree returned None and would have been rescued as a fake
        # complete). This is the load-bearing regression pin for DEFECT 1.
        missing = tmp_path / "gone"
        r = asyncio.run(leerie.branch_has_commits_ahead(str(missing), "run"))
        assert r is False

    def test_git_failure_is_false(self, leerie, tmp_path):
        # An existing dir that is NOT a git repo → `git log` returns nonzero →
        # indeterminate → False (not rescued). Same DEFECT 1 regression class as
        # the missing-worktree case, for the returncode!=0 branch.
        notarepo = tmp_path / "plain"
        notarepo.mkdir()
        r = asyncio.run(leerie.branch_has_commits_ahead(str(notarepo), "run"))
        assert r is False


class TestCheckBranchHasCommitsUnchanged:
    """`check_branch_has_commits` (the no-op gate on the `complete` path) must
    keep its original semantics after the refactor — indeterminate states
    return None (don't block), only a proven-empty branch is `no_commits`."""

    def test_has_commits_returns_none(self, leerie, tmp_path):
        d = _repo_with_run_branch(tmp_path)
        (d / "feature.py").write_text("x\n")
        _git(d, "add", "-A")
        _git(d, "commit", "-qm", "feat")
        r = asyncio.run(leerie.check_branch_has_commits("sid", str(d), "run"))
        assert r is None

    def test_empty_branch_returns_no_commits(self, leerie, tmp_path):
        d = _repo_with_run_branch(tmp_path)
        r = asyncio.run(leerie.check_branch_has_commits("sid", str(d), "run"))
        assert r is not None and r[0] == "no_commits"

    def test_missing_worktree_returns_none(self, leerie, tmp_path):
        # Preserved original behavior: gate does NOT block on a gone worktree.
        missing = tmp_path / "gone"
        r = asyncio.run(
            leerie.check_branch_has_commits("sid", str(missing), "run"))
        assert r is None


class TestSettleWiring:
    """Source-coupling guards: the rescue logic is wired into settle_subtask.
    The fix is inert without this wiring, and a plain re-read of the function
    body catches a silent revert (mirrors test_dep_capture_wiring.py's approach).
    """

    def test_settle_subtask_rescues_empty_handoff_with_commits(self, leerie):
        src = inspect.getsource(leerie.settle_subtask)
        # The rescue branch keys on the empty_handoff kind...
        assert 'kind == "empty_handoff"' in src
        # ...and gates on PROVEN committed work via the positive-polarity
        # predicate (NOT the buggy `check_branch_has_commits(...) is None`).
        assert "branch_has_commits_ahead(" in src
        # A rescued result is settled as complete, not failed/reset.
        assert "rescued_from_empty_handoff" in src

    def test_rescue_does_not_use_the_buggy_is_none_gate(self, leerie):
        """DEFECT 1 regression pin: the rescue must NOT re-introduce the
        `check_branch_has_commits(...) is None` gate, which over-fires on a
        gone worktree / git failure."""
        src = inspect.getsource(leerie.settle_subtask)
        # The empty_handoff rescue region must not gate on `... is None`.
        rescue_start = src.find('kind == "empty_handoff"')
        rescue_region = src[rescue_start:rescue_start + 400]
        assert "check_branch_has_commits" not in rescue_region
        assert "branch_has_commits_ahead" in rescue_region

    def test_rescue_precedes_the_fail_call(self, leerie):
        """The commit-check must be evaluated BEFORE fail() on the
        empty_handoff path — fail() calls _reset_subtask_worktree, which would
        destroy the very commits the rescue keeps."""
        src = inspect.getsource(leerie.settle_subtask)
        rescue_idx = src.find('kind == "empty_handoff"')
        fail_idx = src.find("done = await fail(kind, message)")
        assert rescue_idx != -1 and fail_idx != -1
        assert rescue_idx < fail_idx

    def test_confidence_gate_skipped_for_rescued(self, leerie):
        """A rescued result has no confidence envelope (the worker was reaped
        mid-turn), so the confidence gate must be skipped — otherwise it loops
        re-spawning the doomed worker."""
        src = inspect.getsource(leerie.settle_subtask)
        assert "not rescued_from_empty_handoff" in src

    def test_dirty_check_discards_debris_for_rescued(self, leerie):
        """A reaped worker may leave uncommitted debris; a dirty_worktree fail
        would _reset_subtask_worktree and destroy the kept commits. The dirty
        check must discard the debris for rescued results instead of failing."""
        src = inspect.getsource(leerie.settle_subtask)
        assert 'git", "checkout", "--", "."' in src
        assert "rescued_from_empty_handoff" in src
