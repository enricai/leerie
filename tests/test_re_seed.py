"""Tests for Phase 4: mid-run re-rsync (`leerie re-seed` + auto-on-resume).

Covers:
  - scripts/remote/seed-repo.sh refactor (seed_repo_clone / seed_repo_dirty / seed_repo)
  - scripts/remote/re-seed.sh: re_seed() reads sidecar, wakes machine,
    runs safety check, calls seed_repo_dirty
  - Safety check: refuse re-seed when remote /work has uncommitted tracked
    changes (unless --force)
  - Launcher: re-seed fast-path
  - Launcher: --no-re-seed and --force are consumed (not forwarded to orchestrator)
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SEED_REPO_SH = REPO_ROOT / "scripts" / "remote" / "seed-repo.sh"
RE_SEED_SH = REPO_ROOT / "scripts" / "remote" / "re-seed.sh"
PROVISION_SH = REPO_ROOT / "scripts" / "remote" / "provision.sh"
LAUNCHER = REPO_ROOT / "leerie"


def _run_bash(script: str, env: dict | None = None, cwd: Path | None = None) -> subprocess.CompletedProcess:
    base_env = {k: v for k, v in os.environ.items()}
    if env:
        base_env.update(env)
    return subprocess.run(
        ["bash", "-c", script],
        env=base_env,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
    )


def _make_git_repo(repo_dir: Path) -> None:
    """Initialise a git repo with one committed file."""
    repo_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "user.email", "test@test"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo_dir, check=True)
    subprocess.run(["git", "remote", "add", "origin", "https://example.com/repo.git"],
                   cwd=repo_dir, check=True)
    (repo_dir / "README.md").write_text("hello\n")
    subprocess.run(["git", "add", "."], cwd=repo_dir, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo_dir, check=True)


def _stub_flyctl(tmp_path: Path, *, remote_status: str = "started",
                 remote_dirty: str = "") -> Path:
    """Stub flyctl with controllable machine state and git-status output.

    The state is stored in a file so `machine start` can flip the
    reported `machine status` from stopped → started, matching real Fly
    behaviour.

    For `ssh console -C "rsync --server ..."` (which seed_repo_dirty
    now uses), the rsync command line is rewritten to substitute /work
    with tmp_path/machine-work, and the rewritten command is exec'd
    locally so the rsync protocol actually completes successfully.

    Also writes a `timeout` stub into tmp_path. lib.sh's
    wait_for_fly_ssh_ready wraps flyctl in `timeout <secs>` and macOS
    doesn't ship `timeout` in /usr/bin. The stub skips the time cap and
    exec's the real (stubbed) child.
    """
    log = tmp_path / "flyctl.log"
    state_file = tmp_path / "stub-machine-state"
    state_file.write_text(remote_status)
    # Where the rsync receiver will write into. Tests don't typically
    # inspect this content (re-seed tests focus on control flow, not
    # transferred bytes), but rsync needs a real dest to talk to.
    machine_work = tmp_path / "machine-work"
    machine_work.mkdir(exist_ok=True)
    fake = tmp_path / "flyctl"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        f'echo "$@" >> "{log}"\n'
        f'STATE_FILE="{state_file}"\n'
        f'MACHINE_WORK="{machine_work}"\n'
        "case \"$1 $2\" in\n"
        "  'auth status') exit 0 ;;\n"
        '  "machine status") printf "Machine ID: mach-test\\nState: %s\\n" "$(cat $STATE_FILE)"; exit 0 ;;\n'
        '  "machine start") echo "started" > "$STATE_FILE"; exit 0 ;;\n'
        '  "machine stop") echo "stopped" > "$STATE_FILE"; exit 0 ;;\n'
        '  "machine destroy") echo "destroyed" > "$STATE_FILE"; exit 0 ;;\n'
        '  "ssh issue") exit 0 ;;\n'
        '  "ssh console")\n'
        # Parse -C "<cmd>" from the remaining args.
        '    cmd=""\n'
        '    while [ $# -gt 0 ]; do\n'
        '      case "$1" in\n'
        '        -C) cmd="$2"; shift 2 ;;\n'
        '        *) shift ;;\n'
        '      esac\n'
        '    done\n'
        '    case "$cmd" in\n'
        f'      *"git -C /work status"*) printf "%s" "{remote_dirty}"; exit 0 ;;\n'
        '      "true") exit 0 ;;\n'
        '      chown*) exit 0 ;;\n'
        '      rsync*)\n'
        # Rewrite the trailing "/work" → tmp_path/machine-work so the
        # rsync receiver actually has a real dest. Then exec it.
        '        local_cmd="${cmd// \\/work/ $MACHINE_WORK}"\n'
        '        eval "$local_cmd"\n'
        '        exit $?\n'
        '        ;;\n'
        '      *tar*-xzf*) cat > /dev/null; exit 0 ;;\n'
        '      *tar*-xC*) cat > /dev/null; exit 0 ;;\n'
        '      *) cat > /dev/null; exit 0 ;;\n'
        '    esac\n'
        '    ;;\n'
        "esac\n"
        'if [ "$1" = "machine" ] && [ "$2" = "exec" ]; then\n'
        "  found_dashes=0\n"
        '  for arg in "$@"; do\n'
        '    if [ "$arg" = "--" ]; then found_dashes=1; continue; fi\n'
        "    if [ \"$found_dashes\" = \"1\" ]; then\n"
        '      case "$arg" in\n'
        '        git)\n'
        f'          printf "%s" "{remote_dirty}"\n'
        "          exit 0\n"
        "          ;;\n"
        '        tar)\n'
        "          cat > /dev/null\n"
        "          exit 0\n"
        "          ;;\n"
        "      esac\n"
        "      exit 0\n"
        "    fi\n"
        "  done\n"
        "  exit 0\n"
        "fi\n"
        "exit 0\n"
    )
    fake.chmod(0o755)

    # Stub `timeout` so wait_for_fly_ssh_ready works on macOS hosts
    # where /usr/bin doesn't include it. Tests pin PATH to
    # tmp_path:/usr/bin:/bin which excludes Homebrew.
    timeout_stub = tmp_path / "timeout"
    timeout_stub.write_text(
        "#!/usr/bin/env bash\n"
        "while [[ \"$1\" == --* ]]; do shift; done\n"
        "shift  # discard the seconds arg\n"
        'exec "$@"\n'
    )
    timeout_stub.chmod(0o755)

    return fake


# --- seed-repo.sh refactor preserved contract -----------------------------

def test_seed_repo_clone_function_exists():
    """seed_repo_clone is a publicly-callable function after refactor."""
    result = _run_bash(
        f"source {SEED_REPO_SH}; declare -f seed_repo_clone >/dev/null && echo OK"
    )
    assert "OK" in result.stdout


def test_seed_repo_dirty_function_exists():
    """seed_repo_dirty is a publicly-callable function after refactor."""
    result = _run_bash(
        f"source {SEED_REPO_SH}; declare -f seed_repo_dirty >/dev/null && echo OK"
    )
    assert "OK" in result.stdout


def test_seed_repo_wrapper_still_exists():
    """seed_repo (the wrapper) is still callable so existing callers don't break."""
    result = _run_bash(
        f"source {SEED_REPO_SH}; declare -f seed_repo >/dev/null && echo OK"
    )
    assert "OK" in result.stdout


# --- re_seed: argument validation -----------------------------------------

def test_re_seed_requires_run_id(tmp_path: Path):
    result = _run_bash(
        f"source {PROVISION_SH}; source {SEED_REPO_SH}; source {RE_SEED_SH}; re_seed",
        env={"USER_REPO": str(tmp_path)},
    )
    assert result.returncode != 0
    assert "LEERIE_RUN_ID" in result.stderr


def test_re_seed_uses_run_id_as_machine_id(tmp_path: Path):
    """re_seed uses LEERIE_RUN_ID directly as the machine ID (run_id =
    machine_id per DESIGN §6) — no sidecar resolution needed.

    Asserts re_seed reaches `machine start` against the LEERIE_RUN_ID
    value.
    """
    repo = tmp_path / "user-repo"
    _make_git_repo(repo)

    _stub_flyctl(tmp_path, remote_status="stopped", remote_dirty="")
    result = _run_bash(
        f"source {PROVISION_SH}; source {SEED_REPO_SH}; source {RE_SEED_SH}; re_seed",
        env={
            "USER_REPO": str(repo),
            "LEERIE_RUN_ID": "mach-state-001",
            "PATH": f"{tmp_path}:/usr/bin:/bin",
            "LEERIE_MACHINE_START_TIMEOUT": "5",
        },
    )
    assert result.returncode == 0, result.stderr
    invocations = (tmp_path / "flyctl.log").read_text()
    assert "machine start mach-state-001" in invocations


# --- re_seed: happy path on a clean machine -------------------------------

def test_re_seed_starts_stopped_machine_and_calls_dirty(tmp_path: Path):
    """re_seed wakes a stopped machine and runs seed_repo_dirty."""
    repo = tmp_path / "user-repo"
    _make_git_repo(repo)
    # Add an uncommitted edit so seed_repo_dirty has something to send.
    (repo / "edit.txt").write_text("new file\n")

    _stub_flyctl(tmp_path, remote_status="stopped", remote_dirty="")
    result = _run_bash(
        f"source {PROVISION_SH}; source {SEED_REPO_SH}; source {RE_SEED_SH}; re_seed",
        env={
            "USER_REPO": str(repo),
            "LEERIE_RUN_ID": "mach-001",
            "PATH": f"{tmp_path}:/usr/bin:/bin",
            "LEERIE_MACHINE_START_TIMEOUT": "5",
        },
    )
    assert result.returncode == 0, result.stderr
    invocations = (tmp_path / "flyctl.log").read_text()
    assert "machine start mach-001" in invocations
    # rsync should have been invoked for the dirty file (via ssh console -C).
    # seed_repo_dirty now uses rsync over flyctl ssh console (was tar in
    # the older two-channel design).
    assert "ssh console" in invocations
    assert "rsync --server" in invocations


def test_re_seed_skips_start_when_machine_already_started(tmp_path: Path):
    """re_seed doesn't try to start a machine that's already 'started'."""
    repo = tmp_path / "user-repo"
    _make_git_repo(repo)
    run_dir = repo / ".leerie" / "runs" / "my-run"
    run_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text(json.dumps({
        "run_id": "my-run",
        "fly_machine_id": "mach-001",
    }))
    _stub_flyctl(tmp_path, remote_status="started", remote_dirty="")
    result = _run_bash(
        f"source {PROVISION_SH}; source {SEED_REPO_SH}; source {RE_SEED_SH}; re_seed",
        env={
            "USER_REPO": str(repo),
            "LEERIE_RUN_ID": "my-run",
            "PATH": f"{tmp_path}:/usr/bin:/bin",
        },
    )
    assert result.returncode == 0, result.stderr
    invocations = (tmp_path / "flyctl.log").read_text()
    assert "machine start" not in invocations


def test_re_seed_refuses_destroyed_machine(tmp_path: Path):
    """re_seed errors when the machine has been destroyed."""
    repo = tmp_path / "user-repo"
    _make_git_repo(repo)
    run_dir = repo / ".leerie" / "runs" / "my-run"
    run_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text(json.dumps({
        "run_id": "my-run",
        "fly_machine_id": "mach-gone",
    }))
    _stub_flyctl(tmp_path, remote_status="destroyed")
    result = _run_bash(
        f"source {PROVISION_SH}; source {SEED_REPO_SH}; source {RE_SEED_SH}; re_seed",
        env={
            "USER_REPO": str(repo),
            "LEERIE_RUN_ID": "my-run",
            "PATH": f"{tmp_path}:/usr/bin:/bin",
        },
    )
    assert result.returncode == 1
    assert "destroyed" in result.stderr


# --- re_seed: safety check on machine-side dirty state --------------------

def test_re_seed_refuses_when_machine_has_dirty_tracked_files(tmp_path: Path):
    """re_seed refuses (without --force) when /work on the machine has
    uncommitted tracked changes that aren't under .leerie/."""
    repo = tmp_path / "user-repo"
    _make_git_repo(repo)
    run_dir = repo / ".leerie" / "runs" / "my-run"
    run_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text(json.dumps({
        "run_id": "my-run",
        "fly_machine_id": "mach-001",
    }))
    # Stub the machine's git status to show a dirty src/foo.py.
    _stub_flyctl(tmp_path, remote_status="started",
                 remote_dirty=" M src/foo.py\n")
    result = _run_bash(
        f"source {PROVISION_SH}; source {SEED_REPO_SH}; source {RE_SEED_SH}; re_seed",
        env={
            "USER_REPO": str(repo),
            "LEERIE_RUN_ID": "my-run",
            "PATH": f"{tmp_path}:/usr/bin:/bin",
        },
    )
    assert result.returncode != 0
    assert "uncommitted" in result.stderr
    assert "src/foo.py" in result.stderr
    assert "--force" in result.stderr


def test_re_seed_ignores_leerie_dirty_paths(tmp_path: Path):
    """Dirty paths under .leerie/ are expected (worker state) and don't trip
    the safety check."""
    repo = tmp_path / "user-repo"
    _make_git_repo(repo)
    run_dir = repo / ".leerie" / "runs" / "my-run"
    run_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text(json.dumps({
        "run_id": "my-run",
        "fly_machine_id": "mach-001",
    }))
    _stub_flyctl(tmp_path, remote_status="started",
                 remote_dirty=" M .leerie/runs/my-run/state.json\n M .leerie/runs/my-run/logs/orch.log\n")
    result = _run_bash(
        f"source {PROVISION_SH}; source {SEED_REPO_SH}; source {RE_SEED_SH}; re_seed",
        env={
            "USER_REPO": str(repo),
            "LEERIE_RUN_ID": "my-run",
            "PATH": f"{tmp_path}:/usr/bin:/bin",
        },
    )
    assert result.returncode == 0, result.stderr


def test_re_seed_force_bypasses_safety_check(tmp_path: Path):
    """LEERIE_RE_SEED_FORCE=1 bypasses the dirty-state safety check."""
    repo = tmp_path / "user-repo"
    _make_git_repo(repo)
    run_dir = repo / ".leerie" / "runs" / "my-run"
    run_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text(json.dumps({
        "run_id": "my-run",
        "fly_machine_id": "mach-001",
    }))
    _stub_flyctl(tmp_path, remote_status="started",
                 remote_dirty=" M src/foo.py\n")
    result = _run_bash(
        f"source {PROVISION_SH}; source {SEED_REPO_SH}; source {RE_SEED_SH}; re_seed",
        env={
            "USER_REPO": str(repo),
            "LEERIE_RUN_ID": "my-run",
            "PATH": f"{tmp_path}:/usr/bin:/bin",
            "LEERIE_RE_SEED_FORCE": "1",
        },
    )
    assert result.returncode == 0, result.stderr


# --- launcher fast-path ----------------------------------------------------

def test_launcher_re_seed_fastpath_present():
    """The launcher has a re-seed fast-path before runtime preflight."""
    text = LAUNCHER.read_text()
    assert "re-seed)" in text
    re_seed_idx = text.find("re-seed)")
    preflight_idx = text.find("# --- platform preflight")
    assert re_seed_idx < preflight_idx, (
        "re-seed fast-path must run before runtime preflight"
    )


def test_launcher_consumes_re_seed_flags():
    """--no-re-seed and --force are launcher-only — not forwarded to the
    orchestrator's argparse via REWRITTEN_ARGS."""
    text = LAUNCHER.read_text()
    assert "--no-re-seed)" in text
    assert "NO_RE_SEED=true" in text
    assert "RE_SEED_FORCE=true" in text


def test_launcher_re_seed_requires_run_id_arg():
    """`leerie re-seed` without <run-id> errors with usage."""
    result = _run_bash(
        f"{LAUNCHER} re-seed",
    )
    assert result.returncode != 0
    assert "requires a <run-id>" in result.stderr
