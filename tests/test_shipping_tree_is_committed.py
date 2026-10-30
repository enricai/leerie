"""Review round 8 M1: the tree that ships is the committed one (DESIGN §8
*The final diff describes the tree that ships*).

Before: the refresh identified the shipping tree by `_worktree_tree_sha`,
which is None on any `git status --porcelain` output — untracked files
included. In a repository that does not ignore `__pycache__/`, the test
measurements themselves left `?? __pycache__/`, so a clean run listed
"failing-test-ID diff: not measured on the tree that ships — the staging
worktree has uncommitted changes" (it had been measured, on that very
tree), and a run whose repair commit fixed a confirmed-new test stayed
`unconverged`, because the refresh never measured again. Rounds 9 to 14
answered with a clean checkout of HEAD and a list of edits "that do not
ship"; with I1 and I2 (DESIGN §8 *The final diff describes the tree that
ships*) staging is its HEAD plus ignored files at every reader, asserted,
and any dirt there stops the run. Every driver runs on a real pytest
suite, with `PYTHONDONTWRITEBYTECODE` unset so the artifacts really
appear."""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))
from test_redrive_test_id_diff_refresh import CMD, SUITE, _git  # noqa: E402

from tests.conftest import relocate_into_run_repo  # noqa: E402

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
    # The run's own repository (DESIGN §6 *The run's own repository*).
    relocate_into_run_repo(leerie, st)
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
    return subprocess.run(["git", "status", "--porcelain",
                           "--untracked-files=all"], cwd=staging,
                          capture_output=True, text=True).stdout


def test_a_clean_run_whose_tests_leave_artifacts_ends_done_and_empty(
        leerie, tmp_path, monkeypatch, capsys):
    """The reviewer's driver 1: nothing regressed, the conformer did
    nothing, `__pycache__/` is not ignored. The section is empty."""
    run = _repo(leerie, tmp_path, monkeypatch)
    _quiet_conformer(leerie, monkeypatch)
    asyncio.run(leerie._run_final_conformance(
        run["run_dir"], run["st"], run["caps"], {"conformer": "sonnet"},
        {"conformer": "low"}))
    # The precondition: the test runs made `__pycache__/`, and I2 removed it.
    assert "untracked file(s) its own tests created" in capsys.readouterr().out
    assert _untracked(run["staging"]) == ""
    _phase5(leerie, run)
    st = run["st"]
    committed = asyncio.run(leerie._committed_tree_sha(str(run["staging"])))
    diff = st.data["test_id_diff"]
    assert diff["tree_sha"] == committed and diff["new"] == []
    assert not st.data.get("needs_human_verification")
    assert leerie._decide_outcome(st, any_commits=True) == "done"
    assert leerie._format_pr_end_state_section(st.data) == ""


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
    assert _untracked(staging) == ""
    _phase5(leerie, run)
    diff = st.data["test_id_diff"]
    assert diff["new"] == [] and diff["tree_sha"] == asyncio.run(
        leerie._committed_tree_sha(str(staging)))
    assert leerie._decide_outcome(st, any_commits=True) == "done"
    assert leerie._format_pr_end_state_section(st.data) == ""


@pytest.mark.parametrize("dirt", ["untracked", "tracked"])
def test_dirt_in_staging_at_the_refresh_stops_the_run(
        leerie, tmp_path, monkeypatch, capsys, dirt):
    """I1 + I2 leave staging at its HEAD plus ignored files at every reader
    (DESIGN §8 *The final diff describes the tree that ships*); anything
    else is leerie's own invariant broken. Before, an untracked failing test
    sent the refresh to a clean checkout and a tracked edit was measured
    past and listed "for the reviewer"; now the run stops naming the line,
    and nothing is measured or recorded."""
    run = _repo(leerie, tmp_path, monkeypatch, ignore="__pycache__/\n")
    st, staging = run["st"], run["staging"]
    if dirt == "untracked":
        (staging / "tests" / "test_stray.py").write_text(
            "def test_stray():\n    assert False\n")
        line = "?? tests/test_stray.py"
    else:
        (staging / "mod.py").write_text("X = 0\n")
        line = " M mod.py"
    with pytest.raises(SystemExit):
        _phase5(leerie, run)
    err = capsys.readouterr().err
    assert "staging is not clean at phase 5's shipping-tree refresh" in err
    assert line in err
    assert "test_id_diff" not in st.data
    assert not st.data.get("needs_human_verification")


def test_ignored_files_in_staging_are_not_dirt(leerie, tmp_path,
                                               monkeypatch):
    """The converse: an ignored file (dependencies, a cache) is in every
    tree leerie measures and never stops the run."""
    run = _repo(leerie, tmp_path, monkeypatch, ignore="__pycache__/\nbuild/\n")
    staging = run["staging"]
    (staging / "build").mkdir()
    (staging / "build" / "x.o").write_text("o")
    head_tree = _git(staging, "rev-parse", "HEAD^{tree}")
    assert asyncio.run(leerie._assert_staging_clean(staging, "x")) == \
        head_tree
    _phase5(leerie, run)
    assert run["st"].data["test_id_diff"]["tree_sha"] == head_tree


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
    repository, so it never fired. A test run's artifacts are gone now (I2),
    and the crashed round passes its clean-tree gate before it ends."""
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
        leerie, tmp_path, monkeypatch, capsys, ignore):
    """`_committed_tree_sha` ignores the worktree; `_worktree_tree_sha`
    (the measurement memo key) does not; `_assert_staging_clean` returns
    the committed tree when only ignored files differ and stops the run on
    anything else."""
    run = _repo(leerie, tmp_path, monkeypatch, ignore=ignore)
    staging = str(run["staging"])
    head_tree = _git(run["staging"], "rev-parse", "HEAD^{tree}")
    subprocess.run(["python3", "-m", "pytest", "-q", "tests"], cwd=staging,
                   capture_output=True)
    assert asyncio.run(leerie._committed_tree_sha(staging)) == head_tree
    measured = asyncio.run(leerie._worktree_tree_sha(staging))
    assert measured == (head_tree if ignore else None)
    if ignore:
        assert asyncio.run(leerie._assert_staging_clean(staging, "x")) == \
            head_tree
    else:
        with pytest.raises(SystemExit):
            asyncio.run(leerie._assert_staging_clean(staging, "x"))
        assert "?? tests/__pycache__/" in capsys.readouterr().err
    (run["staging"] / "mod.py").write_text("X = 5\n")
    assert asyncio.run(leerie._committed_tree_sha(staging)) == head_tree
    with pytest.raises(SystemExit):
        asyncio.run(leerie._assert_staging_clean(staging, "x"))


def test_a_crashed_final_conformer_is_held_to_a_clean_tree(
        leerie, tmp_path, monkeypatch):
    """I1 after a raised worker too (DESIGN §8 *A worker leaves its tree
    clean*): a final conformer that left a file and then crashed used to
    break before its gate, leaving the file in staging for every later
    reader — now an `_assert_staging_clean` stop. It is re-asked (no
    session recorded: the attempt is spent), its round is rolled back and
    recorded, and the run goes on to a clean refresh."""
    run = _repo(leerie, tmp_path, monkeypatch, ignore="__pycache__/\n")
    staging, st = run["staging"], run["st"]

    async def crash(*a, **k):
        (staging / "half.py").write_text("X = 1\n")
        raise leerie.WorkerError("final conformer: transport error")
    _quiet_conformer(leerie, monkeypatch)
    monkeypatch.setattr(leerie, "claude_p", crash)
    asyncio.run(leerie._run_final_conformance(
        run["run_dir"], st, run["caps"], {"conformer": "sonnet"},
        {"conformer": "low"}))
    assert not (staging / "half.py").exists()
    assert st.data["unclean_trees"]["final conformer:round 0"][
        "lines"] == ["?? half.py"]
    _phase5(leerie, run)                       # no stop: staging is clean
    assert st.data["test_id_diff"]["tree_sha"] == _git(
        staging, "rev-parse", "HEAD^{tree}")
    assert leerie._decide_outcome(st, any_commits=True) == "unconverged"
