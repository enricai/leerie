"""Every test outcome the PR states was measured on the tree that ships
(DESIGN §9 final-tree pass; lane FC, barnacle 39c4c5ad).

Replay of 39c4c5ad's shape on real git: the final conformer commits, its
post-round measurement runs the repository's real test script on that
commit and records the failure (`conformance._final`, stamped `tree_sha`);
a later step — there, held-out repair round 1 and two re-drive groups —
commits a fix on staging, so the run branch ships another tree on which
the test passes. The PR said the test failed. `_compose_pr_via_llm`'s
payload must not carry the stale record; it still carries it when nothing
moved the branch after the pass (the control).

Stubbed: `claude_p` (both workers) and `_measure_axes`, whose stub runs the
tree's own `check.sh` with an absolute `/bin/sh` and reports what it did,
so every outcome here is of a real commit.
"""
from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

import pytest

from tests.conftest import relocate_into_run_repo
from tests.conftest import run_git_cwd_first_stdout as _git

FAILING_TEST = "src/scripts/recon-generate-shape-verdict-base-headers.test.ts"


@pytest.fixture(autouse=True)
def _restore(leerie):
    saved = (leerie.claude_p, leerie._measure_axes)
    yield
    leerie.claude_p, leerie._measure_axes = saved


@pytest.fixture
def env(leerie, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    # The repository's test command: passes while `verdict.txt` says ok.
    (repo / "check.sh").write_text(
        'read v < verdict.txt\n[ "$v" = ok ] || { echo "312 passed (313)"; '
        f'echo "FAIL {FAILING_TEST}"; exit 1; }}\necho "313 passed (313)"\n')
    (repo / "verdict.txt").write_text("ok\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    run_id = "39c4c5ad4740"
    branch = f"leerie/runs/{run_id}"
    _git(repo, "checkout", "-q", "-b", branch)
    (repo / "impl.txt").write_text("integrated subtasks\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "wave-1: integrate")
    _git(repo, "checkout", "-q", "main")
    leerie_root = repo / ".leerie"
    run_dir = leerie_root / "runs" / run_id
    for d in ("subtasks", "logs", "worktrees"):
        (run_dir / d).mkdir(parents=True, exist_ok=True)
    staging = run_dir / "worktrees" / "staging"
    _git(repo, "worktree", "add", "-q", str(staging), branch)
    st = leerie.State(leerie_root, run_id)
    st.data = {"task": "x", "answers": {"source_of_truth": "codebase"},
               "working_branch": "main"}
    st.save()
    relocate_into_run_repo(leerie, st)
    return {"leerie": leerie, "st": st, "run_dir": run_dir,
            "staging": staging,
            "caps": dict(leerie.DEFAULT_CAPS),
            "models": {w: "sonnet" for w in leerie.WORKER_TYPES},
            "efforts": {w: None for w in leerie.WORKER_TYPES}}


def _real_tests_axis(leerie):
    """`_measure_axes` reporting the tree's own `check.sh`, run for real."""
    async def _measure(tree, axes_map, st, caps, **kw):
        r = subprocess.run(["/bin/sh", "check.sh"], cwd=tree,
                           capture_output=True, text=True)
        return {"tests": {"ran": True, "measured": True,
                          "passed": r.returncode == 0,
                          "command": "sh check.sh",
                          "summary": r.stdout.strip()}}
    leerie._measure_axes = _measure


def _conformer_result():
    return {
        "subtask_id": "whole-tree", "rules_files_read": [],
        "rule_violations_fixed": [], "rule_violations_residual": [],
        "docs_updates": [], "tests_updates": [],
        "build": {"ran": False, "passed": False, "command": "",
                  "summary": ""},
        "lint": {"ran": False, "passed": False, "command": "",
                 "summary": ""},
        "tests": {"ran": False, "passed": False, "command": "",
                  "summary": ""},
        "summary": "kept replay success as transport verdict",
        "production_evidence": {"exercised": True, "how": "sh check.sh",
                                "observed": "ran"},
    }


def _run_final_conformer(env) -> None:
    """The real final pass; its one round commits the change that makes
    the repository's test fail (39c4c5ad's `b0ef30f`)."""
    c = env["leerie"]
    calls = {"n": 0}

    async def _conformer(**kw):
        calls["n"] += 1
        if calls["n"] == 1:
            tree = Path(kw["cwd"])
            (tree / "verdict.txt").write_text("broken\n")
            _git(tree, "add", "-A")
            _git(tree, "commit", "-q", "-m", "conformer: shape verdict")
        return _conformer_result()

    c.claude_p = _conformer
    _real_tests_axis(c)
    env["caps"]["conformance_rounds"] = 1
    asyncio.run(c._run_final_conformance(
        env["run_dir"], env["st"], env["caps"], env["models"],
        env["efforts"]))


def _pr_payload(env) -> dict:
    """What `_compose_pr_via_llm` hands the pr_writer worker."""
    c = env["leerie"]
    seen: list[dict] = []

    async def _writer(**kw):
        seen.append(json.loads(kw["user_prompt"]))
        return {"title": "t", "body": "b"}

    c.claude_p = _writer
    asyncio.run(c._compose_pr_via_llm(env["st"], env["caps"], {}, {}, None))
    assert len(seen) == 1, "the pr_writer worker was not run"
    return seen[0]


def test_the_failure_measured_on_the_conformers_tree_is_recorded(env):
    """Precondition of the replay: the pass recorded a real test failure,
    stamped with the tree it ran on."""
    _run_final_conformer(env)
    block = env["st"].data["conformance"]["_final"]
    assert block["result"]["tests"]["passed"] is False
    assert any(FAILING_TEST in w for w in block["warnings"]), block
    assert block["tree_sha"] == _git(env["staging"], "rev-parse",
                                     "HEAD^{tree}")


def test_a_failure_of_a_tree_that_no_longer_ships_is_not_stated(env, capfd):
    """39c4c5ad: a later commit fixed the test; the PR must not say it
    fails."""
    _run_final_conformer(env)
    measured = env["st"].data["conformance"]["_final"]["tree_sha"]
    staging = env["staging"]
    # Held-out repair round 1 / a re-drive group commits after the pass.
    (staging / "verdict.txt").write_text("ok\n")
    _git(staging, "add", "-A")
    _git(staging, "commit", "-q", "-m", "conformer: restore the verdict")
    shipping = env["leerie"]._shipping_tree(env["st"])
    assert shipping and shipping != measured
    # On the tree that ships the test the record names passes.
    assert subprocess.run(["/bin/sh", "check.sh"], cwd=staging,
                          capture_output=True).returncode == 0

    payload = _pr_payload(env)
    assert "final_conformance" not in payload, payload["final_conformance"]
    assert FAILING_TEST not in json.dumps(payload)
    out = capfd.readouterr().out
    assert (f"final conformance notes left out — measured on tree "
            f"{measured[:12]}, the run ships {shipping[:12]}") in out


def test_a_failure_of_the_tree_that_ships_is_stated(env):
    """Control: nothing committed after the pass, so its failure is of the
    tree that ships and reaches the PR writer whole."""
    _run_final_conformer(env)
    payload = _pr_payload(env)
    fc = payload["final_conformance"]
    assert [a["axis"] for a in fc["failed_axes"]] == ["tests"]
    assert "312 passed (313)" in fc["failed_axes"][0]["summary"]
    assert any(FAILING_TEST in w for w in fc["warnings"])


def test_a_record_with_no_tree_is_not_stated(env):
    """A record that names no tree is not shown to be of the shipping one."""
    _run_final_conformer(env)
    env["st"].data["conformance"]["_final"].pop("tree_sha")
    env["st"].save()
    assert "final_conformance" not in _pr_payload(env)
