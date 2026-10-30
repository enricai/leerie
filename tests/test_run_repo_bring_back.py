"""`host_bring_back_run_branch` (scripts/host-finalize.sh): the run branch
comes back from the run's own repository into the user's (DESIGN §6 *The
run's own repository*), as leerie's own git — hooks off, no FETCH_HEAD — and
as a compare-and-swap against `run.json` `brought_back_sha`, so a finalize
rebase or the user's own change to the ref is never undone. Real git, real
bash; `jq` as on the host.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from tests.conftest import HAS_JQ

pytestmark = pytest.mark.skipif(
    not HAS_JQ,
    reason="host-only script: needs real `jq`, which the launcher guarantees "
           "on the host and the leerie image deliberately omits",
)

REPO_ROOT = Path(__file__).resolve().parent.parent
HOST_FINALIZE_SH = REPO_ROOT / "scripts" / "host-finalize.sh"
LAUNCHER = REPO_ROOT / "leerie"
RUN_ID = "c0ffee123456"
BRANCH = f"leerie/runs/{RUN_ID}"


def _git(cwd: Path, *args: str, check: bool = True) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], check=check,
                          capture_output=True, text=True).stdout.strip()


@pytest.fixture()
def run(tmp_path):
    """A user repository and a run directory whose `repo/` holds a run
    branch one commit past the user's main."""
    user = tmp_path / "user"
    user.mkdir()
    _git(user, "init", "-q", "-b", "main")
    _git(user, "config", "user.email", "t@x")
    _git(user, "config", "user.name", "t")
    (user / "a.txt").write_text("a\n")
    _git(user, "add", "-A")
    _git(user, "commit", "-qm", "base")
    run_dir = tmp_path / "state" / "runs" / RUN_ID
    run_dir.mkdir(parents=True)
    repo = run_dir / "repo"
    _git(tmp_path, "init", "-q", "--bare", str(repo))
    _git(repo, "fetch", "-q", str(user), "+refs/heads/main:refs/heads/main")
    wt = tmp_path / "wt"
    _git(repo, "worktree", "add", "-q", "-b", BRANCH, str(wt), "main")
    (wt / "b.txt").write_text("b\n")
    _git(wt, "add", "-A")
    _git(wt, "-c", "user.email=t@x", "-c", "user.name=t", "commit", "-qm",
         "run work")
    (run_dir / "run.json").write_text(json.dumps(
        {"run_id": RUN_ID, "branch": BRANCH}))
    return user, run_dir, repo, wt


def _bring_back(user: Path, run_dir: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", 'set -euo pipefail; . "$1"; host_bring_back_run_branch "$2"',
         "_", str(HOST_FINALIZE_SH), str(run_dir)],
        env={"PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(run_dir),
             "USER_REPO": str(user)},
        capture_output=True, text=True)


def _recorded(run_dir: Path) -> str | None:
    return json.loads((run_dir / "run.json").read_text()).get(
        "brought_back_sha")


def test_brings_the_branch_back_with_hooks_off_and_no_fetch_head(run):
    user, run_dir, repo, _wt = run
    log = user.parent / "hook.log"
    for name in ("reference-transaction", "post-checkout"):
        hook = user / ".git" / "hooks" / name
        hook.write_text(f"#!/bin/sh\necho {name} >> '{log}'\n")
        hook.chmod(0o755)
    r = _bring_back(user, run_dir)
    assert r.returncode == 0, r.stderr
    tip = _git(repo, "rev-parse", BRANCH)
    assert _git(user, "rev-parse", BRANCH) == tip
    assert _recorded(run_dir) == tip
    assert not (user / ".git" / "FETCH_HEAD").exists()
    assert not log.exists()                     # no hook of the user's ran
    assert _git(user, "for-each-ref", "refs/leerie") == ""


def test_a_later_run_tip_advances_the_users_ref(run):
    user, run_dir, repo, wt = run
    assert _bring_back(user, run_dir).returncode == 0
    (wt / "c.txt").write_text("c\n")
    _git(wt, "add", "-A")
    _git(wt, "-c", "user.email=t@x", "-c", "user.name=t", "commit", "-qm",
         "more")
    assert _bring_back(user, run_dir).returncode == 0
    assert _git(user, "rev-parse", BRANCH) == _git(repo, "rev-parse", BRANCH)


def test_refuses_to_undo_a_finalize_rebase(run):
    """The compare-and-swap: the user's ref moved off `brought_back_sha`
    (a finalize rebase) — it is left there, and since the run's tip is what
    was brought back, nothing is lost (rc 0)."""
    user, run_dir, repo, _wt = run
    assert _bring_back(user, run_dir).returncode == 0
    brought = _git(user, "rev-parse", BRANCH)
    _git(user, "update-ref", f"refs/heads/{BRANCH}", "main")    # "rebased"
    r = _bring_back(user, run_dir)
    assert r.returncode == 0
    assert "leaving it" in r.stderr
    assert _git(user, "rev-parse", BRANCH) == _git(user, "rev-parse", "main")
    assert _recorded(run_dir) == brought


def test_refusal_with_unbrought_work_returns_2(run):
    """The user's ref is not where leerie left it AND the run has commits
    since: refused, and rc 2 tells a caller (prune) to keep the run."""
    user, run_dir, repo, wt = run
    _git(user, "branch", BRANCH, "main")      # never brought back by leerie
    r = _bring_back(user, run_dir)
    assert r.returncode == 2
    assert _git(user, "rev-parse", BRANCH) == _git(user, "rev-parse", "main")
    assert _recorded(run_dir) is None


def test_a_deleted_brought_back_ref_is_not_recreated(run):
    user, run_dir, _repo, _wt = run
    assert _bring_back(user, run_dir).returncode == 0
    _git(user, "branch", "-D", BRANCH)
    r = _bring_back(user, run_dir)
    assert r.returncode == 0
    assert _git(user, "rev-parse", "--verify", "-q", BRANCH,
                check=False) == ""


def test_a_run_without_its_own_repository_is_a_no_op(run, tmp_path):
    user, _run_dir, _repo, _wt = run
    old = tmp_path / "state" / "runs" / "0ld0ld0ld0ld"
    old.mkdir(parents=True)
    (old / "run.json").write_text("{}")
    r = _bring_back(user, old)
    assert r.returncode == 0 and r.stderr == ""


def test_every_launcher_path_calls_the_bring_back():
    """The three call sites the design names: after the container exits
    (before the skip on a non-zero exit or --no-push), `finalize` (local),
    and `prune` before it deletes a run dir; plus `host_finalize` itself."""
    src = LAUNCHER.read_text()
    post = src.index('host_bring_back_run_branch "$LEERIE_STATE_HOST_DIR/runs/$_bb_run_id"')
    skip = src.index('if [ "$container_rc" -ne 0 ] || [ "$NO_PUSH" = "true" ]; then')
    assert post < skip
    assert 'host_bring_back_run_branch "$_fin_run_dir"' in src
    assert "host_bring_back_run_branch \"$2\"" in src          # prune
    hf = HOST_FINALIZE_SH.read_text()
    body = hf[hf.index("host_finalize() {"):]
    assert body.index('host_bring_back_run_branch "$run_dir"') < body.index(
        "no_push // false")


def test_prune_keeps_a_run_whose_branch_cannot_come_back(run, tmp_path):
    """`prune --apply` deletes an old terminal run only after its branch is
    in the user's repository; a refused bring-back with unbrought work keeps
    the directory."""
    user, run_dir, repo, _wt = run
    _git(user, "branch", BRANCH, "main")          # blocks the swap
    (run_dir / "run.json").write_text(json.dumps(
        {"run_id": RUN_ID, "branch": BRANCH,
         "finished_at": "2020-01-01T00:00:00Z"}))
    import os
    old = 946684800                                # 2000-01-01
    os.utime(run_dir, (old, old))
    src = LAUNCHER.read_text()
    start = src.index("      python3 - \"$LEERIE_STATE_HOST_DIR\" \"$USER_REPO\" <<'PY'\n")
    body = src[src.index("\n", start) + 1:]
    body = body[:body.index("\nPY\n")]
    script = tmp_path / "prune.py"
    script.write_text(body)
    r = subprocess.run(
        ["python3", str(script), str(tmp_path / "state"), str(user)],
        env={"PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(tmp_path),
             "LEERIE_PRUNE_DAYS": "1", "LEERIE_PRUNE_APPLY": "true",
             "LEERIE_PRUNE_HOST_FINALIZE": str(HOST_FINALIZE_SH)},
        capture_output=True, text=True, cwd=str(user))
    assert r.returncode == 0, r.stderr
    assert run_dir.is_dir()
    assert f"kept run dir {RUN_ID}" in r.stdout
    # Unblocked, the same prune brings the branch back and then deletes it.
    _git(user, "branch", "-D", BRANCH)
    os.utime(run_dir, (old, old))
    r = subprocess.run(
        ["python3", str(script), str(tmp_path / "state"), str(user)],
        env={"PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(tmp_path),
             "LEERIE_PRUNE_DAYS": "1", "LEERIE_PRUNE_APPLY": "true",
             "LEERIE_PRUNE_HOST_FINALIZE": str(HOST_FINALIZE_SH)},
        capture_output=True, text=True, cwd=str(user))
    assert r.returncode == 0, r.stderr
    assert not run_dir.exists()
    assert _git(user, "rev-parse", "--verify", "-q", BRANCH)
