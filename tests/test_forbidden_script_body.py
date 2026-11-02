"""A forbidden package script forbids what it runs (DESIGN §9 *A forbidden
package script forbids what it runs*).

Round-4 review M3: the extractor returns the repo's test command for a
whole-suite forbid, which on every JS repo is `<pm> run test`, and
`_process_constraints` persisted it as written. So on a repo whose `test`
script is `vitest run`, the forbid held only for the script call:
`vitest run`, `npx vitest run`, `pnpm exec vitest run --coverage`,
`pnpm run test:ci` and CI's `pnpm install && pnpm vitest run --coverage`
all passed `_blt_shell_argv`, and the worker hook (the same matcher) let
them through too. The reviewer's `forbid_script_body_probe.py` is kept
below with its assertions inverted; the real-bash rows run each spelling
through the real package managers against an argv-printing `vitest`.
"""
from __future__ import annotations

import base64
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

TASK = ("Fix the export. Don't run the full test suite unnecessarily, it "
        "takes 40 minutes.")
QUOTE = "Don't run the full test suite unnecessarily"


def _repo(tmp_path: Path, scripts: dict) -> Path:
    (tmp_path / "package.json").write_text(json.dumps(
        {"name": "x", "version": "1.0.0", "private": True,
         "scripts": scripts}))
    (tmp_path / "src").mkdir(exist_ok=True)
    (tmp_path / "pnpm-lock.yaml").write_text("")
    return tmp_path


def _pc(leerie, repo, command="pnpm run test", scope="whole_suite",
        prescribed=None):
    res = {"process_constraints": {"forbidden_commands": [
        {"command": command, "scope": scope, "evidence_quote": QUOTE}]}}
    return leerie._process_constraints(res, prescribed, [TASK], repo)


def _refused(leerie, cmd, pc) -> bool:
    forb = leerie._forbidden_specs({"process_constraints": pc})
    try:
        leerie._blt_shell_argv(cmd, forb)
    except leerie.ForbiddenByTask:
        return True
    return False


BARNACLE = {"test": "vitest run", "test:ci": "vitest run --coverage",
            "lint": "eslint .", "typecheck": "tsc --noEmit"}


# --- the reviewer's driver, inverted -----------------------------------------

@pytest.mark.parametrize("argv", [
    ["pnpm", "run", "test"], ["pnpm", "test"], ["npm", "test"],
    ["vitest", "run"], ["pnpm", "vitest", "run"], ["npx", "vitest", "run"],
    ["pnpm", "exec", "vitest", "run", "--coverage"],
    ["pnpm", "run", "test:ci"],
    ["bash", "-c",
     "pnpm install --frozen-lockfile && pnpm vitest run --coverage"],
    ["pnpm", "vitest", "run", "--coverage"],
    ["vitest"],
])
def test_reviewer_spellings_are_refused(leerie, tmp_path, argv):
    pc = _pc(leerie, _repo(tmp_path, BARNACLE))
    assert _refused(leerie, argv, pc), argv


@pytest.mark.parametrize("cmd", [
    "vitest run src/a.test.ts", "npx vitest run src/a.test.ts",
    "pnpm vitest run --coverage src/a.test.ts", "pnpm test src/a.test.ts",
    "pnpm run test:ci src/a.test.ts", "pnpm run lint", "eslint .",
    "pnpm run typecheck", "tsc --noEmit",
])
def test_scoped_runs_and_other_scripts_still_pass(leerie, tmp_path, cmd):
    pc = _pc(leerie, _repo(tmp_path, BARNACLE))
    assert not _refused(leerie, cmd, pc), cmd


def test_derived_entries_are_recorded_with_their_source(leerie, tmp_path):
    pc = _pc(leerie, _repo(tmp_path, BARNACLE))
    assert pc["forbidden_commands"] == ["pnpm run test"]
    assert [e["command"] for e in pc["entries"]] == [
        "pnpm run test", "vitest run", "pnpm run test:ci"]
    for e in pc["entries"][1:]:
        assert e["derived_from"] == "pnpm run test"
        assert e["scope"] == "whole_suite"
        assert e["evidence_quote"] == QUOTE
        assert e["test_roots"] == pc["entries"][0]["test_roots"]
    # Display names only the task's own forbid; matchers read all three.
    data = {"process_constraints": pc}
    assert leerie._forbidden_commands(data) == ["pnpm run test"]
    assert len(leerie._forbidden_specs(data)) == 3


def test_a_body_forbids_only_its_runner(leerie, tmp_path):
    pc = _pc(leerie, _repo(tmp_path, {
        "test": "tsc --noEmit && NODE_OPTIONS=--max-old-space-size=4096 "
                "vitest run"}))
    derived = [e["command"] for e in pc["entries"] if e.get("derived_from")]
    assert derived == ["vitest run"]
    assert not _refused(leerie, "tsc --noEmit", pc)
    assert _refused(leerie, "vitest run", pc)


def test_scripts_calling_scripts_resolve(leerie, tmp_path):
    pc = _pc(leerie, _repo(tmp_path, {
        "test": "pnpm run unit", "unit": "jest --ci",
        "ci": "pnpm lint && pnpm test", "lint": "eslint ."}))
    cmds = [e["command"] for e in pc["entries"]]
    assert "jest --ci" in cmds
    assert "pnpm run unit" in cmds and "pnpm run ci" in cmds
    assert "pnpm run lint" not in cmds
    assert _refused(leerie, "npx jest", pc)
    assert not _refused(leerie, "npx jest src/a.test.ts", pc)


def test_a_script_cycle_derives_nothing_and_ends(leerie, tmp_path):
    pc = _pc(leerie, _repo(tmp_path, {"test": "npm run a", "a": "npm run test"}))
    assert [e["command"] for e in pc["entries"]] == ["pnpm run test"]


def test_a_forbidden_runner_forbids_the_scripts_that_run_it(leerie, tmp_path):
    pc = _pc(leerie, _repo(tmp_path, BARNACLE), command="vitest run")
    cmds = [e["command"] for e in pc["entries"]]
    assert cmds == ["vitest run", "npm run test", "npm run test:ci"]
    assert _refused(leerie, "pnpm test", pc)
    assert _refused(leerie, "yarn run test:ci", pc)


def test_no_repo_or_no_scripts_derives_nothing(leerie, tmp_path):
    res = {"process_constraints": {"forbidden_commands": [
        {"command": "pnpm run test", "scope": "whole_suite",
         "evidence_quote": QUOTE}]}}
    assert len(leerie._process_constraints(res, None, [TASK])["entries"]) == 1
    assert len(leerie._process_constraints(
        res, None, [TASK], tmp_path)["entries"]) == 1


def test_a_prescribed_derived_command_is_dropped(leerie, tmp_path,
                                                 monkeypatch):
    monkeypatch.setattr(leerie, "_prescribed_commands",
                        lambda _p: {"vitest run"})
    pc = _pc(leerie, _repo(tmp_path, BARNACLE), prescribed={"x": 1})
    cmds = [e["command"] for e in pc["entries"]]
    assert "vitest run" not in cmds
    assert {"command": "vitest run", "reason": "prescribed",
            "evidence_quote": QUOTE} in pc["dropped"]


def test_scope_any_carries_through_and_names_the_scripts(leerie, tmp_path):
    pc = _pc(leerie, _repo(tmp_path, BARNACLE), command="pnpm test",
             scope="any")
    by = {e["command"]: e for e in pc["entries"]}
    assert by["vitest run"]["scope"] == "any"
    assert "test_roots" not in by["vitest run"]
    assert _refused(leerie, "npx vitest run src/a.test.ts", pc)
    names = leerie._forbidden_script_names(
        leerie._forbidden_specs({"process_constraints": pc}))
    assert names == ["test", "test:ci"]


# --- the worker hook ---------------------------------------------------------

HOOK = Path(__file__).resolve().parent.parent / "scripts" \
    / "forbidden-command-hook.py"


def _hook(leerie, pc, command: str) -> str:
    settings = json.loads(leerie._forbidden_command_hook_settings(
        leerie._forbidden_specs({"process_constraints": pc})))
    argv = shlex.split(settings["hooks"]["PreToolUse"][0]["hooks"][0]
                       ["command"])
    r = subprocess.run([sys.executable, "-I", str(HOOK), argv[-1]],
                       input=json.dumps({"tool_name": "Bash",
                                         "tool_input": {"command": command}}),
                       capture_output=True, text=True, timeout=120)
    return r.stdout


@pytest.mark.parametrize("cmd, denied", [
    ("npx vitest run", True),
    ("pnpm vitest run --coverage", True),
    ("pnpm run test:ci", True),
    ("npx vitest run src/a.test.ts", False),
    ("pnpm run lint", False),
])
def test_the_hook_process_refuses_the_body(leerie, tmp_path, cmd, denied):
    pc = _pc(leerie, _repo(tmp_path, BARNACLE))
    out = _hook(leerie, pc, cmd)
    assert ('"permissionDecision": "deny"' in out) is denied, (cmd, out)


def test_the_hook_payload_carries_the_derived_entries(leerie, tmp_path):
    pc = _pc(leerie, _repo(tmp_path, BARNACLE))
    settings = json.loads(leerie._forbidden_command_hook_settings(
        leerie._forbidden_specs({"process_constraints": pc})))
    payload = json.loads(base64.b64decode(shlex.split(
        settings["hooks"]["PreToolUse"][0]["hooks"][0]["command"])[-1]))
    assert [e["command"] for e in payload["forbidden"]] == [
        "pnpm run test", "vitest run", "pnpm run test:ci"]


# --- real bash, real package managers, an argv-printing vitest --------------

STUB = ("#!{py}\nimport json, os, sys\n"
        "print('STUB' + json.dumps(sys.argv[1:]))\n")
REAL_BASH = [
    ("pnpm", "pnpm run test"), ("pnpm", "pnpm test"),
    ("pnpm", "pnpm run test:ci"), ("pnpm", "pnpm vitest run"),
    ("pnpm", "pnpm exec vitest run --coverage"),
    ("pnpm", "pnpm vitest run --coverage"),
    ("pnpm", "pnpm test src/a.test.ts"),
    ("pnpm", "pnpm vitest run src/a.test.ts"),
    ("npm", "npm test"), ("npm", "npm run test:ci"),
    ("npm", "npm test -- src/a.test.ts"),
    ("npx", "npx vitest run"), ("npx", "npx vitest run src/a.test.ts"),
    ("yarn", "yarn test"), ("yarn", "yarn run test:ci"),
]


@pytest.mark.parametrize("tool, cmd", REAL_BASH)
def test_real_package_managers(leerie, tmp_path, tool, cmd):
    """Every spelling that reached `vitest` with no file operand is refused;
    every one that reached it with a file is allowed."""
    if shutil.which(tool) is None:
        pytest.skip(f"{tool} is not installed here")
    repo = _repo(tmp_path, BARNACLE)
    (repo / "pnpm-lock.yaml").unlink()
    bin_ = repo / "node_modules" / ".bin"
    bin_.mkdir(parents=True)
    (bin_ / "vitest").write_text(STUB.format(py=sys.executable))
    (bin_ / "vitest").chmod(0o755)
    env = dict(os.environ, CI="1", npm_config_update_notifier="false",
               COREPACK_ENABLE_DOWNLOAD_PROMPT="0",
               npm_config_offline="true")
    r = subprocess.run(["bash", "-c", cmd], cwd=repo, env=env, text=True,
                       capture_output=True, timeout=120,
                       stdin=subprocess.DEVNULL)
    ran = [json.loads(line[4:]) for line in (r.stdout + r.stderr)
           .splitlines() if line.startswith("STUB[")]
    if not ran:
        pytest.skip(f"{cmd!r} did not reach the stub here")
    whole = any(not [w for w in a if not w.startswith("-") and w != "run"]
                for a in ran)
    pc = _pc(leerie, _repo(tmp_path, BARNACLE))
    assert _refused(leerie, cmd, pc) is whole, (cmd, ran)
