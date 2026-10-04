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
import subprocess
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
    assert subprocess.run(
        ["git", "-C", str(staging), "ls-files", "--others"],
        capture_output=True, text=True).stdout.split() == ["acc/test_defect_1.py"]


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
    used to raise SameFileError out of asyncio.gather and lose every set."""
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
            return {"files": [{"path": str(p), "kind": "defect", "cases": ["a"]}]}
        if k == 2:
            raise OSError("writer 2 blew up")
        return {"files": [{"path": f"acc/test_defect_{k}.py", "kind": "defect",
                           "cases": ["c"]}]}

    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    acc = asyncio.run(leerie.phase_acceptance_write(
        st.data["task"], st, _caps(leerie, 3), MODELS, EFFORTS))
    assert [s["index"] for s in acc["sets"]] == [3]
    assert sorted(calls) == [1, 2, 3]


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
        assert leerie._acceptance_read_denials(w, run_dir) == \
            f"Read(/{run_dir}/acceptance/**)"
    for w in ("planner", "acceptance_writer", "delivery_judge"):
        assert leerie._acceptance_read_denials(w, run_dir) == ""
    src = inspect.getsource(leerie.claude_p)
    assert "_acceptance_read_denials(schema_key, st.run_dir)" in src


def test_wiring_order_in_run_phases(leerie):
    src = inspect.getsource(leerie._run_phases)
    i_audit = src.index("await phase_defect_scope_audit(")
    i_write = src.index("await phase_acceptance_write(")
    i_plan = src.index("plans = await phase_plan(")
    assert i_audit < i_write < i_plan
    i_recheck = src.index("await _run_delivery_recheck(")
    i_gate = src.index("await _run_acceptance_gate(")
    i_final = src.index("await phase_finalize(")
    assert i_recheck < i_gate < i_final
