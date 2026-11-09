"""A repository's browser suite is a channel the run provisions (DESIGN §8).

The `ci_checks` worker types a repository's browser suite like any other CI
check; code picks the channel from those records (`_browser_suite_candidate`),
offers it only after the suite ran here with a case passing
(`_establish_browser_suite`), runs a probe file through the suite's own
command in its declared environment (`_capture_failing_test_ids(suite=)`), and
leaves every payload byte-identical when there is no channel. Values and
behaviour are asserted, not shapes: the command that runs, the environment it
gets, the verdict the report gives, the payload text.
"""
from __future__ import annotations

import asyncio
import json
import os
import shlex
import subprocess
from pathlib import Path

import pytest

WORKFLOW = """\
name: e2e
on:
  pull_request:
jobs:
  e2e:
    runs-on: ubuntu-latest
    env:
      BASE_URL: http://localhost:3000
      SECRET_KEY: a b
      K: v
    steps:
      - run: pnpm run db:generate
      - run: pnpm run test:e2e --project=chromium
      - run: pnpm run test:e2e --project=chromium smoke.spec.ts
      - run: pnpm run test:unit
"""


def _rec(cid, argv, *, kind="test", requires=("browser", "database"),
         trigger=("pull_request",), cwd=".", env=(), pre=()):
    return {"id": cid, "kind": kind, "argv": list(argv), "cwd": cwd,
            "env": [{"name": n, "value": v} for n, v in env],
            "prerequisites": [{"argv": list(p), "cwd": "."} for p in pre],
            "trigger": list(trigger), "requires": list(requires),
            "strict_argv": [],
            "source": {"file": ".github/workflows/e2e.yml", "job": "e2e",
                       "step": cid},
            "coverage": {"summary_path": "", "config_file": "",
                         "thresholds": []}}


def _tree(tmp_path: Path, e2e_script: str = "playwright test") -> Path:
    t = tmp_path / "repo"
    (t / ".github" / "workflows").mkdir(parents=True)
    (t / ".github" / "workflows" / "e2e.yml").write_text(WORKFLOW)
    (t / "package.json").write_text(json.dumps({"scripts": {
        "test:e2e": e2e_script, "test:unit": "vitest run",
        "db:generate": "prisma generate"}}))
    return t


SUITE_ARGV = ["pnpm", "run", "test:e2e", "--project=chromium"]


# --- which check is the channel ---------------------------------------------

def test_the_declared_browser_suite_is_the_channel(leerie, tmp_path):
    t = _tree(tmp_path)
    got = leerie._browser_suite_candidate([
        _rec("unit", ["pnpm", "run", "test:unit"], requires=()),
        _rec("e2e-one", SUITE_ARGV + ["smoke.spec.ts"]),
        _rec("e2e", SUITE_ARGV, env=[("BASE_URL", "http://localhost:3000")],
             pre=[["pnpm", "run", "db:generate"]]),
    ], t)
    assert got["check_id"] == "e2e"
    assert got["runner"] == "playwright"
    assert leerie._browser_suite_cmd(got, ["e2e/p r.spec.ts"]) == (
        "pnpm run test:e2e --project=chromium 'e2e/p r.spec.ts'")
    assert leerie._browser_suite_env(got) == {
        "CI": "true", "BASE_URL": "http://localhost:3000"}
    assert got["prerequisites"] == [{"argv": ["pnpm", "run", "db:generate"],
                                     "cwd": "."}]


@pytest.mark.parametrize("rec", [
    # A check that names one file of its own: appending another would run
    # both.
    _rec("e2e-one", SUITE_ARGV + ["smoke.spec.ts"]),
    # Needs no browser, is not a test, gates no merge, runs elsewhere.
    _rec("e2e", SUITE_ARGV, requires=("database",)),
    _rec("e2e", SUITE_ARGV, kind="lint"),
    _rec("e2e", SUITE_ARGV, trigger=("schedule",)),
    _rec("e2e", SUITE_ARGV, cwd="web"),
    # Its argv is not in the CI file it names (provenance).
    _rec("e2e", ["pnpm", "run", "test:e2e", "--project=webkit"]),
])
def test_a_check_that_is_not_a_bare_declared_browser_suite_is_no_channel(
        leerie, tmp_path, rec):
    assert leerie._browser_suite_candidate([rec], _tree(tmp_path)) is None


def test_a_suite_whose_runner_is_unknown_is_no_channel(leerie, tmp_path):
    t = _tree(tmp_path, e2e_script="cypress run")
    assert leerie._browser_suite_candidate([_rec("e2e", SUITE_ARGV)],
                                           t) is None


def test_a_suite_the_task_forbids_is_no_channel(leerie, tmp_path):
    t = _tree(tmp_path)
    assert leerie._browser_suite_candidate(
        [_rec("e2e", SUITE_ARGV)], t,
        [{"command": "pnpm run test:e2e", "evidence_quote": "x",
          "scope": "any"}]) is None


@pytest.mark.parametrize("words,bare", [
    (["playwright", "test"], True),
    (["playwright", "test", "--project=chromium"], True),
    (["playwright", "test", "--project", "chromium", "--workers=1"], True),
    (["/x/node_modules/.bin/playwright", "test", "-x"], True),
    (["playwright", "test", "e2e/a.spec.ts"], False),
    (["playwright", "test", "--grep", "e2e/a.spec.ts"], False),
    (["playwright", "show-report"], False),
])
def test_bare_means_only_options_after_the_runner(leerie, words, bare):
    assert leerie._browser_suite_words_bare(words,
                                            ("playwright", "test")) is bare


# --- the runner's report ----------------------------------------------------

def _pw_report(root: str) -> bytes:
    return json.dumps({
        "config": {"rootDir": root},
        "errors": [{"message": "Error: Timed out waiting 120000ms from "
                               "config.webServer."}],
        "suites": [{
            "title": "nav.spec.ts", "file": "nav.spec.ts",
            "specs": [{"title": "header fits", "file": "nav.spec.ts",
                       "tests": [
                           {"projectName": "chromium", "status": "expected",
                            "results": [{"status": "passed"}]},
                           {"projectName": "Mobile Chrome",
                            "status": "unexpected",
                            "results": [{"status": "failed", "error": {
                                "message": "Expected: <= 375"}}]}]}],
            "suites": [{"title": "inbox", "file": "nav.spec.ts", "specs": [
                {"title": "reply is visible", "file": "nav.spec.ts",
                 "tests": [{"projectName": "chromium", "status": "flaky",
                            "results": [{"status": "failed"},
                                        {"status": "passed"}]}]},
                {"title": "later", "file": "nav.spec.ts",
                 "tests": [{"projectName": "chromium",
                            "status": "skipped", "results": []}]}]}],
        }],
    }).encode()


def test_the_playwright_report_gives_one_case_per_name(leerie, tmp_path):
    root = str(tmp_path / "e2e")
    cases = {c["id"]: c for c in leerie._runner_report_cases(
        "playwright-json", _pw_report(root), str(tmp_path))}
    # A case another project failed is failing; a flaky pass passed; a run
    # error with no file (the web server) is the run's, never a case's.
    assert cases["e2e/nav.spec.ts :: header fits"]["status"] == "failed"
    assert "<= 375" in cases["e2e/nav.spec.ts :: header fits"]["message"]
    assert cases["e2e/nav.spec.ts :: inbox reply is visible"][
        "status"] == "passed"
    assert cases["e2e/nav.spec.ts :: inbox later"]["status"] == "skipped"
    assert cases["(playwright) :: (file failed to load)"][
        "status"] == "load_error"
    parsed = leerie._parse_runner_report("playwright-json",
                                         _pw_report(root), str(tmp_path))
    assert parsed["executed"] == 2 and parsed["collection_error"] is True
    # The falsifier's case-name match finds the failing case, never the run
    # error.
    ids = leerie._runner_report_failing_ids("playwright-json",
                                            _pw_report(root), str(tmp_path))
    assert leerie._falsifier_case_id(ids, "header fits") == (
        "e2e/nav.spec.ts :: header fits")
    assert leerie._falsifier_case_id(ids, "(file failed to load)") is None


def test_the_report_is_requested_through_the_runners_environment(leerie,
                                                                 tmp_path):
    path = tmp_path / "r.json"
    cmd, env, kind = leerie._acceptance_report_spec(
        "playwright test --project=chromium e2e/a.spec.ts", path,
        ["e2e/a.spec.ts"])
    assert kind == "playwright-json"
    assert cmd == ("playwright test --project=chromium e2e/a.spec.ts "
                   "--reporter=json")
    assert env["PLAYWRIGHT_JSON_OUTPUT_FILE"] == str(path)
    assert env["PLAYWRIGHT_JSON_OUTPUT_NAME"] == str(path)


def test_the_runners_browser_download_is_a_store(leerie, tmp_path):
    env: dict[str, str] = {}
    run_dir = tmp_path / "runs" / "r1"
    run_dir.mkdir(parents=True)
    leerie._leerie_stores(env, run_dir)
    d = Path(env["PLAYWRIGHT_BROWSERS_PATH"])
    assert d.is_dir() and d.name == "playwright-browsers"
    assert d.is_relative_to(run_dir)


# --- which probe runs through it --------------------------------------------

@pytest.mark.parametrize("cmd,ran", [
    ("cd /lane && pnpm exec playwright test e2e/probe.spec.ts", True),
    ("npx playwright test probe.spec.ts --project=chromium", True),
    ("pnpm run test:e2e e2e/probe.spec.ts 2>&1 | tail -40", True),
    ("playwright test /lane/e2e/probe.spec.ts", True),
    ("pnpm exec vitest run e2e/probe.spec.ts", False),
    ("pnpm exec playwright test e2e/other.spec.ts", False),
    ("cat e2e/probe.spec.ts", False),
])
def test_a_probe_runs_through_the_suite_only_when_the_sample_ran_it_so(
        leerie, tmp_path, cmd, ran):
    t = _tree(tmp_path)
    suite = leerie._browser_suite_candidate([_rec("e2e", SUITE_ARGV)], t)
    lane = Path("/lane")
    (t / "e2e").mkdir()
    cmd = cmd.replace("/lane", str(t))
    assert leerie._browser_suite_ran_probe([cmd], "e2e/probe.spec.ts", t,
                                           suite) is ran
    del lane


def test_only_a_suite_seen_running_here_is_the_runs_channel(leerie):
    class St:
        data: dict = {}
    s = St()
    s.data = {"ci_parity": {"browser_suite": {"check_id": "e2e",
                                              "viable": False}}}
    assert leerie._browser_suite(s) is None
    s.data["ci_parity"]["browser_suite"]["viable"] = True
    assert leerie._browser_suite(s)["check_id"] == "e2e"
    s.data = {"ci_parity": {}}
    assert leerie._browser_suite(s) is None


# --- payloads ---------------------------------------------------------------

def _suite(leerie, tmp_path):
    return {**leerie._browser_suite_candidate(
        [_rec("e2e", SUITE_ARGV, env=[("SECRET_KEY", "a b")])],
        _tree(tmp_path)), "viable": True}


def test_without_a_channel_the_payloads_are_the_benched_ones(leerie,
                                                             tmp_path):
    args = ("task", {"item": "it"}, "abc", "stat", [], [], [])
    assert leerie._falsifier_user_prompt(*args) == \
        leerie._falsifier_user_prompt(*args, suite=None)
    assert leerie._observability_user_prompt("t", [], "pnpm test") == \
        leerie._observability_user_prompt("t", [], "pnpm test", suite=None)
    assert "BROWSER SUITE" not in leerie._observability_user_prompt(
        "t", [], "pnpm test")
    assert "BROWSER SUITE" not in leerie._falsifier_user_prompt(*args)


def test_with_a_channel_the_workers_are_told_its_command_and_environment(
        leerie, tmp_path):
    suite = _suite(leerie, tmp_path)
    f = leerie._falsifier_user_prompt("task", {"item": "it"}, "abc", "stat",
                                      [], [], [], suite=suite)
    o = leerie._observability_user_prompt("t", [], "pnpm test", suite=suite)
    for text in (f, o):
        assert "pnpm run test:e2e --project=chromium '<file>'" in text
        assert "SECRET_KEY='a b'" in text and "CI=true" in text
    # The run's database replaces the CI's: its URL is never handed on.
    db = {**suite, "requires": ["browser", "database"],
          "env": [{"name": "DATABASE_URL", "value": "postgresql://ci@x/ci"},
                  {"name": "DIRECT_URL", "value": "postgresql://ci@x/ci"},
                  {"name": "K", "value": "v"}]}
    text = leerie._falsifier_user_prompt("task", {"item": "it"}, "abc",
                                         "stat", [], [], [], suite=db)
    assert "postgresql://ci@x/ci" not in text and "K=v" in text
    assert "never set those" in text
    nodb = {**db, "requires": ["browser"]}
    assert "DATABASE_URL=postgresql://ci@x/ci" in \
        leerie._browser_suite_payload(nodb)
    # The falsifier's section sits before its closing instruction, and the
    # judge is told such a part is `test_command`.
    assert f.index("BROWSER SUITE") < f.index("Your working directory")
    assert "is `test_command`" in o
    # A layout part is steered to the suite: the test command's run cannot
    # fail on it.
    steer = ("A part about layout, what is visible on screen, viewport, "
             "hover or touch is one only this suite can show")
    assert steer in " ".join(f.split()) and "only this suite" not in o


# --- establishing it, by execution ------------------------------------------

def _establish(leerie, tmp_path, monkeypatch, report_for):
    """Drive `_establish_browser_suite` with the worktree, prerequisites and
    the run stubbed; the stubbed run writes `report_for(root)` where the
    runner's environment says to, as Playwright does."""
    t = _tree(tmp_path)
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / "package.json").write_text((t / "package.json").read_text())
    run_dir = tmp_path / "state" / "runs" / "r1"
    run_dir.mkdir(parents=True)

    class St:
        data = {"task": "x"}
    st = St()
    st.run_dir = run_dir
    st.repo_root = t
    seen: dict = {}

    async def wt_(st_, name, sha):
        return wt

    async def prep(tree, recs, st_, caps, side, done=None):
        seen["prep"] = (tree, [r["prerequisites"] for r in recs])

    async def ci_run(argv, *, tree, rec, st, caps, label, kind=None):
        seen["argv"], seen["env"] = argv, {e["name"]: e["value"]
                                          for e in rec["env"]}
        seen["requires"] = rec["requires"]
        out = Path(seen["env"]["PLAYWRIGHT_JSON_OUTPUT_FILE"])
        body = report_for(str(wt / "e2e"))
        if body is not None:
            out.write_bytes(body)
        return {"rc": 1, "timed_out": False}
    monkeypatch.setattr(leerie, "_ci_worktree", wt_)
    monkeypatch.setattr(leerie, "_ci_prepare", prep)
    monkeypatch.setattr(leerie, "_ci_run", ci_run)
    monkeypatch.setattr(leerie, "_ci_teardown", lambda *a: None)
    monkeypatch.setattr(leerie, "_measure_dir",
                        lambda st_, tree: tmp_path / "measure")
    (tmp_path / "measure").mkdir()
    ci = {"checks": [_rec("e2e", SUITE_ARGV, env=[("K", "v")],
                          pre=[["pnpm", "run", "db:generate"]])]}
    asyncio.run(leerie._establish_browser_suite(st, {}, ci, "basesha"))
    return ci, seen, wt


def test_a_suite_with_a_passing_case_here_is_offered(leerie, tmp_path,
                                                     monkeypatch):
    ci, seen, wt = _establish(leerie, tmp_path, monkeypatch, _pw_report)
    bs = ci["browser_suite"]
    assert bs["viable"] is True
    assert bs["base"]["passed"] == 1 and bs["base"]["failed"] == 1
    # Its prerequisites ran first, in the base checkout; the suite ran as
    # its runner, with its report requested, its CI environment, and the
    # database it requires.
    assert seen["prep"] == (wt, [[{"argv": ["pnpm", "run", "db:generate"],
                                   "cwd": "."}]])
    assert seen["argv"][:2] == ["bash", "-c"]
    assert shlex.split(seen["argv"][2]) == [
        "playwright", "test", "--project=chromium", "--reporter=json"]
    assert seen["env"]["K"] == "v" and seen["env"]["CI"] == "true"
    assert seen["env"]["PATH"].startswith(str(wt / "node_modules" / ".bin"))
    assert "database" in seen["requires"]


@pytest.mark.parametrize("report", [
    # Nothing passed here (the web server never came up), or no report.
    lambda root: json.dumps({"config": {"rootDir": root}, "suites": [],
                             "errors": [{"message": "webServer"}]}).encode(),
    lambda root: None,
])
def test_a_suite_with_no_passing_case_here_is_not_offered(
        leerie, tmp_path, monkeypatch, report):
    ci, _seen, _wt = _establish(leerie, tmp_path, monkeypatch, report)
    assert ci["browser_suite"]["viable"] is False
    assert leerie._browser_suite(type("S", (), {"data": {
        "ci_parity": ci}})()) is None


def test_no_declared_suite_leaves_ci_parity_as_it_was(leerie, tmp_path,
                                                      monkeypatch):
    t = _tree(tmp_path)

    class St:
        data = {"task": "x"}
    st = St()
    st.repo_root = t

    async def boom(*a, **k):
        raise AssertionError("no worktree for a run without a suite")
    monkeypatch.setattr(leerie, "_ci_worktree", boom)
    ci = {"checks": [_rec("unit", ["pnpm", "run", "test:unit"],
                          requires=())]}
    asyncio.run(leerie._establish_browser_suite(st, {}, ci, "basesha"))
    assert "browser_suite" not in ci


# --- the probe runs through it ----------------------------------------------

def test_a_probe_runs_through_the_suites_command_and_environment(
        leerie, tmp_path, monkeypatch):
    suite = _suite(leerie, tmp_path)
    tree = tmp_path / "repo"
    seen: dict = {}

    async def measured(st, tree_, caps, **k):
        return tree_

    async def stream(argv, *, grants, cwd, env=None, **k):
        seen["argv"], seen["env"] = argv, env
        root = str(Path(cwd) / "e2e")
        rep = env["PLAYWRIGHT_JSON_OUTPUT_FILE"]
        Path(rep).parent.mkdir(parents=True, exist_ok=True)
        Path(rep).write_bytes(_pw_report(root))
        return 1, ""
    monkeypatch.setattr(leerie, "_measured_tree", measured)
    monkeypatch.setattr(leerie, "_run_streaming", stream)
    monkeypatch.setattr(leerie, "_measurement_grants",
                        lambda st, tree, kind=None: leerie.WorkerGrants(
                            (), ()))
    monkeypatch.setattr(leerie, "_measure_dir",
                        lambda st, tree: tmp_path / "measure")
    monkeypatch.setattr(leerie, "_test_db_argv", lambda *a, **k: [])

    class St:
        data = {"task": "x"}
    st = St()
    st.repo_root = tree
    st.run_dir = tmp_path / "run"
    got = asyncio.run(leerie._capture_failing_test_ids(
        str(tree), "unused", st, {}, label="probe", log_path=tmp_path / "l",
        timeout=60, serial=True, files=["e2e/nav.spec.ts"], suite=suite))
    joined = " ".join(seen["argv"])
    assert "playwright test --project=chromium e2e/nav.spec.ts" in joined
    assert "--workers=1" in joined and "--reporter=json" in joined
    assert seen["env"]["SECRET_KEY"] == "a b"
    assert "e2e/nav.spec.ts :: header fits" in got["ids"]


# --- a demonstration fails at its assertion ---------------------------------

PROBE = """import { expect, test } from "@playwright/test";

test("header fits", async ({ page }) => {
  await page.goto("/es/chat");
  const off = await page.evaluate(() => 1);
  expect(off).toEqual([]);
});
"""


@pytest.mark.parametrize("messages,ok", [
    # At the assertion's line (6): the item's failure.
    (["Expected: []\n    at /t/e2e/p.spec.ts:6:15"], True),
    # At the navigation (4): the environment's, on every run.
    (["TimeoutError: page.goto: Timeout 90000ms exceeded.\n"
      "    at /t/e2e/p.spec.ts:4:14"] * 2, False),
    # One run at the assertion is enough.
    (["at /t/e2e/p.spec.ts:4:14", "at /t/e2e/p.spec.ts:6:3"], True),
    # The runner named no line of the probe: as before, not judged here.
    (["AssertionError: expected 1 to equal 2"], True),
    ([], True),
])
def test_a_failure_counts_only_at_the_quoted_assertion(leerie, messages,
                                                       ok):
    assert leerie._falsifier_failed_at_assertion(
        messages, "e2e/p.spec.ts", PROBE,
        "expect(off).toEqual([]);") is ok


def test_a_playwright_case_message_leads_with_where_it_failed(leerie,
                                                              tmp_path):
    root = str(tmp_path / "e2e")
    rep = json.dumps({"config": {"rootDir": root}, "suites": [{
        "title": "p.spec.ts", "file": "p.spec.ts", "specs": [{
            "title": "header fits", "file": "p.spec.ts", "tests": [{
                "status": "unexpected", "results": [
                    {"status": "failed", "error": {"message": "first try"}},
                    {"status": "failed", "error": {
                        "message": "x" * 5000,
                        "location": {"file": f"{root}/p.spec.ts",
                                     "line": 6, "column": 15}}}]}]}]}]}
                     ).encode()
    (case,) = leerie._runner_report_cases("playwright-json", rep,
                                          str(tmp_path))
    # The last attempt's, its location first, so the cap keeps it.
    assert case["message"].startswith(f"at {root}/p.spec.ts:6:15\n")
    assert "first try" not in case["message"]
    assert leerie._evidence_line(case["message"], "e2e/p.spec.ts") == 6


def test_the_runners_browser_is_the_trees_own_store(leerie, tmp_path):
    """The browser a tree's install fetched is the one every command in the
    tree runs — leerie's and, once leerie installed there, its worker's
    (DESIGN §6 *A worker started in a tree leerie installed for runs on that
    tree's stores*)."""
    run_dir = tmp_path / "state" / "runs" / "r1"
    lane = run_dir / "worktrees" / "falsifier-lane0"
    lane.mkdir(parents=True)
    (lane / ".git").write_text("gitdir: elsewhere\n")
    env = {"PLAYWRIGHT_BROWSERS_PATH": "/elsewhere"}
    leerie._tree_store_env(env, lane)
    assert Path(env["PLAYWRIGHT_BROWSERS_PATH"]).parts[-2:] == (
        "falsifier-lane0", "playwright-browsers")
    # A baked browser (the image's) is kept.
    baked = {"PLAYWRIGHT_BROWSERS_PATH": "/opt/browsers"}
    leerie._tree_store_env(baked, lane)
    assert baked["PLAYWRIGHT_BROWSERS_PATH"] == "/opt/browsers"


def test_an_unexpected_error_in_the_suite_check_is_not_swallowed(
        leerie, tmp_path, monkeypatch):
    """Only the check's own typed outcomes (`base.why`) end it quietly; a
    bug in it propagates out of the CI parity baseline like any CI parity
    error, never read as "no browser channel"."""
    async def boom(*a, **k):
        raise RuntimeError("leerie bug")
    monkeypatch.setattr(leerie, "_establish_browser_suite", boom)

    async def disc(*a, **k):
        return {"config_files": [".github/workflows/e2e.yml"], "checks": [],
                "excluded": []}
    monkeypatch.setattr(leerie, "_ci_discover", disc)

    async def head(*a, **k):
        return "basesha"
    monkeypatch.setattr(leerie, "_branch_head_sha", head)
    run_dir = tmp_path / "runs" / "r1"
    (run_dir / "worktrees" / "staging").mkdir(parents=True)

    class St:
        data: dict = {"task": "x"}
        repo_root = tmp_path
        saved = 0

        def save(self):
            St.saved += 1
    st = St()
    st.run_dir = run_dir
    with pytest.raises(RuntimeError, match="leerie bug"):
        asyncio.run(leerie._capture_ci_parity_baseline(
            run_dir, st, {}, {}, {}))
    # Nothing recorded as done: a resume runs the baseline again.
    assert "ci_parity" not in st.data
