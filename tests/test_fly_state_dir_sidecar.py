"""Tests that fly-runtime host-side sidecar writes and run-dir lookups
target LEERIE_STATE_HOST_DIR rather than USER_REPO/.leerie.

Contract verified here:

  1. The `--resume --runtime fly` lookup resolves the machine pointer
     from LEERIE_STATE_HOST_DIR/runs/<id>/, not USER_REPO/.leerie/runs/.
  2. The launcher's sidecar-write block (fly-machine.json, run.json,
     task.txt) lands under LEERIE_STATE_HOST_DIR, not USER_REPO/.leerie.
  3. The `--finalize` verb looks up run dirs from LEERIE_STATE_HOST_DIR,
     not USER_REPO/.leerie.
  4. The `--stop` and `--kill` verbs look up run dirs from
     LEERIE_STATE_HOST_DIR.
  5. fetch_branch streams state back to LEERIE_STATE_HOST_DIR/runs/,
     not USER_REPO/.leerie/runs/.
  6. Coupling: the launcher source references LEERIE_STATE_HOST_DIR for
     these paths.

All tests use LEERIE_STATE_DIR env var to inject a custom state dir
and confirm that fixtures NOT under USER_REPO/.leerie are found.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LEERIE = REPO_ROOT / "leerie"
FETCH_SH = REPO_ROOT / "scripts" / "remote" / "fetch-branch.sh"


# --- Coupling tests: source-level assertions ---------------------------------

def test_launcher_paused_mid_uses_state_host_dir():
    """The _paused_mid resolver in the --resume fly dispatch must
    use LEERIE_STATE_HOST_DIR/runs/, not USER_REPO/.leerie/runs/."""
    launcher = LEERIE.read_text()
    assert (
        '$LEERIE_STATE_HOST_DIR/runs/$LEERIE_RUN_ID")"'
        in launcher
    ), (
        "Resume dispatch must resolve machine pointer from "
        "LEERIE_STATE_HOST_DIR/runs/, not USER_REPO/.leerie/runs/."
    )


def test_launcher_sidecar_writes_use_state_host_dir():
    """The fly-path sidecar writes (fly-machine.json, run.json, task.txt)
    must use LEERIE_STATE_HOST_DIR, not USER_REPO/.leerie."""
    launcher = LEERIE.read_text()
    # _pid_record
    assert '$LEERIE_STATE_HOST_DIR/remote/$$.json' in launcher, (
        "PID-keyed attach pointer must be written under LEERIE_STATE_HOST_DIR/remote/"
    )
    # mkdir + _run_record
    assert 'mkdir -p "$LEERIE_STATE_HOST_DIR/runs/$LEERIE_RUN_ID"' in launcher, (
        "Run dir must be created under LEERIE_STATE_HOST_DIR/runs/"
    )
    assert '$LEERIE_STATE_HOST_DIR/runs/$LEERIE_RUN_ID/fly-machine.json' in launcher, (
        "fly-machine.json must be written under LEERIE_STATE_HOST_DIR/runs/"
    )
    assert '$LEERIE_STATE_HOST_DIR/runs/$LEERIE_RUN_ID/task.txt' in launcher, (
        "task.txt must be written under LEERIE_STATE_HOST_DIR/runs/"
    )


def test_launcher_bootstrap_migration_uses_state_host_dir():
    """The bootstrap→final-id migration (host-side mv) must use
    LEERIE_STATE_HOST_DIR, not USER_REPO/.leerie."""
    launcher = LEERIE.read_text()
    assert '$LEERIE_STATE_HOST_DIR/runs/${_bootstrap_pid}' in launcher, (
        "Bootstrap host dir must be under LEERIE_STATE_HOST_DIR/runs/"
    )
    assert '$LEERIE_STATE_HOST_DIR/runs/${_final_id}' in launcher, (
        "Final host dir must be under LEERIE_STATE_HOST_DIR/runs/"
    )


def test_launcher_local_finalize_uses_state_host_dir():
    """The local-runtime post-run finalize block must scan
    LEERIE_STATE_HOST_DIR/runs/, not USER_REPO/.leerie/runs/."""
    launcher = LEERIE.read_text()
    # The local-runtime finalize scans for run.json files.
    assert (
        'if [ -d "$LEERIE_STATE_HOST_DIR/runs" ]'
        in launcher
    ), (
        "Local-runtime finalize must check LEERIE_STATE_HOST_DIR/runs/, "
        "not USER_REPO/.leerie/runs/."
    )


def test_launcher_stop_verb_uses_state_host_dir():
    """The --stop verb must look up the run dir from LEERIE_STATE_HOST_DIR."""
    launcher = LEERIE.read_text()
    assert '$LEERIE_STATE_HOST_DIR/runs/$_stop_run_id' in launcher, (
        "--stop verb must resolve run dir from LEERIE_STATE_HOST_DIR/runs/"
    )


def test_launcher_kill_verb_uses_state_host_dir():
    """The --kill verb must look up the run dir from LEERIE_STATE_HOST_DIR."""
    launcher = LEERIE.read_text()
    assert '$LEERIE_STATE_HOST_DIR/runs/$_kill_run_id' in launcher, (
        "--kill verb must resolve run dir from LEERIE_STATE_HOST_DIR/runs/"
    )


def test_launcher_finalize_verb_uses_state_host_dir():
    """The --finalize verb must look up run dirs from LEERIE_STATE_HOST_DIR."""
    launcher = LEERIE.read_text()
    assert '$LEERIE_STATE_HOST_DIR/runs/$_fin_run_id' in launcher, (
        "--finalize verb must resolve run dir from LEERIE_STATE_HOST_DIR/runs/"
    )
    assert '$LEERIE_STATE_HOST_DIR/runs' in launcher, (
        "--finalize bootstrap fallback must scan LEERIE_STATE_HOST_DIR/runs/"
    )


def test_fetch_branch_uses_state_host_dir():
    """fetch-branch.sh must stream state back to LEERIE_STATE_HOST_DIR/runs/
    when set, rather than USER_REPO/.leerie/runs/."""
    src = FETCH_SH.read_text()
    assert 'LEERIE_STATE_HOST_DIR' in src, (
        "fetch-branch.sh must consult LEERIE_STATE_HOST_DIR for the "
        "host-side run state directory."
    )
    assert (
        'host_leerie_runs="$LEERIE_STATE_HOST_DIR/runs"'
        in src
    ), (
        "fetch-branch.sh must set host_leerie_runs to LEERIE_STATE_HOST_DIR/runs "
        "when LEERIE_STATE_HOST_DIR is set."
    )


# --- Behavioral tests: --stop/--kill/--finalize find state_dir fixtures ------

def _make_user_repo(tmp_path: Path) -> Path:
    user_repo = tmp_path / "user-repo"
    user_repo.mkdir()
    for cmd in [
        ["git", "-C", str(user_repo), "init", "-b", "main"],
        ["git", "-C", str(user_repo), "config", "user.email", "t@t.com"],
        ["git", "-C", str(user_repo), "config", "user.name", "T"],
    ]:
        subprocess.run(cmd, check=True, capture_output=True)
    (user_repo / "README.md").write_text("hello")
    subprocess.run(["git", "-C", str(user_repo), "add", "README.md"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(user_repo), "commit", "-m", "init"],
                   check=True, capture_output=True)
    return user_repo


def _make_flyctl_stub_auth_only(tmp_path: Path) -> Path:
    """Minimal flyctl stub: auth status passes, all else fails."""
    stub = tmp_path / "flyctl"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        'case "$1 ${2:-}" in\n'
        "  'auth status') exit 0 ;;\n"
        "  *) exit 1 ;;\n"
        "esac\n"
    )
    stub.chmod(0o755)
    return stub


def test_stop_resolves_run_dir_from_state_host_dir(tmp_path: Path):
    """leerie --stop must find the run dir under LEERIE_STATE_HOST_DIR/runs/,
    not USER_REPO/.leerie/runs/. When a fixture is placed in state_dir
    (not user_repo/.leerie), the verb must succeed the dir-existence check."""
    import json
    user_repo = _make_user_repo(tmp_path)
    state_dir = tmp_path / "leerie-state"
    _make_flyctl_stub_auth_only(tmp_path)

    run_id = "_bootstrap-aa1234"
    run_dir = state_dir / "runs" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "fly-machine.json").write_text(json.dumps({
        "fly_machine_id": "mach-stop-test",
        "fly_app": "leerie",
    }))

    # Also put a stub flyctl that records machine stop so we can verify it
    (tmp_path / "flyctl").write_text(
        "#!/usr/bin/env bash\n"
        f'echo "$@" >> {tmp_path}/flyctl.log\n'
        'case "$1 ${2:-}" in\n'
        "  'auth status') exit 0 ;;\n"
        "  'machine stop') exit 0 ;;\n"
        "  *) exit 0 ;;\n"
        "esac\n"
    )
    (tmp_path / "flyctl").chmod(0o755)

    result = subprocess.run(
        ["bash", str(LEERIE), "--stop", run_id],
        cwd=str(user_repo),
        capture_output=True, text=True,
        env={
            "PATH": f"{tmp_path}:{__import__('os').environ['PATH']}",
            "LEERIE_STATE_DIR": str(state_dir),
            "HOME": str(tmp_path),
        },
    )
    # The run dir was found in state_dir → must not error with "no run dir".
    assert "no run dir" not in result.stderr, (
        "--stop should NOT report 'no run dir' when fixture is in state_dir.\n"
        f"stderr:\n{result.stderr}"
    )


def test_stop_does_not_find_run_dir_under_user_repo(tmp_path: Path):
    """Negative: if the fixture is placed only under USER_REPO/.leerie/runs/
    (old layout) and LEERIE_STATE_DIR points elsewhere, --stop must NOT
    find it — confirming the verb really uses the new state dir."""
    import json
    user_repo = _make_user_repo(tmp_path)
    state_dir = tmp_path / "leerie-state"  # empty — no runs here

    run_id = "_bootstrap-bb5678"
    # Place fixture in OLD location (user_repo/.leerie/runs/).
    old_run_dir = user_repo / ".leerie" / "runs" / run_id
    old_run_dir.mkdir(parents=True)
    (old_run_dir / "fly-machine.json").write_text(json.dumps({
        "fly_machine_id": "mach-old-layout",
        "fly_app": "leerie",
    }))

    result = subprocess.run(
        ["bash", str(LEERIE), "--stop", run_id],
        cwd=str(user_repo),
        capture_output=True, text=True,
        env={
            "PATH": __import__("os").environ["PATH"],
            "LEERIE_STATE_DIR": str(state_dir),
            "HOME": str(tmp_path),
        },
    )
    # Must fail because state_dir has no such run (old layout is not searched).
    assert result.returncode != 0, (
        "--stop must NOT find run dirs in USER_REPO/.leerie/runs/ "
        "when LEERIE_STATE_DIR points elsewhere.\n"
        f"stderr:\n{result.stderr}"
    )
    assert "no run dir" in result.stderr, (
        "Expected 'no run dir' error since state_dir has no runs.\n"
        f"stderr:\n{result.stderr}"
    )
