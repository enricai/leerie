"""Review round 9 L1: on a repository that leaves test artifacts, the
final conformer's failing-test-ID diff is stamped with the committed tree,
so phase 5's shipping-tree refresh reuses it instead of measuring the same
tree again (DESIGN §8 *The final diff describes the tree that ships*).

Before: the stamp was `_worktree_tree_sha(staging)`, None whenever
`__pycache__/` was not ignored, so every such run paid a fresh checkout,
a full install, the discovery repeats and B* a second time. The stamp is
still withheld when the measurement saw something that does not ship: an
uncommitted tracked edit, or an untracked file the final conformer
wrote."""
from __future__ import annotations

import asyncio
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))
from test_redrive_test_id_diff_refresh import _git  # noqa: E402
from test_shipping_tree_is_committed import (  # noqa: E402
    _phase5, _quiet_conformer, _repo)


def _drive(leerie, tmp_path, monkeypatch, ignore, *, worker_file=None,
           tracked_edit=False):
    run = _repo(leerie, tmp_path, monkeypatch, ignore=ignore)
    run["caps"]["confirm_intermittent_repeats"] = 3
    staging = run["staging"]

    async def conformer(*a, **k):
        # The final conformer leaves its work uncommitted.
        if worker_file:
            (staging / worker_file).write_text("def test_w():\n    pass\n")
            log = run["run_dir"] / "logs" / f"{k['sid']}.log"
            log.write_text(json.dumps({"message": {"content": [
                {"type": "tool_use", "id": "1", "name": "Write",
                 "input": {"file_path": str(staging / worker_file)}}]}})
                + "\n")
        if tracked_edit:
            (staging / "mod.py").write_text("X = 2  # edited\n")
        return {}
    _quiet_conformer(leerie, monkeypatch)
    monkeypatch.setattr(leerie, "claude_p", conformer)
    calls = []
    real = leerie._intermittent_discovery

    async def spy(st, caps, tree, *a, **k):
        r = await real(st, caps, tree, *a, **k)
        calls.append(str(tree).rsplit("/", 1)[-1])
        return r
    monkeypatch.setattr(leerie, "_intermittent_discovery", spy)
    asyncio.run(leerie._run_final_conformance(
        run["run_dir"], run["st"], run["caps"], {"conformer": "sonnet"},
        {"conformer": "low"}))
    _phase5(leerie, run)
    return run, calls


@pytest.mark.parametrize("ignore", ["", "__pycache__/\n"])
def test_the_same_committed_tree_is_measured_once(leerie, tmp_path,
                                                  monkeypatch, ignore):
    """The reviewer's driver: with `__pycache__/` not ignored, discovery
    ran twice (`staging`, then `…-shipping-tree`). Now once, either way,
    and the run still ends `done` with an empty section."""
    run, calls = _drive(leerie, tmp_path, monkeypatch, ignore)
    assert calls == ["staging"]
    st = run["st"]
    assert st.data["test_id_diff"]["tree_sha"] == asyncio.run(
        leerie._committed_tree_sha(str(run["staging"])))
    assert leerie._decide_outcome(st, any_commits=True) == "done"
    assert leerie._format_pr_end_state_section(st.data) == ""


def test_an_untracked_file_the_conformer_wrote_is_measured_again(
        leerie, tmp_path, monkeypatch):
    """The measurement in staging collected a test the conformer wrote and
    never committed: not the tree that ships, so the refresh measures the
    clean checkout."""
    run, calls = _drive(leerie, tmp_path, monkeypatch, "",
                        worker_file="tests/test_w.py")
    assert calls == ["staging", f"{run['st'].run_id[:12]}-shipping-tree"]


def test_a_tracked_edit_is_measured_again(leerie, tmp_path, monkeypatch):
    run, calls = _drive(leerie, tmp_path, monkeypatch, "",
                        tracked_edit=True)
    assert len(calls) == 2 and calls[1].endswith("shipping-tree")
