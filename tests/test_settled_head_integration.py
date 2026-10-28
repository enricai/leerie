"""Integrated means the settled commit is on the run branch, not that the
merge exited 0 (DESIGN §6).

Replay shape (barnacle 45fa4253, pass 1): a test-only subtask's implementer
ran `git checkout <run-branch-sha>~0` to compare behaviour, never switched
back, and committed three times on the detached HEAD. The subtask branch
still pointed at the run branch, `integrate.sh` merged it as "Already up to
date" (exit 0), the wave reported "integrated 1 of 1", and the work never
reached the PR.

Driven on the real `setup-run.sh` / `new-worktree.sh` / `integrate.sh` and
the real `integrate_wave`."""
from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

from tests.conftest import REPO_ROOT

RUN_ID = "20261028-settled-head"


def _git(cwd: Path, *args: str, check: bool = True) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=check,
                          capture_output=True, text=True).stdout.strip()


def _world(leerie, tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "f.txt").write_text("base\n")
    (repo / ".gitignore").write_text(".leerie/\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    root = repo / ".leerie"
    monkeypatch.setenv("LEERIE_STATE_DIR", str(root))
    monkeypatch.chdir(repo)
    subprocess.run(["bash", str(REPO_ROOT / "scripts" / "setup-run.sh"),
                    RUN_ID, "main"], cwd=repo, check=True,
                   capture_output=True)
    st = leerie.State(root, RUN_ID)
    st.repo_root = repo
    st.data = {"task": "t", "waves": [["t1"]], "completed_waves": 0,
               "subtask_status": {"t1": "complete"},
               "working_branch": "main"}
    st.save()
    out = subprocess.run(
        ["bash", str(REPO_ROOT / "scripts" / "new-worktree.sh"), "t1",
         RUN_ID], cwd=repo, capture_output=True, text=True, check=True)
    wt = Path(out.stdout.strip().splitlines()[-1])
    assert wt == leerie._subtask_worktree_path(st.run_dir, "t1").resolve()
    staging = st.run_dir / "worktrees" / "staging"
    return repo, st, staging, wt


def _detached_commits(wt: Path) -> str:
    """The replay's worker: detach at the fork point, commit three times."""
    fork = _git(wt, "rev-parse", "HEAD")
    _git(wt, "checkout", "-q", f"{fork}~0")
    for i in range(3):
        (wt / "search.test.ts").write_text(f"case {i}\n" * (i + 1))
        _git(wt, "add", "-A")
        _git(wt, "commit", "-q", "-m", f"t1 test {i}")
    return _git(wt, "rev-parse", "HEAD")


def _branch_tip(repo: Path, sid: str = "t1") -> str:
    return _git(repo, "rev-parse", f"refs/heads/leerie/subtasks/{RUN_ID}/{sid}")


def _in_staging(staging: Path, sha: str) -> bool:
    return subprocess.run(["git", "merge-base", "--is-ancestor", sha, "HEAD"],
                          cwd=staging).returncode == 0


def _integrate(leerie, st):
    return asyncio.run(leerie.integrate_wave(
        ["t1"], (results := {"t1": {"status": "complete"}}), st.run_dir,
        dict(leerie.DEFAULT_CAPS), st, {"integrator": "sonnet"},
        {"integrator": None})), results


def test_settle_puts_the_branch_where_the_work_is(leerie, tmp_path,
                                                  monkeypatch):
    repo, st, staging, wt = _world(leerie, tmp_path, monkeypatch)
    fork = _branch_tip(repo)
    head = _detached_commits(wt)
    assert _branch_tip(repo) == fork          # the replay's state
    got = asyncio.run(leerie._record_settled_head(st, st.run_dir, "t1"))
    assert got == head
    assert st.data["settled_heads"] == {"t1": head}
    assert _branch_tip(repo) == head
    assert _git(wt, "symbolic-ref", "HEAD") == \
        f"refs/heads/leerie/subtasks/{RUN_ID}/t1"
    # The merge that follows carries the work.
    out, _ = _integrate(leerie, st)
    assert out == ["t1"]
    assert _in_staging(staging, head)
    assert (staging / "search.test.ts").read_text() == "case 2\n" * 3


def test_a_no_op_merge_is_re_integrated_from_the_settled_head(
        leerie, tmp_path, monkeypatch):
    """The settle-time repair did not happen (the branch stayed at the fork
    point): integrate.sh exits 0 having merged nothing, and integrate_wave
    re-integrates once from the settled head."""
    repo, st, staging, wt = _world(leerie, tmp_path, monkeypatch)
    head = _detached_commits(wt)
    st.data["settled_heads"] = {"t1": head}
    st.save()
    out, results = _integrate(leerie, st)
    assert out == ["t1"]
    assert results["t1"]["status"] == "complete"
    assert _in_staging(staging, head)
    assert _branch_tip(repo) == head
    assert _git(staging, "log", "-1", "--format=%s") == "leerie: integrate t1"


def test_settled_head_still_missing_is_unconverged_not_dropped(
        leerie, tmp_path, monkeypatch):
    """A settled head the second merge cannot bring in either (here: the
    branch is put back to the fork point before each integrate.sh)
    settles the subtask `unconverged`, naming the head — never counted as
    integrated."""
    repo, st, staging, wt = _world(leerie, tmp_path, monkeypatch)
    fork = _branch_tip(repo)
    head = _detached_commits(wt)
    st.data["settled_heads"] = {"t1": head}
    st.save()
    before = _git(staging, "rev-parse", "HEAD")
    # The re-point is undone before integrate.sh reads the branch.
    calls = []

    async def run_script(name, *args):
        if name == "integrate.sh":
            calls.append(args)
            _git(repo, "update-ref",
                 f"refs/heads/leerie/subtasks/{RUN_ID}/t1", fork)
        return await orig_run_script(name, *args)
    orig_run_script = leerie._run_script
    monkeypatch.setattr(leerie, "_run_script", run_script)
    out, results = _integrate(leerie, st)
    assert out == []
    assert len(calls) == 2                    # integrated, then once more
    assert results["t1"]["status"] == "unconverged"
    assert st.data["subtask_status"]["t1"] == "unconverged"
    gate = st.data["integration_gate"]["t1"]
    assert gate["outcome"] == "backed_out"
    assert head in gate["fallback_reason"]
    assert _git(staging, "rev-parse", "HEAD") == before
    assert not _in_staging(staging, head)


def test_resumed_wave_finds_unintegrated_work_by_settled_head(
        leerie, tmp_path, monkeypatch):
    repo, st, staging, wt = _world(leerie, tmp_path, monkeypatch)
    head = _detached_commits(wt)
    heads = {"t1": head}
    # By branch the work reads as merged (the branch is the fork point);
    # by settled head it is not.
    assert asyncio.run(leerie._wave_sids_not_integrated(
        staging, RUN_ID, ["t1"])) == []
    assert asyncio.run(leerie._wave_sids_not_integrated(
        staging, RUN_ID, ["t1"], heads)) == ["t1"]
    assert asyncio.run(leerie._wave_sids_integrated_before(
        staging, RUN_ID, ["t1"], "HEAD", heads)) == []


def test_interrupted_redrive_rollback_resets_the_settled_head(
        leerie, tmp_path, monkeypatch):
    repo, st, staging, wt = _world(leerie, tmp_path, monkeypatch)
    pre = _git(wt, "rev-parse", "HEAD")
    (wt / "g.txt").write_text("half\n")
    _git(wt, "add", "-A")
    _git(wt, "commit", "-q", "-m", "half-done round")
    st.data["settled_heads"] = {"t1": _git(wt, "rev-parse", "HEAD")}
    st.data["redrive_in_flight"] = {"t1": {
        "kind": "coverage", "pre_sha": pre, "round": 1,
        "status": "complete"}}
    st.save()
    asyncio.run(leerie._undo_interrupted_redrives(st, st.run_dir, ["t1"]))
    assert st.data["settled_heads"] == {"t1": pre}
