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


def _git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


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
        for rel, (kind, text, cases) in files_by_set.get(k, {}).items():
            p = Path(kw["cwd"]) / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text)
            decl.append({"path": rel, "kind": kind, "cases": cases})
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


def test_evaluation_leaves_the_tree_clean_and_never_overwrites(leerie, tmp_path):
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head)
    staging = _staging(st, repo)
    sets = _make_sets(leerie, st, 2)
    (staging / "acc").mkdir()
    (staging / "acc" / "test_defect_1.py").write_text("# the run's own file\n")
    res = asyncio.run(leerie._evaluate_acceptance_sets(
        st, _caps(leerie, 2), str(staging), sets, "t"))
    # Set 1 collides with the run's own file: it could not run that file, so
    # it is unmeasured — never a pass (round-1 review M1/M2).
    assert [(r["passed"], r["unmeasured"]) for r in res] == [
        (False, True), (False, False)]
    assert (staging / "acc" / "test_defect_1.py").read_text() == "# the run's own file\n"
    assert not (staging / "acc" / "test_defect_2.py").exists()
    # Nothing the call created is left behind — not even bytecode.
    left = subprocess.run(["git", "-C", str(staging), "ls-files", "--others"],
                          capture_output=True, text=True).stdout.split()
    # Nothing of the hidden sets lingers where they were placed (the run's
    # own file stays); residue elsewhere — the repo's own bytecode at the
    # root — is the runner's, not a hidden set's, and is left alone.
    assert [p for p in left if p.startswith("acc/")] == ["acc/test_defect_1.py"]


def test_evaluation_removes_the_directories_it_created(leerie, tmp_path):
    """`git ls-files --others` never lists an empty directory, so only the
    created-directory cleanup keeps a hidden set's directory name out of
    the tree a fixer works in."""
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head)
    staging = _staging(st, repo)
    sets = _make_sets(leerie, st, 1)
    d = Path(sets[0]["dir"])
    (d / "acc" / "deep").mkdir()
    (d / "acc" / "test_defect_1.py").rename(d / "acc" / "deep" / "test_defect_1.py")
    sets[0]["defect_files"] = ["acc/deep/test_defect_1.py"]
    asyncio.run(leerie._evaluate_acceptance_sets(
        st, _caps(leerie, 1), str(staging), sets, "t"))
    assert not (staging / "acc").exists()


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
    byp, cache = (leerie._acceptance_is_byproduct_path,
                  leerie._acceptance_is_cache_path)
    assert byp("__pycache__/calc.cpython-314.pyc")
    assert byp(".pytest_cache/v/cache/lastfailed")
    assert byp("tests/x.pyc")
    assert not byp(".venv/lib/__pycache__/site.cpython-314.pyc")
    assert not byp("tests/acc_helpers.py")
    assert cache(".venv/lib/site.py") and cache("web/node_modules/a/b.js")
    assert not cache("tests/conftest.py")


def test_existing_pytest_cache_is_restored_and_a_new_one_removed(
        leerie, tmp_path):
    """Round-3 M5: pytest's cache recorded the hidden tests' ids in the
    tree a fixer works in."""
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


def _provisioning_runner(repo: Path):
    (repo / ".leerie" / "config.toml").write_text(
        'test_scoped = "mkdir -p .venv/lib build acc/node_modules '
        '&& touch .venv/lib/site.py build/out.txt acc/node_modules/m.js '
        'prov.lock && python3 -m pytest -q -p no:cacheprovider '
        '{test_files}"\n')


@pytest.mark.parametrize("where", ["acc", "."])
def test_runner_provisioning_outside_the_set_survives_cleanup(
        leerie, tmp_path, where):
    """The cleanup is scoped to where held-out files were placed (round-3
    L6: a root-level set used to match every path in the tree)."""
    repo, head = _repo(tmp_path)
    _provisioning_runner(repo)
    st = _st(leerie, tmp_path, repo, head)
    staging = _staging(st, repo)
    sets = _make_sets(leerie, st, 1)
    # acc/ already exists (a directory the evaluation creates goes whole).
    (staging / "acc").mkdir()
    (staging / "acc" / "keep.txt").write_text("the run's own\n")
    if where == ".":
        d = Path(sets[0]["dir"])
        for rel in ("test_defect_1.py", "test_control_1.py"):
            (d / "acc" / rel).rename(d / rel)
        sets[0]["defect_files"] = ["test_defect_1.py"]
        sets[0]["control_files"] = ["test_control_1.py"]
    asyncio.run(leerie._evaluate_acceptance_sets(
        st, _caps(leerie, 1), str(staging), sets, "t"))
    assert (staging / ".venv" / "lib" / "site.py").exists()
    assert (staging / "build" / "out.txt").exists()
    # A provisioned environment beneath the set's own directory stays too.
    assert (staging / "acc" / "node_modules" / "m.js").exists()
    # A root-level by-product of a root-level set is the set's to clean;
    # beneath acc/ it is not.
    assert (staging / "prov.lock").exists() is (where == "acc")


def test_a_failed_snapshot_cleans_nothing(leerie, tmp_path, monkeypatch):
    """When the before-snapshot fails, nothing can be told apart from the
    run's own untracked files, so nothing is deleted."""
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head)
    staging = _staging(st, repo)
    (staging / "acc").mkdir()
    (staging / "acc" / "run_own_notes.txt").write_text("the run's own\n")
    real_run = subprocess.run
    failed = []

    def flaky(argv, *a, **k):
        if "ls-files" in argv and not failed:
            failed.append(1)
            return subprocess.CompletedProcess(argv, 128, "", "boom")
        return real_run(argv, *a, **k)
    monkeypatch.setattr(leerie.subprocess, "run", flaky)
    asyncio.run(leerie._evaluate_acceptance_sets(
        st, _caps(leerie, 1), str(staging), _make_sets(leerie, st, 1), "t"))
    assert (staging / "acc" / "run_own_notes.txt").exists()


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


def test_gate_installs_deps_in_staging(leerie, tmp_path, monkeypatch):
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head, working_branch="main")
    staging = _staging(st, repo)
    st.data["acceptance"] = {"sets": _make_sets(leerie, st, 1)}
    events = []
    real_run_file = leerie._run_acceptance_file

    async def fake_deps(tree, *a, **k):
        events.append(("deps", tree))

    async def spy_run_file(st_, caps, tree, *a, **k):
        events.append(("run", tree))
        return await real_run_file(st_, caps, tree, *a, **k)
    monkeypatch.setattr(leerie, "_ensure_worktree_deps", fake_deps)
    monkeypatch.setattr(leerie, "_run_acceptance_file", spy_run_file)
    _run_gate(leerie, monkeypatch, st, _commit_fix)
    # Deps are in place before the first held-out file runs on staging.
    assert events[0] == ("deps", str(staging.resolve()))
    assert events[1][0] == "run"


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
        raise RuntimeError("killed during round 1")

    async def axes(tree, axes_, st_, caps, **kw):
        return {"tests": {"passed": True, "measured": True}}
    monkeypatch.setattr(leerie, "claude_p", dies)
    monkeypatch.setattr(leerie, "_measure_axes", axes)
    monkeypatch.setattr(leerie.State, "bump_workers", lambda self, caps: None)
    with pytest.raises(RuntimeError):
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
        raise RuntimeError("killed before the post-repair measurement")

    async def fixer_claude_p(**kw):
        _commit_fix(Path(kw["cwd"]))
        return {}
    monkeypatch.setattr(leerie, "_measure_axes", axes)
    monkeypatch.setattr(leerie, "claude_p", fixer_claude_p)
    with pytest.raises(RuntimeError):
        asyncio.run(leerie._run_acceptance_gate(
            st.run_dir, st, _caps(leerie, 5), MODELS, EFFORTS))
    disk = json.loads((st.run_dir / "state.json").read_text())
    assert [r["round"] for r in disk["acceptance"]["gate"]["rounds"]] == [1]
    assert disk["acceptance"]["gate"]["initial"] == _failing(5)


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
    assert "acceptance_unmet" in leerie._load_prompt("planner")


def test_fixers_cannot_read_the_sets(leerie, tmp_path):
    run_dir = tmp_path / "runs" / "r"
    for w in ("implementer", "conformer"):
        # Round-3 M4: the writers' stream transcripts (logs/acceptance-<k>.log)
        # carry the test source, evaluation logs the runner output, and
        # calls.ndjson the writers' responses.
        assert leerie._acceptance_read_denials(w, run_dir).split(",") == [
            f"Read(/{run_dir}/acceptance/**)",
            f"Read(/{run_dir}/logs/acceptance-*)",
            f"Read(/{run_dir}/calls.ndjson)"]
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
