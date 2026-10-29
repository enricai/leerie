"""The delivery judges read exactly the commit (DESIGN §8 *Every
post-execution verdict is of the committed tree*): a detached checkout of
staging's HEAD, no install, removed afterwards — never staging.

Review round 12 claims M1: the DEFECT CONTRACT verdict carries no citation
and the ratchet's re-verification has no citation check, so whatever is in
the judge's tree can hold them up — an untracked helper (round 12), a
worker's uncommitted, ignored `dist/` build (round 13 L2). I1 and I2 keep
staging at HEAD plus ignored files, but the ignored files are the
exposure: the judge's `grep -r`/`cat` read them, and a build in staging is
stale as soon as HEAD moves past it."""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))
from stub_helpers import (DELIVERY_CITED_FILE, DELIVERY_CITED_LINE,  # noqa: E402
                          _delivery_tree, _judge_verdict)


def _git(tree, *args) -> str:
    return subprocess.run(["git", "-C", str(tree), "-c", "user.email=t@t",
                           "-c", "user.name=t", *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def _staging(leerie, tmp_path):
    st = leerie.State(tmp_path / "state", "r")
    st.data = {"task": "t", "worker_count": 0}
    staging = _delivery_tree(st.run_dir / "worktrees" / "staging")
    return st, staging


def _seen(kw) -> dict:
    cwd = kw["cwd"]
    return {"cwd": cwd, "head": _git(cwd, "rev-parse", "HEAD"),
            "cited": open(os.path.join(cwd, DELIVERY_CITED_FILE)).read(),
            "helper": os.path.exists(os.path.join(cwd, "helper.py"))}


def _judge_unmet(leerie, st, staging, **kw):
    return asyncio.run(leerie._delivery_judge_unmet(
        "t", [{"item": "the feature exists"}], staging, st,
        {**leerie.DEFAULT_CAPS}, {}, {}, "final", **kw))


def test_the_judge_and_its_contract_read_a_checkout_of_the_commit(
        leerie, tmp_path, monkeypatch):
    st, staging = _staging(leerie, tmp_path)
    seen = []

    async def judge(**kw):
        seen.append(_seen(kw))
        return {"verdicts": [_judge_verdict(0, True)],
                "contract": {"verdict": "met", "evidence": "holds"}}
    monkeypatch.setattr(leerie, "claude_p", judge)
    unmet, _n, contract = _judge_unmet(
        leerie, st, staging, contract_ctx={"defect_shape": "x", "sites": []})
    assert unmet == [] and contract["verdict"] == "met"
    (s,) = seen
    assert s["cwd"] != str(staging) and s["head"] == _git(staging,
                                                         "rev-parse", "HEAD")
    assert s["cited"].startswith(DELIVERY_CITED_LINE)
    assert not os.path.exists(s["cwd"])            # removed afterwards


STALE_BUILD = "export const FIXED_SYMBOL = true; // built before the rollback\n"


def test_a_stale_ignored_build_never_reaches_an_uncited_verdict(
        leerie, tmp_path, monkeypatch):
    """The evidence for reading a checkout: staging was built while the fix
    was in (an ignored `dist/` holding the fixed symbol), then the fix was
    rolled back and HEAD moved on without a rebuild. The contract judge
    does what judges do — `grep -rn` for the symbol — and its verdict is
    uncited. In staging the grep finds the stale build; in the checkout it
    finds nothing, so the contract is not met."""
    st, staging = _staging(leerie, tmp_path)
    (staging / ".gitignore").write_text("dist/\n")
    _git(staging, "add", ".gitignore")
    _git(staging, "commit", "-qm", "ignore dist")
    (staging / "dist").mkdir()
    (staging / "dist" / "a.js").write_text(STALE_BUILD)
    assert _git(staging, "status", "--porcelain",
                "--untracked-files=all") == ""          # I1 + I2 hold
    hits = subprocess.run(["grep", "-rln", "FIXED_SYMBOL", "."],
                          cwd=staging, capture_output=True, text=True).stdout
    assert "dist/a.js" in hits                         # the exposure is real
    seen = []

    async def judge(**kw):
        out = subprocess.run(["grep", "-rln", "FIXED_SYMBOL", "."],
                             cwd=kw["cwd"], capture_output=True,
                             text=True).stdout
        seen.append(out)
        return {"verdicts": [_judge_verdict(0, True)],
                "contract": {"verdict": "met" if out else "unmet",
                             "evidence": out or "no FIXED_SYMBOL anywhere"}}
    monkeypatch.setattr(leerie, "claude_p", judge)
    _unmet, _n, contract = _judge_unmet(
        leerie, st, staging,
        contract_ctx={"defect_shape": "x", "sites": []})
    assert seen and all(s == "" for s in seen)
    assert contract["verdict"] != "met"


@pytest.mark.parametrize("dirt", ["untracked", "tracked"])
def test_content_only_staging_holds_is_not_in_front_of_the_judge(
        leerie, tmp_path, monkeypatch, dirt):
    """Round 12's driver: an untracked helper or an uncommitted edit. The
    judge reads the commit whatever staging holds."""
    st, staging = _staging(leerie, tmp_path)
    if dirt == "untracked":
        (staging / "helper.py").write_text("CONTRACT_HOLDS = True\n")
    else:
        (staging / DELIVERY_CITED_FILE).write_text("# edited\n")
    seen = []

    async def judge(**kw):
        seen.append(_seen(kw))
        return {"verdicts": [_judge_verdict(0, True)]}
    monkeypatch.setattr(leerie, "claude_p", judge)
    _judge_unmet(leerie, st, staging)
    (s,) = seen
    assert not s["helper"] and s["cited"].startswith(DELIVERY_CITED_LINE)


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
    unmet, _n, _c = _judge_unmet(leerie, st, staging, item_details=details)
    assert unmet == [] and details[0]["rejected"] == 0


def test_the_ratchet_reverifies_on_a_checkout_of_the_commit(
        leerie, tmp_path, monkeypatch):
    st, staging = _staging(leerie, tmp_path)
    pre = _git(staging, "rev-parse", "HEAD")
    _git(staging, "commit", "-q", "--allow-empty", "-m", "group")
    post = _git(staging, "rev-parse", "HEAD")
    (staging / "helper.py").write_text("X = 1\n")
    st.data["required_items"] = [{"item": "the feature exists"}]
    seen = []

    async def judge(**kw):
        seen.append(_seen(kw))
        return {"verdicts": [{"item_index": 0, "met": True,
                              "evidence": "e", "evidence_files": [
                                  os.path.join(kw["cwd"],
                                               DELIVERY_CITED_FILE)]}]}
    monkeypatch.setattr(leerie, "claude_p", judge)
    out = asyncio.run(leerie._ratchet_judge(
        st, {**leerie.DEFAULT_CAPS}, {}, {}, staging, pre, post,
        {0: "prior"}, [0], typed_evidence=[], label="t"))
    assert out["confirmed"] == [] and out["errors"] == []
    (s,) = seen
    assert s["head"] == post and s["cwd"] != str(staging)
    assert not s["helper"]


def test_the_judge_checkout_installs_nothing_and_fails_as_a_crash(
        leerie, tmp_path, monkeypatch):
    """A judge runs nothing, so its checkout gets no dependency install;
    one that cannot be made raises WorkerError — the judge's crash path,
    which settles nothing met — and the next entry makes a fresh one."""
    st, staging = _staging(leerie, tmp_path)
    called = []

    async def no_install(*a, **k):
        called.append(a)
        return True
    monkeypatch.setattr(leerie, "_ensure_worktree_deps", no_install)
    real = leerie._worktree_add_detached_async
    fail = {"n": 1}

    async def flaky(*a, **k):
        if fail["n"]:
            fail["n"] -= 1
            return subprocess.CompletedProcess([], 128, "", "transient")
        return await real(*a, **k)
    monkeypatch.setattr(leerie, "_worktree_add_detached_async", flaky)

    async def enter():
        async with leerie._judge_checkout(st, staging, "j") as cwd:
            return cwd, (cwd / "node_modules").exists()
    with pytest.raises(leerie.WorkerError):
        asyncio.run(enter())
    cwd, deps = asyncio.run(enter())
    assert cwd != staging and not deps and called == []
    assert not cwd.exists()
