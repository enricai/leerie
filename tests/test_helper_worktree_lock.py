"""Helper worktrees follow the repository-wide worktree rules (DESIGN §6
*Never a repository-global git operation*; review r1 L3).

Subtask and planning worktrees were moved under the repository-wide lock
(`leerie_repo_lock`, `<git-common-dir>/leerie-worktree.lock`) and given
run-id-prefixed names, but the orchestrator's own helper worktrees —
`confirm-base`, `acceptance-eval`, `redrive-base`, the CI and integration
trees, the writer/falsifier checkouts' worktree fallback — still ran a bare
`git worktree add` under an unprefixed name: the add a concurrent prune of
the run could orphan, and an admin name every run shared.
"""
from __future__ import annotations

import ast
import fcntl
import inspect
import os
import subprocess
import threading
import time
import types
from pathlib import Path


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], check=True,
                          capture_output=True, text=True).stdout


def _st(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "a.txt").write_text("a\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    run_id = "20261028-abcdef-0123"
    run_dir = tmp_path / "state" / "runs" / run_id
    (run_dir / "worktrees").mkdir(parents=True)
    return types.SimpleNamespace(repo_root=repo, run_dir=run_dir,
                                 run_id=run_id)


def test_helper_paths_are_run_id_prefixed(leerie, tmp_path):
    st = _st(tmp_path)
    for name in ("confirm-base", "acceptance-eval", "redrive-base",
                 "ci-final", "integ-feat-001-ours", "falsifier-lane0"):
        p = leerie._helper_worktree_path(st, name)
        assert p.parent == st.run_dir / "worktrees"
        assert p.name == "20261028-abc-" + name


def test_the_add_waits_for_the_repository_lock(leerie, tmp_path):
    """Hold the lock the scripts take; the add must not happen until it is
    released, then succeed."""
    st = _st(tmp_path)
    common = Path(_git(st.repo_root, "rev-parse", "--path-format=absolute",
                       "--git-common-dir").strip())
    fd = os.open(str(common / "leerie-worktree.lock"),
                 os.O_WRONLY | os.O_CREAT, 0o644)
    fcntl.flock(fd, fcntl.LOCK_EX)
    wt = leerie._helper_worktree_path(st, "confirm-base")
    head = _git(st.repo_root, "rev-parse", "HEAD").strip()
    out: dict = {}
    t = threading.Thread(target=lambda: out.update(
        r=leerie._worktree_add_detached(st, wt, head)))
    t.start()
    time.sleep(1.0)
    assert t.is_alive() and not wt.exists(), "added while the lock was held"
    fcntl.flock(fd, fcntl.LOCK_UN)
    os.close(fd)
    t.join(30)
    assert out["r"].returncode == 0, out["r"].stderr
    assert (wt / "a.txt").is_file()
    listed = _git(st.repo_root, "worktree", "list", "--porcelain")
    assert f"worktree {wt.resolve()}" in listed


def test_every_worktree_add_goes_through_the_locked_helper(leerie):
    """Structural, paired with the behavioural test above: no function but
    `_worktree_add_detached` builds a `worktree add` argv."""
    tree = ast.parse(inspect.getsource(leerie))
    owners = set()
    for f in ast.walk(tree):
        if not isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for n in ast.walk(f):
            if isinstance(n, ast.List):
                words = [e.value for e in n.elts
                         if isinstance(e, ast.Constant)]
                if any(words[i:i + 2] == ["worktree", "add"]
                       for i in range(len(words))):
                    owners.add(f.name)
            elif isinstance(n, ast.Call) and any(
                    isinstance(a, ast.Constant) and a.value == "worktree"
                    for a in n.args) and any(
                    isinstance(a, ast.Constant) and a.value == "add"
                    for a in n.args):
                owners.add(f.name)
    assert owners == {"_worktree_add_detached"}, owners


def test_the_add_does_not_block_the_event_loop(leerie, tmp_path):
    """Review r2 L3: the add busy-waited with `time.sleep` for up to 120 s
    on the event loop, stalling every worker's watchdog and stream reader
    while another run's script held the lock. Awaited from a coroutine, it
    now waits off the loop: a ticker keeps running while the lock is held,
    and the add still waits for the lock."""
    import asyncio
    st = _st(tmp_path)
    common = Path(_git(st.repo_root, "rev-parse", "--path-format=absolute",
                       "--git-common-dir").strip())
    fd = os.open(str(common / "leerie-worktree.lock"),
                 os.O_WRONLY | os.O_CREAT, 0o644)
    fcntl.flock(fd, fcntl.LOCK_EX)
    wt = leerie._helper_worktree_path(st, "confirm-base")
    head = _git(st.repo_root, "rev-parse", "HEAD").strip()
    ticks: list[float] = []

    async def main():
        add = asyncio.ensure_future(
            leerie._worktree_add_detached_async(st, wt, head))
        for _ in range(10):
            await asyncio.sleep(0.05)
            ticks.append(time.monotonic())
        assert not add.done() and not wt.exists()
        fcntl.flock(fd, fcntl.LOCK_UN)
        return await asyncio.wait_for(add, 30)
    try:
        r = asyncio.run(main())
    finally:
        os.close(fd)
    assert len(ticks) == 10 and ticks[-1] - ticks[0] < 2.0
    assert r.returncode == 0, r.stderr
    assert (wt / "a.txt").is_file()


def _blocking_add_callers(tree: ast.Module) -> list[tuple[int, str, str]]:
    """Direct calls, from an `async def`, to `_worktree_add_detached` or to
    any sync function that reaches it — each blocks the event loop for the
    lock wait and the checkout."""
    fns = {n.name: n for n in ast.walk(tree)
           if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}

    def callees(fn: ast.AST) -> set[str]:
        return {c.func.id for c in ast.walk(fn) if isinstance(c, ast.Call)
                and isinstance(c.func, ast.Name)}
    blocking = {"_worktree_add_detached"}
    grew = True
    while grew:
        grew = False
        for name, fn in fns.items():
            if (isinstance(fn, ast.FunctionDef) and name not in blocking
                    and callees(fn) & blocking):
                blocking.add(name)
                grew = True
    bad = []
    for name, fn in fns.items():
        if not isinstance(fn, ast.AsyncFunctionDef):
            continue
        for c in ast.walk(fn):
            if (isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
                    and c.func.id in blocking):
                bad.append((c.lineno, name, c.func.id))
    return bad


def test_no_coroutine_calls_the_blocking_add(leerie):
    """Structural, paired with the test above: every coroutine reaches the
    add through `asyncio.to_thread` (`_worktree_add_detached_async`, or a
    `to_thread` of a sync checkout helper that calls it)."""
    assert _blocking_add_callers(ast.parse(inspect.getsource(leerie))) == []


def test_the_blocking_caller_scan_sees_a_transitive_call():
    src = ("def _worktree_add_detached(st, wt, rev):\n    pass\n"
           "def helper(st):\n    return _worktree_add_detached(st, 1, 2)\n"
           "async def bad(st):\n    helper(st)\n"
           "async def good(st):\n"
           "    await asyncio.to_thread(helper, st)\n")
    assert [b[1:] for b in _blocking_add_callers(ast.parse(src))] == [
        ("bad", "helper")]
