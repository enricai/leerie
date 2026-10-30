"""N7b: preflight reaps idle runs' worktrees (DESIGN §6 *Idle runs'
worktrees are reaped at preflight*).

Real directories, real git registrations, a real flock held by "live" runs.
Only `<run>/worktrees/` may go; a run that is this one, live, or recently
touched keeps everything.
"""
from __future__ import annotations

import asyncio
import fcntl
import os
import time
from pathlib import Path

import pytest

from tests.conftest import run_git_cwd_kw as _git

OLD = time.time() - 30 * 86400


def _age(path: Path) -> None:
    """Back-date `path` and everything under it."""
    for dirpath, dirnames, filenames in os.walk(path, topdown=False):
        for n in filenames + dirnames:
            os.utime(os.path.join(dirpath, n), (OLD, OLD),
                     follow_symlinks=False)
    os.utime(path, (OLD, OLD))


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git("init", "-q", "-b", "main", cwd=repo)
    for k, v in (("user.email", "t@leerie.local"), ("user.name", "t"),
                 ("commit.gpgsign", "false")):
        _git("config", k, v, cwd=repo)
    (repo / "f.txt").write_text("x\n")
    _git("add", "-A", cwd=repo)
    _git("commit", "-q", "-m", "init", cwd=repo)
    return repo


def _run(runs: Path, repo: Path, rid: str, *, old: bool) -> Path:
    d = runs / rid
    (d / "logs").mkdir(parents=True)
    (d / "state.json").write_text("{}")
    (d / "logs" / "w.log").write_text("log\n")
    # The run's own repository, where its worktrees are registered (DESIGN
    # §6 *The run's own repository*): the reap prunes that registry, never
    # the user's (§6 *The explicit channel*).
    _git("clone", "-q", "--bare", str(repo), str(d / "repo"), cwd=repo)
    wt = d / "worktrees" / f"{rid}-feat-001"
    wt.parent.mkdir()
    _git("worktree", "add", "-q", "-b", f"leerie/subtasks/{rid}/feat-001",
         str(wt), "main", cwd=d / "repo")
    (wt / "node_modules").mkdir()
    (wt / "node_modules" / "big.js").write_text("y" * 10000)
    if old:
        _age(d)
    return d


@pytest.fixture()
def env(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    runs = tmp_path / "state" / "runs"
    runs.mkdir(parents=True)
    monkeypatch.chdir(repo)
    return repo, runs


def _snapshot_outside_worktrees(d: Path) -> dict:
    out = {}
    for dirpath, dirnames, filenames in os.walk(d):
        dirnames[:] = [n for n in dirnames if n != "worktrees"
                       and not n.startswith(".worktrees.reap-")]
        for f in filenames:
            p = Path(dirpath, f)
            rel = str(p.relative_to(d))
            # The prune's own lock in the run's repository is housekeeping.
            if rel != "repo/leerie-worktree.lock":
                out[rel] = p.read_bytes()
    return out


def test_idle_dead_runs_lose_worktrees_only(leerie, env):
    repo, runs = env
    idle = _run(runs, repo, "idle1", old=True)
    before = _snapshot_outside_worktrees(idle)
    rep = leerie._reap_idle_worktrees(runs, "self")
    assert rep["reaped"] == ["idle1"]
    assert not (idle / "worktrees").exists()
    assert not list(idle.glob(".worktrees.reap-*"))
    assert _snapshot_outside_worktrees(idle) == before
    assert rep["bytes"] >= 10000
    assert rep["errors"] == 0
    # Its registration in the run's repository went with it; its branch
    # stays.
    assert "idle1-feat-001" not in os.listdir(idle / "repo" / "worktrees")
    assert _git("show-ref", "--verify", "-q",
                "refs/heads/leerie/subtasks/idle1/feat-001",
                cwd=idle / "repo").returncode == 0


def test_live_recent_and_self_runs_are_untouched(leerie, env):
    repo, runs = env
    live = _run(runs, repo, "live1", old=True)
    recent = _run(runs, repo, "recent1", old=False)
    me = _run(runs, repo, "self", old=True)
    fd = os.open(str(live), os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)     # what State.__init__ holds
        rep = leerie._reap_idle_worktrees(runs, "self")
    finally:
        os.close(fd)
    assert rep["reaped"] == []
    assert rep["skipped_live"] == ["live1"]
    assert rep["skipped_recent"] == ["recent1"]
    for d in (live, recent, me):
        assert (d / "worktrees" / f"{d.name}-feat-001" / "f.txt").exists()


def test_a_recent_log_keeps_an_old_run(leerie, env):
    """Activity is the newest of several files, not the directory mtime: a
    run still writing its log is not idle."""
    repo, runs = env
    d = _run(runs, repo, "busy1", old=True)
    os.utime(d / "logs" / "w.log", None)
    rep = leerie._reap_idle_worktrees(runs, "self")
    assert rep["skipped_recent"] == ["busy1"]
    assert (d / "worktrees").is_dir()


def test_leftover_trash_from_an_interrupted_reap_is_deleted(leerie, env):
    repo, runs = env
    d = runs / "old2"
    trash = d / ".worktrees.reap-999"
    (trash / "x").mkdir(parents=True)
    (trash / "x" / "f").write_text("z" * 500)
    rep = leerie._reap_idle_worktrees(runs, "self")
    assert not trash.exists()
    assert rep["bytes"] >= 500


def test_a_resumed_run_rebuilds_its_worktree(leerie, env, tmp_path):
    """After a reap, the run's own script recreates the worktree at its
    branch tip with committed work intact."""
    repo, runs = env
    rid = "resume1"
    _git("branch", f"leerie/runs/{rid}", "main", cwd=repo)
    d = runs / rid
    import subprocess
    script = Path(__file__).resolve().parents[1] / "scripts" / "new-worktree.sh"
    envv = {"PATH": "/usr/bin:/bin", "HOME": str(repo),
            "LEERIE_STATE_DIR": str(runs.parent)}
    r = subprocess.run(["bash", str(script), "feat-001", rid], cwd=repo,
                       capture_output=True, text=True, env=envv)
    assert r.returncode == 0, r.stderr
    wt = Path(r.stdout.strip().splitlines()[-1])
    (wt / "done.txt").write_text("work\n")
    _git("add", "-A", cwd=wt)
    _git("commit", "-q", "-m", "work", cwd=wt)
    tip = _git("rev-parse", "HEAD", cwd=wt).stdout.strip()
    _age(d)
    assert leerie._reap_idle_worktrees(runs, "self")["reaped"] == [rid]
    r = subprocess.run(["bash", str(script), "feat-001", rid], cwd=repo,
                       capture_output=True, text=True, env=envv)
    assert r.returncode == 0, r.stderr
    assert _git("rev-parse", "HEAD", cwd=wt).stdout.strip() == tip
    assert (wt / "done.txt").exists()


def test_preflight_reaps_before_the_disk_check(leerie, env, monkeypatch):
    repo, runs = env
    idle = _run(runs, repo, "idle9", old=True)
    me = runs / "me"
    me.mkdir()
    order: list[str] = []
    real = leerie._reap_idle_worktrees

    def spy(runs_dir, self_id, *a, **k):
        order.append(f"reap:{self_id}")
        return real(runs_dir, self_id, *a, **k)

    def ratio(path):
        order.append("disk")
        return 1.0

    logs: list[str] = []
    monkeypatch.setattr(leerie, "_reap_idle_worktrees", spy)
    monkeypatch.setattr(leerie, "_disk_free_ratio", ratio)
    monkeypatch.setattr(leerie, "_check_claude_cli_version", lambda: None)
    monkeypatch.setattr(leerie, "log", lambda m: logs.append(m))
    asyncio.run(leerie.preflight(me, skip_smoke=True))
    assert order == ["reap:me", "disk"]
    assert not (idle / "worktrees").exists()
    (line,) = [m for m in logs if "reaped the worktrees" in m]
    assert "1 run(s) idle >= 7d" in line and "MiB" in line


def test_a_reap_failure_never_stops_preflight(leerie, env, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("disk on fire")

    logs: list[str] = []
    monkeypatch.setattr(leerie, "_reap_idle_worktrees", boom)
    monkeypatch.setattr(leerie, "_disk_free_ratio", lambda p: 1.0)
    monkeypatch.setattr(leerie, "_check_claude_cli_version", lambda: None)
    monkeypatch.setattr(leerie, "log", lambda m: logs.append(m))
    me = env[1] / "me"
    me.mkdir()
    asyncio.run(leerie.preflight(me, skip_smoke=True))
    assert any("worktree reap skipped" in m for m in logs)
