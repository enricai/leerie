"""Review round 8 M1: the tree that ships is the committed one (DESIGN §8
*The final diff describes the tree that ships*).

Before: the refresh identified the shipping tree by `_worktree_tree_sha`,
which is None on any `git status --porcelain` output — untracked files
included. In a repository that does not ignore `__pycache__/`, the test
measurements themselves left `?? __pycache__/`, so a clean run listed
"failing-test-ID diff: not measured on the tree that ships — the staging
worktree has uncommitted changes" (it had been measured, on that very
tree), and a run whose repair commit fixed a confirmed-new test stayed
`unconverged`, because the refresh never measured again. Every driver runs
on a real pytest suite, with `PYTHONDONTWRITEBYTECODE` unset so the
artifacts really appear."""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys

import pytest

# Red until lane B's I2 step; see the reason.
_NEEDS_I2 = pytest.mark.xfail(
    strict=True,
    reason=("I1 (DESIGN §8 *A worker leaves its tree clean*) holds the final "
           "conformer to a clean staging, and leerie's own test run leaves "
           "this untracked output there until I2 (DESIGN §6½: leerie's "
           "commands leave the tree as they found it) — remove this mark "
           "when I2 lands; strict, so it cannot be forgotten"))

sys.path.insert(0, os.path.dirname(__file__))
from test_redrive_test_id_diff_refresh import CMD, SUITE, _git  # noqa: E402

MOD_TEST = ("import sys, os\n"
            "sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))\n"
            "import mod\n\ndef test_m():\n    assert mod.X\n")


def _repo(leerie, tmp_path, monkeypatch, *, ignore: str = "",
          branch_suite_ok: str = "True") -> dict:
    """A base where every test passes and a run branch whose `test_x` is
    `branch_suite_ok`. `.gitignore` lists only `.leerie/` plus `ignore`."""
    monkeypatch.setattr(leerie, "_host_cpu_psi", lambda path=None: 0.0)
    monkeypatch.delenv("PYTHONDONTWRITEBYTECODE", raising=False)
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / ".gitignore").write_text(".leerie/\n" + ignore)
    (repo / "tests" / "test_s.py").write_text(SUITE.format(ok="True"))
    (repo / "mod.py").write_text("X = 1\n")
    (repo / "tests" / "test_m.py").write_text(MOD_TEST)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    base = _git(repo, "rev-parse", "HEAD")
    run_id = "fix-001-abcdef"
    _git(repo, "checkout", "-qb", f"leerie/runs/{run_id}")
    (repo / "mod.py").write_text("X = 2\n")
    (repo / "tests" / "test_s.py").write_text(
        SUITE.format(ok=branch_suite_ok))
    _git(repo, "commit", "-qam", "feature")
    _git(repo, "checkout", "-q", "main")
    root = repo / ".leerie"
    run_dir = root / "runs" / run_id
    (run_dir / "logs").mkdir(parents=True)
    staging = run_dir / "worktrees" / "staging"
    _git(repo, "worktree", "add", "-q", str(staging), f"leerie/runs/{run_id}")
    st = leerie.State(root, run_id)
    st.data = {"task": "x", "working_branch": "main",
               "conformance": {"_baseline": {"sha": base}},
               "baseline_failing_ids": [], "subtask_status": {"a": "done"},
               "required_items": [{"item": "set X to 2"}]}
    st.repo_root = repo
    st.save()
    monkeypatch.setattr(leerie, "resolve_blt", lambda root: {"test": CMD})
    caps = {**leerie.DEFAULT_CAPS, "confirm_intermittent_repeats": 0}
    return {"st": st, "staging": staging, "run_dir": run_dir, "caps": caps,
            "repo": repo}


def _quiet_conformer(leerie, monkeypatch, *, clean: bool = True) -> None:
    async def noop(*a, **k):
        return {}
    monkeypatch.setattr(leerie, "claude_p", noop)
    monkeypatch.setattr(leerie, "_validate_conformance_result",
                        lambda r, wt: None)
    monkeypatch.setattr(leerie, "check_production_evidence", lambda r: [])
    if clean:
        monkeypatch.setattr(leerie, "_conformance_clean", lambda r, b: True)


def _phase5(leerie, run) -> None:
    for label in ("test-id-diff-acceptance", "test-id-diff-final"):
        asyncio.run(leerie._refresh_shipping_test_id_diff(
            run["run_dir"], run["st"], run["caps"], label=label))


def _untracked(staging) -> str:
    return subprocess.run(["git", "status", "--porcelain"], cwd=staging,
                          capture_output=True, text=True).stdout


@_NEEDS_I2
def test_a_clean_run_whose_tests_leave_artifacts_ends_done_and_empty(
        leerie, tmp_path, monkeypatch):
    """The reviewer's driver 1: nothing regressed, the conformer did
    nothing, `__pycache__/` is not ignored. The section is empty."""
    run = _repo(leerie, tmp_path, monkeypatch)
    _quiet_conformer(leerie, monkeypatch)
    asyncio.run(leerie._run_final_conformance(
        run["run_dir"], run["st"], run["caps"], {"conformer": "sonnet"},
        {"conformer": "low"}))
    assert "?? " in _untracked(run["staging"])   # the precondition
    _phase5(leerie, run)
    st = run["st"]
    committed = asyncio.run(leerie._committed_tree_sha(str(run["staging"])))
    diff = st.data["test_id_diff"]
    assert diff["tree_sha"] == committed and diff["new"] == []
    assert not st.data.get("needs_human_verification")
    assert leerie._decide_outcome(st, any_commits=True) == "done"
    assert leerie._format_pr_end_state_section(st.data) == ""
    # The clean checkout is removed afterwards.
    assert not leerie._helper_worktree_path(st, "shipping-tree").exists()


@_NEEDS_I2
def test_a_repair_commit_that_fixes_a_confirmed_new_test_ends_done(
        leerie, tmp_path, monkeypatch):
    """The reviewer's driver 2: the final conformer confirms `test_x` new,
    a repair commit fixes it. Before: the refresh never measured, and the
    run ended `unconverged` claiming `test_x` newly failing on the final
    tree. Now the committed tree is measured and the run is `done`."""
    run = _repo(leerie, tmp_path, monkeypatch, branch_suite_ok="False")
    _quiet_conformer(leerie, monkeypatch, clean=False)
    asyncio.run(leerie._run_final_conformance(
        run["run_dir"], run["st"], run["caps"], {"conformer": "sonnet"},
        {"conformer": "low"}))
    st, staging = run["st"], run["staging"]
    assert st.data["test_id_diff"]["new"]
    (staging / "tests" / "test_s.py").write_text(SUITE.format(ok="True"))
    _git(staging, "commit", "-qm", "conformer: fix", "tests/test_s.py")
    assert "?? " in _untracked(staging)
    _phase5(leerie, run)
    diff = st.data["test_id_diff"]
    assert diff["new"] == [] and diff["tree_sha"] == asyncio.run(
        leerie._committed_tree_sha(str(staging)))
    assert leerie._decide_outcome(st, any_commits=True) == "done"
    assert leerie._format_pr_end_state_section(st.data) == ""


def test_an_untracked_file_that_would_fail_the_suite_is_not_measured(
        leerie, tmp_path, monkeypatch):
    """Substance of "the committed tree": an untracked test file in staging
    that fails is not part of what ships, so it cannot make the diff say
    the run broke a test."""
    run = _repo(leerie, tmp_path, monkeypatch, ignore="__pycache__/\n")
    st, staging = run["st"], run["staging"]
    (staging / "tests" / "test_stray.py").write_text(
        "def test_stray():\n    assert False\n")
    _phase5(leerie, run)
    diff = st.data["test_id_diff"]
    assert diff["new"] == [] and diff["candidates"] == []
    assert not st.data.get("needs_human_verification")
    # Measured in staging itself, the stray file is what fails.
    post = asyncio.run(leerie._measure_axes(
        str(staging), {"tests": CMD}, st, run["caps"],
        log_path=st.run_dir / "logs" / "x.log", verbosity="quiet",
        memo=False, failing_ids_axes={"tests"}))
    assert any("test_stray" in i for i in post["tests"]["failing_ids"])


def test_tracked_edits_are_measured_past_and_listed(
        leerie, tmp_path, monkeypatch):
    """A tracked edit staging left uncommitted does not ship: the committed
    tree is still measured (it used to give up), and the edit is listed."""
    run = _repo(leerie, tmp_path, monkeypatch)
    st, staging = run["st"], run["staging"]
    (staging / "mod.py").write_text("X = 0\n")    # would fail test_m
    _phase5(leerie, run)
    diff = st.data["test_id_diff"]
    assert diff["new"] == [] and diff["tree_sha"] == asyncio.run(
        leerie._committed_tree_sha(str(staging)))
    gap = [e for e in st.data["needs_human_verification"]
           if e.get("source") == leerie.FINAL_DIFF_GAP_SOURCE]
    assert len(gap) == 1
    assert "measured on the committed tree that ships" in gap[0]["detail"]
    assert "mod.py" in gap[0]["detail"]
    assert "could not be identified" not in gap[0]["detail"]
    # Committed: the note goes.
    _git(staging, "checkout", "--", "mod.py")
    _phase5(leerie, run)
    assert not [e for e in st.data.get("needs_human_verification") or []
                if e.get("source") == leerie.FINAL_DIFF_GAP_SOURCE]


def test_the_log_says_marked_stale_only_when_it_was(
        leerie, tmp_path, monkeypatch, capsys):
    run = _repo(leerie, tmp_path, monkeypatch)
    st = run["st"]
    st.data["test_id_diff"] = {"new": [], "candidates": [], "tree_sha": "0"}
    st.save()

    async def no_tree(tree):
        return None
    monkeypatch.setattr(leerie, "_committed_tree_sha", no_tree)
    _phase5(leerie, run)
    err = capsys.readouterr()
    text = err.out + err.err
    assert "the old diff is kept" in text and "marked stale" not in text
    assert "stale_for_tree" not in st.data["test_id_diff"]


def test_a_broken_round_keeps_its_pre_ids_despite_test_artifacts(
        leerie, tmp_path, monkeypatch):
    """The round-7 H1 fallback compared `_worktree_tree_sha`, None in this
    repository, so it never fired: an untracked artifact is not a move."""
    run = _repo(leerie, tmp_path, monkeypatch, branch_suite_ok="False")

    async def boom(*a, **k):
        raise leerie.WorkerError("final conformer: transport error")
    monkeypatch.setattr(leerie, "claude_p", boom)
    asyncio.run(leerie._run_final_conformance(
        run["run_dir"], run["st"], run["caps"], {"conformer": "sonnet"},
        {"conformer": "low"}))
    diff = run["st"].data["test_id_diff"]
    assert [x.rsplit(".", 1)[-1] for x in diff["new"]] == ["test_x"]


@pytest.mark.parametrize("ignore", ["", "__pycache__/\n"])
def test_the_shipping_tree_reads_are_committed_tree_reads(
        leerie, tmp_path, monkeypatch, ignore):
    """`_committed_tree_sha` ignores the worktree; `_worktree_tree_sha`
    (the measurement identity) does not."""
    run = _repo(leerie, tmp_path, monkeypatch, ignore=ignore)
    staging = str(run["staging"])
    head_tree = _git(run["staging"], "rev-parse", "HEAD^{tree}")
    subprocess.run(["python3", "-m", "pytest", "-q", "tests"], cwd=staging,
                   capture_output=True)
    assert asyncio.run(leerie._committed_tree_sha(staging)) == head_tree
    assert asyncio.run(leerie._unmoved_tree_sha(staging)) == head_tree
    measured = asyncio.run(leerie._worktree_tree_sha(staging))
    assert measured == (head_tree if ignore else None)
    (run["staging"] / "mod.py").write_text("X = 5\n")
    assert asyncio.run(leerie._unmoved_tree_sha(staging)) is None
    assert asyncio.run(leerie._committed_tree_sha(staging)) == head_tree
