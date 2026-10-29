"""Review round 12 claims M1: two paths could make a verdict met with no
read of a commit (DESIGN §8 *Every post-execution verdict is of the
committed tree*). The delivery judge's DEFECT CONTRACT verdict carries no
citation and resolved `met` on a single sample, judged with staging's
working tree as cwd; `_ratchet_judge`'s re-verification of kept items ran
in staging with no citation check.

Rounds 12 to 14 sent both to a disposable checkout of staging's HEAD
whenever staging held anything. With I1 (every worker leaves staging
clean) and I2 (every command of leerie's own leaves it as it found it),
staging is its HEAD plus ignored files at every reader: the judges read
staging itself, after `_assert_staging_clean`, and dirt there — an
untracked helper, an uncommitted edit — stops the run before any judge
sees it."""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))
from stub_helpers import (DELIVERY_CITED_FILE, DELIVERY_CITED_LINE,  # noqa: E402
                          _delivery_tree, _judge_verdict)


def _clean_staging(leerie, tmp_path):
    st = leerie.State(tmp_path / "state", "r")
    st.data = {"task": "t", "worker_count": 0}
    staging = _delivery_tree(st.run_dir / "worktrees" / "staging")
    return st, staging


def _dirty(staging, dirt: str) -> str:
    """What does not ship, the round 12 driver's two shapes; returns the
    porcelain line."""
    if dirt == "untracked":
        (staging / "helper.py").write_text("CONTRACT_HOLDS = True\n")
        return "?? helper.py"
    (staging / DELIVERY_CITED_FILE).write_text("# edited, uncommitted\n")
    return f" M {DELIVERY_CITED_FILE}"


def _seen(kw) -> dict:
    cwd = kw["cwd"]
    head = subprocess.run(["git", "-C", cwd, "rev-parse", "HEAD"],
                          capture_output=True, text=True).stdout.strip()
    return {"cwd": cwd, "head": head,
            "cited": open(os.path.join(cwd, DELIVERY_CITED_FILE)).read()}


def _head(tree) -> str:
    return subprocess.run(["git", "-C", str(tree), "rev-parse", "HEAD"],
                          capture_output=True, text=True).stdout.strip()


def test_the_delivery_judge_and_its_contract_read_staging_at_its_commit(
        leerie, tmp_path, monkeypatch):
    st, staging = _clean_staging(leerie, tmp_path)
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
    assert s["cwd"] == str(staging) and s["head"] == _head(staging)
    assert s["cited"].startswith(DELIVERY_CITED_LINE)


@pytest.mark.parametrize("dirt", ["untracked", "tracked"])
def test_dirt_in_staging_stops_the_judge_before_it_reads(
        leerie, tmp_path, monkeypatch, capsys, dirt):
    """The round 12 driver: an untracked helper or an uncommitted edit in
    staging. No judge is spawned on that tree — the run stops, naming the
    line (DESIGN §8 *The final diff describes the tree that ships*)."""
    st, staging = _clean_staging(leerie, tmp_path)
    line = _dirty(staging, dirt)
    seen = []

    async def judge(**kw):
        seen.append(kw["cwd"])
        return {"verdicts": [_judge_verdict(0, True)]}
    monkeypatch.setattr(leerie, "claude_p", judge)
    with pytest.raises(SystemExit):
        asyncio.run(leerie._delivery_judge_unmet(
            "t", [{"item": "the feature exists"}], staging, st,
            {**leerie.DEFAULT_CAPS}, {}, {}, "final",
            contract_ctx={"defect_shape": "x", "sites": []}))
    assert seen == []
    err = capsys.readouterr().err
    assert "staging is not clean at the delivery judge" in err
    assert line in err


def test_a_judge_citing_stagings_absolute_path_verifies(
        leerie, tmp_path, monkeypatch):
    """The judge sees staging's absolute paths; its citation is made
    relative before it is checked against staging's HEAD."""
    st, staging = _clean_staging(leerie, tmp_path)

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


def test_a_met_resting_on_an_ignored_file_buys_nothing(leerie, tmp_path,
                                                       monkeypatch):
    """Ignored files stay in front of the judges, as in front of every test
    run (round 13 L2's checkout is gone with I1 + I2): a cite of an
    uncommitted, ignored `dist/a.py` is not in `HEAD`, so it is rejected
    and the item is not met; the run is not stopped by it."""
    st, staging = _clean_staging(leerie, tmp_path)
    (staging / ".gitignore").write_text("dist/\n")
    subprocess.run(["git", "-C", str(staging), "add", ".gitignore"],
                   check=True)
    subprocess.run(["git", "-C", str(staging), "-c", "user.email=t@t", "-c",
                    "user.name=t", "commit", "-qm", "ignore"], check=True)
    (staging / "dist").mkdir()
    (staging / "dist" / "a.py").write_text("FEATURE = True\n")

    async def judge(**kw):
        assert os.path.exists(os.path.join(kw["cwd"], "dist", "a.py"))
        v = _judge_verdict(0, True)
        v["parts"][0]["cites"] = [{"path": "dist/a.py", "line_start": 1,
                                   "line_end": 1,
                                   "quote": "FEATURE = True"}]
        return {"verdicts": [v]}
    monkeypatch.setattr(leerie, "claude_p", judge)
    details: dict = {}
    unmet, _n, _c = asyncio.run(leerie._delivery_judge_unmet(
        "t", [{"item": "the feature exists"}], staging, st,
        {**leerie.DEFAULT_CAPS}, {}, {}, "final", item_details=details))
    assert [u["item_index"] for u in unmet] == [0]
    assert details[0]["rejected"] >= 1


@pytest.mark.parametrize("dirt", [None, "untracked", "tracked"])
def test_the_ratchet_reverifies_on_the_commit(leerie, tmp_path,
                                             monkeypatch, capsys, dirt):
    st, staging = _clean_staging(leerie, tmp_path)
    pre = _head(staging)
    subprocess.run(["git", "-C", str(staging), "-c", "user.email=t@t", "-c",
                    "user.name=t", "commit", "-q", "--allow-empty", "-m",
                    "group"], check=True)
    post = _head(staging)
    st.data["required_items"] = [{"item": "the feature exists"}]
    line = _dirty(staging, dirt) if dirt else None
    seen = []

    async def judge(**kw):
        seen.append(_seen(kw))
        return {"verdicts": [{"item_index": 0, "met": True,
                              "evidence": "e", "evidence_files": [
                                  os.path.join(kw["cwd"],
                                               DELIVERY_CITED_FILE)]}]}
    monkeypatch.setattr(leerie, "claude_p", judge)

    def ratchet():
        return asyncio.run(leerie._ratchet_judge(
            st, {**leerie.DEFAULT_CAPS}, {}, {}, staging, pre, post,
            {0: "prior"}, [0], typed_evidence=[], label="t"))
    if dirt:
        with pytest.raises(SystemExit):
            ratchet()
        assert seen == []
        assert line in capsys.readouterr().err
        return
    out = ratchet()
    assert out["confirmed"] == [] and out["errors"] == []
    (s,) = seen
    assert s["head"] == post and s["cwd"] == str(staging)
