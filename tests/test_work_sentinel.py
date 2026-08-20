"""DESIGN §12 *Judgment-worker isolation*, L4 — the `/work` sentinel.

This is the layer that is an actual guarantee rather than a strong mitigation,
so it gets the sharpest tests.

Why it must exist even after judgment workers lose the permission bypass: the
CLI's working-directory boundary is enforced on tool *arguments*, and an
allowlisted build verb runs arbitrary code. Measured live — with permissions ON
and `python3` allowlisted, `python3 -c "open('<outside>','w')"` wrote outside
the worker's cwd while the `Write` tool aimed at the same path was rejected. So
a residual escape exists by construction, and this is what catches it.

Modelled on `check_rebaser_worktree_state`: trust the worker, then mechanically
re-check the claim.

Every assertion here runs against real git repositories. A mocked `git` would
be testing the mock.
"""
from __future__ import annotations

import asyncio
import inspect
import subprocess

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
    """Only what the sentinel touches."""

    def __init__(self, repo):
        self.repo_root = repo
        self.run_id = "r1"
        self.data = {}

    def save(self):
        pass


def _snap(leerie, repo):
    return asyncio.run(leerie._snapshot_repo_state(str(repo)))


def _check(leerie, st, phase="phase 1 (classify)"):
    return asyncio.run(leerie._assert_repo_unchanged(st, phase))


# ---------------------------------------------------------------------------
# the reproduction, and its anti-vacuity partner
# ---------------------------------------------------------------------------

class TestDetection:
    def test_a_modified_tracked_file_is_caught_and_named(
            self, leerie, tmp_path, monkeypatch):
        """The incident shape: a judgment worker edits a tracked source file
        in the user's checkout."""
        repo = _init(tmp_path / "repo")
        st = _St(repo)
        st.data["repo_state_before_planning"] = _snap(leerie, repo)
        (repo / "src.txt").write_text("TAMPERED\n")
        msgs = []
        monkeypatch.setattr(leerie, "die",
                            lambda m, code=1: (_ for _ in ()).throw(
                                SystemExit(m)))
        with pytest.raises(SystemExit) as ei:
            _check(leerie, st)
        assert "src.txt" in str(ei.value)
        assert "phase 1 (classify)" in str(ei.value)

    def test_an_untouched_tree_passes(self, leerie, tmp_path, monkeypatch):
        """Anti-vacuity. The test above is equally satisfied by a sentinel
        that fires unconditionally, which would make every run die."""
        repo = _init(tmp_path / "repo")
        st = _St(repo)
        st.data["repo_state_before_planning"] = _snap(leerie, repo)
        monkeypatch.setattr(leerie, "die",
                            lambda m, code=1: pytest.fail(
                                f"sentinel fired on a clean tree: {m}"))
        _check(leerie, st)

    def test_an_untracked_file_is_caught(self, leerie, tmp_path, monkeypatch):
        """The case `_preflight_repo` structurally cannot see: its clean-tree
        gate filters `??` lines, so a worker CREATING files passes it. On the
        run that motivated this feature the task document itself was
        untracked, so this is not a corner case."""
        repo = _init(tmp_path / "repo")
        st = _St(repo)
        st.data["repo_state_before_planning"] = _snap(leerie, repo)
        (repo / "invented.txt").write_text("new\n")
        monkeypatch.setattr(leerie, "die",
                            lambda m, code=1: (_ for _ in ()).throw(
                                SystemExit(m)))
        with pytest.raises(SystemExit) as ei:
            _check(leerie, st)
        assert "invented.txt" in str(ei.value)

    def test_a_commit_on_the_users_branch_is_caught(
            self, leerie, tmp_path, monkeypatch):
        """Measured: a worker under the old configuration did exactly this —
        HEAD moved on the branch the operator was sitting on."""
        repo = _init(tmp_path / "repo")
        st = _St(repo)
        st.data["repo_state_before_planning"] = _snap(leerie, repo)
        (repo / "src.txt").write_text("changed\n")
        run_git_repo_first(repo, "add", "-A")
        run_git_repo_first(repo, "commit", "-qm", "worker commit")
        monkeypatch.setattr(leerie, "die",
                            lambda m, code=1: (_ for _ in ()).throw(
                                SystemExit(m)))
        with pytest.raises(SystemExit) as ei:
            _check(leerie, st)
        assert "HEAD moved" in str(ei.value)


class TestExemptions:
    def test_leerie_own_config_write_does_not_fire_it(
            self, leerie, tmp_path, monkeypatch):
        """`.leerie/config.toml` is the one committable file the orchestrator
        writes into the checkout (DESIGN §6½ dep capture). Reporting leerie's
        own bookkeeping as tampering would make the sentinel fire on healthy
        runs, and a check that cries wolf gets disabled."""
        repo = _init(tmp_path / "repo")
        st = _St(repo)
        st.data["repo_state_before_planning"] = _snap(leerie, repo)
        (repo / ".leerie").mkdir()
        (repo / ".leerie" / "config.toml").write_text('build = "x"\n')
        monkeypatch.setattr(leerie, "die",
                            lambda m, code=1: pytest.fail(
                                f"fired on leerie's own write: {m}"))
        _check(leerie, st)

    def test_leerie_own_branches_do_not_fire_it(self, leerie, tmp_path):
        """A run legitimately creates `leerie/runs/<id>` and
        `leerie/subtasks/<id>/*`. Only refs OUTSIDE that namespace count."""
        before = {"head": "a", "porcelain": [], "refs": [
            "refs/heads/main a"]}
        after = {"head": "a", "porcelain": [], "refs": [
            "refs/heads/main a", "refs/heads/leerie/runs/r1 a"]}
        assert leerie._diff_repo_state(before, after) == []

    def test_a_foreign_branch_still_fires_it(self, leerie):
        """Anti-vacuity for the exemption: it must not swallow every ref."""
        before = {"head": "a", "porcelain": [], "refs": [
            "refs/heads/main a"]}
        after = {"head": "a", "porcelain": [], "refs": [
            "refs/heads/main a", "refs/heads/worker-invented a"]}
        deltas = leerie._diff_repo_state(before, after)
        assert deltas and "worker-invented" in deltas[0]


class TestBaselineIdentity:
    def test_baseline_is_read_from_the_checkout_not_the_worktree(
            self, leerie, tmp_path, monkeypatch):
        """Drive it with the two trees DISAGREEING. A fixture where the
        checkout and the worktree hold the same content cannot tell a correct
        read from one that silently snapshots the wrong tree."""
        repo = _init(tmp_path / "repo")
        other = _init(tmp_path / "other")
        (other / "src.txt").write_text("DIFFERENT\n")
        run_git_repo_first(other, "add", "-A")
        run_git_repo_first(other, "commit", "-qm", "diverge")
        snap_repo = _snap(leerie, repo)
        snap_other = _snap(leerie, other)
        assert snap_repo["head"] != snap_other["head"]

        st = _St(repo)
        st.data["repo_state_before_planning"] = snap_repo
        monkeypatch.setattr(leerie, "die",
                            lambda m, code=1: pytest.fail(f"fired: {m}"))
        _check(leerie, st)

    def test_absent_baseline_is_a_no_op(self, leerie, tmp_path, monkeypatch):
        """A run that never captured one (an old state.json resumed under a
        new leerie) must not die — the sentinel degrades to off rather than
        blocking recovery."""
        repo = _init(tmp_path / "repo")
        st = _St(repo)
        (repo / "src.txt").write_text("whatever\n")
        monkeypatch.setattr(leerie, "die",
                            lambda m, code=1: pytest.fail(f"fired: {m}"))
        _check(leerie, st)

    def test_an_unreadable_checkout_warns_instead_of_dying(
            self, leerie, tmp_path, monkeypatch):
        """"Could not tell" is not "tampered".

        Every probe in `_snapshot_repo_state` returns "" on a non-zero git,
        so a transiently unreadable checkout yields head="" and refs=[] —
        which a naive diff reads as "HEAD moved" *plus* "every branch
        deleted", killing a healthy run on fabricated evidence. Caught by
        driving the real diff against a failed snapshot, not by inspection."""
        repo = _init(tmp_path / "repo")
        st = _St(repo)
        st.data["repo_state_before_planning"] = _snap(leerie, repo)

        async def failing_snapshot(_root):
            return {"head": "", "porcelain": [], "refs": [], "ok": False}

        monkeypatch.setattr(leerie, "_snapshot_repo_state", failing_snapshot)
        monkeypatch.setattr(leerie, "die",
                            lambda m, code=1: pytest.fail(
                                f"died on an unreadable checkout: {m}"))
        _check(leerie, st)

    def test_the_naive_diff_would_have_fired(self, leerie):
        """Anti-vacuity partner: proves the guard above is load-bearing by
        showing the underlying diff really does report tampering for that
        input. Without this, the test above passes against a sentinel that
        never fires at all."""
        before = {"head": "abc", "porcelain": [], "refs": ["refs/heads/main abc"]}
        failed = {"head": "", "porcelain": [], "refs": [], "ok": False}
        assert leerie._diff_repo_state(before, failed), (
            "the diff no longer reports a failed snapshot as change; the "
            "`ok` guard may now be untested")

    def test_snapshot_records_success(self, leerie, tmp_path):
        repo = _init(tmp_path / "repo")
        assert _snap(leerie, repo)["ok"] is True

    def test_snapshot_round_trips_through_json(self, leerie, tmp_path):
        """It is persisted in state.json, so it must survive serialization —
        a set or a Path here would raise at `st.save()` on every run."""
        import json
        repo = _init(tmp_path / "repo")
        snap = _snap(leerie, repo)
        assert json.loads(json.dumps(snap)) == snap


class TestWiring:
    def test_sentinel_runs_after_every_planning_checkpoint(self, leerie):
        """Per-phase, not once at the end: the point is to fire within one
        worker of the damage rather than after the whole planning spend."""
        src = inspect.getsource(leerie._run_phases)
        n = src.count("_assert_repo_unchanged(st,")
        assert n >= 6, (
            f"only {n} sentinel checks in _run_phases; every planning phase "
            "checkpoint should be followed by one")

    def test_baseline_is_captured_before_the_first_worker(self, leerie):
        """It must precede phase_classify, or the baseline already includes
        whatever the classifier did."""
        src = inspect.getsource(leerie._run_phases)
        i_base = src.index('"repo_state_before_planning"')
        i_classify = src.index("await phase_classify(")
        assert i_base < i_classify

    def test_baseline_is_not_recaptured_on_resume(self, leerie):
        """Guarded on absence, so a resume compares against the ORIGINAL
        tree. Re-snapshotting would launder a modification made by an earlier
        planning pass into the new baseline."""
        src = inspect.getsource(leerie._run_phases)
        assert '"repo_state_before_planning" not in st.data' in src
