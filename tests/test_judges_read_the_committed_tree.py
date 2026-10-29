"""Review round 12 claims M1: two paths could make a verdict met with no
read of a commit (DESIGN §8 *Every post-execution verdict is of the
committed tree*). The delivery judge's DEFECT CONTRACT verdict carries no
citation and resolved `met` on a single sample, judged with staging's
working tree as cwd; `_ratchet_judge`'s re-verification of kept items ran
in staging with no citation check. Both now read a clean checkout of
staging's HEAD whenever staging holds anything uncommitted: an untracked
file there that HEAD lacks is invisible to them."""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))
from stub_helpers import (DELIVERY_CITED_FILE, _delivery_tree,  # noqa: E402
                          _judge_verdict)


def _staging(leerie, tmp_path):
    st = leerie.State(tmp_path / "state", "r")
    st.data = {"task": "t", "worker_count": 0}
    staging = _delivery_tree(st.run_dir / "worktrees" / "staging")
    # What does not ship: an untracked helper and an uncommitted edit.
    (staging / "helper.py").write_text("CONTRACT_HOLDS = True\n")
    (staging / DELIVERY_CITED_FILE).write_text("# edited, uncommitted\n")
    return st, staging


def _seen(kw) -> dict:
    cwd = kw["cwd"]
    head = subprocess.run(["git", "-C", cwd, "rev-parse", "HEAD"],
                          capture_output=True, text=True).stdout.strip()
    return {"cwd": cwd, "head": head,
            "helper": os.path.exists(os.path.join(cwd, "helper.py")),
            "cited": open(os.path.join(cwd, DELIVERY_CITED_FILE)).read()}


def _head(tree) -> str:
    return subprocess.run(["git", "-C", str(tree), "rev-parse", "HEAD"],
                          capture_output=True, text=True).stdout.strip()


def test_the_delivery_judge_and_its_contract_read_the_commit(
        leerie, tmp_path, monkeypatch):
    st, staging = _staging(leerie, tmp_path)
    seen = []

    async def judge(**kw):
        seen.append(_seen(kw))
        return {"verdicts": [_judge_verdict(0, True)],
                "contract": {"verdict": "met", "evidence": "holds"}}
    monkeypatch.setattr(leerie, "claude_p", judge)
    unmet, n, contract = asyncio.run(leerie._delivery_judge_unmet(
        "t", [{"item": "the feature exists"}], staging, st,
        {**leerie.DEFAULT_CAPS}, {}, {}, "final",
        contract_ctx={"defect_shape": "x", "sites": []}))
    assert unmet == [] and contract["verdict"] == "met"
    (s,) = seen
    assert s["cwd"] != str(staging) and s["head"] == _head(staging)
    assert not s["helper"]
    assert s["cited"].startswith("def delivered_feature")
    # Removed afterwards.
    assert not os.path.exists(s["cwd"])


def test_a_clean_staging_is_judged_in_place(leerie, tmp_path, monkeypatch):
    st = leerie.State(tmp_path / "state", "r")
    st.data = {"task": "t", "worker_count": 0}
    staging = _delivery_tree(st.run_dir / "worktrees" / "staging")
    seen = []

    async def judge(**kw):
        seen.append(kw["cwd"])
        return {"verdicts": [_judge_verdict(0, True)]}
    monkeypatch.setattr(leerie, "claude_p", judge)
    asyncio.run(leerie._delivery_judge_unmet(
        "t", [{"item": "the feature exists"}], staging, st,
        {**leerie.DEFAULT_CAPS}, {}, {}, "final"))
    assert seen == [str(staging)]


def test_a_judge_citing_the_checkouts_absolute_path_verifies(
        leerie, tmp_path, monkeypatch):
    """The judge sees the checkout's absolute paths; its citation is made
    relative before it is checked against staging's HEAD."""
    st, staging = _staging(leerie, tmp_path)

    async def judge(**kw):
        v = _judge_verdict(0, True)
        c = v["parts"][0]["cites"][0]
        c["path"] = os.path.join(kw["cwd"], c["path"])
        return {"verdicts": [v]}
    monkeypatch.setattr(leerie, "claude_p", judge)
    details: dict = {}
    unmet, _n, _c = asyncio.run(leerie._delivery_judge_unmet(
        "t", [{"item": "the feature exists"}], staging, st,
        {**leerie.DEFAULT_CAPS}, {}, {}, "final", item_details=details))
    assert unmet == [] and details[0]["rejected"] == 0


def test_the_ratchet_reverifies_on_the_commit(leerie, tmp_path,
                                             monkeypatch):
    st, staging = _staging(leerie, tmp_path)
    pre = _head(staging)
    subprocess.run(["git", "-C", str(staging), "-c", "user.email=t@t", "-c",
                    "user.name=t", "commit", "-q", "--allow-empty", "-m",
                    "group"], check=True)
    post = _head(staging)
    st.data["required_items"] = [{"item": "the feature exists"}]
    seen = []

    async def judge(**kw):
        seen.append(_seen(kw))
        return {"verdicts": [{"item_index": 0, "met": True,
                              "evidence": "e", "evidence_files": [
                                  os.path.join(kw["cwd"], "helper.py")]}]}
    monkeypatch.setattr(leerie, "claude_p", judge)
    out = asyncio.run(leerie._ratchet_judge(
        st, {**leerie.DEFAULT_CAPS}, {}, {}, staging, pre, post,
        {0: "prior"}, [0], typed_evidence=[], label="t"))
    assert out["confirmed"] == [] and out["errors"] == []
    (s,) = seen
    assert s["head"] == post and s["cwd"] != str(staging)
    assert not s["helper"]


def test_an_ignored_file_is_a_difference_too(leerie, tmp_path, monkeypatch):
    """Review round 13 L2, the reviewer's driver
    (`test_zz_r13_ignored_judge_tree.py`): `git status --porcelain` hides
    ignored files, so a worker's uncommitted `dist/a.py` (ignored) was in
    the tree the judges read — the uncited DEFECT CONTRACT verdict and the
    ratchet's reads included. Only a byte-identical tree reuses staging."""
    st = leerie.State(tmp_path / "state", "r")
    st.data = {"task": "t", "worker_count": 0}
    staging = _delivery_tree(st.run_dir / "worktrees" / "staging")
    (staging / ".gitignore").write_text("dist/\n")
    subprocess.run(["git", "-C", str(staging), "add", ".gitignore"],
                   check=True)
    subprocess.run(["git", "-C", str(staging), "-c", "user.email=t@t", "-c",
                    "user.name=t", "commit", "-qm", "ignore"], check=True)
    (staging / "dist").mkdir()
    (staging / "dist" / "a.py").write_text("A = 2  # never committed\n")
    assert subprocess.run(["git", "-C", str(staging), "status",
                           "--porcelain"], capture_output=True,
                          text=True).stdout == ""
    seen = []

    async def judge(**kw):
        seen.append((kw["cwd"], os.path.exists(
            os.path.join(kw["cwd"], "dist", "a.py"))))
        return {"verdicts": [_judge_verdict(0, True)]}
    monkeypatch.setattr(leerie, "claude_p", judge)
    asyncio.run(leerie._delivery_judge_unmet(
        "t", [{"item": "the feature exists"}], staging, st,
        {**leerie.DEFAULT_CAPS}, {}, {}, "final"))
    ((cwd, saw_ignored),) = seen
    assert cwd != str(staging) and not saw_ignored
    # Once the ignored file is gone, staging is byte-identical: reused.
    (staging / "dist" / "a.py").unlink()
    (staging / "dist").rmdir()
    seen.clear()
    asyncio.run(leerie._delivery_judge_unmet(
        "t", [{"item": "the feature exists"}], staging, st,
        {**leerie.DEFAULT_CAPS}, {}, {}, "final"))
    assert seen == [(str(staging), False)]


def test_the_judge_checkout_installs_nothing(leerie, tmp_path, monkeypatch):
    """Round 14 L2 follow-up: a staging with installed dependencies (ignored
    files) always takes the checkout, so that checkout must stay a plain
    `git worktree add --detach` — a judge only reads files. The run has an
    install recipe that would leave a marker; the judge's checkout runs
    none of it."""
    st, staging = _staging(leerie, tmp_path)
    marker = tmp_path / "installed"
    st.data["provision"] = {"recipe": [{
        "kind": "install",
        "command": ["sh", "-c", f"touch {marker}; mkdir -p node_modules"]}]}
    called = []

    async def no_install(*a, **k):
        called.append(a)
        return True
    monkeypatch.setattr(leerie, "_ensure_worktree_deps", no_install)
    seen = []

    async def judge(**kw):
        seen.append((kw["cwd"], os.path.exists(
            os.path.join(kw["cwd"], "node_modules"))))
        return {"verdicts": [_judge_verdict(0, True)]}
    monkeypatch.setattr(leerie, "claude_p", judge)
    asyncio.run(leerie._delivery_judge_unmet(
        "t", [{"item": "the feature exists"}], staging, st,
        {**leerie.DEFAULT_CAPS}, {}, {}, "final"))
    ((cwd, saw_deps),) = seen
    assert cwd != str(staging) and not saw_deps
    assert called == [] and not marker.exists()


def test_a_failed_checkout_is_retried_on_the_next_entry(leerie, tmp_path,
                                                        monkeypatch):
    """Round 14: a checkout that could not be made is not remembered. The
    judges' `_committed_checkout` raises on that entry and makes a fresh
    checkout on the next; the execution route's `_committed_test_tree`
    yields None (no measurement) and then a checkout."""
    st, staging = _staging(leerie, tmp_path)
    st.data["provision"] = {"recipe": []}
    real = leerie._worktree_add_detached_async
    fail = {"n": 1}
    adds = []

    async def flaky(*a, **k):
        adds.append(a[1])
        if fail["n"]:
            fail["n"] -= 1
            return subprocess.CompletedProcess([], 128, "", "transient")
        return await real(*a, **k)
    monkeypatch.setattr(leerie, "_worktree_add_detached_async", flaky)

    async def judge_tree():
        async with leerie._committed_checkout(st, staging, "j") as cwd:
            return cwd, os.path.exists(os.path.join(cwd, "helper.py"))
    with pytest.raises(RuntimeError):
        asyncio.run(judge_tree())
    cwd, helper = asyncio.run(judge_tree())
    assert cwd != staging and not helper and len(adds) == 2

    async def test_tree():
        async with leerie._committed_test_tree(
                st, {**leerie.DEFAULT_CAPS}, staging, "t",
                log_path=tmp_path / "t.log") as tree:
            return tree, tree is not None and (tree / "helper.py").exists()
    fail["n"] = 1
    assert asyncio.run(test_tree()) == (None, False)
    tree, helper = asyncio.run(test_tree())
    assert tree is not None and tree != staging and not helper
    assert len(adds) == 4
