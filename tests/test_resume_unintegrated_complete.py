"""A crash between a wave's settle and its integration must not drop a
finished sibling from the run branch (DESIGN §6 *Skipping a settled subtask
never skips its integration*; review round 1 H2).

Driven for real: a git repo, the real `setup-run.sh` / `new-worktree.sh` /
`integrate.sh`, the real `integrate_wave`, `_redrive_blocked_subtasks` and
`_redrive_uncovered_subtasks`. Only the implementer (`_settle_subtask`) and
the coverage measurement are scripted. Wave `[a, b]`: `a` completes, `b`
ends blocked and is re-driven, and `a`'s coverage fires once and is
re-driven — so the stretch between "a is complete" and `integrate_wave`
holds every kind of save the wave-end re-drives make. The process is killed
right after each of those saves in turn, then resumed; every time, both
branches must end up merged into the run branch and the wave advanced."""
from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path

import pytest

from tests.conftest import worker_exit
from tests.conftest import REPO_ROOT

RUN_ID = "20261028-resume-h2"


class _Crash(BaseException):
    """A process death: not an `Exception`, so no advisory handler on the
    way (the coverage measurement catches `Exception`) can swallow it."""


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True,
                          capture_output=True, text=True).stdout.strip()


def _setup(leerie, tmp_path: Path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "base.txt").write_text("base\n")
    (repo / ".gitignore").write_text(".leerie/\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    root = repo / ".leerie"
    monkeypatch.setenv("LEERIE_STATE_DIR", str(root))
    monkeypatch.chdir(repo)
    st = leerie.State(root, RUN_ID)
    st.repo_root = repo
    st.data = {"task": "t", "waves": [["a", "b"]], "completed_waves": 0,
               "skip_base_baseline": True, "subtask_status": {},
               "working_branch": "main"}
    st.save()
    # `repo` below is the run's own repository (DESIGN §6 *The run's own
    # repository*): the run's branches, the scripts' working directory.
    # The user's repository is `root.parent`.
    leerie._ensure_run_repo(st)
    return st.run_repo, root, st


def _patch(leerie, monkeypatch, repo: Path, run_dir: Path):
    monkeypatch.setattr(leerie, "_warn_scoped_degraded_once",
                        lambda *a, **k: None)
    monkeypatch.setattr(leerie, "_disk_free_ratio", lambda *a: 1.0)

    async def _noop(*a, **k):
        return None
    monkeypatch.setattr(leerie, "_assert_repo_unchanged", _noop)

    def _commit_on_branch(sid: str, text: str) -> None:
        # The implementer's work: one commit on the subtask's own branch,
        # in the worktree the real script made.
        out = subprocess.run(
            ["bash", str(REPO_ROOT / "scripts" / "new-worktree.sh"), sid,
             RUN_ID], cwd=repo, capture_output=True, text=True, check=True)
        wt = Path(out.stdout.strip().splitlines()[-1])
        # Appended, so a re-run attempt after a resume still has a change.
        with (wt / f"{sid}.txt").open("a") as f:
            f.write(text)
        _git(wt, "add", "-A")
        _git(wt, "commit", "-q", "-m", f"{sid}: {text.strip()}")
        worker_exit(wt)   # a worker's exit takes its tree's branch

    async def settle(sid, leerie_dir, caps, st, models, efforts,
                     redrive_note=None):
        st.data["subtask_status"][sid] = "in_progress"
        st.save()
        if sid == "b" and redrive_note is None:
            _commit_on_branch(sid, "b try\n")
            st.data["subtask_status"][sid] = "blocked"
            st.save()
            return {"subtask_id": sid, "status": "blocked",
                    "blocker": "x", "blocker_kind": "other"}
        _commit_on_branch(sid, f"{sid} {'redrive' if redrive_note else 'work'}\n")
        st.data["subtask_status"][sid] = "complete"
        st.save()
        return {"subtask_id": sid, "status": "complete", "summary": "ok"}
    monkeypatch.setattr(leerie, "_settle_subtask", settle)

    measured: dict[str, int] = {}

    async def measure(sid, tree, *a, **k):
        assert Path(tree) == leerie._subtask_worktree_path(run_dir, sid)
        measured[sid] = measured.get(sid, 0) + 1
        fire = sid == "a" and measured[sid] == 1
        return {"measured": True, "share": 1.0 if fire else 0.0,
                "uncovered": {"a.txt": [1]} if fire else {}}

    async def gaps(*a, **k):
        return []
    monkeypatch.setattr(leerie, "_measure_subtask_coverage", measure)
    monkeypatch.setattr(leerie, "_settle_typed_gaps", gaps)


def _run_wave(leerie, st, crash_at: int | None, marks: list[int]):
    """One invocation of `phase_execute`. Saves are numbered from the first
    one at which `a` is `complete`; `marks` collects the numbers seen before
    `integrate_wave` starts. With `crash_at`, the process dies right after
    that save reaches disk."""
    real_save = type(st).save
    real_integrate = leerie.integrate_wave
    seen = {"n": 0, "integrating": False}

    def save(self):
        real_save(self)
        if seen["integrating"]:
            return
        if (self.data.get("subtask_status") or {}).get("a") != "complete" \
                and not seen["n"]:
            return
        seen["n"] += 1
        marks.append(seen["n"])
        if crash_at is not None and seen["n"] == crash_at:
            raise _Crash(f"killed after save {crash_at}")

    async def integrate(*a, **k):
        seen["integrating"] = True
        return await real_integrate(*a, **k)

    st.save = save.__get__(st)
    try:
        leerie.integrate_wave = integrate
        asyncio.run(leerie.phase_execute(
            st.run_dir, st, dict(leerie.DEFAULT_CAPS), {}, {}))
    finally:
        leerie.integrate_wave = real_integrate
        st.save = real_save.__get__(st)


def _merged(repo: Path, sid: str) -> bool:
    return subprocess.run(
        ["git", "merge-base", "--is-ancestor",
         f"leerie/subtasks/{RUN_ID}/{sid}", f"leerie/runs/{RUN_ID}"],
        cwd=repo).returncode == 0


_POINTS: list[list[int]] = []


def _crash_points(leerie, tmp_path, monkeypatch) -> list[int]:
    """The save numbers of an uninterrupted wave, measured once per session
    (every parametrized case needs the same count)."""
    if _POINTS:
        return _POINTS[0]
    repo, root, st = _setup(leerie, tmp_path / "probe", monkeypatch)
    _patch(leerie, monkeypatch, repo, st.run_dir)
    marks: list[int] = []
    _run_wave(leerie, st, None, marks)
    assert st.data["completed_waves"] == 1
    assert _merged(repo, "a") and _merged(repo, "b")
    st.release_lock()
    _POINTS.append(marks)
    return marks


def test_uninterrupted_wave_has_saves_between_settle_and_integrate(
        leerie, tmp_path, monkeypatch):
    # The window this file is about is real: the blocked re-drive and the
    # coverage re-drive each save more than once between `a` settling and
    # the wave integrating.
    marks = _crash_points(leerie, tmp_path, monkeypatch)
    assert len(marks) >= 5, marks
    # Every one of them is a parametrized crash point below.
    assert len(marks) < 25, marks


@pytest.mark.parametrize("crash_at", range(1, 25))
def test_crash_after_each_save_then_resume_integrates_everything(
        leerie, tmp_path, monkeypatch, crash_at):
    points = _crash_points(leerie, tmp_path, monkeypatch)
    if crash_at > len(points):
        pytest.skip(f"only {len(points)} save points in this window")
    repo, root, st = _setup(leerie, tmp_path / "run", monkeypatch)
    _patch(leerie, monkeypatch, repo, st.run_dir)
    with pytest.raises(_Crash):
        _run_wave(leerie, st, crash_at, [])
    st.release_lock()
    # The resume: a fresh State read back from disk, a fresh phase_execute.
    st2 = leerie.State(root, RUN_ID)
    st2.repo_root = root.parent
    assert st2.load()
    assert st2.data["completed_waves"] == 0
    _run_wave(leerie, st2, None, [])
    assert st2.data["completed_waves"] == 1
    assert _merged(repo, "a"), f"a's work never reached the run branch " \
        f"after a crash at save {crash_at}"
    assert _merged(repo, "b")
    subjects = _git(repo, "log", "--first-parent", "--format=%s",
                    f"leerie/runs/{RUN_ID}").splitlines()
    assert "leerie: integrate a" in subjects
    assert "leerie: integrate b" in subjects
    st2.release_lock()


def test_not_integrated_is_exit_one_only(leerie, tmp_path, monkeypatch):
    """A missing branch or an unreadable staging is no evidence: integrating
    on it would die on `integrate.sh`'s precondition."""
    repo, root, st = _setup(leerie, tmp_path, monkeypatch)
    subprocess.run(["bash", str(REPO_ROOT / "scripts" / "setup-run.sh"),
                    RUN_ID, "main"], cwd=repo, check=True,
                   capture_output=True)
    staging = st.run_dir / "worktrees" / "staging"
    _git(repo, "branch", f"leerie/subtasks/{RUN_ID}/merged",
         f"leerie/runs/{RUN_ID}")
    wt = tmp_path / "side"
    _git(repo, "worktree", "add", "-q", "-b",
         f"leerie/subtasks/{RUN_ID}/ahead", str(wt), f"leerie/runs/{RUN_ID}")
    (wt / "x.txt").write_text("x\n")
    _git(wt, "add", "-A")
    _git(wt, "commit", "-q", "-m", "x")
    worker_exit(wt)   # a worker's exit takes its tree's branch
    got = asyncio.run(leerie._wave_sids_not_integrated(
        staging, RUN_ID, ["merged", "ahead", "missing"]))
    assert got == ["ahead"]
    got = asyncio.run(leerie._wave_sids_not_integrated(
        tmp_path / "nope", RUN_ID, ["ahead"]))
    assert got == []
    st.release_lock()
