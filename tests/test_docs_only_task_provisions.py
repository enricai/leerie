"""A docs-only task is provisioned like any other (DESIGN §6½: the phase runs
for every task, whatever classification returned; *Who runs that install*).

The recipe phase 1½ detects is what every tree leerie measures installs from
(`_ensure_worktree_deps`), not only a hint to workers. A docs-only
short-circuit in `phase_provision` once recorded `kind: none` for any task
classified `documentation` alone, so nothing was ever installed in a
measured tree: proof run 9aa2506d (navegando, 2026-11-09) logged "docs-only
task: skipping dep detection", then `vitest: command not found` on the base,
every CI parity check red at the baseline ("gate none") and the browser
suite at rc 127.

Replayed here on the real path: a repository whose merge check runs a runner
that only its install provides (`npm ci` of a local package, so offline), a
task classified `documentation` alone, the real `phase_provision`, then the
real CI parity baseline (`_capture_ci_parity_baseline`, its real
`_ensure_worktree_deps` and real confined child processes). Stubbed: the
`ci_checks` worker (`claude_p`), the cgroup broker and the host-pressure
gate, as `tests/test_ci_parity.py` stubs them.
"""
from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from tests.conftest import _run, relocate_into_run_repo

pytestmark = pytest.mark.skipif(shutil.which("npm") is None,
                                reason="needs npm")

WORKFLOW = """\
name: ci
on:
  pull_request:
jobs:
  checks:
    runs-on: ubuntu-latest
    steps:
      - run: npm ci
      - run: npm run check
"""

PACKAGE = {"name": "app", "version": "1.0.0", "private": True,
           "scripts": {"check": "vitest"},
           "devDependencies": {"vitest": "file:tools/vitest"}}
# What `npm install --package-lock-only` writes for PACKAGE: a link to the
# local package, so `npm ci` needs no registry.
LOCK = {"name": "app", "version": "1.0.0", "lockfileVersion": 3,
        "requires": True,
        "packages": {
            "": {"name": "app", "version": "1.0.0",
                 "devDependencies": {"vitest": "file:tools/vitest"}},
            "node_modules/vitest": {"resolved": "tools/vitest",
                                    "link": True},
            "tools/vitest": {"version": "0.0.0", "dev": True,
                             "bin": {"vitest": "bin.sh"}}}}


def _sh(argv, cwd):
    subprocess.run(argv, cwd=str(cwd), capture_output=True, check=True)


@pytest.fixture
def env(leerie, tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    _sh(["git", "init", "-q", "-b", "main"], cwd=repo)
    _sh(["git", "config", "user.email", "t@t"], cwd=repo)
    _sh(["git", "config", "user.name", "t"], cwd=repo)
    (repo / ".github" / "workflows").mkdir(parents=True)
    (repo / ".github" / "workflows" / "ci.yml").write_text(WORKFLOW)
    (repo / "package.json").write_text(json.dumps(PACKAGE, indent=2) + "\n")
    (repo / "package-lock.json").write_text(json.dumps(LOCK, indent=2)
                                            + "\n")
    tool = repo / "tools" / "vitest"
    tool.mkdir(parents=True)
    (tool / "package.json").write_text(json.dumps(
        {"name": "vitest", "version": "0.0.0",
         "bin": {"vitest": "bin.sh"}}) + "\n")
    (tool / "bin.sh").write_text("#!/bin/sh\necho vitest ran\n")
    (tool / "bin.sh").chmod(0o755)
    (repo / "README.md").write_text("# app\n")
    (repo / ".gitignore").write_text(".leerie/\nnode_modules/\n")
    _sh(["git", "add", "-A"], cwd=repo)
    _sh(["git", "commit", "-q", "-m", "base"], cwd=repo)
    run_id = "docs-001-abcdef"
    leerie_root = repo / ".leerie"
    run_dir = leerie_root / "runs" / run_id
    (run_dir / "logs").mkdir(parents=True)
    staging = run_dir / "worktrees" / "staging"
    _sh(["git", "worktree", "add", "-q", "-b", f"leerie/runs/{run_id}",
         str(staging), "main"], cwd=repo)
    st = leerie.State(leerie_root, run_id)
    st.data = {"task": "Document the e2e setup in the README",
               "categories": ["documentation"]}
    st.repo_root = repo
    st.save()
    relocate_into_run_repo(leerie, st)
    # phase_provision's checkout is made from the planning tree's commit;
    # staging is at the same base commit.
    st.data["planning_worktree"] = str(staging)
    caps = dict(leerie.DEFAULT_CAPS)
    caps.update({"worker_timeout_sec": 120})
    monkeypatch.setattr(leerie, "_cgroup_create", lambda *a, **k: None)

    # `mise install` runs in every provisioned tree (DESIGN §6½ *A
    # repository's runtimes are the ones mise discovers*); this test's
    # subject is the recipe, and the host has no mise.
    async def _mise(tree, log_dir, st_):
        st_.data.setdefault("provision", {})["mise_versions"] = {}
    monkeypatch.setattr(leerie, "_run_mise_install", _mise)

    async def _gate(caps, **_k):
        return {"waited_sec": 0.0, "timed_out": False, "psi": 1.0,
                "readable": True}
    monkeypatch.setattr(leerie, "_confirm_gate", _gate)
    calls: list[dict] = []
    check = {"id": "check-vitest", "kind": "lint",
             "argv": ["npm", "run", "check"], "cwd": ".", "env": [],
             "prerequisites": [], "trigger": ["pull_request"],
             "path_filters": [], "requires": [], "strict_argv": [],
             "source": {"file": ".github/workflows/ci.yml",
                        "job": "checks", "step": "check"},
             "coverage": {"summary_path": "", "config_file": "",
                          "thresholds": []},
             "notes": ""}

    async def fake_claude_p(**kw):
        calls.append(kw)
        assert kw["schema_key"] == "ci_checks", kw["schema_key"]
        return {"checks": [check], "excluded": []}
    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)

    async def _no_blt(*a, **k):
        return None
    # The build/lint/test discovery is its own worker (tests/
    # test_blt_discovery.py), not the subject here.
    monkeypatch.setattr(leerie, "_discover_blt", _no_blt)
    return {"repo": repo, "run_dir": run_dir, "staging": staging, "st": st,
            "caps": caps, "calls": calls}


def test_a_docs_only_task_gets_the_install_recipe(leerie, env):
    """The phase runs the lockfile table for a `documentation`-only task
    and records the install every measurement runs."""
    st = env["st"]
    _run(leerie.phase_provision(env["repo"], st, env["caps"], {}, {}))
    prov = st.data["provision"]
    assert prov["source"] == "table"
    assert [(e["kind"], e["command"]) for e in prov["recipe"]] == [
        ("install", ["npm", "ci"])]


def test_9aa2506d_a_docs_only_tasks_ci_baseline_finds_its_runner(
        leerie, env):
    """The 9aa2506d shape end to end: a docs-only task's CI parity baseline
    runs a check whose runner only the install provides, and it is green
    there — so it gates — instead of `command not found` (rc 127) and
    "red at baseline"."""
    st = env["st"]
    _run(leerie.phase_provision(env["repo"], st, env["caps"], {}, {}))
    _run(leerie._capture_ci_parity_baseline(
        env["run_dir"], st, env["caps"], {"ci_checks": "sonnet"},
        {"ci_checks": "medium"}))
    assert [c["schema_key"] for c in env["calls"]] == ["ci_checks"]
    recs = {c["id"]: c for c in st.data["ci_parity"]["checks"]}
    rec = recs["check-vitest"]
    assert rec["selected"], rec
    assert rec["base"]["rc"] == 0, rec
    assert rec.get("gate") == "plain", rec
    assert rec.get("reason") != "red_at_baseline", rec
