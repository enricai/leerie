"""`_compose_pr_via_llm`'s nested `_git` helper (leerie.py:32170-32187) must
not hang phase_finalize forever on a stalled git process (e.g. lock
contention on the shared bind-mounted .git across concurrent worktree
operations). It wraps `proc.communicate()` in `asyncio.wait_for` bounded by
`PR_WRITER_GIT_TIMEOUT_SEC` and swallows the timeout via the function's
existing fail-open `except Exception` contract.
"""
from __future__ import annotations

import asyncio
import json
import subprocess

import pytest


def _run(cmd, cwd, check=True):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if check:
        assert r.returncode == 0, f"{cmd} failed in {cwd}: {r.stderr}"
    return r


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    _run(["git", "init", "-q", "-b", "main"], root)
    _run(["git", "config", "user.email", "t@t"], root)
    _run(["git", "config", "user.name", "t"], root)
    (root / "README.md").write_text("hi\n")
    _run(["git", "add", "-A"], root)
    _run(["git", "commit", "-qm", "init"], root)
    return root


@pytest.fixture
def st(leerie, repo, tmp_path):
    leerie_root = tmp_path / ".leerie"
    (leerie_root / "runs" / "r1").mkdir(parents=True)
    s = leerie.State(leerie_root, "r1", repo_root=repo)
    yield s
    s.release_lock()


def _caps(leerie):
    caps = dict(leerie.DEFAULT_CAPS)
    caps["max_total_workers"] = 100
    return caps


class _HangingProc:
    """Stand-in for `asyncio.subprocess.Process` whose `communicate()`
    never resolves, mimicking a git process stalled on lock contention."""

    returncode = None

    async def communicate(self):
        await asyncio.Event().wait()  # never set: blocks forever

    async def wait(self):
        await asyncio.Event().wait()

    def kill(self):
        self.returncode = -9

    def terminate(self):
        self.returncode = -15

    @property
    def pid(self):
        return 999999


def test_git_helper_bounded_by_timeout_not_unbounded_hang(
        leerie, monkeypatch, st, repo):
    st.data["working_branch"] = "main"
    monkeypatch.setattr(leerie, "PR_WRITER_GIT_TIMEOUT_SEC", 0.05)

    async def _fake_create_subprocess_exec(*args, **kwargs):
        return _HangingProc()

    monkeypatch.setattr(
        asyncio, "create_subprocess_exec", _fake_create_subprocess_exec)

    terminated = []

    async def _fake_terminate_proc_tree(proc):
        terminated.append(proc)

    monkeypatch.setattr(
        leerie, "_terminate_proc_tree", _fake_terminate_proc_tree)

    async def _fake_claude_p(**kwargs):
        raise AssertionError(
            "claude_p must not be reached if git context collection hangs")

    monkeypatch.setattr(leerie, "claude_p", _fake_claude_p)

    async def _bounded():
        return await asyncio.wait_for(
            leerie._compose_pr_via_llm(
                st, _caps(leerie), {}, {}, repo, None),
            timeout=5.0)

    # The whole call must return well within the outer 5s ceiling instead of
    # hanging forever — proves the fix, not just that a timeout is *possible*.
    asyncio.run(_bounded())

    # Fail-open: no PR title/body written, so the launcher's bash fallback
    # takes over exactly as it does for any other _compose_pr_via_llm error.
    assert not (st.run_dir / "run.json").exists() or \
        "pr_title" not in json.loads(
            (st.run_dir / "run.json").read_text() or "{}")
    # The stalled git subprocess must be reaped, not orphaned.
    assert len(terminated) >= 1
