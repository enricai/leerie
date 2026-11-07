"""A test's result is measured once per change that can affect it (DESIGN §8).

Every test here runs the real `_measure_axes` over a real git repository and a
real pytest, confined as a measurement is. Only the worker (`claude_p`) is ever
stubbed. Two things are counted: how many times the command really ran
(`runs`, a spy that calls through) and which test files it named.
"""
from __future__ import annotations

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))
from tests.conftest import relocate_into_run_repo  # noqa: E402
from tests.conftest import run_git_cwd_first_stdout  # noqa: E402

CMD = "python3 -m pytest -q -p no:cacheprovider tests"
SCOPED = "python3 -m pytest -q -p no:cacheprovider {test_files}"

PREAMBLE = ("import os, sys\n"
            "sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))\n")


def _test_file(module: str | None, name: str, ok: str = "True") -> str:
    imp = f"import {module}\n" if module else ""
    return f"{PREAMBLE}{imp}\ndef test_{name}():\n    assert {ok}\n"


class Run:
    """A repository with `hub.py` (reached by two tests), `leaf.py` (by one)
    and one test that imports nothing, on a staging tree."""

    def __init__(self, leerie, tmp_path, monkeypatch, *, forbidden=None,
                 test_other_ok: str = "True"):
        self.leerie = leerie
        monkeypatch.setattr(leerie, "_host_cpu_psi", lambda path=None: 0.0)
        repo = tmp_path / "repo"
        (repo / "tests").mkdir(parents=True)
        (repo / ".leerie").mkdir()
        run_git_cwd_first_stdout(repo, "init", "-q", "-b", "main")
        run_git_cwd_first_stdout(repo, "config", "user.email", "t@t")
        run_git_cwd_first_stdout(repo, "config", "user.name", "t")
        (repo / ".leerie" / "config.toml").write_text(
            f'test = "{CMD}"\ntest_scoped = "{SCOPED}"\n')
        (repo / ".gitignore").write_text(".leerie/\n__pycache__/\n")
        (repo / "hub.py").write_text("H = 1\n")
        (repo / "leaf.py").write_text("L = 1\n")
        (repo / "data.json").write_text("{}\n")
        (repo / "tests" / "test_hub_a.py").write_text(
            _test_file("hub", "hub_a"))
        (repo / "tests" / "test_hub_b.py").write_text(
            _test_file("hub", "hub_b"))
        (repo / "tests" / "test_leaf.py").write_text(
            _test_file("leaf", "leaf", "leaf.L == 1"))
        (repo / "tests" / "test_other.py").write_text(
            _test_file(None, "other", test_other_ok))
        run_git_cwd_first_stdout(repo, "add", "-A")
        run_git_cwd_first_stdout(repo, "commit", "-qm", "base")
        base = run_git_cwd_first_stdout(repo, "rev-parse", "HEAD")
        run_id = "fix-001-abcdef"
        run_git_cwd_first_stdout(repo, "branch", f"leerie/runs/{run_id}")
        root = tmp_path / "state"
        self.run_dir = root / "runs" / run_id
        (self.run_dir / "logs").mkdir(parents=True)
        self.staging = self.run_dir / "worktrees" / "staging"
        run_git_cwd_first_stdout(repo, "worktree", "add", "-q", str(self.staging),
             f"leerie/runs/{run_id}")
        st = leerie.State(root, run_id)
        st.data = {"task": "x", "working_branch": "main",
                   "conformance": {"_baseline": {"sha": base}},
                   "baseline_failing_ids": [], "subtask_status": {"a": "done"}}
        if forbidden:
            st.data["process_constraints"] = {"forbidden_commands": forbidden}
        st.repo_root = repo
        st.save()
        relocate_into_run_repo(leerie, st)
        self.st = st
        self.caps = {**leerie.DEFAULT_CAPS, "confirm_intermittent_repeats": 0}
        self.runs: list[str] = []
        real = leerie._run_streaming

        async def spy(cmd, **kw):
            label = str(kw.get("label") or "")
            if "install" not in label:
                self.runs.append(" ".join(cmd))
            return await real(cmd, **kw)
        monkeypatch.setattr(leerie, "_run_streaming", spy)

    def commit(self, **files: str) -> None:
        """Write and commit; a key's `__` is `/`, its `_DOT_` a dot."""
        for name, text in files.items():
            path = self.staging / name.replace("__", "/").replace("_DOT_", ".")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        run_git_cwd_first_stdout(self.staging, "add", "-A")
        run_git_cwd_first_stdout(self.staging, "commit", "-qm", "round")

    def measure(self, label: str = "m") -> dict:
        return asyncio.run(self.leerie._measure_axes(
            str(self.staging), {"tests": self.cmd()}, self.st, self.caps,
            log_path=self.run_dir / "logs" / "m.log", verbosity="quiet",
            label_prefix=label, log_prefix=label,
            failing_ids_axes={"tests"}, carry=True))["tests"]

    def cmd(self) -> str:
        return CMD

    def ran_whole(self, c: str | None = None) -> bool:
        """The last command (or `c`) is the whole one: `CMD` is a prefix of
        every narrowed form, so never a substring test."""
        c = self.runs[-1] if c is None else c
        return c.split(CMD, 1)[-1] in ("", "'", '"') if CMD in c else False

    def ran_files(self) -> set[str]:
        """The test files named by the last command that ran."""
        return {w for w in self.runs[-1].replace("'", " ").split()
                if w.startswith("tests/test_")}

    def ledger(self) -> dict:
        return self.st.data["test_ledger"]


@pytest.fixture
def run(leerie, tmp_path, monkeypatch):
    return Run(leerie, tmp_path, monkeypatch)


# --- Candidate 1: identical tree, and a failing run's report is its own -----

def test_a_failing_run_gives_its_verdict_and_its_ids_in_one_run(
        leerie, tmp_path, monkeypatch):
    """The report used to be a second whole run of the same tree: 36 minutes
    on barnacle a2a825c2, invisible in its log because it printed nothing."""
    run = Run(leerie, tmp_path, monkeypatch, test_other_ok="False")
    res = run.measure()
    assert len(run.runs) == 1
    assert res["passed"] is False
    assert res["failing_ids"] == ["tests/test_other.py :: test_other"] or (
        any("test_other" in i for i in res["failing_ids"])
        and len(res["failing_ids"]) == 1)
    assert list(res["failing_id_files"].values()) == ["tests/test_other.py"]
    # The failure is the run's own: its messages travel with it.
    assert list(res["failing_messages"]) == res["failing_ids"]


def test_an_identical_tree_is_never_measured_again(run):
    run.measure()
    n = len(run.runs)
    second = run.measure()
    assert len(run.runs) == n
    assert second["passed"] is True
    # A new commit with the same tree is the same measurement.
    run_git_cwd_first_stdout(run.staging, "commit", "-q", "--allow-empty", "-m", "empty")
    assert run.measure()["passed"] is True
    assert len(run.runs) == n


def test_the_ratchet_reads_a_tree_already_measured_without_running(run):
    """Measuring it again was 25 minutes on a2a825c2: the delivery ratchet's
    first read of the tree the final conformer had just measured."""
    res = run.measure()
    n = len(run.runs)
    got = asyncio.run(run.leerie._ratchet_test_ids(
        run.st, run.caps, str(run.staging), label="ratchet",
        log_path=run.run_dir / "logs" / "r.log"))
    assert len(run.runs) == n
    assert got["ids"] == res["failing_ids"] == []


def test_the_ratchet_carries_like_every_other_measurement(run):
    run.measure()
    run.commit(leaf_DOT_py="L = 1\n# touched\n")
    got = asyncio.run(run.leerie._ratchet_test_ids(
        run.st, run.caps, str(run.staging), label="ratchet",
        log_path=run.run_dir / "logs" / "r.log"))
    assert run.ran_files() == {"tests/test_leaf.py"}
    assert got["ids"] == []


# --- Candidate 2: a round re-runs what its diff can reach -------------------

def test_a_leaf_change_reruns_its_tests_and_carries_the_rest(run):
    first = run.measure()
    assert "carried" not in first
    assert len(run.runs) == 1 and run.ran_whole()
    tree0 = run_git_cwd_first_stdout(run.staging, "rev-parse", "HEAD^{tree}")
    run.commit(leaf_DOT_py="L = 1\n# touched\n")
    tree1 = run_git_cwd_first_stdout(run.staging, "rev-parse", "HEAD^{tree}")
    res = run.measure()
    assert len(run.runs) == 2
    assert run.ran_files() == {"tests/test_leaf.py"}
    assert res["passed"] is True
    assert res["whole_command"] == CMD
    assert res["carried"] == {"files": 3, "ran": 1, "from_trees": {tree0: 3}}
    led = run.ledger()
    assert led["tests/test_leaf.py"] == {"tree": tree1, "passed": True}
    assert led["tests/test_hub_a.py"] == {"tree": tree0, "passed": True}
    assert led["tests/test_other.py"] == {"tree": tree0, "passed": True}


def test_a_regression_in_the_leaf_is_caught_by_the_narrowed_run(run):
    run.measure()
    run.commit(leaf_DOT_py="L = 2\n")
    res = run.measure()
    assert res["passed"] is False
    assert list(res["failing_id_files"].values()) == ["tests/test_leaf.py"]


def test_a_changed_hub_reruns_everything_it_reaches_and_no_more(run):
    run.measure()
    run.commit(hub_DOT_py="H = 2\n")
    res = run.measure()
    assert run.ran_files() == {"tests/test_hub_a.py", "tests/test_hub_b.py"}
    assert res["carried"]["files"] == 2


def test_a_change_reaching_every_test_runs_the_whole_command(run):
    run.measure()
    run.commit(leaf_DOT_py="L = 1\n#\n", hub_DOT_py="H = 2\n",
               tests__test_other_DOT_py=_test_file(None, "other", "1 == 1"))
    res = run.measure()
    assert run.ran_whole()
    assert "carried" not in res


def test_a_changed_test_file_runs_itself_and_a_new_one_too(run):
    run.measure()
    run.commit(tests__test_other_DOT_py=_test_file(None, "other", "1 == 1"),
               tests__test_new_DOT_py=_test_file(None, "new"))
    run.measure()
    assert run.ran_files() == {"tests/test_other.py", "tests/test_new.py"}


@pytest.mark.parametrize("name,text", [
    ("data_DOT_json", '{"a": 1}\n'),            # a fixture no graph holds
    ("package-lock_DOT_json", "{}\n"),          # a lockfile
    ("pytest_DOT_ini", "[pytest]\n"),           # runner config
    ("conftest_DOT_py", "X = 1\n"),             # reached through the runner
    ("vitest_DOT_config_DOT_ts", "export {}\n"),
    ("main_DOT_go", "package main\n"),          # a language with no graph
    ("docs__x_DOT_md", "# x\n"),                # a test may read it
    ("untested_DOT_py", "Y = 1\n"),             # reaches no test: unknowable
])
def test_a_change_nobody_can_scope_invalidates_everything(run, name, text):
    run.measure()
    run.commit(**{name: text})
    res = run.measure()
    assert run.ran_whole(), run.runs[-1]
    assert "carried" not in res
    # And the whole measurement replaces the ledger: every file on this tree.
    tree = run_git_cwd_first_stdout(run.staging, "rev-parse", "HEAD^{tree}")
    assert {e["tree"] for e in run.ledger().values()} == {tree}


def test_a_deleted_source_file_invalidates_everything(run):
    run.measure()
    (run.staging / "leaf.py").unlink()
    run_git_cwd_first_stdout(run.staging, "add", "-A")
    run_git_cwd_first_stdout(run.staging, "commit", "-qm", "gone")
    res = run.measure()
    assert run.ran_whole()
    assert "carried" not in res


# --- A carried result: a failure re-runs, and staleness is the diff's -------

def test_a_carried_failure_is_rerun_and_still_red(leerie, tmp_path,
                                                  monkeypatch):
    run = Run(leerie, tmp_path, monkeypatch, test_other_ok="False")
    run.measure()
    run.commit(leaf_DOT_py="L = 1\n# touched\n")
    res = run.measure()
    assert run.ran_files() == {"tests/test_leaf.py", "tests/test_other.py"}
    assert res["passed"] is False
    assert any("test_other" in i for i in res["failing_ids"])
    assert run.ledger()["tests/test_other.py"]["passed"] is False


def test_a_failure_the_round_fixed_stops_being_rerun(leerie, tmp_path,
                                                     monkeypatch):
    run = Run(leerie, tmp_path, monkeypatch, test_other_ok="False")
    run.measure()
    run.commit(tests__test_other_DOT_py=_test_file(None, "other", "True"))
    assert run.measure()["passed"] is True
    run.commit(leaf_DOT_py="L = 1\n# touched\n")
    assert run.measure()["passed"] is True
    assert run.ran_files() == {"tests/test_leaf.py"}


def test_staleness_is_the_tree_diff_not_the_order_of_rounds(run):
    """A file carried from tree B is stale on any tree that differs from B
    where it reaches, however recently it passed: here the leaf's edit is
    reverted. A round counter or a "last measured" flag reads it as fresh."""
    run.measure()
    run.commit(leaf_DOT_py="L = 1\n# edit\n")   # B: test_leaf measured here
    run.measure()
    run.commit(hub_DOT_py="H = 2\n")            # C: leaves test_leaf at B
    run.measure()
    assert run.ran_files() == {"tests/test_hub_a.py", "tests/test_hub_b.py"}
    run.commit(leaf_DOT_py="L = 1\n")           # D: differs from B at leaf.py
    run.measure()
    assert run.ran_files() == {"tests/test_leaf.py"}


def test_a_rollback_to_a_measured_tree_reuses_it(run):
    run.measure()
    good = run_git_cwd_first_stdout(run.staging, "rev-parse", "HEAD")
    run.commit(leaf_DOT_py="L = 2\n")
    assert run.measure()["passed"] is False
    n = len(run.runs)
    run_git_cwd_first_stdout(run.staging, "reset", "-q", "--hard", good)
    assert run.measure()["passed"] is True
    assert len(run.runs) == n
    # The ledger still holds the red: a move on from here re-runs it.
    run.commit(hub_DOT_py="H = 2\n")
    assert run.measure()["passed"] is True
    assert "tests/test_leaf.py" in run.ran_files()


def test_a_red_run_no_case_accounts_for_is_no_ledger(run):
    """vitest exits red on an unhandled error with every test passing: that
    says nothing a file can carry, so the next measurement is whole."""
    leerie = run.leerie
    run.measure()
    tree = run_git_cwd_first_stdout(run.staging, "rev-parse", "HEAD^{tree}")
    # Control: a green run with the same cases is entered.
    cases = [{"id": "tests/test_leaf.py :: t", "file": "tests/test_leaf.py",
              "status": "passed"}]
    leerie._ledger_record(run.st, tree, {"passed": True, "failing_ids": []},
                          cases, None)
    assert run.ledger() == {"tests/test_leaf.py": {"tree": tree,
                                                   "passed": True}}
    leerie._ledger_record(run.st, tree, {"passed": False, "failing_ids": []},
                          cases, None)
    assert run.ledger() == {}
    run.commit(leaf_DOT_py="L = 1\n# touched\n")
    assert "carried" not in run.measure()
    assert run.ran_whole()


def test_an_unreadable_measured_tree_measures_whole(run):
    run.measure()
    for e in run.ledger().values():
        e["tree"] = "0" * 40
    run.commit(leaf_DOT_py="L = 1\n# touched\n")
    assert "carried" not in run.measure()
    assert run.ran_whole()


def test_measuring_without_carry_never_narrows(run):
    run.measure()
    run.commit(leaf_DOT_py="L = 1\n# touched\n")
    res = asyncio.run(run.leerie._measure_axes(
        str(run.staging), {"tests": CMD}, run.st, run.caps,
        log_path=run.run_dir / "logs" / "m.log", verbosity="quiet",
        label_prefix="x", log_prefix="x"))["tests"]
    assert run.ran_whole()
    assert "carried" not in res


# --- Forbidden commands stay in force ---------------------------------------

@pytest.fixture
def forbidding(leerie, tmp_path, monkeypatch):
    run = Run(leerie, tmp_path, monkeypatch, forbidden=[CMD])
    # A run branch that changed leaf.py and hub.py: the replacement's coupling.
    run.commit(leaf_DOT_py="L = 1\n# a\n", hub_DOT_py="H = 2\n")
    return run


def test_a_forbidden_command_is_replaced_then_narrowed_never_run(forbidding):
    run = forbidding
    first = run.measure()
    assert first["scoped_replacement"]["replaces"] == CMD
    assert run.ran_files() == {"tests/test_hub_a.py", "tests/test_hub_b.py",
                               "tests/test_leaf.py"}
    run.commit(leaf_DOT_py="L = 1\n# b\n")
    res = run.measure()
    assert run.ran_files() == {"tests/test_leaf.py"}
    assert res["scoped_replacement"]["replaces"] == CMD
    assert res["carried"]["files"] == 2
    assert not any(run.ran_whole(c) for c in run.runs)


def test_a_config_change_under_a_forbid_runs_the_replacement_not_the_suite(
        forbidding):
    run = forbidding
    run.measure()
    run.commit(pytest_DOT_ini="[pytest]\n")
    run.measure()
    assert not any(run.ran_whole(c) for c in run.runs)
    assert run.ran_files() == {"tests/test_hub_a.py", "tests/test_hub_b.py",
                               "tests/test_leaf.py"}


def test_a_scoped_runner_the_task_forbids_runs_the_whole_replacement(
        leerie, tmp_path, monkeypatch):
    """A narrowing that the task forbids is no narrowing: the selection says
    run whole, and the whole command then passes through the same gate."""
    run = Run(leerie, tmp_path, monkeypatch)
    run.measure()
    run.commit(leaf_DOT_py="L = 1\n# touched\n")
    run.st.data["process_constraints"] = {
        "forbidden_commands": ["python3 -m pytest -q -p no:cacheprovider "
                               "tests/test_leaf.py"]}
    assert asyncio.run(leerie._carried_selection(
        run.st, str(run.staging),
        run_git_cwd_first_stdout(run.staging, "rev-parse", "HEAD^{tree}"), CMD, None,
        leerie._forbidden_specs(run.st.data), log_prefix="x")) is None


# --- The round loop still sees a regression through a narrowed run ----------

def test_a_regression_is_compared_across_a_narrowed_measurement(run):
    leerie = run.leerie
    pre = run.measure()
    run.commit(leaf_DOT_py="L = 2\n")
    post = run.measure()
    assert pre["command"] != post["command"]
    got = leerie._round_axis_regressions({"tests": pre}, {"tests": post})
    assert len(got) == 1 and got[0].startswith("tests: passed before")


# --- The final conformer's real loop ----------------------------------------

def test_final_rounds_and_phase_five_measure_only_what_changed(run, leerie,
                                                               monkeypatch):
    run.st.data["required_items"] = [{"item": "x"}]
    monkeypatch.setattr(leerie, "resolve_blt", lambda root: {"test": CMD})
    monkeypatch.setattr(leerie, "_validate_conformance_result",
                        lambda r, wt: None)
    monkeypatch.setattr(leerie, "check_production_evidence", lambda r: [])
    monkeypatch.setattr(leerie, "_conformance_clean", lambda r, b: False)
    rounds = []

    async def conformer(*a, **k):
        rounds.append(1)
        if len(rounds) == 1:
            run.commit(leaf_DOT_py="L = 1\n# fixed\n")
        return {}
    monkeypatch.setattr(leerie, "claude_p", conformer)
    run.caps["conformance_rounds"] = 2
    run.commit(hub_DOT_py="H = 2\n")   # the integrated tree
    asyncio.run(leerie._run_final_conformance(
        run.run_dir, run.st, run.caps, {"conformer": "sonnet"},
        {"conformer": "low"}))
    assert run.ran_whole(run.runs[0])
    assert run.st.data["test_ledger"]
    narrowed = [c for c in run.runs[1:] if "tests/test_leaf.py" in c]
    assert narrowed, run.runs
    assert not any(run.ran_whole(c) for c in run.runs[1:])
    n = len(run.runs)
    for label in ("test-id-diff-acceptance", "test-id-diff-final"):
        asyncio.run(leerie._refresh_shipping_test_id_diff(
            run.run_dir, run.st, run.caps, label=label))
    assert len(run.runs) == n


# --- `_change_reach` over the JS graph, and what makes it safe --------------

@pytest.fixture
def js(tmp_path):
    d = tmp_path / "js"
    d.mkdir()
    run_git_cwd_first_stdout(d, "init", "-q", "-b", "main")
    run_git_cwd_first_stdout(d, "config", "user.email", "t@t")
    run_git_cwd_first_stdout(d, "config", "user.name", "t")
    files = {
        "src/hub.ts": "export const h = 1;\n",
        "src/mid.ts": "import { h } from './hub';\nexport const m = h;\n",
        "src/alone.ts": "export const a = 1;\n",
        "src/read.ts": "export const r = 1;\n",
        "src/hub.test.ts": "import { h } from './hub';\n",
        "src/mid.test.ts": "import { m } from './mid';\n",
        "src/other.test.ts": "export {};\n",
        "src/source-reader.test.ts":
            "const t = readFileSync('src/read.ts');\n",
        "test/helper.ts": "export const k = 1;\n",
        "src/helped.test.ts": "import { k } from '../test/helper';\n",
        "vitest.config.ts": "export default {};\n",
        "jest.config.ts": "export const c = 1;\n",
        "src/config-reader.test.ts": "import { c } from '../jest.config';\n",
    }
    for rel, text in files.items():
        p = d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    run_git_cwd_first_stdout(d, "add", "-A")
    run_git_cwd_first_stdout(d, "commit", "-qm", "x")
    return d, run_git_cwd_first_stdout(d, "rev-parse", "HEAD")


def test_reach_follows_the_closure(leerie, js):
    d, rev = js
    assert leerie._change_reach(d, rev, ["src/hub.ts"]) == (
        {"src/hub.test.ts", "src/mid.test.ts"}, [])


def test_reach_of_a_test_case_is_itself(leerie, js):
    d, rev = js
    assert leerie._change_reach(d, rev, ["src/other.test.ts"]) == (
        {"src/other.test.ts"}, [])


def test_reach_walks_a_test_helper(leerie, js):
    """`_coupled_test_files` skips a helper as test-shaped, so a changed
    helper couples nothing there: here it would carry a stale pass."""
    d, rev = js
    assert leerie._coupled_test_files(d, rev, ["test/helper.ts"]) == {}
    assert leerie._change_reach(d, rev, ["test/helper.ts"]) == (
        {"src/helped.test.ts"}, [])


def test_reach_sees_a_test_that_reads_a_source_as_text(leerie, js):
    d, rev = js
    assert leerie._change_reach(d, rev, ["src/read.ts"]) == (
        {"src/source-reader.test.ts"}, [])


@pytest.mark.parametrize("path", [
    "vitest.config.ts",           # the runner's own config, in the graph
    "jest.config.ts",             # and one a test imports: still everything
    "src/alone.ts",               # reaches no test
    "package.json", "pnpm-lock.yaml", "tsconfig.json", ".env",
    "src/gone.ts",                # not there: deleted
    "src/fixtures/a.json", "README.md", "main.go",
])
def test_reach_says_it_does_not_know(leerie, js, path):
    d, rev = js
    assert leerie._change_reach(d, rev, [path])[1] == [path]


# --- The report request ------------------------------------------------------

def test_vitest_keeps_its_output_beside_the_report(leerie, tmp_path):
    cmd, _env, kind = leerie._acceptance_report_spec(
        "npx vitest run a.test.ts", tmp_path / "r", beside_output=True)
    assert "--reporter=default" in cmd and "--reporter=json" in cmd
    assert "--outputFile.json=" in cmd
    assert kind == "jest-json"
    # The B* reruns and held-out sets read no output and keep their form.
    cmd, _env, _kind = leerie._acceptance_report_spec(
        "npx vitest run a.test.ts", tmp_path / "r")
    assert "--reporter=default" not in cmd


@pytest.mark.parametrize("cmd", ["go test ./...", "cargo test"])
def test_a_runner_whose_report_is_its_stdout_runs_as_written(leerie, tmp_path,
                                                             cmd):
    assert leerie._acceptance_report_spec(
        cmd, tmp_path / "r", beside_output=True) is None


@pytest.mark.parametrize("script,inline", [
    ({"test": "vitest run"}, True),
    ({"test": "vitest run", "pretest": "tsc"}, False),
    ({"test": "vitest run", "posttest": "x"}, False),
])
def test_a_script_call_keeps_its_hooks(leerie, tmp_path, monkeypatch, script,
                                       inline):
    """The body runs where the report is asked for: a `pretest` hook would
    be skipped, so that verdict's run stays as written."""
    import json
    (tmp_path / "package.json").write_text(json.dumps({"scripts": script}))
    monkeypatch.setattr(leerie, "_measure_dir", lambda st, tree: tmp_path / "m")
    got = leerie._axis_report_request(None, str(tmp_path), "pnpm run test",
                                      "l")
    assert (got is not None) is inline
    if inline:
        assert got["source"] == "vitest run"
        assert "--outputFile.json=" in got["run_cmd"]
        assert (tmp_path / "node_modules" / ".bin").as_posix() in \
            got["env"]["PATH"]
