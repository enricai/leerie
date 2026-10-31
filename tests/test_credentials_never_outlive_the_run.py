"""No copy of the credentials outlives its run's process, and every deleter
of a run's directories deletes them (DESIGN §6 *No copy of the credentials
outlives its run's process*, *Every deleter of a run's directories deletes
them*; review round 18 M1).

The run's CLI configuration template (`<run-dir>/claude-config/`) is
read-only and holds the operator's `.credentials.json`; `workers/` holds
writable copies. Real `_claude_config_template`, `_worker_dirs`, abnormal
exit, preflight reap and the real launcher `prune --apply`.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from tests.conftest import REPO_ROOT
from tests.test_prune_verb import LAUNCHER, OLD, _repo

SECRET = "OPERATOR-SECRET"


@pytest.fixture()
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    (h / ".claude").mkdir(parents=True)
    (h / ".claude" / ".credentials.json").write_text(
        json.dumps({"claudeAiOauth": {"accessToken": SECRET}}))
    (h / ".claude" / "settings.json").write_text("{}")
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    return h


def _a_run_with_credentials(leerie, run: Path) -> None:
    """The run's first worker makes the template and its own config."""
    run.mkdir(parents=True, exist_ok=True)
    tpl = leerie._claude_config_template(run)
    assert SECRET in (tpl / ".credentials.json").read_text()
    assert not os.access(tpl, os.W_OK)
    config, _tmp = leerie._worker_dirs(run, "impl-s1", None)
    assert SECRET in (config / ".credentials.json").read_text()


def _no_secret_under(path: Path) -> bool:
    for d, _dirs, files in os.walk(path):
        for f in files:
            try:
                if SECRET in Path(d, f).read_text(errors="ignore"):
                    return False
            except OSError:
                pass
    return True


def test_the_owner_aware_removal_removes_read_only_and_unreadable_dirs(
        leerie, tmp_path):
    top = tmp_path / "t"
    (top / "ro" / "deep").mkdir(parents=True)
    (top / "ro" / "deep" / "f").write_text("x")
    (top / "noread").mkdir()
    (top / "noread" / "g").write_text("y")
    os.chmod(top / "ro" / "deep", 0o500)
    os.chmod(top / "ro", 0o500)
    os.chmod(top / "noread", 0o000)
    (top / "link").symlink_to(tmp_path / "outside")
    (tmp_path / "outside").mkdir()
    assert leerie._rmtree_owned(top) is True
    assert not os.path.lexists(top)
    assert (tmp_path / "outside").is_dir()       # a link is never followed
    assert leerie._rmtree_owned(top) is True     # already gone


def test_the_owner_aware_removal_reports_a_survivor(leerie, tmp_path):
    parent = tmp_path / "locked"
    (parent / "child").mkdir(parents=True)
    os.chmod(parent, 0o500)                      # the child cannot go
    try:
        if os.geteuid() == 0:
            pytest.skip("root removes it anyway")
        assert leerie._rmtree_owned(parent / "child") is False
        assert (parent / "child").is_dir()
    finally:
        os.chmod(parent, 0o700)


def test_an_abnormal_exit_removes_every_copy_of_the_credentials(
        leerie, tmp_path, home):
    run = tmp_path / "state" / "runs" / "r18-abnormal"
    _a_run_with_credentials(leerie, run)

    class St:
        run_id = "r18-abnormal"
        run_dir = run
    leerie._cleanup_on_abnormal_exit(St, full_purge=False)
    assert not (run / "claude-config").exists()
    assert not (run / "workers").exists()
    assert run.is_dir()                          # resumable: state stays
    assert _no_secret_under(run)


def test_a_full_purge_removes_the_read_only_template_with_the_run(
        leerie, tmp_path, home):
    run = tmp_path / "state" / "runs" / "r18-purge"
    _a_run_with_credentials(leerie, run)

    class St:
        run_id = "r18-purge"
        run_dir = run
    leerie._cleanup_on_abnormal_exit(St, full_purge=True)
    assert not os.path.lexists(run)


def test_the_preflight_reap_removes_an_idle_runs_credentials(
        leerie, tmp_path, home):
    """An idle run with no worktrees left still has its copies of the
    credentials removed."""
    runs = tmp_path / "state" / "runs"
    run = runs / "r18-idle"
    _a_run_with_credentials(leerie, run)
    (run / "state.json").write_text("{}")
    for p in [run, *run.rglob("*")]:
        try:
            os.utime(p, (OLD, OLD), follow_symlinks=False)
        except OSError:
            pass
    rep = leerie._reap_idle_worktrees(runs, "self")
    assert rep["errors"] == 0, rep
    assert "r18-idle" in rep["reaped"]
    assert not (run / "claude-config").exists()
    assert not (run / "workers").exists()
    assert (run / "state.json").is_file()


def test_prune_removes_a_died_runs_directory_and_its_credentials(
        leerie, tmp_path, home):
    """The reviewer's driver, inverted: a run that died mid-run (terminal,
    never finalized) is removed by `prune --apply`, its read-only template
    with it, and only then reported removed."""
    repo = _repo(tmp_path)
    root = tmp_path / "state"
    run = root / "runs" / "r18e-died-run"
    _a_run_with_credentials(leerie, run)
    (run / "run.json").write_text(json.dumps(
        {"run_id": run.name, "finished_at": "2026-09-01T00:00:00Z",
         "outcome": "failed"}))
    (run / "state.json").write_text("{}")
    os.utime(run, (OLD, OLD))
    env = {**os.environ, "LEERIE_STATE_DIR": str(root),
           "LEERIE_STATE_HOST_DIR": str(root), "USER_REPO": str(repo)}
    r = subprocess.run(["bash", str(LAUNCHER), "prune", "--apply"],
                       capture_output=True, text=True, env=env, cwd=repo)
    assert r.returncode == 0, r.stderr
    assert "removed 1 terminal run dir" in r.stdout
    assert not os.path.lexists(run)


def test_prune_counts_a_run_removed_only_when_it_is_gone():
    """Structural, because an unremovable directory cannot be made without
    privileges: the launcher's prune removes a run with the owner-aware
    removal and, when that leaves anything, keeps the run (reported, never
    counted removed, its `run.json` kept with it)."""
    script = (REPO_ROOT / "leerie").read_text()
    body = script[script.index("        if terminal and mtime < cutoff"):]
    body = body[:body.index("        else:\n            live.add(d.name)")]
    remove = body.index("if apply and not _rm_owned(d):")
    assert remove < body.index("runs_removed.append(d.name)")
    assert body.index("live.add(d.name)", remove) < body.index(
        "runs_removed.append(d.name)")


def test_the_shell_removal_removes_read_only_dirs(tmp_path):
    top = tmp_path / "t"
    (top / "ro" / "deep").mkdir(parents=True)
    (top / "ro" / "deep" / "f").write_text("x")
    os.chmod(top / "ro" / "deep", 0o500)
    os.chmod(top / "ro", 0o000)
    r = subprocess.run(
        ["bash", "-c", '. "$1"; leerie_rm_owned "$2" && echo gone', "_",
         str(REPO_ROOT / "scripts" / "worktree-lib.sh"), str(top)],
        capture_output=True, text=True)
    assert r.stdout.strip() == "gone", r.stderr
    assert not os.path.lexists(top)


def test_no_run_directory_deleter_ignores_its_errors():
    """Every removal of a run directory, or of the workers' directories and
    the template, goes through the owner-aware removal: no plain
    `rmtree(..., ignore_errors=True)` of them."""
    src = (REPO_ROOT / "orchestrator" / "leerie.py").read_text()
    for bad in ("shutil.rmtree(st.run_dir, ignore_errors=True)",
                "shutil.rmtree(d, ignore_errors=True)"):
        assert bad not in src, bad
    launcher = (REPO_ROOT / "leerie").read_text()
    assert "shutil.rmtree(d, ignore_errors=True)" not in launcher
    assert "shutil.rmtree(trash, ignore_errors=True)" not in launcher
