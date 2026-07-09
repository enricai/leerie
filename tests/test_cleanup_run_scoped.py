"""Source-text pins for the run-scoped cleanup.sh modes.

cleanup.sh supports:
- `--run-id <id> [--branches | --subtask-branches]` — single-run cleanup
- `--all-runs [--branches | --subtask-branches]` — every per-run dir
- no flag — most-recently-failed run, with y/N prompt

`--subtask-branches` is the post-finalize default (invoked by
phase_finalize): it deletes only the per-subtask branches and keeps
leerie/runs/<id> (the PR head). `--branches` is broader and deletes
both the run branch and the subtask branches; they are mutually
exclusive.

Plus a small behavioral test: run cleanup.sh against a real tmp_path
repo with a synthetic per-run dir and confirm the dir gets removed.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CLEANUP_SH = REPO_ROOT / "scripts" / "cleanup.sh"


def _src() -> str:
    return CLEANUP_SH.read_text()


# --- mode declarations ---------------------------------------------------

def test_cleanup_declares_run_id_mode():
    src = _src()
    assert '--run-id)' in src
    assert 'RUN_ID="${2:?--run-id needs an argument}"' in src


def test_cleanup_declares_all_runs_mode():
    src = _src()
    assert '--all-runs)' in src


def test_cleanup_declares_branches_flag():
    src = _src()
    assert '--branches)' in src


# --- run-scoping safety --------------------------------------------------

def test_cleanup_scopes_worktree_removal_to_run_dir():
    """--run-id only touches <state-dir>/runs/<id>/worktrees/, not a
    top-level <state-dir>/worktrees/. The construction is via a local
    `run_dir` variable: `run_dir="${LEERIE_ROOT}/runs/${run_id}"` then
    `"${run_dir}/worktrees"`. LEERIE_ROOT honors LEERIE_STATE_DIR with
    .leerie/ as the legacy fallback — same precedence as setup-run.sh
    and finalize.sh."""
    src = _src()
    clean_one_run = src.split("clean_one_run() {")[1].split("\n}")[0]
    # The run_dir variable is correctly anchored under runs/ via LEERIE_ROOT.
    assert 'run_dir="${LEERIE_ROOT}/runs/${run_id}"' in clean_one_run
    # The worktrees path is derived from run_dir.
    assert '${run_dir}/worktrees' in clean_one_run
    # And the top-level path must NOT appear inside clean_one_run.
    assert '.leerie/worktrees/' not in clean_one_run


def test_cleanup_branch_delete_scopes_to_run_id():
    """When --branches is passed, only leerie/runs/<run-id> and
    leerie/subtasks/<run-id>/* get deleted — NOT every leerie/* branch.
    The two prefixes are disjoint so neither is an ancestor ref of the
    other (see compute_run_branch docstring)."""
    src = _src()
    # The for-each-ref patterns restrict to the run_id's namespace.
    assert 'refs/heads/leerie/runs/${run_id}' in src
    assert 'refs/heads/leerie/subtasks/${run_id}/' in src


def test_cleanup_default_mode_uses_most_recently_failed_heuristic():
    """No-flag invocation finds the most-recently-failed run and prompts."""
    src = _src()
    assert "most_recent_failed_run" in src
    # Confirmation prompt — uppercase N as the default ('[y/N]').
    assert "[y/N]" in src


def test_cleanup_unrecognized_arg_exits_nonzero():
    src = _src()
    # The catch-all case in the argument parser.
    assert "cleanup.sh: unrecognized arg:" in src
    assert "exit 2" in src


# --- behavioral: single-run cleanup actually removes the dir -------------

def test_cleanup_run_id_removes_worktrees_but_preserves_state(tmp_path):
    """End-to-end: in a fresh git repo, create a per-run dir with a
    state.json + a (fake) worktree, run `cleanup.sh --run-id <id>`,
    confirm worktrees are gone but the state dir + state.json survive
    as an audit trail. Full purge is reserved for the Ctrl-C path
    inside the orchestrator."""
    # Set up a tiny git repo so `git worktree prune` doesn't error.
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@x"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
    (repo / "file").write_text("x")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)

    run_id = "feat-test-aaa111"
    run_dir = repo / ".leerie" / "runs" / run_id
    (run_dir / "worktrees").mkdir(parents=True)
    (run_dir / "state.json").write_text('{"task": "test"}')
    (run_dir / "criteria").mkdir()
    (run_dir / "criteria" / "feat-001.md").write_text("# criteria")

    import os as _os
    env = {k: v for k, v in _os.environ.items() if k != "LEERIE_STATE_DIR"}
    r = subprocess.run(
        [str(CLEANUP_SH), "--run-id", run_id],
        cwd=repo, env=env, capture_output=True, text=True, check=False,
    )
    assert r.returncode == 0, f"cleanup.sh failed: {r.stderr}"
    # State dir and state.json must survive as an audit trail.
    assert run_dir.exists(), (
        f"cleanup.sh --run-id must NOT remove the run dir (kept for audit); "
        f"missing: {run_dir}"
    )
    assert (run_dir / "state.json").exists()
    assert (run_dir / "criteria" / "feat-001.md").exists()
    # The worktrees subdirectory should be gone (or empty).
    assert not (run_dir / "worktrees").exists() or not any(
        (run_dir / "worktrees").iterdir()
    ), "cleanup.sh --run-id must clear ${run_dir}/worktrees/*"


def test_cleanup_subtask_branches_deletes_only_subtask_branches(tmp_path):
    """End-to-end: `cleanup.sh --run-id <id> --subtask-branches` deletes
    every `leerie/subtasks/<id>/*` branch but keeps `leerie/runs/<id>`
    (the PR head). State dir and the run branch must both survive."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@x"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
    (repo / "file").write_text("x")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)

    run_id = "feat-test-bbb222"
    run_dir = repo / ".leerie" / "runs" / run_id
    (run_dir / "worktrees").mkdir(parents=True)
    (run_dir / "state.json").write_text('{"task": "test"}')

    # Create the run branch + three subtask branches off of main.
    subprocess.run(
        ["git", "branch", f"leerie/runs/{run_id}", "main"],
        cwd=repo, check=True,
    )
    for sid in ("feat-001", "config-002", "feat-003"):
        subprocess.run(
            ["git", "branch", f"leerie/subtasks/{run_id}/{sid}", "main"],
            cwd=repo, check=True,
        )

    import os as _os
    env = {k: v for k, v in _os.environ.items() if k != "LEERIE_STATE_DIR"}
    r = subprocess.run(
        [str(CLEANUP_SH), "--run-id", run_id, "--subtask-branches"],
        cwd=repo, env=env, capture_output=True, text=True, check=False,
    )
    assert r.returncode == 0, f"cleanup.sh failed: {r.stderr}"

    # The run branch must survive (it's the PR head).
    refs = subprocess.run(
        ["git", "for-each-ref", "--format=%(refname:short)", "refs/heads/leerie/"],
        cwd=repo, capture_output=True, text=True, check=True,
    ).stdout.split()
    assert f"leerie/runs/{run_id}" in refs, (
        "cleanup.sh --subtask-branches must NOT delete the run branch "
        "(it's the PR head and must outlive the orchestrator)."
    )
    # Every subtask branch must be gone.
    for sid in ("feat-001", "config-002", "feat-003"):
        assert f"leerie/subtasks/{run_id}/{sid}" not in refs, (
            f"cleanup.sh --subtask-branches must delete "
            f"leerie/subtasks/{run_id}/{sid}"
        )
    # State dir survives.
    assert (run_dir / "state.json").exists()


def test_cleanup_branches_and_subtask_branches_mutually_exclusive(tmp_path):
    """Passing both --branches and --subtask-branches must error with
    exit 2 — they would otherwise conflict on whether to delete the
    run branch."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    (repo / "x").write_text("x")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "i"], cwd=repo, check=True)

    r = subprocess.run(
        [str(CLEANUP_SH), "--run-id", "anything",
         "--branches", "--subtask-branches"],
        cwd=repo, capture_output=True, text=True, check=False,
    )
    assert r.returncode == 2, f"expected exit 2; got {r.returncode}"
    assert "mutually exclusive" in r.stderr


# --- LEERIE_STATE_DIR precedence -----------------------------------------
#
# Regression cover for the bug observed 2026-06-06: cleanup.sh hardcoded
# `.leerie/runs/<id>/` relative to CWD and ignored LEERIE_STATE_DIR.
# Inside the container LEERIE_STATE_DIR=/leerie-state is always set, so
# the post-finalize subtask-branch cleanup invoked from
# orchestrator/leerie.py:13085 silently no-op'd (its target path didn't
# exist), leaving subtask branches behind.

def test_cleanup_finds_run_dir_under_state_dir(tmp_path):
    """cleanup.sh --run-id reads from $LEERIE_STATE_DIR/runs/<id>/ when
    that env var is set — NOT from <cwd>/.leerie/runs/<id>/."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@x"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
    (repo / "file").write_text("x")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)

    state_dir = tmp_path / "leerie-state"
    run_id = "feat-state-dir-test"
    run_dir = state_dir / "runs" / run_id
    (run_dir / "worktrees").mkdir(parents=True)
    (run_dir / "state.json").write_text('{"task": "test"}')
    # Deliberately do NOT create <repo>/.leerie/runs/<run_id>/ — proves the
    # script honors LEERIE_STATE_DIR rather than falling back to the
    # repo-local path.

    import os as _os
    env = {**_os.environ, "LEERIE_STATE_DIR": str(state_dir)}
    r = subprocess.run(
        [str(CLEANUP_SH), "--run-id", run_id],
        cwd=repo, env=env, capture_output=True, text=True, check=False,
    )
    assert r.returncode == 0, (
        f"cleanup.sh should find the run under LEERIE_STATE_DIR; "
        f"stderr:\n{r.stderr}"
    )
    # Audit-trail invariant: state.json kept.
    assert (run_dir / "state.json").exists()


def test_cleanup_state_dir_unset_falls_back_to_repo(tmp_path):
    """Without LEERIE_STATE_DIR, cleanup.sh resolves .leerie/runs/<id>/
    relative to CWD — the legacy/in-repo behavior."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@x"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
    (repo / "file").write_text("x")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)

    run_id = "feat-fallback-test"
    run_dir = repo / ".leerie" / "runs" / run_id
    (run_dir / "worktrees").mkdir(parents=True)
    (run_dir / "state.json").write_text('{"task": "test"}')

    import os as _os
    env = {k: v for k, v in _os.environ.items() if k != "LEERIE_STATE_DIR"}
    r = subprocess.run(
        [str(CLEANUP_SH), "--run-id", run_id],
        cwd=repo, env=env, capture_output=True, text=True, check=False,
    )
    assert r.returncode == 0, (
        f"cleanup.sh fallback to .leerie/ should succeed when LEERIE_STATE_DIR "
        f"is unset; stderr:\n{r.stderr}"
    )
    assert (run_dir / "state.json").exists()
