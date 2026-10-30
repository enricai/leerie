"""Review round 15: the tree that ships is the run branch's.

H2 — a worker leaves its tree clean AND on the branch leerie gave it, at a
commit that descends from where it started (DESIGN §8 *A worker leaves its
tree clean*). A worker that committed on a detached HEAD, or on a branch of
its own, left `git status` empty and passed I1; leerie then measured,
stamped and judged a commit the run branch never received.

H1 — the tree that ships is `refs/heads/leerie/runs/<id>^{tree}`, never
staging's HEAD (DESIGN §6 *A delivery verdict counts only on the tree it
was measured on*): staging is removed by finalize's `cleanup.sh` before the
outcome is decided.

The round's drivers (review-r15-proofs/exec), rewritten to assert the fix.
"""
from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

from tests.test_run_final_conformance import _clean_result, env  # noqa: F401


def _git(cwd, *a, check=True):
    return subprocess.run(["git", *a], cwd=cwd, check=check,
                          capture_output=True, text=True).stdout.strip()


def _repo(tmp_path: Path) -> Path:
    r = tmp_path / "r"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    _git(r, "config", "user.email", "t@x")
    _git(r, "config", "user.name", "t")
    (r / "a.py").write_text("x = 1\n")
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "base")
    return r


# --- the checker -------------------------------------------------------------

def test_a_detached_head_is_not_clean(leerie, tmp_path):
    r = _repo(tmp_path)
    base = _git(r, "rev-parse", "HEAD")
    _git(r, "checkout", "-q", "--detach")
    (r / "a.py").write_text("x = 2\n")
    _git(r, "commit", "-qam", "work on a detached HEAD")
    # The porcelain alone is empty: exactly what passed I1 before.
    assert asyncio.run(leerie._tree_dirt(r)) == []
    assert asyncio.run(leerie._tree_dirt(r, branch="main", base=base)) == [
        "HEAD is not on main (detached)"]


def test_another_branch_is_not_clean(leerie, tmp_path):
    r = _repo(tmp_path)
    _git(r, "checkout", "-q", "-b", "mine")
    assert asyncio.run(leerie._tree_dirt(r, branch="main")) == [
        "HEAD is not on main (on mine)"]


def test_a_branch_moved_off_its_start_is_not_clean(leerie, tmp_path):
    r = _repo(tmp_path)
    (r / "a.py").write_text("x = 2\n")
    _git(r, "commit", "-qam", "second")
    start = _git(r, "rev-parse", "HEAD")
    _git(r, "reset", "-q", "--hard", "HEAD~1")
    assert asyncio.run(leerie._tree_dirt(r, branch="main", base=start)) == [
        f"HEAD does not descend from {start[:12]}, which this worker's work "
        "must sit on"]


def test_on_the_branch_and_ahead_of_its_start_is_clean(leerie, tmp_path):
    r = _repo(tmp_path)
    start = _git(r, "rev-parse", "HEAD")
    (r / "a.py").write_text("x = 2\n")
    _git(r, "commit", "-qam", "work")
    assert asyncio.run(leerie._tree_dirt(r, branch="main", base=start)) == []


def test_the_reask_names_the_branch_and_a_checkout_clears_it(leerie,
                                                             tmp_path):
    r = _repo(tmp_path)
    start = _git(r, "rev-parse", "HEAD")
    _git(r, "checkout", "-q", "--detach")
    (r / "a.py").write_text("x = 2\n")
    _git(r, "commit", "-qam", "work")
    work = _git(r, "rev-parse", "HEAD")
    notes: list[str] = []

    async def reask(note):
        notes.append(note)
        _git(r, "checkout", "-q", "main")
        _git(r, "merge", "-q", "--ff-only", work)
    got = asyncio.run(leerie._clean_tree_gate(
        r, caps=dict(leerie.DEFAULT_CAPS), label="t", reask=reask,
        branch="main", base=start))
    assert got == [] and len(notes) == 1
    assert "    HEAD is not on main (detached)" in notes[0]
    assert "git checkout main" in notes[0]
    assert _git(r, "rev-parse", "main") == work


def test_a_file_only_note_carries_no_branch_instruction(leerie, tmp_path):
    r = _repo(tmp_path)
    (r / "scratch.py").write_text("y\n")
    notes: list[str] = []

    async def reask(note):
        notes.append(note)
        (r / "scratch.py").unlink()
    asyncio.run(leerie._clean_tree_gate(
        r, caps=dict(leerie.DEFAULT_CAPS), label="t", reask=reask,
        branch="main", base=None))
    assert "git checkout main" not in notes[0]


# --- H2 at a worker: the final conformer's detached commit --------------------

def test_a_final_conformer_that_commits_detached_is_reasked_then_rolled_back(
        env, monkeypatch):  # noqa: F811
    """The round's driver: the final conformer detached, fixed and
    committed. It passed I1, its tree was stamped, and the run branch never
    got the fix. Now it is re-asked twice, and, never back on the branch,
    the round is rolled back onto the run branch and recorded."""
    leerie = env["leerie"]
    staging, branch, st = env["staging"], env["run_branch"], env["st"]
    tip = _git(staging, "rev-parse", "HEAD")
    calls = []

    async def fake(**kw):
        calls.append(kw)
        if kw["schema_key"] == "conformer" and not kw.get("resume_session"):
            cwd = Path(kw["cwd"])
            _git(cwd, "checkout", "-q", "--detach")
            (cwd / "src.py").write_text("def f():\n    return 1\n")
            _git(cwd, "commit", "-qam", "conformer: fix f")
        return _clean_result()
    monkeypatch.setattr(leerie, "claude_p", fake)
    monkeypatch.setattr(leerie, "_log_session_id", lambda p: "sess")
    asyncio.run(leerie._run_final_conformance(
        env["run_dir"], st, env["caps"], env["models"], env["efforts"]))
    reasks = [c for c in calls if c.get("resume_session")]
    assert len(reasks) == 2
    assert f"HEAD is not on {branch} (detached)" in reasks[0]["user_prompt"]
    assert f"git checkout {branch}" in reasks[0]["user_prompt"]
    # Rolled back ONTO the branch: HEAD is the branch again, at its start.
    assert _git(staging, "symbolic-ref", "HEAD") == f"refs/heads/{branch}"
    assert _git(staging, "rev-parse", "HEAD") == tip
    assert st.data["unclean_trees"]["final conformer:round 0"]["lines"] == [
        f"HEAD is not on {branch} (detached)"]


def test_a_final_conformer_that_puts_its_commit_on_the_branch_ships_it(
        env, monkeypatch):  # noqa: F811
    leerie = env["leerie"]
    staging, branch, st = env["staging"], env["run_branch"], env["st"]

    async def fake(**kw):
        cwd = Path(kw["cwd"])
        if kw["schema_key"] == "conformer" and not kw.get("resume_session"):
            _git(cwd, "checkout", "-q", "--detach")
            (cwd / "src.py").write_text("def f():\n    return 1\n")
            _git(cwd, "commit", "-qam", "conformer: fix f")
        elif kw.get("resume_session"):
            work = _git(cwd, "rev-parse", "HEAD")
            _git(cwd, "checkout", "-q", branch)
            _git(cwd, "merge", "-q", "--ff-only", work)
        return _clean_result()
    monkeypatch.setattr(leerie, "claude_p", fake)
    monkeypatch.setattr(leerie, "_log_session_id", lambda p: "sess")
    asyncio.run(leerie._run_final_conformance(
        env["run_dir"], st, env["caps"], env["models"], env["efforts"]))
    assert "return 1" in _git(staging, "show", f"{branch}:src.py")
    assert "unclean_trees" not in st.data


# --- H1: the outcome after finalize's cleanup ---------------------------------

def _finalize(leerie, env, monkeypatch):
    st = env["st"]

    async def _noop(*a, **k):
        return None
    monkeypatch.setattr(leerie, "capture_repo_deps", _noop)
    monkeypatch.setenv("LEERIE_STATE_DIR", str(env["run_dir"].parent.parent))
    monkeypatch.chdir(env["repo"])
    (env["run_dir"] / "working-branch").write_text("main\n")
    try:
        asyncio.run(leerie.phase_finalize(
            env["run_dir"].parent.parent, st, no_push=True, no_verify=False,
            caps=dict(leerie.DEFAULT_CAPS), models={}, efforts={}))
    finally:
        st.release_lock()


def test_a_correct_run_ends_done_after_cleanup_removed_staging(
        env, monkeypatch):  # noqa: F811
    """The round's driver, on the real `phase_finalize`, `finalize.sh` and
    `cleanup.sh`: final detection read every item met on the run branch's
    tree; cleanup then removes staging; the outcome is `done`."""
    leerie = env["leerie"]
    st, staging = env["st"], env["staging"]
    tree = _git(env["st"].run_repo, "rev-parse",
                f"{env['run_branch']}^{{tree}}")
    st.data.update({
        "waves": [["feat-001"]], "completed_waves": 1,
        "subtask_status": {"feat-001": "complete"},
        "delivery_gate": {"unmet_final": [], "final_tree":
                          leerie._shipping_tree(st)}})
    st.save()
    assert st.data["delivery_gate"]["final_tree"] == tree   # the ref's tree
    _finalize(leerie, env, monkeypatch)
    assert not staging.exists()                 # cleanup.sh removed it
    assert st.data["outcome"] == "done", st.data["outcome_reasons"]
    run_json = json.loads((env["run_dir"] / "run.json").read_text())
    assert run_json["outcome"] == "done"


def test_a_verdict_of_another_tree_is_still_unconverged_after_cleanup(
        env, monkeypatch):  # noqa: F811
    leerie = env["leerie"]
    st = env["st"]
    st.data.update({
        "waves": [["feat-001"]], "completed_waves": 1,
        "subtask_status": {"feat-001": "complete"},
        "delivery_gate": {"unmet_final": [], "final_tree": "0" * 40}})
    st.save()
    _finalize(leerie, env, monkeypatch)
    assert st.data["outcome"] == "unconverged"
    (reason,) = st.data["outcome_reasons"]
    tree = _git(env["st"].run_repo, "rev-parse",
                f"{env['run_branch']}^{{tree}}")
    assert "not read on the tree that ships" in reason
    assert tree[:12] in reason


def test_a_no_work_run_with_a_verdict_is_no_work_after_cleanup(
        leerie, tmp_path, monkeypatch):
    """The post-execution no-work path: a run branch with no commits past
    the base, a pre-pass verdict of that tree reading nothing unmet; the
    run branch outlives the subtask cleanup, so the verdict is read on the
    tree that ships and the outcome is `no_work`."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "f.txt").write_text("base\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    _git(repo, "branch", "leerie/runs/nowork-001")
    st = leerie.State(tmp_path / ".leerie", "nowork-001", repo_root=repo)
    tree = _git(repo, "rev-parse", "leerie/runs/nowork-001^{tree}")
    st.data = {"task": "x", "subtask_status": {"a": "complete"},
               "delivery_gate": {"unmet_before": [],
                                 "judged_before": {"tree": tree}}}
    st.save()
    # The run branch lives in the run's own repository (DESIGN §6 *The
    # run's own repository*), where `_shipping_tree` reads it.
    leerie._ensure_run_repo(st)
    _git(repo, "update-ref", "-d", "refs/heads/leerie/runs/nowork-001")
    _git(st.run_repo, "branch", "leerie/runs/nowork-001",
         "refs/leerie/user-head")
    assert not (st.run_dir / "worktrees" / "staging").exists()
    leerie._finish_no_work_run(st, {"<finalize>": "empty"},
                               reset_plan_state=False)
    assert st.data["outcome"] == "no_work", st.data["outcome_reasons"]
    st.release_lock()
