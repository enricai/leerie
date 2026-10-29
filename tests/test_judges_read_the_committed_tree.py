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
