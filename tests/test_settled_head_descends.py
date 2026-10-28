"""A settled head is the subtask branch tip or a descendant of it, never a
commit that makes an unmerged branch read as integrated (DESIGN §6
*Integrated means the settled commit is on the run branch*; review round 4
M1).

Two worker shapes, both on the real `setup-run.sh` / `new-worktree.sh` /
`integrate.sh`:

- base checkout: the worker commits on its own branch, then `git checkout
  <fork>~0` to compare behaviour and never switches back. HEAD is the fork
  point, which every staging already contains. Recording it as the settled
  head made a resumed wave skip the merge, and the branch's commits never
  reached the run branch.
- replay (barnacle 45fa4253): the worker detaches at the fork point and
  commits there; the branch is moved forward to the work, as before.

Each is driven through a real `phase_execute` that dies between settle and
integration, then a resumed `phase_execute` on a State read back from
disk."""
from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest

from tests.conftest import REPO_ROOT
from tests.test_settled_head_integration import (RUN_ID, _branch_tip,
                                                 _detached_commits, _git,
                                                 _in_staging, _integrate,
                                                 _world)


class _Crash(BaseException):
    """A process death between settle and integration."""


def _base_checkout(wt: Path) -> tuple[str, str]:
    """Commit the work on the branch, then detach back at the fork point."""
    fork = _git(wt, "rev-parse", "HEAD")
    (wt / "work.txt").write_text("the subtask's work\n")
    _git(wt, "add", "-A")
    _git(wt, "commit", "-q", "-m", "t1 work")
    work = _git(wt, "rev-parse", "HEAD")
    _git(wt, "checkout", "-q", f"{fork}~0")
    return fork, work


def test_base_checkout_records_the_branch_tip(leerie, tmp_path, monkeypatch,
                                              capsys):
    repo, st, staging, wt = _world(leerie, tmp_path, monkeypatch)
    fork, work = _base_checkout(wt)
    assert _branch_tip(repo) == work
    got = asyncio.run(leerie._record_settled_head(st, st.run_dir, "t1"))
    assert got == work, (got, fork, work)
    assert st.data["settled_heads"] == {"t1": work}
    assert _branch_tip(repo) == work                 # never moved back
    cap = capsys.readouterr()
    assert "the branch tip is the settled head" in cap.out + cap.err
    heads = st.data["settled_heads"]
    assert asyncio.run(leerie._wave_sids_not_integrated(
        staging, RUN_ID, ["t1"], heads)) == ["t1"]
    assert asyncio.run(leerie._wave_sids_integrated_before(
        staging, RUN_ID, ["t1"], "HEAD", heads)) == []
    out, _ = _integrate(leerie, st)
    assert out == ["t1"]
    assert _in_staging(staging, work)


def test_resume_tests_the_branch_as_well_as_the_head(leerie, tmp_path,
                                                     monkeypatch):
    """A recorded head that IS on staging (here: the fork point, from an
    older state file) does not make an unmerged branch integrated."""
    repo, st, staging, wt = _world(leerie, tmp_path, monkeypatch)
    fork, work = _base_checkout(wt)
    heads = {"t1": fork}
    assert asyncio.run(leerie._wave_sids_not_integrated(
        staging, RUN_ID, ["t1"], heads)) == ["t1"]
    assert asyncio.run(leerie._wave_sids_integrated_before(
        staging, RUN_ID, ["t1"], "HEAD", heads)) == []


def test_reintegrate_never_points_the_branch_backwards(leerie, tmp_path,
                                                       monkeypatch):
    repo, st, staging, wt = _world(leerie, tmp_path, monkeypatch)
    fork, work = _base_checkout(wt)
    st.data["settled_heads"] = {"t1": fork}
    st.save()
    pre = _git(staging, "rev-parse", "HEAD")
    asyncio.run(leerie._reintegrate_settled_head(staging, st, "t1", pre))
    assert _branch_tip(repo) == work
    assert _in_staging(staging, work)


def _patch(leerie, monkeypatch, repo: Path, worker) -> None:
    monkeypatch.setattr(leerie, "_warn_scoped_degraded_once",
                        lambda *a, **k: None)
    monkeypatch.setattr(leerie, "_disk_free_ratio", lambda *a: 1.0)

    async def _noop(*a, **k):
        return None
    monkeypatch.setattr(leerie, "_assert_repo_unchanged", _noop)

    async def settle(sid, leerie_dir, caps, st, models, efforts,
                     redrive_note=None):
        out = subprocess.run(
            ["bash", str(REPO_ROOT / "scripts" / "new-worktree.sh"), sid,
             RUN_ID], cwd=repo, capture_output=True, text=True, check=True)
        worker(Path(out.stdout.strip().splitlines()[-1]))
        st.data["subtask_status"][sid] = "complete"
        st.save()
        return {"subtask_id": sid, "status": "complete", "summary": "ok"}
    monkeypatch.setattr(leerie, "_settle_subtask", settle)

    async def measure(*a, **k):
        return {"measured": True, "share": 0.0, "uncovered": {}}

    async def gaps(*a, **k):
        return []
    monkeypatch.setattr(leerie, "_measure_subtask_coverage", measure)
    monkeypatch.setattr(leerie, "_settle_typed_gaps", gaps)


def _phase_execute(leerie, st, crash: bool) -> None:
    real = leerie.integrate_wave

    async def integrate(*a, **k):
        if crash:
            raise _Crash("killed between settle and integration")
        return await real(*a, **k)
    leerie.integrate_wave = integrate
    try:
        asyncio.run(leerie.phase_execute(
            st.run_dir, st, dict(leerie.DEFAULT_CAPS), {}, {}))
    finally:
        leerie.integrate_wave = real


@pytest.mark.parametrize("shape", ["base_checkout", "replay"])
def test_crash_after_settle_then_resume_merges_the_work(
        leerie, tmp_path, monkeypatch, shape):
    repo, st, staging, wt0 = _world(leerie, tmp_path, monkeypatch)
    st.data["subtask_status"] = {}
    st.data["skip_base_baseline"] = True
    st.save()
    made: dict[str, str] = {}

    def worker(wt: Path) -> None:
        if shape == "base_checkout":
            made["fork"], made["work"] = _base_checkout(wt)
        else:
            made["fork"] = _git(wt, "rev-parse", "HEAD")
            made["work"] = _detached_commits(wt)
    _patch(leerie, monkeypatch, repo, worker)
    with pytest.raises(_Crash):
        _phase_execute(leerie, st, crash=True)
    assert st.data["subtask_status"]["t1"] == "complete"
    assert st.data["settled_heads"] == {"t1": made["work"]}
    assert _branch_tip(repo) == made["work"]
    assert not _in_staging(staging, made["work"])
    st.release_lock()
    st2 = leerie.State(repo / ".leerie", RUN_ID)
    st2.repo_root = repo
    assert st2.load()
    _phase_execute(leerie, st2, crash=False)
    assert st2.data["completed_waves"] == 1
    assert _in_staging(staging, made["work"]), \
        f"{shape}: the subtask's work never reached the run branch"
    assert subprocess.run(["git", "merge-base", "--is-ancestor",
                           made["work"], f"leerie/runs/{RUN_ID}"],
                          cwd=repo).returncode == 0
    st2.release_lock()
