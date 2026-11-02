"""A push chosen after finalize gets its PR written by `pr_writer`.

DESIGN §6 *One push choice per run, on every runtime*: a run that is pushed
has its PR written by `pr_writer` (the fallback only when the worker fails),
whenever its push choice was made. `leerie resume <id> --push` of a finished
`--no-push` run changes the choice after `phase_finalize`, which ran no
`pr_writer`; the resume then answered "nothing to resume" and the host pushed
with the deterministic fallback body (`run.json.pr_title` null, 0 `pr_writer`
calls — review r25 L1).

These drive the real `_run_phases` resume of a finished run, stubbing only
`claude_p`, and assert the worker ran and its title reached `run.json`.
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest


MODELS: dict = {"pr_writer": "sonnet"}
EFFORTS: dict = {"pr_writer": None}


def _args(*, no_push: bool) -> SimpleNamespace:
    return SimpleNamespace(resume=True, task=None, no_push=no_push,
                           pr_template=None, no_verify=False)


@pytest.fixture
def finished_run(leerie, tmp_path):
    """A run finalized under `--no-push`: its start recorded `no_push` true,
    finalize ran no `pr_writer`, so `run.json` has no `pr_title`."""
    root = tmp_path / ".leerie"
    run_id = "finished-nopush-run"
    (root / "runs" / run_id).mkdir(parents=True)
    st = leerie.State(root, run_id)
    st.data = {
        "task": "fix the slug bug", "worker_count": 3,
        "no_push": True,
        "waves": [["fix-001"]], "completed_waves": 1,
        "finished_at": "2026-10-30T00:00:00Z",
        "current_phase": "phase 6: finalize",
    }
    st.save()
    leerie._write_run_json(st.run_dir, finished_at=st.data["finished_at"],
                           no_push=True)
    yield st
    st.release_lock()


@pytest.fixture
def pr_writer_calls(leerie, monkeypatch):
    calls: list[dict] = []

    async def _fake_claude_p(**kwargs):
        calls.append(kwargs)
        return {"title": "Fix the slug bug", "body": "## Summary\nFixed.",
                "used_template": None}
    monkeypatch.setattr(leerie, "claude_p", _fake_claude_p)
    return calls


def _resume(leerie, st, args) -> None:
    asyncio.run(leerie._run_phases(
        args, dict(leerie.DEFAULT_CAPS), st.leerie_root, st, "codebase", "normal",
        MODELS, EFFORTS))


def _run_json(st) -> dict:
    return json.loads((st.run_dir / "run.json").read_text())


def test_resume_with_push_of_a_finished_no_push_run_writes_its_pr(
        leerie, finished_run, pr_writer_calls):
    # The state still records the start's `no_push: true`; the resume
    # continues with the command line's override, which `main()` has already
    # resolved into `args.no_push` — the two disagree on purpose.
    _resume(leerie, finished_run, _args(no_push=False))

    assert [c["schema_key"] for c in pr_writer_calls] == ["pr_writer"]
    assert _run_json(finished_run)["pr_title"] == "Fix the slug bug"


def test_resume_that_continues_without_a_push_writes_no_pr(
        leerie, finished_run, pr_writer_calls):
    _resume(leerie, finished_run, _args(no_push=True))

    assert pr_writer_calls == []
    assert _run_json(finished_run).get("pr_title") is None


def test_a_written_pr_is_not_rewritten(leerie, finished_run, pr_writer_calls):
    leerie._write_run_json(finished_run.run_dir, pr_title="Already written",
                           pr_body="body")

    _resume(leerie, finished_run, _args(no_push=False))

    assert pr_writer_calls == []
    assert _run_json(finished_run)["pr_title"] == "Already written"
