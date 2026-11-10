"""A base-tree reader has the verbs a reader reaches for (DESIGN §8): the
satisfied probe and the delivery judge run on `TREE_READER_TOOLS`, always with
the tree reader's PreToolUse hook (`scripts/tree-reader-hook.py`), which
drops a leading `cd` into the worker's own cwd and holds `git grep` to the
working tree, the index or HEAD. The hook is driven as the CLI runs it: the
real script, a real event on stdin, a real repository."""
from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import subprocess
import sys
import types
from pathlib import Path

import pytest

from tests.conftest import REPO_ROOT, fake_run_dir, run_git_out

HOOK = REPO_ROOT / "scripts" / "tree-reader-hook.py"
WORKER_SHELL = REPO_ROOT / "scripts" / "worker-shell.py"

# The verbs bench LB re-run 3 measured (arm RO+), in its order.
MEASURED_VERBS = ("Bash(git grep:*)", "Bash(sed -n:*)", "Bash(awk:*)",
                  "Bash(sort:*)", "Bash(cut:*)", "Bash(git ls-files:*)")


@pytest.fixture()
def repo(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "gitconfig"))
    r = tmp_path / "repo"
    (r / "sub").mkdir(parents=True)
    run_git_out(r, "init", "-q", "-b", "main")
    (r / "f.txt").write_text("foo one\n")
    run_git_out(r, "add", "-A")
    run_git_out(r, "-c", "user.email=t@x", "-c", "user.name=t", "commit",
                "-qm", "one")
    (r / "f.txt").write_text("foo two\n")
    run_git_out(r, "-c", "user.email=t@x", "-c", "user.name=t", "commit",
                "-qam", "two")
    run_git_out(r, "branch", "other", "HEAD~1")
    return r


def _hook(repo: Path, tmp_path: Path, command: str, *, tool: str = "Bash"
          ) -> dict | None:
    config = tmp_path / "config"
    config.mkdir(exist_ok=True)
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(repo),
           "CLAUDE_CONFIG_DIR": str(config)}
    p = subprocess.run(
        [sys.executable, "-I", str(HOOK)],
        input=json.dumps({"tool_name": tool, "cwd": str(repo),
                          "tool_input": {"command": command,
                                         "description": "d"}}),
        capture_output=True, text=True, cwd=repo, env=env, timeout=60)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)["hookSpecificOutput"] if p.stdout else None


# --- the hook ------------------------------------------------------------------


@pytest.mark.parametrize("prefix, rest", [
    ("cd {r} && ", "sed -n 1,2p f.txt"),
    ("cd {r}; ", "git ls-files | head -3"),
    ("cd '{r}' && cd {r} ; ", "awk 'NR==1' f.txt"),
    ("cd . && ", "sort f.txt"),
])
def test_a_cd_into_its_own_cwd_is_dropped(repo, tmp_path, prefix, rest):
    out = _hook(repo, tmp_path, prefix.format(r=repo) + rest)
    assert out == {"hookEventName": "PreToolUse",
                   "updatedInput": {"command": rest, "description": "d"}}


@pytest.mark.parametrize("command", [
    "cd {r}/sub && ls",
    "cd /etc && cat hosts",
    "ls -la; cd {r} && ls",
    "sed -n 1,2p f.txt",
    "awk 'NR==1' f.txt | cut -c1-3",
])
def test_any_other_command_is_left_alone(repo, tmp_path, command):
    assert _hook(repo, tmp_path, command.format(r=repo)) is None


@pytest.mark.parametrize("command, ref", [
    ("git grep -n foo HEAD~1 -- f.txt", "HEAD~1"),
    ("git grep foo other", "other"),
    ("git grep -e foo other:f.txt", "other:f.txt"),
    ("cd {r} && git grep -c foo main~1", "main~1"),
])
def test_git_grep_of_another_commit_is_denied(repo, tmp_path, command, ref):
    out = _hook(repo, tmp_path, command.format(r=repo))
    assert out["permissionDecision"] == "deny"
    assert out["permissionDecisionReason"] == (
        "git grep may search only the working tree or HEAD in this session "
        f"(not {ref})")


@pytest.mark.parametrize("command", [
    "git grep foo", "git grep -n foo HEAD -- f.txt", "git grep foo HEAD:sub",
    "git grep --cached foo", "git grep foo -- other",
])
def test_git_grep_of_the_tree_or_head_is_allowed(repo, tmp_path, command):
    assert _hook(repo, tmp_path, command) is None


def test_a_tool_other_than_bash_is_left_alone(repo, tmp_path):
    assert _hook(repo, tmp_path, f"cd {repo} && ls", tool="Read") is None


def test_each_decision_is_logged_in_the_workers_own_directory(
        repo, tmp_path):
    _hook(repo, tmp_path, f"cd {repo} && ls")
    _hook(repo, tmp_path, "git grep foo HEAD~1")
    log = [json.loads(ln) for ln in (
        tmp_path / "config" / "tree-reader-hook.ndjson").read_text()
        .splitlines()]
    assert log == [{"cmd": f"cd {repo} && ls", "new": "ls", "deny": []},
                   {"cmd": "git grep foo HEAD~1", "new": None,
                    "deny": ["HEAD~1"]}]


# --- the worker shell runs it in leerie's root ----------------------------------


def _ws():
    spec = importlib.util.spec_from_file_location("_ws", WORKER_SHELL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_hook_the_orchestrator_writes_is_leeries(leerie):
    ws = _ws()
    here = str(WORKER_SHELL.parent)
    command = json.loads(leerie._tree_reader_hook_settings())[
        "hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    assert ws._is_leeries_hook([command], here)
    for words in ([command + " x"], [command, "x"],
                  [command.replace(" -I ", " ")],
                  ["python3 -I " + here + "/tree-reader-hook.py"],
                  [f"{sys.executable} -I /elsewhere/tree-reader-hook.py"]):
        assert not ws._is_leeries_hook(words, here), words


# --- the list, and what reaches the worker's argv -------------------------------


def test_the_list_is_the_measured_one(leerie):
    assert leerie.TREE_READER_TOOLS == (
        leerie.SATISFIED_PROBE_TOOLS + "," + ",".join(MEASURED_VERBS))


def _spawn(leerie, monkeypatch, tmp_path, *, allowed_tools, schema_key,
           data=None):
    captured: dict = {}

    async def fake_invoke(cmd, cwd, timeout, sid, leerie_dir, verbosity,
                          stdin_data=None, **_kw):
        captured["cmd"] = list(cmd)
        return {"type": "result", "subtype": "success", "is_error": False,
                "result": "{}", "structured_output": {
                    "satisfied": False, "evidence": "x", "verdicts": []}}

    repo = tmp_path / "user"
    repo.mkdir(exist_ok=True)
    st = types.SimpleNamespace(
        path=fake_run_dir() / "state.json", run_dir=fake_run_dir(),
        repo_root=repo, data={"verbosity": "quiet", **(data or {})},
        run_id="r1", bump_workers=lambda *a, **k: None,
        add_telemetry=lambda *a, **k: None)
    monkeypatch.setattr(leerie, "_invoke", fake_invoke)
    monkeypatch.setattr(leerie, "_capture_call", lambda *a, **k: None)
    monkeypatch.setattr(leerie, "_validate_output", lambda *a, **k: None,
                        raising=False)
    monkeypatch.setattr(
        leerie, "_append_system_prompt_file_supported", lambda: False)
    try:
        asyncio.run(leerie.claude_p(
            "u", "s", schema_key=schema_key,
            cwd="/leerie-state/runs/r1/worktrees/w",
            allowed_tools=allowed_tools, max_turns=20, autonomous=False,
            caps=dict(leerie.DEFAULT_CAPS), st=st, model="sonnet", sid="t"))
    except Exception:
        # The argv is what is under test; the stub's output need not
        # satisfy each worker's schema.
        pass
    argv = captured["cmd"]
    return (argv[argv.index("--allowedTools") + 1].split(","),
            json.loads(argv[argv.index("--settings") + 1]))


def _hook_commands(settings: dict) -> list[str]:
    return [h["command"] for m in settings.get("hooks", {}).get(
        "PreToolUse", []) for h in m["hooks"]]


@pytest.mark.parametrize("worker", ["satisfied_probe", "delivery_judge"])
def test_a_tree_reader_gets_the_verbs_and_the_hook(
        leerie, monkeypatch, tmp_path, worker):
    allowed, settings = _spawn(leerie, monkeypatch, tmp_path,
                               allowed_tools=leerie.TREE_READER_TOOLS,
                               schema_key=worker)
    assert allowed[-len(MEASURED_VERBS):] == list(MEASURED_VERBS)
    assert _hook_commands(settings) == [
        f"{sys.executable} -I {leerie.TREE_READER_HOOK}"]
    assert settings["hooks"]["PreToolUse"][0]["matcher"] == "Bash"


def test_the_hook_is_merged_over_the_forbidden_command_hook(
        leerie, monkeypatch, tmp_path):
    _allowed, settings = _spawn(
        leerie, monkeypatch, tmp_path, allowed_tools=leerie.TREE_READER_TOOLS,
        schema_key="satisfied_probe",
        data={"process_constraints": {"forbidden_commands": ["npm test"]}})
    commands = _hook_commands(settings)
    assert len(commands) == 2
    assert str(leerie.FORBIDDEN_COMMAND_HOOK) in commands[0]
    assert commands[1] == f"{sys.executable} -I {leerie.TREE_READER_HOOK}"


def test_a_worker_on_the_base_list_gets_neither(leerie, monkeypatch,
                                                tmp_path):
    allowed, settings = _spawn(leerie, monkeypatch, tmp_path,
                               allowed_tools=leerie.SATISFIED_PROBE_TOOLS,
                               schema_key="no_work_judge")
    assert not set(MEASURED_VERBS) & set(allowed)
    assert _hook_commands(settings) == []
