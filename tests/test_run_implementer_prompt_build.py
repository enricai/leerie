"""Behavioral coverage of `_run_implementer`'s prompt-assembly body.

`tests/test_worker_timeout_handoff.py` documents that this function's
exception-handling arms are covered via source-text pins rather than a
real invocation, because standing up a real git worktree was judged out
of scope for the fast unit suite. That reasoning applies to
`new-worktree.sh` itself, not to the function as a whole: `_run_script`
and `claude_p` are both ordinary module-level coroutines `_run_implementer`
calls by name, so monkeypatching both drives the real prompt-assembly body
(the OWNED_REGION / PROVISION_RECIPE / upstream-artifacts / convention-docs
/ continuation sections, `CAN_ASK_USER`, and the successful return path)
with no live `claude` binary and no real `git worktree add`.

Mirrors `tests/test_oom_naming.py`'s `env` fixture (real git repo + real
`.leerie` run dir + real `State`), reused here because `_run_implementer`
reads `st.data`, `st.repo_root`, and the on-disk subtask spec/plan the
same way regardless of which branch is under test.
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


@pytest.fixture
def env(leerie, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _run(["git", "init", "-q", "-b", "main"], cwd=repo)
    _run(["git", "config", "user.email", "t@t"], cwd=repo)
    _run(["git", "config", "user.name", "t"], cwd=repo)
    (repo / "README.md").write_text("# repo\n")
    _run(["git", "add", "-A"], cwd=repo)
    _run(["git", "commit", "-q", "-m", "initial"], cwd=repo)

    run_id = "runimpl-001"
    sid = "feat-005-r1"
    leerie_root = repo / ".leerie"
    run_dir = leerie_root / "runs" / run_id
    (run_dir / "subtasks").mkdir(parents=True)
    (run_dir / "checkpoints").mkdir()
    (run_dir / "artifacts").mkdir()

    spec = {
        "id": sid, "title": "region 1", "success_criteria_seed": "c",
        "files_likely_touched": ["src/big.ts"],
        "owned_region": {"file": "src/big.ts", "start": 1, "end": 700,
                         "symbols": ["foo", "bar"]},
    }
    (run_dir / "subtasks" / f"{sid}.json").write_text(json.dumps(spec))
    (run_dir / "plan.json").write_text(json.dumps(
        {"task": "x", "waves": [], "subtasks": {
            "producer-1": {"id": "producer-1", "depends_on": [], "requires": []},
            sid: {"id": sid, "depends_on": ["producer-1"], "requires": []},
        }}))
    (run_dir / "artifacts" / "producer-1.json").write_text(json.dumps(
        {"subtask_id": "producer-1",
         "artifacts": [{"name": "spec", "kind": "markdown",
                        "content": "produced content here"}]}))

    State = leerie.State
    st = State(leerie_root, run_id)
    st.data = {"task": "x", "answers": {"source_of_truth": "codebase"}}
    st.save()

    caps = dict(leerie.DEFAULT_CAPS)
    models = {w: "sonnet" for w in leerie.WORKER_TYPES}
    efforts: dict[str, str | None] = {w: None for w in leerie.WORKER_TYPES}

    return {
        "leerie": leerie, "repo": repo, "run_dir": run_dir, "st": st,
        "sid": sid, "caps": caps, "models": models, "efforts": efforts,
    }


def _stub_run_script_ok(leerie_mod, monkeypatch, worktree_path):
    async def _fake(name, *args):
        assert name == "new-worktree.sh"
        return subprocess.CompletedProcess(
            args=["bash"], returncode=0, stdout=f"{worktree_path}\n", stderr="")
    monkeypatch.setattr(leerie_mod, "_run_script", _fake)


def _stub_claude_p_capture(leerie_mod, monkeypatch, calls):
    async def _fake(user_prompt, system_prompt, *, schema_key, cwd,
                    allowed_tools, max_turns, autonomous, caps, st, model,
                    sid, add_dirs=None, effort=None, _suppress_capture=False):
        calls.append({"user_prompt": user_prompt, "schema_key": schema_key,
                     "cwd": cwd, "model": model, "sid": sid})
        return {"subtask_id": sid, "status": "complete", "branch": "b"}
    monkeypatch.setattr(leerie_mod, "claude_p", _fake)


def test_successful_run_returns_claude_p_result(env, monkeypatch):
    leerie_mod = env["leerie"]
    worktree = env["repo"] / "wt"
    _stub_run_script_ok(leerie_mod, monkeypatch, worktree)
    calls: list = []
    _stub_claude_p_capture(leerie_mod, monkeypatch, calls)

    res = asyncio.run(leerie_mod._run_implementer(
        env["sid"], env["run_dir"], env["caps"], env["st"],
        env["models"], env["efforts"]))

    assert res == {"subtask_id": env["sid"], "status": "complete", "branch": "b"}
    assert len(calls) == 1
    assert calls[0]["cwd"] == str(worktree)
    assert calls[0]["schema_key"] == "implementer"
    assert calls[0]["model"] == "sonnet"


def test_prompt_includes_owned_region_section(env, monkeypatch):
    leerie_mod = env["leerie"]
    _stub_run_script_ok(leerie_mod, monkeypatch, env["repo"] / "wt")
    calls: list = []
    _stub_claude_p_capture(leerie_mod, monkeypatch, calls)

    asyncio.run(leerie_mod._run_implementer(
        env["sid"], env["run_dir"], env["caps"], env["st"],
        env["models"], env["efforts"]))

    prompt = calls[0]["user_prompt"]
    assert "OWNED_REGION:" in prompt
    assert "lines 1-700 of src/big.ts" in prompt


def test_prompt_includes_upstream_artifacts_section(env, monkeypatch):
    leerie_mod = env["leerie"]
    _stub_run_script_ok(leerie_mod, monkeypatch, env["repo"] / "wt")
    calls: list = []
    _stub_claude_p_capture(leerie_mod, monkeypatch, calls)

    asyncio.run(leerie_mod._run_implementer(
        env["sid"], env["run_dir"], env["caps"], env["st"],
        env["models"], env["efforts"]))

    prompt = calls[0]["user_prompt"]
    assert "## Artifacts from upstream subtasks" in prompt
    assert "produced content here" in prompt


def test_prompt_includes_provision_recipe_section(env, monkeypatch):
    leerie_mod = env["leerie"]
    _stub_run_script_ok(leerie_mod, monkeypatch, env["repo"] / "wt")
    calls: list = []
    _stub_claude_p_capture(leerie_mod, monkeypatch, calls)
    env["st"].data["provision"] = {"recipe": [
        {"kind": "install", "command": ["pnpm", "install", "--offline"]}]}

    asyncio.run(leerie_mod._run_implementer(
        env["sid"], env["run_dir"], env["caps"], env["st"],
        env["models"], env["efforts"]))

    prompt = calls[0]["user_prompt"]
    assert "PROVISION_RECIPE:" in prompt
    assert "pnpm install --offline" in prompt


def test_prompt_omits_provision_recipe_section_when_empty(env, monkeypatch):
    leerie_mod = env["leerie"]
    _stub_run_script_ok(leerie_mod, monkeypatch, env["repo"] / "wt")
    calls: list = []
    _stub_claude_p_capture(leerie_mod, monkeypatch, calls)

    asyncio.run(leerie_mod._run_implementer(
        env["sid"], env["run_dir"], env["caps"], env["st"],
        env["models"], env["efforts"]))

    assert "PROVISION_RECIPE:" not in calls[0]["user_prompt"]


def test_can_ask_user_reflects_clarify_flag(env, monkeypatch):
    leerie_mod = env["leerie"]
    _stub_run_script_ok(leerie_mod, monkeypatch, env["repo"] / "wt")
    calls: list = []
    _stub_claude_p_capture(leerie_mod, monkeypatch, calls)
    env["st"].data["clarify"] = True

    asyncio.run(leerie_mod._run_implementer(
        env["sid"], env["run_dir"], env["caps"], env["st"],
        env["models"], env["efforts"]))

    assert "CAN_ASK_USER: true" in calls[0]["user_prompt"]


def test_continuation_with_checkpoint_present(env, monkeypatch):
    leerie_mod = env["leerie"]
    _stub_run_script_ok(leerie_mod, monkeypatch, env["repo"] / "wt")
    calls: list = []
    _stub_claude_p_capture(leerie_mod, monkeypatch, calls)
    ckpt = env["run_dir"] / "checkpoints" / f"{env['sid']}.md"
    ckpt.write_text("# Checkpoint\n")

    asyncio.run(leerie_mod._run_implementer(
        env["sid"], env["run_dir"], env["caps"], env["st"],
        env["models"], env["efforts"], continuation=True))

    prompt = calls[0]["user_prompt"]
    assert "This is a CONTINUATION. Read the checkpoint at" in prompt
    assert str(ckpt) in prompt


def test_continuation_without_checkpoint(env, monkeypatch):
    leerie_mod = env["leerie"]
    _stub_run_script_ok(leerie_mod, monkeypatch, env["repo"] / "wt")
    calls: list = []
    _stub_claude_p_capture(leerie_mod, monkeypatch, calls)

    asyncio.run(leerie_mod._run_implementer(
        env["sid"], env["run_dir"], env["caps"], env["st"],
        env["models"], env["efforts"], continuation=True))

    prompt = calls[0]["user_prompt"]
    assert "There is no checkpoint file" in prompt
    assert "Read the checkpoint at" not in prompt


def test_note_is_appended(env, monkeypatch):
    leerie_mod = env["leerie"]
    _stub_run_script_ok(leerie_mod, monkeypatch, env["repo"] / "wt")
    calls: list = []
    _stub_claude_p_capture(leerie_mod, monkeypatch, calls)

    asyncio.run(leerie_mod._run_implementer(
        env["sid"], env["run_dir"], env["caps"], env["st"],
        env["models"], env["efforts"], note="watch the budget"))

    assert "NOTE FROM ORCHESTRATOR: watch the budget" in calls[0]["user_prompt"]


def test_worker_error_becomes_incomplete_handoff(env, monkeypatch):
    """A `claude_p` that never returns schema-valid output raises
    `WorkerError`; `_run_implementer` folds it into a synthesized
    incomplete-handoff envelope naming an (unwritten) checkpoint path."""
    leerie_mod = env["leerie"]
    _stub_run_script_ok(leerie_mod, monkeypatch, env["repo"] / "wt")

    async def _raising(*a, **kw):
        raise leerie_mod.WorkerError("hit --max-turns mid-task")
    monkeypatch.setattr(leerie_mod, "claude_p", _raising)

    res = asyncio.run(leerie_mod._run_implementer(
        env["sid"], env["run_dir"], env["caps"], env["st"],
        env["models"], env["efforts"]))

    assert res["status"] == "incomplete-handoff"
    assert res["checkpoint_path"] == str(
        env["run_dir"] / "checkpoints" / f"{env['sid']}.md")
    assert "hit --max-turns mid-task" in res["summary"]


def test_timeout_expired_becomes_incomplete_handoff(env, monkeypatch):
    """A `claude_p` that hits the per-worker wall-clock cap raises
    `subprocess.TimeoutExpired`; `_run_implementer` names the actual
    per-worker ceiling (not the global default) in the summary."""
    leerie_mod = env["leerie"]
    _stub_run_script_ok(leerie_mod, monkeypatch, env["repo"] / "wt")

    async def _raising(*a, **kw):
        raise subprocess.TimeoutExpired(cmd=["claude"], timeout=5400)
    monkeypatch.setattr(leerie_mod, "claude_p", _raising)

    res = asyncio.run(leerie_mod._run_implementer(
        env["sid"], env["run_dir"], env["caps"], env["st"],
        env["models"], env["efforts"]))

    expected_timeout = leerie_mod.resolve_worker_timeout(
        "implementer", env["caps"])
    assert res["status"] == "incomplete-handoff"
    assert f"worker timed out after {expected_timeout}s" in res["summary"]


def test_worktree_setup_error_on_nonzero_returncode(env, monkeypatch):
    """The one exception-raising branch this file didn't already exercise
    via a stub — `_run_script` returning nonzero — mirrors
    test_worktree_failure_not_fatal.py's production shape but drives it
    through the real function body rather than a stub of it."""
    leerie_mod = env["leerie"]

    async def _fake(name, *args):
        return subprocess.CompletedProcess(
            args=["bash"], returncode=1, stdout="", stderr="fatal: exists")
    monkeypatch.setattr(leerie_mod, "_run_script", _fake)

    with pytest.raises(leerie_mod.WorktreeSetupError, match="fatal: exists"):
        asyncio.run(leerie_mod._run_implementer(
            env["sid"], env["run_dir"], env["caps"], env["st"],
            env["models"], env["efforts"]))
