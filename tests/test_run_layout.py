"""A run directory is continued only by a leerie of its layout (DESIGN §6).

A fresh run records `run_layout`; `resume` and `--phase` refuse a run
directory whose layout differs (absent = made before the field) before
anything writes in it; `leerie list` shows such a run with work left as
`other-leerie`, and a bare `resume` does not pick it.

The old-layout fixture is shaped like a run directory main's code made after
it cut its run branch: `run.json` has `branch`, `state.json` has neither
`run_layout` nor `run_repo`, and `logs/` holds a transcript with no
`transcripts.ndjson` registry (review round 21, L1). This replaces
tests/test_run_repo.py's pins of `_refuse_pre_run_repo_resume`, which keyed
on `run_repo`-absent-and-`branch`-present and refused only after the run
directory had been loaded into the orchestrator's exit arms.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.conftest import fake_claude_on_path
from tests.test_resume_planning_reentry import _args, _caps


@pytest.fixture()
def repo(leerie, tmp_path, monkeypatch):
    r = tmp_path / "repo"
    r.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=r, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-q", "--allow-empty", "-m", "init"],
                   cwd=r, check=True)
    monkeypatch.chdir(r)
    monkeypatch.delenv("LEERIE_STATE_DIR", raising=False)
    fake_claude_on_path(tmp_path, monkeypatch)
    monkeypatch.setattr(leerie, "_CURRENT_RUN_ID", None, raising=False)
    return r


def _make_run(repo: Path, run_id: str, started: str, **state) -> Path:
    d = repo / ".leerie" / "runs" / run_id
    (d / "logs").mkdir(parents=True)
    (d / "state.json").write_text(json.dumps({
        "task": f"task of {run_id}", "started_at": started,
        "categories": ["feature"], "current_phase": "phase 4-5: implementing",
        "waves": [["feat-001"]], "completed_waves": 0,
        "subtask_status": {"feat-001": "in_progress"},
        "leerie_version": "0.40.0", **state}))
    (d / "run.json").write_text(json.dumps({
        "run_id": run_id, "branch": f"leerie/runs/{run_id}",
        "started_at": started}))
    (d / "logs" / "feat-001.log").write_text(json.dumps(
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "t1", "name": "Bash",
             "input": {"command": "pnpm test"}}]}}) + "\n")
    return d


def _snapshot(d: Path) -> dict[str, str]:
    """Every path under `d` with a digest of its content: what the refusal
    must leave exactly as it found it."""
    return {str(p.relative_to(d)): (hashlib.sha256(p.read_bytes()).hexdigest()
                                    if p.is_file() else "dir")
            for p in sorted(d.rglob("*"))}


def _main(leerie, monkeypatch, argv: list[str]) -> tuple[object, dict]:
    """The real `main()` with `_orchestrate` recording what it was handed.
    Returns (exit code or None, what `_orchestrate` saw)."""
    seen: dict = {}

    async def orchestrate(args, caps, leerie_dir, st, *a, **k):
        seen["run_id"] = st.run_id
    monkeypatch.setattr(leerie, "_orchestrate", orchestrate)
    monkeypatch.setattr(sys, "argv", ["leerie", *argv])
    try:
        leerie.main()
    except SystemExit as e:
        return e.code, seen
    return None, seen


# ------------------------------------------------------------- resume

def test_resume_of_an_old_layout_is_refused_and_leaves_it_untouched(
        leerie, repo, tmp_path, monkeypatch, capsys):
    d = _make_run(repo, "old-run", "2026-01-01T00:00:00+00:00")
    before = _snapshot(d)
    launcher_file = tmp_path / ".runid-launcher"
    monkeypatch.setenv("LEERIE_RUN_ID_FILE", str(launcher_file))

    code, seen = _main(leerie, monkeypatch, ["resume", "old-run"])

    assert code == 1
    assert seen == {}, "the run was driven"
    err = capsys.readouterr().err
    assert "run old-run was made by an older leerie (recorded as 0.40.0)" in err
    assert "run layout none recorded" in err
    assert f"reads only layout {leerie.RUN_LAYOUT_VERSION}" in err
    assert ("Finish it with the leerie that made it, or start the task again "
            "as a fresh run") in err
    # Untouched: no `finished_at` stamp, no clean-up, no new directories, no
    # orchestrator.log — and the launcher was not told it ran this run.
    assert _snapshot(d) == before
    assert not launcher_file.exists()


def test_resume_of_a_newer_layout_is_refused(leerie, repo, monkeypatch,
                                             capsys):
    _make_run(repo, "new-run", "2026-01-01T00:00:00+00:00",
              run_layout=leerie.RUN_LAYOUT_VERSION + 1)
    code, seen = _main(leerie, monkeypatch, ["resume", "new-run"])
    assert code == 1 and seen == {}
    err = capsys.readouterr().err
    assert "made by a newer leerie" in err
    assert f"run layout {leerie.RUN_LAYOUT_VERSION + 1}," in err


def test_resume_of_this_layout_is_driven(leerie, repo, monkeypatch):
    _make_run(repo, "cur-run", "2026-01-01T00:00:00+00:00",
              run_layout=leerie.RUN_LAYOUT_VERSION)
    code, seen = _main(leerie, monkeypatch, ["resume", "cur-run"])
    assert code is None
    assert seen == {"run_id": "cur-run"}


def test_bare_resume_picks_this_layouts_run_over_a_newer_old_one(
        leerie, repo, monkeypatch):
    """The old-layout run started later, so by recency alone it would win."""
    _make_run(repo, "cur-run", "2026-01-01T00:00:00+00:00",
              run_layout=leerie.RUN_LAYOUT_VERSION)
    _make_run(repo, "old-run", "2026-02-01T00:00:00+00:00")
    code, seen = _main(leerie, monkeypatch, ["resume"])
    assert code is None
    assert seen == {"run_id": "cur-run"}


def test_phase_on_an_old_layout_is_refused_before_its_workers(
        leerie, repo, monkeypatch, capsys):
    d = _make_run(repo, "old-run", "2026-01-01T00:00:00+00:00")
    # `--phase` takes the run's lock twice in one process (see
    # tests/test_main_exception_arms.py::TestPhaseFlagShortCircuit).
    monkeypatch.setattr(leerie.State, "_acquire_lock", lambda self: None)
    judged: list = []

    async def judge(*a, **k):
        judged.append(1)
    monkeypatch.setattr(leerie, "phase_judge", judge)
    before = _snapshot(d)
    code, _seen = _main(leerie, monkeypatch,
                        ["--phase", "judge", "--run-id", "old-run"])
    assert code == 1
    assert judged == []
    assert "so it cannot `--phase judge` it" in capsys.readouterr().err
    assert _snapshot(d) == before


# ------------------------------------------------------- the record itself

class _ReachedGate(Exception):
    pass


def _drive_to_containment(leerie, monkeypatch, st, args) -> None:
    def gate(*_a, **_k):
        raise _ReachedGate()
    monkeypatch.setattr(leerie, "_enforce_and_record_cgroup_containment",
                        gate)
    with pytest.raises(_ReachedGate):
        asyncio.run(leerie._run_phases(args, _caps(leerie), st.leerie_root,
                                       st, "both", "quiet", {}, {}))


def test_a_fresh_run_records_this_layout(leerie, repo, monkeypatch):
    st = leerie.State(repo / ".leerie", "fresh-run", repo_root=repo)
    _drive_to_containment(leerie, monkeypatch, st,
                          _args(resume=False, task="a trivial task"))
    on_disk = json.loads((st.run_dir / "state.json").read_text())
    assert on_disk["run_layout"] == leerie.RUN_LAYOUT_VERSION


def test_a_resume_never_rewrites_the_layout(leerie, repo, monkeypatch):
    """`_run_phases` itself does not check (main() does, earlier); what it
    must not do is overwrite the record, or a refused directory resumed once
    by any other path would pass for this layout from then on."""
    d = _make_run(repo, "odd-run", "2026-01-01T00:00:00+00:00",
                  run_layout=leerie.RUN_LAYOUT_VERSION + 7)
    st = leerie.State(repo / ".leerie", "odd-run", repo_root=repo)
    _drive_to_containment(leerie, monkeypatch, st, _args(resume=True))
    on_disk = json.loads((d / "state.json").read_text())
    assert on_disk["run_layout"] == leerie.RUN_LAYOUT_VERSION + 7
    assert on_disk["leerie_versions"], "the resume arm did not run"


# ------------------------------------------------------------------ list

def test_list_shows_an_old_run_with_work_left_as_other_leerie(
        leerie, repo, capsys):
    root = repo / ".leerie"
    _make_run(repo, "old-live", "2026-01-01T00:00:00+00:00")
    _make_run(repo, "cur-live", "2026-01-02T00:00:00+00:00",
              run_layout=leerie.RUN_LAYOUT_VERSION)
    old_done = _make_run(repo, "old-done", "2026-01-03T00:00:00+00:00")
    (old_done / "run.json").write_text(json.dumps({
        "run_id": "old-done", "branch": "leerie/runs/old-done",
        "finished_at": "2026-01-04T00:00:00+00:00"}))
    st = json.loads((old_done / "state.json").read_text())
    st["completed_waves"] = 1
    (old_done / "state.json").write_text(json.dumps(st))

    leerie._list_runs(root)
    rows = {line.split()[0]: line.split()[2]
            for line in capsys.readouterr().out.splitlines()[2:]}
    assert rows == {"old-live": "other-leerie", "cur-live": "in-progress",
                    "old-done": "done"}

    leerie._list_runs(root, status_filter="other-leerie")
    out = capsys.readouterr().out
    assert "old-live" in out and "cur-live" not in out


def test_layout_overlay_leaves_orphans_and_finished_runs_alone(leerie):
    w = leerie._with_run_layout
    assert w("in-progress", {"task": "t"}) == "other-leerie"
    assert w("paused", {"task": "t"}) == "other-leerie"
    assert w("incomplete", {"task": "t"}) == "other-leerie"
    assert w("in-progress", {"task": "t", "run_layout": 1}) == "other-leerie"
    assert w("in-progress", {"task": "t",
                             "run_layout": leerie.RUN_LAYOUT_VERSION}) \
        == "in-progress"
    assert w("done", {"task": "t"}) == "done"
    assert w("seed-failed", {"_orphan": True}) == "seed-failed"
    assert w("in-progress", None) == "in-progress"
    assert "other-leerie" in leerie.RUN_STATUSES
    assert "other-leerie" not in leerie._AUTO_RESUMABLE_STATUSES
