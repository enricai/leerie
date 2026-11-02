"""DESIGN §6 *The host never runs a program the checkout chose*, part 4
(review round 28, isolation C1 and C2).

The CLI loads skills, slash commands and agents from every `--add-dir`
directory, and with `CLAUDE_CODE_ADDITIONAL_DIRECTORIES_CLAUDE_MD` set (in
the environment or in a settings `env`) the directory's `CLAUDE.md` as well.
Review round 28's proof (`c_add_dir_skill_runs_host_commands.sh`): a committed
`.claude/skills/<x>/SKILL.md` was invoked in `config --chat`, and its `!` line
and its frontmatter hook ran on the host. Every host-side CLI now carries
`HOST_CLI_ARGS`, denies `HOST_CLI_DENIED_TOOLS` and pins `HOST_CLI_ENV_PIN`.
Measured on CLI 2.1.316, `--setting-sources user` alone stopped every skill,
command and agent loading from `--add-dir`, and a `--settings` env of `0`
stopped `CLAUDE.md` loading even with the variable set in the environment.
That is the live check in the commit message, since CI has no CLI.

Inverted here on the argv and environment each host seam actually builds:
the real `run_rebaser` through `claude_p` (`_invoke` captured), and the real
launcher's `config --chat` with a stub `claude` that logs its argv and
environment.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from tests.conftest import REPO_ROOT

LAUNCHER = REPO_ROOT / "leerie"
PIN = "CLAUDE_CODE_ADDITIONAL_DIRECTORIES_CLAUDE_MD"


def _assert_host_cli(leerie, argv: list[str]) -> None:
    for a in leerie.HOST_CLI_ARGS:
        assert a in argv, (a, argv)
    i = argv.index("--setting-sources")
    assert argv[i + 1] == "user"
    assert argv.count("--setting-sources") == 1
    denied = argv[argv.index("--disallowedTools") + 1].split(",")
    for t in leerie.HOST_CLI_DENIED_TOOLS:
        assert t in denied, (t, denied)
    settings = json.loads(argv[argv.index("--settings") + 1])
    for k, v in leerie.HOST_CLI_ENV_PIN.items():
        assert settings["env"][k] == v


def test_the_rebaser_cli_loads_nothing_from_its_tree(leerie, tmp_path,
                                                     monkeypatch):
    wt = tmp_path / "wt"
    wt.mkdir()
    for a in (["init", "-q", "-b", "main"], ["commit", "-q", "--allow-empty",
                                             "-m", "m"]):
        subprocess.run(["git", "-C", str(wt), "-c", "user.email=t@t", "-c",
                        "user.name=t", *a], check=True)
    monkeypatch.setenv(PIN, "1")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    calls: list = []

    async def _capture(cmd, cwd, timeout, *a, **k):
        calls.append((list(cmd), k.get("env_extra") or {}))
        raise RuntimeError("captured")
    monkeypatch.setattr(leerie, "_invoke", _capture)
    result = leerie.run_rebaser(tmp_path / "state", wt, "r28", wt,
                                "main", "main", "main")
    assert result["status"] == "failed"
    assert calls, result
    argv, env_extra = calls[0]
    _assert_host_cli(leerie, argv)
    assert argv[argv.index("--add-dir") + 1] == str(wt)
    assert env_extra.get(PIN) == "0"


def test_the_extra_args_never_repeat_the_shared_flag(leerie):
    assert leerie._host_cli_extra_args(["claude", "--strict-mcp-config"]) == [
        "--setting-sources", "user", "--disable-slash-commands"]


def test_config_chat_loads_nothing_from_the_checkout(leerie, tmp_path):
    co = tmp_path / "checkout"
    co.mkdir()
    subprocess.run(["git", "init", "-q", str(co)], check=True)
    stub = tmp_path / "cli"
    stub.mkdir()
    log = tmp_path / "chat.log"
    # Builtins only; logs its argv, one per line, and the pinned variable.
    (stub / "claude").write_text(
        "#!/bin/sh\n"
        f'printf "env=%s\\n" "${{{PIN}-<unset>}}" >> "{log}"\n'
        f'for a in "$@"; do printf "arg=%s\\n" "$a" >> "{log}"; done\n'
        "exit 0\n")
    (stub / "claude").chmod(0o755)
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    env = {"PATH": f"{stub}:/usr/local/bin:/usr/bin:/bin", "HOME": str(home),
           PIN: "1"}
    r = subprocess.run(["/usr/bin/timeout", "120", str(LAUNCHER), "config",
                        "--chat"], cwd=co, env=env, capture_output=True,
                       text=True, timeout=150)
    assert r.returncode == 0, r.stderr[-800:]
    lines = log.read_text().splitlines()
    assert lines[0] == "env=<unset>"
    argv = [ln.removeprefix("arg=") for ln in lines[1:]]
    _assert_host_cli(leerie, argv)
    assert "--strict-mcp-config" in argv
    # `--add-dir` takes every word up to the next option: `--` ends it, so
    # the first message is a message, not a directory.
    assert argv[-2] == "--"
    assert argv[argv.index("--add-dir") + 1] == str(co.resolve())
    assert argv.index("--add-dir") + 2 == len(argv) - 2


@pytest.mark.parametrize("missing", ["Agent", "Task", "Skill"])
def test_a_shared_deny_list_that_stops_denying_is_caught(leerie, monkeypatch,
                                                         missing):
    monkeypatch.setattr(leerie, "DISALLOWED_TOOLS", ",".join(
        t for t in leerie.DISALLOWED_TOOLS.split(",") if t != missing))
    with pytest.raises(RuntimeError, match=missing):
        leerie._host_cli_extra_args([])
