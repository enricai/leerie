"""A merge a hard kill left in progress is abandoned, not handed to the next
sid's integrator (DESIGN §6 *Skipping a settled subtask never skips its
integration*; review r2 L4).

Driven end to end on the real `setup-run.sh` / `new-worktree.sh` /
`integrate.sh` and the real `integrate_wave`. The first invocation's
integrator for `b` is killed mid-resolution, so staging stays mid-merge
of `b`. On resume, sibling `a`, blocked before the kill and complete after
its retry, comes first in wave order. The integrator stub resolves
whatever merge is in progress and commits it. Before the fix, `a`'s
integrator committed `b`'s half-resolved merge as `leerie: integrate a`,
and `a`'s branch was never merged, though both counted integrated."""
from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

from tests.conftest import REPO_ROOT

RUN_ID = "20261028-l4-midmerge"


def _git(cwd: Path, *args: str, check: bool = True) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=check,
                          capture_output=True, text=True).stdout.strip()


def _branch(repo: Path, sid: str, fname: str, text: str) -> None:
    out = subprocess.run(
        ["bash", str(REPO_ROOT / "scripts" / "new-worktree.sh"), sid, RUN_ID],
        cwd=repo, capture_output=True, text=True, check=True)
    wt = Path(out.stdout.strip().splitlines()[-1])
    (wt / fname).write_text(text)
    _git(wt, "add", "-A")
    _git(wt, "commit", "-q", "-m", f"{sid} work")


def _midmerge(leerie, tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "f.txt").write_text("base\n")
    (repo / ".gitignore").write_text(".leerie/\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    root = repo / ".leerie"
    monkeypatch.setenv("LEERIE_STATE_DIR", str(root))
    monkeypatch.chdir(repo)
    subprocess.run(["bash", str(REPO_ROOT / "scripts" / "setup-run.sh"),
                    RUN_ID, "main"], cwd=repo, check=True,
                   capture_output=True)
    st = leerie.State(root, RUN_ID)
    st.repo_root = repo
    st.data = {"task": "t", "waves": [["a", "b"]], "completed_waves": 0,
               "subtask_status": {"a": "complete", "b": "complete"},
               "working_branch": "main"}
    st.save()
    staging = st.run_dir / "worktrees" / "staging"
    # b conflicts with the run branch (a commit integrated earlier).
    _branch(repo, "b", "f.txt", "b\n")
    _branch(repo, "a", "a.txt", "a\n")
    (staging / "f.txt").write_text("earlier\n")
    _git(staging, "commit", "-qam", "earlier integration")
    # Invocation 1: integrate.sh b conflicts; the integrator is hard-killed
    # mid-resolution — staging stays mid-merge.
    r = subprocess.run(["bash", str(REPO_ROOT / "scripts" / "integrate.sh"),
                        "b", RUN_ID], cwd=repo, capture_output=True, text=True)
    assert r.returncode == 1
    (staging / "f.txt").write_text("half-resolved\n")
    assert _git(staging, "rev-parse", "-q", "--verify", "MERGE_HEAD",
                check=False)

    return repo, st, staging


def test_resume_abandons_the_interrupted_merge(leerie, tmp_path, monkeypatch):
    repo, st, staging = _midmerge(leerie, tmp_path, monkeypatch)
    calls: list[str] = []

    async def claude_p(**kw):
        calls.append(kw["sid"])
        # Resolve whatever is in progress and commit it.
        _git(staging, "add", "-A")
        _git(staging, "commit", "-q", "--no-edit")
        return {"status": "resolved", "summary": "ok"}

    async def gate(*a, **k):
        return True
    monkeypatch.setattr(leerie, "claude_p", claude_p)
    monkeypatch.setattr(leerie, "_run_integration_judge_gate", gate)
    caps = dict(leerie.DEFAULT_CAPS)
    out = asyncio.run(leerie.integrate_wave(
        ["a", "b"], {"a": {"status": "complete"}, "b": {"status": "complete"}},
        st.run_dir, caps, st, {"integrator": "sonnet"},
        {"integrator": None}))

    def merged(sid):
        return subprocess.run(
            ["git", "merge-base", "--is-ancestor",
             f"leerie/subtasks/{RUN_ID}/{sid}", "HEAD"],
            cwd=staging).returncode == 0
    log = _git(staging, "log", "--format=%s", "-4")
    assert out == ["a", "b"]
    assert merged("a") and merged("b"), "a sibling's work was lost"
    # Only b conflicts, so only b's integrator runs, and each merge carries
    # its own sid.
    assert calls == ["integrator-b"], calls
    assert log.splitlines()[:2] == ["leerie: integrate b",
                                    "leerie: integrate a"], log
    # The killed integrator's half-resolution is kept on a rescue ref.
    shown = _git(staging, "show", f"refs/leerie/rescue/{RUN_ID}/b:f.txt",
                 check=False)
    assert shown == "half-resolved"


def test_integrate_sh_refuses_a_mid_merge_staging(leerie, tmp_path,
                                                  monkeypatch):
    """The backstop: the script never reads another sid's interrupted
    merge as this sid's conflict."""
    repo, st, staging = _midmerge(leerie, tmp_path, monkeypatch)
    r = subprocess.run(["bash", str(REPO_ROOT / "scripts" / "integrate.sh"),
                        "a", RUN_ID], cwd=repo, capture_output=True, text=True)
    assert r.returncode == 2 and "mid-merge" in r.stderr, r.stderr


def test_a_clean_staging_is_left_alone(leerie, tmp_path, monkeypatch):
    repo, st, staging = _midmerge(leerie, tmp_path, monkeypatch)
    _git(staging, "merge", "--abort")
    before = _git(staging, "rev-parse", "HEAD")
    assert asyncio.run(leerie._abandon_interrupted_merge(staging, st)) is None
    assert _git(staging, "rev-parse", "HEAD") == before
