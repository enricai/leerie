"""A verdict's stamp is the commit it read (DESIGN §6 *Every statement about
the result is keyed to the commit it was made on*).

Every delivery pass used to stamp its verdict with two readings from two
places: the commit from staging's HEAD, the tree from the run branch's ref
(`_shipping_tree`), re-read when the verdict was written. They name the same
tree only because every move of staging is taken into the run repository
before anything reads it. `_stamp(st, sha)` takes both from the commit the
pass read. These drive the real passes over a real run repository whose run
branch is moved away from the commit read, the one shape that tells the two
readings apart; only the judge (`_delivery_judge_unmet`, as
tests/test_round14_delivery_verdicts.py stubs it) and `claude_p` are stubbed.
The integration judge's and settle-by-execution's records carry the commit
they read too.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from tests.conftest import run_git_cwd_first_stdout as _git
from tests.conftest import run_git_out as _git_out
from tests.test_delivery_detection import (EFFORTS, MODELS, _detection_env,
                                           _repo, _st, _staging)
from tests.test_integration_repair_loop import DEFECT as _IRL_DEFECT
from tests.test_integration_repair_loop import SID as _IRL_SID
from tests.test_integration_repair_loop import _run as _irl_run
from tests.test_integration_repair_loop import _setup as _irl_setup
from tests.test_integration_repair_loop import _Workers as _IrlWorkers
from tests.test_oom_naming import env  # noqa: F401  (pytest fixture)

ITEMS = [{"item": "add(2, 3) returns 5", "verifiability": "tree"}]


def _move_run_branch(st) -> str:
    """Point the run branch in the run repository at a new commit of the
    empty tree, without touching staging's checkout; returns that tree."""
    run_repo = st.run_dir / "repo"
    branch = f"refs/heads/leerie/runs/{st.run_id}"
    tip = _git_out(run_repo, "rev-parse", branch)
    empty = _git_out(run_repo, "hash-object", "-w", "-t", "tree", "/dev/null")
    new = _git_out(run_repo, "commit-tree", empty, "-p", tip, "-m", "moved",
                   config=("user.name=t", "user.email=t@t"))
    _git_out(run_repo, "update-ref", branch, new)
    assert empty != _git_out(run_repo, "rev-parse", f"{tip}^{{tree}}")
    return empty


def _tree_of(st, sha: str) -> str:
    return _git(st.run_dir / "repo", "rev-parse", f"{sha}^{{tree}}")


def test_stamp_reads_the_commit_not_the_ref(leerie, tmp_path):
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head)
    _staging(st, repo, head)
    moved = _move_run_branch(st)
    got = leerie._stamp(st, head)
    assert got == {"sha": head, "tree": _tree_of(st, head)}
    assert leerie._shipping_tree(st) == moved != got["tree"]
    assert leerie._stamp(st, "") == {"sha": "", "tree": ""}


def test_detection_stamps_the_commit_it_judged(leerie, tmp_path,
                                               monkeypatch):
    """The run branch moves while the judge reads: `final_tree` is still the
    tree of `final_sha`, the commit detection judged."""
    repo, head, st, caps = _detection_env(leerie, tmp_path, monkeypatch,
                                          judged=False, k=1)
    moved: list[str] = []

    async def judge(*a, **k):
        moved.append(_move_run_branch(st))
        return [], 1, None
    monkeypatch.setattr(leerie, "_delivery_judge_unmet", judge)

    async def fake_claude_p(**kw):
        return {"status": "no_probe"}
    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    asyncio.run(leerie._run_delivery_detection(st.run_dir, st, caps, MODELS,
                                               EFFORTS))
    gate = st.data["delivery_gate"]
    assert gate["final_sha"] == head
    assert gate["final_tree"] == _tree_of(st, head)
    assert moved and gate["final_tree"] != moved[0]


def test_prejudge_and_recheck_stamp_the_commit_they_judged(leerie, tmp_path,
                                                          monkeypatch):
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head, required_items=ITEMS)
    staging = _staging(st, repo, head)
    # Staging stays on the commit it read while the run branch moves.
    _git(staging, "checkout", "-q", "--detach")
    moved = _move_run_branch(st)

    async def judge(*a, **k):
        return ([{"item_index": 0, "item": ITEMS[0]["item"],
                  "evidence": "e"}], 1, None)
    monkeypatch.setattr(leerie, "_delivery_judge_unmet", judge)
    caps = dict(leerie.DEFAULT_CAPS)
    asyncio.run(leerie._run_delivery_prejudge(st.run_dir, st, caps, MODELS,
                                              EFFORTS))
    asyncio.run(leerie._run_delivery_recheck(st.run_dir, st, caps, MODELS,
                                             EFFORTS))
    gate = st.data["delivery_gate"]
    for key in ("judged_before", "judged_after"):
        assert gate[key]["sha"] == head, key
        assert gate[key]["tree"] == _tree_of(st, head) != moved, key


def test_settle_by_execution_records_the_commit_it_ran_on(env, monkeypatch):  # noqa: F811
    leerie = env["leerie"]
    monkeypatch.setattr(leerie, "_cgroup_create", lambda *a, **k: None)
    monkeypatch.setattr(leerie, "resolve_blt", lambda root: {})
    worktree = Path(env["worktree"])
    tip = _git(worktree, "rev-parse", "HEAD")
    res = _run_settle(leerie, env, [sys.executable, "-c", "pass"])
    assert res["verdict"] == "pass"
    assert res["sha"] == tip
    assert env["st"].data["settled_by_execution"][env["sid"]]["sha"] == tip


def _run_settle(leerie, env, argv):  # noqa: F811
    caps = dict(env["caps"])
    caps.update({"confirm_pairs": 1, "confirm_pairs_escalated": 1,
                 "confirm_budget_sec": 60, "worker_timeout_sec": 60})
    return asyncio.run(leerie._settle_by_execution(
        {"id": env["sid"], "verification_only": True,
         "verify": [{"argv": argv}]},
        str(env["worktree"]), env["st"], caps, sid=env["sid"]))


def test_a_repaired_integration_verdict_names_the_commit_it_judged(
        leerie, tmp_path, monkeypatch):
    """After a repair round the accepting verdict read a synthetic merge of
    the repaired head; `merge_commit_sha` stays the original merge."""
    st, staging, _p1, merge = _irl_setup(leerie, tmp_path)
    w = _IrlWorkers(staging, [[_IRL_DEFECT], []])
    assert _irl_run(leerie, st, staging, monkeypatch, w) is True
    rejudge = w.calls[2]["user_prompt"]
    syn = rejudge.split("Commit: ", 1)[1].split("\n", 1)[0].strip()
    rec = st.data["integration_gate"][_IRL_SID]
    assert rec["merge_commit_sha"] == merge
    assert rec["judged_sha"] == syn != merge
