"""A script's environment is part of what it runs (DESIGN §9 *A script's
environment is part of what it runs*).

Round-5 review M1: deriving from a forbidden script's body dropped its
environment, so forbidding `"test:db2": "NODE_ENV=production vitest run"`
(it hits the production database) derived `vitest run` and refused every
test run in the repo — and the sibling scan then forbade `pnpm test`.
Review M2: `cross-env`, `dotenv` and `env-cmd` bodies derived nothing and
the matcher did not see through them, so on a `"test": "cross-env TZ=UTC
vitest run"` repo a worker's `npx vitest run` ran the forbidden suite.

The reviewer's envprobe.py is kept below with its outcome inverted; the
measured rows (`tests/fixtures/forbid_runner_options.json`, "env
wrappers") pin what the real tools run, and a live re-run checks them
again when the tools are on PATH.
"""
from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

FIXTURE = Path(__file__).resolve().parent / "fixtures" \
    / "forbid_runner_options.json"
HOOK = Path(__file__).resolve().parent.parent / "scripts" \
    / "forbidden-command-hook.py"


def _pc(leerie, tmp_path: Path, scripts: dict, forbid: str, scope: str):
    (tmp_path / "package.json").write_text(json.dumps(
        {"name": "root", "private": True, "scripts": scripts}))
    (tmp_path / "pnpm-lock.yaml").write_text("")
    (tmp_path / "tests").mkdir(exist_ok=True)
    res = {"process_constraints": {"forbidden_commands": [
        {"command": forbid, "scope": scope, "evidence_quote": forbid}]}}
    return leerie._process_constraints(
        res, None, [f"do not run `{forbid}`"], tmp_path)


def _refused(leerie, cmd: str, pc: dict) -> bool:
    return leerie._forbidden_command_hit(cmd, pc["entries"]) is not None


# --- the reviewer's envprobe.py, inverted --------------------------------

ENVPROBE = {"test": "vitest run",
            "test:db": "dotenv -e .env.production -- vitest run",
            "test:db2": "NODE_ENV=production vitest run",
            "test:e2e": "vitest run --config vitest.e2e.config.ts"}
ORDINARY = ["pnpm test", "pnpm run test", "vitest run src/a.test.ts",
            "pnpm vitest run src/a.test.ts", "vitest run",
            "npx vitest run src/a.test.ts", "pnpm run test:e2e"]


@pytest.mark.parametrize("forbid, own", [
    ("pnpm run test:db", "dotenv -e .env.production -- vitest run"),
    ("pnpm run test:db2", "NODE_ENV=production vitest run")])
@pytest.mark.parametrize("scope", ["any", "whole_suite"])
def test_an_env_script_forbids_its_env_not_the_runner(leerie, tmp_path,
                                                      forbid, own, scope):
    pc = _pc(leerie, tmp_path, ENVPROBE, forbid, scope)
    cmds = [e["command"] for e in pc["entries"]]
    # Each sibling runs the forbidden command when a call sets the env
    # (round-6 review L3): named in that env, never bare.
    prefix = own[:-len("vitest run")]
    assert cmds == [forbid, own] + [
        prefix + f"pnpm run {n}" for n in ("test", "test:db", "test:db2",
                                           "test:e2e")
        if f"pnpm run {n}" != forbid]
    for c in ORDINARY:
        assert not _refused(leerie, c, pc), c
    assert _refused(leerie, own, pc)
    assert _refused(leerie, forbid, pc)
    assert _refused(leerie, prefix + "pnpm test", pc)


def test_the_kept_env_is_required_wherever_it_is_set(leerie, tmp_path):
    pc = _pc(leerie, tmp_path, ENVPROBE, "pnpm run test:db2", "any")
    for c in ["NODE_ENV=production npx vitest run src/a.test.ts",
              "env NODE_ENV=production vitest run",
              "cross-env NODE_ENV=production vitest run",
              "dotenv -v NODE_ENV=production -- vitest run",
              "export NODE_ENV=production && vitest run",
              "NODE_ENV=production CI=1 bash -c 'vitest run'"]:
        assert _refused(leerie, c, pc), c
    for c in ["NODE_ENV=test vitest run", "cross-env TZ=UTC vitest run"]:
        assert not _refused(leerie, c, pc), c


def test_one_env_file_reads_the_same_through_either_tool(leerie, tmp_path):
    # `dotenv -e F` and `env-cmd -f F` load the same file: the sibling scan
    # names the env-cmd script, and a plain run stays allowed.
    scripts = dict(ENVPROBE, **{
        "test:db3": "env-cmd -f ./.env.production vitest run"})
    pc = _pc(leerie, tmp_path, scripts, "pnpm run test:db", "any")
    env = "dotenv -e .env.production -- "
    assert [e["command"] for e in pc["entries"]] == [
        "pnpm run test:db", env + "vitest run", env + "pnpm run test",
        env + "pnpm run test:db2", "pnpm run test:db3",
        env + "pnpm run test:e2e"]
    assert not _refused(leerie, "vitest run", pc)
    assert not _refused(leerie, "pnpm test", pc)


# --- when the env is dropped ---------------------------------------------

ANY_SPELLING = ["npx vitest run", "vitest run", "pnpm vitest run",
                "cross-env CI=1 vitest run", "dotenv -- vitest run"]


@pytest.mark.parametrize("body", [
    "cross-env TZ=UTC vitest run", "dotenv -e .env.test -- vitest run",
    "env-cmd -f .env.test vitest run", "NODE_ENV=test vitest run",
    "cross-env-shell TZ=UTC 'vitest run'"])
def test_the_repo_test_script_env_is_how_it_runs_the_suite(leerie, tmp_path,
                                                           body):
    pc = _pc(leerie, tmp_path, {"test": body}, "pnpm run test",
             "whole_suite")
    assert [e["command"] for e in pc["entries"]] == [
        "pnpm run test", "vitest run"]
    for c in ANY_SPELLING:
        assert _refused(leerie, c, pc), c
    assert not _refused(leerie, "npx vitest run src/a.test.ts", pc)


def test_round4_m3_plain_body_still_derives_the_runner(leerie, tmp_path):
    pc = _pc(leerie, tmp_path, {"test": "vitest run"}, "pnpm test",
             "whole_suite")
    assert [e["command"] for e in pc["entries"]] == ["pnpm test",
                                                     "vitest run"]
    assert _refused(leerie, "npx vitest run", pc)


def test_the_test_script_env_drops_even_beside_another_env(leerie,
                                                           tmp_path):
    # The suite runs under NODE_ENV=test and a sibling under production:
    # the forbidden script is the repo's test command, so the env goes.
    scripts = {"test": "NODE_ENV=test vitest run",
               "test:prod": "NODE_ENV=production vitest run"}
    pc = _pc(leerie, tmp_path, scripts, "pnpm test", "whole_suite")
    assert [e["command"] for e in pc["entries"]] == [
        "pnpm test", "vitest run", "pnpm run test:prod"]
    assert _refused(leerie, "npx vitest run", pc)


def test_an_env_nothing_else_lacks_is_dropped(leerie, tmp_path):
    # No other script runs vitest, so TZ=UTC sets test:ui apart from
    # nothing: the env is how this repo runs vitest.
    scripts = {"test": "jest", "test:ui": "cross-env TZ=UTC vitest run"}
    pc = _pc(leerie, tmp_path, scripts, "pnpm run test:ui", "whole_suite")
    assert [e["command"] for e in pc["entries"]] == [
        "pnpm run test:ui", "vitest run"]
    assert _refused(leerie, "npx vitest run", pc)
    assert not _refused(leerie, "pnpm test", pc)


def test_a_non_test_script_with_a_distinct_env_keeps_it(leerie, tmp_path):
    # Inputs disagree: test:stg's env differs from the plain sibling's, is
    # no hygiene variable, and the forbidden script is not the repo's test
    # command.
    scripts = {"test": "vitest run",
               "test:stg": "cross-env NODE_ENV=staging vitest run --coverage"}
    pc = _pc(leerie, tmp_path, scripts, "pnpm run test:stg", "whole_suite")
    assert [e["command"] for e in pc["entries"]] == [
        "pnpm run test:stg", "NODE_ENV=staging vitest run --coverage",
        "NODE_ENV=staging pnpm run test"]
    assert not _refused(leerie, "npx vitest run", pc)
    assert not _refused(leerie, "pnpm test", pc)
    assert _refused(leerie, "NODE_ENV=staging npx vitest run", pc)


@pytest.mark.parametrize("env", [
    "CI=1", "TZ=UTC", "FORCE_COLOR=0", "NO_COLOR=1", "LANG=C.UTF-8",
    "LC_ALL=C", "TZ=UTC CI=true",
    "NODE_OPTIONS=--max-old-space-size=4096",
    "NODE_OPTIONS='--max-old-space-size=8192 --inspect'"])
def test_a_hygiene_env_is_how_the_repo_runs_the_runner(leerie, tmp_path,
                                                       env):
    # Before round 6 this case dropped through "no other script runs the
    # runner without it"; now through the hygiene list, beside a plain
    # sibling that the old rule would have kept the env for.
    scripts = {"test": "vitest run",
               "test:ci": f"cross-env {env} vitest run --coverage"}
    pc = _pc(leerie, tmp_path, scripts, "pnpm run test:ci", "whole_suite")
    assert [e["command"] for e in pc["entries"]][:2] == [
        "pnpm run test:ci", "vitest run --coverage"]
    assert _refused(leerie, "npx vitest run", pc)


@pytest.mark.parametrize("env", [
    "NODE_ENV=test", "DATABASE_URL=postgres://prod.example.com/app",
    "NODE_OPTIONS=--require=./seed-prod.js",
    "NODE_OPTIONS='--max-old-space-size=4096 --require=./x.js'",
    "TZ=UTC NODE_ENV=production", "CIRCLE=1"])
def test_an_env_off_the_hygiene_list_is_kept(leerie, tmp_path, env):
    scripts = {"test": "turbo run test",
               "test:x": f"cross-env {env} vitest run"}
    pc = _pc(leerie, tmp_path, scripts, "pnpm run test:x", "any")
    assert pc["entries"][1]["command"] != "vitest run"
    assert not _refused(leerie, "vitest run src/a.test.ts", pc)


def test_r6_l2_turborepo_root_keeps_the_dangerous_env(leerie, tmp_path):
    # Round-6 review L2: the only root script that calls vitest is the one
    # carrying the prod DATABASE_URL. "No other script runs the runner
    # without it" dropped the env, refusing every vitest run in the repo.
    url = "DATABASE_URL=postgres://prod.example.com/app"
    scripts = {"test": "turbo run test", "test:db": f"{url} vitest run"}
    pc = _pc(leerie, tmp_path, scripts, "pnpm run test:db", "any")
    assert [e["command"] for e in pc["entries"]] == [
        "pnpm run test:db", f"{url} vitest run"]
    for c in ["vitest run src/a.test.ts", "npx vitest run src/a.test.ts",
              "vitest run", "pnpm test"]:
        assert not _refused(leerie, c, pc), c
    for c in [f"{url} npx vitest run", "pnpm run test:db",
              f"export {url} && vitest run src/a.test.ts"]:
        assert _refused(leerie, c, pc), c


# --- an env set on a script call (round-6 review L3) ----------------------

@pytest.mark.parametrize("cmd, refused", [
    ("NODE_ENV=production pnpm test", True),
    ("export NODE_ENV=production && pnpm test", True),
    ("env NODE_ENV=production npm run test", True),
    ("cross-env NODE_ENV=production pnpm run test", True),
    ("NODE_ENV=production pnpm run test:e2e", True),
    ("pnpm test", False), ("NODE_ENV=test pnpm test", False),
    ("NODE_ENV=production pnpm test src/a.test.ts", True),
])
def test_an_env_on_a_script_call_reaches_its_body(leerie, tmp_path, cmd,
                                                  refused):
    pc = _pc(leerie, tmp_path, ENVPROBE, "pnpm run test:db2", "any")
    assert _refused(leerie, cmd, pc) is refused, cmd


def test_an_env_on_a_script_call_respects_the_scope(leerie, tmp_path):
    pc = _pc(leerie, tmp_path, ENVPROBE, "pnpm run test:db2", "whole_suite")
    assert _refused(leerie, "NODE_ENV=production pnpm test", pc)
    assert not _refused(leerie,
                        "NODE_ENV=production pnpm test src/a.test.ts", pc)


def test_an_env_scoped_script_name_never_reaches_the_guard(leerie,
                                                           tmp_path):
    # The runtime guard sees a lifecycle name and no env: naming `test`
    # there would refuse the plain `pnpm test` the task allows.
    pc = _pc(leerie, tmp_path, ENVPROBE, "pnpm run test:db2", "any")
    assert "NODE_ENV=production pnpm run test" in [
        e["command"] for e in pc["entries"]]
    assert leerie._forbidden_script_names(pc["entries"]) == ["test:db2"]
    assert leerie._forbidden_script_names(
        ["NODE_ENV=production pnpm test", "pnpm run lint"]) == ["lint"]


# --- the env round trip ---------------------------------------------------

@pytest.mark.parametrize("env", [
    set(), {"NODE_ENV=production"}, {"A=1", "B=two words"},
    {"envfile:.env.production"}, {"envfile:.env", "envfile:.env.local"},
    {"env-cmd -e dev"}, {"env-cmd -e dev", "env-cmd -e ci"},
    {"-i", "-u HOME", "-C sub dir", "X=1"},
    {"NODE_ENV=x", "envfile:.env.x", "env-cmd -e e"}])
def test_a_derived_command_reads_back_to_its_env(leerie, env):
    argv = ["vitest", "run", "--coverage"]
    cmd = leerie._forbidden_env_render(argv, frozenset(env))
    assert leerie._forbidden_argvs_env(cmd) == [(argv, frozenset(env))]


# --- the measured rows ----------------------------------------------------

def _rows():
    return json.loads(FIXTURE.read_text())["env wrappers"]


def _check_row(leerie, row) -> str | None:
    """None when the matcher reads what the real tool ran: the argv the
    stub received, in an environment whose tokens account for every
    variable it saw."""
    sec = _rows()
    reads = leerie._forbidden_argvs_env(row["cmd"])
    ran = row["ran"]
    if ran is None:
        return None
    hits = [e for a, e in reads if a == ["vitest", *ran["args"]]]
    if not hits:
        return f"argv {ran['args']} not among {[a for a, _ in reads]}"
    srcs: dict[str, set] = {}
    for t in hits[0]:
        if t.startswith("envfile:") and t[8:] in sec["files"]:
            srcs.setdefault("NODE_ENV", set()).add(sec["files"][t[8:]])
        elif t.startswith("env-cmd -e ") and t[11:] in sec["rc"]:
            srcs.setdefault("NODE_ENV", set()).add(sec["rc"][t[11:]])
        elif "=" in t and not t.startswith("envfile:"):
            k, v = t.split("=", 1)
            srcs.setdefault(k, set()).add(v)
    for k, v in ran["env"].items():
        if v not in srcs.get(k, ()):
            return f"{k}={v} not read (tokens {sorted(hits[0])})"
    return None


@pytest.mark.parametrize("i", range(54))
def test_the_measured_env_wrapper_rows(leerie, i):
    row = _rows()["rows"][i]
    assert _check_row(leerie, row) is None, row


def test_the_rows_cover_every_wrapper(leerie):
    words = {w for r in _rows()["rows"] for w in shlex.split(r["cmd"])}
    assert set(leerie._FORBID_ENV_WRAPPERS) <= words
    assert len(_rows()["rows"]) == 54


@pytest.mark.parametrize("args, files", [
    ("-c", [".env.local", ".env"]),
    ("-c production", [".env.production.local", ".env.local",
                       ".env.production", ".env"]),
    ("-qe .env.production", [".env.production"]),
    ("-e.env.x", [".env.x"]),
    ("", [".env"])])
def test_dotenv_reads_the_files_dotenv_debug_lists(leerie, args, files):
    # `dotenv <args> --debug` printed these lists (dotenv-cli 11.1).
    (_a, env), = leerie._forbidden_argvs_env(f"dotenv {args} -- vitest")
    assert env == frozenset("envfile:" + f for f in files)


@pytest.mark.parametrize("cmd", [
    "dotenv --debug vitest run", "dotenv -p NODE_ENV vitest",
    "dotenv --help -- vitest", "env-cmd -e dev vitest run",
    "env-cmd -v vitest run", "cross-env TZ=UTC", "env-cmd -r x vitest"])
def test_a_wrapper_that_runs_nothing_reads_as_nothing(leerie, cmd):
    assert leerie._forbidden_argvs_env(cmd) == []


TOOLS = ("cross-env", "cross-env-shell", "dotenv", "dotenv-cli", "env-cmd")


@pytest.mark.skipif(not all(shutil.which(t) for t in TOOLS),
                    reason="cross-env, dotenv-cli and env-cmd not on PATH")
def test_the_rows_still_hold_with_the_real_tools(leerie, tmp_path):
    sec = _rows()
    for name, val in sec["files"].items():
        (tmp_path / name).write_text(f"NODE_ENV={val}\n")
    (tmp_path / ".env-cmdrc.json").write_text(json.dumps(
        {k: {"NODE_ENV": v} for k, v in sec["rc"].items()}))
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    stub = bin_ / "vitest"
    stub.write_text("#!/bin/sh\nprintf 'RAN'; for a in \"$@\"; do "
                    "printf ' [%s]' \"$a\"; done; printf ' ENV NODE_ENV=%s "
                    "TZ=%s CI=%s\\n' \"$NODE_ENV\" \"$TZ\" \"$CI\"\n")
    stub.chmod(0o755)
    env = {k: v for k, v in os.environ.items()
           if k not in ("NODE_ENV", "TZ", "CI")}
    env["PATH"] = f"{bin_}{os.pathsep}{env['PATH']}"
    for row in sec["rows"]:
        if row["cmd"].startswith(("npx", "pnpm")):
            continue
        out = subprocess.run(["bash", "-c", row["cmd"]], cwd=tmp_path,
                             env=env, capture_output=True, text=True,
                             timeout=60).stdout
        line = next((x for x in out.splitlines() if x.startswith("RAN")),
                    None)
        if row["ran"] is None:
            assert line is None, row
            continue
        head, _, envs = line.partition(" ENV ")
        assert [w[1:-1] for w in head.split(" ")[1:]] == row["ran"]["args"]
        seen = dict(kv.split("=", 1) for kv in envs.split(" "))
        assert {k: v for k, v in seen.items() if v} == row["ran"]["env"]


# --- the worker hook --------------------------------------------------------

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
    ("NODE_ENV=production npx vitest run", True),
    ("cross-env NODE_ENV=production vitest run", True),
    ("npx vitest run", False),
    ("pnpm test", False),
    ("NODE_ENV=production pnpm test", True),
    ("export NODE_ENV=production && pnpm test", True),
])
def test_the_hook_process_reads_the_env(leerie, tmp_path, cmd, denied):
    pc = _pc(leerie, tmp_path, ENVPROBE, "pnpm run test:db2", "any")
    out = _hook(leerie, pc, cmd)
    assert ('"permissionDecision": "deny"' in out) is denied, (cmd, out)
