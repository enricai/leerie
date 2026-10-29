"""Review round 9 L1: on a repository that leaves test artifacts, the
final conformer's failing-test-ID diff is stamped with the committed tree,
so phase 5's shipping-tree refresh reuses it instead of measuring the same
tree again (DESIGN §8 *The final diff describes the tree that ships*).

Before: the stamp was `_worktree_tree_sha(staging)`, None whenever
`__pycache__/` was not ignored, so every such run paid a fresh checkout,
a full install, the discovery repeats and B* a second time. The stamp is
still withheld when the measurement saw something that does not ship: an
uncommitted tracked edit, or any untracked file leerie's own commands did
not produce. Round 10 H1: the round 9 rule asked the final conformer's
Write/Edit tool calls instead, so a module it made through Bash read as an
artifact, and a run whose shipped tree failed a test ended `done`."""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
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


# Red until I2 (DESIGN §6½: leerie's commands leave the tree as they found
# it): leerie's own test run leaves `__pycache__/` in staging, and the
# clean-tree gate (DESIGN §8 *A worker leaves its tree clean*) holds the
# final conformer to it. Strict, so the mark goes when I2 lands.
_NEEDS_I2 = pytest.mark.xfail(strict=True, reason="needs I2")


@pytest.mark.parametrize("ignore", [pytest.param("", marks=_NEEDS_I2),
                                    "__pycache__/\n"])
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


def test_an_untracked_file_the_conformer_wrote_never_reaches_a_measurement(
        leerie, tmp_path, monkeypatch):
    """Rewritten for I1 (DESIGN §8 *A worker leaves its tree clean*): the
    measurement in staging used to collect a test the conformer wrote and
    never committed, so the refresh measured the clean checkout. The
    conformer is now held to a clean tree: it is re-asked, and, still
    dirty, its round is rolled back before any measurement of it — the
    file is gone and the run is unconverged with it named."""
    run, calls = _drive(leerie, tmp_path, monkeypatch, "__pycache__/\n",
                        worker_file="tests/test_w.py")
    assert not (run["staging"] / "tests" / "test_w.py").exists()
    (rec,) = run["st"].data["unclean_trees"].values()
    assert rec["lines"] == ["?? tests/test_w.py"]
    assert leerie._decide_outcome(run["st"], any_commits=True) == \
        "unconverged"


def test_a_tracked_edit_never_reaches_a_measurement(leerie, tmp_path,
                                                    monkeypatch):
    run, calls = _drive(leerie, tmp_path, monkeypatch, "__pycache__/\n",
                        tracked_edit=True)
    assert (run["staging"] / "mod.py").read_text() == "X = 2\n"
    (rec,) = run["st"].data["unclean_trees"].values()
    assert rec["lines"] == [" M mod.py"]


MOD = "try:\n    from helper import X\nexcept ImportError:\n    X = 0\n"


@pytest.mark.parametrize("tool", ["Bash", "Write"])
def test_an_untracked_module_that_made_the_suite_pass_is_never_reused(
        leerie, tmp_path, monkeypatch, tool):
    """Round 10 H1, the reviewer's driver: the branch commits `mod.py`,
    which falls back to `X = 0` without a `helper` module that does not
    ship, so `test_m` fails on HEAD. The final conformer makes `helper.py`
    and leaves it untracked. Before, through Bash (`cat > helper.py`) the
    staging measurement — green — was stamped with the committed tree and
    the run ended `done` with an empty section; only a Write was caught.
    Either way the clean checkout is measured now and `test_m` is new."""
    run = _repo(leerie, tmp_path, monkeypatch, ignore="__pycache__/\n")
    staging, st = run["staging"], run["st"]
    (staging / "mod.py").write_text(MOD)
    _git(staging, "commit", "-qam", "use helper")

    async def conformer(*a, **k):
        p = os.path.join(k["cwd"], "helper.py")
        with open(p, "w") as f:
            f.write("X = 2\n")
        inp = ({"command": "cat > helper.py <<'E'\nX = 2\nE"}
               if tool == "Bash" else {"file_path": p, "content": "X = 2\n"})
        with open(run["run_dir"] / "logs" / f"{k['sid']}.log", "a") as f:
            f.write(json.dumps({"type": "assistant", "message": {"content": [
                {"type": "tool_use", "id": "t1", "name": tool,
                 "input": inp}]}}) + "\n")
        return {}
    _quiet_conformer(leerie, monkeypatch)
    monkeypatch.setattr(leerie, "claude_p", conformer)
    asyncio.run(leerie._run_final_conformance(
        run["run_dir"], st, run["caps"], {"conformer": "sonnet"},
        {"conformer": "low"}))
    # I1 (DESIGN §8 *A worker leaves its tree clean*): the conformer is
    # re-asked, and, still dirty, its round is rolled back — `helper.py`
    # never reaches a measurement and the lines are recorded.
    assert not (staging / "helper.py").exists()
    assert st.data["unclean_trees"]["final conformer:round 0"][
        "lines"] == ["?? helper.py"]
    _phase5(leerie, run)
    diff = st.data["test_id_diff"]
    assert diff["tree_sha"] == _git(staging, "rev-parse", "HEAD^{tree}")
    assert any("test_m" in i for i in diff["new"])
    assert leerie._decide_outcome(st, any_commits=True) == "unconverged"
    assert leerie._format_pr_end_state_section(st.data) != ""
    # What ships really fails test_m.
    co = tmp_path / "co"
    _git(run["repo"], "worktree", "add", "-q", "--detach", str(co),
         _git(staging, "rev-parse", "HEAD"))
    r = subprocess.run(["python3", "-m", "pytest", "-q", "-p",
                        "no:cacheprovider", "tests"], cwd=co,
                       capture_output=True, text=True)
    assert r.returncode != 0 and "test_m" in r.stdout
