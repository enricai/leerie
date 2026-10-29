"""Review round 6, unproven suspicion, driven and fixed: a delivery re-drive
moves HEAD and the failing-test-ID diff was never recomputed, so
`_redrive_apply_item_verdicts` re-applied the detection-time test-outcome
verdict and `_decide_outcome` read the detection-time `new`, on a tree the
diff never measured. Driven before the fix on a real pytest suite: a test
confirmed new at detection, fixed by a re-drive group, left the row "unmet —
new failing test(s) confirmed against the base" and the run `unconverged`
on a tree where the test passes (DESIGN §8 *Delivery re-drive*)."""
from __future__ import annotations

import asyncio
import subprocess

import pytest

CMD = "python3 -m pytest -q -p no:cacheprovider tests"
SUITE = "def test_x():\n    assert {ok}\n"
ITEM = "fix y; no existing test breaks"


def _git(cwd, *a):
    r = subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


@pytest.fixture
def redriven(leerie, tmp_path, monkeypatch):
    """Detection's diff (test_x confirmed new) and an item row carrying its
    test-outcome part; then a re-drive group fixes test_x and moves HEAD."""
    monkeypatch.setattr(leerie, "_host_cpu_psi", lambda path=None: 0.0)
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / ".gitignore").write_text(".leerie/\n__pycache__/\n")
    (repo / "tests" / "test_s.py").write_text(SUITE.format(ok="True"))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    base = _git(repo, "rev-parse", "HEAD")
    run_id = "fix-001-abcdef"
    _git(repo, "checkout", "-qb", f"leerie/runs/{run_id}")
    (repo / "tests" / "test_s.py").write_text(SUITE.format(ok="False"))
    _git(repo, "commit", "-qam", "break")
    _git(repo, "checkout", "-q", "main")
    root = repo / ".leerie"
    run_dir = root / "runs" / run_id
    (run_dir / "logs").mkdir(parents=True)
    staging = run_dir / "worktrees" / "staging"
    _git(repo, "worktree", "add", "-q", str(staging), f"leerie/runs/{run_id}")
    st = leerie.State(root, run_id)
    st.data = {"task": "x", "working_branch": "main",
               "conformance": {"_baseline": {"sha": base}},
               "baseline_failing_ids": [], "subtask_status": {},
               "required_items": [{
                   "item": ITEM, "parts": ["fix y", "no existing test breaks"],
                   "part_checks": [{"part": "no existing test breaks",
                                    "check": "test_id_diff",
                                    "verifiability": "execution"}]}]}
    st.repo_root = repo
    st.save()
    caps = {**leerie.DEFAULT_CAPS, "confirm_intermittent_repeats": 0}
    post = {"tests": {"ran": True, "measured": True, "passed": False,
                      "command": CMD, "summary": ""}}
    log = st.run_dir / "logs" / "t.log"
    assert asyncio.run(leerie._attach_failing_ids(
        post["tests"], str(staging), CMD, st, caps, log_path=log,
        label="m"))
    v = asyncio.run(leerie._confirm_final_test_failures(
        st, caps, str(staging), post, log_path=log, label="final-r0"))
    assert len(v["new"]) == 1
    t0 = asyncio.run(leerie._worktree_tree_sha(str(staging)))
    tid = leerie._test_outcome_verdict(st, t0)
    assert tid["verdict"] == "unmet"
    row = leerie._settle_test_outcome_part({
        "item_index": 0, "item": ITEM, "verdict": "met", "by": [],
        "test_outcome_part": {"parts": ["no existing test breaks"],
                              "verdict": tid["verdict"], "new": tid["new"],
                              "reason": ""}})
    assert row["verdict"] == "unmet"
    st.data["delivery_gate"] = {"items": [row], "unmet_final": []}
    st.save()
    # A re-drive group fixes y — and with it test_x — and moves HEAD.
    (staging / "tests" / "test_s.py").write_text(SUITE.format(ok="True"))
    _git(staging, "commit", "-qam", "redrive")
    t1 = asyncio.run(leerie._worktree_tree_sha(str(staging)))
    assert t1 and t1 != t0
    monkeypatch.setattr(leerie, "resolve_blt", lambda root: {"test": CMD})
    return {"st": st, "staging": staging, "x": v["new"][0], "t0": t0,
            "t1": t1, "caps": caps, "sha": _git(staging, "rev-parse", "HEAD")}


def test_the_diff_is_measured_again_on_the_tree_the_redrive_ships(
        leerie, redriven):
    st, staging = redriven["st"], redriven["staging"]
    asyncio.run(leerie._redrive_refresh_test_id_diff(
        st.run_dir, st, redriven["caps"], staging))
    diff = st.data["test_id_diff"]
    assert diff["tree_sha"] == redriven["t1"] and diff["new"] == []
    leerie._redrive_apply_item_verdicts(
        st, {0: {"unmet": False, "by": [], "evidence": ""}},
        redriven["sha"], redriven["t1"])
    row = st.data["delivery_gate"]["items"][0]
    assert row["verdict"] == "met" and "test_outcome_part" not in row
    assert leerie._decide_outcome(st, any_commits=True) == "done"


def test_a_diff_that_cannot_be_measured_again_is_marked_stale(
        leerie, redriven, monkeypatch):
    """No test command on the shipping tree: the old diff is kept, marked
    `stale_for_tree`. Its confirmed `new` still counts (unmet, outcome
    `unconverged`), the reason saying it was confirmed on an earlier tree;
    with no `new`, the part reads unknown ("not of the final tree") —
    never met over a tree the diff never measured."""
    st, staging = redriven["st"], redriven["staging"]
    monkeypatch.setattr(leerie, "resolve_blt", lambda root: {})
    asyncio.run(leerie._redrive_refresh_test_id_diff(
        st.run_dir, st, redriven["caps"], staging))
    diff = st.data["test_id_diff"]
    assert diff["tree_sha"] == redriven["t0"]
    assert diff["stale_for_tree"] == redriven["t1"]
    leerie._redrive_apply_item_verdicts(
        st, {0: {"unmet": False, "by": [], "evidence": ""}},
        redriven["sha"], redriven["t1"])
    row = st.data["delivery_gate"]["items"][0]
    assert row["verdict"] == "unmet" and redriven["x"] in row["reason"]
    assert leerie._decide_outcome(st, any_commits=True) == "unconverged"
    assert "an earlier tree" in st.data["outcome_reasons"][0]
    # Detection's diff said "no regressions" instead: on the shipping tree
    # that reads unknown, never met.
    st.data["test_id_diff"] = {**st.data["test_id_diff"], "new": []}
    leerie._resettle_test_outcome_rows(st, redriven["t1"])
    row = st.data["delivery_gate"]["items"][0]
    assert row["verdict"] == "unknown"
    assert "not of the final tree" in row["reason"]
    gaps = [e for e in st.data["needs_human_verification"]
            if e.get("source") == leerie.TEST_OUTCOME_GAP_SOURCE]
    assert len(gaps) == 1 and ITEM in gaps[0]["detail"]


def test_the_redrive_tail_refreshes_and_resettles(leerie):
    """Structure, paired with the two consumer tests above: the re-drive's
    end-of-pass block refreshes the diff before re-checking the items, and
    re-settles every row on the final tree."""
    import inspect
    src = inspect.getsource(leerie._run_delivery_redrive)
    i_ref = src.index("_redrive_refresh_test_id_diff(")
    i_app = src.index("_redrive_apply_item_verdicts(st, ver, final,")
    i_res = src.index("_resettle_test_outcome_rows(st, final_tree)")
    assert i_ref < i_app < i_res
