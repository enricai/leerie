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
    """The call sites the design names: the launcher's EXIT trap (every
    exit of a local run, Ctrl-C and TERM included), `finalize` (local), and
    `prune` before it deletes a run dir; plus `host_finalize` itself."""
    src = LAUNCHER.read_text()
    assert 'host_bring_back_run_branch "$LEERIE_STATE_HOST_DIR/runs/$_ran_run_id"' in _functions_block()
    exit_trap = [ln for ln in src.splitlines()
                 if ln.strip().startswith("trap '") and ln.endswith("' EXIT")
                 and "_kill_container_from_cidfile" in ln]
    assert len(exit_trap) == 1
    assert exit_trap[0].index("_bring_back_ran_run") < exit_trap[0].index(
        'rm -f "${_cidfile:-}" "${_run_id_file:-}"')
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

SKIP_LINE = 'if [ "$container_rc" -ne 0 ] || [ "$NO_PUSH" = "true" ]; then\n'


def _functions_block() -> str:
    """The launcher's `_read_ran_run_id` and `_bring_back_ran_run`,
    verbatim."""
    src = LAUNCHER.read_text()
    start = src.index("  _read_ran_run_id() {\n")
    fn = src.index("  _bring_back_ran_run() {\n", start)
    return src[start:src.index("\n  }\n", fn) + len("\n  }\n")]


def _bring_back_block() -> str:
    """What the launcher's EXIT trap runs for the bring-back."""
    return _functions_block() + "_bring_back_ran_run\n"


def _post_exit_block() -> str:
    """Every post-exit step after the container returns, verbatim: the
    read, the skip line and the auto-finalize, up to the Fly comment that
    follows them; with the functions they call."""
    src = LAUNCHER.read_text()
    start = src.index('_ran_run_id=""\n[ "$RUNTIME" != "local" ]')
    return _functions_block() + src[
        start:src.index("# Fly path: decide_teardown", start)]


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


# --- auto-finalize: the run this container ran, never another -------------

def _finalize_env(tmp_path: Path) -> tuple[Path, Path, Path]:
    """A state root and a stand-in `scripts/host-finalize.sh` that records
    which run directory `host_finalize` was handed (the push itself is
    host-finalize's own, tested elsewhere)."""
    state = tmp_path / "state"
    (state / "runs").mkdir(parents=True)
    fake = tmp_path / "fake-leerie"
    (fake / "scripts").mkdir(parents=True)
    log = tmp_path / "finalized.log"
    (fake / "scripts" / "host-finalize.sh").write_text(
        f'host_finalize() {{ echo "$1" >> "{log}"; }}\n'
        "host_bring_back_run_branch() { :; }\n")
    return state, fake, log


def _a_run(state: Path, run_id: str, **fields) -> Path:
    d = state / "runs" / run_id
    d.mkdir()
    (d / "run.json").write_text(json.dumps(
        {"run_id": run_id, "branch": f"leerie/runs/{run_id}",
         "working_branch": "main", **fields}))
    return d


def _post_exit(tmp_path, state, fake, *, ran: str | None, rc: int = 0,
               leerie_run_id: str = "", cid: str = ""):
    rid = state / ".runid-test"
    if ran is not None:
        rid.write_text(ran + "\n")
    cidfile = state / ".cidfile-test"
    if cid:
        cidfile.write_text(cid)
    script = (f"set -euo pipefail\n. {REPO_ROOT / 'scripts/remote/_log.sh'}\n"
              f"RUNTIME=local\ncontainer_rc={rc}\nNO_PUSH=false\n"
              f'LEERIE_RUN_ID="{leerie_run_id}"\n_cidfile="{cidfile}"\n'
              f'_run_id_file="{rid}"\n' + _post_exit_block())
    return subprocess.run(
        ["bash", "-c", script],
        env={"PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(tmp_path),
             "USER_REPO": str(tmp_path), "LEERIE_REPO": str(fake),
             "LEERIE_STATE_HOST_DIR": str(state)},
        capture_output=True, text=True)


def _finalized(log: Path) -> list[str]:
    return log.read_text().split() if log.exists() else []


def test_two_runs_auto_finalize_takes_the_one_this_container_ran(tmp_path):
    """Two finished, unpushed runs of one repository; the NEWER run.json is
    the other run's. This container ran the older one — that is the one
    finalized. The newest-unpushed rule this replaced pushed the other."""
    import os
    state, fake, log = _finalize_env(tmp_path)
    mine = _a_run(state, "older-run", finished_at="2026-10-30T01:00:00Z")
    other = _a_run(state, "newer-run", finished_at="2026-10-30T02:00:00Z")
    os.utime(mine / "run.json", (1_000_000, 1_000_000))
    os.utime(other / "run.json", (2_000_000, 2_000_000))
    r = _post_exit(tmp_path, state, fake, ran="older-run",
                   leerie_run_id="newer-run", cid="newer-run")
    assert r.returncode == 0, r.stderr
    assert _finalized(log) == [str(mine)]


def test_another_run_still_running_is_never_finalized(tmp_path):
    """This container's run is not finished; a second run is finished and
    unpushed. Nothing is finalized — not the other run."""
    state, fake, log = _finalize_env(tmp_path)
    _a_run(state, "mine")
    _a_run(state, "theirs", finished_at="2026-10-30T02:00:00Z")
    r = _post_exit(tmp_path, state, fake, ran="mine")
    assert r.returncode == 0, r.stderr
    assert _finalized(log) == []
    assert "run mine is not finished" in r.stderr


def test_no_run_named_finalizes_nothing(tmp_path):
    """No run-id file (the container ran no run): no finalize, whatever is
    on disk and whatever `--run-id` or the cidfile say."""
    state, fake, log = _finalize_env(tmp_path)
    _a_run(state, "some-run", finished_at="2026-10-30T02:00:00Z")
    r = _post_exit(tmp_path, state, fake, ran=None,
                   leerie_run_id="some-run", cid="some-run")
    assert r.returncode == 0, r.stderr
    assert _finalized(log) == []
    assert "named no run" in r.stderr


def test_an_already_pushed_run_is_not_finalized_again(tmp_path):
    state, fake, log = _finalize_env(tmp_path)
    _a_run(state, "mine", finished_at="2026-10-30T02:00:00Z",
           pushed_at="2026-10-30T03:00:00Z")
    r = _post_exit(tmp_path, state, fake, ran="mine")
    assert r.returncode == 0
    assert _finalized(log) == []


def test_the_skip_line_names_the_run_from_the_file_only(tmp_path):
    """A failed exit: the line names the run the file names, never the
    `--run-id` or the cidfile; with no file it names none."""
    state, fake, _log = _finalize_env(tmp_path)
    r = _post_exit(tmp_path, state, fake, ran="mine", rc=1,
                   leerie_run_id="other", cid="containerid")
    assert r.returncode == 1
    assert r.stderr.rstrip().endswith(
        "finalize: skipped — container exited with code 1 (run mine)")
    (state / ".runid-test").unlink()
    r = _post_exit(tmp_path, state, fake, ran=None, rc=1,
                   leerie_run_id="other", cid="containerid")
    assert r.stderr.rstrip().endswith(
        "finalize: skipped — container exited with code 1")


def test_no_post_exit_step_names_the_run_another_way():
    """Structural, paired with the behavioural tests above: after the
    container exits, nothing in the launcher reads `--run-id`, the cidfile
    or a scan of runs to know its run."""
    block = _post_exit_block()
    assert "LEERIE_RUN_ID" not in block
    assert "_cidfile" not in block
    assert "runs/*/" not in block and "mtime" not in block


def test_ctrl_c_still_brings_the_branch_back(run, tmp_path):
    """Review round 17 M2: the launcher's own trap lines, verbatim, around a
    foreground child standing in for `nerdctl run`. SIGINT to the process
    group, as a terminal's Ctrl-C sends it: the script exits 130 through its
    INT trap, and the EXIT trap still brings the run's branch back."""
    import os
    import signal
    import time
    user, run_dir, repo, _wt = run
    state_root = run_dir.parent.parent
    rid = state_root / ".runid-test"
    rid.write_text(RUN_ID + "\n")
    src = LAUNCHER.read_text()
    traps = [ln.strip() for ln in src.splitlines()
             if ln.strip().startswith("trap '")
             and "_kill_container_from_cidfile" in ln]
    assert [t.rsplit(" ", 1)[-1] for t in traps] == ["EXIT", "INT", "TERM"]
    ready = tmp_path / "ready"
    script = ("set -euo pipefail\nRUNTIME=local\n"
              f'_run_id_file="{rid}"\n_cidfile=""\nSTAGE="{tmp_path}/stage"\n'
              # A keepalive that already exited: `kill` fails, and under
              # `set -e` that must not end the trap before the bring-back.
              "_stage_keepalive_pid=999999\n"
              "_reap_tail() { :; }\n_kill_container_from_cidfile() { :; }\n"
              + _functions_block() + "\n".join(traps) + "\n"
              f'touch "{ready}"\nsleep 30\necho NOT-INTERRUPTED\n')
    proc = subprocess.Popen(
        ["bash", "-c", script], start_new_session=True,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(tmp_path),
             "USER_REPO": str(user), "LEERIE_REPO": str(REPO_ROOT),
             "LEERIE_STATE_HOST_DIR": str(state_root)},
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    deadline = time.monotonic() + 20
    while not ready.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    time.sleep(0.2)
    os.killpg(proc.pid, signal.SIGINT)
    out, err = proc.communicate(timeout=60)
    assert proc.returncode == 130, (out, err)
    assert "NOT-INTERRUPTED" not in out
    assert _git(user, "rev-parse", BRANCH) == _git(repo, "rev-parse", BRANCH)
    assert not rid.exists()            # the EXIT trap's clean-up ran after


def test_a_tip_that_is_not_a_fast_forward_is_refused(run):
    """Review round 17 H1: after a bring-back, the run's branch is replaced
    by an unrelated tip (a rebuilt run repository). The compare-and-swap
    alone would move the user's ref; the fast-forward rule refuses (rc 2),
    and the brought-back work stays on the user's branch."""
    user, run_dir, repo, wt = run
    assert _bring_back(user, run_dir).returncode == 0
    brought = _git(user, "rev-parse", BRANCH)
    _git(wt, "checkout", "-q", "--orphan", "fresh")
    (wt / "z.txt").write_text("unrelated\n")
    _git(wt, "add", "-A")
    _git(wt, "-c", "user.email=t@x", "-c", "user.name=t", "commit", "-qm",
         "unrelated")
    _git(repo, "update-ref", f"refs/heads/{BRANCH}", "refs/heads/fresh")
    r = _bring_back(user, run_dir)
    assert r.returncode == 2, r.stderr
    assert "does not descend" in r.stderr
    assert _git(user, "rev-parse", BRANCH) == brought
    assert _git(user, "for-each-ref", "refs/leerie") == ""
