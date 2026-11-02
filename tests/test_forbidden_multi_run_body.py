"""A body that runs one runner several times, and a whole-suite run against
a selecting entry (DESIGN §9 *A forbidden package script forbids what it
runs*, *A whole-suite run contains every selector's run*).

Round-5 review L1: `"test": "vitest run --project unit && vitest run
--project e2e"` derived the two `--project` entries, so under a forbidden
`pnpm test` the parts were refused while the bare `vitest run`, which runs
both, was allowed. The measured rows (`tests/fixtures/
forbid_runner_options.json`, "multi-run bodies") ran each command with the
real vitest and pnpm.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURE = Path(__file__).resolve().parent / "fixtures" \
    / "forbid_runner_options.json"


def _pc(leerie, tmp_path: Path, scripts: dict, forbid="pnpm test",
        scope="whole_suite"):
    (tmp_path / "package.json").write_text(json.dumps(
        {"name": "vp", "private": True, "scripts": scripts}))
    (tmp_path / "pnpm-lock.yaml").write_text("")
    (tmp_path / "tests").mkdir(exist_ok=True)
    res = {"process_constraints": {"forbidden_commands": [
        {"command": forbid, "scope": scope, "evidence_quote": forbid}]}}
    return leerie._process_constraints(res, None, [forbid], tmp_path)


def _hit(leerie, cmd, pc) -> bool:
    return leerie._forbidden_command_hit(cmd, pc["entries"]) is not None


TWO = "vitest run --project unit && vitest run --project e2e"


def test_the_review_body_derives_the_bare_runner(leerie, tmp_path):
    pc = _pc(leerie, tmp_path, {"test": TWO})
    assert [e["command"] for e in pc["entries"]] == ["pnpm test", "vitest"]
    for c in ["vitest run", "npx vitest run", "pnpm exec vitest run",
              "vitest run tests", "vitest --reporter=dot"]:
        assert _hit(leerie, c, pc), c
    for c in ["vitest run --project unit", "vitest run --project e2e",
              "vitest run tests/unit/a.test.mjs"]:
        assert not _hit(leerie, c, pc), c


def test_the_parts_share_only_their_common_env(leerie, tmp_path):
    # NODE_ENV, not CI: a hygiene-only env (CI, TZ) is dropped since round
    # 6 (DESIGN §9 *A script's environment is part of what it runs*).
    body = ("cross-env NODE_ENV=staging TZ=UTC vitest run --project unit && "
            "cross-env NODE_ENV=staging vitest run --project e2e")
    pc = _pc(leerie, tmp_path, {"test": "vitest run", "test:ci": body},
             forbid="pnpm run test:ci")
    assert [e["command"] for e in pc["entries"]][:2] == [
        "pnpm run test:ci", "NODE_ENV=staging vitest"]
    assert _hit(leerie, "NODE_ENV=staging vitest run", pc)
    assert not _hit(leerie, "vitest run", pc)


def test_two_runners_derive_one_entry_each(leerie, tmp_path):
    pc = _pc(leerie, tmp_path, {"test": "vitest run && jest --ci"})
    assert [e["command"] for e in pc["entries"]] == [
        "pnpm test", "vitest run", "jest --ci"]


def test_scope_any_multi_run_body_forbids_the_runner(leerie, tmp_path):
    pc = _pc(leerie, tmp_path, {"test": TWO}, scope="any")
    assert [e["command"] for e in pc["entries"]] == ["pnpm test", "vitest"]
    assert _hit(leerie, "vitest run --project unit", pc)


@pytest.mark.parametrize("forbid, cmd, hit", [
    ("vitest run --project unit", "vitest run", True),
    ("vitest run --project unit", "npx vitest", True),
    ("vitest run --project unit", "vitest run tests", True),
    ("vitest run --project unit", "vitest run --project unit", True),
    ("vitest run --project unit", "vitest run --project e2e", False),
    ("vitest run --project unit", "vitest run src/a.test.ts", False),
    ("cargo test -p p1", "cargo test", True),
    ("cargo test -p p1", "cargo test -p p2", False),
    ("jest --selectProjects unit", "jest --ci", True),
    ("jest --selectProjects unit", "jest --selectProjects e2e", False),
    ("go test ./...", "go test ./pkg/x", False),
])
def test_a_whole_suite_run_contains_a_selector_entry(leerie, forbid, cmd,
                                                     hit):
    entry = {"command": forbid, "scope": "whole_suite",
             "test_roots": ["tests"]}
    assert (leerie._forbidden_command_hit(cmd, [entry]) is not None) is hit


def test_scope_any_selector_entry_keeps_its_prefix(leerie):
    assert leerie._forbidden_command_hit(
        "vitest run", ["vitest run --project unit"]) is None


def _rows():
    return json.loads(FIXTURE.read_text())["multi-run bodies"]["rows"]


# Rows the matcher deliberately reads otherwise than "loads everything the
# body loaded" (DESIGN §9): a selector list naming several projects is a
# documented limit, and a run aimed at a test directory under the root is
# scoped.
KNOWN = {(TWO, "vitest run --project unit --project e2e"),
         ("vitest run --project unit",
          "vitest run --project unit --project e2e"),
         ("vitest run --project unit", "vitest run tests/unit")}


@pytest.mark.parametrize("i", range(39))
def test_the_measured_multi_run_rows(leerie, tmp_path, i):
    row = _rows()[i]
    pc = _pc(leerie, tmp_path, {"test": row["body"]})
    contains = set(row["body_ran"]) <= set(row["ran"])
    want = contains and (row["body"], row["cmd"]) not in KNOWN
    assert _hit(leerie, row["cmd"], pc) is want, row


def test_the_rows_are_all_there():
    assert len(_rows()) == 39
    assert {r["body"] for r in _rows()} == {
        TWO, "vitest run --project unit",
        "vitest run --project unit; vitest run --project e2e; "
        "vitest run --project slow"}
