"""One restore primitive (DESIGN §8 *One restore*; review round 15 M2).

"Back to the committed state" had three implementations of different
reach: one restored no write bits, none removed a nested repository (`git
clean -fd` skips them; `-ffd` does not), and none checked that I1's
checker then read nothing. So I1's designed exhaustion path — roll the
round back and record it — ended in `_assert_staging_clean`'s `die()`,
and the nested-repo case died again on every resume. `_restore_tree` and
`leerie_reset_worktree` are the one primitive; the drivers are the
reviewer's proofs, turned to assert the designed outcome."""
from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path

import pytest

from tests.conftest import REPO_ROOT
from tests.test_run_final_conformance import _clean_result, env  # noqa: F401


def _porcelain(tree) -> str:
    return subprocess.run(["git", "status", "--porcelain",
                           "--untracked-files=all"], cwd=str(tree),
                          capture_output=True, text=True).stdout


@pytest.mark.parametrize("shape", ["nested_repo", "readonly_dir"])
def test_an_unclean_final_conformer_is_rolled_back_not_died_on(
        env, monkeypatch, shape):  # noqa: F811
    leerie = env["leerie"]
    staging = env["staging"]

    async def fake(**kw):
        if kw["schema_key"] == "conformer" and not kw.get("resume_session"):
            cwd = Path(kw["cwd"])
            if shape == "nested_repo":
                subprocess.run(["git", "init", "-q", str(cwd / "upstream")],
                               check=True)
                (cwd / "upstream" / "lib.py").write_text("x = 1\n")
            else:
                d = cwd / "fixtures-ro"
                d.mkdir()
                (d / "snap.txt").write_text("x\n")
                d.chmod(0o555)
        return _clean_result()
    monkeypatch.setattr(leerie, "claude_p", fake)
    monkeypatch.setattr(leerie, "_log_session_id", lambda p: "sess-1")
    try:
        asyncio.run(leerie._run_final_conformance(
            env["run_dir"], env["st"], env["caps"], env["models"],
            env["efforts"]))
        assert "final conformer:round 0" in env["st"].data["unclean_trees"]
        assert _porcelain(staging) == ""
        assert leerie._decide_outcome(env["st"], any_commits=True) == \
            "unconverged"
    finally:
        ro = staging / "fixtures-ro"
        if ro.exists():
            os.chmod(ro, 0o755)


def test_a_nested_repo_does_not_survive_the_resume(leerie, tmp_path,
                                                   monkeypatch):
    def _git(cwd, *a):
        return subprocess.run(["git", *a], cwd=cwd, check=True,
                              capture_output=True, text=True).stdout.strip()
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "f.txt").write_text("base\n")
    (repo / ".gitignore").write_text(".leerie/\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    root = repo / ".leerie"
    monkeypatch.setenv("LEERIE_STATE_DIR", str(root))
    monkeypatch.chdir(repo)
    run_id = "20261030-nested"

    def setup():
        subprocess.run(["bash", str(REPO_ROOT / "scripts" / "setup-run.sh"),
                        run_id, "main"], cwd=repo, check=True,
                       capture_output=True)
    setup()
    staging = root / "runs" / run_id / "worktrees" / "staging"
    subprocess.run(["git", "init", "-q", str(staging / "upstream")],
                   check=True)
    (staging / "upstream" / "lib.py").write_text("x = 1\n")
    setup()                                       # the resume
    assert not (staging / "upstream").exists()
    assert asyncio.run(leerie._assert_staging_clean(staging, "x")) == _git(
        staging, "rev-parse", "HEAD^{tree}")


def test_the_i2_bracket_removes_a_nested_repo_its_command_made(
        leerie, tmp_path):
    repo = tmp_path / "r"
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    (repo / "a").write_text("a\n")
    subprocess.run(["git", "-C", str(repo), "add", "a"], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c",
                    "user.name=t", "commit", "-qm", "i"], check=True)

    async def go():
        async with leerie._leaves_tree_as_found(repo, "tests", "t") as rec:
            subprocess.run(["git", "init", "-q", str(repo / "fx-repo")],
                           check=True)
            (repo / "fx-repo" / "f").write_text("x\n")
        return rec
    rec = asyncio.run(go())
    assert rec["removed"] == ["fx-repo/"]
    assert not (repo / "fx-repo").exists() and _porcelain(repo) == ""


def test_what_the_restore_cannot_remove_stops_the_run_named(
        leerie, tmp_path, monkeypatch, capsys):
    """The postcondition is I1's own checker: dirt still read after a reset
    that landed stops the run there, with the lines."""
    repo = tmp_path / "r"
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c",
                    "user.name=t", "commit", "-q", "--allow-empty", "-m",
                    "i"], check=True)
    head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                          capture_output=True, text=True).stdout.strip()

    async def stuck(tree):
        return ["?? root-owned/x"]
    monkeypatch.setattr(leerie, "_tree_dirt", stuck)
    with pytest.raises(SystemExit):
        asyncio.run(leerie._restore_tree(repo, head, where="a rollback"))
    err = capsys.readouterr().err
    assert "a rollback:" in err and "?? root-owned/x" in err
