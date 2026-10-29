"""Review round 12 L3: a met resting only on a worker's command run was
rejected as `record_worker` (a worker's run happened in a worker's tree),
which could leave an item no implementer can satisfy — the orchestrator
never runs that command itself, so a re-drive cannot make the citation
verify. Now the orchestrator re-runs the cited command on the committed
tree (DESIGN §8 *Execution-shaped items are judged from the run's own
records*): passed → the citation verifies as its own measurement of HEAD;
failed → the vote stays unmet; cannot be run → human verification, never
a re-drive. Real git, real child processes; only the cgroup broker and
the host-pressure gate are stubbed, as in tests/test_settle_by_execution.py.
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))
from stub_helpers import _delivery_tree  # noqa: E402

QUOTE = "check ok: the e2e suite passed"


@pytest.fixture
def world(leerie, tmp_path, monkeypatch):
    monkeypatch.setattr(leerie, "_cgroup_create", lambda *a, **k: None)

    async def _gate(caps, **_k):
        return {"waited_sec": 0.0, "timed_out": False, "psi": 1.0,
                "readable": True}
    monkeypatch.setattr(leerie, "_confirm_gate", _gate)
    monkeypatch.setattr(leerie, "resolve_blt", lambda root: {})
    monkeypatch.setattr(leerie, "_blt_verbs", lambda root: {"python3"})
    st = leerie.State(tmp_path / "state", "r")
    st.data = {"task": "t", "worker_count": 0}
    staging = _delivery_tree(st.run_dir / "worktrees" / "staging")
    st.repo_root = staging
    (staging / "e2e.py").write_text(
        "import sys\nfrom pathlib import Path\n"
        "sys.exit(0 if Path('src/delivered_feature.py').exists() else 1)\n")
    (staging / "needs_helper.py").write_text("import helper\n")
    subprocess.run(["git", "-C", str(staging), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(staging), "-c", "user.email=t@t", "-c",
                    "user.name=t", "commit", "-qm", "e2e"], check=True)
    # Only staging has the helper: a worker's run there passes, HEAD's
    # does not (review round 11's shape).
    (staging / "helper.py").write_text("H = 1\n")
    calls: list[dict] = []

    def judge_with(cmd):
        entry = (f"[final-conformer-r0] $ {cmd}\n"
                 f"    result tail: {QUOTE}")
        record = leerie._ExecutedRecord(entry, "", [(entry, cmd)])

        async def judge(**kw):
            calls.append(kw)
            return {"verdicts": [{
                "item_index": 0, "met": True, "evidence": "ran it",
                "parts": [{"requirement": "the e2e suite passes",
                           "kind": "execution", "status": "met",
                           "cites": [{"path": "RECORD", "line_start": 0,
                                      "line_end": 0, "quote": QUOTE}]}]}]}
        monkeypatch.setattr(leerie, "claude_p", judge)
        routing: dict = {}
        unmet, n, _c = asyncio.run(leerie._delivery_judge_unmet(
            "t", [{"item": "the e2e suite passes"}], staging, st,
            {**leerie.DEFAULT_CAPS, "worker_timeout_sec": 60,
             "confirm_budget_sec": 30}, {}, {}, "final",
            exec_digest=record, routing=routing))
        return unmet, routing
    return {"st": st, "staging": staging, "judge": judge_with,
            "calls": calls}


def _runs(st):
    return [r for r in (st.data.get("record_reruns") or {}).values()
            if "head" in r]


def _human(st):
    return [e for e in st.data.get("needs_human_verification") or []
            if e.get("source") == "record_rerun"]


def test_a_cited_command_that_passes_on_head_verifies(leerie, world):
    unmet, routing = world["judge"]("python3 e2e.py")
    assert unmet == []
    (r,) = _runs(world["st"])
    assert r["verdict"] == "passed" and r["command"] == "python3 e2e.py"
    assert not _human(world["st"]) and "human_verification" not in routing
    # Once per command per commit, however many samples cited it.
    assert len(world["calls"]) == 3


def test_a_cited_command_that_fails_on_head_stays_unmet(leerie, world):
    """The worker's run passed only because staging had `helper.py`; on
    the committed tree the command fails, so the vote stays unmet and the
    item is a real re-drive target."""
    unmet, routing = world["judge"]("python3 needs_helper.py")
    assert [u["item_index"] for u in unmet] == [0]
    (r,) = _runs(world["st"])
    assert r["verdict"] == "failed"
    assert not _human(world["st"])


@pytest.mark.parametrize("cmd,why", [
    ("python3 e2e.py && echo done", "not one plain command"),
    ("FOO=1 python3 e2e.py", "not one plain command"),
    ("python3 e2e.py", "forbids"),
])
def test_a_command_leerie_cannot_run_goes_to_a_human(leerie, world, cmd,
                                                     why):
    if why == "forbids":
        world["st"].data["process_constraints"] = {
            "forbidden_commands": ["python3 e2e.py"]}
    unmet, routing = world["judge"](cmd)
    assert unmet == []                       # never a re-drive
    assert routing["human_verification"] == [0]
    (e,) = _human(world["st"])
    assert e["item_index"] == 0 and cmd in e["detail"] and why in e["detail"]
    assert "Run it there and check" in e["detail"]
    sec = leerie._format_pr_end_state_section(world["st"].data)
    assert "Task requirements" in sec and cmd in sec


def test_the_unsafe_shapes_are_mechanical(leerie):
    v = {"pytest", "npm"}
    assert leerie._rerunnable_argv("pytest -q tests/test_a.py", v) == [
        "pytest", "-q", "tests/test_a.py"]
    assert leerie._rerunnable_argv("npm run 'e2e:ci'", v) == [
        "npm", "run", "e2e:ci"]
    for bad in ("pytest; rm -rf x", "pytest | tee o", "pytest > o",
                "pytest $(x)", "pytest tests/*.py", "cd a && pytest",
                "make docs", "pytest 'unterminated", "pytest\nrm x"):
        assert leerie._rerunnable_argv(bad, v) is None, bad
