"""A package-manager script call names its runner through `package.json`
(DESIGN §9 *Which failures are new*).

Replay shape (barnacle 7cc87f9a, early replay): the test command was
`pnpm run test`, whose script is `vitest run`. `_acceptance_runner` matched
runners on the command's own words, found none, and `_capture_failing_test_
ids` returned None — so all three passes recorded `baseline_failing_ids:
null` on a red base, and the E3 diff, its intermittent discovery and N5's
execution route never ran.

The end-to-end tests use a stand-in `vitest` in the tree's
`node_modules/.bin` that writes the jest-compatible JSON report vitest
writes, so the whole path — resolution, report request, PATH, report
parsing — runs for real.
"""
from __future__ import annotations

import asyncio
import json
import os
import stat
from pathlib import Path

import pytest

BARNACLE_SCRIPTS = {
    "test": "vitest run",
    "test:unit": "pnpm run test:inner -- --project unit",
    "test:inner": "vitest run",
    "test:all": "pnpm run lint && pnpm run test",
    "lint": "eslint .",
    "loop:a": "npm run loop:b",
    "loop:b": "npm run loop:a",
}


def _tree(tmp_path: Path, scripts: dict | None = None) -> Path:
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "package.json").write_text(json.dumps(
        {"name": "barnacle", "scripts": scripts or BARNACLE_SCRIPTS}))
    return tree


@pytest.mark.parametrize("cmd,want", [
    ("pnpm run test", "vitest run"),
    ("pnpm test", "vitest run"),
    ("npm test", "vitest run"),
    ("npm run-script test", "vitest run"),
    ("yarn test", "vitest run"),
    ("bun run test", "vitest run"),
    ("npm run test -- --bail", "vitest run --bail"),
    ("pnpm run test --bail", "vitest run --bail"),
    # A script calling a script resolves too, arguments carried down.
    ("pnpm run test:unit", "vitest run --project unit"),
    ("pnpm run test:all", "eslint . && vitest run"),
])
def test_a_script_call_resolves_to_its_body(leerie, tmp_path, cmd, want):
    assert leerie._resolve_pm_script(cmd, _tree(tmp_path)) == want


@pytest.mark.parametrize("cmd", [
    "pnpm vitest run",            # not a script: the runner is named already
    "pnpm run nope",              # no such script
    "npm run loop:a",             # a cycle: stops, never loops
    "cd web && pnpm run test",    # another package.json would be the one
    "vitest run",
])
def test_what_does_not_resolve(leerie, tmp_path, cmd):
    assert leerie._resolve_pm_script(cmd, _tree(tmp_path)) is None


def test_no_package_json_resolves_nothing(leerie, tmp_path):
    assert leerie._resolve_pm_script("pnpm run test", tmp_path) is None


def test_the_replay_command_now_names_vitest(leerie, tmp_path):
    tree = _tree(tmp_path)
    assert leerie._acceptance_runner(
        "pnpm run test", leerie._RUNNER_REPORTS) is None
    cmd, env = leerie._runner_command("pnpm run test", tree,
                                      leerie._RUNNER_REPORTS)
    assert cmd == "vitest run"
    assert env["PATH"].split(os.pathsep)[0] == str(
        tree / "node_modules" / ".bin")
    spec = leerie._acceptance_report_spec(cmd, tmp_path / "r.json")
    assert spec is not None and spec[2] == "jest-json"
    assert "--reporter=json" in spec[0]
    # A command that names its runner is left exactly as written.
    assert leerie._runner_command("pnpm vitest run", tree,
                                  leerie._RUNNER_REPORTS) == (
        "pnpm vitest run", None)


FAKE_VITEST = r'''#!/usr/bin/env python3
import json, os, sys
out = next(a.split("=", 1)[1] for a in sys.argv if a.startswith("--outputFile="))
assert "--reporter=json" in sys.argv, sys.argv
root = os.getcwd()
json.dump({"testResults": [{
    "name": os.path.join(root, "src/password.test.ts"),
    "assertionResults": [
        {"fullName": "every sample has a special other than - or _",
         "status": "failed",
         "failureMessages": ["AssertionError: expected 1 to be +0"]},
        {"fullName": "length is 24", "status": "passed",
         "failureMessages": []}]}]}, open(out, "w"))
sys.exit(1)
'''


def _with_fake_vitest(tree: Path) -> None:
    b = tree / "node_modules" / ".bin"
    b.mkdir(parents=True)
    (b / "vitest").write_text(FAKE_VITEST)
    (b / "vitest").chmod((b / "vitest").stat().st_mode | stat.S_IXUSR)


def _state(leerie, tmp_path, tree):
    root = tmp_path / "state"
    (root / "runs" / "run-a" / "logs").mkdir(parents=True)
    st = leerie.State(root, "run-a", repo_root=tree)
    st.data = {"task": "x", "verbosity": "quiet"}
    st.save()
    return st


WANT = "src/password.test.ts :: every sample has a special other than - or _"


def test_the_axis_ids_are_captured_through_the_script(leerie, tmp_path):
    """`_attach_failing_ids` on a red `pnpm run test` axis — the call that
    left `baseline_failing_ids` null in all three replay passes."""
    tree = _tree(tmp_path, {"test": "vitest run"})
    _with_fake_vitest(tree)
    st = _state(leerie, tmp_path, tree)
    res = {"ran": True, "measured": True, "passed": False,
           "summary": "1 failed"}
    ok = asyncio.run(leerie._attach_failing_ids(
        res, str(tree), "pnpm run test", st, dict(leerie.DEFAULT_CAPS),
        log_path=st.run_dir / "logs" / "t.log", label="base-baseline"))
    assert ok is True
    assert res["failing_ids"] == [WANT]
    assert res["failing_id_files"] == {WANT: "src/password.test.ts"}


def test_a_nested_script_serial_capture_reaches_the_runner(leerie, tmp_path):
    tree = _tree(tmp_path, {"test": "pnpm run test:inner",
                            "test:inner": "vitest run"})
    _with_fake_vitest(tree)
    st = _state(leerie, tmp_path, tree)
    got = asyncio.run(leerie._capture_failing_test_ids(
        str(tree), "pnpm run test", st, dict(leerie.DEFAULT_CAPS),
        label="t", log_path=st.run_dir / "logs" / "t.log", timeout=60,
        serial=True))
    assert got is not None and got["kind"] == "jest-json"
    assert got["ids"] == [WANT]
    assert got["messages"][WANT].startswith("AssertionError")


def test_a_held_out_set_file_run_through_a_script_gets_its_cases(leerie,
                                                                 tmp_path):
    """The held-out runs go through the same resolution: a scoped template
    calling a package script yields the runner's per-case report (the
    held-case rule reads its messages)."""
    tree = _tree(tmp_path, {"test": "vitest run"})
    _with_fake_vitest(tree)
    (tree / ".leerie").mkdir()
    (tree / ".leerie" / "config.toml").write_text(
        'test_scoped = "pnpm run test -- {test_files}"\n')
    (tree / "src").mkdir()
    (tree / "src" / "password.test.ts").write_text("// placed set file\n")
    st = _state(leerie, tmp_path, tree)
    obs: dict = {}
    v = asyncio.run(leerie._run_acceptance_file(
        st, dict(leerie.DEFAULT_CAPS), str(tree), "src/password.test.ts",
        st.run_dir / "logs" / "a.log", "acceptance-t", cases_out=obs))
    assert v is False
    # Evaluation's case IDs are the report's own names (absolute here, as
    # validation records them too).
    (cid,) = obs["failing_cases"]
    assert cid == f"{tree}/{WANT}"
    assert obs["failing_messages"][cid].startswith("AssertionError")
