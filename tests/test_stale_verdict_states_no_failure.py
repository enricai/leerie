"""DESIGN §6 *A verdict of another tree states no failure of the tree that
ships*: a failing verdict whose recorded commit is not the tree the run
ships still vetoes `done`, in its input's place, but says which tree the
failure is of, never "on the final tree". When the tree that ships cannot
be read, each input keeps its own text. Real git: a run repository with
an earlier commit A and the shipped commit B; nothing is stubbed but
`resolve_blt`."""
from __future__ import annotations

import subprocess

import pytest

from tests.conftest import relocate_into_run_repo, run_git_cwd_first_stdout

RUN_ID = "fix-001-abcdef"


@pytest.fixture
def run(leerie, tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    run_git_cwd_first_stdout(repo, "init", "-q", "-b", "main")
    run_git_cwd_first_stdout(repo, "config", "user.email", "t@t")
    run_git_cwd_first_stdout(repo, "config", "user.name", "t")
    (repo / "mod.py").write_text("X = 0\n")
    run_git_cwd_first_stdout(repo, "add", "-A")
    run_git_cwd_first_stdout(repo, "commit", "-qm", "base")
    run_git_cwd_first_stdout(repo, "checkout", "-qb", f"leerie/runs/{RUN_ID}")
    (repo / "mod.py").write_text("X = 1\n")
    run_git_cwd_first_stdout(repo, "commit", "-qam", "earlier")
    a = run_git_cwd_first_stdout(repo, "rev-parse", "HEAD")
    (repo / "mod.py").write_text("X = 2\n")
    run_git_cwd_first_stdout(repo, "commit", "-qam", "ships")
    b = run_git_cwd_first_stdout(repo, "rev-parse", "HEAD")
    run_git_cwd_first_stdout(repo, "checkout", "-q", "main")
    root = tmp_path / "state"
    (root / "runs" / RUN_ID).mkdir(parents=True)
    st = leerie.State(root, RUN_ID)
    st.data = {"task": "x", "working_branch": "main",
               "subtask_status": {"a": "done"}}
    st.repo_root = repo
    st.save()
    relocate_into_run_repo(leerie, st)
    # No test command: the "not measured" reason of the tests stays out,
    # so each case reads only the input it sets.
    monkeypatch.setattr(leerie, "resolve_blt", lambda root: {})
    run_repo = st.run_dir / leerie.RUN_REPO_NAME

    def tree(c: str) -> str:
        return subprocess.run(["git", "--git-dir", str(run_repo), "rev-parse",
                               f"{c}^{{tree}}"], check=True,
                              capture_output=True, text=True).stdout.strip()

    return {"st": st, "a": a, "b": b, "ta": tree(a), "tb": tree(b)}


def _decide(leerie, st) -> list[str]:
    assert leerie._decide_outcome(st, any_commits=True) == "unconverged"
    return st.data["outcome_reasons"]


def _stale(reasons: list[str], tree: str, ships: str, of: str) -> str:
    # The input's own reason; others (round 19's "not measured on the tree
    # that ships") may stand beside it and are not this rule's.
    [r] = [x for x in reasons if of in x]
    assert f"on tree {tree[:12]}" in r
    assert f"not the tree that ships ({ships[:12]})" in r
    assert "on the final tree" not in r
    return r


def test_new_failing_tests_of_an_earlier_tree(leerie, run):
    st = run["st"]
    st.data["test_id_diff"] = {"new": ["t::x"], "tree_sha": run["ta"]}
    r = _stale(_decide(leerie, st), run["ta"], run["tb"], "newly failing")
    assert r.startswith("1 test(s) newly failing, confirmed against the base")


def test_new_failing_tests_of_the_tree_that_ships(leerie, run):
    st = run["st"]
    st.data["test_id_diff"] = {"new": ["t::x"], "tree_sha": run["tb"]}
    assert "1 test(s) newly failing on the final tree, confirmed against " \
        "the base" in _decide(leerie, st)


def test_merge_checks_red_on_an_earlier_commit(leerie, run):
    st = run["st"]
    st.data["ci_parity"] = {"final": {"sha": run["a"],
                                      "unmet": [{"id": "lint"}]}}
    r = _stale(_decide(leerie, st), run["ta"], run["tb"], "merge check")
    assert r.startswith("1 of the repository's own merge check(s) red: lint")


def test_merge_checks_red_on_the_commit_that_ships(leerie, run):
    st = run["st"]
    st.data["ci_parity"] = {"final": {"sha": run["b"],
                                      "unmet": [{"id": "lint"}]}}
    assert "1 of the repository's own merge check(s) red on the final " \
        "tree: lint" in _decide(leerie, st)


def test_held_out_sets_failing_on_an_earlier_commit(leerie, run):
    st = run["st"]
    st.data["acceptance"] = {
        "sets": [{"index": 1, "defect_files": ["t.py"]}],
        "gate": {"final_sha": run["a"],
                 "final": [{"index": 1, "passed": False,
                            "failing_files": ["t.py"]}]}}
    r = _stale(_decide(leerie, st), run["ta"], run["tb"], "held-out")
    assert r.startswith("1 held-out acceptance set(s) failing")


def test_base_form_and_residual_defects_of_an_earlier_tree(leerie, run):
    st = run["st"]
    st.data["existing_behaviour"] = {
        "tree_sha": run["ta"], "files": ["t.py"],
        "changed": [{"id": "t.py::c", "file": "t.py", "message": "m"}]}
    st.data["conformance"] = {"_final": {
        "tree_sha": run["ta"],
        "residual_defects": [{"kind": "k", "where": "mod.py:1",
                              "concrete_case": "c"}]}}
    reasons = _decide(leerie, st)
    _stale(reasons, run["ta"], run["tb"], "existing_behaviour_changed")
    _stale(reasons, run["ta"], run["tb"], "solution defect(s)")


def test_an_unreadable_shipping_tree_keeps_each_text(leerie, run,
                                                      monkeypatch):
    st = run["st"]
    monkeypatch.setattr(leerie, "_shipping_tree", lambda st: "")
    st.data["ci_parity"] = {"final": {"sha": run["a"],
                                      "unmet": [{"id": "lint"}]}}
    assert "1 of the repository's own merge check(s) red on the final " \
        "tree: lint" in _decide(leerie, st)


def test_the_detection_row_still_counts_new_ids_of_an_earlier_tree(
        leerie, run):
    # DESIGN §8: a `new` that a stale diff carries still makes the item
    # unmet; only the outcome's reason says which tree it is of.
    st = run["st"]
    st.data["test_id_diff"] = {"new": ["t::x"], "tree_sha": run["ta"],
                               "candidates": ["t::x"]}
    assert leerie._test_outcome_verdict(st, run["tb"])["verdict"] == "unmet"


def test_a_record_with_no_stamp_keeps_its_text(leerie, run):
    # No stamp shows no other tree: "not the tree that ships" would be a
    # claim nothing measured.
    st = run["st"]
    st.data["acceptance"] = {
        "sets": [{"index": 1, "defect_files": ["t.py"]}],
        "gate": {"final": [{"index": 1, "passed": False,
                            "failing_files": ["t.py"]}]}}
    assert "1 held-out acceptance set(s) still fail" in _decide(leerie, st)
