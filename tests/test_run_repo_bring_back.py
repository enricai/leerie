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


# --- which run the container ran (DESIGN §6 *Which run the container ran*)

def _bring_back_block() -> str:
    """The launcher's post-run bring-back block, verbatim."""
    src = LAUNCHER.read_text()
    start = src.index('_bb_run_id=""\n')
    end = src.index("unset _bb_run_id\n", start) + len("unset _bb_run_id\n")
    return src[start:end]


def _post_run(user: Path, state_root: Path, run_id_file: Path,
              *, leerie_run_id: str = "", cid: str = ""):
    cidfile = state_root / ".cidfile-test"
    if cid:
        cidfile.write_text(cid)
    script = (f'set -euo pipefail\nRUNTIME=local\n'
              f'LEERIE_RUN_ID="{leerie_run_id}"\n'
              f'_cidfile="{cidfile}"\n_run_id_file="{run_id_file}"\n'
              + _bring_back_block())
    return subprocess.run(
        ["bash", "-c", script],
        env={"PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(state_root),
             "USER_REPO": str(user), "LEERIE_REPO": str(REPO_ROOT),
             "LEERIE_STATE_HOST_DIR": str(state_root)},
        capture_output=True, text=True)


def test_a_bare_resumes_own_pick_is_brought_back(run):
    """The gap this closes: a bare `resume` auto-picked the run, so the
    launcher had no `--run-id`, and its cidfile names the NEW container,
    no run at all. The orchestrator's run-id file names the run it ran, and
    that run's branch comes back."""
    user, run_dir, repo, _wt = run
    state_root = run_dir.parent.parent
    rid = state_root / ".runid-test"
    rid.write_text(RUN_ID + "\n")
    r = _post_run(user, state_root, rid, cid="f00dcafe" * 8)
    assert r.returncode == 0, r.stderr
    assert _git(user, "rev-parse", BRANCH) == _git(repo, "rev-parse", BRANCH)


def test_the_file_is_the_one_source_not_the_launchers_guesses(run):
    """`--run-id` / the cidfile name a run; the file names another (the one
    the container ran). Only the file's run is brought back; with no file,
    nothing is — there is no guess and no scan."""
    user, run_dir, repo, _wt = run
    state_root = run_dir.parent.parent
    rid = state_root / ".runid-test"
    r = _post_run(user, state_root, rid, leerie_run_id=RUN_ID, cid=RUN_ID)
    assert r.returncode == 0, r.stderr
    assert _git(user, "rev-parse", "--verify", "-q", BRANCH,
                check=False) == ""
    rid.write_text("../../etc\n")             # not a run id: ignored
    assert _post_run(user, state_root, rid).returncode == 0
    assert _git(user, "rev-parse", "--verify", "-q", BRANCH,
                check=False) == ""


def test_the_launcher_names_the_file_and_the_container_gets_it():
    """The path the launcher reads is the one it hands the container, under
    the state root mount, and a host value of the variable never reaches
    the container in its place."""
    src = LAUNCHER.read_text()
    assert '_run_id_file="$LEERIE_STATE_HOST_DIR/.runid-$$"' in src
    assert '-e "LEERIE_RUN_ID_FILE=/leerie-state/.runid-$$"' in src
    assert '-v "$LEERIE_STATE_HOST_DIR:/leerie-state"' in src
    deny = src[src.index('_leerie_env_denylist=" \\'):]
    deny = deny[:deny.index('"\n')]
    assert " LEERIE_RUN_ID_FILE " in deny.replace("\\\n", " ")
