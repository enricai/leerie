"""A pnpm filter that names one package of several is scoped (DESIGN §9 *A
pnpm filter that names one package of several is scoped*).

Round-5 review L2: `pnpm --filter pkg test` — the standard per-package
spelling in a monorepo — was refused under a whole-suite `pnpm test`
forbid, beside vitest's `--project unit`, which reads as scoped. The
measured rows (`tests/fixtures/forbid_runner_options.json`, "pnpm
filters") ran each spelling with the real pnpm; a live re-run checks them
again when pnpm is on PATH.
"""
from __future__ import annotations

import base64
import json
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


def _write(d: Path, name: str, workspace: bool) -> None:
    d.mkdir(parents=True, exist_ok=True)
    (d / "package.json").write_text(json.dumps(
        {"name": name, "private": True,
         "scripts": {"test": "echo MARK-root"}}))
    if workspace:
        (d / "pnpm-workspace.yaml").write_text(
            'packages:\n  - "packages/*"\n')
        for p in "ab":
            (d / "packages" / p).mkdir(parents=True, exist_ok=True)
            (d / "packages" / p / "package.json").write_text(json.dumps(
                {"name": f"@s/{p}",
                 "scripts": {"test": f"echo MARK-{p}"}}))


def _pc(leerie, d: Path, scope="whole_suite"):
    res = {"process_constraints": {"forbidden_commands": [
        {"command": "pnpm test", "scope": scope,
         "evidence_quote": "pnpm test"}]}}
    return leerie._process_constraints(res, None, ["pnpm test"], d)


def _repos(tmp_path: Path) -> dict[str, Path]:
    _write(tmp_path / "ws", "root", True)
    _write(tmp_path / "single", "solo", False)
    return {"ws": tmp_path / "ws", "single": tmp_path / "single"}


def _rows():
    return json.loads(FIXTURE.read_text())["pnpm filters"]["rows"]


# Read conservatively (DESIGN §9): an exclusion or a dependency closure
# selects packages the matcher cannot list (a closure holds the root once
# the root depends on its package, round-6 review M1), and `-r` runs every
# package's suite.
KNOWN = {("ws", "pnpm --filter '!@s/a' test"), ("ws", "pnpm -r test"),
         ("ws", "pnpm --filter '...@s/a' test"),
         ("single", "pnpm --filter '...@s/a' test")}


@pytest.mark.parametrize("i", range(48))
def test_the_measured_filter_rows(leerie, tmp_path, i):
    row = _rows()[i]
    pc = _pc(leerie, _repos(tmp_path)[row["repo"]])
    want = "root" in row["ran"] or (row["repo"], row["cmd"]) in KNOWN
    got = leerie._forbidden_command_hit(row["cmd"], pc["entries"])
    assert (got is not None) is want, row


def test_the_rows_are_all_there():
    rows = _rows()
    assert len(rows) == 48
    assert {r["repo"] for r in rows} == {"ws", "single"}


def test_the_entry_records_the_root_package(leerie, tmp_path):
    pc = _pc(leerie, _repos(tmp_path)["ws"])
    assert [e.get("package_name") for e in pc["entries"]] == ["root"]
    assert _pc(leerie, _repos(tmp_path)["single"])["entries"][0][
        "package_name"] == "solo"


def test_derived_entries_carry_the_package(leerie, tmp_path):
    d = tmp_path / "r"
    d.mkdir()
    (d / "package.json").write_text(json.dumps(
        {"name": "app", "scripts": {"test": "vitest run",
                                    "test:ci": "vitest run --coverage"}}))
    pc = _pc(leerie, d)
    assert {e["command"]: e.get("package_name") for e in pc["entries"]} \
        == {"pnpm test": "app", "vitest run": "app",
            "pnpm run test:ci": "app"}


@pytest.mark.parametrize("values, name, scoped", [
    (["@s/a"], "root", True), (["@s/*"], "root", True),
    (["./packages/**"], "root", True), (["{packages/a}"], "root", True),
    (["./*"], "root", True), (["{./*}"], "root", True),
    (["./**/*"], "root", True), (["{./packages/**}"], "root", True),
    # Round-6 review M1: a closure is read as holding the root, and a path
    # glob matching `.` selects it.
    (["...@s/a"], "root", False), (["@s/a^..."], "root", False),
    (["@s/a..."], "root", False), (["^@s/a"], "root", False),
    (["...^@s/a"], "root", False), (["/abs/dir"], "root", False),
    (["./**"], "root", False), (["{./**}"], "root", False),
    (["{**}"], "root", False), (["./**/"], "root", False),
    (["{./}"], "root", False),
    (["*"], "root", False), (["**"], "root", False), (["."], "root", False),
    (["./"], "root", False), (["{.}"], "root", False),
    (["root"], "root", False), (["ro*"], "root", False),
    (["...root"], "root", False), (["@s/a", "root"], "root", False),
    (["!@s/a"], "root", False), (["[origin/main]"], "root", False),
    (["root"], None, True), (["*"], None, False)])
def test_which_filters_select_the_root(leerie, values, name, scoped):
    env = frozenset("pnpm --filter " + v for v in values)
    assert leerie._forbidden_filter_scoped(env, name) is scoped


def test_no_filter_is_not_scoped(leerie):
    assert leerie._forbidden_filter_scoped(frozenset({"CI=1"}), "x") is False


@pytest.mark.parametrize("cmd", [
    "pnpm --filter @s/a test", "pnpm -F @s/a run test",
    "pnpm --filter=@s/a exec vitest run"])
def test_scope_any_still_refuses_a_filtered_run(leerie, tmp_path, cmd):
    d = tmp_path / "r"
    d.mkdir()
    (d / "package.json").write_text(json.dumps(
        {"name": "root", "scripts": {"test": "vitest run"}}))
    pc = _pc(leerie, d, scope="any")
    assert leerie._forbidden_command_hit(cmd, pc["entries"]) is not None


def test_a_filtered_runner_entry_reads_back(leerie):
    argv = ["vitest", "run"]
    env = frozenset({"pnpm --filter web", "CI=1"})
    cmd = leerie._forbidden_env_render(argv, env)
    assert leerie._forbidden_argvs_env(cmd) == [(argv, env)]


def test_an_entry_naming_a_filter_needs_it(leerie):
    entry = {"command": "pnpm --filter web test", "scope": "whole_suite",
             "test_roots": [], "package_name": "root"}
    hit = leerie._forbidden_command_hit
    assert hit("pnpm --filter web test", [entry])
    assert hit("pnpm --filter web --filter api test", [entry])
    assert not hit("pnpm --filter api test", [entry])


def _hook(leerie, pc, command: str) -> str:
    settings = json.loads(leerie._forbidden_command_hook_settings(
        leerie._forbidden_specs({"process_constraints": pc})))
    argv = shlex.split(settings["hooks"]["PreToolUse"][0]["hooks"][0]
                       ["command"])
    payload = json.loads(base64.b64decode(argv[-1]))
    assert payload["forbidden"][0]["package_name"] == "root"
    r = subprocess.run([sys.executable, "-I", str(HOOK), argv[-1]],
                       input=json.dumps({"tool_name": "Bash",
                                         "tool_input": {"command": command}}),
                       capture_output=True, text=True, timeout=120)
    return r.stdout


@pytest.mark.parametrize("cmd, denied", [
    ("pnpm --filter @s/a test", False),
    ("pnpm --filter root test", True),
    ("pnpm --filter '*' test", True),
    ("pnpm test", True),
])
def test_the_hook_process_reads_the_filter(leerie, tmp_path, cmd, denied):
    pc = _pc(leerie, _repos(tmp_path)["ws"])
    out = _hook(leerie, pc, cmd)
    assert ('"permissionDecision": "deny"' in out) is denied, (cmd, out)


@pytest.mark.skipif(shutil.which("pnpm") is None, reason="pnpm not on PATH")
def test_the_rows_still_hold_with_the_real_pnpm(tmp_path):
    repos = _repos(tmp_path)
    for row in _rows():
        out = subprocess.run(["bash", "-c", row["cmd"]],
                             cwd=repos[row["repo"]], capture_output=True,
                             text=True, timeout=120).stdout
        ran = sorted({ln.split("MARK-")[1].strip()
                      for ln in out.splitlines()
                      if "MARK-" in ln and "echo" not in ln})
        assert ran == row["ran"], row


# --- round-6 review M1 -----------------------------------------------------

def _write_r6(d: Path, name: str, workspace: bool, root_dep: bool) -> None:
    d.mkdir(parents=True, exist_ok=True)
    pj = {"name": name, "private": True,
          "scripts": {"test": "echo MARK-root"}}
    if root_dep:
        pj["devDependencies"] = {"@s/a": "workspace:*"}
    (d / "package.json").write_text(json.dumps(pj))
    if workspace:
        (d / "pnpm-workspace.yaml").write_text(
            'packages:\n  - "packages/*"\n')
        for p in "ab":
            (d / "packages" / p).mkdir(parents=True, exist_ok=True)
            pp = {"name": f"@s/{p}", "scripts": {"test": f"echo MARK-{p}"}}
            if p == "b":
                pp["dependencies"] = {"@s/a": "workspace:*"}
            (d / "packages" / p / "package.json").write_text(json.dumps(pp))


def _repos_r6(tmp_path: Path) -> dict[str, Path]:
    _write_r6(tmp_path / "ws", "root", True, False)
    _write_r6(tmp_path / "wsdep", "root", True, True)
    _write_r6(tmp_path / "single", "solo", False, False)
    return {k: tmp_path / k for k in ("ws", "wsdep", "single")}


def _rows_r6():
    return json.loads(FIXTURE.read_text())["pnpm filters r6"]["rows"]


def _r6_conservative(row) -> bool:
    """A false refusal the matcher takes on knowingly: a closure selector
    (it reads no dependency graph), and `-w` outside a workspace, which
    pnpm rejects (ran nothing)."""
    words = shlex.split(row["cmd"])
    closure = any(w.startswith(("...", "^")) or w.endswith("...")
                  for w in words)
    return closure or (row["repo"] == "single"
                       and bool({"-w", "--workspace-root"} & set(words)))


def test_the_r6_rows_are_all_there():
    rows = _rows_r6()
    assert len(rows) == 69
    assert {r["repo"] for r in rows} == {"ws", "wsdep", "single"}
    # The reviewer's five spellings, each running the root's `test`.
    for cmd in ["pnpm --filter './**' test", "pnpm --filter '{./**}' test",
                "pnpm -w --filter @s/a test",
                "pnpm --workspace-root --filter @s/a test"]:
        assert all("root" in r["ran"] for r in rows
                   if r["cmd"] == cmd and r["repo"] != "single")
    assert [r["ran"] for r in rows if r["repo"] == "wsdep"
            and r["cmd"] == "pnpm --filter '...@s/a' test"] == [
        ["a", "b", "root"]]


@pytest.mark.parametrize("i", range(69))
def test_the_measured_r6_rows(leerie, tmp_path, i):
    row = _rows_r6()[i]
    pc = _pc(leerie, _repos_r6(tmp_path)[row["repo"]])
    want = "root" in row["ran"] or _r6_conservative(row)
    got = leerie._forbidden_command_hit(row["cmd"], pc["entries"])
    assert (got is not None) is want, row


def test_no_r6_row_that_runs_the_root_is_conservative():
    # The false refusals the conservative reading takes on knowingly,
    # counted so a widening of it shows up here.
    rows = _rows_r6()
    assert sum("root" in r["ran"] for r in rows) == 32
    assert sum(_r6_conservative(r) and "root" not in r["ran"]
               for r in rows) == 25


@pytest.mark.parametrize("words, root", [
    (["-w", "--filter", "@s/a"], True),
    (["--workspace-root", "--filter", "@s/a"], True),
    (["--filter", "@s/a", "-w"], True),
    (["-F", "@s/a"], False),
    (["-w"], False),
])
def test_workspace_root_beside_a_filter_adds_the_root(leerie, words, root):
    vals = leerie._FORBID_PM_VALUE_OPTS["pnpm"]
    toks = leerie._forbidden_pnpm_filters(words + ["test"], vals)
    assert ("pnpm --filter ." in toks) is root


def test_a_lone_workspace_root_entry_still_hits_the_plain_run(leerie):
    # `-w` alone adds no token, so a forbidden `pnpm -w test` keeps
    # hitting `pnpm test` (an env token would demand it of the candidate).
    entry = {"command": "pnpm -w test", "scope": "whole_suite",
             "test_roots": [], "package_name": "root"}
    assert leerie._forbidden_command_hit("pnpm test", [entry])


@pytest.mark.skipif(shutil.which("pnpm") is None, reason="pnpm not on PATH")
def test_the_r6_rows_still_hold_with_the_real_pnpm(tmp_path):
    repos = _repos_r6(tmp_path)
    for row in _rows_r6():
        out = subprocess.run(["bash", "-c", row["cmd"]],
                             cwd=repos[row["repo"]], capture_output=True,
                             text=True, timeout=120).stdout
        ran = sorted({ln.split("MARK-")[1].strip()
                      for ln in out.splitlines()
                      if "MARK-" in ln and "echo" not in ln})
        assert ran == row["ran"], row
