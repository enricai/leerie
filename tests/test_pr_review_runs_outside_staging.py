"""A worker that does not commit never runs in a tree leerie measures
(DESIGN §8 *A worker that does not commit never runs in a tree leerie
measures*).

Review round 15 M1 (`test_zz_r15e_pr_review_leftover_dies.py`): the
advisory pr_reviewer ran the repo's commands in staging and then undid
them with a restore of its own that unlinked only regular files. A file in
a directory a test made read-only, a dangling symlink and a nested fixture
repository survived it, and the final conformance then `die()`d with
"leerie's own invariant is broken" before the conformer ran. The
reviewer — and the other non-committing workers that ran in staging,
`ci_checks` and `integration_judge` — now read and run in their own
disposable checkout (`_judge_checkout`), removed with whatever is in it.

Each test drives the real function, its worker stubbed to leave the shape
behind in its cwd. The M1 shapes are the reviewer's driver with its
verdict inverted: the final conformance now runs its conformer.
"""
from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path

import pytest

from tests.test_finalize_evidence import (EFFORTS, MODELS, _review_env,
                                          _stub_reviewer)
from tests.test_run_final_conformance import env  # noqa: F401


def _porcelain(tree: Path) -> str:
    return subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=tree, capture_output=True, text=True, check=True).stdout


def _readonly_dir(cwd: Path) -> None:
    d = cwd / "tmp-perm"
    d.mkdir()
    (d / "out.txt").write_text("x\n")
    d.chmod(0o555)


def _dangling_symlink(cwd: Path) -> None:
    os.symlink("/nonexistent-target", cwd / "latest-report")


def _nested_repo(cwd: Path) -> None:
    d = cwd / "fixture-repo"
    subprocess.run(["git", "init", "-q", str(d)], check=True)
    (d / "f").write_text("x\n")


@pytest.mark.parametrize("mutate", [_readonly_dir, _dangling_symlink,
                                    _nested_repo])
def test_what_the_reviewer_leaves_never_stops_the_run(
        env, monkeypatch, mutate):  # noqa: F811
    leerie = env["leerie"]
    _review_env(env)
    seen: list[Path] = []

    def record(cwd: Path) -> None:
        seen.append(cwd)
        mutate(cwd)
    calls = _stub_reviewer(leerie, monkeypatch, [], mutate=record)
    rec = asyncio.run(leerie._run_pr_review(
        env["run_dir"], env["st"], env["caps"], MODELS, EFFORTS))
    assert "error" not in rec, rec
    # It ran in its own checkout, which is gone with the shape in it.
    assert seen and seen[0] != env["staging"] and not seen[0].exists()
    assert _porcelain(env["staging"]) == ""
    died: list[str] = []

    def fake_die(msg, code=1):
        died.append(msg)
        raise SystemExit(code)
    monkeypatch.setattr(leerie, "die", fake_die)
    asyncio.run(leerie._run_final_conformance(
        env["run_dir"], env["st"], env["caps"], env["models"],
        env["efforts"]))
    assert died == []
    assert [c for c in calls if c["schema_key"] == "conformer"]


def test_the_reviewer_runs_with_stagings_installed_dependencies(
        env, monkeypatch):  # noqa: F811
    """It runs the repo's build and test verbs, so its checkout shares
    staging's ignored paths (symlinked, nothing installed)."""
    leerie = env["leerie"]
    _review_env(env)
    st_ = env["staging"]
    (st_ / ".gitignore").write_text("node_modules/\n")
    subprocess.run(["git", "add", ".gitignore"], cwd=st_, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "ignore"], cwd=st_, check=True)
    (st_ / "node_modules" / "dep").mkdir(parents=True)
    (st_ / "node_modules" / "dep" / "index.js").write_text("ok\n")
    got: list[str] = []

    def read_dep(cwd: Path) -> None:
        got.append((cwd / "node_modules" / "dep" / "index.js").read_text())
    _stub_reviewer(leerie, monkeypatch, [], mutate=read_dep)
    asyncio.run(leerie._run_pr_review(
        env["run_dir"], env["st"], env["caps"], MODELS, EFFORTS))
    assert got == ["ok\n"]
    # The share is a link: removing the checkout left staging's install.
    assert (st_ / "node_modules" / "dep" / "index.js").read_text() == "ok\n"
    assert _porcelain(st_) == ""


def _stub_cwd(leerie, monkeypatch, result: dict) -> list[Path]:
    seen: list[Path] = []

    async def fake(**kw):
        cwd = Path(kw["cwd"])
        seen.append(cwd)
        _readonly_dir(cwd)
        _nested_repo(cwd)
        return result
    monkeypatch.setattr(leerie, "claude_p", fake)
    return seen


def test_ci_checks_runs_outside_staging(env, monkeypatch):  # noqa: F811
    leerie = env["leerie"]
    st_ = env["staging"]
    (st_ / ".github" / "workflows").mkdir(parents=True)
    (st_ / ".github" / "workflows" / "ci.yml").write_text("on: [push]\n")
    subprocess.run(["git", "add", "-A"], cwd=st_, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "ci"], cwd=st_, check=True)
    seen = _stub_cwd(leerie, monkeypatch, {"checks": [], "excluded": []})
    out = asyncio.run(leerie._ci_discover(
        st_, env["st"], env["caps"], env["models"], env["efforts"]))
    assert "skipped" not in out, out
    assert seen and seen[0] != st_ and not seen[0].exists()
    assert _porcelain(st_) == ""


def test_integration_judge_runs_outside_staging(env, monkeypatch):  # noqa: F811
    leerie = env["leerie"]
    st_ = env["staging"]
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=st_,
                          capture_output=True, text=True,
                          check=True).stdout.strip()
    seen = _stub_cwd(leerie, monkeypatch, {
        "merge_reviewed": True, "defects": [], "rationale": "clean"})
    out = asyncio.run(leerie._integration_judge_verdict(
        "feat-001", st_, head, {"intent": "x"}, [], env["caps"], env["st"],
        env["models"], env["efforts"]))
    assert out == ([], [])
    # It judged exactly the merge commit, in a checkout of it.
    assert seen and seen[0] != st_ and not seen[0].exists()
    assert _porcelain(st_) == ""
