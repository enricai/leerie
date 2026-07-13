"""Tests for the conformer clobber-survival guard (DESIGN §9 *No
clobbering the implementer's work*): `clobbered_owned_files` +
`_blob_sha`, and the source-coupling wiring in both conformance loops.

The guard detects when a conformer reverted-to-base or deleted a file the
implementer committed — the data-loss signature from run b5d82a9a
(`git stash -u; git checkout <base> -- .`). A legitimate conformer edit
(a distinct third content state) must NOT be flagged.
"""
from __future__ import annotations

import asyncio
import inspect
import subprocess


def _git(path, *args):
    subprocess.run(["git", *args], cwd=str(path), check=True,
                   capture_output=True, text=True)


def _rev(path, ref="HEAD"):
    return subprocess.run(["git", "rev-parse", ref], cwd=str(path),
                          capture_output=True, text=True).stdout.strip()


def _impl_repo(tmp_path):
    """A repo with base commit (branch `run`) + an implementer commit that
    changes a.py and b.py but leaves c.py. Returns (path, impl_head_sha)."""
    d = tmp_path
    _git(d, "init", "-q", "-b", "main")
    _git(d, "config", "user.email", "t@leerie.local")
    _git(d, "config", "user.name", "leerie test")
    for n in ("a.py", "b.py", "c.py"):
        (d / n).write_text(f"base {n}\n")
    _git(d, "add", "-A")
    _git(d, "commit", "-qm", "base")
    _git(d, "branch", "run")
    (d / "a.py").write_text("IMPL a\n")
    (d / "b.py").write_text("IMPL b\n")
    _git(d, "add", "-A")
    _git(d, "commit", "-qm", "implementer")
    return d, _rev(d)


class TestClobberedOwnedFiles:
    def test_legit_edit_not_flagged(self, leerie, tmp_path):
        d, impl = _impl_repo(tmp_path)
        (d / "a.py").write_text("IMPL a + conformer fix\n")  # distinct 3rd state
        _git(d, "add", "-A")
        _git(d, "commit", "-qm", "conformer: fix a")
        r = asyncio.run(leerie.clobbered_owned_files(str(d), "run", impl))
        assert r == []

    def test_revert_to_base_flagged(self, leerie, tmp_path):
        d, impl = _impl_repo(tmp_path)
        _git(d, "checkout", "run", "--", "a.py", "b.py")  # the incident
        _git(d, "add", "-A")
        _git(d, "commit", "-qm", "conformer: revert")
        r = asyncio.run(leerie.clobbered_owned_files(str(d), "run", impl))
        assert set(r) == {"a.py (reverted-to-base)", "b.py (reverted-to-base)"}

    def test_deletion_flagged(self, leerie, tmp_path):
        d, impl = _impl_repo(tmp_path)
        _git(d, "rm", "-q", "b.py")
        _git(d, "commit", "-qm", "conformer: remove b")
        r = asyncio.run(leerie.clobbered_owned_files(str(d), "run", impl))
        assert r == ["b.py (deleted)"]

    def test_untouched_impl_file_never_flagged(self, leerie, tmp_path):
        # c.py was never in the implementer's owned set, so even the
        # conformer editing it (or reverting it) is not a clobber of
        # implementer work — it is outside the owned set entirely.
        d, impl = _impl_repo(tmp_path)
        (d / "c.py").write_text("conformer touched c\n")
        _git(d, "add", "-A")
        _git(d, "commit", "-qm", "conformer: touch c")
        r = asyncio.run(leerie.clobbered_owned_files(str(d), "run", impl))
        assert r == []

    def test_new_file_addition_not_flagged(self, leerie, tmp_path):
        d, impl = _impl_repo(tmp_path)
        (d / "a.py").write_text("IMPL a + fix\n")
        (d / "d.py").write_text("brand new\n")
        _git(d, "add", "-A")
        _git(d, "commit", "-qm", "conformer: fix a, add d")
        r = asyncio.run(leerie.clobbered_owned_files(str(d), "run", impl))
        assert r == []

    def test_round0_snapshot_catches_multi_round_clobber(self, leerie, tmp_path):
        """Load-bearing: impl_head must be snapshotted BEFORE round 0. A
        round-0 clobber + a later legit round would be MISSED if the guard
        used a per-round HEAD (by round 1 the file already == base)."""
        d, impl = _impl_repo(tmp_path)
        # round 0: revert a to base and commit
        _git(d, "checkout", "run", "--", "a.py")
        _git(d, "add", "-A")
        _git(d, "commit", "-qm", "conformer: round0 revert a")
        r0_head = _rev(d)
        # round 1: legit edit to b
        (d / "b.py").write_text("IMPL b + fix\n")
        _git(d, "add", "-A")
        _git(d, "commit", "-qm", "conformer: round1 fix b")
        # correct base (pre-loop impl HEAD): catches the round-0 clobber
        assert asyncio.run(leerie.clobbered_owned_files(str(d), "run", impl)) \
            == ["a.py (reverted-to-base)"]
        # wrong base (per-round HEAD captured at round 1): misses it
        assert asyncio.run(
            leerie.clobbered_owned_files(str(d), "run", r0_head)) == []

    def test_empty_refs_return_empty(self, leerie, tmp_path):
        d, impl = _impl_repo(tmp_path)
        assert asyncio.run(leerie.clobbered_owned_files(str(d), "", impl)) == []
        assert asyncio.run(leerie.clobbered_owned_files(str(d), "run", "")) == []


class TestBlobSha:
    def test_present_and_absent(self, leerie, tmp_path):
        d, impl = _impl_repo(tmp_path)
        present = asyncio.run(leerie._blob_sha(str(d), "HEAD", "a.py"))
        assert present and len(present) >= 7
        # a path absent at the ref returns None (NOT the literal ref string —
        # the bare-`rev-parse` footgun this helper guards against).
        absent = asyncio.run(leerie._blob_sha(str(d), "HEAD", "nope.py"))
        assert absent is None


# --- rollback behavior (the strict-mode "restore clobbered work" half) ---

class TestRollbackRestoresClobber:
    def test_rollback_to_impl_head_restores_clobbered_files(
            self, leerie, tmp_path):
        """The strict-mode response to a detected clobber: resetting to the
        pre-conformer implementer HEAD must restore the clobbered content
        AND drop the conformer's clobbering commit."""
        d, impl = _impl_repo(tmp_path)
        # conformer reverts a.py to base and commits (the clobber)
        _git(d, "checkout", "run", "--", "a.py")
        _git(d, "add", "-A")
        _git(d, "commit", "-qm", "conformer: clobber a")
        assert (d / "a.py").read_text() == "base a.py\n"  # clobbered
        # guard fires, then strict mode rolls back
        assert asyncio.run(
            leerie.clobbered_owned_files(str(d), "run", impl)) \
            == ["a.py (reverted-to-base)"]
        asyncio.run(leerie.rollback_conformer_commits(str(d), impl))
        # implementer content restored, and no clobber remains detectable
        assert (d / "a.py").read_text() == "IMPL a\n"
        assert _rev(d) == impl
        assert asyncio.run(
            leerie.clobbered_owned_files(str(d), "run", impl)) == []


# --- wiring seams (source-coupling; the guard is inert without them) -----

def test_per_subtask_loop_snapshots_before_loop_and_checks(leerie):
    src = inspect.getsource(leerie._run_conformance_phase)
    # snapshot must be captured before the round loop
    snap_pos = src.index("impl_head_sha = await _branch_head_sha")
    loop_pos = src.index('for c_round in range(caps["conformance_rounds"])')
    assert snap_pos < loop_pos, (
        "impl_head_sha must be snapshotted BEFORE the conformer round loop")
    assert "clobbered_owned_files(" in src
    # rollback only under strict mode
    check_pos = src.index("clobbered_owned_files(")
    assert 'caps.get("strict_conformer")' in src[check_pos:]


def test_final_conformer_loop_checks_clobber(leerie):
    src = inspect.getsource(leerie.run_final_conformance)
    assert "staging_before_sha = await _branch_head_sha" in src
    assert "clobbered_owned_files(" in src
    snap_pos = src.index("staging_before_sha = await _branch_head_sha")
    loop_pos = src.index('for c_round in range(caps["conformance_rounds"])')
    assert snap_pos < loop_pos
