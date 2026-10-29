"""A held-out set the final tree leaves unmeasured is never silent (DESIGN §8).

Review round 7, M3: a final held-out result that is `unmeasured` was read by
nothing — `_decide_outcome` skips it (no evidence is not a failure) and the
PR's end-state section had no line for it, so a run whose change made a
held-out test hang ended `done` with an empty section. Every cause an
evaluation can end a set unmeasured by now records a code on the result,
and `_acceptance_unmeasured_lines` lists the set under "Needs human
verification" with each file's reason. The outcome is unchanged.
"""
from __future__ import annotations

import ast
import asyncio
import inspect
import textwrap
from pathlib import Path

import pytest

from tests.conftest import run_git_cwd_first_stdout as _git

REL = "acc/test_defect_1.py"
CONTROL = "acc/test_control_1.py"
HEADING = "**Held-out acceptance sets the run could not measure**"


def _caps(leerie, **kw):
    caps = dict(leerie.DEFAULT_CAPS)
    caps.update(worker_timeout_sec=120, confirm_pairs=1,
                confirm_pairs_escalated=1, confirm_gate_wait_sec=0,
                confirm_budget_sec=30, acceptance_intermittent_repeats=0)
    caps.update(kw)
    return caps


@pytest.fixture(autouse=True)
def _no_pressure_wait(leerie, monkeypatch):
    async def gate(caps, **_kw):
        return {"waited_sec": 0.0, "timed_out": False, "psi": 0.0,
                "readable": True}
    monkeypatch.setattr(leerie, "_confirm_gate", gate)


def _set(tmp_path: Path) -> tuple[dict, Path]:
    d = tmp_path / "set-1"
    (d / "acc").mkdir(parents=True)
    (d / REL).write_text("")
    (d / CONTROL).write_text("")
    wt = tmp_path / "wt"
    wt.mkdir()
    s_ = {"index": 1, "valid": True, "dir": str(d),
          "defect_files": [REL], "control_files": [CONTROL],
          "cases": {REL: ["a"], CONTROL: ["c"]},
          "base_failing_cases": {REL: ["acc/test_defect_1.py::test_a"]}}
    return s_, wt


def _st(leerie, tmp_path):
    st = leerie.State(tmp_path / "state", "r")
    st.data = {"task": "t", "categories": ["bug-fixing"]}
    return st


def _placed(leerie, monkeypatch, tmp_path, run, **caps):
    st = _st(leerie, tmp_path)
    s_, wt = _set(tmp_path)
    monkeypatch.setattr(leerie, "_run_acceptance_file", run)
    (res,) = asyncio.run(leerie._acceptance_run_placed(
        st, _caps(leerie, **caps), wt, [s_], "t"))
    return st, s_, res, wt


def _finish(leerie, st, sets, final):
    st.data["acceptance"] = {"sets": sets, "gate": {"final": final}}
    outcome = leerie._decide_outcome(st, any_commits=True)
    return outcome, leerie._format_pr_end_state_section(st.data)


def _nhv_line(body: str, needle: str) -> str:
    assert "## Needs human verification" in body, body
    nhv = body.split("## Needs human verification", 1)[1]
    assert HEADING in nhv, nhv
    line = next(x for x in nhv.splitlines() if needle in x)
    # The line sits under its own group heading, not another group's.
    assert nhv.index(HEADING) < nhv.index(line)
    return line


# --- each per-file cause, driven through `_acceptance_run_placed` ---------

def _first_then(first: dict, rerun):
    async def run(st_, caps_, tree, rel, log_path, label, *, cases_out=None,
                  **_kw):
        if rel == CONTROL:
            return True
        if "-confirm" in label:
            return await rerun(caps_, cases_out)
        cases_out.update(first)
        return False
    return run


async def _hang(caps_, obs):
    await asyncio.sleep(caps_["worker_timeout_sec"] + 0.05)
    obs.update(resource=True, timed_out=True)
    return False


@pytest.mark.parametrize("first, code, words", [
    ({"resource": True, "timed_out": True}, "timed_out",
     "timed out on its first run"),
    ({"resource": True, "killed": True}, "killed",
     "was killed by a signal on its first run"),
    ({"resource": True}, "resource_first_run",
     "timed out or was killed by a signal"),
])
def test_a_first_run_resource_signature_every_rerun_cut(
        leerie, tmp_path, monkeypatch, first, code, words):
    st, s_, res, _ = _placed(leerie, monkeypatch, tmp_path,
                             _first_then(first, _hang),
                             confirm_budget_sec=0.3)
    assert res["unmeasured"] is True and res["failing_files"] == []
    assert res["unmeasured_files"] == {REL: code}
    outcome, body = _finish(leerie, st, [s_], [res])
    assert outcome == "done"
    line = _nhv_line(body, "held-out set 1")
    assert f"`{REL}` {words}" in line


def test_no_rerun_verdict(leerie, tmp_path, monkeypatch):
    async def none(caps_, obs):
        return None
    st, s_, res, _ = _placed(leerie, monkeypatch, tmp_path,
                             _first_then({}, none))
    assert res["unmeasured_files"] == {REL: "no_verdict"}
    line = _nhv_line(_finish(leerie, st, [s_], [res])[1], "held-out set 1")
    assert f"`{REL}` failed its first run, and no rerun produced" in line


def test_reruns_that_disagree(leerie, tmp_path, monkeypatch):
    async def passes(caps_, obs):
        return True
    st, s_, res, _ = _placed(leerie, monkeypatch, tmp_path,
                             _first_then({}, passes))
    assert res["unmeasured_files"] == {REL: "resource_affected"}
    line = _nhv_line(_finish(leerie, st, [s_], [res])[1], "held-out set 1")
    assert f"`{REL}` the reruns disagreed" in line


def test_the_runner_gave_no_verdict(leerie, tmp_path, monkeypatch):
    async def run(st_, caps_, tree, rel, log_path, label, *, cases_out=None,
                  **_kw):
        return True if rel == CONTROL else None
    st, s_, res, _ = _placed(leerie, monkeypatch, tmp_path, run)
    assert res["unmeasured"] is True
    assert res["unmeasured_files"] == {REL: "no_runner_verdict"}
    line = _nhv_line(_finish(leerie, st, [s_], [res])[1], "held-out set 1")
    assert f"`{REL}` the runner gave no verdict" in line


def test_a_path_the_tree_already_uses(leerie, tmp_path, monkeypatch):
    st = _st(leerie, tmp_path)
    s_, wt = _set(tmp_path)
    (wt / "acc").mkdir()
    (wt / CONTROL).write_text("# the tree's own file\n")

    async def run(st_, caps_, tree, rel, log_path, label, *, cases_out=None,
                  **_kw):
        return True
    monkeypatch.setattr(leerie, "_run_acceptance_file", run)
    (res,) = asyncio.run(leerie._acceptance_run_placed(
        st, _caps(leerie), wt, [s_], "t"))
    assert res["unmeasured"] is True
    assert res["unmeasured_files"] == {CONTROL: "path_in_use"}
    assert (wt / CONTROL).read_text() == "# the tree's own file\n"
    line = _nhv_line(_finish(leerie, st, [s_], [res])[1], "held-out set 1")
    assert f"`{CONTROL}` not run: the tree already has a file" in line


def test_files_that_could_not_be_placed(leerie, tmp_path, monkeypatch):
    def boom(*_a, **_k):
        raise OSError("disk full")
    monkeypatch.setattr(leerie.shutil, "copy2", boom)

    async def run(*_a, **_k):
        raise AssertionError("nothing is run when placing failed")
    st, s_, res, _ = _placed(leerie, monkeypatch, tmp_path, run)
    assert res["unmeasured"] is True
    assert res["unmeasured_reason"] == "placement_failed"
    line = _nhv_line(_finish(leerie, st, [s_], [res])[1], "held-out set 1")
    assert "its files could not be placed" in line


def test_a_measured_set_carries_no_reason_and_no_line(leerie, tmp_path,
                                                      monkeypatch):
    """The control: a set that passed, and one that failed, record no
    unmeasured cause and are never listed for a human."""
    async def passes(st_, caps_, tree, rel, log_path, label, *,
                     cases_out=None, **_kw):
        return True
    st, s_, res, _ = _placed(leerie, monkeypatch, tmp_path, passes)
    assert res["passed"] is True
    assert "unmeasured_files" not in res and "unmeasured_reason" not in res
    assert HEADING not in _finish(leerie, st, [s_], [res])[1]

    async def fails(caps_, obs):
        return False
    st, s_, res, _ = _placed(leerie, monkeypatch, tmp_path / "f",
                             _first_then({}, fails))
    assert res["failing_files"] == [REL] and res["unmeasured"] is False
    assert "unmeasured_files" not in res
    outcome, body = _finish(leerie, st, [s_], [res])
    assert HEADING not in body


# --- each whole-set cause, driven through `_evaluate_acceptance_sets` -----

def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@x")
    _git(repo, "config", "user.name", "t")
    (repo / "README").write_text("x\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    return repo


def _eval_st(leerie, tmp_path, repo):
    root = tmp_path / "state"
    (root / "runs" / "run-a" / "logs").mkdir(parents=True)
    st = leerie.State(root, "run-a", repo_root=repo)
    st.data = {"task": "t", "categories": ["bug-fixing"],
               "verbosity": "quiet"}
    return st


def test_whole_set_causes(leerie, tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    st = _eval_st(leerie, tmp_path, repo)
    s_, _ = _set(tmp_path)

    def evaluate(tree=str(repo), **kw):
        (res,) = asyncio.run(leerie._evaluate_acceptance_sets(
            st, _caps(leerie), tree, [s_], "final", **kw))
        assert res["unmeasured"] is True
        return res["unmeasured_reason"]

    assert evaluate(rev="no-such-rev") == "no_commit"

    class _Failed:
        returncode, stderr = 1, "fatal: nope"

    async def add_fails(st_, wt, sha):
        return _Failed()
    real_add = leerie._worktree_add_detached_async
    monkeypatch.setattr(leerie, "_worktree_add_detached_async", add_fails)
    assert evaluate() == "worktree_failed"
    monkeypatch.setattr(leerie, "_worktree_add_detached_async", real_add)

    async def no_deps(*_a, **_k):
        return False
    monkeypatch.setattr(leerie, "_ensure_worktree_deps", no_deps)
    assert evaluate() == "deps_failed"

    async def raises(*_a, **_k):
        raise RuntimeError("fork failed")
    monkeypatch.setattr(leerie, "_evaluate_acceptance_sets_inner", raises)
    assert evaluate() == "evaluation_failed"


@pytest.mark.parametrize("code", ["evaluation_failed", "no_commit",
                                  "worktree_failed", "deps_failed"])
def test_a_whole_set_cause_reaches_the_pr(leerie, tmp_path, code):
    st = _st(leerie, tmp_path)
    s_, _ = _set(tmp_path)
    res = {"index": 1, "passed": False, "unmeasured": True,
           "failing_files": [], "unmeasured_reason": code}
    outcome, body = _finish(leerie, st, [s_], [res])
    assert outcome == "done"
    line = _nhv_line(body, "held-out set 1")
    assert leerie.ACCEPTANCE_UNMEASURED_WHY[code] in line
    assert f"`{REL}`" in line and f"`{CONTROL}`" in line


# --- the whole-gate shapes ------------------------------------------------

def test_valid_sets_with_no_final_verdict(leerie, tmp_path):
    st = _st(leerie, tmp_path)
    s_, _ = _set(tmp_path)
    st.data["acceptance"] = {"sets": [s_], "gate": {"initial": []}}
    line = _nhv_line(leerie._format_pr_end_state_section(st.data),
                     "held-out set(s) 1")
    assert "the acceptance gate stopped or did not run" in line
    assert f"`{REL}`" in line
    # No gate record at all reads the same.
    st.data["acceptance"] = {"sets": [s_]}
    assert _nhv_line(leerie._format_pr_end_state_section(st.data),
                     "held-out set(s) 1")
    # A skipped acceptance check has nothing to verify.
    st.data["acceptance"] = {"skipped": "not a defect fix"}
    assert HEADING not in leerie._format_pr_end_state_section(st.data)


def test_a_remeasure_that_measured_nothing_is_marked_and_listed(
        leerie, tmp_path, monkeypatch):
    st = _st(leerie, tmp_path)
    s_, _ = _set(tmp_path)
    old = {"index": 1, "passed": True, "unmeasured": False,
           "failing_files": []}
    st.data["acceptance"] = {"sets": [s_], "gate": {
        "final": [old], "final_sha": "a" * 40}}
    calls = {"n": 0}

    async def ev(st_, caps, tree, sets, label, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return [{"index": 1, "passed": False, "unmeasured": True,
                     "failing_files": [], "unmeasured_reason": "deps_failed"}]
        return [dict(old)]
    monkeypatch.setattr(leerie, "_evaluate_acceptance_sets", ev)
    ok = asyncio.run(leerie._acceptance_remeasure_final(
        st, _caps(leerie), str(tmp_path), [s_], "b" * 40, "redrive-final"))
    gate = st.data["acceptance"]["gate"]
    assert ok is False and gate["final"] == [old]
    assert gate["final_sha"] == "a" * 40
    assert gate["final_unremeasured"] == {"sha": "b" * 40,
                                          "label": "redrive-final"}
    outcome, body = (leerie._decide_outcome(st, any_commits=True),
                     leerie._format_pr_end_state_section(st.data))
    assert outcome == "done"
    line = _nhv_line(body, "re-measuring them on the final commit")
    assert "`bbbbbbbbbbbb`" in line and "`aaaaaaaaaaaa`" in line
    # A later re-measure that measures clears it.
    assert asyncio.run(leerie._acceptance_remeasure_final(
        st, _caps(leerie), str(tmp_path), [s_], "c" * 40, "x")) is True
    assert "final_unremeasured" not in st.data["acceptance"]["gate"]
    assert HEADING not in leerie._format_pr_end_state_section(st.data)


def test_broken_and_variant_sets_are_not_listed(leerie, tmp_path):
    st = _st(leerie, tmp_path)
    s_, _ = _set(tmp_path)
    res = {"index": 1, "passed": False, "unmeasured": True,
           "failing_files": [], "unmeasured_files": {REL: "timed_out"}}
    for variant in ({"broken": True},
                    {"scope": {"bounded": True, "counted_cases": {REL: []}}}):
        _, body = _finish(leerie, st, [{**s_, **variant}], [res])
        assert HEADING not in body, variant
    # A bounded set with a counted case is the task's scenario: listed.
    bounded = {**s_, "scope": {"bounded": True,
                               "counted_cases": {REL: ["x"]}}}
    assert _nhv_line(_finish(leerie, st, [bounded], [res])[1],
                     "held-out set 1")


def test_a_result_recorded_before_the_codes_still_lists(leerie, tmp_path):
    st = _st(leerie, tmp_path)
    s_, _ = _set(tmp_path)
    res = {"index": 1, "passed": False, "unmeasured": True,
           "failing_files": []}
    line = _nhv_line(_finish(leerie, st, [s_], [res])[1], "held-out set 1")
    assert "no verdict recorded" in line


# --- the vocabulary is complete in both directions ------------------------

def _producer_constants(leerie) -> set[str]:
    out: set[str] = set()
    for fn in (leerie._acceptance_run_placed,
               leerie._evaluate_acceptance_sets,
               leerie._evaluate_acceptance_sets_inner):
        tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
        out |= {n.value for n in ast.walk(tree)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    return out


def test_every_code_is_produced_and_every_produced_code_is_named(leerie):
    produced = _producer_constants(leerie)
    why = leerie.ACCEPTANCE_UNMEASURED_WHY
    assert set(why) <= produced, set(why) - produced
    # Every value written to a reason slot is a named code.
    src = textwrap.dedent(inspect.getsource(leerie._acceptance_run_placed))
    tree = ast.parse(src)
    written = set()
    for n in ast.walk(tree):
        if (isinstance(n, ast.Assign) and isinstance(n.value, ast.Constant)
                and isinstance(n.value.value, str)):
            for t in n.targets:
                if ((isinstance(t, ast.Subscript)
                     and getattr(t.value, "id", "") == "why_files")
                        or getattr(t, "id", "") == "why_set"):
                    written.add(n.value.value)
    assert written and written <= set(why), written - set(why)


def test_a_real_timeout_is_recorded_as_one(leerie, tmp_path):
    """The `timed_out` code rests on `_run_acceptance_file` telling a
    timeout from a signal death: a real pytest file that hangs."""
    repo = _repo(tmp_path)
    (repo / ".leerie").mkdir()
    (repo / ".leerie" / "config.toml").write_text(
        'test = "python3 -m pytest -q -p no:cacheprovider"\n')
    (repo / "acc").mkdir()
    (repo / REL).write_text("import time\n\ndef test_a():\n"
                            "    time.sleep(60)\n")
    st = _eval_st(leerie, tmp_path, repo)
    obs: dict = {}
    v = asyncio.run(leerie._run_acceptance_file(
        st, _caps(leerie, worker_timeout_sec=3), str(repo), REL,
        st.run_dir / "logs" / "t.log", "t", cases_out=obs))
    assert v is False
    assert obs.get("resource") is True and obs.get("timed_out") is True
    assert "killed" not in obs
