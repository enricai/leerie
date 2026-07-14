"""Pin Fix-B's diagnostics payoff end-to-end through `settle_subtask`.

Background (bugfix-003 handoff doc, DESIGN §6 *Detecting memory OOM*): a
worker running a build/test command that overshoots its cgroup's
`memory.max` is killed by the kernel mid-turn with a bare `Killed` — no
result event, no checkpoint. `_invoke`'s no-envelope path already names
this (`tests/test_invoke_streaming.py`) by probing the cgroup's
`oom_kill` counter and raising a `WorkerError` that carries the offending
command and the memory cap. `run_implementer` catches that `WorkerError`
and folds its message into `res["summary"]` on a synthesized
`status="incomplete-handoff"` envelope with a checkpoint_path that was
never written.

This file pins the NEXT seam: once that envelope reaches
`settle_subtask`, `validate_result` tags it `empty_handoff` (generic
"checkpoint ... does not exist" text) — but `settle_subtask` must prefer
the worker's named OOM summary over that generic message, on both of its
empty_handoff branches (the has-commits rescue and the no-commits fail
path), so the operator sees the named cause instead of a cryptic
checkpoint/retry-cap error. Conversely, a healthy worker's ordinary
empty_handoff (oom_kill==0 — e.g. a session-limit no-op) must NOT emit
the OOM message: no false positive.

Mirrors `tests/test_run_conformance_phase.py`'s `env`-fixture pattern
(real git repo + `.leerie` run dir + a real `State`) and
`tests/test_invoke_streaming.py`'s PID-exhaustion-detector precedent for
asserting on the named diagnostic's message text.
"""
from __future__ import annotations

import asyncio
import json
import subprocess

import pytest


def _run(cmd, cwd, check=True):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if check:
        assert r.returncode == 0, f"{cmd} failed in {cwd}: {r.stderr}"
    return r


# The named-OOM message _invoke would have produced (see
# test_invoke_streaming.py::test_invoke_names_memory_oom_on_no_envelope) —
# threaded through run_implementer's `except WorkerError` catch into
# `res["summary"]` on the synthesized incomplete-handoff envelope.
_OOM_SUMMARY = (
    "worker produced no schema-valid result: worker cfg-002 was "
    "OOM-killed on `pnpm run build` (memory.max=2.6 GiB) — raise "
    "--worker-memory-max or lower --max-parallel"
)


@pytest.fixture
def env(leerie, tmp_path):
    """A real git repo + `.leerie` run dir + State, with NO committed
    implementer work yet — callers add commits (or not) per scenario to
    control which empty_handoff branch (rescue vs. fail) fires."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _run(["git", "init", "-q", "-b", "main"], cwd=repo)
    _run(["git", "config", "user.email", "t@t"], cwd=repo)
    _run(["git", "config", "user.name", "t"], cwd=repo)
    (repo / "README.md").write_text("# repo\n")
    _run(["git", "add", "-A"], cwd=repo)
    _run(["git", "commit", "-q", "-m", "initial"], cwd=repo)

    run_id = "oom-001-abcdef"
    run_branch = f"leerie/runs/{run_id}"
    _run(["git", "checkout", "-q", "-b", run_branch], cwd=repo)

    sid = "t1"
    subtask_branch = f"leerie/subtasks/{run_id}/{sid}"
    leerie_root = repo / ".leerie"
    run_dir = leerie_root / "runs" / run_id
    (run_dir / "subtasks").mkdir(parents=True)
    (run_dir / "criteria").mkdir()
    (run_dir / "logs").mkdir()
    (run_dir / "worktrees").mkdir()
    worktree = run_dir / "worktrees" / sid
    _run(["git", "worktree", "add", "-q", "-b", subtask_branch,
          str(worktree), run_branch], cwd=repo)

    (run_dir / "criteria" / f"{sid}.md").write_text("# Criteria\n- builds\n")
    subtask = {"id": sid, "files_likely_touched": ["src.py"]}
    (run_dir / "subtasks" / f"{sid}.json").write_text(json.dumps(subtask))

    State = leerie.State
    st = State(leerie_root, run_id)
    st.data = {"task": "x", "answers": {"source_of_truth": "codebase"}}
    st.save()

    caps = dict(leerie.DEFAULT_CAPS)
    # Terminate on the very first failed attempt — no retry loop needed
    # to observe the named message on the terminal `failed` result.
    caps["failed_retries"] = 0
    models = {w: "sonnet" for w in leerie.WORKER_TYPES}
    efforts: dict[str, str | None] = {}

    return {
        "leerie": leerie, "repo": repo, "worktree": worktree,
        "sid": sid, "subtask": subtask, "run_dir": run_dir, "st": st,
        "caps": caps, "models": models, "efforts": efforts,
        "run_branch": run_branch,
    }


def _stub_named_oom_handoff(leerie_mod, sid, run_dir, monkeypatch,
                             summary=_OOM_SUMMARY):
    """Patch run_implementer to return the exact shape `run_implementer`'s
    `except WorkerError` arm synthesizes: status='incomplete-handoff'
    with a checkpoint_path that was never written (empty_handoff once
    validate_result sees it) and the named-OOM text as `summary`."""
    async def _stub(sid_, leerie_dir, caps, st, models, efforts,
                    continuation=False, note=""):
        return {
            "subtask_id": sid_,
            "status": "incomplete-handoff",
            "checkpoint_path": str(run_dir / "checkpoints" / f"{sid_}.md"),
            "summary": summary,
        }
    monkeypatch.setattr(leerie_mod, "run_implementer", _stub)


def _stub_healthy_no_op_handoff(leerie_mod, monkeypatch):
    """A session-limit no-op empty_handoff with NO named cause — mirrors
    the ordinary WorkerError message run_implementer synthesizes when
    _cgroup_stat's oom_kill was 0 (or containment was off), i.e. the
    pre-existing generic path validate_result's `message` covers."""
    async def _stub(sid_, leerie_dir, caps, st, models, efforts,
                    continuation=False, note=""):
        return {
            "subtask_id": sid_,
            "status": "incomplete-handoff",
            "checkpoint_path": "/nonexistent/checkpoint.md",
            "summary": "worker produced no schema-valid result: "
                       "claude -p produced no result event (stderr: (empty))",
        }
    monkeypatch.setattr(leerie_mod, "run_implementer", _stub)


# --- oom_kill > 0: the operator sees the named cause, not the cryptic ------
# checkpoint error, on BOTH empty_handoff branches (no-commits fail path
# and has-commits rescue path).

def test_no_commits_empty_handoff_surfaces_named_oom(env, monkeypatch):
    """No committed work on the subtask branch -> settle_subtask's
    empty_handoff `fail()` path. The terminal `failed` result's summary
    must be the worker's named-OOM diagnostic (command + memory cap),
    not validate_result's generic 'checkpoint_path ... does not exist'
    text, and must point the operator at the remediation flags."""
    c = env["leerie"]
    _stub_named_oom_handoff(c, env["sid"], env["run_dir"], monkeypatch)

    res = asyncio.run(c.settle_subtask(
        env["sid"], env["run_dir"], env["caps"], env["st"],
        env["models"], env["efforts"]))

    assert res["status"] == "failed"
    summary = res["summary"]
    assert "OOM-killed" in summary
    assert "pnpm run build" in summary
    assert "memory.max=2.6 GiB" in summary
    assert "--worker-memory-max" in summary
    assert "--max-parallel" in summary
    # The generic checkpoint text must NOT be what the operator sees —
    # the whole point of Fix-B is replacing it with the named cause.
    assert "does not exist on disk" not in summary


def test_has_commits_empty_handoff_rescue_logs_named_oom(env, monkeypatch, capsys):
    """Committed work IS present -> settle_subtask's rescue branch (keeps
    the diff, settles as `complete`). The rescue log line must still
    prefer the named-OOM summary over the generic checkpoint message —
    same operator-facing naming guarantee applies on this branch too."""
    c = env["leerie"]
    _stub_named_oom_handoff(c, env["sid"], env["run_dir"], monkeypatch)

    # Give the subtask branch a commit ahead of the run branch, so
    # branch_has_commits_ahead is True and the rescue branch fires
    # instead of fail().
    (env["worktree"] / "src.py").write_text("def f():\n    pass\n")
    _run(["git", "add", "-A"], cwd=env["worktree"])
    _run(["git", "commit", "-q", "-m", "implementer: add f()"],
         cwd=env["worktree"])

    res = asyncio.run(c.settle_subtask(
        env["sid"], env["run_dir"], env["caps"], env["st"],
        env["models"], env["efforts"]))

    assert res["status"] == "complete"
    out = capsys.readouterr().out
    assert "OOM-killed" in out
    assert "pnpm run build" in out
    assert "memory.max=2.6 GiB" in out
    assert "--worker-memory-max" in out
    assert "--max-parallel" in out
    assert "does not exist on disk" not in out


# --- oom_kill == 0 (or unavailable): no false positive ---------------------

def test_no_oom_empty_handoff_does_not_emit_oom_message(env, monkeypatch):
    """A session-limit no-op empty_handoff (no named cause from _invoke —
    the oom_kill==0 / containment-off case) must NOT be misreported as an
    OOM. settle_subtask falls back to the ordinary message; no
    'OOM-killed' text is fabricated."""
    c = env["leerie"]
    _stub_healthy_no_op_handoff(c, monkeypatch)

    res = asyncio.run(c.settle_subtask(
        env["sid"], env["run_dir"], env["caps"], env["st"],
        env["models"], env["efforts"]))

    assert res["status"] == "failed"
    assert "OOM-killed" not in res["summary"]
    assert "--worker-memory-max" not in res["summary"]
