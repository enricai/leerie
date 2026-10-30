"""A crash inside a wave-end re-drive round must not integrate the round's
undecided commits on resume (DESIGN §6 *A re-drive interrupted mid-round is
undone, never integrated*; review round 2 M2).

Driven for real, like tests/test_resume_unintegrated_complete.py: a git
repo, the real scripts, the real `phase_execute`, `integrate_wave` and both
wave-end re-drives. Only the implementer (`_settle_subtask`) and the
coverage measurement are scripted. Wave `[a, b]`: `a` completes and its
coverage fires once; `b` ends blocked and is re-driven. Every re-drive
session commits a half-done file, saves, and only then finishes it — so a
crash at any save inside a round leaves half-done work on the branch."""
from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest

from tests.conftest import REPO_ROOT
from tests.test_resume_unintegrated_complete import (RUN_ID, _Crash, _git,
                                                     _merged, _setup)

HALF = "half\n"
DONE = "done\n"


def _commit(repo: Path, sid: str, name: str, text: str) -> None:
    out = subprocess.run(
        ["bash", str(REPO_ROOT / "scripts" / "new-worktree.sh"), sid,
         RUN_ID], cwd=repo, capture_output=True, text=True, check=True)
    wt = Path(out.stdout.strip().splitlines()[-1])
    with (wt / name).open("a") as f:
        f.write(text)
    _git(wt, "add", "-A")
    _git(wt, "commit", "-q", "-m", f"{sid}: {name} {text.strip()}")


def _patch(leerie, monkeypatch, repo: Path, run_dir: Path, *,
           crash_in_session: dict | None = None):
    monkeypatch.setattr(leerie, "_warn_scoped_degraded_once",
                        lambda *a, **k: None)
    monkeypatch.setattr(leerie, "_disk_free_ratio", lambda *a: 1.0)

    async def _noop(*a, **k):
        return None
    monkeypatch.setattr(leerie, "_assert_repo_unchanged", _noop)

    async def settle(sid, leerie_dir, caps, st, models, efforts,
                     redrive_note=None):
        # Like the real `_settle_subtask`: the status is written when the
        # attempt ends, not when a re-drive starts.
        if redrive_note is None:
            _commit(repo, sid, f"{sid}.txt", f"{sid} work\n")
            status = "blocked" if sid == "b" else "complete"
            st.data["subtask_status"][sid] = status
            st.save()
            if status == "blocked":
                return {"subtask_id": sid, "status": "blocked",
                        "blocker": "x", "blocker_kind": "other"}
            return {"subtask_id": sid, "status": "complete", "summary": "ok"}
        # The re-drive session: half-done work committed, a mid-session
        # save, then the work finished.
        name = f"{sid}-redrive.txt"
        _commit(repo, sid, name, HALF)
        st.save()
        if crash_in_session and crash_in_session.get(sid):
            crash_in_session[sid] = False
            raise _Crash(f"killed mid {sid} re-drive")
        _commit(repo, sid, name, DONE)
        st.data["subtask_status"][sid] = "complete"
        st.save()
        return {"subtask_id": sid, "status": "complete", "summary": "ok"}
    monkeypatch.setattr(leerie, "_settle_subtask", settle)

    measured: dict[str, int] = {}

    async def measure(sid, tree, *a, **k):
        measured[sid] = measured.get(sid, 0) + 1
        fire = sid == "a" and measured[sid] == 1
        return {"measured": True, "share": 1.0 if fire else 0.0,
                "uncovered": {"a.txt": [1]} if fire else {}}

    async def gaps(*a, **k):
        return []
    monkeypatch.setattr(leerie, "_measure_subtask_coverage", measure)
    monkeypatch.setattr(leerie, "_settle_typed_gaps", gaps)


def _run(leerie, st, crash_at: int | None, marks: list[int]):
    """One `phase_execute`. Saves are numbered from the first one after `a`
    is `complete`, up to `integrate_wave`; with `crash_at` the process dies
    right after that save reaches disk."""
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


def _run_branch_file(repo: Path, name: str) -> str | None:
    r = subprocess.run(["git", "show", f"leerie/runs/{RUN_ID}:{name}"],
                       cwd=repo, capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else None


def _resume(leerie, root: Path, repo: Path):
    st2 = leerie.State(root, RUN_ID)
    st2.repo_root = root.parent
    assert st2.load()
    return st2


def _assert_nothing_half_done_shipped(leerie, repo: Path, st2) -> None:
    assert st2.data["completed_waves"] == 1
    assert _merged(repo, "a") and _merged(repo, "b")
    for sid in ("a", "b"):
        text = _run_branch_file(repo, f"{sid}-redrive.txt")
        # Absent (rolled back and not redone) or finished — never a round
        # cut off after its half-done commit.
        assert text is None or text.endswith(DONE), (sid, text)
    assert not st2.data.get("redrive_in_flight")


def test_the_reviewers_case_a_crash_in_a_coverage_session(
        leerie, tmp_path, monkeypatch):
    """Review round 2 M2, the reviewer's driver: `a`'s coverage re-drive
    commits half-done work and the process dies in the session. `a` read
    `complete`, so the resume merged that commit, and its coverage record
    stayed open, never evaluated again."""
    repo, root, st = _setup(leerie, tmp_path, monkeypatch)
    _patch(leerie, monkeypatch, repo, st.run_dir,
           crash_in_session={"a": True})
    with pytest.raises(_Crash):
        _run(leerie, st, None, [])
    st.release_lock()
    st2 = _resume(leerie, root, repo)
    assert st2.data["subtask_status"]["a"] == "complete"
    mark = st2.data["redrive_in_flight"]["a"]
    assert mark["kind"] == "coverage" and mark["status"] == "complete"
    assert mark["pre_sha"] == \
        st2.data["coverage_redrive"]["a"]["rounds"][-1]["pre_sha"]
    _run(leerie, st2, None, [])
    _assert_nothing_half_done_shipped(leerie, repo, st2)
    assert _run_branch_file(repo, "a-redrive.txt") is None
    rec = st2.data["coverage_redrive"]["a"]
    # The round is not done: it is marked, and the record evaluated again.
    assert rec["rounds"][0].get("interrupted") is True
    assert rec["final"] == "covered"


def test_a_crash_in_a_blocked_session_is_rolled_back_and_retried(
        leerie, tmp_path, monkeypatch):
    repo, root, st = _setup(leerie, tmp_path, monkeypatch)
    _patch(leerie, monkeypatch, repo, st.run_dir,
           crash_in_session={"b": True})
    with pytest.raises(_Crash):
        _run(leerie, st, None, [])
    st.release_lock()
    st2 = _resume(leerie, root, repo)
    assert st2.data["redrive_in_flight"]["b"]["kind"] == "blocked"
    assert st2.data["redrive_in_flight"]["b"]["status"] == "blocked"
    _run(leerie, st2, None, [])
    _assert_nothing_half_done_shipped(leerie, repo, st2)
    assert st2.data["subtask_status"]["b"] == "complete"


def test_the_unintegrated_path_refuses_a_marked_sid(leerie, tmp_path,
                                                   monkeypatch):
    """Belt and braces: with the undo skipped, a sid still carrying the
    marker is never a "complete but not yet integrated" stand-in."""
    repo, root, st = _setup(leerie, tmp_path, monkeypatch)
    _patch(leerie, monkeypatch, repo, st.run_dir,
           crash_in_session={"a": True})
    with pytest.raises(_Crash):
        _run(leerie, st, None, [])
    st.release_lock()
    st2 = _resume(leerie, root, repo)

    async def skip(*a, **k):
        return None
    monkeypatch.setattr(leerie, "_undo_interrupted_redrives", skip)
    seen: list[list[str]] = []
    real = leerie._wave_sids_not_integrated

    async def spy(staging, run_id, sids, heads=None):
        seen.append(list(sids))
        return await real(staging, run_id, sids, heads)
    monkeypatch.setattr(leerie, "_wave_sids_not_integrated", spy)
    try:
        _run(leerie, st2, None, [])
    except BaseException:
        pass
    assert seen and all("a" not in s for s in seen)


_POINTS: list[list[int]] = []


def _crash_points(leerie, tmp_path, monkeypatch) -> list[int]:
    if _POINTS:
        return _POINTS[0]
    repo, root, st = _setup(leerie, tmp_path / "probe", monkeypatch)
    _patch(leerie, monkeypatch, repo, st.run_dir)
    marks: list[int] = []
    _run(leerie, st, None, marks)
    assert st.data["completed_waves"] == 1
    assert _run_branch_file(repo, "a-redrive.txt") == HALF + DONE
    st.release_lock()
    _POINTS.append(marks)
    return marks


def test_the_window_holds_saves_inside_both_rounds(leerie, tmp_path,
                                                   monkeypatch):
    marks = _crash_points(leerie, tmp_path, monkeypatch)
    assert 8 <= len(marks) < 30, marks


@pytest.mark.parametrize("crash_at", range(1, 30))
def test_crash_after_each_save_never_ships_half_done_work(
        leerie, tmp_path, monkeypatch, crash_at):
    points = _crash_points(leerie, tmp_path, monkeypatch)
    if crash_at > len(points):
        pytest.skip(f"only {len(points)} save points in this window")
    repo, root, st = _setup(leerie, tmp_path / "run", monkeypatch)
    _patch(leerie, monkeypatch, repo, st.run_dir)
    with pytest.raises(_Crash):
        _run(leerie, st, crash_at, [])
    st.release_lock()
    st2 = _resume(leerie, root, repo)
    _run(leerie, st2, None, [])
    _assert_nothing_half_done_shipped(leerie, repo, st2)
