"""N8: a worker never kills a process by name (DESIGN §9 *A worker never
kills a process by name*).

Every worker of a run shares one process table, so `pkill -f vitest` stops
every sibling's vitest too: of 48 pattern kills on record, 15 killed another
worker's (or the orchestrator's) install, test run or watcher. `claude_p`
gives every worker whose tools reach a shell deny rules, the PreToolUse hook
and a notice pointing at `TaskStop` (no longer denied — DESIGN §2).
"""
from __future__ import annotations

import asyncio
import base64
import importlib.util
import io
import json
import pathlib
import subprocess
import sys
import types

import pytest
from tests.conftest import fake_run_dir

# The 48 recorded pattern kills, verbatim from the run corpus (bench N8,
# `pkill/instances.tsv`'s kill_cmd column), in record order.
RECORDED = [
    'pkill -f "pytest tests/ -q" 2>/dev/null',
    'pkill -f "until \\[ -x"',
    'pkill -f "bugfix-002-f1/node_modules/.bin/../jest"',
    'pkill -f jest',
    'pkill -f "until ! pgrep"',
    'pkill -f "until grep"',
    'pkill -f "until \\[ -x node_modules/.bin/vitest"',
    'pkill -f vitest',
    'pkill -f "while pgrep"',
    'pkill -f "until \\["',
    'pkill -f "until \\[ -x"',
    'pkill -f "pnpm install"',
    'pkill -f "until \\[ -s"',
    'pkill -f "until \\[ -d node_modules"',
    'pkill -f "until grep"',
    'pkill -f "pgrep"',
    'pkill -f check-translations',
    'pkill -f "pgrep"',
    'pkill -f "pnpm install"',
    'pkill -f "find / -type d"',
    'pkill -f "until ! pgrep"',
    'pkill -f "npm exec prisma format"',
    'pkill -f vitest',
    'pkill -f "until \\[ -s"',
    'pkill -f "pnpm"',
    'pkill -f "cat > src/tests/website/blocks.test.ts"',
    'pkill -f "until \\[ -x node_modules"',
    'pkill -f "until \\[ -x"',
    'pkill -f "until \\[ -d infra/node_modules"',
    'pkill -f "until \\["',
    'pkill -f "tasks/bl2vqlnue"',
    'pkill -f "pnpm install"',
    "pkill -f 'pnpm install'",
    'pkill -f "until \\["',
    'pkill -f pnpm',
    'pkill -f "pgrep"',
    'pkill -f "while pgrep"',
    'pkill -f "until grep"',
    'pkill -f prisma',
    'pkill -f "pnpm install"',
    'pkill -f vitest',
    'pkill -f "while pgrep"',
    'pkill -f "tsc --noEmit"',
    'pkill -f vitest',
    'pkill -f "pgrep"',
    'pkill -f "until ! pgrep"',
    'pkill -f "until ! pgrep"',
    'pkill -f "pnpm add"',
]

# Spellings the corpus did not contain but a worker denied the plain form
# plausibly reaches for, each wrapped the way the hook must still see.
WRAPPED = [
    "sh -c 'pkill -f vitest'",
    'bash -lc "pkill -f x"',
    "timeout 5 pkill -f x",
    "/usr/bin/pkill x",
    "killall node",
    "killall5 -9",
    "pgrep -f vitest | xargs kill",
    "pgrep -f vitest | xargs -r kill -9",
    "xargs -I{} kill {} < pids",
    "pgrep -f x | xargs -I % sh -c 'kill %'",
    "ps aux | grep vitest | awk '{print $2}' | xargs kill",
    "kill $(pgrep -f jest)",
    "kill -9 `pgrep node`",
    "kill $(pidof node)",
    "for p in $(pgrep -f vite); do kill $p; done",
    "pgrep -f x | while read p; do kill -9 $p; done",
    "eval 'pkill -f x'",
    "python3 -c \"import os; os.system('pkill -f x')\"",
    "node -e \"require('child_process').execSync('pkill -f x')\"",
    "fuser -k 3000/tcp",
    "env pkill x",
    "nohup pkill x &",
    "sudo -u leerie pkill x",
    "npx pkill x",
    "cd /tmp && pkill -f foo; echo done",
    # bench N8 case 2, arm B, sample 1 (denied there by the bench's rules)
    'pkill -f jest; pkill -f "pnpm run test"; NODE_ENV=test timeout 450 '
    "node_modules/.bin/jest --config=jest.config.js --runTestsByPath src/x",
]

# Must stay allowed: the word appears, but no kill by name runs.
LEGIT = [
    "git commit -m 'stop using pkill -f in tests'",
    "grep -rn pkill .",
    "rg 'xargs kill' docs",
    "git log --grep=pkill",
    "sed -i 's/pkill/kill/' f.sh",
    "man pkill",
    "cat killall.txt",
    "npm run kill-port",
    "node scripts/killall.js",
    "echo kill",
    'echo "kill $(pgrep x)"',
    "kill 12345",
    "kill -9 4321",
    "kill %1",
    "kill $!",
    "kill -0 $(pgrep -f x)",
    "kill -s 0 $(pgrep -f x)",
    "kill -l",
    "pgrep -a node | grep -F vitest",
    "ps aux | grep vitest",
]


@pytest.mark.parametrize("cmd", RECORDED)
def test_every_recorded_kill_is_denied(leerie, cmd):
    assert leerie._process_kill_hit(cmd)


def test_the_recorded_corpus_is_the_48(leerie):
    assert len(RECORDED) == 48


@pytest.mark.parametrize("cmd", WRAPPED)
def test_wrapped_kill_forms_are_denied(leerie, cmd):
    assert leerie._process_kill_hit(cmd)


@pytest.mark.parametrize("cmd", LEGIT)
def test_legitimate_commands_are_not_kills(leerie, cmd):
    assert leerie._process_kill_hit(cmd) is None


def test_the_hit_names_the_form(leerie):
    """Value, not truthiness: the deny reason the worker reads names what was
    refused."""
    assert leerie._process_kill_hit("sh -c 'pkill -f x'") == "pkill"
    assert leerie._process_kill_hit("pgrep x | xargs kill") == "xargs kill"
    assert leerie._process_kill_hit("kill $(pgrep x)") == \
        "kill fed by a pid finder"
    assert leerie._process_kill_hit("fuser -k 80/tcp") == "fuser -k"


def test_forbidden_matcher_is_unchanged_by_keep_xargs(leerie):
    """The D5 matcher still unwraps `xargs` (its default), so a forbidden
    command behind xargs keeps matching."""
    assert leerie._forbidden_command_hit("echo x | xargs pnpm test",
                                         ["pnpm run test"]) == "pnpm run test"
    assert leerie._forbidden_argvs("xargs kill") == [["kill"]]
    assert leerie._forbidden_argvs("xargs kill", keep_xargs=True) == [
        ["xargs", "kill"]]


# === the hook script's import-free prefilter =================================

def _hook_module(leerie):
    spec = importlib.util.spec_from_file_location(
        "leerie_hook_under_test", leerie.FORBIDDEN_COMMAND_HOOK)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("cmd", RECORDED + WRAPPED)
def test_every_denied_form_passes_the_prefilter(leerie, cmd):
    """The hook allows a kills-only call WITHOUT importing leerie when the
    text holds none of `_KILL_SUBSTRINGS`; every form the matcher denies must
    therefore reach the matcher (a derivation guard over both lists)."""
    hook = _hook_module(leerie)
    assert hook._needs_matcher({"forbidden": [], "kills": True}, cmd)


def test_the_prefilter_skips_ordinary_commands_but_not_forbidden_ones(leerie):
    hook = _hook_module(leerie)
    assert not hook._needs_matcher({"forbidden": [], "kills": True}, "ls -la")
    # A forbidden list always goes to the matcher: its spellings are open.
    assert hook._needs_matcher({"forbidden": ["pnpm test"], "kills": True},
                               "ls -la")
    assert hook._needs_matcher(["pnpm test"], "ls")
    assert not hook._needs_matcher({"forbidden": [], "kills": False},
                                   "pkill x")


def _b64(obj) -> str:
    return base64.b64encode(json.dumps(obj).encode()).decode()


def _event(cmd: str) -> str:
    return json.dumps({"tool_name": "Bash", "tool_input": {"command": cmd}})


def test_the_real_hook_script_denies_a_wrapped_kill(leerie):
    for cmd, denied in (("sh -c 'pkill -f vitest'", True),
                        ("pgrep -f x | xargs kill -9", True),
                        ("ls -la", False),
                        ("git commit -m 'no more pkill'", False)):
        p = subprocess.run(
            [sys.executable, "-I", str(leerie.FORBIDDEN_COMMAND_HOOK),
             _b64({"forbidden": [], "kills": True})],
            input=_event(cmd), capture_output=True, text=True, timeout=60)
        assert p.returncode == 0, p.stderr
        assert ('"permissionDecision": "deny"' in p.stdout) is denied, cmd
        if denied:
            reason = json.loads(p.stdout)["hookSpecificOutput"][
                "permissionDecisionReason"]
            assert "TaskStop" in reason


def test_hook_main_kills_flag_gates_the_kill_deny(leerie):
    out = io.StringIO()
    leerie._forbidden_command_hook_main(
        ["h", _b64({"forbidden": [], "kills": False})],
        io.StringIO(_event("pkill -f x")), out)
    assert out.getvalue() == ""
    out = io.StringIO()
    leerie._forbidden_command_hook_main(
        ["h", _b64({"forbidden": [], "kills": True})],
        io.StringIO(_event("pkill -f x")), out)
    assert json.loads(out.getvalue())["hookSpecificOutput"][
        "permissionDecision"] == "deny"


# === claude_p wiring (the real claude_p, only _invoke stubbed) ===============

def _spawn(leerie, monkeypatch, tmp_path, *, allowed_tools, schema_key):
    captured: dict = {}

    async def fake_invoke(cmd, cwd, timeout, sid, leerie_dir, verbosity,
                          stdin_data=None, **_kw):
        captured["cmd"] = list(cmd)
        captured["stdin"] = stdin_data
        return {"type": "result", "subtype": "success", "is_error": False,
                "result": "{}", "structured_output": {"categories": []}}

    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    st = types.SimpleNamespace(
        path=fake_run_dir() / "state.json",
        run_dir=fake_run_dir(),
        repo_root=repo, data={"verbosity": "quiet"},
        run_id="r1", bump_workers=lambda *a, **k: None,
        add_telemetry=lambda *a, **k: None)
    monkeypatch.setattr(leerie, "_invoke", fake_invoke)
    monkeypatch.setattr(leerie, "_capture_call", lambda *a, **k: None)
    monkeypatch.setattr(
        leerie, "_append_system_prompt_file_supported", lambda: False)
    asyncio.run(leerie.claude_p(
        "u", "s", schema_key=schema_key,
        cwd="/leerie-state/runs/r1/worktrees/w",
        allowed_tools=allowed_tools, max_turns=40,
        autonomous=allowed_tools == leerie.ACT_TOOLS,
        caps=dict(leerie.DEFAULT_CAPS), st=st, model="sonnet", sid="t"))
    return captured["cmd"], captured["stdin"]


def _kill_lines(prompt):
    return [ln for ln in prompt.splitlines()
            if ln.startswith("NOTE FROM ORCHESTRATOR:")
            and "killing processes by name" in ln]


def test_an_acting_worker_gets_rules_notice_and_hook_together(
        leerie, monkeypatch, tmp_path):
    argv, prompt = _spawn(leerie, monkeypatch, tmp_path,
                          allowed_tools=leerie.ACT_TOOLS,
                          schema_key="implementer")
    deny = argv[argv.index("--disallowedTools") + 1].split(",")
    for rule in ("Bash(pkill)", "Bash(pkill *)", "Bash(killall *)",
                 "Bash(xargs kill)", "Bash(xargs kill *)"):
        assert rule in deny
    assert "TaskStop" not in deny
    assert "TaskStop" in argv[argv.index("--allowedTools") + 1].split(",")
    settings = json.loads(argv[argv.index("--settings") + 1])
    hook = settings["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    assert json.loads(base64.b64decode(hook.split()[-1])) == {
        "forbidden": [], "kills": True}
    (line,) = _kill_lines(prompt)
    # Behavioural read of the notice: names what is denied, why, and the
    # sanctioned alternative.
    assert "`pkill`" in line and "`xargs`" in line
    assert "same process table" in line
    assert "TaskStop tool" in line
    assert "`$!`" in line


def test_a_shell_less_worker_gets_none_of_it(leerie, monkeypatch, tmp_path):
    argv, prompt = _spawn(leerie, monkeypatch, tmp_path,
                          allowed_tools=leerie.INSPECT_TOOLS,
                          schema_key="classifier")
    deny = argv[argv.index("--disallowedTools") + 1]
    assert "Bash(pkill" not in deny
    assert "--settings" not in argv
    assert _kill_lines(prompt) == []
