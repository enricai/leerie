"""A whole-suite forbid matches on the runner alone (DESIGN §9 *A whole-suite
forbid matches on the runner alone*).

Round-4 review M4: `_forbidden_command_hit` required a candidate to start
with the whole forbidden argv, operands included, so a whole-suite forbid of
`go test ./...` — leerie's inferred test command on every Go repo — missed
`go test -race ./...` and `go test -v -count=1 ./...`, and `pytest tests`
missed `pytest -q tests`. The reviewer's `match_probe2.py` driver is kept
below with its assertions inverted, and his 83 matcher rows
(`match_probe.py`) are pinned in `tests/fixtures/forbid_runner_options.json`
so none of their scoped runs becomes a false refusal.

The runner-head rows were measured through real bash with the real runners
(fixture key "whole-suite runner head"); the pytest ones are re-run live
here, since pytest is the one runner every host running this suite has.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

FIXTURE = json.loads((Path(__file__).parent / "fixtures"
                      / "forbid_runner_options.json").read_text())
HEAD_ROWS = FIXTURE["whole-suite runner head"]["rows"]
REVIEW_ROWS = FIXTURE["r4 reviewer matcher rows"]["rows"]
ROOTS = {"go": [], "pytest": ["tests"], "vitest": ["tests"],
         "jest": ["tests"], "mocha": ["test"], "rspec": ["spec"],
         "cargo": ["tests"]}


def _ws(cmd: str, roots: list[str]) -> list[dict]:
    return [{"command": cmd, "scope": "whole_suite", "test_roots": roots}]


def _refused(leerie, cmd: str, forbidden: list[dict]) -> bool:
    try:
        leerie._blt_shell_argv(cmd, forbidden)
    except leerie.ForbiddenByTask:
        return True
    return False


# --- the reviewer's driver (proofs4/match_probe2.py), inverted -----------------

@pytest.mark.parametrize("forb, roots, cmd", [
    ("go test ./...", [], "go test ./..."),
    ("go test ./...", [], "go test -race ./..."),
    ("go test ./...", [], "go test -v -count=1 ./..."),
    ("go test ./...", [], "go test -coverprofile=c.out ./..."),
    ("go test ./...", [], "go test ./... -race"),
    ("pytest tests", ["tests"], "pytest -q tests"),
    ("uv run pytest tests", ["tests"], "uv run pytest -x tests"),
    ("pytest", ["tests"], "pytest ."),
    ("pytest", ["tests"], "pytest ./"),
    ("pytest", [], "pytest -q"),
    ("vitest", ["src"], "vitest run ."),
])
def test_reviewer_whole_suite_spellings_are_refused(leerie, forb, roots, cmd):
    assert _refused(leerie, cmd, _ws(forb, roots)), cmd


@pytest.mark.parametrize("forb, roots, cmd", [
    ("go test ./...", [], "go test ./pkg/x"),
    ("go test ./...", [], "go test -race ./pkg/x"),
    ("go test ./...", [], "go test ."),
    ("pytest", ["tests"], "pytest tests/test_a.py"),
    ("pytest tests", ["tests"], "pytest -q tests/test_a.py"),
    ("uv run pytest tests", ["tests"], "uv run pytest -x tests/test_a.py"),
    ("mocha", ["test"], "mocha ."),
    ("cargo test", ["tests"], "cargo test ."),
])
def test_scoped_runs_still_pass(leerie, forb, roots, cmd):
    assert not _refused(leerie, cmd, _ws(forb, roots)), cmd


# --- the measured rows -------------------------------------------------------

@pytest.mark.parametrize("row", HEAD_ROWS,
                         ids=[f"{r['forbid']}|{r['cmd']}" for r in HEAD_ROWS])
def test_matcher_refuses_exactly_the_runs_the_real_runner_ran_whole(
        leerie, row):
    hit = leerie._forbidden_command_hit(
        row["cmd"], _ws(row["forbid"], ROOTS[row["project"]]))
    assert bool(hit) is row["whole"], row


def test_the_measured_rows_cover_both_outcomes_per_runner():
    """A table of only refusals (or only passes) would pin nothing."""
    for proj in ("go", "pytest", "vitest", "jest", "rspec"):
        got = {r["whole"] for r in HEAD_ROWS if r["project"] == proj}
        assert got == {True, False}, proj


@pytest.mark.parametrize("row", REVIEW_ROWS,
                         ids=[f"{r[0]}|{r[2]}" for r in REVIEW_ROWS])
def test_reviewer_matcher_rows(leerie, row):
    forb, roots, cmd, expected = row
    hit = leerie._forbidden_command_hit(cmd, _ws(forb, roots))
    assert ("refuse" if hit else "allow") == expected, row


def test_reviewer_rows_are_all_83_with_their_scoped_runs():
    assert len(REVIEW_ROWS) == 83
    assert sum(r[3] == "allow" for r in REVIEW_ROWS) >= 50


# --- the split itself --------------------------------------------------------

@pytest.mark.parametrize("forb, head, ops", [
    (["go", "test", "-v", "./..."], ["go", "test"], ["..."]),
    (["pytest", "-q", "tests/"], ["pytest"], ["tests"]),
    (["pytest", "-p", "no:cacheprovider"], ["pytest"], []),
    (["vitest", "--changed"], ["vitest", "--changed"], []),
    (["pnpm", "run", "test"], ["pnpm", "run", "test"], []),
])
def test_suite_head_splits_runner_and_operands(leerie, forb, head, ops):
    assert leerie._forbidden_suite_head(forb) == (head, ops)


def test_an_any_scope_entry_keeps_its_whole_prefix(leerie):
    """Only whole_suite entries are split: an `any` forbid of `go test
    ./...` forbids that spelling, not every `go test`."""
    any_ = [{"command": "go test ./...", "scope": "any"}]
    assert leerie._forbidden_command_hit("go test ./...", any_)
    assert not leerie._forbidden_command_hit("go test -race ./pkg/x", any_)


# --- live: pytest, run for real ---------------------------------------------

PYTEST_ROWS = [r for r in HEAD_ROWS
               if r["project"] == "pytest" and r["cmd"].startswith("pytest")]


@pytest.fixture(scope="module")
def pytest_project(tmp_path_factory):
    d = tmp_path_factory.mktemp("wsproj")
    (d / "tests").mkdir()
    for n in "ab":
        (d / "tests" / f"test_{n}.py").write_text(
            "import os\nopen(os.environ['MARK'], 'a').write("
            f"'{n}')\ndef test_{n}():\n    pass\n")
    return d


@pytest.mark.parametrize("row", PYTEST_ROWS, ids=[r["cmd"] for r in PYTEST_ROWS])
def test_pytest_rows_live(leerie, pytest_project, tmp_path, row):
    mark = tmp_path / "ran.txt"
    argv = row["cmd"].split()
    subprocess.run([sys.executable, "-m", *argv, "-p", "no:cacheprovider"],
                   cwd=pytest_project, capture_output=True, timeout=300,
                   stdin=subprocess.DEVNULL,
                   env=dict(os.environ, MARK=str(mark),
                            PYTEST_ADDOPTS="", PYTHONDONTWRITEBYTECODE="1"))
    ran = mark.read_text() if mark.exists() else ""
    whole = "a" in ran and "b" in ran
    assert whole is row["whole"], (row, ran)
    hit = leerie._forbidden_command_hit(row["cmd"],
                                        _ws(row["forbid"], ["tests"]))
    assert bool(hit) is whole, row
