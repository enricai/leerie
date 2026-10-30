"""A kill between an integrator's merge commit and the integration judge's
recorded verdict must not let `resume` count the merge integrated unjudged
(DESIGN §6 *A committed resolution is not a judged one*).

Driven for real: a git repo, the real `setup-run.sh` / `new-worktree.sh` /
`integrate.sh`, the real `phase_execute` and `integrate_wave` and the real
integration gate. Only the implementer (`_settle_subtask`), the workers
(`claude_p`) and the conflicted-area test run are scripted. Wave `[a, b]`:
both edit `app.py`, `a` merges clean, `b` conflicts. The integrator commits a
resolution that drops `b`'s helper — under its own message, as a real
integrator may — and the judge finds the drop, which a repair round mends.

The process is killed inside the integrator before and after its commit,
and right after each save from there until the verdict is recorded and the
write-ahead marker cleared; then resumed. Every time the merge must be judged, its
verdict recorded, the dropped helper repaired, the merge carry its
`leerie: integrate b` subject, and the wave advance."""
from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

import pytest

from tests.conftest import REPO_ROOT

RUN_ID = "20261028-unjudged-merge"

BASE = "def compute():\n    return 1\n"
A_SIDE = BASE + "\ndef integrated_helper():\n    return 'a'\n"
B_SIDE = BASE + "\ndef incoming_helper():\n    return 'b'\n"
DROPPED = A_SIDE  # the first resolution keeps a's side only
REPAIRED = A_SIDE + "\ndef incoming_helper():\n    return 'b'\n"

DEFECT = {"kind": "dropped_change", "location": "app.py",
          "concrete_scenario": "incoming_helper was dropped by the merge",
          "why_broken": "callers of incoming_helper fail"}


class _Crash(BaseException):
    """A process death: not an `Exception`, so no handler on the way can
    swallow it."""


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True,
                          capture_output=True, text=True).stdout.strip()


def _setup(leerie, tmp_path: Path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "app.py").write_text(BASE)
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
    (st.run_dir / "subtasks").mkdir(parents=True, exist_ok=True)
    for sid in ("a", "b"):
        (st.run_dir / "subtasks" / f"{sid}.json").write_text(json.dumps(
            {"id": sid, "intent": f"add {sid}'s helper",
             "criteria": [f"{sid}'s helper exists"]}))
    # `repo` below is the run's own repository (DESIGN §6 *The run's own
    # repository*): the run's branches, the scripts' working directory.
    # The user's repository is `root.parent`.
    leerie._ensure_run_repo(st)
    return st.run_repo, root, st


def _staging(st) -> Path:
    return st.run_dir / "worktrees" / "staging"


class _World:
    """The scripted workers and their record. `kill_in_integrator` dies
    inside the first integrator call, right before or right after its
    commit."""

    def __init__(self, leerie, st, kill_in_integrator: str | None = None):
        self.leerie = leerie
        self.st = st
        # "before" / "after" its commit, or None
        self.kill_in_integrator = kill_in_integrator
        self.calls: list[str] = []
        self.merge_committed = False

    async def settle(self, sid, leerie_dir, caps, st, models, efforts,
                     redrive_note=None):
        out = subprocess.run(
            ["bash", str(REPO_ROOT / "scripts" / "new-worktree.sh"), sid,
             RUN_ID], cwd=st.run_repo, capture_output=True, text=True,
            check=True)
        wt = Path(out.stdout.strip().splitlines()[-1])
        (wt / "app.py").write_text(A_SIDE if sid == "a" else B_SIDE)
        _git(wt, "commit", "-q", "-am", f"{sid}: helper")
        st.data["subtask_status"][sid] = "complete"
        st.save()
        return {"subtask_id": sid, "status": "complete", "summary": "ok"}

    async def claude_p(self, **kw):
        staging = _staging(self.st)
        self.calls.append(kw["sid"])
        if kw["schema_key"] == "integration_judge":
            text = _git(staging, "show", "HEAD:app.py") + "\n"
            return {"merge_reviewed": True,
                    "defects": [] if "incoming_helper" in text
                    else [DEFECT], "rationale": "r"}
        assert kw["schema_key"] == "integrator", kw["schema_key"]
        if kw["sid"].startswith("integrator-repair-"):
            (staging / "app.py").write_text(REPAIRED)
            _git(staging, "commit", "-q", "-am", "restore incoming_helper")
            return {"incoming_subtask": "b", "status": "resolved",
                    "superseded": []}
        # The first resolution, committed under the integrator's own
        # message: the subject is code's to set afterwards.
        (staging / "app.py").write_text(DROPPED)
        if self.kill_in_integrator == "before":
            raise _Crash("killed inside the integrator, mid-resolution")
        _git(staging, "add", "app.py")
        _git(staging, "commit", "-q", "-m", "resolve the app.py conflict")
        self.merge_committed = True
        if self.kill_in_integrator == "after":
            raise _Crash("killed inside the integrator, after its commit")
        return {"status": "resolved", "resolution_summary": "kept a",
                "confidence": {"resolution": 9.0, "basis": "clean"}}


def _patch(leerie, monkeypatch, world: _World):
    monkeypatch.setattr(leerie, "_warn_scoped_degraded_once",
                        lambda *a, **k: None)
    monkeypatch.setattr(leerie, "_disk_free_ratio", lambda *a: 1.0)

    async def _noop(*a, **k):
        return None

    async def _no_tests(*a, **k):
        return {"test_failure": [], "stale_by_other_side": [], "scope": [],
                "skipped": "no test command"}

    async def _uncovered(*a, **k):
        return {"measured": True, "share": 0.0, "uncovered": {}}

    async def _no_gaps(*a, **k):
        return []
    monkeypatch.setattr(leerie, "_assert_repo_unchanged", _noop)
    monkeypatch.setattr(leerie, "_integration_test_failures", _no_tests)
    monkeypatch.setattr(leerie, "_measure_subtask_coverage", _uncovered)
    monkeypatch.setattr(leerie, "_settle_typed_gaps", _no_gaps)
    monkeypatch.setattr(leerie, "_settle_subtask", world.settle)
    monkeypatch.setattr(leerie, "claude_p", world.claude_p)


def _decided(data: dict) -> bool:
    """The judge's verdict is recorded and the write-ahead marker gone."""
    return (bool(((data.get("integration_gate") or {}).get("b") or {})
                 .get("merge_commit_sha"))
            and "b" not in (data.get("integration_unjudged") or {}))


def _run(leerie, st, world: _World, crash_at: int | None,
         marks: list[int]) -> None:
    """One invocation of `phase_execute`. Saves are numbered from the
    first one after the integrator's commit, through the one by which the
    judge's verdict is recorded and the marker cleared — the repair round's
    saves included; with `crash_at`, the process dies right after that save
    reaches disk."""
    real_save = type(st).save
    seen = {"n": 0, "done": False}

    def save(self):
        real_save(self)
        if seen["done"] or not world.merge_committed:
            return
        seen["n"] += 1
        marks.append(seen["n"])
        if _decided(self.data):
            seen["done"] = True
        if crash_at is not None and seen["n"] == crash_at:
            raise _Crash(f"killed after save {crash_at}")

    st.save = save.__get__(st)
    try:
        asyncio.run(leerie.phase_execute(
            st.run_dir, st, dict(leerie.DEFAULT_CAPS),
            {"integrator": "sonnet", "integration_judge": "sonnet"},
            {"integrator": "low", "integration_judge": "medium"}))
    finally:
        st.save = real_save.__get__(st)


def _assert_judged_and_repaired(leerie, st, repo: Path, resumed: _World,
                                judged_before: bool = False) -> None:
    assert st.data["completed_waves"] == 1
    rec = st.data["integration_gate"]["b"]
    assert rec["accepted"] is True, rec
    assert rec["outcome"] == "repaired", rec
    if judged_before:
        # The kill came after the repaired merge's clean verdict was saved:
        # nothing is left to judge.
        assert "integration_judge-b" not in resumed.calls, resumed.calls
    else:
        assert "integration_judge-b" in resumed.calls, resumed.calls
    staging = _staging(st)
    assert (staging / "app.py").read_text() == REPAIRED
    run_branch = f"leerie/runs/{RUN_ID}"
    subjects = _git(repo, "log", "--first-parent", "--format=%s",
                    run_branch).splitlines()
    assert "leerie: integrate b" in subjects, subjects
    assert "leerie: integrate a" in subjects, subjects
    assert "integration_unjudged" not in st.data, st.data.get(
        "integration_unjudged")
    # a's clean merge was never judged on the first pass and carries no
    # marker, so no resume judges it either.
    assert "integration_judge-a" not in resumed.calls, resumed.calls
    assert "a" not in (st.data.get("integration_gate") or {})


_POINTS: list[list[int]] = []


def _crash_points(leerie, tmp_path, monkeypatch) -> list[int]:
    if _POINTS:
        return _POINTS[0]
    repo, _root, st = _setup(leerie, tmp_path / "probe", monkeypatch)
    world = _World(leerie, st)
    _patch(leerie, monkeypatch, world)
    marks: list[int] = []
    _run(leerie, st, world, None, marks)
    # The uninterrupted run is the reference: judged, repaired, advanced.
    _assert_judged_and_repaired(leerie, st, repo, world)
    assert world.calls == ["integrator-b", "integration_judge-b",
                           "integrator-repair-b", "integration_judge-b"]
    st.release_lock()
    _POINTS.append(marks)
    return marks


def test_the_window_holds_saves(leerie, tmp_path, monkeypatch):
    marks = _crash_points(leerie, tmp_path, monkeypatch)
    # At least the verdict, the repair round's and the marker's clearing.
    assert len(marks) >= 4, marks
    assert len(marks) < 13, marks


def _resume(leerie, root: Path, repo: Path, monkeypatch):
    st2 = leerie.State(root, RUN_ID)
    st2.repo_root = root.parent
    assert st2.load()
    assert st2.data["completed_waves"] == 0
    judged_before = bool(((st2.data.get("integration_gate") or {})
                          .get("b") or {}).get("accepted"))
    world2 = _World(leerie, st2)
    _patch(leerie, monkeypatch, world2)
    _run(leerie, st2, world2, None, [])
    return st2, world2, judged_before


def test_killed_inside_the_integrator_after_its_commit(
        leerie, tmp_path, monkeypatch):
    repo, root, st = _setup(leerie, tmp_path / "run", monkeypatch)
    world = _World(leerie, st, kill_in_integrator="after")
    _patch(leerie, monkeypatch, world)
    with pytest.raises(_Crash):
        _run(leerie, st, world, None, [])
    assert world.merge_committed
    st.release_lock()
    st2, world2, _ = _resume(leerie, root, repo, monkeypatch)
    _assert_judged_and_repaired(leerie, st2, repo, world2)
    # The committed resolution is judged, not redone by a fresh integrator.
    assert world2.calls == ["integration_judge-b", "integrator-repair-b",
                            "integration_judge-b"], world2.calls
    st2.release_lock()


def test_killed_inside_the_integrator_before_its_commit(
        leerie, tmp_path, monkeypatch):
    """The marker without a merge on the run branch: an integration that
    never committed is merged again in its own turn, a fresh integrator
    meets the conflict, and the gate judges what it commits."""
    repo, root, st = _setup(leerie, tmp_path / "run", monkeypatch)
    world = _World(leerie, st, kill_in_integrator="before")
    _patch(leerie, monkeypatch, world)
    with pytest.raises(_Crash):
        _run(leerie, st, world, None, [])
    assert st.data["integration_unjudged"] == {
        "b": _git(_staging(st), "rev-parse", "HEAD")}
    st.release_lock()
    st2, world2, _ = _resume(leerie, root, repo, monkeypatch)
    _assert_judged_and_repaired(leerie, st2, repo, world2)
    assert world2.calls == ["integrator-b", "integration_judge-b",
                            "integrator-repair-b", "integration_judge-b"]
    st2.release_lock()


def test_marker_round_trip(leerie, tmp_path):
    st = leerie.State(tmp_path / ".leerie", "run-marker")
    st.data = {"task": "t"}
    leerie._mark_integration_unjudged(st, "x", "abc")
    leerie._mark_integration_unjudged(st, "y", "def")
    on_disk = json.loads((st.run_dir / "state.json").read_text())
    assert on_disk["integration_unjudged"] == {"x": "abc", "y": "def"}
    leerie._clear_integration_unjudged(st, "x")
    assert st.data["integration_unjudged"] == {"y": "def"}
    leerie._clear_integration_unjudged(st, "x")  # absent: no-op
    leerie._clear_integration_unjudged(st, "y")
    assert "integration_unjudged" not in st.data
    on_disk = json.loads((st.run_dir / "state.json").read_text())
    assert "integration_unjudged" not in on_disk
    st.release_lock()


@pytest.mark.parametrize("crash_at", range(1, 13))
def test_killed_after_each_save_then_resume_judges_the_merge(
        leerie, tmp_path, monkeypatch, crash_at):
    points = _crash_points(leerie, tmp_path, monkeypatch)
    if crash_at > len(points):
        pytest.skip(f"only {len(points)} save points in this window")
    repo, root, st = _setup(leerie, tmp_path / "run", monkeypatch)
    world = _World(leerie, st)
    _patch(leerie, monkeypatch, world)
    with pytest.raises(_Crash):
        _run(leerie, st, world, crash_at, [])
    st.release_lock()
    st2, world2, judged = _resume(leerie, root, repo, monkeypatch)
    _assert_judged_and_repaired(leerie, st2, repo, world2, judged)
    st2.release_lock()
