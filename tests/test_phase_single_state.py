"""`--phase judge|heal` reaches its workers: one `State` per run per process.

The defect (on `main` since 60fca170, 2026-06-08, which made `main()`'s
run-id the `--run-id` the launcher passes): `main()` opened `State(run_id)`,
then the `--phase` branch opened a second `State` on the same run. The run
directory's flock belongs to an open file description, not to the process,
so the second was refused like another orchestrator's and the process exited
75 (`EXIT_LOCKED`) before any worker. Every phase test stood in a lock that
did nothing, so none saw it.

Here the real `main()` is driven, with the real lock, to the first worker
spawn (`claude_p`, stubbed) of each phase; the worker must receive the one
`State` `main()` opened.
"""
from __future__ import annotations

import json
import sys

import pytest

from tests.test_main_exception_arms import repo  # noqa: F401  (fixture)


class _FirstSpawn(BaseException):
    """Out of every `except Exception` between `main()` and the spawn —
    `_replay_and_judge_sample` swallows a replay's `Exception`."""


def _seed(leerie, repo, run_id: str):
    run_dir = repo / ".leerie" / "runs" / run_id
    (run_dir / "subtasks").mkdir(parents=True)
    (run_dir / "state.json").write_text(json.dumps(
        {"task": "a trivial task", "run_layout": leerie.RUN_LAYOUT_VERSION}))
    (run_dir / "calls.ndjson").write_text(json.dumps(
        {"call_id": "c1", "call_type": "planner", "system_prompt": "s",
         "user_prompt": "u", "response_content": "{}"}) + "\n")
    return run_dir


def _arm(leerie, monkeypatch):
    """The confinement entry is another file's subject; the State is not."""
    monkeypatch.setattr(leerie, "_require_landlock", lambda: None)
    monkeypatch.setattr(leerie, "_require_heldout_sandbox", lambda d: None)
    real, opened = leerie.State, []

    def counting(root, rid, **kw):
        st = real(root, rid, **kw)
        opened.append(st)
        return st
    monkeypatch.setattr(leerie, "State", counting)
    spawns: list = []

    async def claude_p(*a, **kw):
        spawns.append(kw)
        raise _FirstSpawn()
    monkeypatch.setattr(leerie, "claude_p", claude_p)
    return opened, spawns


def _main(leerie, monkeypatch, argv: list[str]):
    monkeypatch.setattr(sys, "argv", ["leerie", *argv])
    try:
        leerie.main()
    except SystemExit as e:
        return ("exit", e.code)
    except _FirstSpawn:
        return ("spawned", None)
    return ("returned", None)


def test_phase_judge_reaches_its_first_worker(leerie, monkeypatch, repo):
    _seed(leerie, repo, "phase-judge")
    opened, spawns = _arm(leerie, monkeypatch)
    got = _main(leerie, monkeypatch,
                ["--phase", "judge", "--run-id", "phase-judge"])
    assert got != ("exit", leerie.EXIT_LOCKED)
    assert got == ("spawned", None)
    assert [st.run_id for st in opened] == ["phase-judge"]
    assert spawns[0]["schema_key"] == "judge"
    assert spawns[0]["st"] is opened[0]


def test_phase_heal_reaches_its_first_worker(leerie, monkeypatch, repo):
    """With a failing verdict already indexed, heal skips the judge phase and
    its baseline reaches a worker carrying the State `main()` holds."""
    run_dir = _seed(leerie, repo, "phase-heal")
    (run_dir / leerie.JUDGE_DIR_DEFAULT).mkdir()
    (run_dir / leerie.JUDGE_DIR_DEFAULT / "INDEX.json").write_text(json.dumps(
        [{"call_id": "c1", "call_type": "planner", "passed": False}]))
    opened, spawns = _arm(leerie, monkeypatch)
    got = _main(leerie, monkeypatch,
                ["--phase", "heal", "--run-id", "phase-heal"])
    assert got != ("exit", leerie.EXIT_LOCKED)
    assert got == ("spawned", None)
    assert [st.run_id for st in opened] == ["phase-heal"]
    assert spawns[-1]["st"] is opened[0]


def test_phase_on_a_run_id_naming_no_run_mints_nothing(leerie, monkeypatch,
                                                       repo):
    """The run is resolved before `State`, so a typo is refused without a
    directory left behind for `leerie list` to show."""
    _seed(leerie, repo, "phase-real")
    opened, spawns = _arm(leerie, monkeypatch)
    got = _main(leerie, monkeypatch,
                ["--phase", "judge", "--run-id", "phase-typo"])
    assert got == ("exit", 1)
    assert opened == [] and spawns == []
    assert not (repo / ".leerie" / "runs" / "phase-typo").exists()
