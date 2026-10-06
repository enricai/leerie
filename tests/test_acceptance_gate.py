"""Held-out acceptance tests (DESIGN §8 *Held-out acceptance tests*).

Every verification layer before this graded a fix against tests the fixing
run wrote itself; on both v0.36.0 repeat runs the shipped fix failed on the
report's own input and 44 review calls missed it. An `acceptance_writer`
writes test sets from the report alone at the validity base; Python keeps
only files whose exit codes discriminate; after integration the sets gate the
tree, with at most two high-effort repair rounds that are shown case NAMES
only, a rollback if the repair turns the test axis red, and a non-blocking
residual handed to the next run.

These tests run the REAL validation and gate code against a real git repo
with a real pytest command (`test_scoped` in `.leerie/config.toml`); only
`claude_p` is stubbed, and the stubs write files the way the workers would.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.conftest import run_git_cwd_first_stdout as _git

BUGGY = "def add(a, b):\n    return a - b\n"
FIXED = "def add(a, b):\n    return a + b\n"
DEFECT_TEST = ("from calc import add\n\n"
               "def test_add_sums():\n    assert add(2, 3) == 5\n")
CONTROL_TEST = ("from calc import add\n\n"
                "def test_add_zero():\n    assert add(0, 0) == 0\n")
NONDISCRIMINATING = ("from calc import add\n\n"
                     "def test_callable():\n    assert callable(add)\n")
FAILING_CONTROL = ("def test_always_fails():\n    assert False\n")

MODELS = {"acceptance_writer": "sonnet", "conformer": "sonnet"}
EFFORTS = {"acceptance_writer": "medium", "conformer": "low"}


@pytest.fixture(autouse=True)
def _clear_deps_memo(leerie):
    leerie._DEPS_INSTALLED.clear()
    yield
    leerie._DEPS_INSTALLED.clear()


def _repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@x")
    _git(repo, "config", "user.name", "t")
    (repo / "calc.py").write_text(BUGGY)
    (repo / "conftest.py").write_text(
        "import sys, pathlib\nsys.path.insert(0, str(pathlib.Path(__file__).parent))\n")
    (repo / ".leerie").mkdir()
    (repo / ".leerie" / "config.toml").write_text(
        'test_scoped = "python3 -m pytest -q -p no:cacheprovider {test_files}"\n'
        'test = "python3 -m pytest -q -p no:cacheprovider"\n')
    (repo / "report.md").write_text("add(2, 3) returns -1; it must return 5\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    return repo, _git(repo, "rev-parse", "HEAD")


def _st(leerie, tmp_path, repo, head, **data):
    root = tmp_path / "state"
    run_dir = root / "runs" / "run-a"
    run_dir.mkdir(parents=True)
    (run_dir / "logs").mkdir()
    st = leerie.State(root, "run-a", repo_root=repo)
    st.data = {"task": "fix the bug in report.md", "verbosity": "quiet",
               "repo_state_before_planning": {"head": head},
               "defect_scope": {"applicable": True, "sites": [],
                                "defect_shape": "add subtracts"},
               "worker_count": 0}
    st.data.update(data)
    st.save()
    return st


def _writer_stub(leerie, monkeypatch, files_by_set):
    """A stub acceptance_writer: writes the files for its set index into its
    cwd and returns the declared list."""
    seen = []

    async def fake_claude_p(**kw):
        assert kw["schema_key"] == "acceptance_writer"
        k = int(kw["sid"].split("-")[-1])
        seen.append(kw)
        decl = []
        for rel, spec in files_by_set.get(k, {}).items():
            kind, text, cases = spec[:3]
            mode = spec[3] if len(spec) > 3 else "assertion"
            p = Path(kw["cwd"]) / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text)
            decl.append({"path": rel, "kind": kind, "cases": cases,
                         "failure_mode": mode})
        return {"files": decl}

    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    return seen


def _caps(leerie, n):
    caps = dict(leerie.DEFAULT_CAPS)
    caps["acceptance_sets"] = n
    caps["worker_timeout_sec"] = 120
    return caps


# --- writing + mechanical validity -------------------------------------------

def test_valid_set_keeps_only_discriminating_defect_files(leerie, tmp_path,
                                                         monkeypatch):
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head)
    _writer_stub(leerie, monkeypatch, {1: {
        "acc/test_defect_sum.py": ("defect", DEFECT_TEST, ["add sums two ints"]),
        "acc/test_defect_weak.py": ("defect", NONDISCRIMINATING, ["callable"]),
        "acc/test_control_zero.py": ("control", CONTROL_TEST, ["zero stays zero"]),
    }})
    acc = asyncio.run(leerie.phase_acceptance_write(
        st.data["task"], st, _caps(leerie, 1), MODELS, EFFORTS))
    assert acc["validity_base"] == head
    (s,) = acc["sets"]
    assert s["defect_files"] == ["acc/test_defect_sum.py"]   # weak one dropped
    assert s["control_files"] == ["acc/test_control_zero.py"]
    assert s["cases"]["acc/test_defect_sum.py"] == ["add sums two ints"]
    stored = Path(s["dir"]) / "acc" / "test_defect_sum.py"
    assert stored.read_text() == DEFECT_TEST
    assert "acceptance" in str(stored.relative_to(st.run_dir))
    # The disposable worktree is gone and the user's checkout is untouched.
    assert not (st.run_dir / "worktrees" / "acceptance-1").exists()
    assert _git(repo, "status", "--porcelain") == ""


def test_failing_control_or_no_discriminating_defect_discards_the_set(
        leerie, tmp_path, monkeypatch):
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head)
    _writer_stub(leerie, monkeypatch, {
        1: {"a/test_defect.py": ("defect", DEFECT_TEST, ["x"]),
            "a/test_control.py": ("control", FAILING_CONTROL, ["y"])},
        2: {"b/test_defect.py": ("defect", NONDISCRIMINATING, ["z"])},
    })
    acc = asyncio.run(leerie.phase_acceptance_write(
        st.data["task"], st, _caps(leerie, 2), MODELS, EFFORTS))
    assert acc["sets"] == []


def test_unmeasurable_file_discards_the_set_with_an_honest_reason(
        leerie, tmp_path, monkeypatch, capsys):
    """A file the test command cannot run (here: not test-shaped, so the
    scoped template renders nothing) must never read as pass or fail."""
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head)
    _writer_stub(leerie, monkeypatch, {1: {
        "acc/helper_check.py": ("defect", DEFECT_TEST, ["x"])}})
    acc = asyncio.run(leerie.phase_acceptance_write(
        st.data["task"], st, _caps(leerie, 1), MODELS, EFFORTS))
    assert acc["sets"] == []
    assert "could not run acc/helper_check.py" in capsys.readouterr().out


def test_writer_sees_the_report_contract_and_examples(leerie, tmp_path,
                                                     monkeypatch):
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head)
    st.data["defect_scope"]["ground_truth"] = {"inline_examples": [
        {"literal": "add(2, 3)", "site_identifying": False,
         "trigger_tokens": [], "site_tokens": []}]}
    seen = _writer_stub(leerie, monkeypatch, {1: {}})
    asyncio.run(leerie.phase_acceptance_write(
        st.data["task"], st, _caps(leerie, 1), MODELS, EFFORTS))
    up = seen[0]["user_prompt"]
    assert "add subtracts" in up and "add(2, 3)" in up and "report.md" in up
    assert seen[0]["effort"] == "medium" and seen[0]["autonomous"] is True


@pytest.mark.parametrize("scope,skip,reason", [
    ({"applicable": False}, False, "not applicable"),
    ({"applicable": True, "sites": []}, True, "--skip-acceptance-check"),
])
def test_skips_are_recorded(leerie, tmp_path, scope, skip, reason):
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head, defect_scope=scope,
             skip_acceptance_check=skip)
    acc = asyncio.run(leerie.phase_acceptance_write(
        "t", st, _caps(leerie, 1), MODELS, EFFORTS))
    assert reason in acc["skipped"]


def test_validity_base_prefers_the_earliest_same_task_sibling(leerie, tmp_path):
    repo, head = _repo(tmp_path)
    (repo / "calc.py").write_text(FIXED)
    _git(repo, "commit", "-qam", "later")
    later = _git(repo, "rev-parse", "HEAD")
    st = _st(leerie, tmp_path, repo, later)
    for name, started, h in (("run-old", "2026-01-01", head),
                             ("run-mid", "2026-02-01", later)):
        d = st.run_dir.parent / name
        d.mkdir()
        (d / "state.json").write_text(json.dumps({
            "task": st.data["task"], "started_at": started,
            "repo_state_before_planning": {"head": h}}))
    assert leerie._acceptance_validity_base(st) == head
    # A sibling with no recorded start time cannot be ranked earliest ("" would
    # sort first): it is skipped, not chosen.
    d = st.run_dir.parent / "run-undated"
    d.mkdir()
    (d / "state.json").write_text(json.dumps({
        "task": st.data["task"], "repo_state_before_planning": {"head": later}}))
    assert leerie._acceptance_validity_base(st) == head
    # A different task is ignored.
    st.data["task"] = "another task"
    assert leerie._acceptance_validity_base(st) == later


# --- the gate on the integrated tree -----------------------------------------

def _make_sets(leerie, st, n):
    sets = []
    for k in range(1, n + 1):
        d = st.run_dir / "acceptance" / f"set-{k}"
        (d / "acc").mkdir(parents=True)
        (d / "acc" / f"test_defect_{k}.py").write_text(DEFECT_TEST)
        (d / "acc" / f"test_control_{k}.py").write_text(CONTROL_TEST)
        sets.append({"index": k, "valid": True, "dir": str(d),
                     "defect_files": [f"acc/test_defect_{k}.py"],
                     "control_files": [f"acc/test_control_{k}.py"],
                     "cases": {f"acc/test_defect_{k}.py": [f"case from set {k}"],
                               f"acc/test_control_{k}.py": ["control"]}})
    return sets


def _staging(st, repo):
    staging = st.run_dir / "worktrees" / "staging"
    _git(repo, "worktree", "add", "-q", "-b", "leerie/runs/x", str(staging))
    return staging


def _tree_snapshot(tree: Path) -> list[tuple[str, bytes]]:
    return sorted((str(p.relative_to(tree)), p.read_bytes())
                  for p in tree.rglob("*") if p.is_file() and ".git" not in p.parts)


def test_evaluation_never_touches_the_tree_and_never_overwrites(
        leerie, tmp_path):
    """Round-4 M1: sets copied into staging survived a crash mid-run (no
    `finally` survives a SIGKILL). Each evaluation now runs in a disposable
    worktree at the tree's HEAD; the tree itself is never written."""
    repo, head = _repo(tmp_path)
    (repo / ".leerie" / "config.toml").write_text(
        'test_scoped = "python3 -m pytest -q {test_files}"\n')
    st = _st(leerie, tmp_path, repo, head)
    staging = _staging(st, repo)
    (staging / "acc").mkdir()
    (staging / "acc" / "test_defect_1.py").write_text("# the run's own file\n")
    _git(staging, "add", "-A")
    _git(staging, "commit", "-qm", "run's own test")
    sets = _make_sets(leerie, st, 2)
    before = _tree_snapshot(staging)
    seen_trees = []
    real = leerie._run_acceptance_file

    async def spy(st_, caps, tree, rel, *a, **k):
        seen_trees.append(tree)
        assert not (staging / rel).exists() or rel == "acc/test_defect_1.py"
        return await real(st_, caps, tree, rel, *a, **k)
    leerie_mp = pytest.MonkeyPatch()
    leerie_mp.setattr(leerie, "_run_acceptance_file", spy)
    try:
        res = asyncio.run(leerie._evaluate_acceptance_sets(
            st, _caps(leerie, 2), str(staging), sets, "t"))
    finally:
        leerie_mp.undo()
    # Set 1 collides with the run's own committed file: it could not run
    # that file, so it is unmeasured — never a pass (round-1 M1/M2).
    assert [(r["passed"], r["unmeasured"]) for r in res] == [
        (False, True), (False, False)]
    assert _tree_snapshot(staging) == before    # not a byte changed
    assert seen_trees and all(t != str(staging) for t in seen_trees)
    assert not (st.run_dir / "worktrees" / "acceptance-eval").exists()


def test_a_crashed_evaluations_worktree_is_replaced(leerie, tmp_path):
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head)
    staging = _staging(st, repo)
    stale = st.run_dir / "worktrees" / "acceptance-eval"
    _git(repo, "worktree", "add", "-q", "--detach", str(stale), head)
    (stale / "acc").mkdir()
    (stale / "acc" / "test_defect_1.py").write_text("# left by a crash\n")
    _commit_fix(staging)
    res = asyncio.run(leerie._evaluate_acceptance_sets(
        st, _caps(leerie, 1), str(staging), _make_sets(leerie, st, 1), "t"))
    assert res == [{"index": 1, "passed": True, "unmeasured": False,
                    "failing_files": []}]
    assert not stale.exists()


def test_each_evaluation_installs_into_its_fresh_worktree(
        leerie, tmp_path, monkeypatch):
    """The evaluation worktree's path is reused; a stale install memo would
    skip installing into the second, fresh checkout."""
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head)
    staging = _staging(st, repo)
    real = leerie._ensure_worktree_deps
    installs = []

    async def spy(tree, *a, **k):
        before = len(leerie._DEPS_INSTALLED)
        ok = await real(tree, *a, **k)
        installs.append(len(leerie._DEPS_INSTALLED) > before)
        return ok
    monkeypatch.setattr(leerie, "_ensure_worktree_deps", spy)
    sets = _make_sets(leerie, st, 1)
    results = [asyncio.run(leerie._evaluate_acceptance_sets(
        st, _caps(leerie, 1), str(staging), sets, label))
        for label in ("one", "two")]
    assert installs == [True, True]
    # The spy passes the install's verdict on, so both runs measured.
    assert all(not r[0]["unmeasured"] for r in results)


def _failing_recipe(st, *, fail: bool):
    st.data["provision"] = {"recipe": [{
        "kind": "install",
        "command": ["bash", "-c", "exit 3" if fail else "true"]}]}


def test_a_failed_install_is_no_evidence(leerie, tmp_path):
    """Round-5 M3: verdicts are by exit code, so a dependency install that
    failed in the evaluation worktree would read as every test failing."""
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head)
    staging = _staging(st, repo)
    _commit_fix(staging)
    sets = _make_sets(leerie, st, 3)
    _failing_recipe(st, fail=True)
    res = asyncio.run(leerie._evaluate_acceptance_sets(
        st, _caps(leerie, 3), str(staging), sets, "t"))
    assert all(r["unmeasured"] for r in res)
    assert not leerie._acceptance_majority_fails(res)
    _failing_recipe(st, fail=False)
    res = asyncio.run(leerie._evaluate_acceptance_sets(
        st, _caps(leerie, 3), str(staging), sets, "t2"))
    assert all(r["passed"] for r in res)


@pytest.mark.parametrize("failure", [
    {"command": ["bash", "-c", "exit 1"]},                       # non-zero
    {"command": ["bash", "-c", "sleep 5"], "timeout_s": 1},      # timeout
    {"command": ["no-such-binary-xyz"]},                         # raises
])
@pytest.mark.parametrize("kind,want", [("build", True), ("install", False)])
def test_only_a_failed_install_counts(leerie, tmp_path, failure, kind, want):
    """Round-6: a fix that breaks the build is evidence against the fix,
    not a missing dependency — whichever way the build step fails."""
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head)
    st.data["provision"] = {"recipe": [
        {"kind": "install", "command": ["bash", "-c", "true"]},
        {"kind": kind, **failure}]}
    assert asyncio.run(leerie._ensure_worktree_deps(
        str(repo), st, _caps(leerie, 1),
        log_path=st.run_dir / "logs" / "d.log", verbosity="quiet")) is want


def test_an_evaluation_that_cannot_be_set_up_is_no_evidence(
        leerie, tmp_path, monkeypatch):
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head)
    staging = _staging(st, repo)
    real = leerie.subprocess.run

    def eagain(argv, *a, **k):
        if "worktree" in argv and "add" in argv:
            raise BlockingIOError(11, "Resource temporarily unavailable")
        return real(argv, *a, **k)
    monkeypatch.setattr(leerie.subprocess, "run", eagain)
    res = asyncio.run(leerie._evaluate_acceptance_sets(
        st, _caps(leerie, 2), str(staging), _make_sets(leerie, st, 2), "t"))
    assert all(r["unmeasured"] for r in res)


def test_ensure_worktree_deps_reports_failure(leerie, tmp_path):
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head)
    kw = dict(log_path=st.run_dir / "logs" / "d.log", verbosity="quiet")
    _failing_recipe(st, fail=True)
    assert asyncio.run(leerie._ensure_worktree_deps(
        str(repo), st, _caps(leerie, 1), **kw)) is False
    leerie._DEPS_INSTALLED.clear()
    _failing_recipe(st, fail=False)
    assert asyncio.run(leerie._ensure_worktree_deps(
        str(repo), st, _caps(leerie, 1), **kw)) is True


def test_a_set_that_cannot_be_placed_is_no_evidence(leerie, tmp_path,
                                                    monkeypatch):
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head)
    staging = _staging(st, repo)
    real = leerie.shutil.copy2
    calls = []

    def full(src, dst, *a, **k):
        calls.append(dst)
        if len(calls) == 1:
            raise OSError(28, "No space left on device")
        return real(src, dst, *a, **k)
    monkeypatch.setattr(leerie.shutil, "copy2", full)
    _commit_fix(staging)
    res = asyncio.run(leerie._evaluate_acceptance_sets(
        st, _caps(leerie, 2), str(staging), _make_sets(leerie, st, 2), "t"))
    assert [(r["passed"], r["unmeasured"]) for r in res] == [
        (False, True), (True, False)]


def test_sets_are_isolated_from_one_another(leerie, tmp_path):
    """Two sets carrying the same helper path: each set's files are gone
    before the next set's go in, or the second would collide."""
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head)
    staging = _staging(st, repo)
    _commit_fix(staging)
    sets = _make_sets(leerie, st, 2)
    for s_ in sets:
        (Path(s_["dir"]) / "acc" / "helper_vals.py").write_text("X = 1\n")
        s_["support_files"] = ["acc/helper_vals.py"]
    res = asyncio.run(leerie._evaluate_acceptance_sets(
        st, _caps(leerie, 2), str(staging), sets, "t"))
    assert [(r["passed"], r["unmeasured"]) for r in res] == [
        (True, False), (True, False)]


def test_helper_beside_the_tests_travels_with_the_set(leerie, tmp_path,
                                                     monkeypatch):
    """Round-3 H2: a helper under a test-shaped directory (`tests/`) was
    dropped as "a test file", so a correct fix failed the set. Every
    undeclared new file travels; caches and bytecode never do, and nothing
    that existed before the writer ran (the dependency install's output)
    is the writer's."""
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head)
    helper_test = ("from tests.acc_helpers import EXPECTED\n"
                   "from calc import add\n\n"
                   "def test_add_sums():\n    assert add(2, 3) == EXPECTED\n")

    async def fake_deps(tree, *a, **k):
        (Path(tree) / "provisioned.txt").write_text("from the install\n")
        return True
    monkeypatch.setattr(leerie, "_ensure_worktree_deps", fake_deps)

    async def fake_claude_p(**kw):
        wt = Path(kw["cwd"])
        (wt / "tests").mkdir()
        (wt / "tests" / "__init__.py").write_text("")
        (wt / "tests" / "acc_helpers.py").write_text("EXPECTED = 5\n")
        (wt / "tests" / "test_defect_h.py").write_text(helper_test)
        # What running the test leaves behind.
        subprocess.run([sys.executable, "-m", "pytest", "-q",
                        "tests/test_defect_h.py"], cwd=wt, capture_output=True)
        return {"files": [{"path": "tests/test_defect_h.py", "kind": "defect",
                           "cases": ["sums via helper"]}]}

    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    acc = asyncio.run(leerie.phase_acceptance_write(
        st.data["task"], st, _caps(leerie, 1), MODELS, EFFORTS))
    (s,) = acc["sets"]
    assert s["support_files"] == ["tests/__init__.py", "tests/acc_helpers.py"]
    staging = _staging(st, repo)
    _commit_fix(staging)
    # The run's own tests already ran in staging, leaving bytecode there.
    subprocess.run([sys.executable, "-c", "import calc"], cwd=staging,
                   env={**os.environ, "PYTHONPATH": str(staging)})
    res = asyncio.run(leerie._evaluate_acceptance_sets(
        st, _caps(leerie, 1), str(staging), acc["sets"], "t"))
    assert res == [{"index": 1, "passed": True, "unmeasured": False,
                    "failing_files": []}]


def test_cache_and_provision_paths(leerie):
    cache = leerie._acceptance_is_cache_path
    assert cache("__pycache__/calc.cpython-314.pyc")
    assert cache(".pytest_cache/v/cache/lastfailed")
    assert cache("tests/x.pyc")
    assert cache(".venv/lib/site.py") and cache("web/node_modules/a/b.js")
    assert not cache("tests/acc_helpers.py") and not cache("tests/conftest.py")
    # Too broad to drop (round-4 L3): a fixture may live under it.
    assert not cache("tests/.cache/fixture.json")


def test_a_runner_cache_in_the_tree_is_never_touched(
        leerie, tmp_path):
    """Round-3 M5: pytest's cache recorded the hidden tests' ids in the
    tree a fixer works in. Evaluation no longer runs in that tree."""
    repo, head = _repo(tmp_path)
    (repo / ".leerie" / "config.toml").write_text(
        'test_scoped = "python3 -m pytest -q {test_files}"\n')
    st = _st(leerie, tmp_path, repo, head)
    staging = _staging(st, repo)
    sets = _make_sets(leerie, st, 2)
    asyncio.run(leerie._evaluate_acceptance_sets(
        st, _caps(leerie, 2), str(staging), sets, "t"))
    assert not (staging / ".pytest_cache").exists()
    own = staging / ".pytest_cache" / "v" / "cache" / "lastfailed"
    own.parent.mkdir(parents=True)
    own.write_text('{"test_own.py::test_x": true}')
    asyncio.run(leerie._evaluate_acceptance_sets(
        st, _caps(leerie, 2), str(staging), sets, "t"))
    assert own.read_text() == '{"test_own.py::test_x": true}'
    left = subprocess.run(["git", "-C", str(staging), "ls-files", "--others"],
                          capture_output=True, text=True).stdout
    assert "test_defect_" not in left


_JUNIT_PASS = ('<testsuites><testsuite tests="1" errors="0">'
               '<testcase classname="t" name="test_a"/></testsuite>'
               '</testsuites>')
_JUNIT_NONE = '<testsuites><testsuite tests="0"/></testsuites>'
_JUNIT_SKIPPED = ('<testsuites><testsuite tests="1"><testcase classname="t" '
                  'name="test_a"><skipped/></testcase></testsuite>'
                  '</testsuites>')
# pytest's fixed messages (src/_pytest/junitxml.py): "collection failure"
# for a file it could not collect, 'failed on setup with "…"' for a setup
# error on a case that ran.
_JUNIT_COLLECT = ('<testsuites><testsuite tests="1" errors="1"><testcase '
                  'classname="" name="t"><error message="collection failure"/>'
                  '</testcase></testsuite></testsuites>')
_JUNIT_COLLECT_PREFIXED = (
    '<testsuites><testsuite tests="1" errors="1"><testcase classname="pfx" '
    'name="t"><error message="collection failure"/></testcase></testsuite>'
    '</testsuites>')
_JUNIT_SETUP = ('<testsuites><testsuite tests="1" errors="1"><testcase '
                'classname="t" name="test_a"><error message='
                '\'failed on setup with "boom"\'/></testcase></testsuite>'
                '</testsuites>')
_JEST_PASS = json.dumps({"testResults": [{"status": "passed",
    "assertionResults": [{"status": "passed"}, {"status": "failed"}]}]})
_JEST_NONE = json.dumps({"testResults": []})
_JEST_BROKEN = json.dumps({"testResults": [{"status": "failed",
    "assertionResults": [], "message": "Test suite failed to run"}]})


@pytest.mark.parametrize("kind,text,want", [
    ("junit", _JUNIT_PASS, {"executed": 1, "collection_error": False}),
    ("junit", _JUNIT_NONE, {"executed": 0, "collection_error": False}),
    ("junit", _JUNIT_SKIPPED, {"executed": 0, "collection_error": False}),
    ("junit", _JUNIT_COLLECT, {"executed": 0, "collection_error": True}),
    # `--junit-prefix` fills in the classname; the message still says it.
    ("junit", _JUNIT_COLLECT_PREFIXED,
     {"executed": 0, "collection_error": True}),
    # A setup error sits on a real, named case: a test that ran.
    ("junit", _JUNIT_SETUP, {"executed": 1, "collection_error": False}),
    ("jest-json", _JEST_PASS, {"executed": 2, "collection_error": False}),
    ("jest-json", _JEST_NONE, {"executed": 0, "collection_error": False}),
    ("jest-json", _JEST_BROKEN, {"executed": 0, "collection_error": True}),
    ("junit", "<not xml", None),
    ("jest-json", "{not json", None),
])
def test_runner_reports_are_read_mechanically(leerie, tmp_path, kind, text,
                                              want):
    p = tmp_path / "report"
    p.write_text(text)
    assert leerie._parse_runner_report(kind, p) == want
    assert leerie._parse_runner_report(kind, tmp_path / "absent") is None


@pytest.mark.parametrize("cmd,placed", [
    ("cd web && npx jest acc/a.test.js", True),
    ("npx vitest run acc/a.test.ts", True),
    ("npx jest acc/a.test.js | tee log", False),
    ("npx jest acc/a.test.js > out.txt", False),
    ("npx jest acc/a.test.js && echo done", False),
    # The runner also appears after a separator: the flags would reach echo.
    ("npx jest acc/a.test.js; echo jest", False),
    ("npm test -- acc/a.test.js", False),
    ("(cd web && npx jest acc/a.test.js)", False),
    ("npx jest -- acc/a.test.js", False),
    # The wrapper names the runner as a package before its own `--`: the
    # runner proper comes after it, so the flags reach it (#284 review).
    ("npx -p jest -- jest acc/a.test.js", True),
])
def test_report_flags_are_placed_only_where_they_reach_the_runner(
        leerie, tmp_path, cmd, placed):
    spec = leerie._acceptance_report_spec(cmd, tmp_path / "r.json")
    assert (spec is not None) is placed
    if placed:
        assert spec[0].startswith(cmd) and "--outputFile=" in spec[0]
        assert spec[1] is None and spec[2] == "jest-json"


def test_pytest_is_asked_for_junit_through_its_environment(
        leerie, tmp_path, monkeypatch):
    monkeypatch.setenv("PYTEST_ADDOPTS", "-x")
    cmd = "cd sub && python3 -m pytest -q acc/test_a.py | cat"
    run_cmd, env, kind = leerie._acceptance_report_spec(
        cmd, tmp_path / "r.xml")
    assert run_cmd == cmd and kind == "junit"
    assert env["PYTEST_ADDOPTS"] == f"-x --junitxml={tmp_path / 'r.xml'}"


@pytest.mark.parametrize("cmd,placed", [
    ("python3 -m pytest -q --junitxml=own.xml {f}", True),
    ("pytest --junit-xml own.xml {f}", True),
    # Our flag cannot be placed after the runner's own: no report at all,
    # rather than an environment request the command would override.
    ("python3 -m pytest --junitxml=own.xml {f} | tee log", False),
    # Appended after `--` it is a file argument, after `)` a syntax error:
    # either turned a working template into a discarded set (#283 review).
    ("pytest --junitxml=own.xml -- {f}", False),
    ("(cd sub && python3 -m pytest --junitxml=own.xml {f})", False),
    # A wrapper's own `--` before the runner proper is not the runner's.
    ("uv run --with pytest -- pytest --junitxml=own.xml {f}", True),
])
def test_a_command_naming_its_own_junit_path_gets_ours_appended(
        leerie, tmp_path, monkeypatch, cmd, placed):
    """The template's own --junitxml beats PYTEST_ADDOPTS (measured: pytest
    writes only the command line's), so the request moves to the end of
    the command, where pytest keeps the last one given."""
    monkeypatch.delenv("PYTEST_ADDOPTS", raising=False)
    cmd = cmd.format(f="acc/test_a.py")
    spec = leerie._acceptance_report_spec(cmd, tmp_path / "r.xml")
    assert (spec is not None) is placed
    if placed:
        assert spec == (f"{cmd} --junitxml={tmp_path / 'r.xml'}", None,
                        "junit")


def test_an_appended_junit_path_is_the_report_pytest_writes(
        leerie, tmp_path):
    """Behavioural: run the spec'd command and read the report back."""
    (tmp_path / "test_a.py").write_text("def test_a():\n    assert True\n")
    cmd = ("python3 -m pytest -q -p no:cacheprovider "
           "--junitxml=own.xml test_a.py")
    run_cmd, env, kind = leerie._acceptance_report_spec(
        cmd, tmp_path / "r.xml")
    subprocess.run(run_cmd, shell=True, cwd=tmp_path, env=env,
                   capture_output=True, check=False)
    assert leerie._parse_runner_report(kind, tmp_path / "r.xml") == {
        "executed": 1, "collection_error": False}


def _validate(leerie, st, repo, rel, mode="assertion"):
    return asyncio.run(leerie._run_acceptance_file(
        st, _caps(leerie, 1), str(repo), rel, st.run_dir / "logs" / "v.log",
        "v", validating=True, failure_mode=mode))


def test_a_file_whose_tests_all_skip_is_no_verdict_while_validating(
        leerie, tmp_path):
    """Exit code 0, but nothing ran: only the report shows it. As a control
    it used to be accepted, proving nothing."""
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head)
    (repo / "test_probe.py").write_text(
        "import pytest\n\n@pytest.mark.skip\ndef test_x():\n    pass\n")
    assert _validate(leerie, st, repo, "test_probe.py") is None
    # Evaluating a fix is unchanged: exit code alone.
    assert asyncio.run(leerie._run_acceptance_file(
        st, _caps(leerie, 1), str(repo), "test_probe.py",
        st.run_dir / "logs" / "v.log", "v")) is True


_IMPORT_DEFECT = ("from calc import mul\n\n"
                  "def test_mul():\n    assert mul(2, 3) == 6\n")


@pytest.mark.parametrize("mode,want", [("import", False),
                                       ("assertion", None)])
def test_a_declared_import_defect_counts_as_failing(leerie, tmp_path, mode,
                                                    want):
    """The report's defect is that `mul` does not exist: a defect file that
    cannot load on the base is showing it — but only when the writer said
    so (Language-to-JSON: the writer, not Python, reads the report)."""
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head)
    (repo / "test_probe.py").write_text(_IMPORT_DEFECT)
    assert _validate(leerie, st, repo, "test_probe.py", mode) is want


def test_a_junit_prefix_does_not_turn_a_load_failure_into_a_run(
        leerie, tmp_path, monkeypatch):
    """`--junit-prefix` gave pytest's collection-error case a classname, so
    the old empty-classname rule read it as an executed test, and an
    assertion-mode defect file that could not even load counted as
    failing on the base."""
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head)
    monkeypatch.setenv("PYTEST_ADDOPTS", "--junit-prefix=pfx")
    (repo / "test_probe.py").write_text(_IMPORT_DEFECT)
    assert _validate(leerie, st, repo, "test_probe.py") is None


def _import_writer(leerie, monkeypatch, files):
    async def fake_claude_p(**kw):
        wt = Path(kw["cwd"])
        decl = []
        for rel, (text, mode) in files.items():
            (wt / rel).parent.mkdir(parents=True, exist_ok=True)
            (wt / rel).write_text(text)
            if mode:
                decl.append({"path": rel, "kind": "defect",
                             "cases": ["mul exists"], "failure_mode": mode})
        return {"files": decl}
    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)


@pytest.mark.parametrize("files", [
    # The test file itself does not parse.
    {"acc/test_defect_mul.py": ("from calc import mul(\n", "import")},
    # It parses, but a helper the writer wrote beside it does not.
    {"acc/test_defect_mul.py": (_IMPORT_DEFECT, "import"),
     "acc/helpers_mul.py": ("def broken(:\n    pass\n", None)},
])
def test_an_import_declaration_over_unparseable_writer_files_is_not_honoured(
        leerie, tmp_path, monkeypatch, capsys, files):
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head)
    _import_writer(leerie, monkeypatch, files)
    acc = asyncio.run(leerie.phase_acceptance_write(
        st.data["task"], st, _caps(leerie, 1), MODELS, EFFORTS))
    assert acc["sets"] == []
    out = capsys.readouterr().out
    assert "declaration not honoured" in out and "does not parse" in out


def test_the_parse_check_runs_under_the_projects_own_interpreter(
        leerie, tmp_path, monkeypatch, capsys):
    """The scoped command runs the probe, so its interpreter decides. Here
    the project's "interpreter" (a wrapper) rejects the probe outright: an
    in-process check by the orchestrator's own Python would have passed."""
    repo, head = _repo(tmp_path)
    wrapper = tmp_path / "project-python"
    # The project's interpreter: it rejects the probe file, and otherwise
    # behaves as python3. Invoked as `<it> -m pytest`, so the runner (and
    # its report) is still recognised.
    wrapper.write_text(
        "#!/bin/sh\ncase \"$*\" in *leerie_probe*) exit 1;; esac\n"
        "exec python3 \"$@\"\n")
    wrapper.chmod(0o755)
    (repo / ".leerie" / "config.toml").write_text(
        f'test_scoped = "{wrapper} -m pytest -q -p no:cacheprovider '
        '{test_files}"\n')
    st = _st(leerie, tmp_path, repo, head)
    _import_writer(leerie, monkeypatch,
                   {"acc/test_defect_mul.py": (_IMPORT_DEFECT, "import")})
    acc = asyncio.run(leerie.phase_acceptance_write(
        st.data["task"], st, _caps(leerie, 1), MODELS, EFFORTS))
    assert acc["sets"] == []
    assert "declaration not honoured" in capsys.readouterr().out


def test_the_parse_probe_follows_a_repos_own_test_naming(
        leerie, tmp_path, monkeypatch):
    """PR #282 review: the probe was always `test_leerie_parse_probe_*.py`,
    which a `*_test.py` repo's scoped command will not render — so its
    import declarations were never honoured."""
    repo, head = _repo(tmp_path)
    (repo / ".leerie" / "config.toml").write_text(
        'test_file_globs = "*_test.py"\n'
        'test_scoped = "python3 -m pytest -q -p no:cacheprovider '
        '-o python_files=*_test.py {test_files}"\n')
    st = _st(leerie, tmp_path, repo, head)
    _import_writer(leerie, monkeypatch,
                   {"acc/defect_mul_test.py": (_IMPORT_DEFECT, "import")})
    acc = asyncio.run(leerie.phase_acceptance_write(
        st.data["task"], st, _caps(leerie, 1), MODELS, EFFORTS))
    (s,) = acc["sets"]
    assert s["modes"] == {"acc/defect_mul_test.py": "import"}


@pytest.mark.parametrize("globs,declared,want_end", [
    ("", "acc/test_defect_mul.py", "acc/test_defect_mul_leerie_probe_"),
    ("*_test.py", "acc/defect_mul_test.py", "acc/leerie_probe_"),
    ("**/*_test.py", "acc/defect_mul_test.py", "acc/leerie_probe_"),
    ("*.spec.py", "acc/defect_mul.spec.py", "acc/leerie_probe_"),
    ("check_*.py", "acc/defect_mul.py", None),
])
def test_the_probe_name_matches_the_declared_files_convention(
        leerie, tmp_path, globs, declared, want_end):
    repo, head = _repo(tmp_path)
    if globs:
        (repo / ".leerie" / "config.toml").write_text(
            f'test_file_globs = "{globs}"\n')
    st = _st(leerie, tmp_path, repo, head)
    got = leerie._acceptance_probe_rel(st, declared)
    shaped = leerie._is_test_file(got, leerie.resolve_test_file_globs(repo))
    if want_end is None:
        # No test-shaped candidate: the first is returned anyway, and a
        # `{test_files}` template then renders no command for it.
        assert not shaped
    else:
        assert got.startswith(want_end) and shaped


def test_a_long_declared_name_still_gets_a_probe_name(leerie, tmp_path):
    """A declared name near NAME_MAX cannot be extended; the probe falls
    back to a short hash-only name the globs still accept."""
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head)
    got = leerie._acceptance_probe_rel(st, "tests/test_" + "a" * 233 + ".py")
    assert len(Path(got).name.encode()) <= 200
    assert leerie._is_test_file(got, [])


def test_a_probe_the_template_deselects_is_unrunnable_not_unparseable(
        leerie, tmp_path):
    """pytest exits 5 when a `-k` in the template deselects the probe:
    that says nothing about parsing."""
    repo, head = _repo(tmp_path)
    (repo / ".leerie" / "config.toml").write_text(
        'test_scoped = "python3 -m pytest -q -p no:cacheprovider '
        '-k nothing_matches {test_files}"\n')
    st = _st(leerie, tmp_path, repo, head)
    (repo / "test_probe_me.py").write_text("x = 1\n")
    got, why = asyncio.run(leerie._acceptance_parse_probe(
        st, _caps(leerie, 1), str(repo), "test_probe_me.py",
        ["test_probe_me.py"], st.run_dir / "logs" / "p.log", "p"))
    assert got is None and "collected or selected no test" in why
    assert "exit 5" in why


@pytest.mark.parametrize("conftest,addopts", [
    # pytest cannot load the conftest (exit 4) before the probe's body runs,
    # so the probe cannot judge parsing — None, not "does not parse".
    ("def broken(:\n    pass\n", ""),
    # Imports the very entry point an import defect lacks — every file
    # parses (post-merge review of 11e9431).
    ("from calc import mul\n", ""),
    ("", "--no-such-plugin-flag"),                    # unknown option: exit 4
])
def test_a_probe_that_never_reaches_its_check_says_so(leerie, tmp_path,
                                                     monkeypatch, conftest,
                                                     addopts):
    """pytest exits 2/3/4 before the probe's body runs, so whether the
    targets parse is unknown: None, with the exit code in the reason —
    never "does not parse"."""
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head)
    (repo / "acc").mkdir()
    if conftest:
        (repo / "acc" / "conftest.py").write_text(conftest)
    if addopts:
        monkeypatch.setenv("PYTEST_ADDOPTS", addopts)
    (repo / "acc" / "test_defect_mul.py").write_text(_IMPORT_DEFECT)
    got, why = asyncio.run(leerie._acceptance_parse_probe(
        st, _caps(leerie, 1), str(repo), "acc/test_defect_mul.py",
        ["acc/test_defect_mul.py"], st.run_dir / "logs" / "p.log", "p"))
    assert got is None
    assert "before reaching the parse check" in why and "exit " in why
    assert "does not parse" not in why


_PASSING_THEN_TEARDOWN_FAILS = (
    "import pytest\n\n@pytest.fixture(autouse=True)\ndef boom():\n"
    "    yield\n    raise RuntimeError('teardown')\n")
_SKIPS_EVERYTHING = (
    "import pytest\n\ndef pytest_collection_modifyitems(items):\n"
    "    for item in items:\n"
    "        item.add_marker(pytest.mark.skip(reason='x'))\n")


@pytest.mark.parametrize("conftest,template,want_reason", [
    # The check passed ("ok"), then a teardown failed the run.
    (_PASSING_THEN_TEARDOWN_FAILS, None, "passed but the test run failed"),
    # Exit 0, but the probe's body never ran.
    (_SKIPS_EVERYTHING, None, "collected or selected no test"),
    # The runner does not exist.
    ("", "no-such-runner-xyz {test_files}", "could not be run (exit 127)"),
])
def test_a_probe_failure_that_is_not_about_parsing_says_what_it_was(
        leerie, tmp_path, conftest, template, want_reason):
    """Post-merge review of 5f22a2e: a teardown error after the body read as
    "does not parse"; a probe skipped by the repo read as True; exit 127
    blamed a conftest."""
    repo, head = _repo(tmp_path)
    if template:
        (repo / ".leerie" / "config.toml").write_text(
            f'test_scoped = "{template}"\n')
    st = _st(leerie, tmp_path, repo, head)
    (repo / "acc").mkdir()
    if conftest:
        (repo / "acc" / "conftest.py").write_text(conftest)
    (repo / "acc" / "test_defect_mul.py").write_text(_IMPORT_DEFECT)
    got, why = asyncio.run(leerie._acceptance_parse_probe(
        st, _caps(leerie, 1), str(repo), "acc/test_defect_mul.py",
        ["acc/test_defect_mul.py"], st.run_dir / "logs" / "p.log", "p"))
    assert got is None and want_reason in why


@pytest.mark.skipif(os.geteuid() == 0,
                    reason="root ignores directory permissions")
def test_an_unwritable_marker_directory_is_named_as_such(leerie, tmp_path):
    """The probe's body could not write its marker, which read as "failed
    before reaching the parse check (… a conftest …)"."""
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head)
    (repo / "acc").mkdir()
    (repo / "acc" / "test_defect_mul.py").write_text(_IMPORT_DEFECT)
    reports = st.run_dir / "acceptance" / "reports"
    reports.mkdir(parents=True)
    reports.chmod(0o555)
    try:
        got, why = asyncio.run(leerie._acceptance_parse_probe(
            st, _caps(leerie, 1), str(repo), "acc/test_defect_mul.py",
            ["acc/test_defect_mul.py"], st.run_dir / "logs" / "p.log", "p"))
    finally:
        reports.chmod(0o755)
    assert got is None and "could not be prepared" in why
    assert "conftest" not in why


def test_a_target_that_does_not_compile_is_a_parse_failure(leerie, tmp_path):
    """The probe's body ran (its marker exists) and a target failed to
    compile: that, and only that, is "does not parse"."""
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head)
    (repo / "acc").mkdir()
    (repo / "acc" / "test_defect_mul.py").write_text(_IMPORT_DEFECT)
    (repo / "acc" / "helpers_mul.py").write_text("def broken(:\n")
    got, why = asyncio.run(leerie._acceptance_parse_probe(
        st, _caps(leerie, 1), str(repo), "acc/test_defect_mul.py",
        ["acc/test_defect_mul.py", "acc/helpers_mul.py"],
        st.run_dir / "logs" / "p.log", "p"))
    assert got is False and "does not parse" in why


def test_a_files_template_runs_the_probe_whatever_its_name(leerie, tmp_path):
    """A `{files}` template renders any name, so the probe runs even where
    no candidate is test-shaped (round-18 LOW: it had returned None)."""
    repo, head = _repo(tmp_path)
    (repo / ".leerie" / "config.toml").write_text(
        'test_file_globs = "check_*.py"\n'
        'test_scoped = "python3 -m pytest -q -p no:cacheprovider {files}"\n')
    st = _st(leerie, tmp_path, repo, head)
    (repo / "src").mkdir()
    (repo / "src" / "defect_mul.py").write_text("x = 1\n")
    assert asyncio.run(leerie._acceptance_parse_probe(
        st, _caps(leerie, 1), str(repo), "src/defect_mul.py",
        ["src/defect_mul.py"], st.run_dir / "logs" / "p.log", "p")) == (
            True, "")


def test_an_unrunnable_probe_is_reported_as_such(leerie, tmp_path,
                                                monkeypatch, capsys):
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head)

    async def unrunnable(*a, **k):
        return None, "no test command here could run the parse check"
    monkeypatch.setattr(leerie, "_acceptance_parse_probe", unrunnable)
    _import_writer(leerie, monkeypatch,
                   {"acc/test_defect_mul.py": (_IMPORT_DEFECT, "import")})
    asyncio.run(leerie.phase_acceptance_write(
        st.data["task"], st, _caps(leerie, 1), MODELS, EFFORTS))
    out = capsys.readouterr().out
    assert "no test command here could run the parse check" in out
    assert "does not parse" not in out


def test_the_parse_probe_leaves_nothing_in_the_set(leerie, tmp_path,
                                                  monkeypatch):
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head)
    _import_writer(leerie, monkeypatch, {
        "acc/test_defect_mul.py": (_IMPORT_DEFECT, "import"),
        "acc/helpers_mul.py": ("VALUE = 6\n", None)})
    acc = asyncio.run(leerie.phase_acceptance_write(
        st.data["task"], st, _caps(leerie, 1), MODELS, EFFORTS))
    (s,) = acc["sets"]
    assert s["defect_files"] == ["acc/test_defect_mul.py"]
    assert s["support_files"] == ["acc/helpers_mul.py"]
    stored = sorted(str(p.relative_to(s["dir"]))
                    for p in Path(s["dir"]).rglob("*") if p.is_file())
    assert not any("leerie_probe" in p for p in stored)


def test_import_cases_are_named_as_import_failures_in_the_repair_section(
        leerie):
    sets = [{"index": 1, "modes": {"acc/test_defect_mul.py": "import"},
             "cases": {"acc/test_defect_mul.py": ["mul exists"],
                       "acc/test_defect_add.py": ["add sums"]}}]
    res = [{"index": 1, "passed": False,
            "failing_files": ["acc/test_defect_mul.py",
                              "acc/test_defect_add.py"]}]
    text = leerie._format_acceptance_failures_section(res, sets, {1}, 1, "x")
    # A fact about the UNFIXED tree, never a claim about the current one
    # (final review of #282: the entry point may exist by now).
    head, _, tail = text.partition("could not even load against the UNFIXED")
    assert "  - add sums" in head and "mul exists" not in head
    assert "  - mul exists" in tail


def test_an_unhonourable_import_declaration_drops_only_its_file(
        leerie, tmp_path, monkeypatch, capsys):
    """PR #282 review round 2: a non-Python file declared "import" cannot
    load, which made its verdict None and discarded the WHOLE set — the
    valid defect file and control with it — while the prompt promised only
    the file was lost. It is now dropped alone, before running."""
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head)
    _writer_stub(leerie, monkeypatch, {1: {
        "acc/test_defect_sum.py": ("defect", DEFECT_TEST, ["sums"]),
        "acc/mul.test.ts": ("defect", "import { mul } from '../mathx';\n",
                            ["mul exists"], "import"),
        "acc/test_control_zero.py": ("control", CONTROL_TEST, ["zero"])}})
    acc = asyncio.run(leerie.phase_acceptance_write(
        st.data["task"], st, _caps(leerie, 1), MODELS, EFFORTS))
    (s,) = acc["sets"]
    assert s["defect_files"] == ["acc/test_defect_sum.py"]
    assert s["control_files"] == ["acc/test_control_zero.py"]
    assert "acc/mul.test.ts" not in s["support_files"]
    assert "honoured only for Python test files" in capsys.readouterr().out


def test_the_writer_prompt_example_matches_the_schema(leerie):
    """test_prompt_schema_parity checks field NAMES appear in the prompt;
    this checks the example's file items carry exactly the schema's fields
    with values its enums allow."""
    import re
    prompt = leerie._load_prompt("acceptance_writer")
    example = json.loads(re.search(r"```json\n(.*?)```", prompt,
                                   re.S).group(1))
    item = leerie.SCHEMAS["acceptance_writer"]["properties"]["files"][
        "items"]
    for f in example["files"]:
        assert set(f) == set(item["properties"])
        assert set(item["required"]) <= set(f)
        for key, spec in item["properties"].items():
            if "enum" in spec:
                assert f[key] in spec["enum"]


def test_a_declared_import_defect_that_loaded_is_recorded_as_an_assertion(
        leerie, tmp_path, monkeypatch):
    """Final review of #282: `modes` stored the writer's declaration as-is,
    so a file that loaded fine on the base and failed an assertion was
    later described to the repair rounds as an import failure."""
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head)
    _import_writer(leerie, monkeypatch,
                   {"acc/test_defect_sum.py": (DEFECT_TEST, "import")})
    acc = asyncio.run(leerie.phase_acceptance_write(
        st.data["task"], st, _caps(leerie, 1), MODELS, EFFORTS))
    (s,) = acc["sets"]
    assert s["defect_files"] == ["acc/test_defect_sum.py"]
    assert s["modes"] == {"acc/test_defect_sum.py": "assertion"}


def test_an_import_defect_set_validates_and_passes_on_the_fix(
        leerie, tmp_path, monkeypatch):
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head)
    _writer_stub(leerie, monkeypatch, {1: {
        "acc/test_defect_mul.py": ("defect", _IMPORT_DEFECT, ["mul exists"],
                                   "import"),
        "acc/test_control_add.py": ("control", CONTROL_TEST, ["add zero"])}})
    acc = asyncio.run(leerie.phase_acceptance_write(
        st.data["task"], st, _caps(leerie, 1), MODELS, EFFORTS))
    (s,) = acc["sets"]
    assert s["defect_files"] == ["acc/test_defect_mul.py"]
    assert s["modes"] == {"acc/test_defect_mul.py": "import"}
    staging = _staging(st, repo)
    (staging / "calc.py").write_text(
        BUGGY + "\n\ndef mul(a, b):\n    return a * b\n")
    _git(staging, "commit", "-qam", "conformer: add mul")
    res = asyncio.run(leerie._evaluate_acceptance_sets(
        st, _caps(leerie, 1), str(staging), acc["sets"], "t"))
    assert res[0]["passed"] is True


def test_majority_rule(leerie):
    f = leerie._acceptance_majority_fails
    assert f([{"passed": False}, {"passed": False}, {"passed": True}])
    assert not f([{"passed": True}, {"passed": True}, {"passed": False}])
    # A tie is not a passing majority.
    assert f([{"passed": False}, {"passed": True}])
    # Unmeasured sets are not evidence: excluded from the vote, and nothing
    # measured is "no evidence", never a failing (or passing) majority.
    assert not f([{"passed": True}, {"passed": False, "unmeasured": True}])
    assert not f([{"passed": False, "unmeasured": True}])
    assert not f([])


def test_unrunnable_files_never_count_as_passing(leerie, tmp_path):
    """Round-1 M1: a runner that cannot run the file must not read as a
    pass — that would end a run as "no work" on tests that never ran."""
    repo, head = _repo(tmp_path)
    (repo / ".leerie" / "config.toml").write_text(
        'test_scoped = "no-such-runner-xyz {test_files}"\n')
    st = _st(leerie, tmp_path, repo, head)
    staging = _staging(st, repo)
    res = asyncio.run(leerie._evaluate_acceptance_sets(
        st, _caps(leerie, 3), str(staging), _make_sets(leerie, st, 3), "t"))
    assert all(r["unmeasured"] and not r["passed"] for r in res)
    assert leerie._acceptance_measured(res) == []


def test_undeclared_helper_travels_with_the_set(leerie, tmp_path,
                                               monkeypatch):
    """Round-2 M1: a fixture module the writer created but did not declare
    was present at validation and absent at evaluation, so a CORRECT fix
    failed the set. It is now a support file copied with the set."""
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head)
    helper_test = ("from acc.helper_vals import EXPECTED\nfrom calc import add\n\n"
                   "def test_add_sums():\n    assert add(2, 3) == EXPECTED\n")

    async def fake_claude_p(**kw):
        wt = Path(kw["cwd"])
        (wt / "acc").mkdir()
        (wt / "acc" / "__init__.py").write_text("")
        (wt / "acc" / "helper_vals.py").write_text("EXPECTED = 5\n")
        (wt / "acc" / "test_defect_h.py").write_text(helper_test)
        return {"files": [{"path": "acc/test_defect_h.py", "kind": "defect",
                           "cases": ["sums via helper"]}]}

    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    acc = asyncio.run(leerie.phase_acceptance_write(
        st.data["task"], st, _caps(leerie, 1), MODELS, EFFORTS))
    (s,) = acc["sets"]
    assert s["support_files"] == ["acc/__init__.py", "acc/helper_vals.py"]
    staging = _staging(st, repo)
    _commit_fix(staging)
    res = asyncio.run(leerie._evaluate_acceptance_sets(
        st, _caps(leerie, 1), str(staging), acc["sets"], "t"))
    assert res == [{"index": 1, "passed": True, "unmeasured": False,
                    "failing_files": []}]


def test_writer_editing_a_tracked_file_discards_the_set(leerie, tmp_path,
                                                       monkeypatch):
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head)

    async def fake_claude_p(**kw):
        wt = Path(kw["cwd"])
        # An edit to a tracked file that does not change the verdicts: only
        # the tracked-file check can reject this set.
        (wt / "conftest.py").write_text(
            (wt / "conftest.py").read_text() + "# tweaked by the writer\n")
        (wt / "acc").mkdir()
        (wt / "acc" / "test_defect_x.py").write_text(DEFECT_TEST)
        return {"files": [{"path": "acc/test_defect_x.py", "kind": "defect",
                           "cases": ["x"]}]}

    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    acc = asyncio.run(leerie.phase_acceptance_write(
        st.data["task"], st, _caps(leerie, 1), MODELS, EFFORTS))
    assert acc["sets"] == []


def test_writer_editing_an_existing_file_is_not_kept(leerie, tmp_path,
                                                    monkeypatch):
    """Round-1 M2: "new files only" is enforced in code, not left to the
    prompt."""
    repo, head = _repo(tmp_path)
    (repo / "tests").mkdir()
    (repo / "tests" / "test_calc.py").write_text(CONTROL_TEST)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "existing test")
    head = _git(repo, "rev-parse", "HEAD")
    st = _st(leerie, tmp_path, repo, head)
    _writer_stub(leerie, monkeypatch, {1: {
        "tests/test_calc.py": ("defect", DEFECT_TEST, ["edits existing"])}})
    acc = asyncio.run(leerie.phase_acceptance_write(
        st.data["task"], st, _caps(leerie, 1), MODELS, EFFORTS))
    assert acc["sets"] == []


def test_absolute_path_is_rejected_and_one_writer_cannot_sink_the_rest(
        leerie, tmp_path, monkeypatch):
    """Round-1 M3: an absolute declared path (models often report them)
    used to raise SameFileError out of asyncio.gather and lose every set.
    Set 1 also declares a valid relative file: with the absolute entry
    rejected the set survives on it; without the rejection, copying the
    absolute entry onto itself discards the whole set."""
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head)
    calls = []

    async def fake_claude_p(**kw):
        k = int(kw["sid"].split("-")[-1])
        calls.append(k)
        p = Path(kw["cwd"]) / "acc" / f"test_defect_{k}.py"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(DEFECT_TEST)
        if k == 1:
            q = Path(kw["cwd"]) / "acc" / "test_defect_1b.py"
            q.write_text(DEFECT_TEST)
            return {"files": [
                {"path": str(p), "kind": "defect", "cases": ["a"]},
                {"path": "acc/test_defect_1b.py", "kind": "defect",
                 "cases": ["b"]}]}
        if k == 2:
            raise OSError("writer 2 blew up")
        return {"files": [{"path": f"acc/test_defect_{k}.py", "kind": "defect",
                           "cases": ["c"]}]}

    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    acc = asyncio.run(leerie.phase_acceptance_write(
        st.data["task"], st, _caps(leerie, 3), MODELS, EFFORTS))
    assert [s["index"] for s in acc["sets"]] == [1, 3]
    assert acc["sets"][0]["defect_files"] == ["acc/test_defect_1b.py"]
    assert sorted(calls) == [1, 2, 3]


def test_dot_slash_declared_paths_are_not_also_support_files(
        leerie, tmp_path, monkeypatch):
    """Round-4 M2: a declared `./x` was compared unnormalised against git's
    `x`, carried as a support file too, and collided with itself."""
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head)
    _writer_stub(leerie, monkeypatch, {1: {
        "acc/test_defect_sum.py": ("defect", DEFECT_TEST, ["sums"]),
        "acc/test_control_zero.py": ("control", CONTROL_TEST, ["zero"])}})
    real = leerie.claude_p

    async def dotted(**kw):
        out = await real(**kw)
        # Round-5 L2: `a/./b` and `a//b` too, not only a leading `./`.
        for f, form in zip(out["files"], ("./{}", "acc/./{}")):
            f["path"] = form.format(f["path"].split("/", 1)[1]) \
                if form.startswith("acc") else form.format(f["path"])
        return out
    monkeypatch.setattr(leerie, "claude_p", dotted)
    acc = asyncio.run(leerie.phase_acceptance_write(
        st.data["task"], st, _caps(leerie, 1), MODELS, EFFORTS))
    (s,) = acc["sets"]
    assert s["support_files"] == []
    staging = _staging(st, repo)
    _commit_fix(staging)
    res = asyncio.run(leerie._evaluate_acceptance_sets(
        st, _caps(leerie, 1), str(staging), acc["sets"], "t"))
    assert res[0]["passed"] is True


def test_ignored_build_output_elsewhere_does_not_travel(leerie, tmp_path,
                                                        monkeypatch):
    """Round-5 (claims) LOW: build output from the UNFIXED tree, ignored and
    away from the tests, would shadow a correct fix."""
    repo, head = _repo(tmp_path)
    (repo / ".gitignore").write_text("dist/\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "ignore dist")
    head = _git(repo, "rev-parse", "HEAD")
    st = _st(leerie, tmp_path, repo, head)

    async def fake_claude_p(**kw):
        wt = Path(kw["cwd"])
        (wt / "dist").mkdir()
        (wt / "dist" / "calc_built.py").write_text(BUGGY)
        (wt / "acc").mkdir()
        (wt / "acc" / "test_defect_x.py").write_text(DEFECT_TEST)
        return {"files": [{"path": "acc/test_defect_x.py", "kind": "defect",
                           "cases": ["sums"]}]}
    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    acc = asyncio.run(leerie.phase_acceptance_write(
        st.data["task"], st, _caps(leerie, 1), MODELS, EFFORTS))
    (s,) = acc["sets"]
    assert s["support_files"] == []


def test_a_gitignored_helper_still_travels(leerie, tmp_path, monkeypatch):
    """Round-4 M5: `git status` does not list ignored files."""
    repo, head = _repo(tmp_path)
    (repo / ".gitignore").write_text("*.json\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "ignore json")
    head = _git(repo, "rev-parse", "HEAD")
    st = _st(leerie, tmp_path, repo, head)
    reader = ("import json, pathlib\nfrom calc import add\n\n"
              "def test_add_sums():\n"
              "    want = json.loads((pathlib.Path(__file__).parent / "
              "'cases.json').read_text())['want']\n"
              "    assert add(2, 3) == want\n")

    async def fake_claude_p(**kw):
        wt = Path(kw["cwd"])
        (wt / "acc").mkdir()
        (wt / "acc" / "cases.json").write_text('{"want": 5}')
        (wt / "acc" / "test_defect_j.py").write_text(reader)
        return {"files": [{"path": "acc/test_defect_j.py", "kind": "defect",
                           "cases": ["sums per cases.json"]}]}
    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    acc = asyncio.run(leerie.phase_acceptance_write(
        st.data["task"], st, _caps(leerie, 1), MODELS, EFFORTS))
    (s,) = acc["sets"]
    assert s["support_files"] == ["acc/cases.json"]
    staging = _staging(st, repo)
    _commit_fix(staging)
    res = asyncio.run(leerie._evaluate_acceptance_sets(
        st, _caps(leerie, 1), str(staging), acc["sets"], "t"))
    assert res[0]["passed"] is True


@pytest.mark.parametrize("body,want", [
    # A missing-file defect prints "No such file or directory" when its test
    # fails; that is a failure, not an unrunnable runner (round-4 M4).
    ("def test_x():\n    open('/nonexistent/acceptance/input')\n", False),
    ("def test_x():\n    assert True\n", True),
])
def test_verdict_is_by_exit_code(leerie, tmp_path, body, want):
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head)
    (repo / "test_probe.py").write_text(body)
    got = asyncio.run(leerie._run_acceptance_file(
        st, _caps(leerie, 1), str(repo), "test_probe.py",
        st.run_dir / "logs" / "p.log", "p"))
    assert got is want


@pytest.mark.parametrize("body", [
    "x = 1\n",                         # no test collected: pytest exit 5
    "def test_x(:\n    pass\n",        # the test file itself broken: 2
])
def test_a_file_that_ran_no_test_is_no_verdict(leerie, tmp_path, body):
    """Round-5 L1: a defect file that never ran must not count as failing on
    the base — it would fail on every fix forever. Round 7: only while
    VALIDATING; evaluating a fix, the same exit is a failure (the file ran
    on the base, so the fix is what stopped it)."""
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head)
    (repo / "test_probe.py").write_text(body)
    run = lambda validating: asyncio.run(leerie._run_acceptance_file(
        st, _caps(leerie, 1), str(repo), "test_probe.py",
        st.run_dir / "logs" / "p.log", "p", validating=validating))
    assert run(True) is None
    assert run(False) is False


def test_a_fork_exhaustion_kill_is_no_verdict(leerie, tmp_path):
    repo, head = _repo(tmp_path)
    (repo / ".leerie" / "config.toml").write_text(
        'test_scoped = "echo \'bash: fork: retry: Resource temporarily '
        'unavailable\'; exit 1; {test_files}"\n')
    st = _st(leerie, tmp_path, repo, head)
    (repo / "test_probe.py").write_text("def test_x():\n    pass\n")
    assert asyncio.run(leerie._run_acceptance_file(
        st, _caps(leerie, 1), str(repo), "test_probe.py",
        st.run_dir / "logs" / "p.log", "p")) is None


def test_a_timeout_fails(leerie, tmp_path):
    """A hung test is the fix failing to finish, not "no verdict"."""
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head)
    (repo / "test_probe.py").write_text(
        "import time\n\ndef test_x():\n    time.sleep(30)\n")
    caps = _caps(leerie, 1)
    caps["worker_timeout_sec"] = 2
    assert asyncio.run(leerie._run_acceptance_file(
        st, caps, str(repo), "test_probe.py",
        st.run_dir / "logs" / "p.log", "p")) is False


@pytest.mark.parametrize("module", [
    "def f():\n    return 1\n",           # a fix that removed the entry point
    "def add(a, b):\n    return a +\n",  # a fix that broke the module
])
def test_an_unimportable_module_under_test_is_a_failure(leerie, tmp_path,
                                                        module):
    """Round-6 M1: pytest's collection error (exit 2) is also what a fix
    that broke the module produces — at evaluation that is evidence, not
    "ran no test"."""
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head)
    (repo / "calcmod.py").write_text(module)
    (repo / "test_probe.py").write_text(
        "from calcmod import g\n\ndef test_g():\n    assert g() == 2\n")
    assert asyncio.run(leerie._run_acceptance_file(
        st, _caps(leerie, 1), str(repo), "test_probe.py",
        st.run_dir / "logs" / "p.log", "p")) is False


def test_a_broken_module_a_conftest_imports_is_a_failure(leerie, tmp_path):
    """Round-7 M1: pytest reports a conftest ImportError as exit 4 — at
    evaluation, still the fix's doing."""
    repo, head = _repo(tmp_path)
    (repo / "conftest.py").write_text(
        "import sys, pathlib\nsys.path.insert(0, str(pathlib.Path(__file__)"
        ".parent))\nimport calc\n")
    (repo / "calc.py").write_text("def add(a, b):\n    return a +\n")
    st = _st(leerie, tmp_path, repo, head)
    (repo / "test_probe.py").write_text(DEFECT_TEST)
    assert asyncio.run(leerie._run_acceptance_file(
        st, _caps(leerie, 1), str(repo), "test_probe.py",
        st.run_dir / "logs" / "p.log", "p")) is False


def test_a_writer_file_broken_in_itself_discards_the_set(
        leerie, tmp_path, monkeypatch):
    """Round-7 M2: a defect file with a syntax error (or a missing library)
    "fails" on the base by its own fault and could never pass anywhere."""
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head)
    _writer_stub(leerie, monkeypatch, {1: {
        "acc/test_defect_x.py": ("defect", "def test_add(:\n    pass\n",
                                 ["broken"])}})
    acc = asyncio.run(leerie.phase_acceptance_write(
        st.data["task"], st, _caps(leerie, 1), MODELS, EFFORTS))
    assert acc["sets"] == []


def test_no_verdict_exits_are_matched_on_command_tokens(leerie):
    f = leerie._acceptance_no_verdict_exits
    assert f("python3 -m pytest -q acc/test_a.py") == frozenset({2, 3, 4, 5})
    assert f("uv run /usr/bin/pytest x") == frozenset({2, 3, 4, 5})
    assert f("npx jest acc/a.test.js") == frozenset()
    assert f("echo pytestish") == frozenset()


def test_an_unrunnable_command_is_no_verdict(leerie, tmp_path):
    repo, head = _repo(tmp_path)
    (repo / ".leerie" / "config.toml").write_text(
        'test_scoped = "no-such-runner-xyz {test_files}"\n')
    st = _st(leerie, tmp_path, repo, head)
    (repo / "test_probe.py").write_text("def test_x():\n    pass\n")
    assert asyncio.run(leerie._run_acceptance_file(
        st, _caps(leerie, 1), str(repo), "test_probe.py",
        st.run_dir / "logs" / "p.log", "p")) is None


def test_runner_output_never_reaches_the_orchestrator_log(
        leerie, tmp_path, capsys):
    """Round-4 M3: at `stream` verbosity a set's failing output was echoed
    through `log()` into orchestrator.log."""
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head, verbosity="stream")
    staging = _staging(st, repo)
    asyncio.run(leerie._evaluate_acceptance_sets(
        st, _caps(leerie, 1), str(staging), _make_sets(leerie, st, 1), "t"))
    out = capsys.readouterr().out
    assert "assert add(2, 3) == 5" not in out
    assert "test_defect_1" not in out


def test_a_failed_install_in_the_writer_discards_the_set(
        leerie, tmp_path, monkeypatch, capsys):
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head)
    _failing_recipe(st, fail=True)
    _writer_stub(leerie, monkeypatch, {1: {
        "acc/test_defect_sum.py": ("defect", DEFECT_TEST, ["sums"])}})
    acc = asyncio.run(leerie.phase_acceptance_write(
        st.data["task"], st, _caps(leerie, 1), MODELS, EFFORTS))
    assert acc["sets"] == []
    assert "dependency install failed" in capsys.readouterr().out


def test_writer_budget_exhaustion_propagates(leerie, tmp_path, monkeypatch):
    """`bump_workers` sits outside the writer's try: an exhausted budget
    stops the run instead of reading as an invalid set."""
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head)
    _writer_stub(leerie, monkeypatch, {})
    caps = _caps(leerie, 2)
    caps["max_total_workers"] = 0
    with pytest.raises(leerie.WorkerError, match="budget exhausted"):
        asyncio.run(leerie.phase_acceptance_write(
            st.data["task"], st, caps, MODELS, EFFORTS))


def test_write_or_skip_lets_budget_exhaustion_stop_the_run(
        leerie, tmp_path, monkeypatch):
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head)

    async def budget(*a, **k):
        raise leerie.WorkerError("worker budget exhausted (3)")

    async def other(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(leerie, "phase_acceptance_write", budget)
    with pytest.raises(leerie.WorkerError):
        asyncio.run(leerie._acceptance_write_or_skip(
            "t", st, _caps(leerie, 1), MODELS, EFFORTS))
    monkeypatch.setattr(leerie, "phase_acceptance_write", other)
    assert asyncio.run(leerie._acceptance_write_or_skip(
        "t", st, _caps(leerie, 1), MODELS, EFFORTS)) == {
            "skipped": "error: OSError"}


def test_budget_exhaustion_cancels_the_other_writers(leerie, tmp_path,
                                                    monkeypatch):
    """`_gather_or_cancel`, not `asyncio.gather`: once one writer stops the
    run, the others must not keep spending workers in the background."""
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head)
    finished = []

    async def slow_claude_p(**kw):
        await asyncio.sleep(0.5)
        finished.append(kw["sid"])
        return {"files": []}
    monkeypatch.setattr(leerie, "claude_p", slow_claude_p)
    caps = _caps(leerie, 2)
    caps["max_parallel"] = 2
    caps["max_total_workers"] = 1

    async def drive():
        with pytest.raises(leerie.WorkerError):
            await leerie.phase_acceptance_write(st.data["task"], st, caps,
                                                MODELS, EFFORTS)
        await asyncio.sleep(1.0)
    asyncio.run(drive())
    assert finished == []


def test_writers_respect_max_parallel(leerie, tmp_path, monkeypatch):
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head)
    live, peak = [0], [0]

    async def fake_claude_p(**kw):
        live[0] += 1
        peak[0] = max(peak[0], live[0])
        await asyncio.sleep(0.05)
        live[0] -= 1
        return {"files": []}

    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    caps = _caps(leerie, 3)
    caps["max_parallel"] = 1
    asyncio.run(leerie.phase_acceptance_write(
        st.data["task"], st, caps, MODELS, EFFORTS))
    assert peak[0] == 1


def test_failures_section_is_names_only_and_hides_the_holdout(leerie, tmp_path):
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head)
    sets = _make_sets(leerie, st, 5)
    results = [{"index": k, "passed": False,
                "failing_files": [f"acc/test_defect_{k}.py"]} for k in range(1, 6)]
    text = leerie._format_acceptance_failures_section(
        results, sets, {1, 2, 3}, 1, "add subtracts")
    assert "case from set 1" in text and "case from set 3" in text
    assert "case from set 4" not in text and "case from set 5" not in text
    assert "assert add(2, 3) == 5" not in text      # never the test source
    assert "add subtracts" in text


def _run_gate(leerie, monkeypatch, st, fixer, measured=None):
    calls = []

    async def fake_claude_p(**kw):
        calls.append(kw)
        fixer(Path(kw["cwd"]))
        return {}

    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    if measured is not None:
        seq = list(measured)

        async def fake_axes(tree, axes, st_, caps, **kw):
            return {"tests": seq.pop(0)}
        monkeypatch.setattr(leerie, "_measure_axes", fake_axes)
    asyncio.run(leerie._run_acceptance_gate(
        st.run_dir, st, _caps(leerie, 5), MODELS, EFFORTS))
    return calls


def _commit_fix(staging: Path):
    (staging / "calc.py").write_text(FIXED)
    _git(staging, "commit", "-qam", "conformer: fix add")


def test_gate_repairs_at_high_effort_and_records_a_clean_final(
        leerie, tmp_path, monkeypatch):
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head, working_branch="main")
    _staging(st, repo)
    st.data["acceptance"] = {"sets": _make_sets(leerie, st, 5)}
    calls = _run_gate(leerie, monkeypatch, st, _commit_fix)
    assert len(calls) == 1                      # fixed in round 1
    assert calls[0]["effort"] == "high" == leerie.EFFORT_ACCEPTANCE_REPAIR
    assert calls[0]["schema_key"] == "conformer"
    up = calls[0]["user_prompt"]
    assert "case from set 1" in up and "case from set 5" not in up
    gate = st.data["acceptance"]["gate"]
    assert all(r["passed"] for r in gate["final"]) and "residual" not in gate


def test_unfixed_tree_ships_with_a_residual_after_two_rounds(
        leerie, tmp_path, monkeypatch):
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head, working_branch="main")
    _staging(st, repo)
    st.data["acceptance"] = {"sets": _make_sets(leerie, st, 5)}
    calls = _run_gate(leerie, monkeypatch, st, lambda _p: None)
    assert len(calls) == 2                      # at most two rounds
    res = st.data["acceptance"]["gate"]["residual"]
    assert res["failing_sets"] == 5 and "case from set 1" in res["cases"]


def test_repair_that_turns_the_test_axis_red_is_rolled_back(
        leerie, tmp_path, monkeypatch):
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head, working_branch="main")
    staging = _staging(st, repo)
    st.data["acceptance"] = {"sets": _make_sets(leerie, st, 5)}
    before = _git(staging, "rev-parse", "HEAD")
    _run_gate(leerie, monkeypatch, st, _commit_fix,
              measured=[{"passed": True, "measured": True},
                        {"passed": False, "measured": True}])
    gate = st.data["acceptance"]["gate"]
    assert gate["rolled_back"] is True
    assert _git(staging, "rev-parse", "HEAD") == before
    assert gate["residual"]["failing_sets"] == 5


def test_resume_mid_repair_still_rolls_back_a_red_repair(
        leerie, tmp_path, monkeypatch):
    """Round-2 M2: the pre-repair verdict is persisted, so a resume after a
    red repair round still rolls back, and spent rounds count against the
    cap."""
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head, working_branch="main")
    staging = _staging(st, repo)
    before = _git(staging, "rev-parse", "HEAD")
    sets = _make_sets(leerie, st, 5)
    # State as an interrupted round 1 left it: baseline green, one round
    # spent, its (bad) commit on staging, no final verdict.
    (staging / "calc.py").write_text("def add(a, b):\n    raise SystemExit(3)\n")
    _git(staging, "commit", "-qam", "conformer: r1")
    st.data["acceptance"] = {"sets": sets, "gate": {
        "before_sha": before, "pre_tests_passed": True,
        "rounds": [{"round": 1, "results": []}]}}
    calls = _run_gate(leerie, monkeypatch, st, lambda _p: None,
                      measured=[{"passed": False, "measured": True}])
    gate = st.data["acceptance"]["gate"]
    assert len(calls) == 1                      # only round 2 left
    assert gate["rolled_back"] is True
    assert _git(staging, "rev-parse", "HEAD") == before


def test_repair_rounds_follow_the_cap(leerie, tmp_path, monkeypatch):
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head, working_branch="main")
    _staging(st, repo)
    st.data["acceptance"] = {"sets": _make_sets(leerie, st, 5)}
    calls = []

    async def fake_claude_p(**kw):
        calls.append(kw)
        return {}
    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    caps = _caps(leerie, 5)
    caps["acceptance_repair_rounds"] = 1
    asyncio.run(leerie._run_acceptance_gate(st.run_dir, st, caps, MODELS,
                                            EFFORTS))
    assert len(calls) == 1
    assert "round 1 of 1" in calls[0]["user_prompt"]


def test_pre_repair_state_is_persisted_before_the_first_round(
        leerie, tmp_path, monkeypatch):
    """A resume reads before_sha and pre_tests_passed from disk, so both
    must be saved before any repair can commit. before_sha is checked as
    the pre-repair test run starts — before anything else saves state."""
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head, working_branch="main")
    staging = _staging(st, repo)
    before = _git(staging, "rev-parse", "HEAD")
    st.data["acceptance"] = {"sets": _make_sets(leerie, st, 5)}
    on_disk = []

    def _disk_gate():
        return (json.loads((st.run_dir / "state.json").read_text())
                ["acceptance"].get("gate") or {})

    def fixer(p):
        on_disk.append(_disk_gate())
        _commit_fix(p)
    seq = [{"passed": True, "measured": True},
           {"passed": True, "measured": True}]
    at_pre = []

    async def fake_axes(tree, axes, st_, caps, **kw):
        if not at_pre:
            at_pre.append(_disk_gate())
        return {"tests": seq.pop(0)}

    async def fake_claude_p(**kw):
        fixer(Path(kw["cwd"]))
        return {}
    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    monkeypatch.setattr(leerie, "_measure_axes", fake_axes)
    asyncio.run(leerie._run_acceptance_gate(
        st.run_dir, st, _caps(leerie, 5), MODELS, EFFORTS))
    assert at_pre[0].get("before_sha") == before
    assert on_disk[0]["before_sha"] == before
    assert on_disk[0]["pre_tests_passed"] is True


def test_evaluation_installs_deps_before_running(leerie, tmp_path, monkeypatch):
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head, working_branch="main")
    staging = _staging(st, repo)
    st.data["acceptance"] = {"sets": _make_sets(leerie, st, 1)}
    events = []
    real_run_file = leerie._run_acceptance_file

    async def fake_deps(tree, *a, **k):
        events.append(("deps", tree))
        return True

    async def spy_run_file(st_, caps, tree, *a, **k):
        events.append(("run", tree))
        return await real_run_file(st_, caps, tree, *a, **k)
    monkeypatch.setattr(leerie, "_ensure_worktree_deps", fake_deps)
    monkeypatch.setattr(leerie, "_run_acceptance_file", spy_run_file)
    _run_gate(leerie, monkeypatch, st, _commit_fix)
    # Deps are installed in the evaluation worktree before its first
    # held-out file runs there — never in staging.
    eval_wt = str(st.run_dir / "worktrees" / "acceptance-eval")
    assert events[0] == ("deps", eval_wt)
    assert events[1] == ("run", eval_wt)


def test_budget_exhaustion_mid_repair_still_rolls_back(leerie, tmp_path,
                                                      monkeypatch):
    """Round 1 commits a repair that turns the test axis red; round 2's
    `bump_workers` exhausts the budget. The rollback check must still run
    (the budget error used to escape the gate before it)."""
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head, working_branch="main")
    staging = _staging(st, repo)
    before = _git(staging, "rev-parse", "HEAD")
    st.data["acceptance"] = {"sets": _make_sets(leerie, st, 5)}
    calls = []

    async def fake_claude_p(**kw):
        calls.append(kw)
        (Path(kw["cwd"]) / "calc.py").write_text(
            "def add(a, b):\n    return a * b\n")
        _git(kw["cwd"], "commit", "-qam", "conformer: r1")
        return {}
    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    seq = [{"passed": True, "measured": True},
           {"passed": False, "measured": True}]

    async def fake_axes(tree, axes, st_, caps, **kw):
        return {"tests": seq.pop(0)}
    monkeypatch.setattr(leerie, "_measure_axes", fake_axes)
    caps = _caps(leerie, 5)
    caps["max_total_workers"] = 1
    asyncio.run(leerie._run_acceptance_gate(st.run_dir, st, caps, MODELS,
                                            EFFORTS))
    gate = st.data["acceptance"]["gate"]
    assert len(calls) == 1
    assert "budget exhausted" in gate["rounds"][-1]["error"]
    assert gate["rolled_back"] is True
    assert _git(staging, "rev-parse", "HEAD") == before
    assert gate["final"] is not None


def test_no_hidden_sets_means_no_claim_of_hidden_tests(leerie, tmp_path,
                                                      monkeypatch, capsys):
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head, working_branch="main")
    _staging(st, repo)
    st.data["acceptance"] = {"sets": _make_sets(leerie, st, 3)}
    calls = _run_gate(leerie, monkeypatch, st, _commit_fix)
    up = calls[0]["user_prompt"]
    assert "not shown" not in up
    assert "case from set 3" in up                  # every set is shown
    assert "none is held back" in capsys.readouterr().out
    sets = [{"index": k, "cases": {f"acc/test_defect_{k}.py": [f"c{k}"]}}
            for k in range(1, 6)]
    res = [{"index": k, "passed": False,
            "failing_files": [f"acc/test_defect_{k}.py"]} for k in range(1, 6)]
    held = leerie._format_acceptance_failures_section(
        res, sets, {1, 2, 3}, 1, "x", held_back=True)
    assert "further tests you are not shown" in held


def test_a_section_with_only_hidden_failures_promises_no_names(leerie):
    """Only held-back sets fail: the round is told every failing test is
    one it is not shown, never that names follow an empty list."""
    sets = [{"index": k, "cases": {f"acc/test_defect_{k}.py": [f"c{k}"]}}
            for k in range(1, 6)]
    res = [{"index": k, "passed": k <= 3,
            "failing_files": [] if k <= 3 else [f"acc/test_defect_{k}.py"]}
           for k in range(1, 6)]
    text = leerie._format_acceptance_failures_section(
        res, sets, {1, 2, 3}, 1, "the contract", held_back=True)
    assert "named below" not in text
    assert "Every failing test is one you are not shown" in text
    assert "c4" not in text and "c5" not in text
    assert text.rstrip().endswith("DEFECT CONTRACT: the contract")


def test_a_shown_set_naming_no_cases_is_not_called_hidden(leerie):
    """The schema allows `cases: []`. A shown set failing on such a file
    has nothing to name, but nothing is hidden either (#283 review)."""
    sets = [{"index": k, "cases": {f"acc/test_defect_{k}.py": []}}
            for k in range(1, 4)]
    res = [{"index": 1, "passed": False,
            "failing_files": ["acc/test_defect_1.py"]},
           {"index": 2, "passed": True, "failing_files": []},
           {"index": 3, "passed": True, "failing_files": []}]
    text = leerie._format_acceptance_failures_section(
        res, sets, {1, 2, 3}, 1, "c", held_back=False)
    assert "not shown" not in text and "named below" not in text
    assert "declared no case names" in text


def test_an_unmeasured_shown_set_is_not_a_failure_without_names(leerie):
    """A shown set with no verdict on HEAD (`passed: False, unmeasured`) is
    not a failing shown set: with only held-back sets failing, the section
    still says every failing test is unseen (#284 review)."""
    sets = [{"index": k, "cases": {f"acc/test_defect_{k}.py": [f"c{k}"]}}
            for k in range(1, 5)]
    res = [{"index": 1, "passed": False, "unmeasured": True,
            "failing_files": []},
           {"index": 2, "passed": True, "failing_files": []},
           {"index": 3, "passed": False,
            "failing_files": ["acc/test_defect_3.py"]},
           {"index": 4, "passed": False,
            "failing_files": ["acc/test_defect_4.py"]}]
    text = leerie._format_acceptance_failures_section(
        res, sets, {1, 2}, 1, "c", held_back=True)
    assert "Every failing test is one you are not shown" in text
    assert "declared no case names" not in text


def test_shown_indices_hold_back_the_two_highest_from_four(leerie):
    def sets(*ix):
        return [{"index": i} for i in ix]
    assert leerie._acceptance_shown_indices(sets(1, 2, 3)) == {1, 2, 3}
    assert leerie._acceptance_shown_indices(sets(4, 1, 3, 2)) == {1, 2}
    assert leerie._acceptance_shown_indices(sets(1, 2, 3, 5, 7)) == {1, 2, 3}


def test_residual_counts_only_measured_sets(leerie, tmp_path, monkeypatch):
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head, working_branch="main")
    staging = _staging(st, repo)
    sets = _make_sets(leerie, st, 5)
    # Set 5 cannot run: the run's own tree already holds its file.
    (staging / "acc").mkdir()
    (staging / "acc" / "test_defect_5.py").write_text("# the run's own\n")
    _git(staging, "add", "-A")
    _git(staging, "commit", "-qm", "run's own test")
    st.data["acceptance"] = {"sets": sets}
    _run_gate(leerie, monkeypatch, st, lambda _p: None)
    res = st.data["acceptance"]["gate"]["residual"]
    assert (res["failing_sets"], res["total_sets"]) == (4, 4)


async def _no_sleep(*a, **k):
    return None


class _Killed(BaseException):
    """A process kill, as the gate sees it: not an `Exception`, so nothing
    the gate catches on purpose can absorb it."""


def _passing(n):
    return [{"index": k, "passed": True, "unmeasured": False,
             "failing_files": []} for k in range(1, n + 1)]


def _failing(n):
    return [{"index": k, "passed": False, "unmeasured": False,
             "failing_files": [f"acc/test_defect_{k}.py"]}
            for k in range(1, n + 1)]


@pytest.mark.parametrize("with_initial", [True, False])
def test_resume_after_a_passing_but_red_round_still_rolls_back(
        leerie, tmp_path, monkeypatch, with_initial):
    """Round-3 H1: round 1 made the sets pass but turned the test axis red,
    then the process died before the rollback check. Re-measured on resume,
    `initial` read the repaired tree as passing and skipped the check. Now
    `initial` is persisted, and a recorded before_sha alone resumes the
    repair (the `with_initial=False` case)."""
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head, working_branch="main")
    staging = _staging(st, repo)
    before = _git(staging, "rev-parse", "HEAD")
    _commit_fix(staging)
    gate = {"before_sha": before, "pre_tests_passed": True,
            "rounds": [{"round": 1, "results": _passing(5)}]}
    if with_initial:
        gate["initial"] = _failing(5)
    st.data["acceptance"] = {"sets": _make_sets(leerie, st, 5), "gate": gate}
    calls = _run_gate(leerie, monkeypatch, st, lambda _p: None,
                      measured=[{"passed": False, "measured": True}])
    gate = st.data["acceptance"]["gate"]
    assert calls == []                  # the round already passed
    assert gate["rolled_back"] is True
    assert _git(staging, "rev-parse", "HEAD") == before
    if with_initial:
        # What ships is the pre-repair tree, so its persisted verdict — not
        # one re-measured on the repaired tree — is the record.
        assert gate["final"] == _failing(5)
        assert gate["residual"]["failing_sets"] == 5


def test_the_pre_repair_verdict_is_on_disk_before_round_one(
        leerie, tmp_path, monkeypatch):
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head, working_branch="main")
    _staging(st, repo)
    st.data["acceptance"] = {"sets": _make_sets(leerie, st, 5)}

    async def dies(**kw):
        raise _Killed("killed during round 1")

    async def axes(tree, axes_, st_, caps, **kw):
        return {"tests": {"passed": True, "measured": True}}
    monkeypatch.setattr(leerie, "claude_p", dies)
    monkeypatch.setattr(leerie, "_measure_axes", axes)
    monkeypatch.setattr(leerie.State, "bump_workers", lambda self, caps: None)
    with pytest.raises(_Killed):
        asyncio.run(leerie._run_acceptance_gate(
            st.run_dir, st, _caps(leerie, 5), MODELS, EFFORTS))
    disk = json.loads((st.run_dir / "state.json").read_text())
    assert disk["acceptance"]["gate"]["initial"] == _failing(5)


def test_resume_after_a_passing_round_runs_no_further_round(
        leerie, tmp_path, monkeypatch):
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head, working_branch="main")
    staging = _staging(st, repo)
    before = _git(staging, "rev-parse", "HEAD")
    _commit_fix(staging)
    st.data["acceptance"] = {"sets": _make_sets(leerie, st, 5), "gate": {
        "before_sha": before, "pre_tests_passed": True,
        "initial": _failing(5),
        "rounds": [{"round": 1, "results": _passing(5)}]}}
    calls = _run_gate(leerie, monkeypatch, st, lambda _p: None,
                      measured=[{"passed": True, "measured": True}])
    gate = st.data["acceptance"]["gate"]
    assert calls == [] and "rolled_back" not in gate
    assert "residual" not in gate


def test_every_round_is_on_disk_before_the_rollback_check(
        leerie, tmp_path, monkeypatch):
    """A crash between the last round and the rollback check must not lose
    the round, or a resume would run it again past the cap."""
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head, working_branch="main")
    _staging(st, repo)
    st.data["acceptance"] = {"sets": _make_sets(leerie, st, 5)}
    seq = [{"passed": True, "measured": True}]

    async def axes(tree, axes_, st_, caps, **kw):
        if seq:
            return {"tests": seq.pop(0)}
        raise _Killed("killed before the post-repair measurement")

    async def fixer_claude_p(**kw):
        _commit_fix(Path(kw["cwd"]))
        return {}
    monkeypatch.setattr(leerie, "_measure_axes", axes)
    monkeypatch.setattr(leerie, "claude_p", fixer_claude_p)
    with pytest.raises(_Killed):
        asyncio.run(leerie._run_acceptance_gate(
            st.run_dir, st, _caps(leerie, 5), MODELS, EFFORTS))
    disk = json.loads((st.run_dir / "state.json").read_text())
    assert [r["round"] for r in disk["acceptance"]["gate"]["rounds"]] == [1]
    assert disk["acceptance"]["gate"]["initial"] == _failing(5)


def test_resume_after_the_rollback_decision_finishes_the_reset(
        leerie, tmp_path, monkeypatch):
    """Round-4 L1: the decision is saved before the reset, so a crash
    between them resumes into the reset — never a pass recorded for the
    reset tree."""
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head, working_branch="main")
    staging = _staging(st, repo)
    before = _git(staging, "rev-parse", "HEAD")
    _commit_fix(staging)
    st.data["acceptance"] = {"sets": _make_sets(leerie, st, 5), "gate": {
        "before_sha": before, "pre_tests_passed": True,
        "initial": _failing(5), "rolled_back": True,
        "rounds": [{"round": 1, "results": _passing(5)}]}}
    calls = _run_gate(leerie, monkeypatch, st, lambda _p: None,
                      measured=[])
    gate = st.data["acceptance"]["gate"]
    assert calls == []
    assert _git(staging, "rev-parse", "HEAD") == before
    assert gate["final"] == _failing(5) and gate["residual"]["failing_sets"] == 5


def test_a_recorded_rollback_runs_no_further_round(leerie, tmp_path,
                                                   monkeypatch):
    """Rollback decided after an errored round with rounds still under the
    cap: a resume must finish the reset, not repair the reset tree again."""
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head, working_branch="main")
    staging = _staging(st, repo)
    before = _git(staging, "rev-parse", "HEAD")
    _commit_fix(staging)
    st.data["acceptance"] = {"sets": _make_sets(leerie, st, 5), "gate": {
        "before_sha": before, "pre_tests_passed": True,
        "initial": _failing(5), "rolled_back": True,
        "rounds": [{"round": 1, "results": _failing(5)},
                   {"round": 2, "error": "budget"}]}}
    calls = []

    async def fake_claude_p(**kw):
        calls.append(kw)
        return {}
    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    caps = _caps(leerie, 5)
    caps["acceptance_repair_rounds"] = 3
    asyncio.run(leerie._run_acceptance_gate(st.run_dir, st, caps, MODELS,
                                            EFFORTS))
    assert calls == []
    assert _git(staging, "rev-parse", "HEAD") == before


def test_the_rollback_decision_is_on_disk_before_the_reset(
        leerie, tmp_path, monkeypatch):
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head, working_branch="main")
    _staging(st, repo)
    st.data["acceptance"] = {"sets": _make_sets(leerie, st, 5)}
    real_run = subprocess.run
    at_reset = []

    def spy(argv, *a, **k):
        if "reset" in argv and "--hard" in argv:
            at_reset.append(json.loads(
                (st.run_dir / "state.json").read_text())
                ["acceptance"]["gate"].get("rolled_back"))
        return real_run(argv, *a, **k)
    monkeypatch.setattr(leerie.subprocess, "run", spy)
    _run_gate(leerie, monkeypatch, st, _commit_fix,
              measured=[{"passed": True, "measured": True},
                        {"passed": False, "measured": True}])
    assert at_reset == [True]


def test_an_errored_round_is_saved_and_the_round_before_still_counts(
        leerie, tmp_path, monkeypatch):
    """Round-4: an errored round is on disk before the rollback check, and a
    resume after it reads the last round WITH results, not `initial`."""
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head, working_branch="main")
    _staging(st, repo)
    st.data["acceptance"] = {"sets": _make_sets(leerie, st, 5)}
    seq = [{"passed": True, "measured": True}]

    async def axes(tree, axes_, st_, caps, **kw):
        if seq:
            return {"tests": seq.pop(0)}
        raise _Killed("killed before the post-repair measurement")

    async def fails(**kw):
        # Commits, then errors: the rollback check then measures, and the
        # process dies there.
        _commit_fix(Path(kw["cwd"]))
        raise leerie.WorkerError("conformer crashed")
    monkeypatch.setattr(leerie, "_measure_axes", axes)
    monkeypatch.setattr(leerie, "claude_p", fails)
    caps = _caps(leerie, 5)
    caps["acceptance_repair_rounds"] = 1
    with pytest.raises(_Killed):
        asyncio.run(leerie._run_acceptance_gate(st.run_dir, st, caps, MODELS,
                                                EFFORTS))
    disk = json.loads((st.run_dir / "state.json").read_text())
    assert "error" in disk["acceptance"]["gate"]["rounds"][0]
    # Resume shape: round 1 with results, round 2 errored.
    round1 = _failing(5)
    round1[0] = dict(round1[0], passed=True, failing_files=[])
    st.data["acceptance"]["gate"] = {
        "before_sha": _git(st.run_dir / "worktrees" / "staging",
                           "rev-parse", "HEAD"),
        "pre_tests_passed": False, "initial": _failing(5),
        "rounds": [{"round": 1, "results": round1},
                   {"round": 2, "error": "x"}]}
    _run_gate(leerie, monkeypatch, st, lambda _p: None)
    assert st.data["acceptance"]["gate"]["residual"]["failing_sets"] == 4


def test_a_round_that_commits_then_errors_is_measured_again(
        leerie, tmp_path, monkeypatch):
    """Round-5 M1: the conformer committed a correct fix and then failed
    (a schema miss); the gate kept the stale failing verdict and handed a
    false residual to the next run."""
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head, working_branch="main")
    _staging(st, repo)
    st.data["acceptance"] = {"sets": _make_sets(leerie, st, 5)}

    async def commits_then_fails(**kw):
        _commit_fix(Path(kw["cwd"]))
        raise leerie.WorkerError("schema-valid output twice: no")
    monkeypatch.setattr(leerie, "claude_p", commits_then_fails)

    async def axes(tree, axes_, st_, caps, **kw):
        return {"tests": {"passed": True, "measured": True}}
    monkeypatch.setattr(leerie, "_measure_axes", axes)
    asyncio.run(leerie._run_acceptance_gate(
        st.run_dir, st, _caps(leerie, 5), MODELS, EFFORTS))
    gate = st.data["acceptance"]["gate"]
    assert gate["rounds"][0]["error"] and gate["rounds"][0]["results"]
    assert all(r["passed"] for r in gate["final"])
    assert "residual" not in gate


def test_a_red_repair_rolls_back_even_when_evaluation_breaks(
        leerie, tmp_path, monkeypatch):
    """Round-5 M2: an exception while placing a set after a red repair
    escaped the gate before the rollback check, and the red repair
    shipped."""
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head, working_branch="main")
    staging = _staging(st, repo)
    before = _git(staging, "rev-parse", "HEAD")
    st.data["acceptance"] = {"sets": _make_sets(leerie, st, 5)}
    real = leerie.shutil.copy2
    repaired = []

    def full(src, dst, *a, **k):
        if repaired:
            raise OSError(28, "No space left on device")
        return real(src, dst, *a, **k)
    monkeypatch.setattr(leerie.shutil, "copy2", full)

    def fixer(p):
        _commit_fix(p)
        repaired.append(1)
    _run_gate(leerie, monkeypatch, st, fixer,
              measured=[{"passed": True, "measured": True},
                        {"passed": False, "measured": True}])
    gate = st.data["acceptance"]["gate"]
    assert gate["rolled_back"] is True
    assert _git(staging, "rev-parse", "HEAD") == before


def test_a_red_repair_rolls_back_when_evaluation_cannot_be_set_up(
        leerie, tmp_path, monkeypatch):
    """Round-6 M2: an exception setting up the evaluation worktree after a
    repair commit escaped the gate before the rollback check."""
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head, working_branch="main")
    staging = _staging(st, repo)
    before = _git(staging, "rev-parse", "HEAD")
    st.data["acceptance"] = {"sets": _make_sets(leerie, st, 5)}
    real = leerie.subprocess.run
    repaired = []

    def eagain(argv, *a, **k):
        if repaired and "worktree" in argv and "add" in argv:
            raise BlockingIOError(11, "Resource temporarily unavailable")
        return real(argv, *a, **k)
    monkeypatch.setattr(leerie.subprocess, "run", eagain)

    def fixer(p):
        _commit_fix(p)
        repaired.append(1)
    _run_gate(leerie, monkeypatch, st, fixer,
              measured=[{"passed": True, "measured": True},
                        {"passed": False, "measured": True}])
    gate = st.data["acceptance"]["gate"]
    assert gate["rolled_back"] is True
    assert _git(staging, "rev-parse", "HEAD") == before


def test_an_unmeasurable_final_keeps_the_last_measured_residual(
        leerie, tmp_path, monkeypatch):
    """A final tree that cannot be measured at all (here: no repair commit,
    and the round's evaluation fails) must not erase the failing verdict
    the next run's planner needs."""
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head, working_branch="main")
    _staging(st, repo)
    st.data["acceptance"] = {"sets": _make_sets(leerie, st, 5)}
    real = leerie._evaluate_acceptance_sets_inner
    calls = []

    async def second_unmeasured(st_, caps, tree, sets, label, **kw):
        calls.append(label)
        if len(calls) == 1:
            return await real(st_, caps, tree, sets, label, **kw)
        return [{"index": s_["index"], "passed": False, "unmeasured": True,
                 "failing_files": []} for s_ in sets]
    monkeypatch.setattr(leerie, "_evaluate_acceptance_sets_inner",
                        second_unmeasured)
    _run_gate(leerie, monkeypatch, st, lambda _p: None)
    res = st.data["acceptance"]["gate"]["residual"]
    assert res["failing_sets"] == 5 and res["unmeasured_final"] is True


def test_two_unreadable_heads_still_count_as_moved(leerie, tmp_path,
                                                   monkeypatch):
    """The verdict's commit and the HEAD after an errored round both
    unreadable: "" == "" must not read as "unchanged", so the tree is
    measured again."""
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head, working_branch="main")
    _staging(st, repo)
    st.data["acceptance"] = {"sets": _make_sets(leerie, st, 5)}
    real = leerie._branch_head_sha
    n = []

    async def flaky(wt):
        n.append(1)
        # 1: the initial verdict's sha, 3: HEAD after the errored round —
        # both unreadable; 2 (before_sha) and later reads are real.
        return "" if len(n) in (1, 3) else await real(wt)
    monkeypatch.setattr(leerie, "_branch_head_sha", flaky)

    async def fails(**kw):
        raise leerie.WorkerError("schema miss")
    monkeypatch.setattr(leerie, "claude_p", fails)

    async def axes(tree, axes_, st_, caps, **kw):
        return {"tests": {"passed": True, "measured": True}}
    monkeypatch.setattr(leerie, "_measure_axes", axes)
    asyncio.run(leerie._run_acceptance_gate(
        st.run_dir, st, _caps(leerie, 5), MODELS, EFFORTS))
    assert st.data["acceptance"]["gate"]["rounds"][0].get("results")


def test_a_resumed_round_after_a_lost_commit_is_measured_again(
        leerie, tmp_path, monkeypatch):
    """Post-merge review M: round 1 committed, the process died before the
    round was saved, and the resumed round 1 failed without committing.
    HEAD at the resumed round's start already held the lost commit, so a
    "did this round move HEAD" check saw nothing and kept the stale
    verdict — a false residual for a correct fix. The comparison is now
    against the commit the verdict was measured on."""
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head, working_branch="main")
    staging = _staging(st, repo)
    st.data["acceptance"] = {"sets": _make_sets(leerie, st, 5)}

    async def axes(tree, axes_, st_, caps, **kw):
        return {"tests": {"passed": True, "measured": True}}
    monkeypatch.setattr(leerie, "_measure_axes", axes)

    async def commits_then_dies(**kw):
        _commit_fix(Path(kw["cwd"]))
        raise _Killed("interrupted mid-round")
    monkeypatch.setattr(leerie, "claude_p", commits_then_dies)
    with pytest.raises(_Killed):
        asyncio.run(leerie._run_acceptance_gate(
            st.run_dir, st, _caps(leerie, 5), MODELS, EFFORTS))
    # Resume: state as saved on disk, the lost commit still on staging.
    st.data = json.loads((st.run_dir / "state.json").read_text())
    assert st.data["acceptance"]["gate"]["rounds"] == []

    async def fails(**kw):
        raise leerie.WorkerError("worker timed out")
    monkeypatch.setattr(leerie, "claude_p", fails)
    asyncio.run(leerie._run_acceptance_gate(
        st.run_dir, st, _caps(leerie, 5), MODELS, EFFORTS))
    gate = st.data["acceptance"]["gate"]
    assert _git(staging, "log", "-1", "--format=%s") == "conformer: fix add"
    assert gate["rounds"][0]["results"] and all(
        r["passed"] for r in gate["final"])
    assert "residual" not in gate


def test_branch_head_sha_never_raises(leerie, tmp_path, monkeypatch):
    async def eagain(*a, **k):
        raise BlockingIOError(11, "Resource temporarily unavailable")
    monkeypatch.setattr(leerie, "run_proc", eagain)
    assert asyncio.run(leerie._branch_head_sha(str(tmp_path))) == ""


def test_a_fork_failure_reading_head_after_a_repair_still_rolls_back(
        leerie, tmp_path, monkeypatch):
    """Round-7 M3: reading HEAD after a red repair raised EAGAIN out of the
    gate, past the rollback check."""
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head, working_branch="main")
    staging = _staging(st, repo)
    before = _git(staging, "rev-parse", "HEAD")
    st.data["acceptance"] = {"sets": _make_sets(leerie, st, 5)}
    real = leerie.run_proc
    repaired, failed = [], []

    async def eagain_once(argv, *a, **k):
        if repaired and not failed and argv[:2] == ["git", "rev-parse"]:
            failed.append(1)
            raise BlockingIOError(11, "Resource temporarily unavailable")
        return await real(argv, *a, **k)
    monkeypatch.setattr(leerie, "run_proc", eagain_once)

    async def commits_then_fails(**kw):
        (Path(kw["cwd"]) / "calc.py").write_text(
            "def add(a, b):\n    return 99\n")
        _git(kw["cwd"], "commit", "-qam", "conformer: bad")
        repaired.append(1)
        raise leerie.WorkerError("schema miss")
    monkeypatch.setattr(leerie, "claude_p", commits_then_fails)
    seq = [{"passed": True, "measured": True},
           {"passed": False, "measured": True}]

    async def axes(tree, axes_, st_, caps, **kw):
        return {"tests": seq.pop(0)}
    monkeypatch.setattr(leerie, "_measure_axes", axes)
    asyncio.run(leerie._run_acceptance_gate(
        st.run_dir, st, _caps(leerie, 5), MODELS, EFFORTS))
    assert failed == [1]
    gate = st.data["acceptance"]["gate"]
    assert gate["rolled_back"] is True
    assert _git(staging, "rev-parse", "HEAD") == before


def test_a_failure_spawning_a_later_round_still_rolls_back(
        leerie, tmp_path, monkeypatch):
    """Round-8 M1: `claude_p` lets an OSError spawning the worker through;
    raised in round 2 after round 1's red commit, it escaped the gate past
    the rollback check."""
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head, working_branch="main")
    staging = _staging(st, repo)
    before = _git(staging, "rev-parse", "HEAD")
    st.data["acceptance"] = {"sets": _make_sets(leerie, st, 5)}
    calls = []

    async def red_then_eagain(**kw):
        calls.append(1)
        if len(calls) == 2:
            raise BlockingIOError(11, "Resource temporarily unavailable")
        (Path(kw["cwd"]) / "calc.py").write_text(
            "def add(a, b):\n    return 99\n")
        _git(kw["cwd"], "commit", "-qam", "conformer: red")
        return {}
    monkeypatch.setattr(leerie, "claude_p", red_then_eagain)
    seq = [{"passed": True, "measured": True},
           {"passed": False, "measured": True}]

    async def axes(tree, axes_, st_, caps, **kw):
        return {"tests": seq.pop(0)}
    monkeypatch.setattr(leerie, "_measure_axes", axes)
    asyncio.run(leerie._run_acceptance_gate(
        st.run_dir, st, _caps(leerie, 5), MODELS, EFFORTS))
    gate = st.data["acceptance"]["gate"]
    assert len(calls) == 2 and "error" in gate["rounds"][1]
    assert gate["rolled_back"] is True
    assert _git(staging, "rev-parse", "HEAD") == before


def test_a_repair_that_breaks_the_install_is_rolled_back(
        leerie, tmp_path, monkeypatch):
    """Round-7 M4: staging's memoised install never noticed; every set went
    unmeasured and the broken manifest shipped."""
    repo, head = _repo(tmp_path)
    (repo / "requirements.txt").write_text("ok\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "manifest")
    head = _git(repo, "rev-parse", "HEAD")
    st = _st(leerie, tmp_path, repo, head, working_branch="main")
    st.data["provision"] = {"recipe": [{"kind": "install", "command": [
        "bash", "-c", "grep -qx ok requirements.txt"]}]}
    staging = _staging(st, repo)
    before = _git(staging, "rev-parse", "HEAD")
    st.data["acceptance"] = {"sets": _make_sets(leerie, st, 5)}

    def breaks_manifest(p):
        (p / "requirements.txt").write_text("nonexistent-pkg==0.0.0\n")
        _git(p, "commit", "-qam", "conformer: deps")
    _run_gate(leerie, monkeypatch, st, breaks_manifest,
              measured=[{"passed": False, "measured": True}])
    gate = st.data["acceptance"]["gate"]
    assert gate["rolled_back"] is True
    assert gate["rollback_reason"] == "left the held-out sets unmeasurable"
    assert _git(staging, "rev-parse", "HEAD") == before
    assert gate["residual"]["failing_sets"] == 5


def _counted_recipe(st, tmp_path, fail_from: int, fail_to: int):
    """An install that fails on calls fail_from..fail_to (1-based)."""
    counter = tmp_path / "install-calls"
    st.data["provision"] = {"recipe": [{"kind": "install", "command": [
        "bash", "-c",
        f'n=$(( $(cat {counter} 2>/dev/null || echo 0) + 1 )); '
        f'echo $n > {counter}; '
        f'[ $n -lt {fail_from} ] || [ $n -gt {fail_to} ]']}]}


def test_a_one_off_install_failure_keeps_a_correct_repair(
        leerie, tmp_path, monkeypatch):
    """Round-8 M1: the unmeasurable-sets rollback reset a correct fix when
    the post-repair evaluation's fresh install failed once."""
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head, working_branch="main")
    staging = _staging(st, repo)
    st.data["acceptance"] = {"sets": _make_sets(leerie, st, 5)}
    _counted_recipe(st, tmp_path, 2, 2)       # the round-1 evaluation fails
    _run_gate(leerie, monkeypatch, st, _commit_fix,
              measured=[{"passed": True, "measured": True},
                        {"passed": True, "measured": True}])
    gate = st.data["acceptance"]["gate"]
    assert "rolled_back" not in gate
    assert _git(staging, "log", "-1", "--format=%s") == "conformer: fix add"
    assert all(r["passed"] for r in gate["final"]) and "residual" not in gate
    # The measured retry replaces the round's unmeasured record.
    assert all(r["passed"] for r in gate["rounds"][-1]["results"])


def test_a_lasting_environment_failure_keeps_the_repair(
        leerie, tmp_path, monkeypatch, capsys):
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head, working_branch="main")
    staging = _staging(st, repo)
    st.data["acceptance"] = {"sets": _make_sets(leerie, st, 5)}
    _counted_recipe(st, tmp_path, 2, 99)      # every later install fails
    _run_gate(leerie, monkeypatch, st, _commit_fix,
              measured=[{"passed": True, "measured": True},
                        {"passed": True, "measured": True}])
    gate = st.data["acceptance"]["gate"]
    assert "rolled_back" not in gate
    assert _git(staging, "log", "-1", "--format=%s") == "conformer: fix add"
    assert "an environment failure" in capsys.readouterr().out
    assert gate["residual"]["unmeasured_final"] is True


def test_a_failed_reset_is_recorded_not_assumed(leerie, tmp_path,
                                                monkeypatch, capsys):
    """Round-7 L1: a stale index.lock makes `reset --hard` fail; the gate
    recorded `rolled_back` while the red repair shipped."""
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head, working_branch="main")
    staging = _staging(st, repo)
    st.data["acceptance"] = {"sets": _make_sets(leerie, st, 5)}

    def red_and_locked(p):
        _commit_fix(p)
        (Path(_git(p, "rev-parse", "--git-dir")) / "index.lock").write_text("")
    _run_gate(leerie, monkeypatch, st, red_and_locked,
              measured=[{"passed": True, "measured": True},
                        {"passed": False, "measured": True}])
    gate = st.data["acceptance"]["gate"]
    assert gate["rolled_back"] is True and gate["rollback_failed"] is True
    assert "FAILED" in capsys.readouterr().out
    assert _git(staging, "log", "-1", "--format=%s") == "conformer: fix add"
    # What ships is the repair's tree, so the record describes it, not the
    # pre-repair tree (round-8 L) — and the next run hears that a repair
    # the gate meant to revert is on the branch, though its sets pass
    # (round-9 L).
    assert all(r["passed"] for r in gate["final"])
    assert gate["residual"] == {"failing_sets": 0, "total_sets": 5,
                                "cases": [], "rollback_failed": True}


def test_an_unmeasurable_repair_whose_reset_fails_says_so(
        leerie, tmp_path, monkeypatch):
    """Round-9 M: the residual read "environment failure" when a repair had
    broken the measurement and its rollback then failed."""
    repo, head = _repo(tmp_path)
    (repo / "requirements.txt").write_text("ok\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "manifest")
    head = _git(repo, "rev-parse", "HEAD")
    st = _st(leerie, tmp_path, repo, head, working_branch="main")
    st.data["provision"] = {"recipe": [{"kind": "install", "command": [
        "bash", "-c", "grep -qx ok requirements.txt"]}]}
    _staging(st, repo)
    st.data["acceptance"] = {"sets": _make_sets(leerie, st, 5)}

    def breaks_and_locks(p):
        (p / "requirements.txt").write_text("nonexistent-pkg==0.0.0\n")
        _git(p, "commit", "-qam", "conformer: deps")
        (Path(_git(p, "rev-parse", "--git-dir")) / "index.lock").write_text("")
    monkeypatch.setattr(leerie.asyncio, "sleep", _no_sleep)
    _run_gate(leerie, monkeypatch, st, breaks_and_locks,
              measured=[{"passed": False, "measured": True}])
    res = st.data["acceptance"]["gate"]["residual"]
    assert res["rollback_failed"] is True and res["unmeasured_final"] is True
    d = st.run_dir.parent / "run-next"
    d.mkdir()
    (d / "orchestrator.exit_code").write_text("0")
    nxt = leerie.State(st.run_dir.parent.parent, "run-next", repo_root=repo)
    nxt.data = {"task": st.data["task"]}
    st.save()
    (st.run_dir / "orchestrator.exit_code").write_text("0")
    unmet = leerie._prior_delivery_residual(nxt)["acceptance_unmet"]
    assert unmet["rollback_failed"] is True
    assert unmet["unmeasured_final"] is True


def test_a_reset_that_cannot_spawn_is_tried_again(leerie, tmp_path,
                                                 monkeypatch):
    repo, head = _repo(tmp_path)
    before = _git(repo, "rev-parse", "HEAD")
    _commit_fix(repo)
    real = leerie.subprocess.run
    n = []

    def eagain_once(argv, *a, **k):
        if "reset" in argv and not n:
            n.append(1)
            raise BlockingIOError(11, "Resource temporarily unavailable")
        return real(argv, *a, **k)
    monkeypatch.setattr(leerie.subprocess, "run", eagain_once)
    monkeypatch.setattr(leerie.asyncio, "sleep", _no_sleep)
    assert asyncio.run(leerie._acceptance_rollback(str(repo), before))
    assert _git(repo, "rev-parse", "HEAD") == before


def test_an_unreadable_head_after_a_reset_is_read_again(leerie, tmp_path,
                                                        monkeypatch):
    repo, head = _repo(tmp_path)
    staging = repo
    before = _git(repo, "rev-parse", "HEAD")
    _commit_fix(repo)
    real = leerie._branch_head_sha
    n = []

    async def blip(wt):
        n.append(1)
        return "" if len(n) == 1 else await real(wt)
    monkeypatch.setattr(leerie, "_branch_head_sha", blip)
    monkeypatch.setattr(leerie.asyncio, "sleep", _no_sleep)
    assert asyncio.run(leerie._acceptance_rollback(str(staging), before))
    assert _git(repo, "rev-parse", "HEAD") == before


@pytest.mark.parametrize("raises_on", ["pre", "post"])
def test_a_test_axis_measurement_that_raises_never_escapes(
        leerie, tmp_path, monkeypatch, raises_on):
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head, working_branch="main")
    _staging(st, repo)
    st.data["acceptance"] = {"sets": _make_sets(leerie, st, 5)}
    n = []

    async def axes(tree, axes_, st_, caps, **kw):
        n.append(1)
        if raises_on == "pre" or len(n) == 2:
            raise BlockingIOError(11, "Resource temporarily unavailable")
        return {"tests": {"passed": True, "measured": True}}
    monkeypatch.setattr(leerie, "_measure_axes", axes)
    _run_gate(leerie, monkeypatch, st, _commit_fix)
    assert st.data["acceptance"]["gate"]["final"] is not None


def test_no_rollback_target_means_no_repair(leerie, tmp_path, monkeypatch):
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head, working_branch="main")
    _staging(st, repo)
    st.data["acceptance"] = {"sets": _make_sets(leerie, st, 5)}

    async def unreadable(wt):
        return ""
    monkeypatch.setattr(leerie, "_branch_head_sha", unreadable)
    calls = _run_gate(leerie, monkeypatch, st, _commit_fix,
                      measured=[{"passed": True, "measured": True}])
    gate = st.data["acceptance"]["gate"]
    assert calls == []
    assert gate["residual"]["failing_sets"] == 5


def test_a_one_off_install_failure_mid_loop_keeps_the_next_round(
        leerie, tmp_path, monkeypatch):
    """Round-9 LOW: the round's own evaluation failed to install once, read
    as "nothing measured", and the loop stopped with a round left. It is
    measured again inside the loop now."""
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head, working_branch="main")
    _staging(st, repo)
    st.data["acceptance"] = {"sets": _make_sets(leerie, st, 5)}
    _counted_recipe(st, tmp_path, 2, 2)       # round 1's evaluation fails
    calls = _run_gate(leerie, monkeypatch, st, lambda _p: None,
                      measured=[{"passed": True, "measured": True}])
    gate = st.data["acceptance"]["gate"]
    assert len(calls) == 2
    assert gate["rounds"][0]["retried"] is True
    assert leerie._acceptance_measured(gate["rounds"][0]["results"])


def test_a_commit_already_retried_in_the_loop_is_not_retried_again(
        leerie, tmp_path, monkeypatch):
    """The post-loop retry is skipped for a commit the round's own retry
    already measured: straight to the pre-repair control."""
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head, working_branch="main")
    _staging(st, repo)
    st.data["acceptance"] = {"sets": _make_sets(leerie, st, 5)}
    _counted_recipe(st, tmp_path, 2, 99)      # every later install fails
    labels = []
    real = leerie._evaluate_acceptance_sets_inner

    async def spy(st_, caps, tree, sets, label, **kw):
        labels.append(label)
        return await real(st_, caps, tree, sets, label, **kw)
    monkeypatch.setattr(leerie, "_evaluate_acceptance_sets_inner", spy)
    _run_gate(leerie, monkeypatch, st, _commit_fix,
              measured=[{"passed": True, "measured": True},
                        {"passed": True, "measured": True}])
    assert labels == ["gate-initial", "gate-r1", "gate-r1-retry",
                      "gate-before"]


def test_gate_is_resume_idempotent(leerie, tmp_path, monkeypatch):
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head, working_branch="main")
    _staging(st, repo)
    st.data["acceptance"] = {"sets": _make_sets(leerie, st, 1),
                             "gate": {"final": []}}
    assert _run_gate(leerie, monkeypatch, st, _commit_fix) == []


# --- cross-run residual, hiding, wiring --------------------------------------

def test_residual_reaches_the_next_runs_planner_ctx(leerie, tmp_path):
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head)
    d = st.run_dir.parent / "run-prev"
    d.mkdir()
    (d / "orchestrator.exit_code").write_text("0")
    (d / "state.json").write_text(json.dumps({
        "task": st.data["task"],
        "acceptance": {"gate": {"residual": {
            "failing_sets": 3, "total_sets": 5,
            "cases": ["add sums two ints"]}}}}))
    res = leerie._prior_delivery_residual(st)
    assert res["acceptance_unmet"]["cases"] == ["add sums two ints"]
    assert res["acceptance_unmet"]["unmeasured_final"] is False
    assert "acceptance_unmet" in leerie._load_prompt("planner")


def test_fixers_cannot_read_the_sets(leerie, tmp_path):
    run_dir = tmp_path / "runs" / "r"
    for w in ("implementer", "conformer"):
        # Round-3 M4: the writers' stream transcripts (logs/acceptance-<k>.log)
        # carry the test source, evaluation logs the runner output, and
        # calls.ndjson the writers' responses.
        assert leerie._acceptance_read_denials(w, run_dir).split(",") == [
            f"Read(/{run_dir}/acceptance/**)",
            f"Read(/{run_dir}/worktrees/acceptance-eval/**)",
            f"Read(/{run_dir}/logs/**)",
            f"Read(/{run_dir}/orchestrator.log)",
            f"Read(/{run_dir}/calls.ndjson)",
            f"Read(/{run_dir}/state.json)",
            "Read(~/.claude/projects/**)"]
    # The fixers' prompts read only these run-directory paths, none denied.
    for w in ("implementer", "conformer"):
        prompt = leerie._load_prompt(w)
        for used in ("subtasks/", "criteria/"):
            assert used in prompt
    for w in ("planner", "acceptance_writer", "delivery_judge"):
        assert leerie._acceptance_read_denials(w, run_dir) == ""
    src = inspect.getsource(leerie.claude_p)
    assert "_acceptance_read_denials(schema_key, st.run_dir)" in src


def test_wiring_order_in_run_phases(leerie):
    src = inspect.getsource(leerie._run_phases)
    i_audit = src.index("await phase_defect_scope_audit(")
    i_write = src.index("await _acceptance_write_or_skip(")
    i_plan = src.index("plans = await phase_plan(")
    assert i_audit < i_write < i_plan
    i_recheck = src.index("await _run_delivery_recheck(")
    i_gate = src.index("await _run_acceptance_gate(")
    i_final = src.index("await phase_finalize(")
    assert i_recheck < i_gate < i_final
